#!/usr/bin/env python3
"""Query approved travel data sources and build manual verification fallbacks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from source_adapters import (  # noqa: E402
    PROVIDERS,
    AdapterError,
    adapter,
    call_mcp_http,
    call_mcp_stdio,
    probe_mcp_http,
    probe_mcp_stdio,
    provider_capabilities,
    provider_command,
    run_json_cli,
)
from research_workspace import INVENTORY_PRODUCTS_BY_DOMAIN, WorkspaceError, read_json as read_workspace_json, store_source_snapshot  # noqa: E402
import execution_log  # noqa: E402
from xhs_images import cache_images, prepare_images  # noqa: E402
from scripts.providers.amap_mcp import (  # noqa: E402
    command as amap_mcp_command,
    load_config as load_amap_environment,
)
from scripts.runtime_env import (  # noqa: E402
    SOURCE_KEYS,
    SourceEnvironmentError,
    load_source_environment,
    source_config_file,
)


OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
AMAP_API = "https://restapi.amap.com"
AMAP_DOCS = "https://lbs.amap.com/api/webservice/guide/api/direction"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "travel-planning/1.0 (read-only public API client)"
SKILL_ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG_FILE = source_config_file(os.environ)
XHS_DEFAULT_URL = "http://127.0.0.1:18060/mcp"
XHS_DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "travel-planning" / "xiaohongshu-mcp"
XHS_DATA_ROOT = Path(
    os.environ.get(
        "TRAVEL_XHS_MCP_HOME",
        str(XHS_DEFAULT_DATA_ROOT),
    )
).expanduser().resolve()
XHS_TOKEN_CACHE = XHS_DATA_ROOT / "state" / "search-tokens.json"
ALLOWED_LOCAL_CONFIG_KEYS = SOURCE_KEYS

TRANSPORT_COVERAGE_SORTS = (6, 7, 4, 3, 2)
TRANSPORT_SORT_LABELS = {
    2: "recommended",
    3: "price_asc",
    4: "duration_asc",
    6: "departure_early",
    7: "departure_late",
}

PREFLIGHT_SOURCES = (
    "amap-maps",
    "variflight-aviation",
    "variflight-tripmatch",
    "flyai",
    "open-meteo",
    "xiaohongshu",
)

SOURCE_REVISION = hashlib.sha256(Path(__file__).read_bytes() +
    Path(execution_log.__file__).read_bytes() + (SCRIPTS_DIR / "source_adapters.py").read_bytes()).hexdigest()
QUERY_PHASES = {
    "preflight": "preflight", "xhs-health": "preflight", "xhs-images": "research",
    "weather": "weather", "amap-place": "research", "amap-route": "transport",
    "osm-place": "research", "xhs-search": "research", "xhs-detail": "research",
    "xhs-login-status": "preflight",
}


def source_context(source_id: str | None) -> dict[str, Any]:
    if source_id == "xiaohongshu":
        endpoint = urlparse(xhs_mcp_url())
        # Connection identity excludes auth, query and fragment.
        return {"host": endpoint.hostname, "port": endpoint.port, "path": endpoint.path}
    if source_id == "amap-maps":
        return {"transport": "mcp_stdio"}
    return {"source": source_id}


def health_key(source_id: str) -> str:
    return execution_log.fingerprint({"category": "health", "source": source_id,
                                      "revision": SOURCE_REVISION, "context": source_context(source_id)})


def query_phase(args: argparse.Namespace) -> str:
    explicit = getattr(args, "phase", None)
    if explicit:
        return explicit
    command = getattr(args, "command", "query")
    if command.startswith(("flyai-", "variflight-")):
        return "lodging" if command == "flyai-hotel" else "transport"
    return QUERY_PHASES.get(command, "research")


def run_query(args: argparse.Namespace, tool: str, parameters: dict[str, Any], callback: Any,
              source_id: str | None = None, reuse_key: str | None = None,
              prove_health: bool = False) -> dict[str, Any]:
    workspace = getattr(args, "workspace", None)
    if not workspace:
        return callback()
    root = execution_log.workspace_root(workspace)
    key = reuse_key or execution_log.fingerprint({"tool": tool, "parameters": parameters,
        "revision": SOURCE_REVISION, "context": source_context(source_id)})
    metadata: dict[str, Any] = {}
    call_started = time.monotonic()
    operation = re.sub(r"[^a-z0-9_-]+", "-", tool.casefold()).strip("-")[:64] or "query"
    with execution_log.measured(root, query_phase(args), operation, "query", getattr(args, "task_id", None)) as entry:
        entry["query_key"] = key
        with execution_log.query_lock(root / "state/execution/locks" / f"{key}.lock"):
            record = None if getattr(args, "refresh", False) else execution_log.cached_result(root, key)
            if record:
                payload = record["payload"]
                entry["mode"] = "reused"
                metadata = {"mode": "reused", "query_key": key, "expires_at": record["expires_at"]}
            else:
                entry["mode"] = "fresh"
                started = time.monotonic()
                try:
                    payload = callback()
                except BaseException:
                    # Preserve archived evidence, but invalidate the memo/proof after a failed refresh.
                    execution_log.write_json(root / "state/execution/cache" / f"{key}.json", {"key": key, "invalidated_at": execution_log.stamp()})
                    if source_id:
                        execution_log.write_json(root / "state/execution/cache" / f"{health_key(source_id)}.json", {"invalidated_at": execution_log.stamp()})
                    raise
                execution_log.write_json(root / "state/execution/cache" / f"{key}.json", {"key": key, "invalidated_at": execution_log.stamp()})
                execution_log.store_result(root, key, payload)
                metadata = {"mode": "fresh", "query_key": key}
                if prove_health and source_id and payload.get("status") in {"platform_reported", "community_reported", "no_results"}:
                    captured = (payload.get("freshness") or {}).get("checked_at") or payload.get("checked_at")
                    proof = {"status": "ready", "source_id": source_id, "scope": "observed_operation",
                             "operation": tool, "checked_at": captured,
                             "latency_ms": round((time.monotonic() - started) * 1000)}
                    execution_log.store_result(root, health_key(source_id), proof)
                if payload.get("status") in {"error", "unavailable", "login_required"}:
                    entry["status"] = "failed"
                    if source_id:
                        execution_log.write_json(root / "state/execution/cache" / f"{health_key(source_id)}.json", {"invalidated_at": execution_log.stamp()})
            captured = (payload.get("freshness") or {}).get("checked_at") or payload.get("checked_at")
            metadata["original_checked_at"] = captured
            if execution_log.empty_result(payload):
                entry["status"] = "no_results"
    metadata["elapsed_ms"] = round((time.monotonic() - call_started) * 1000)
    args._execution = metadata
    return payload


def command_parameters(args: argparse.Namespace) -> dict[str, Any]:
    excluded = {"handler", "command", "workspace", "task_id", "phase", "refresh", "timeout", "consent_external_data"}
    return {key: value for key, value in vars(args).items() if key not in excluded and not key.startswith("_")}

WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


def weather_icon_code(code: int | None) -> str:
    if code == 0:
        return "clear_day"
    if code in {1, 2}:
        return "partly_cloudy"
    if code == 3:
        return "cloudy"
    if code in {45, 48}:
        return "fog"
    if code in {51, 53, 55, 56, 57}:
        return "drizzle"
    if code in {61, 63, 66, 80, 81}:
        return "rain"
    if code in {65, 67, 82}:
        return "heavy_rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {95, 96, 99}:
        return "thunderstorm"
    return "unknown"


class SourceError(RuntimeError):
    """A source is unavailable or returned an invalid response."""


def load_local_config(path: Path = LOCAL_CONFIG_FILE) -> None:
    """Load the plugin-level allowlisted provider environment into this process."""
    try:
        environment = load_source_environment(
            os.environ,
            allowed_keys=ALLOWED_LOCAL_CONFIG_KEYS,
            config_path=path,
        )
    except SourceEnvironmentError as error:
        raise SourceError(str(error)) from error
    for key in ALLOWED_LOCAL_CONFIG_KEYS:
        if environment.get(key) and key not in os.environ:
            os.environ[key] = environment[key]


load_local_config()


def checked_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def request_json(base_url: str, params: dict[str, Any], timeout: int = 20) -> Any:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    url = f"{base_url}?{urlencode(clean, doseq=True)}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise SourceError(f"HTTP {error.code}: {base_url}") from error
    except URLError as error:
        raise SourceError(f"无法访问 {base_url}: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise SourceError(f"来源未返回有效 JSON: {base_url}") from error


def post_json(url: str, payload: dict[str, Any], timeout: int = 180) -> Any:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        message = f"HTTP {error.code}: {url}"
        try:
            body = json.loads(error.read().decode("utf-8"))
            detail = body.get("error") or body.get("message")
            code = body.get("code")
            if detail:
                message += f" · {code + ': ' if code else ''}{detail}"
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        raise SourceError(message) from error
    except URLError as error:
        raise SourceError(f"无法访问 {url}: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise SourceError(f"来源未返回有效 JSON: {url}") from error


def xhs_base_url() -> str:
    raw = os.environ.get("XHS_READONLY_URL", XHS_DEFAULT_URL).rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SourceError("小红书只读服务只允许本机 loopback HTTP 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SourceError("XHS_READONLY_URL 不能包含凭证、查询参数或片段")
    return raw


def xhs_token_cache_path() -> Path:
    custom = os.environ.get("XHS_TOKEN_CACHE")
    return Path(custom).expanduser().resolve() if custom else XHS_TOKEN_CACHE


def read_token_cache() -> dict[str, Any]:
    path = xhs_token_cache_path()
    if not path.exists():
        return {"version": 1, "notes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceError(f"无法读取小红书临时令牌缓存：{path}") from error
    if not isinstance(data, dict) or not isinstance(data.get("notes", {}), dict):
        raise SourceError(f"小红书临时令牌缓存格式无效：{path}")
    return data


def cache_xhs_tokens(feeds: list[dict[str, Any]], query: str) -> None:
    cache = read_token_cache()
    notes = cache.setdefault("notes", {})
    captured = checked_at()
    for feed in feeds:
        note_id = feed.get("id")
        token = feed.get("xsecToken")
        if note_id and token:
            notes[str(note_id)] = {
                "xsec_token": token,
                "source_url": feed.get("sourceUrl"),
                "query": query,
                "captured_at": captured,
            }
    path = xhs_token_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def unwrap_xhs_response(data: Any) -> Any:
    if not isinstance(data, dict):
        raise SourceError("小红书只读服务返回了无效响应")
    if data.get("success") is not True:
        code = data.get("code") or "UNKNOWN"
        message = data.get("error") or data.get("message") or "请求失败"
        raise SourceError(f"小红书只读服务 {code}: {message}")
    return data.get("data")


def xhs_health(_: argparse.Namespace) -> dict[str, Any]:
    url = f"{xhs_base_url()}/health"
    data = request_json(url, {}, timeout=3)
    service = unwrap_xhs_response(data)
    return {
        "status": "available",
        "provider": "小红书本机只读服务（非官方）",
        "service": service,
        "login_url": f"{xhs_base_url()}/login",
        "checked_at": checked_at(),
    }


def xhs_login_status(_: argparse.Namespace) -> dict[str, Any]:
    data = unwrap_xhs_response(post_json(f"{xhs_base_url()}/api/v1/login/status", {}))
    return {
        "status": "authenticated" if data.get("is_logged_in") else "login_required",
        "provider": "小红书本机只读服务（非官方）",
        "session": data,
        "login_url": f"{xhs_base_url()}/login",
        "checked_at": checked_at(),
    }


def normalize_xhs_feed(feed: dict[str, Any]) -> dict[str, Any]:
    note = feed.get("noteCard") or {}
    user = note.get("user") or {}
    interactions = note.get("interactInfo") or {}
    return {
        "note_id": feed.get("id"),
        "title": note.get("displayTitle"),
        "author": user.get("nickname") or user.get("nickName"),
        "note_type": note.get("type"),
        "interactions": {
            "likes": interactions.get("likedCount"),
            "comments": interactions.get("commentCount"),
            "collections": interactions.get("collectedCount"),
            "shares": interactions.get("sharedCount"),
        },
        "source_url": feed.get("sourceUrl"),
    }


def xhs_search(args: argparse.Namespace) -> dict[str, Any]:
    filters = {
        "sort_by": args.sort_by,
        "note_type": args.note_type,
        "publish_time": args.publish_time,
        "search_scope": args.search_scope,
        "location": args.location,
    }
    response = post_json(
        f"{xhs_base_url()}/api/v1/feeds/search",
        {"keyword": args.keyword, "filters": filters},
    )
    data = unwrap_xhs_response(response) or {}
    feeds = data.get("feeds") or []
    cache_xhs_tokens(feeds, args.keyword)
    normalized = [normalize_xhs_feed(feed) for feed in feeds[: args.limit]]
    return {
        "status": "community_reported",
        "provider": "小红书本机只读服务（非官方）",
        "query": {"keyword": args.keyword, "filters": filters},
        "results": normalized,
        "count": len(normalized),
        "checked_at": checked_at(),
        "source": {
            "title": "小红书公开笔记搜索",
            "url": "https://www.xiaohongshu.com/explore",
            "kind": "community_search",
        },
        "disclaimer": "社区内容只用于体验、路线与避坑判断；开放、票价、预约、安全和交通规则必须回到官方来源核验。",
    }


def unix_time(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def xhs_detail(args: argparse.Namespace) -> dict[str, Any]:
    cached = read_token_cache().get("notes", {}).get(args.note_id)
    if not cached or not cached.get("xsec_token"):
        raise SourceError("未找到该笔记的临时访问令牌；请先用 xhs-search 搜索，再读取详情")
    response = post_json(
        f"{xhs_base_url()}/api/v1/feeds/detail",
        {
            "feed_id": args.note_id,
            "xsec_token": cached["xsec_token"],
            "load_all_comments": False,
        },
    )
    data = unwrap_xhs_response(response) or {}
    detail = data.get("data") or {}
    note = detail.get("note") or {}
    user = note.get("user") or {}
    interactions = note.get("interactInfo") or {}
    return {
        "status": "community_reported",
        "provider": "小红书本机只读服务（非官方）",
        "note": {
            "note_id": note.get("noteId") or args.note_id,
            "title": note.get("title"),
            "description": note.get("desc"),
            "note_type": note.get("type"),
            "published_at": unix_time(note.get("time")),
            "ip_location": note.get("ipLocation"),
            "author": user.get("nickname") or user.get("nickName"),
            "interactions": {
                "likes": interactions.get("likedCount"),
                "comments": interactions.get("commentCount"),
                "collections": interactions.get("collectedCount"),
                "shares": interactions.get("sharedCount"),
            },
            "source_url": note.get("sourceUrl") or cached.get("source_url"),
        },
        "checked_at": checked_at(),
        "disclaimer": "未读取或归档完整评论、图片和临时媒体地址；关键事实需与官方来源交叉核验。",
    }


def xhs_mcp_url() -> str:
    raw = os.environ.get("XHS_MCP_URL", XHS_DEFAULT_URL).rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SourceError("XHS_MCP_URL 只允许本机 loopback HTTP 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SourceError("XHS_MCP_URL 不能包含凭证、查询参数或片段")
    if not parsed.path.endswith("/mcp"):
        raise SourceError("XHS_MCP_URL 必须指向 /mcp 端点")
    return raw


def xhs_mcp_call(tool: str, arguments: dict[str, Any], timeout: int = 180) -> Any:
    return call_mcp_http(
        xhs_mcp_url(),
        tool,
        arguments,
        timeout,
        os.environ.get("XHS_MCP_AUTH_TOKEN"),
    )


def xhs_mcp_token_cache_path() -> Path:
    custom = os.environ.get("XHS_TOKEN_CACHE")
    return Path(custom).expanduser().resolve() if custom else XHS_TOKEN_CACHE


def cache_xhs_mcp_tokens(feeds: list[dict[str, Any]], query: str) -> None:
    original = os.environ.get("XHS_TOKEN_CACHE")
    os.environ["XHS_TOKEN_CACHE"] = str(xhs_mcp_token_cache_path())
    try:
        cache_xhs_tokens(feeds, query)
    finally:
        if original is None:
            os.environ.pop("XHS_TOKEN_CACHE", None)
        else:
            os.environ["XHS_TOKEN_CACHE"] = original


def read_xhs_mcp_token_cache() -> dict[str, Any]:
    original = os.environ.get("XHS_TOKEN_CACHE")
    os.environ["XHS_TOKEN_CACHE"] = str(xhs_mcp_token_cache_path())
    try:
        return read_token_cache()
    finally:
        if original is None:
            os.environ.pop("XHS_TOKEN_CACHE", None)
        else:
            os.environ["XHS_TOKEN_CACHE"] = original


def xhs_health(_: argparse.Namespace) -> dict[str, Any]:
    transport = probe_mcp_http(
        xhs_mcp_url(),
        15,
        {"check_login_status", "search_feeds", "get_feed_detail"},
        os.environ.get("XHS_MCP_AUTH_TOKEN"),
    )
    return {
        "status": "ready",
        "provider": "xpzouying/xiaohongshu-mcp",
        "transport": transport,
        "checked_at": checked_at(),
    }


def xhs_login_status(args: argparse.Namespace) -> dict[str, Any]:
    data = xhs_mcp_call("check_login_status", {}, timeout=getattr(args, "timeout", 60))
    text = data.get("text", "") if isinstance(data, dict) else str(data)
    logged_in = "未登录" not in text and ("已登录" in text or "logged in" in text.lower())
    return {
        "status": "authenticated" if logged_in else "login_required",
        "provider": "xpzouying/xiaohongshu-mcp（非官方）",
        "session": {"logged_in": logged_in},
        "checked_at": checked_at(),
    }


def require_xhs_login(timeout: int = 60, query_args: argparse.Namespace | None = None) -> None:
    login_args = argparse.Namespace(timeout=timeout, workspace=getattr(query_args, "workspace", None),
        phase=getattr(query_args, "phase", None) or "preflight", task_id=getattr(query_args, "task_id", None),
        refresh=getattr(query_args, "refresh", False))
    session = run_query(login_args, "check-login-status", {}, lambda: xhs_login_status(login_args),
                        "xiaohongshu", reuse_key=health_key("xiaohongshu"))
    if session.get("status") not in {"authenticated", "ready"}:
        raise AdapterError(
            "login_required",
            "小红书当前未登录，请先执行 setup.py login 完成扫码，再启动 MCP 服务",
        )


def normalize_xhs_mcp_feed(feed: dict[str, Any]) -> dict[str, Any]:
    note = feed.get("noteCard") or {}
    user = note.get("user") or {}
    interactions = note.get("interactInfo") or {}
    note_id = feed.get("id")
    return {
        "note_id": note_id,
        "title": note.get("displayTitle"),
        "author": user.get("nickname") or user.get("nickName"),
        "note_type": note.get("type"),
        "interactions": {
            "likes": interactions.get("likedCount"),
            "comments": interactions.get("commentCount"),
            "collections": interactions.get("collectedCount"),
            "shares": interactions.get("sharedCount"),
        },
        "source_url": f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else None,
    }


def xhs_excerpt(value: Any, limit: int = 320) -> str | None:
    if not isinstance(value, str):
        return None
    clean = re.sub(r"\s+", " ", value).strip()
    return clean if len(clean) <= limit else clean[:limit].rstrip() + "…"


def xhs_first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def xhs_comment_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("comments", "commentList", "comment_list", "items"):
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def normalize_xhs_comment(
    comment: dict[str, Any],
    include_replies: bool = False,
) -> dict[str, Any]:
    user = (
        comment.get("user")
        or comment.get("userInfo")
        or comment.get("user_info")
        or {}
    )
    if not isinstance(user, dict):
        user = {}
    replies = xhs_comment_items(
        xhs_first_value(comment, "subComments", "sub_comments", "replies", "replyList")
    )
    normalized = {
        "comment_id": xhs_first_value(comment, "id", "commentId", "comment_id"),
        "author": xhs_first_value(user, "nickname", "nickName", "name"),
        "content": xhs_excerpt(
            xhs_first_value(comment, "content", "text", "commentContent"),
            limit=500,
        ),
        "published_at": unix_time(
            xhs_first_value(comment, "createTime", "create_time", "time", "timestamp")
        ),
        "likes": xhs_first_value(comment, "likeCount", "like_count", "likedCount"),
        "reply_count": xhs_first_value(
            comment,
            "subCommentCount",
            "sub_comment_count",
            "replyCount",
            "reply_count",
        ),
    }
    if include_replies:
        normalized["replies"] = [
            normalize_xhs_comment(reply, include_replies=False)
            for reply in replies
        ]
    return normalized


def normalize_xhs_comments(
    detail: dict[str, Any],
    include_replies: bool = False,
) -> list[dict[str, Any]]:
    comments = xhs_comment_items(
        xhs_first_value(detail, "comments", "commentList", "comment_list")
    )
    if not comments:
        note = detail.get("note") or {}
        if isinstance(note, dict):
            comments = xhs_comment_items(
                xhs_first_value(note, "comments", "commentList", "comment_list")
            )
    return [
        normalize_xhs_comment(comment, include_replies=include_replies)
        for comment in comments
    ]


XHS_COMMENT_TOPIC_RULES = (
    ("推广或疑似广告", r"广告|推广|合作|团购|带货|私信|优惠券|扫码|加微信|代购"),
    ("开放、施工与搬迁", r"施工|维修|修路|围挡|封路|关闭|封闭|搬迁|开放时间|停止入场|恢复开放"),
    ("预约、购票与检票", r"预约|门票|购票|检票|取票|身份证|实名|预约满"),
    ("价格", r"票价|价格|人均|收费|免费|花了|花费|贵|便宜|元|¥|￥"),
    ("交通与停车", r"停车|停车场|地铁|公交|打车|出租|自驾|接驳|换乘|堵车|班车"),
    ("入口与路线", r"东门|西门|南门|北门|入口|出口|路线|步行|爬坡|台阶|绕行"),
    ("排队与拥挤", r"排队|拥挤|人多|人少|空旷|等候|等了|排了|分钟"),
    ("天气影响", r"下雨|下雪|大风|天气|暴晒|日落|日出|雾|能见度|积水"),
    ("餐厅口味与服务", r"好吃|难吃|口味|味道|份量|分量|出餐|座位|服务|排队吃饭|人均"),
    ("老人、儿童与无障碍", r"老人|老年人|孩子|儿童|婴儿车|轮椅|无障碍|推车|亲子"),
    ("安全与风险", r"危险|不安全|小心|落石|打滑|防滑|事故|急救|高反|警察"),
)
XHS_COMMENT_DEFAULT_TOPIC = "其他"
XHS_COMMENT_RECHECK_TOPICS = frozenset(
    {
        "开放、施工与搬迁",
        "预约、购票与检票",
        "价格",
        "交通与停车",
        "排队与拥挤",
        "天气影响",
        "老人、儿童与无障碍",
        "安全与风险",
        "入口与路线",
    }
)
XHS_COMMENT_ANCHOR_PATTERNS = (
    ("东门", r"东门"),
    ("西门", r"西门"),
    ("南门", r"南门"),
    ("北门", r"北门"),
    ("入口", r"入口|进门"),
    ("出口", r"出口|出门"),
    ("施工", r"施工|维修|修路|围挡|封路"),
    ("开放", r"开放|恢复|通行"),
    ("关闭", r"关闭|封闭|不能进|不让进"),
    ("预约", r"预约|实名"),
    ("门票", r"门票|票价|购票|检票"),
    ("停车", r"停车|停车场"),
    ("公共交通", r"地铁|公交|打车|出租|接驳|换乘"),
    ("排队", r"排队|拥挤|等候|等了|排了"),
    ("人少", r"人少|空旷|不挤"),
    ("人多", r"人多|拥挤|很挤"),
    ("餐饮", r"餐厅|吃饭|口味|味道|份量|分量|出餐|座位|服务"),
    ("天气", r"下雨|下雪|大风|暴晒|日落|日出|雾|积水"),
    ("无障碍", r"老人|孩子|儿童|婴儿车|轮椅|无障碍|推车"),
)


def _xhs_numeric_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|k)?", text, re.IGNORECASE)
    if not match:
        return 0.0
    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit == "万":
        number *= 10000
    elif unit in {"千", "k"}:
        number *= 1000
    return number


def _xhs_comment_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().isdigit()
    ):
        timestamp = _xhs_numeric_value(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            parsed = datetime.fromtimestamp(timestamp).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def _xhs_comment_text(comment: dict[str, Any]) -> str:
    return str(comment.get("content") or "").strip()


def classify_xhs_comment_topic(content: Any) -> str:
    """Assign one conservative travel-research topic to a comment."""
    text = str(content or "").strip()
    if not text:
        return XHS_COMMENT_DEFAULT_TOPIC
    for topic, pattern in XHS_COMMENT_TOPIC_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return topic
    return XHS_COMMENT_DEFAULT_TOPIC


def _xhs_comment_anchors(content: Any) -> set[str]:
    text = str(content or "").strip()
    return {
        anchor
        for anchor, pattern in XHS_COMMENT_ANCHOR_PATTERNS
        if re.search(pattern, text, re.IGNORECASE)
    }


def score_xhs_comment(
    comment: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score comment usefulness without treating popularity as truth."""
    text = _xhs_comment_text(comment)
    topic = classify_xhs_comment_topic(text)
    current = (now or datetime.now().astimezone()).astimezone()
    published = _xhs_comment_datetime(comment.get("published_at"))
    reasons: list[str] = []
    flags: list[str] = []

    relevance = 30 if topic != XHS_COMMENT_DEFAULT_TOPIC else 5
    if relevance == 30:
        reasons.append("命中旅行决策主题")
    else:
        flags.append("主题不明确")

    recency = 0
    if published:
        age_days = max(0.0, (current - published).total_seconds() / 86400)
        if age_days <= 7:
            recency = 25
        elif age_days <= 30:
            recency = 20
        elif age_days <= 90:
            recency = 14
        elif age_days <= 180:
            recency = 8
        else:
            recency = 3
        reasons.append(f"发布时间距今约{int(age_days)}天")
    else:
        flags.append("缺少可解析发布时间")

    specificity_markers = (
        (r"\d", "包含数字或时长"),
        (r"\d{1,2}月|\d{1,2}日|昨天|今天|本周|周末|工作日|早上|晚上", "包含具体时间"),
        (r"东门|西门|南门|北门|入口|出口|停车场|地铁|公交|餐厅|景区", "包含具体地点或对象"),
        (r"建议|最好|需要|必须|不要|可以|实际|现场|到了|进场|离开", "包含可执行动作"),
    )
    specificity_hits = sum(bool(re.search(pattern, text)) for pattern, _ in specificity_markers)
    specificity = min(20, specificity_hits * 5)
    reasons.extend(label for pattern, label in specificity_markers if re.search(pattern, text))
    if specificity == 0:
        flags.append("缺少具体事实")

    firsthand = 0
    if re.search(r"我|我们|亲测|实测|现场|刚去|去过|到过|当天|这次", text):
        firsthand = 15
        reasons.append("有亲历或现场信号")
    elif re.search(r"看到|听说|朋友|据说", text):
        firsthand = 5
        flags.append("亲历程度较弱")
    else:
        flags.append("缺少亲历信号")

    likes = _xhs_numeric_value(comment.get("likes"))
    replies = _xhs_numeric_value(comment.get("reply_count"))
    engagement = min(6, int(math.log10(likes + 1) * 3))
    engagement += min(4, int(math.log10(replies + 1) * 3))
    if engagement:
        reasons.append("有互动反馈")

    is_question = bool(
        re.search(r"请问|求问|想问|有人知道|有没有人|怎么去|多少钱啊|？|\?", text)
    )
    is_promotion = topic == "推广或疑似广告"
    is_generic = bool(
        len(text) <= 18
        and not specificity_hits
        and not re.search(r"我|我们|亲测|实测|现场", text)
    )
    if is_question:
        flags.append("求问型评论")
    if is_promotion:
        flags.append("推广或疑似广告")
    if is_generic:
        flags.append("泛泛表达")

    penalty = (12 if is_question else 0) + (20 if is_promotion else 0) + (10 if is_generic else 0)
    score = max(0, min(100, relevance + recency + specificity + firsthand + engagement - penalty))
    eligible = score >= 40 and not is_question and not is_promotion and not is_generic
    return {
        "topic": topic,
        "score": score,
        "eligible": eligible,
        "reasons": reasons,
        "flags": flags,
        "published": published,
        "likes": likes,
        "reply_count": replies,
        "anchors": sorted(_xhs_comment_anchors(text)),
    }


