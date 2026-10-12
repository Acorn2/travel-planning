"""Prepare reviewed trip/site snapshots; publish to a reusable Netlify site on request."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from html import escape, unescape
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import zipfile

from prepare_share import LocalDependencyScanner

FILES = {"index.html", "_trip.json", "_headers"}
SITE_SCHEMA = "travel-site/v1"
HEADERS = "/*\n  Cache-Control: public, max-age=0, must-revalidate\n  X-Robots-Tag: noindex, nofollow\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def publication_env_file(state_file: Path) -> Path:
    """Resolve the travel project's .env even when invoked from a plugin directory."""
    for parent in state_file.resolve().parents:
        if parent.name == ".travel-research":
            return parent.parent / ".env"
    return Path.cwd() / ".env"


def load_publication_settings(env_file: Path, environment: dict | None = None) -> dict:
    allowed = {"NETLIFY_AUTH_TOKEN", "NETLIFY_SITE_ID"}
    inherited = os.environ if environment is None else environment
    settings = {key: inherited[key].strip() for key in allowed if inherited.get(key, "").strip()}
    if not env_file.exists():
        return settings
    for number, raw in enumerate(env_file.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            if re.match(r"(?:export\s+)?NETLIFY_", line):
                raise ValueError(f"发布配置第 {number} 行格式错误；使用 KEY=value，不输出原文")
            continue
        key, value = match.groups()
        if key not in allowed:
            continue
        try:
            parts = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"发布配置第 {number} 行引号不匹配；未输出配置值") from None
        if len(parts) > 1 or any(character in (parts[0] if parts else "") for character in ("$", "`")):
            raise ValueError(f"发布配置第 {number} 行须填写字面值，不支持命令或变量展开；未输出配置值")
        if parts and parts[0].strip():
            settings[key] = parts[0].strip()
    return settings


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def validate_trip_id(trip_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}-\d{2}(?:-[a-z0-9]+)*", trip_id):
        raise ValueError("trip-id 应使用小写英文/拼音目的地和 YYYY-MM-DD 日期")