def _flatten_xhs_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        flattened.append(comment)
        for reply in comment.get("replies") or []:
            if isinstance(reply, dict):
                child = dict(reply)
                child["_parent_comment_id"] = comment.get("comment_id")
                flattened.append(child)
    return flattened


def _xhs_comment_sort_key(
    item: tuple[dict[str, Any], dict[str, Any]],
) -> tuple[int, float, float, float]:
    comment, analysis = item
    published = analysis.get("published")
    timestamp = published.timestamp() if isinstance(published, datetime) else 0.0
    return (
        int(analysis.get("score", 0)),
        timestamp,
        float(analysis.get("likes", 0)),
        float(analysis.get("reply_count", 0)),
    )


def group_xhs_comment_signals(
    comments: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Group eligible comments that describe the same concrete signal."""
    analyzed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for comment in _flatten_xhs_comments(comments):
        analysis = score_xhs_comment(comment, now=now)
        if analysis["eligible"]:
            analyzed.append((comment, analysis))
    analyzed.sort(key=_xhs_comment_sort_key, reverse=True)

    groups: list[dict[str, Any]] = []
    for comment, analysis in analyzed:
        anchors = set(analysis["anchors"])
        normalized_text = re.sub(r"\W+", "", _xhs_comment_text(comment)).lower()
        selected: dict[str, Any] | None = None
        for group in groups:
            if group["topic"] != analysis["topic"]:
                continue
            if anchors and anchors.intersection(group["anchors"]):
                selected = group
                break
            if normalized_text and normalized_text == group["normalized_text"]:
                selected = group
                break
        if selected is None:
            selected = {
                "group_id": f"comment-group-{len(groups) + 1}",
                "topic": analysis["topic"],
                "anchors": set(anchors),
                "normalized_text": normalized_text,
                "content_signatures": set(),
                "comments": [],
            }
            groups.append(selected)
        analysis = dict(analysis)
        analysis["duplicate"] = normalized_text in selected["content_signatures"]
        selected["content_signatures"].add(normalized_text)
        selected["comments"].append((comment, analysis))
        selected["anchors"].update(anchors)
    return groups


def _xhs_contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    if isinstance(patterns, str):
        patterns = (patterns,)
    return any(re.search(pattern, text) for pattern in patterns)


def _xhs_numbers_with_unit(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(分钟|小时|元|块|公里|km|万)?", text, re.IGNORECASE):
        value = float(match.group(1))
        if match.group(2) in {"小时"}:
            value *= 60
        elif match.group(2) in {"公里", "km"}:
            value *= 1000
        values.append(value)
    return values


def _xhs_conflict_reason(topic: str, left: str, right: str) -> str | None:
    positive = {
        "开放": (r"开放|正常进|恢复|可以进|能进|已通行"),
        "需要预约": (r"需要预约|必须预约|要预约|建议预约"),
        "拥挤": (r"排队|拥挤|人多|很挤|等了"),
        "贵": (r"贵|涨价|不便宜"),
    }
    negative = {
        "开放": (r"关闭|封闭|施工|不能进|不让进|封路"),
        "需要预约": (r"无需预约|不用预约|不需要预约|免预约"),
        "拥挤": (r"不排队|人少|空旷|不用等"),
        "贵": (r"便宜|免费|不贵"),
    }
    if topic == "开放、施工与搬迁":
        if (_xhs_contains_any(left, positive["开放"]) and _xhs_contains_any(right, negative["开放"])) or (
            _xhs_contains_any(right, positive["开放"]) and _xhs_contains_any(left, negative["开放"])
        ):
            return "同一主题同时出现开放与关闭/施工表述"
    if topic == "预约、购票与检票":
        if (_xhs_contains_any(left, positive["需要预约"]) and _xhs_contains_any(right, negative["需要预约"])) or (
            _xhs_contains_any(right, positive["需要预约"]) and _xhs_contains_any(left, negative["需要预约"])
        ):
            return "同一主题同时出现需要预约与无需预约表述"
    if topic == "排队与拥挤":
        if (_xhs_contains_any(left, positive["拥挤"]) and _xhs_contains_any(right, negative["拥挤"])) or (
            _xhs_contains_any(right, positive["拥挤"]) and _xhs_contains_any(left, negative["拥挤"])
        ):
            return "同一主题同时出现拥挤与不拥挤表述"
    if topic == "价格":
        left_values = _xhs_numbers_with_unit(left)
        right_values = _xhs_numbers_with_unit(right)
        if left_values and right_values:
            left_value = max(left_values)
            right_value = max(right_values)
            if max(left_value, right_value) and abs(left_value - right_value) / max(left_value, right_value) >= 0.2:
                return "同一主题出现明显不同的价格或费用"
    if topic == "入口与路线":
        left_entries = set(re.findall(r"[东南西北]门", left))
        right_entries = set(re.findall(r"[东南西北]门", right))
        recommendation = r"建议|最好|从.+进|从.+走|优先"
        if left_entries and right_entries and left_entries.isdisjoint(right_entries):
            if re.search(recommendation, left) and re.search(recommendation, right):
                return "同一主题出现不同入口或路线建议"
    return None


def detect_xhs_comment_conflicts(
    groups: list[dict[str, Any]],
    comments: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Find explicit or material disagreements between grouped signals."""
    conflicts: list[dict[str, Any]] = []
    for group in groups:
        for index, (left_comment, _) in enumerate(group["comments"]):
            for right_comment, _ in group["comments"][index + 1 :]:
                reason = _xhs_conflict_reason(
                    group["topic"],
                    _xhs_comment_text(left_comment),
                    _xhs_comment_text(right_comment),
                )
                if reason:
                    conflicts.append(
                        {
                            "topic": group["topic"],
                            "left_group_id": group["group_id"],
                            "right_group_id": group["group_id"],
                            "reason": reason,
                        }
                    )
    for index, left_group in enumerate(groups):
        left_texts = [_xhs_comment_text(comment) for comment, _ in left_group["comments"]]
        left_text = " ".join(left_texts)
        for right_group in groups[index + 1 :]:
            if left_group["topic"] != right_group["topic"]:
                continue
            right_text = " ".join(
                _xhs_comment_text(comment) for comment, _ in right_group["comments"]
            )
            reason = _xhs_conflict_reason(left_group["topic"], left_text, right_text)
            if reason:
                conflicts.append(
                    {
                        "topic": left_group["topic"],
                        "left_group_id": left_group["group_id"],
                        "right_group_id": right_group["group_id"],
                        "reason": reason,
                    }
                )
    return conflicts


def build_xhs_comment_insights(
    comments: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the compact evidence layer consumed by travel planning."""
    flattened = _flatten_xhs_comments(comments)
    groups = group_xhs_comment_signals(flattened, now=now)
    conflicts = detect_xhs_comment_conflicts(groups, comments=flattened)
    conflict_counts: dict[str, int] = {}
    for conflict in conflicts:
        for group_id in {conflict["left_group_id"], conflict["right_group_id"]}:
            conflict_counts[group_id] = conflict_counts.get(group_id, 0) + 1

    insights: list[dict[str, Any]] = []
    duplicate_count = 0
    for group in groups:
        duplicate_count += sum(
            bool(analysis.get("duplicate"))
            for _, analysis in group["comments"]
        )
        ranked = sorted(
            (
                (comment, analysis)
                for comment, analysis in group["comments"]
                if not analysis.get("duplicate")
            ),
            key=_xhs_comment_sort_key,
            reverse=True,
        )
        if not ranked:
            continue
        best_comment, best_analysis = ranked[0]
        conflict_count = conflict_counts.get(group["group_id"], 0)
        needs_recheck = group["topic"] in XHS_COMMENT_RECHECK_TOPICS or conflict_count > 0
        if conflict_count:
            confidence = "low"
            recheck_reason = "评论存在相互冲突的表述"
        elif len(ranked) >= 2:
            confidence = "medium"
            recheck_reason = "社区评论只能提供近期体验信号"
        else:
            confidence = "low"
            recheck_reason = "只有单条合格评论支持"
        evidence = [
            {
                "comment_id": comment.get("comment_id"),
                "author": comment.get("author"),
                "published_at": comment.get("published_at"),
                "likes": comment.get("likes"),
                "reply_count": comment.get("reply_count"),
                "value_score": analysis["score"],
                "excerpt": xhs_excerpt(_xhs_comment_text(comment), limit=180),
            }
            for comment, analysis in ranked[:5]
        ]
        published_dates = [
            analysis["published"]
            for _, analysis in ranked
            if isinstance(analysis.get("published"), datetime)
        ]
        latest = max(published_dates).isoformat(timespec="seconds") if published_dates else None
        insights.append(
            {
                "insight_id": group["group_id"],
                "topic": group["topic"],
                "claim": _xhs_comment_text(best_comment),
                "latest_evidence_at": latest,
                "support_count": len(ranked),
                "contradiction_count": conflict_count,
                "confidence": confidence,
                "needs_official_recheck": needs_recheck,
                "recheck_reason": recheck_reason if needs_recheck else None,
                "evidence": evidence,
            }
        )

    insights.sort(
        key=lambda insight: (
            insight["confidence"] == "medium",
            insight["support_count"],
            insight["latest_evidence_at"] or "",
        ),
        reverse=True,
    )
    needs_recheck = [
        {
            "insight_id": insight["insight_id"],
            "topic": insight["topic"],
            "claim": insight["claim"],
            "reason": insight["recheck_reason"],
        }
        for insight in insights
        if insight["needs_official_recheck"]
    ]
    eligible_count = sum(
        1
        for group in groups
        for _, analysis in group["comments"]
        if not analysis.get("duplicate")
    )
    return {
        "comment_insights": insights,
        "needs_recheck": needs_recheck,
        "comment_analysis": {
            "input_count": len(flattened),
            "eligible_count": eligible_count,
            "filtered_count": len(flattened) - eligible_count,
            "duplicate_count": duplicate_count,
            "group_count": len(insights),
            "scoring": "相关性30% + 时效性25% + 具体程度20% + 亲历信号15% + 互动10%；互动不代表真实性",
        },
    }


def xhs_search(args: argparse.Namespace) -> dict[str, Any]:
    require_xhs_login(query_args=args)
    value_map = {
        "sort_by": {
            "relevance": "综合", "latest": "最新", "most_liked": "最多点赞",
            "most_commented": "最多评论", "most_collected": "最多收藏",
        },
        "note_type": {"all": "不限", "video": "视频", "image": "图文"},
        "publish_time": {"all": "不限", "day": "一天内", "week": "一周内", "half_year": "半年内"},
        "search_scope": {"all": "不限", "viewed": "已看过", "unviewed": "未看过", "following": "已关注"},
        "location": {"all": "不限", "same_city": "同城", "nearby": "附近"},
    }
    filters = {
        "sort_by": args.sort_by,
        "note_type": args.note_type,
        "publish_time": args.publish_time,
        "search_scope": args.search_scope,
        "location": args.location,
    }
    upstream_filters = {
        key: value_map[key][value]
        for key, value in filters.items()
        if value not in {"all", "relevance"}
    }
    arguments: dict[str, Any] = {"keyword": args.keyword}
    if upstream_filters:
        arguments["filters"] = upstream_filters
    data = xhs_mcp_call(
        "search_feeds",
        arguments,
    )
    if not isinstance(data, dict):
        raise SourceError("xiaohongshu-mcp 搜索返回格式无效")
    feeds = [feed for feed in (data.get("feeds") or []) if isinstance(feed, dict)]
    for feed in feeds:
        if feed.get("id") and not feed.get("sourceUrl"):
            feed["sourceUrl"] = f"https://www.xiaohongshu.com/explore/{feed['id']}"
    cache_xhs_mcp_tokens(feeds, args.keyword)
    normalized = [normalize_xhs_mcp_feed(feed) for feed in feeds[: args.limit]]
    return {
        "status": "community_reported",
        "provider": "xpzouying/xiaohongshu-mcp（非官方）",
        "query": {"keyword": args.keyword, "filters": filters},
        "results": normalized,
        "count": len(normalized),
        "checked_at": checked_at(),
        "source": {
            "title": "小红书公开笔记搜索",
            "url": "https://www.xiaohongshu.com/explore",
            "kind": "community_search",
        },
        "disclaimer": "临时令牌只用于详情读取；社区内容必须与官方来源交叉核验。",
    }


def xhs_detail(args: argparse.Namespace) -> dict[str, Any]:
    require_xhs_login(query_args=args)
    cached = read_xhs_mcp_token_cache().get("notes", {}).get(args.note_id)
    if not cached or not cached.get("xsec_token"):
        raise SourceError("未找到该笔记的临时访问令牌；请先用 xhs-search 搜索，再读取详情")
    all_comments = bool(getattr(args, "all_comments", False))
    include_replies = bool(getattr(args, "include_replies", False)) and all_comments
    comment_limit = getattr(args, "comment_limit", 20)
    reply_limit = getattr(args, "reply_limit", 10)
    scroll_speed = getattr(args, "scroll_speed", "normal")
    if comment_limit < 1 or comment_limit > 100:
        raise SourceError("评论数量必须在 1 到 100 之间")
    if reply_limit < 1 or reply_limit > 50:
        raise SourceError("回复阈值必须在 1 到 50 之间")
    arguments = {
        "feed_id": args.note_id,
        "xsec_token": cached["xsec_token"],
        "load_all_comments": all_comments,
    }
    if all_comments:
        arguments.update(
            {
                "limit": comment_limit,
                "click_more_replies": include_replies,
                "reply_limit": reply_limit,
                "scroll_speed": scroll_speed,
            }
        )
    data = xhs_mcp_call(
        "get_feed_detail",
        arguments,
    )
    if not isinstance(data, dict):
        raise SourceError("xiaohongshu-mcp 详情返回格式无效")
    detail = data.get("data") if isinstance(data.get("data"), dict) else data
    note = detail.get("note") or {}
    user = note.get("user") or {}
    interactions = note.get("interactInfo") or {}
    comments = normalize_xhs_comments(detail, include_replies=include_replies)
    comment_insights = build_xhs_comment_insights(comments)
    note_id = str(note.get("noteId") or args.note_id)
    image_count = cache_images(xhs_mcp_token_cache_path().parent, note_id, note.get("imageList") or [])
    return {
        "status": "community_reported",
        "provider": "xpzouying/xiaohongshu-mcp（非官方）",
        "note": {
            "note_id": note.get("noteId") or args.note_id,
            "title": note.get("title"),
            "description_excerpt": xhs_excerpt(note.get("desc") or note.get("body")),
            "note_type": note.get("type"),
            "published_at": unix_time(note.get("time")),
            "ip_location": note.get("ipLocation"),
            "author": user.get("nickname") or user.get("nickName"),
            "interactions": {
                "likes": interactions.get("likedCount"),
                "comments": interactions.get("commentCount"),
                "collections": interactions.get("collectedCount"),
                "shares": interactions.get("sharedCount"),
            },
            "source_url": cached.get("source_url"),
        },
        "comments": comments,
        "comment_insights": comment_insights["comment_insights"],
        "needs_recheck": comment_insights["needs_recheck"],
        "comment_analysis": comment_insights["comment_analysis"],
        "image_reading": {"image_count": image_count, "status": "not_read" if image_count else "no_images",
                          "body_missing": not bool(str(note.get("desc") or note.get("body") or "").strip()),
                          "image_indices": list(range(1, image_count + 1))},
        "comment_collection": {
            "mode": "expanded" if all_comments else "sampled",
            "top_level_limit": comment_limit if all_comments else 10,
            "include_replies": include_replies,
            "reply_limit": reply_limit if all_comments and include_replies else None,
            "scroll_speed": scroll_speed if all_comments else None,
        },
        "checked_at": checked_at(),
        "disclaimer": "评论洞察仅作为近期社区体验信号；默认只读取前10条一级评论，完整评论区和临时令牌不归档，开放、价格、预约、交通和安全等关键事实需与官方来源交叉核验。",
    }


def xhs_images(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return prepare_images(xhs_mcp_token_cache_path().parent, args.note_id, args.indices)
    except ValueError as error:
        raise SourceError(str(error)) from None


def get_amap_key() -> str | None:
    return os.environ.get("AMAP_API_KEY") or os.environ.get("AMAP_MAPS_API_KEY")


def output(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def capabilities(_: argparse.Namespace) -> dict[str, Any]:
    return {
        "attraction_official": {
            "adapter_available": True,
            "live_query_available": False,
            "integration": "公共搜索定位官方页 + 证据归档 + 官方 HTTPS 复核链接",
            "fields": ["门票", "开放时间", "停止入场", "预约", "入口", "临时公告"],
        },
        "weather": {
            "available": True,
            "provider": "Open-Meteo",
            "credential_required": False,
            "fields": ["近期预报", "降雨", "风速", "紫外线", "日出", "日落", "图标代码", "天气查看入口", "预警复核入口"],
            "warning_fallback": "https://www.nmc.cn/publish/alarm.html",
        },
        "map": {
            "available": True,
            "providers": [
                {
                    "name": "高德开放平台",
                    "available": bool(get_amap_key()),
                    "scope": "中国境内 POI 与路径",
                    "credential_required": True,
                    "transport": "官方 stdio MCP + Web 服务 API",
                    "mcp_server": "amap-maps",
                    "mcp_package": "@amap/amap-maps-mcp-server@0.0.8",
                    "skill": "$amap-maps",
                },
                {"name": "Nominatim / OpenStreetMap", "available": True, "scope": "境外地点与入口候选检索", "credential_required": False},
            ],
            "accepted_env": ["AMAP_API_KEY", "AMAP_MAPS_API_KEY"],
            "local_config_file": str(LOCAL_CONFIG_FILE),
            "embed_map": {
                "available": True,
                "credential_required": False,
                "provider": "按设备切换的高德消费端路线页 iframe",
                "caveat": "桌面使用通用路线页，移动端驾车使用 carmap；首次登录提示关闭后由高德保存展示状态",
            },
            "fields": ["POI/入口", "步行", "公交/地铁", "驾车", "门到门基础耗时"],
        },
        "train": {
            "adapter_available": True,
            "live_query_available": provider_capabilities()["fliggy_flyai"]["live_query_available"],
            "provider": "飞猪 FlyAI / 飞常准 Tripmatch；12306 最终复核",
            "integration": "授权平台只读查询 + 12306 官方查询页用户会话交接",
            "reason": "不调用或逆向 12306 非公开接口；平台结果不能替代 12306 最终余票与出票确认",
            "manual_url": "https://www.12306.cn/index/",
        },
        "ctrip": {
            "adapter_available": True,
            "live_query_available": False,
            "provider": "携程",
            "integration": "平台查询页用户会话交接",
            "reason": "未配置获授权的官方数据接口",
            "manual_url": "https://www.ctrip.com/",
        },
        "travel_inventory": {
            "snapshot_schema": "travel-source-snapshot/v1",
            "providers": provider_capabilities(),
            "consent_required_per_query": False,
            "query_policy": "已配置的只读供应商查询在深度规划中直接执行，不逐次请求用户确认",
            "boundary": "只读查询；不提交订单、不占座、不付款；价格、库存和运行状态需在下单前复核",
            "generic_flyai_skill": "$flyai（航班、火车、酒店、景点、活动和旅行产品）",
        },
        "xiaohongshu": {
            "adapter_available": True,
            "live_query_requires": ["固定版本本地 MCP 已安装并启动", "用户本人已扫码登录"],
            "provider": "xpzouying/xiaohongshu-mcp（非官方，固定 v2.5.0）",
            "transport": "MCP Streamable HTTP + 独立无头浏览器",
            "fields": ["近期玩法", "昼夜体验", "入口体验", "拥挤与避坑", "包车和行李体验"],
            "setup": "python3 skills/xiaohongshu/scripts/setup.py install",
            "start": "python3 skills/xiaohongshu/scripts/setup.py start",
            "endpoint": xhs_mcp_url(),
            "travel_default": "read_only",
        },
        "food_discovery": {
            "enabled": False,
            "status": "paused",
            "reason": "餐饮研究仅使用小红书和高德；抖音、点评实验代码保留，不注册 MCP",
        },
        "checked_at": checked_at(),
    }


def _preflight_failure(
    source_id: str,
    kind: str,
    required: bool,
    error: Exception,
    started_at: float,
) -> dict[str, Any]:
    failure_kind = error.failure_kind if isinstance(error, AdapterError) else "unavailable"
    return {
        "source_id": source_id,
        "kind": kind,
        "required": required,
        "status": "unavailable",
        "failure_kind": failure_kind,
        "message": str(error),
        "latency_ms": round((time.monotonic() - started_at) * 1000),
    }


def _mcp_preflight(
    *,
    source_id: str,
    command: list[str],
    environment: dict[str, str],
    expected_tools: set[str],
    smoke_tool: str,
    smoke_arguments: dict[str, Any],
    required: bool,
    timeout: int,
    skip_upstream: bool,
) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        transport = probe_mcp_stdio(command, environment, timeout, expected_tools)
        if skip_upstream:
            upstream = {"status": "skipped", "reason": "--skip-upstream"}
            status = "degraded"
        else:
            call_mcp_stdio(command, smoke_tool, smoke_arguments, environment, timeout)
            upstream = {"status": "ready", "tool": smoke_tool}
            status = "ready"
        return {
            "source_id": source_id,
            "kind": "mcp_stdio",
            "required": required,
            "status": status,
            "transport": transport,
            "upstream": upstream,
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }
    except (AdapterError, OSError, RuntimeError, SourceEnvironmentError) as error:
        return _preflight_failure(source_id, "mcp_stdio", required, error, started_at)


def _amap_preflight(args: argparse.Namespace, required: bool) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        environment = load_amap_environment(os.environ.copy())
        if not environment.get("AMAP_MAPS_API_KEY"):
            raise AdapterError("credential_missing", "未配置 AMAP_MAPS_API_KEY 或 AMAP_API_KEY")
        command = amap_mcp_command()
    except (AdapterError, OSError, RuntimeError, SourceEnvironmentError) as error:
        return _preflight_failure("amap-maps", "mcp_stdio", required, error, started_at)
    return _mcp_preflight(
        source_id="amap-maps",
        command=command,
        environment=environment,
        expected_tools={"maps_weather", "maps_text_search"},
        smoke_tool="maps_weather",
        smoke_arguments={"city": args.city},
        required=required,
        timeout=args.timeout,
        skip_upstream=args.skip_upstream,
    )


def _variflight_preflight(
    args: argparse.Namespace,
    provider_id: str,
    source_id: str,
    expected_tools: set[str],
    smoke_tool: str,
    smoke_arguments: dict[str, Any],
    required: bool,
) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        provider = adapter(provider_id)
        environment = provider.environment()
        spec = PROVIDERS[provider_id]
        if spec.credential_env and not environment.get(spec.credential_env):
            raise AdapterError("credential_missing", f"未配置 {spec.credential_env}")
        command = provider_command(spec)
    except (AdapterError, OSError, RuntimeError, SourceEnvironmentError) as error:
        return _preflight_failure(source_id, "mcp_stdio", required, error, started_at)
    return _mcp_preflight(
        source_id=source_id,
        command=command,
        environment=environment,
        expected_tools=expected_tools,
        smoke_tool=smoke_tool,
        smoke_arguments=smoke_arguments,
        required=required,
        timeout=args.timeout,
        skip_upstream=args.skip_upstream,
    )


def _flyai_preflight(args: argparse.Namespace, required: bool) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        provider = adapter("fliggy_flyai")
        environment = provider.environment()
        command = provider_command(PROVIDERS["fliggy_flyai"])
        if args.skip_upstream:
            upstream = {"status": "skipped", "reason": "--skip-upstream"}
            status = "degraded"
        else:
            run_json_cli(
                command + ["keyword-search", "--query", f"{args.city} 景点"],
                environment,
                args.timeout,
            )
            upstream = {"status": "ready", "command": "keyword-search"}
            status = "ready"
        return {
            "source_id": "flyai",
            "kind": "cli_to_vendor_mcp_api",
            "required": required,
            "status": status,
            "runtime": {"status": "ready", "pinned_version": PROVIDERS["fliggy_flyai"].version},
            "upstream": upstream,
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }
    except (AdapterError, OSError, RuntimeError, SourceEnvironmentError) as error:
        return _preflight_failure("flyai", "cli_to_vendor_mcp_api", required, error, started_at)


def _weather_preflight(args: argparse.Namespace, required: bool) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        if args.skip_upstream:
            return {
                "source_id": "open-meteo",
                "kind": "public_api",
                "required": required,
                "status": "degraded",
                "upstream": {"status": "skipped", "reason": "--skip-upstream"},
                "latency_ms": round((time.monotonic() - started_at) * 1000),
            }
        payload = request_json(
            OPEN_METEO_FORECAST,
            {
                "latitude": 39.9042,
                "longitude": 116.4074,
                "daily": "weather_code",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=args.timeout,
        )
        if not isinstance(payload, dict) or not payload.get("daily"):
            raise SourceError("Open-Meteo 健康探测未返回 daily 数据")
        return {
            "source_id": "open-meteo",
            "kind": "public_api",
            "required": required,
            "status": "ready",
            "upstream": {"status": "ready"},
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }
    except (OSError, RuntimeError, SourceError) as error:
        return _preflight_failure("open-meteo", "public_api", required, error, started_at)


def _xiaohongshu_preflight(args: argparse.Namespace, required: bool) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        transport = probe_mcp_http(
            xhs_mcp_url(),
            args.timeout,
            {"check_login_status", "search_feeds", "get_feed_detail"},
            os.environ.get("XHS_MCP_AUTH_TOKEN"),
        )
        if args.skip_upstream:
            session = {"status": "skipped", "reason": "--skip-upstream"}
            status = "degraded"
        else:
            session = xhs_login_status(argparse.Namespace(timeout=args.timeout))
            if session.get("status") != "authenticated":
                raise SourceError("xiaohongshu-mcp 已连接，但用户尚未扫码登录")
            status = "ready"
        return {
            "source_id": "xiaohongshu",
            "kind": "mcp_streamable_http",
            "required": required,
            "status": status,
            "transport": transport,
            "session": session,
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }
    except (AdapterError, OSError, RuntimeError, SourceError) as error:
        return _preflight_failure(
            "xiaohongshu", "mcp_streamable_http", required, error, started_at
        )


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "phase", None):
        args.phase = "preflight"
    required = set(args.require or [])
    selected = set(PREFLIGHT_SOURCES) if getattr(args, "all_sources", False) else set(getattr(args, "source", None) or []) | required
    probes = {
        "amap-maps": lambda: _amap_preflight(args, "amap-maps" in required),
        "variflight-aviation": lambda: _variflight_preflight(args, "variflight_aviation", "variflight-aviation",
            {"getFutureWeatherByAirport", "searchFlightsByDepArr"}, "getFutureWeatherByAirport",
            {"airport": args.airport}, "variflight-aviation" in required),
        "variflight-tripmatch": lambda: _variflight_preflight(args, "variflight_tripmatch", "variflight-tripmatch",
            {"searchTrainStations", "searchTrainTicketsByCity"}, "searchTrainStations",
            {"query": args.city}, "variflight-tripmatch" in required),
        "flyai": lambda: _flyai_preflight(args, "flyai" in required),
        "open-meteo": lambda: _weather_preflight(args, "open-meteo" in required),
        "xiaohongshu": lambda: _xiaohongshu_preflight(args, "xiaohongshu" in required),
    }
    checks = []
    for source_id in PREFLIGHT_SOURCES:
        if source_id not in selected:
            continue
        def probe(source_id=source_id):
            result = probes[source_id]()
            result["checked_at"] = checked_at()
            return result
        if args.skip_upstream:
            # An offline handshake must not consume or create a live health proof.
            result = probe()
            result["execution"] = {"mode": "fresh", "offline": True}
        else:
            result = dict(run_query(args, "preflight-" + source_id, {}, probe, source_id,
                                    reuse_key=health_key(source_id)))
            result["execution"] = getattr(args, "_execution", {"mode": "fresh"})
        result["required"] = source_id in required
        if source_id == "xiaohongshu" and result.get("status") == "authenticated":
            result.update(status="ready", scope="observed_operation", operation="check-login-status")
        if "latency_ms" in result:
            result["probe_latency_ms"] = result["latency_ms"]
        result["latency_ms"] = result["execution"].get("elapsed_ms", result.get("latency_ms", 0))
        checks.append(result)
    required_failures = [check["source_id"] for check in checks if check["required"] and check["status"] != "ready"]
    status = ("not_requested" if not checks else "unavailable" if required_failures else
              "degraded" if any(check["status"] != "ready" for check in checks) else "ready")
    return {
        "schema_version": "travel-source-preflight/v1", "status": status,
        "selected_sources": sorted(selected), "skipped_sources": sorted(set(PREFLIGHT_SOURCES) - selected),
        "required_sources": sorted(required), "required_failures": required_failures,
        "checks": checks, "checked_at": checked_at(),
    }


def provider_query(
    args: argparse.Namespace,
    provider_id: str,
    product_type: str,
    tool: str,
    arguments: dict[str, Any],
    query: dict[str, Any],
) -> dict[str, Any]:
    workspace = getattr(args, "workspace", None)
    task_id = getattr(args, "task_id", None)
    if bool(workspace) != bool(task_id):
        raise AdapterError("invalid_response", "--workspace 和 --task-id 必须同时提供")
    if workspace:
        root = execution_log.workspace_root(workspace)
        assignment_path = root / "assignments" / f"{execution_log.identifier(task_id)}.json"
        if not assignment_path.is_file():
            raise AdapterError("workspace_error", "报价查询的任务尚未分配，未请求供应商")
        assignment = read_workspace_json(assignment_path)
        if product_type not in INVENTORY_PRODUCTS_BY_DOMAIN.get(assignment.get("domain"), set()):
            raise AdapterError("workspace_error", "此任务领域不接收该产品快照，未请求供应商")
    source_id = {"fliggy_flyai": "flyai", "variflight_aviation": "variflight-aviation",
                 "variflight_tripmatch": "variflight-tripmatch"}[provider_id]
    snapshot = run_query(args, tool, {"provider": provider_id, "product_type": product_type,
        "arguments": arguments, "query": query, "limit": getattr(args, "limit", None)},
        lambda: adapter(provider_id).query(product_type=product_type, tool=tool, arguments=arguments,
            query=query, timeout=args.timeout, limit=getattr(args, "limit", None)), source_id, prove_health=True)
    if workspace and task_id:
        try:
            store_source_snapshot(Path(workspace), task_id, snapshot)
        except WorkspaceError as error:
            raise AdapterError("workspace_error", f"无法写入研究 workspace：{error}") from error
    return snapshot


def flyai_flight(args: argparse.Namespace) -> dict[str, Any]:
    values = {
        "origin": args.origin,
        "destination": args.destination,
        "dep_date": args.date,
        "back_date": args.return_date,
        "journey_type": args.journey_type,
        "seat_class_name": args.seat_class,
        "max_price": args.max_price,
        "sort_type": args.sort_type,
    }
    return provider_query(args, "fliggy_flyai", "flight", "search-flight", values, values)


def provider_coverage_query(
    args: argparse.Namespace,
    handler: Any,
    product_type: str,
) -> dict[str, Any]:
    """Run the standard feasibility-first sort coverage and persist every snapshot."""
    snapshots = []
    executions = []
    dimensions = getattr(args, "dimension", None)
    selected_sorts = set(TRANSPORT_COVERAGE_SORTS) if not dimensions else set().union(*(
        {"time": {6, 7, 4}, "price": {3}, "recommendation": {2}}[dimension] for dimension in dimensions))
    sorts = [value for value in TRANSPORT_COVERAGE_SORTS if value in selected_sorts]
    for sort_type in sorts:
        query_args = argparse.Namespace(**vars(args))
        query_args.sort_type = sort_type
        snapshots.append(handler(query_args))
        if getattr(query_args, "_execution", None):
            executions.append({"sort_type": sort_type, **query_args._execution})
    return {
        "schema_version": "travel-source-snapshot-batch/v1",
        "status": "platform_reported",
        "product_type": product_type,
        "coverage": [
            {"sort_type": value, "purpose": TRANSPORT_SORT_LABELS[value]}
            for value in sorts
        ],
        "snapshot_ids": [snapshot["snapshot_id"] for snapshot in snapshots],
        "snapshots": snapshots,
        **({"execution": {"mode": "batch", "queries": executions}} if executions else {}),
        "checked_at": checked_at(),
        "disclaimer": "多排序查询用于覆盖时间与价格边界；结果不代表库存持续有效或已经预订。",
    }


def flyai_flight_coverage(args: argparse.Namespace) -> dict[str, Any]:
    return provider_coverage_query(args, flyai_flight, "flight")


def flyai_train(args: argparse.Namespace) -> dict[str, Any]:
    values = {
        "origin": args.origin,
        "destination": args.destination,
        "dep_date": args.date,
        "journey_type": args.journey_type,
        "seat_class_name": args.seat_class,
        "transport_no": args.train_number,
        "max_price": args.max_price,
        "sort_type": args.sort_type,
    }
    return provider_query(args, "fliggy_flyai", "train", "search-train", values, values)


def flyai_train_coverage(args: argparse.Namespace) -> dict[str, Any]:
    return provider_coverage_query(args, flyai_train, "train")


def flyai_hotel(args: argparse.Namespace) -> dict[str, Any]:
    values = {
        "dest_name": args.destination,
        "key_words": args.keywords,
        "poi_name": args.poi,
        "hotel_types": args.hotel_type,
        "sort": args.sort,
        "check_in_date": args.check_in,
        "check_out_date": args.check_out,
        "hotel_stars": args.stars,
        "hotel_bed_types": args.bed_type,
        "max_price": args.max_price,
    }
    query = {
        **values,
        "requested_occupancy": {
            "adults": getattr(args, "adults", None),
            "rooms": getattr(args, "rooms", None),
        },
        "supplier_capacity_filter_supported": False,
    }
    return provider_query(args, "fliggy_flyai", "hotel", "search-hotel", values, query)


def variflight_flight_search(args: argparse.Namespace) -> dict[str, Any]:
    arguments = {
        "depcity" if args.origin_kind == "city" else "dep": args.origin,
        "arrcity" if args.destination_kind == "city" else "arr": args.destination,
        "date": args.date,
    }
    query = {
        "origin": args.origin,
        "origin_kind": args.origin_kind,
        "destination": args.destination,
        "destination_kind": args.destination_kind,
        "date": args.date,
    }
    return provider_query(
        args, "variflight_aviation", "flight", "searchFlightsByDepArr", arguments, query
    )


def variflight_flight_number(args: argparse.Namespace) -> dict[str, Any]:
    values = {"fnum": args.flight_number, "date": args.date, "dep": args.origin, "arr": args.destination}
    return provider_query(
        args, "variflight_aviation", "flight", "searchFlightsByNumber", values, values
    )


def variflight_flight_price(args: argparse.Namespace) -> dict[str, Any]:
    arguments = {"dep_city": args.origin, "arr_city": args.destination, "dep_date": args.date}
    return provider_query(
        args, "variflight_aviation", "flight", "getFlightPriceByCities", arguments, arguments
    )


def variflight_flight_comfort(args: argparse.Namespace) -> dict[str, Any]:
    values = {"fnum": args.flight_number, "date": args.date, "dep": args.origin, "arr": args.destination}
    return provider_query(
        args, "variflight_aviation", "flight", "flightHappinessIndex", values, values
    )


def variflight_train(args: argparse.Namespace) -> dict[str, Any]:
    arguments = {"from": args.origin, "to": args.destination, "date": args.date}
    return provider_query(
        args, "variflight_tripmatch", "train", "searchTrainTicketsByCity", arguments, arguments
    )


def variflight_train_stations(args: argparse.Namespace) -> dict[str, Any]:
    arguments = {"query": args.query}
    return provider_query(
        args, "variflight_tripmatch", "train_station", "searchTrainStations", arguments, arguments
    )


def variflight_air_rail(args: argparse.Namespace) -> dict[str, Any]:
    arguments = {"depcity": args.origin, "arrcity": args.destination, "depdate": args.date}
    return provider_query(
        args,
        "variflight_tripmatch",
        "air_rail_transfer",
        "getFlightAndTrainTransferInfo",
        arguments,
        arguments,
    )


def resolve_location(name: str) -> dict[str, Any]:
    data = request_json(
        OPEN_METEO_GEOCODING,
        {"name": name, "count": 1, "language": "zh", "format": "json"},
    )
    results = data.get("results") or []
    if not results:
        raise SourceError(f"未找到地点：{name}")
    result = results[0]
    return {
        "name": result.get("name"),
        "admin1": result.get("admin1"),
        "country": result.get("country"),
        "latitude": result.get("latitude"),
        "longitude": result.get("longitude"),
        "timezone": result.get("timezone"),
    }


def weather(args: argparse.Namespace) -> dict[str, Any]:
    if args.location:
        location = resolve_location(args.location)
        latitude, longitude = location["latitude"], location["longitude"]
    else:
        if args.latitude is None or args.longitude is None:
            raise SourceError("必须提供 --location，或同时提供 --latitude 和 --longitude")
        latitude, longitude = args.latitude, args.longitude
        location = {
            "name": args.name or f"{latitude},{longitude}",
            "latitude": latitude,
            "longitude": longitude,
        }

    daily_fields = [
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "precipitation_probability_max",
        "wind_speed_10m_max",
        "wind_gusts_10m_max",
        "uv_index_max",
        "sunrise",
        "sunset",
    ]
    data = request_json(
        OPEN_METEO_FORECAST,
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": ",".join(daily_fields),
            "timezone": "auto",
            "forecast_days": args.days,
        },
    )
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    forecasts = []
    for index, date in enumerate(dates):
        row = {"date": date}
        for field in daily_fields:
            values = daily.get(field) or []
            row[field] = values[index] if index < len(values) else None
        row["summary"] = WEATHER_CODES.get(row["weather_code"], f'天气代码 {row["weather_code"]}')
        row["icon_code"] = weather_icon_code(row["weather_code"])
        forecasts.append(row)
    location_label = location.get("name") or f"{latitude},{longitude}"
    return {
        "status": "platform_reported",
        "provider": "Open-Meteo",
        "location": location,
        "timezone": data.get("timezone"),
        "daily_units": data.get("daily_units") or {},
        "forecasts": forecasts,
        "checked_at": checked_at(),
        "action_links": [
            {
                "type": "weather",
                "label": f"在 Windy 查看{location_label}天气",
                "provider": "Windy",
                "url": "https://www.windy.com/",
                "checked_at": checked_at(),
                "disclaimer": f"在站内定位 {latitude},{longitude}；出发前48小时复核",
            },
            {
                "type": "weather_warning",
                "label": "查看中央气象台预警",
                "provider": "中央气象台",
                "url": "https://www.nmc.cn/publish/alarm.html",
                "checked_at": checked_at(),
                "disclaimer": "中国境内预警以官方发布为准",
            },
        ],
        "sources": [
            {"title": "Open-Meteo Forecast API", "url": "https://open-meteo.com/en/docs", "kind": "weather_api"},
            {"title": "中央气象台预警", "url": "https://www.nmc.cn/publish/alarm.html", "kind": "manual_warning_check"},
        ],
        "disclaimer": "预报会变化；出发前 48 小时复核，中国境内预警以官方气象机构为准。",
    }


def amap_request(path: str, params: dict[str, Any]) -> dict[str, Any]:
    key = get_amap_key()
    if not key:
        raise SourceError("未配置高德 Key；请设置 AMAP_API_KEY 或 AMAP_MAPS_API_KEY")
    data = request_json(f"{AMAP_API}{path}", {"key": key, **params})
    if str(data.get("status")) != "1":
        raise SourceError(f'高德 API 返回错误：{data.get("info") or "unknown"}')
    return data


def amap_place(args: argparse.Namespace) -> dict[str, Any]:
    data = amap_request(
        "/v5/place/text",
        {"keywords": args.keywords, "region": args.city, "page_size": args.limit, "show_fields": "business"},
    )
    places = []
    for poi in (data.get("pois") or [])[: args.limit]:
        name = poi.get("name")
        location = poi.get("location")
        marker_url = None
        if name and location:
            marker_url = "https://uri.amap.com/marker?" + urlencode(
                {
                    "position": location,
                    "name": name,
                    "src": "travel-planning",
                    "coordinate": "gaode",
                    "callnative": 0,
                }
            )
        places.append(
            {
                "id": poi.get("id"),
                "name": name,
                "address": poi.get("address"),
                "location": location,
                "type": poi.get("type"),
                "business": poi.get("business"),
                "map_url": marker_url,
            }
        )
    return {
        "status": "platform_reported",
        "provider": "高德开放平台",
        "query": {"keywords": args.keywords, "city": args.city},
        "places": places,
        "checked_at": checked_at(),
        "source": {"title": "高德地图 Web 服务 API", "url": "https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch"},
        "disclaimer": "景区入口名称与开放状态仍需景区官方来源确认。",
    }


def osm_place(args: argparse.Namespace) -> dict[str, Any]:
    """Low-volume overseas place lookup through the public Nominatim endpoint."""
    data = request_json(
        NOMINATIM_SEARCH,
        {
            "q": args.query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": args.limit,
            "accept-language": args.language,
            "countrycodes": args.countrycodes,
        },
    )
    if not isinstance(data, list):
        raise SourceError("Nominatim 未返回有效地点列表")
    places = []
    for item in data[: args.limit]:
        lat, lon = item.get("lat"), item.get("lon")
        map_url = None
        if lat and lon:
            map_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=18/{lat}/{lon}"
        places.append(
            {
                "osm_type": item.get("osm_type"),
                "osm_id": item.get("osm_id"),
                "name": item.get("name") or item.get("display_name"),
                "display_name": item.get("display_name"),
                "latitude": lat,
                "longitude": lon,
                "category": item.get("category"),
                "type": item.get("type"),
                "address": item.get("address") or {},
                "map_url": map_url,
            }
        )
    return {
        "status": "platform_reported",
        "provider": "Nominatim / OpenStreetMap",
        "query": {"text": args.query, "countrycodes": args.countrycodes},
        "places": places,
        "checked_at": checked_at(),
        "source": {"title": "OpenStreetMap Nominatim", "url": "https://nominatim.org/release-docs/latest/api/Search/"},
        "disclaimer": "仅用于低频地点候选检索；名称和坐标需与景区官网或地图平台交叉核对，入口开放状态以景区官方为准。",
    }


def route_options(mode: str, data: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    route = data.get("route") or {}
    raw_options = route.get("transits") if mode == "transit" else route.get("paths")
    options = []
    for item in (raw_options or [])[:limit]:
        option = {
            "distance_meters": item.get("distance"),
            "duration_seconds": item.get("duration"),
        }
        if mode == "transit":
            option.update(
                {
                    "cost": item.get("cost"),
                    "walking_distance_meters": item.get("walking_distance"),
                    "nightflag": item.get("nightflag"),
                    "segments": item.get("segments") or [],
                }
            )
        else:
            option.update(
                {
                    "strategy": item.get("strategy"),
                    "tolls": item.get("tolls"),
                    "steps": [
                        {
                            "instruction": step.get("instruction"),
                            "road": step.get("road"),
                            "distance_meters": step.get("distance"),
                            "duration_seconds": step.get("duration"),
                        }
                        for step in item.get("steps") or []
                    ],
                }
            )
        options.append(option)
    return options


def amap_route(args: argparse.Namespace) -> dict[str, Any]:
    if args.mode == "transit" and not args.city:
        raise SourceError("公交/地铁路线必须提供 --city（城市名或 citycode）")
    path = {
        "walking": "/v3/direction/walking",
        "driving": "/v3/direction/driving",
        "transit": "/v3/direction/transit/integrated",
    }[args.mode]
    data = amap_request(
        path,
        {"origin": args.origin, "destination": args.destination, "city": args.city, "extensions": "all"},
    )
    navigation_url = "https://uri.amap.com/navigation?" + urlencode(
        {
            "from": f"{args.origin},起点",
            "to": f"{args.destination},终点",
            "mode": {"walking": "walk", "driving": "car", "transit": "bus"}[args.mode],
            "policy": 0,
            "src": "travel-planning",
            "callnative": 0,
        }
    )
    return {
        "status": "platform_reported",
        "provider": "高德开放平台",
        "query": {
            "mode": args.mode,
            "origin": args.origin,
            "destination": args.destination,
            "city": args.city,
        },
        "options": route_options(args.mode, data, args.limit),
        "navigation_url": navigation_url,
        "checked_at": checked_at(),
        "source": {"title": "高德路径规划 API", "url": AMAP_DOCS},
        "disclaimer": "时间是平台查询时估算；出发时用实时导航复核。",
    }


def manual_link(label: str, url: str, provider: str, disclaimer: str) -> dict[str, Any]:
    return {
        "type": "source",
        "label": label,
        "provider": provider,
        "url": url,
        "checked_at": checked_at(),
        "disclaimer": disclaimer,
        "manual_required": True,
    }


def fallback(args: argparse.Namespace) -> dict[str, Any]:
    conditions = {
        "date": args.date,
        "origin": args.origin,
        "destination": args.destination,
        "city": args.city,
        "keywords": args.keywords,
        "travelers": args.travelers,
        "fields": args.fields,
    }
    summary = "、".join(str(value) for value in conditions.values() if value)
    links: list[dict[str, Any]] = []
    if args.kind == "train":
        label = f"在 12306 查询：{args.origin or '出发站'} → {args.destination or '到达站'}"
        if args.date:
            label += f"·{args.date}"
        links.append(manual_link(label, "https://www.12306.cn/index/", "12306", f"核对车次、时刻、席别价格和查询时余票；{args.recheck}"))
    elif args.kind == "ctrip":
        product = args.product or "旅行产品"
        links.append(manual_link(f"在携程查询{product}：{summary or '请填写查询条件'}", "https://www.ctrip.com/", "携程", f"库存与价格以平台当时显示为准；{args.recheck}"))
    elif args.kind == "map":
        query = args.keywords or args.destination or args.city or "目的地"
        if args.map_provider == "google":
            url = "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": query})
            links.append(manual_link(f"在 Google Maps 查询：{summary or query}", url, "Google Maps", f"复核入口、路线和实时耗时；{args.recheck}"))
        else:
            url = "https://uri.amap.com/search?" + urlencode(
                {"keyword": query, "city": args.city or "", "src": "travel-planning", "callnative": 0}
            )
            links.append(manual_link(f"在高德地图查询：{summary or query}", url, "高德地图", f"复核入口、路线和实时耗时；{args.recheck}"))
    elif args.kind == "weather":
        links.append(manual_link(f"在中国天气网查询：{summary or '目的地与日期'}", "https://www.weather.com.cn/", "中国天气网", f"复核预报、降雨和紫外线；{args.recheck}"))
        links.append(manual_link("查看中央气象台当前预警", "https://www.nmc.cn/publish/alarm.html", "中央气象台", f"确认是否存在有效预警；{args.recheck}"))
    elif args.kind == "attraction":
        if not args.url:
            raise SourceError("景区手动复核必须提供已定位的官方 --url，不能猜测首页或深链")
        links.append(manual_link(f"查看景区官方信息：{summary or args.keywords or '开放与票务'}", args.url, "景区官方", f"核对门票、开放/停止入场、预约、入口和临时公告；{args.recheck}"))
    elif args.kind == "xiaohongshu":
        query = args.keywords or args.destination or args.city or "目的地 当季 入口 避坑"
        links.append(manual_link(f"在小红书搜索：{query}", "https://www.xiaohongshu.com/explore", "小红书", f"需登录后输入该检索词；社区经验须与官方来源交叉核验；{args.recheck}"))
    return {
        "status": "to_recheck",
        "kind": args.kind,
        "query_conditions": {key: value for key, value in conditions.items() if value},
        "action_links": links,
        "checked_at": checked_at(),
    }


def iso_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise argparse.ArgumentTypeError("日期必须是 YYYY-MM-DD") from error
    return value


def iata_code(value: str) -> str:
    code = value.upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise argparse.ArgumentTypeError("IATA 代码必须是 3 个英文字母")
    return code


def flight_number(value: str) -> str:
    number = value.upper()
    if not re.fullmatch(r"[A-Z0-9]{2,3}[0-9]{1,4}", number):
        raise argparse.ArgumentTypeError("航班号格式无效，例如 MU2157")
    return number


def add_external_query_options(command: argparse.ArgumentParser, default_limit: int = 10) -> None:
    command.add_argument("--limit", type=int, default=default_limit, choices=range(1, 51), metavar="1-50")
    command.add_argument("--timeout", type=int, default=45, choices=range(5, 181), metavar="5-180")
    command.add_argument(
        "--consent-external-data",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    command.add_argument("--workspace", help="可选：将标准快照直接写入本次研究 workspace")
    command.add_argument("--task-id", help="与 --workspace 同时使用；必须是已分配的 route-data 或 stay-food 任务")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return number


def add_flyai_flight_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--origin", required=True, help="出发城市或机场")
    command.add_argument("--destination", required=True, help="到达城市或机场")
    command.add_argument("--date", required=True, type=iso_date)
    command.add_argument("--return-date", type=iso_date)
    command.add_argument("--journey-type", type=int, choices=[1, 2], default=1, help="1 直达，2 中转")
    command.add_argument("--seat-class")
    command.add_argument("--max-price", type=float)
    command.add_argument("--sort-type", type=int, choices=range(1, 9), default=2)
    add_external_query_options(command)


def add_flyai_train_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--origin", required=True, help="出发城市或车站")
    command.add_argument("--destination", required=True, help="到达城市或车站")
    command.add_argument("--date", required=True, type=iso_date)
    command.add_argument("--journey-type", type=int, choices=[1, 2], default=1, help="1 直达，2 中转")
    command.add_argument("--seat-class")
    command.add_argument("--train-number")
    command.add_argument("--max-price", type=float)
    command.add_argument("--sort-type", type=int, choices=range(1, 9), default=2)
    add_external_query_options(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("capabilities", help="检查当前可用的数据源")
    command.set_defaults(handler=capabilities)

    command = subparsers.add_parser(
        "preflight",
        help="仅探测本次选定来源；全部探测仅用于显式诊断",
    )
    command.add_argument("--source", action="append", choices=PREFLIGHT_SOURCES,
                         help="本次需要检查的可选来源；可重复传入，不探测未选择来源")
    command.add_argument("--all-sources", action="store_true", help="显式诊断全部来源，不用于默认规划")
    command.add_argument(
        "--require",
        action="append",
        choices=PREFLIGHT_SOURCES,
        help="本次行程必须可用的数据源；可重复传入，失败时命令返回非零",
    )
    command.add_argument("--city", default="北京", help="只读 smoke query 使用的城市")
    command.add_argument("--airport", default="PEK", type=iata_code, help="航空 smoke query 使用的机场 IATA 码")
    command.add_argument("--timeout", type=int, default=30, choices=range(5, 181), metavar="5-180")
    command.add_argument(
        "--skip-upstream",
        action="store_true",
        help="只检查本地运行时和 MCP 握手，不访问供应商上游；结果记为 degraded",
    )
    command.set_defaults(handler=preflight)

    command = subparsers.add_parser("weather", help="查询近期天气、降雨、紫外线和日出日落")
    command.add_argument("--location", help="城市或地点名")
    command.add_argument("--latitude", type=float)
    command.add_argument("--longitude", type=float)
    command.add_argument("--name", help="坐标对应的显示名称")
    command.add_argument("--days", type=int, default=7, choices=range(1, 17), metavar="1-16")
    command.set_defaults(handler=weather)

    command = subparsers.add_parser("xhs-health", help="检查本机小红书 HTTP MCP 协议和工具契约")
    command.set_defaults(handler=xhs_health)

    command = subparsers.add_parser("xhs-login-status", help="检查小红书本机登录态")
    command.set_defaults(handler=xhs_login_status)

    command = subparsers.add_parser("xhs-search", help="搜索小红书公开笔记并脱敏输出")
    command.add_argument("--keyword", required=True)
    command.add_argument("--sort-by", choices=["relevance", "latest", "most_liked", "most_commented", "most_collected"], default="relevance")
    command.add_argument("--note-type", choices=["all", "video", "image"], default="all")
    command.add_argument("--publish-time", choices=["all", "day", "week", "half_year"], default="all")
    command.add_argument("--search-scope", choices=["all", "viewed", "unviewed", "following"], default="all")
    command.add_argument("--location", choices=["all", "same_city", "nearby"], default="all")
    command.add_argument("--limit", type=int, default=10, choices=range(1, 21), metavar="1-20")
    command.set_defaults(handler=xhs_search)

    command = subparsers.add_parser("xhs-detail", help="读取已搜索到的小红书笔记详情")
    command.add_argument("--note-id", required=True)
    command.add_argument(
        "--all-comments",
        action="store_true",
        help="滚动加载更多一级评论；默认只返回上游默认的前10条一级评论",
    )
    command.add_argument(
        "--comment-limit",
        type=int,
        default=20,
        choices=range(1, 101),
        metavar="1-100",
        help="扩展评论模式下的一级评论上限",
    )
    command.add_argument(
        "--include-replies",
        action="store_true",
        help="扩展评论模式下展开二级回复",
    )
    command.add_argument(
        "--reply-limit",
        type=int,
        default=10,
        choices=range(1, 51),
        metavar="1-50",
        help="扩展评论模式下跳过回复数超过该阈值的评论",
    )
    command.add_argument(
        "--scroll-speed",
        choices=["slow", "normal", "fast"],
        default="normal",
        help="扩展评论模式下的滚动速度",
    )
    command.set_defaults(handler=xhs_detail)

    command = subparsers.add_parser("xhs-images", help="临时准备已读笔记的1至3张图片，供看图提取信息")
    command.add_argument("--note-id", required=True)
    command.add_argument("--indices", type=int, nargs="+", required=True, help="图片序号，从1开始；最多3张")
    command.set_defaults(handler=xhs_images)

    command = subparsers.add_parser("amap-place", help="查询高德 POI 或景区入口")
    command.add_argument("--keywords", required=True)
    command.add_argument("--city", required=True)
    command.add_argument("--limit", type=int, default=5, choices=range(1, 26), metavar="1-25")
    command.set_defaults(handler=amap_place)

    command = subparsers.add_parser("osm-place", help="通过 OpenStreetMap 查询境外地点或入口候选")
    command.add_argument("--query", required=True, help="尽量包含当地语言名称、城市和国家")
    command.add_argument("--countrycodes", help="可选 ISO 3166-1 alpha-2 国家码，如 jp、fr")
    command.add_argument("--language", default="zh-CN,en")
    command.add_argument("--limit", type=int, default=3, choices=range(1, 6), metavar="1-5")
    command.set_defaults(handler=osm_place)

    command = subparsers.add_parser("amap-route", help="查询高德步行、公交/地铁或驾车路线")
    command.add_argument("--origin", required=True, help="起点经纬度，lon,lat")
    command.add_argument("--destination", required=True, help="终点经纬度，lon,lat")
    command.add_argument("--mode", choices=["walking", "transit", "driving"], required=True)
    command.add_argument("--city", help="城市名或 citycode；公交/地铁必填")
    command.add_argument("--limit", type=int, default=3, choices=range(1, 6), metavar="1-5")
    command.set_defaults(handler=amap_route)

    command = subparsers.add_parser("flyai-flight", help="通过飞猪 FlyAI 查询航班候选")
    add_flyai_flight_options(command)
    command.set_defaults(handler=flyai_flight)

    command = subparsers.add_parser(
        "flyai-flight-coverage",
        help="按最早、最晚、时长、价格和推荐排序查询航班可行性边界",
    )
    add_flyai_flight_options(command)
    command.add_argument("--dimension", action="append", choices=["time", "price", "recommendation"],
                         help="只查询需要比较的维度；可重复指定，省略时兼容旧全排序")
    command.set_defaults(handler=flyai_flight_coverage)

    command = subparsers.add_parser("flyai-train", help="通过飞猪 FlyAI 查询火车候选")
    add_flyai_train_options(command)
    command.set_defaults(handler=flyai_train)

    command = subparsers.add_parser(
        "flyai-train-coverage",
        help="按最早、最晚、时长、价格和推荐排序查询火车可行性边界",
    )
    add_flyai_train_options(command)
    command.add_argument("--dimension", action="append", choices=["time", "price", "recommendation"],
                         help="只查询需要比较的维度；可重复指定，省略时兼容旧全排序")
    command.set_defaults(handler=flyai_train_coverage)

    command = subparsers.add_parser("flyai-hotel", help="通过飞猪 FlyAI 查询酒店候选")
    command.add_argument("--destination", required=True, help="国家、省、市或区")
    command.add_argument("--check-in", required=True, type=iso_date)
    command.add_argument("--check-out", required=True, type=iso_date)
    command.add_argument("--keywords")
    command.add_argument("--poi", help="附近景点或地标")
    command.add_argument("--hotel-type", choices=["酒店", "民宿", "客栈"])
    command.add_argument("--stars", help="逗号分隔星级，例如 4,5")
    command.add_argument("--bed-type", choices=["大床房", "双床房", "多床房"])
    command.add_argument("--adults", type=positive_int, help="本次入住成人数；用于记录需求，不代表供应商已按人数校验库存")
    command.add_argument("--rooms", type=positive_int, help="本次所需房间数；用于记录需求，不代表供应商已确认多间同房型库存")
    command.add_argument("--max-price", type=float)
    command.add_argument("--sort", choices=["distance_asc", "rate_desc", "price_asc", "price_desc", "no_rank"], default="no_rank")
    add_external_query_options(command)
    command.set_defaults(handler=flyai_hotel)

    command = subparsers.add_parser("variflight-flight", help="通过飞常准 Aviation MCP 查询航班运行候选")
    command.add_argument("--origin", required=True, type=iata_code)
    command.add_argument("--origin-kind", choices=["city", "airport"], default="city")
    command.add_argument("--destination", required=True, type=iata_code)
    command.add_argument("--destination-kind", choices=["city", "airport"], default="city")
    command.add_argument("--date", required=True, type=iso_date)
    add_external_query_options(command)
    command.set_defaults(handler=variflight_flight_search)

    command = subparsers.add_parser("variflight-flight-number", help="通过飞常准查询指定航班")
    command.add_argument("--flight-number", required=True, type=flight_number)
    command.add_argument("--date", required=True, type=iso_date)
    command.add_argument("--origin", type=iata_code, help="可选出发机场 IATA 代码")
    command.add_argument("--destination", type=iata_code, help="可选到达机场 IATA 代码")
    add_external_query_options(command)
    command.set_defaults(handler=variflight_flight_number)

    command = subparsers.add_parser("variflight-flight-price", help="通过飞常准查询城市间舱位价格")
    command.add_argument("--origin", required=True, type=iata_code, help="出发城市 IATA 代码")
    command.add_argument("--destination", required=True, type=iata_code, help="到达城市 IATA 代码")
    command.add_argument("--date", required=True, type=iso_date)
    add_external_query_options(command)
    command.set_defaults(handler=variflight_flight_price)

    command = subparsers.add_parser("variflight-flight-comfort", help="通过飞常准查询指定航班舒适度与准点等信息")
    command.add_argument("--flight-number", required=True, type=flight_number)
    command.add_argument("--date", required=True, type=iso_date)
    command.add_argument("--origin", type=iata_code, help="可选出发机场 IATA 代码")
    command.add_argument("--destination", type=iata_code, help="可选到达机场 IATA 代码")
    add_external_query_options(command)
    command.set_defaults(handler=variflight_flight_comfort)

    command = subparsers.add_parser("variflight-train", help="通过飞常准 Tripmatch MCP 查询火车票候选")
    command.add_argument("--origin", required=True, help="出发城市")
    command.add_argument("--destination", required=True, help="到达城市")
    command.add_argument("--date", required=True, type=iso_date)
    add_external_query_options(command)
    command.set_defaults(handler=variflight_train)

    command = subparsers.add_parser("variflight-train-stations", help="通过飞常准 Tripmatch MCP 检索车站")
    command.add_argument("--query", required=True)
    add_external_query_options(command)
    command.set_defaults(handler=variflight_train_stations)

    command = subparsers.add_parser("variflight-air-rail", help="通过飞常准 Tripmatch MCP 查询空铁联运候选")
    command.add_argument("--origin", required=True, type=iata_code, help="出发城市 IATA 代码")
    command.add_argument("--destination", required=True, type=iata_code, help="到达城市 IATA 代码")
    command.add_argument("--date", required=True, type=iso_date)
    add_external_query_options(command)
    command.set_defaults(handler=variflight_air_rail)

    command = subparsers.add_parser("fallback", help="生成用户可手动查询的 action_links")
    command.add_argument("--kind", choices=["attraction", "train", "ctrip", "map", "weather", "xiaohongshu"], required=True)
    command.add_argument("--date")
    command.add_argument("--origin")
    command.add_argument("--destination")
    command.add_argument("--city")
    command.add_argument("--keywords")
    command.add_argument("--travelers")
    command.add_argument("--fields")
    command.add_argument("--product", help="携程产品类型，例如酒店/航班/大巴/门票/餐厅")
    command.add_argument("--map-provider", choices=["amap", "google"], default="amap", help="地图手动入口；中国境内默认高德，境外可选 Google")
    command.add_argument("--url", help="已核验的景区官方 HTTPS 地址")
    command.add_argument("--recheck", default="建议出发前再次复核")
    command.set_defaults(handler=fallback)
    for name, query_parser in subparsers.choices.items():
        if name in {"capabilities", "fallback"}:
            continue
        existing = {action.dest for action in query_parser._actions}
        if "workspace" not in existing:
            query_parser.add_argument("--workspace", help="本次研究工作区；用于短时复用和执行计时")
        if "task_id" not in existing:
            query_parser.add_argument("--task-id", help="记录调用所属任务；非报价查询可省略")
        query_parser.add_argument("--phase", type=execution_log.identifier,
                                  help="调用所属研究阶段；省略时按命令类型分类")
        if name not in {"xhs-health", "xhs-images", "xhs-login-status"}:
            query_parser.add_argument("--refresh", action="store_true", help="忽略本次工作区的有效复用记录，重新请求")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        with execution_log.measured(getattr(args, "workspace", None), query_phase(args), args.command,
                                    task_id=getattr(args, "task_id", None)) as command_span:
            source_ids = {"weather": "open-meteo", "amap-place": "amap-api", "amap-route": "amap-api",
                          "xhs-search": "xiaohongshu", "xhs-detail": "xiaohongshu", "osm-place": "osm"}
            if args.command in source_ids:
                source_id = source_ids[args.command]
                payload = run_query(args, args.command, command_parameters(args), lambda: args.handler(args),
                                    source_id, prove_health=source_id in {"open-meteo", "xiaohongshu"})
            elif args.command == "xhs-login-status":
                # Explicit login diagnostics always check the live session.
                args.refresh = True
                payload = run_query(args, "check-login-status", {}, lambda: args.handler(args),
                                    "xiaohongshu", reuse_key=health_key("xiaohongshu"))
            else:
                payload = args.handler(args)
            if args.command == "preflight" and payload.get("status") == "unavailable":
                command_span["status"] = "failed"
        if args.command != "preflight" and getattr(args, "_execution", None):
            payload = {**payload, "execution": args._execution}
        output(payload)
        if args.command == "preflight" and payload.get("status") == "unavailable":
            return 2
        return 0
    except AdapterError as error:
        output(
            {
                "schema_version": "travel-source-error/v1",
                "status": "error",
                "failure_kind": error.failure_kind,
                "message": str(error),
                "checked_at": checked_at(),
            }
        )
        return 2
    except (SourceError, WorkspaceError, execution_log.ExecutionError) as error:
        output({"status": "error", "message": str(error), "checked_at": checked_at()})
        return 2


if __name__ == "__main__":
    sys.exit(main())