@contextmanager
def publication_lock(state_file: Path):
    state_file = state_file.resolve()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    lock = state_file.with_name(state_file.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError("同一站点已有发布或验证进程；若进程已退出，请先核对远端和状态再移除锁文件") from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def prepare(source: Path, trip_id: str, output_dir: Path) -> dict:
    validate_trip_id(trip_id)
    html = source.read_text(encoding="utf-8")
    scanner = LocalDependencyScanner()
    scanner.feed(html)
    if scanner.dependencies:
        raise ValueError("页面仍依赖本地资源，先重新渲染单文件 HTML")
    if not re.search(r"</head\s*>", html, re.I):
        raise ValueError("HTML 缺少 head 结束标签")
    if output_dir.exists():
        raise FileExistsError("发布快照目录已存在；请选择新的 release 目录，保留旧版")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    html = re.sub(r'<meta\s+name="itinerary-release-at"[^>]*>', "", html, flags=re.I)
    html = re.sub(r"</head\s*>", f'<meta name="itinerary-release-at" content="{created_at}"></head>', html, count=1, flags=re.I)
    page = html.encode("utf-8")
    marker = {"schema": "travel-publication/v1", "trip_id": trip_id,
              "created_at": created_at, "html_sha256": digest(page)}
    files = {"index.html": page, "_trip.json": (json.dumps(marker) + "\n").encode(),
             "_headers": HEADERS.encode()}
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    archive_data = archive_buffer.getvalue()
    review = {**marker, "public_access": "anyone_with_link", "zip_sha256": digest(archive_data),
              "files": {name: digest(data) for name, data in files.items()}}
    output_dir.mkdir(parents=True)
    public = output_dir / "public"
    public.mkdir()
    for name, data in files.items():
        (public / name).write_bytes(data)
    (output_dir / "site.zip").write_bytes(archive_data)
    save_json(output_dir / "review.json", review)
    return review


def load_release(directory: Path) -> tuple[dict, bytes]:
    review = read_json(directory / "review.json")
    if review.get("schema") == SITE_SCHEMA:
        return load_site_release(directory, review)
    if review.get("schema") != "travel-publication/v1":
        raise ValueError("无法识别发布格式")
    data = (directory / "site.zip").read_bytes()
    if digest(data) != review["zip_sha256"]:
        raise ValueError("发布包已改变，请重新准备并审阅")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if set(archive.namelist()) != FILES or len(archive.namelist()) != len(FILES):
            raise ValueError("发布包只能包含 index.html、_trip.json 和 _headers")
        for name in FILES:
            if digest(archive.read(name)) != review["files"].get(name):
                raise ValueError("发布文件与审阅清单不一致")
            if (directory / "public" / name).read_bytes() != archive.read(name):
                raise ValueError("预览文件与发布包不一致，请重新准备")
        marker = json.loads(archive.read("_trip.json"))
        if marker["trip_id"] != review["trip_id"] or marker["html_sha256"] != digest(archive.read("index.html")):
            raise ValueError("行程标识或页面摘要不一致")
    return review, data


def release_files(archive_data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def history_page(entries: dict, files: dict[str, bytes]) -> bytes:
    """Build a public index only from trips included in this reviewed release."""
    ordered = sorted(entries.items(), key=lambda item: (item[1]["created_at"], item[0]), reverse=True)
    cards = []
    for number, (trip_id, entry) in enumerate(ordered, 1):
        validate_trip_id(trip_id)
        page = files[entry["path"] + "index.html"].decode("utf-8")
        match = re.search(r"<title(?:\s[^>]*)?>(.*?)</title\s*>", page, re.I | re.S)
        title = unescape(match.group(1).strip()) if match else trip_id
        departure = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:-|$)", trip_id)
        date = ".".join(departure.groups()) if departure else "日期未标注"
        cards.append(
            f'<li><a class="trip-card" href="/{entry["path"]}" aria-label="查看{escape(title, quote=True)}">'
            f'<span class="trip-number">{number:02d}</span>'
            f'<span class="trip-main"><span class="trip-date">{date} 出发</span>'
            f'<strong>{escape(title)}</strong><span class="trip-id">{escape(trip_id)}</span></span>'
            '<span class="trip-arrow" aria-hidden="true">↗</span></a></li>'
        )
    rows = "\n".join(cards)
    count = len(ordered)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>行程档案 · {count} 份旅行规划</title>
<style>
:root{{--paper:#f7f5eb;--ink:#173b36;--muted:#667570;--line:#cbd4ca;--accent:#f3d34b}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",sans-serif}}
.shell{{max-width:980px;margin:auto;padding:0 clamp(20px,4vw,48px) 60px}}
.masthead{{display:flex;align-items:center;padding:20px 0;border-bottom:1px solid var(--ink)}}
.brand{{font-size:12px;font-weight:750;letter-spacing:.12em}}
.hero{{padding:38px 0 30px}}
h1{{margin:0 0 9px;font-family:"Songti SC","Noto Serif SC",serif;font-size:clamp(32px,4vw,44px);font-weight:700;line-height:1.2;letter-spacing:-.035em}}
.intro{{max-width:610px;margin:0;color:#50625c;font-size:14px;line-height:1.7}}
.section-head{{display:flex;justify-content:space-between;align-items:baseline;gap:16px;border-top:2px solid var(--ink);padding:12px 0}}
.section-head h2{{margin:0;font-size:13px;letter-spacing:.08em}}.section-head p{{margin:0;color:var(--muted);font-size:12px}}
.trips{{list-style:none;padding:0;margin:0;border-top:1px solid var(--line)}}.trips li{{margin:0}}
.trip-card{{display:grid;grid-template-columns:42px minmax(0,1fr) 36px;align-items:center;gap:16px;padding:17px 8px 18px 4px;border-bottom:1px solid var(--line);color:inherit;text-decoration:none;transition:background .2s ease}}
.trip-card:hover,.trip-card:focus-visible{{background:#edf0dd;outline:none}}.trip-card:focus-visible{{box-shadow:inset 0 0 0 2px var(--ink)}}
.trip-number{{align-self:start;padding-top:3px;font:15px Georgia,serif;color:#879a90}}.trip-main{{display:flex;flex-direction:column;min-width:0}}.trip-date{{font-size:11px;letter-spacing:.05em;color:#69786f}}
.trip-main strong{{margin:5px 0 4px;font-size:clamp(18px,2vw,22px);font-weight:650;line-height:1.4;letter-spacing:-.015em}}.trip-id{{font:11px Georgia,serif;letter-spacing:.02em;color:#899991;overflow-wrap:anywhere}}
.trip-arrow{{display:grid;place-items:center;width:32px;height:32px;border:1px solid var(--ink);border-radius:50%;font-size:18px;transition:background .2s ease}}
.trip-card:hover .trip-arrow,.trip-card:focus-visible .trip-arrow{{background:var(--accent)}}
.footnote{{display:flex;align-items:flex-start;gap:10px;margin-top:24px;color:#60736b;font-size:12px;line-height:1.7}}.footnote::before{{content:"↗";font-size:17px;color:var(--ink)}}
@media(max-width:650px){{.shell{{padding-bottom:42px}}.masthead{{padding:17px 0}}.hero{{padding:29px 0 24px}}h1{{font-size:34px}}.intro{{font-size:13px}}.section-head p{{display:none}}.trip-card{{grid-template-columns:28px minmax(0,1fr) 30px;gap:8px;padding:15px 4px}}.trip-number{{font-size:13px}}.trip-arrow{{width:29px;height:29px;font-size:16px}}.trip-main strong{{font-size:18px}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.trip-card,.trip-arrow{{transition:none}}}}
</style></head><body><div class="shell"><header class="masthead"><span class="brand">旅行规划 / ARCHIVE</span></header>
<main><section class="hero" aria-labelledby="page-title"><h1 id="page-title">行程档案</h1><p class="intro">所有已发布的旅行规划都在这里。选择一份行程，查看路线与出行安排。</p></section>
<section aria-labelledby="list-heading"><div class="section-head"><h2 id="list-heading">全部行程 · {count:02d}</h2><p>最近生成的行程排在前面</p></div><ol class="trips">{rows}</ol></section>
<p class="footnote">这个列表本身也可以通过网址访问。分享给同行人时，请复制对应行程详情页的链接。</p></main></div></body></html>
'''.encode("utf-8")


def prepare_site(trip_release: Path, output_dir: Path, *, base_release: Path | None = None,
                 state_file: Path | None = None) -> dict:
    """Merge one reviewed trip into a complete site, never scan the project for pages."""
    trip, trip_archive = load_release(trip_release)
    if trip.get("schema") != "travel-publication/v1":
        raise ValueError("输入应是 prepare 生成的单行程快照")
    if state_file is not None:
        if not state_file.exists():
            raise ValueError("找不到站点状态，不能从零覆盖已有站点；首次创建省略 --state-file，迁移使用 --base-release")
        state = read_json(state_file)
        if state.get("schema") != SITE_SCHEMA or state.get("status") != "published":
            raise ValueError("站点状态尚未验证，先运行 status 或核对远端；不能自动选择基线")
        previous = Path(state["release_dir"])
        if base_release is not None and base_release.resolve() != previous.resolve():
            raise ValueError("指定基线不是此站点最后验证的发布快照")
        base_release = previous
    source_files = release_files(trip_archive)
    trip_id = trip["trip_id"]
    validate_trip_id(trip_id)
    files = {}
    entries = {}
    root_trip_id = trip_id
    base = None
    if base_release is not None:
        prior, prior_archive = load_release(base_release)
        prior_files = release_files(prior_archive)
        base = {"schema": prior["schema"], "files": prior["files"]}
        if prior["schema"] == SITE_SCHEMA:
            files = prior_files
            entries = dict(prior["trips"])
            root_trip_id = prior["root_trip_id"]
        elif prior["schema"] == "travel-publication/v1":
            root_trip_id = prior["trip_id"]
            validate_trip_id(root_trip_id)
            prefix = f"trips/{root_trip_id}/"
            files = {prefix + name: prior_files[name] for name in ("index.html", "_trip.json")}
            entries[root_trip_id] = {"path": prefix, "html_sha256": prior["html_sha256"],
                                     "created_at": prior["created_at"]}
        else:
            raise ValueError("无法识别基线发布格式")
    prefix = f"trips/{trip_id}/"
    for name in ("index.html", "_trip.json"):
        files[prefix + name] = source_files[name]
    entries[trip_id] = {"path": prefix, "html_sha256": trip["html_sha256"],
                        "created_at": trip["created_at"]}
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    marker = {"schema": SITE_SCHEMA, "root_trip_id": root_trip_id,
              "created_at": created_at, "trips": entries}
    # Keep old root URLs and their view hashes working without redirects or a public listing.
    files["index.html"] = files[f"trips/{root_trip_id}/index.html"]
    files["_headers"] = HEADERS.encode()
    files["_site.json"] = (json.dumps(marker, ensure_ascii=False, sort_keys=True) + "\n").encode()
    files["history/index.html"] = history_page(entries, files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    archive_data = buffer.getvalue()
    review = {**marker, "trip_id": trip_id, "base": base, "public_access": "anyone_with_link",
              "zip_sha256": digest(archive_data), "files": {name: digest(data) for name, data in files.items()}}
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        target = output_dir / "public" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output_dir / "site.zip").write_bytes(archive_data)
    save_json(output_dir / "review.json", review)
    return review


def load_site_release(directory: Path, review: dict) -> tuple[dict, bytes]:
    data = (directory / "site.zip").read_bytes()
    if digest(data) != review["zip_sha256"]:
        raise ValueError("发布包已改变，请重新准备并审阅")
    entries = review["trips"]
    if not entries or review["root_trip_id"] not in entries or review["trip_id"] not in entries:
        raise ValueError("整站清单缺少根行程或本次行程")
    allowed = {"index.html", "_site.json", "_headers"}
    # Older site releases predate the history page and remain valid baselines.
    if "history/index.html" in review["files"]:
        allowed.add("history/index.html")
    for trip_id, entry in entries.items():
        validate_trip_id(trip_id)
        if entry["path"] != f"trips/{trip_id}/":
            raise ValueError("行程路径与 ID 不一致")
        allowed.update({entry["path"] + "index.html", entry["path"] + "_trip.json"})
    base = review["base"]
    if base is not None:
        if base["schema"] == "travel-publication/v1":
            if set(base["files"]) != FILES:
                raise ValueError("旧站迁移基线不完整")
        elif base["schema"] == SITE_SCHEMA:
            if not {"index.html", "_site.json", "_headers"}.issubset(base["files"]):
                raise ValueError("整站发布基线不完整")
            for name, expected in base["files"].items():
                if name in {"index.html", "_site.json", "_headers", "history/index.html"}:
                    continue
                match = re.fullmatch(r"trips/([^/]+)/(index\.html|_trip\.json)", name)
                if not match or match[1] not in entries:
                    raise ValueError("整站快照遗漏既有行程或基线路径无效")
                if match[1] != review["trip_id"] and review["files"].get(name) != expected:
                    raise ValueError("本次更新意外修改了其他行程")
        else:
            raise ValueError("无法识别基线发布格式")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if set(names) != allowed or len(names) != len(allowed) or set(review["files"]) != allowed:
            raise ValueError("整站发布包包含未审阅文件或遗漏行程文件")
        public = directory / "public"
        if public.is_symlink() or any(p.is_symlink() for p in public.rglob("*")):
            raise ValueError("发布预览目录不能含符号链接")
        if {p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file()} != allowed:
            raise ValueError("预览目录包含未审阅文件或遗漏行程文件")
        for name in allowed:
            payload = archive.read(name)
            if digest(payload) != review["files"][name] or (public / name).read_bytes() != payload:
                raise ValueError("预览文件、发布包与审阅清单不一致")
        marker = json.loads(archive.read("_site.json"))
        if marker != {key: review[key] for key in ("schema", "root_trip_id", "created_at", "trips")}:
            raise ValueError("整站标识与审阅清单不一致")
        if archive.read("_headers") != HEADERS.encode():
            raise ValueError("整站响应头与发布规范不一致")
        if archive.read("index.html") != archive.read(f"trips/{review['root_trip_id']}/index.html"):
            raise ValueError("根网址没有保留原行程")
        for trip_id, entry in entries.items():
            page = archive.read(entry["path"] + "index.html")
            trip_marker = json.loads(archive.read(entry["path"] + "_trip.json"))
            if (digest(page) != entry["html_sha256"] or trip_marker != {
                    "schema": "travel-publication/v1", "trip_id": trip_id,
                    "created_at": entry["created_at"], "html_sha256": entry["html_sha256"]}):
                raise ValueError("行程标识或页面摘要不一致")
    return review, data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an authentication header to a redirect destination.
        return None


class RequestNotSentError(RuntimeError):
    """Certificate validation failed before the HTTP request was sent."""


def create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    # python.org macOS installs can lack the default CA link even when certifi exists.
    # Preserve explicit CA settings and never retry or disable certificate validation.
    if (not paths.cafile and not paths.capath and context.cert_store_stats()["x509_ca"] == 0
            and not os.environ.get("SSL_CERT_FILE") and not os.environ.get("SSL_CERT_DIR")):
        try:
            import certifi
        except ImportError:
            pass
        else:
            context.load_verify_locations(cafile=certifi.where())
    return context


class Netlify:
    def __init__(self, token: str):
        if not token.strip():
            raise ValueError("请在项目根目录 .env 填写 NETLIFY_AUTH_TOKEN；不要把令牌发到聊天或写入源码")
        self.token = token
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=create_ssl_context()))

    def api(self, method: str, path: str, data: bytes | None = None, content_type: str = "application/json") -> dict | list:
        request = Request("https://api.netlify.com/api/v1" + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": content_type,
                                   "User-Agent": "travel-planning/0.1.0"})
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"Netlify API 返回 HTTP {error.code}；未输出响应或凭证") from None
        except URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise RequestNotSentError(
                    "TLS 证书校验失败，请检查 Python CA 证书（可用 SSL_CERT_FILE 指定）；"
                    "HTTP 请求尚未发送，修复证书后可重试") from None
            raise RuntimeError("Netlify API 网络请求失败；写请求不会自动重试，请先在控制台确认状态") from None

    def public_bytes(self, url: str) -> bytes:
        # Public verification intentionally sends no authentication or cookies.
        try:
            with self.opener.open(Request(url, headers={"Cache-Control": "no-cache"}), timeout=20) as response:
                return response.read()
        except HTTPError as error:
            if error.code == 401:
                try:
                    body = error.read(4096)
                except OSError:
                    body = b""
                if b"app.netlify.com" in body and b"edge-access" in body:
                    raise RuntimeError(
                        f"站点开启了 Netlify 访客登录保护（HTTP 401）：{url}；"
                        "请在此项目的 Visitor access 中将生产版本设为 Public，"
                        "再运行 status；无需重新上传") from None
            raise RuntimeError(f"公网访问返回 HTTP {error.code}：{url}；部署已保留，请用 status 复核") from None
        except URLError as error:
            reason = error.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                detail = "TLS 证书校验失败"
            elif isinstance(reason, ssl.SSLError):
                detail = "TLS 连接失败"
            elif isinstance(reason, socket.gaierror):
                detail = "DNS 解析失败"
            elif isinstance(reason, TimeoutError):
                detail = "连接超时"
            elif isinstance(reason, ConnectionResetError):
                detail = "连接被重置"
            else:
                detail = f"网络连接失败（{type(reason).__name__}）"
            raise RuntimeError(f"公网访问{detail}：{url}；部署已保留，请用 status 复核") from None
        except TimeoutError:
            raise RuntimeError(f"公网页面读取超时：{url}；部署已保留，请用 status 复核") from None


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9-]+", value or ""):
        raise ValueError("无效的 Netlify 项目/部署 ID")
    return value


def site_url(site: dict) -> str:
    url = site.get("ssl_url") or ""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("站点尚未提供有效的 HTTPS 地址")
    return url.rstrip("/") + "/"


def verify(client: Netlify, state: dict, state_file: Path | None = None) -> dict:
    site = client.api("GET", "/sites/" + identifier(state["site_id"]))
    if (site.get("published_deploy") or {}).get("id") != state["deploy_id"]:
        raise RuntimeError("部署尚未成为当前正式版本，请查看 Netlify 控制台")
    url = site_url(site)
    is_site = state.get("schema") == SITE_SCHEMA
    target_url = url + state["trips"][state["trip_id"]]["path"] if is_site else url
    state = {**state, "candidate_url": target_url}
    if state_file is not None:
        save_json(state_file, state)
    if is_site:
        verify_files(client, url, state["files"], state["site_id"])
        # A second check detects another writer publishing during public verification.
        current = client.api("GET", "/sites/" + identifier(state["site_id"]))
        if (current.get("published_deploy") or {}).get("id") != state["deploy_id"]:
            raise RuntimeError("验证期间站点版本发生变化，请核对其他发布进程")
    else:
        data = client.public_bytes(url)
        marker = json.loads(client.public_bytes(url + "_trip.json"))
        if digest(data) != state["html_sha256"] or marker.get("trip_id") != state["trip_id"]:
            raise RuntimeError("公网页面与审阅版不一致，尚不能交付分享链接")
    result = {**state, "status": "published", "url": target_url,
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if is_site:
        result.update(site_url=url, trip_urls={key: url + entry["path"] for key, entry in state["trips"].items()})
        if "history/index.html" in state["files"]:
            result["history_url"] = url + "history/"
        else:
            result.pop("history_url", None)
    return result


def without_netlify_insertions(data: bytes, site_id: str) -> bytes:
    """Netlify sometimes adds these two public-site snippets after deployment."""
    site = identifier(site_id).encode("ascii")
    comment = (b"\n<!-- This site is hosted on Netlify. Anyone can build and deploy a site\n"
               b"     like this one for free: https://netlify.new/?utm_campaign=loops&utm_source=ai-legible"
               b"&utm_medium=owned&utm_content=comment&utm_id=" + site + b"\n"
               b"     Netlify hosting facts for this site: static/SSR served via Netlify Edge. -->")
    script = (b'<script async src="/.netlify/scripts/hud?variant=public" data-nf-variant="public" '
              b'data-netlify-site-id="' + site + b'" data-app-host="app.netlify.com"></script>\n')
    if data.count(comment) == 1:
        data = data.replace(comment, b"", 1)
    if data.endswith(script):
        data = data[:-len(script)]
    return data


def verify_files(client: Netlify, url: str, files: dict, site_id: str) -> None:
    for name, expected_hash in files.items():
        if name == "_headers":
            continue  # Netlify consumes this file rather than serving it.
        path = name[:-len("index.html")] if name.endswith("index.html") else name
        data = client.public_bytes(url + path)
        if digest(data) != expected_hash and not (
                name.endswith("index.html") and digest(without_netlify_insertions(data, site_id)) == expected_hash):
            raise RuntimeError(f"公网页面与审阅版不一致：{path or '/'}；停止发布，核对基线或线上版本")


def verify_remote_inventory(client: Netlify, site_id: str, files: dict) -> None:
    paths = set()
    for page in range(1, 252):
        records = client.api("GET", f"/sites/{identifier(site_id)}/files?per_page=100&page={page}")
        if not isinstance(records, list) or any(not isinstance(row, dict) or not isinstance(row.get("path"), str) for row in records):
            raise RuntimeError("无法读取线上文件清单，停止覆盖")
        batch = {row["path"].lstrip("/") for row in records}
        if not batch - paths:
            break  # Some API versions return the whole collection without pagination.
        paths.update(batch)
        if len(records) < 100:
            break
    else:
        raise RuntimeError("线上文件清单超过支持范围，停止覆盖")
    # _headers may be consumed during Netlify post-processing.
    if paths - {"_headers"} != set(files) - {"_headers"}:
        raise ValueError("线上文件清单与基线不一致，可能存在未纳入的内容，禁止覆盖")


def finish_deploy(client: Netlify, state: dict, state_file: Path, timeout: float = 90) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        deploy = client.api("GET", "/deploys/" + identifier(state["deploy_id"]))
        if deploy.get("site_id") != state["site_id"]:
            raise RuntimeError("部署不属于绑定站点")
        if deploy.get("state") == "ready":
            break
        if deploy.get("state") in {"error", "failed", "rejected"}:
            save_json(state_file, {**state, "status": "failed"})
            raise RuntimeError("托管部署失败，请查看 Netlify 控制台")
        if time.monotonic() >= deadline:
            raise TimeoutError("部署仍在处理中；使用 status 继续检查，不要重复上传")
        time.sleep(2)
    # Persist the ready state before verification so failures can be resumed read-only.
    state = {**state, "status": "ready_unverified"}
    save_json(state_file, state)
    result = verify(client, state, state_file)
    save_json(state_file, result)
    return result


def publish(directory: Path, state_file: Path, client: Netlify, *, site_id: str | None = None,
            create_site: bool = False, confirmed: bool = False) -> dict:
    if not confirmed:
        raise ValueError("公开发布须明确授权；审阅页面后使用 --confirm-public")
    with publication_lock(state_file):
        return _publish(directory, state_file, client, site_id=site_id, create_site=create_site)


def _publish(directory: Path, state_file: Path, client: Netlify, *, site_id: str | None,
             create_site: bool) -> dict:
    review, archive = load_release(directory)
    is_site = review.get("schema") == SITE_SCHEMA
    identity = {"schema": SITE_SCHEMA, "root_trip_id": review["root_trip_id"]} if is_site else {"trip_id": review["trip_id"]}
    state = read_json(state_file) if state_file.exists() else {}
    if is_site and state and (state.get("schema") != SITE_SCHEMA or state.get("root_trip_id") != review["root_trip_id"]):
        raise ValueError("请使用此整站专用状态文件；保留旧行程状态，不得混用或切换根行程")
    if not is_site and state and (state.get("schema") == SITE_SCHEMA or state.get("trip_id") != review["trip_id"]):
        raise ValueError("状态文件属于其他行程，禁止复用")
    if state.get("status") == "creation_not_sent":
        if state.get("site_id") or state.get("deploy_id"):
            raise ValueError("未发送状态不应包含站点或部署 ID，请核对状态文件")
        state = {}
    if state.get("status") in {"creating_site", "creation_unknown"}:
        raise ValueError("上次创建站点的结果未知，请先在控制台核对；不会重复创建")
    if state and (create_site or (site_id and site_id != state["site_id"])):
        raise ValueError("行程已有绑定站点，请复用原地址，不得切换站点")
    if state.get("status") in {"uploading", "upload_unknown", "deploying", "ready_unverified"}:
        raise ValueError("上次发布待确认，请先运行 status；不会重复创建或上传")
    if state.get("status") == "published" and state.get("zip_sha256") == review["zip_sha256"]:
        verified = verify(client, state, state_file)
        save_json(state_file, verified)
        return verified
    if state:
        site_id = state["site_id"]
    elif create_site:
        if is_site and review["base"] is not None:
            raise ValueError("已有发布基线时应更新原站点，不能新建站点")
        state = {**identity, "status": "creating_site"}
        save_json(state_file, state)
        try:
            site = client.api("POST", "/sites", b"{}")
        except RequestNotSentError:
            save_json(state_file, {**state, "status": "creation_not_sent"})
            raise
        except Exception:
            save_json(state_file, {**state, "status": "creation_unknown"})
            raise
        site_id = identifier(site["id"])
        state = {**identity, "site_id": site_id, "status": "bound"}
        save_json(state_file, state)
    elif not site_id:
        raise ValueError("首次发布需指定独立空站点 --site-id，或明确使用 --create-site")
    site_id = identifier(site_id)
    site = client.api("GET", "/sites/" + site_id)
    previous_id = (site.get("published_deploy") or {}).get("id")
    if is_site:
        base = review["base"]
        if bool(previous_id) != bool(base):
            raise ValueError("目标站点与发布基线不一致；已有内容须以当前完整发布快照为基线，禁止覆盖")
        if previous_id:
            verify_remote_inventory(client, site_id, base["files"])
            verify_files(client, site_url(site), base["files"], site_id)
            if state.get("deploy_id") and state["status"] == "published" and state["deploy_id"] != previous_id:
                raise ValueError("线上部署已由其他进程变更，请核对当前发布基线")
        current = client.api("GET", "/sites/" + site_id)
        if (current.get("published_deploy") or {}).get("id") != previous_id:
            raise RuntimeError("基线检查期间站点版本发生变化，停止上传")
    elif previous_id:
        marker = json.loads(client.public_bytes(site_url(site) + "_trip.json"))
        if marker.get("schema") != "travel-publication/v1" or marker.get("trip_id") != review["trip_id"]:
            raise ValueError("目标站点已有其他内容，禁止覆盖；请使用此行程独立站点")
    state = {**state, **identity, "trip_id": review["trip_id"], "site_id": site_id,
             "zip_sha256": review["zip_sha256"],
             "release_at": review["created_at"], "previous_deploy_id": previous_id, "status": "uploading"}
    if is_site:
        state.update(files=review["files"], trips=review["trips"], release_dir=str(directory.resolve()))
        state.pop("trip_urls", None)
        state.pop("history_url", None)
    else:
        state["html_sha256"] = review["html_sha256"]
    state.pop("deploy_id", None)
    state.pop("url", None)
    state.pop("verified_at", None)
    state.pop("candidate_url", None)
    save_json(state_file, state)
    try:
        deploy = client.api("POST", "/sites/" + site_id + "/deploys", archive, "application/zip")
        state = {**state, "deploy_id": identifier(deploy["id"]), "status": "deploying"}
    except RequestNotSentError:
        save_json(state_file, {**state, "status": "bound"})
        raise
    except Exception:
        save_json(state_file, {**state, "status": "upload_unknown"})
        raise
    save_json(state_file, state)
    return finish_deploy(client, state, state_file)


def status(state_file: Path, client: Netlify) -> dict:
    with publication_lock(state_file):
        state = read_json(state_file)
        if not state.get("deploy_id"):
            raise ValueError("尚无部署 ID；若上传结果未知，请在控制台核对，不要盲目重复发布")
        return finish_deploy(client, state, state_file)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare", help="仅在本地生成不可覆盖的发布快照")
    build.add_argument("source", type=Path)
    build.add_argument("--trip-id", required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    aggregate = commands.add_parser("prepare-site", help="将单行程快照合入完整站点，仅本地准备")
    aggregate.add_argument("trip_release", type=Path)
    aggregate.add_argument("--base-release", type=Path, help="上次完整站点快照，首次迁移可指定旧单行程快照")
    aggregate.add_argument("--state-file", type=Path, help="自动读取最后验证的站点快照")
    aggregate.add_argument("--output-dir", type=Path, required=True)
    upload = commands.add_parser("publish", help="明确授权后发布审阅版")
    upload.add_argument("release_dir", type=Path)
    upload.add_argument("--state-file", type=Path, required=True)
    upload.add_argument("--env-file", type=Path, help="默认读取行程项目根目录 .env")
    destination = upload.add_mutually_exclusive_group()
    destination.add_argument("--site-id")
    destination.add_argument("--create-site", action="store_true")
    upload.add_argument("--confirm-public", action="store_true")
    check = commands.add_parser("status", help="只读恢复等待与验证，不重新上传")
    check.add_argument("--state-file", type=Path, required=True)
    check.add_argument("--env-file", type=Path, help="默认读取行程项目根目录 .env")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.source, args.trip_id, args.output_dir)
            print(f"发布快照：{args.output_dir.resolve()}\n页面：{(args.output_dir / 'public/index.html').resolve()}")
            print(f"行程：{result['trip_id']}；本版生成：{result['created_at']}；尚未发布")
        elif args.command == "prepare-site":
            result = prepare_site(args.trip_release, args.output_dir, base_release=args.base_release,
                                  state_file=args.state_file)
            print(f"整站快照：{args.output_dir.resolve()}\n保留行程：{', '.join(result['trips'])}")
            print(f"本次页面：{(args.output_dir / 'public' / result['trips'][result['trip_id']]['path'] / 'index.html').resolve()}")
            print(f"根网址继续显示：{result['root_trip_id']}；尚未发布")
        else:
            settings = load_publication_settings(args.env_file or publication_env_file(args.state_file))
            client = Netlify(settings.get("NETLIFY_AUTH_TOKEN", ""))
            if args.command == "publish":
                configured_site = None if args.create_site else settings.get("NETLIFY_SITE_ID")
                result = publish(args.release_dir, args.state_file, client, site_id=args.site_id or configured_site,
                                 create_site=args.create_site, confirmed=args.confirm_public)
            else:
                result = status(args.state_file, client)
            print(f"已验证公网行程：{result['url']}")
            if result.get("history_url"):
                print(f"全部规划历史：{result['history_url']}")
            print("页面‘更多’中可复制本次行程链接或查看分享二维码。")
    except (ValueError, OSError, RuntimeError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"未完成：{error}\n")


if __name__ == "__main__":
    main()
