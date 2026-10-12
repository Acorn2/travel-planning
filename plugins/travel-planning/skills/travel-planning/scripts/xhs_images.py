"""Prepare selected note images for local vision, without exposing CDN URLs."""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


MAX_IMAGES = 3
MAX_BYTES = 8 * 1024 * 1024
CACHE_SECONDS = 1200


def cache_path(root: Path, note_id: str) -> Path:
    return root / "image-cache" / (hashlib.sha256(note_id.encode()).hexdigest() + ".json")


def cache_images(root: Path, note_id: str, images: list) -> int:
    """Signed links live only in a private, short-lived runtime cache."""
    rows = images if isinstance(images, list) else []
    path = cache_path(root, note_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"expires_at": time.time() + CACHE_SECONDS, "images": rows}, handle)
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    return len(rows)


def image_url(row: dict) -> str | None:
    for value in (row.get("urlDefault"), row.get("urlPre")):
        if not isinstance(value, str):
            continue
        try:
            parsed = urlparse(value)
            host = parsed.hostname or ""
            if (parsed.scheme in {"https", "http"} and host.endswith(".xhscdn.com")
                    and not parsed.username and not parsed.password and not parsed.port):
                return urlunparse(parsed._replace(scheme="https", fragment=""))
        except ValueError:
            continue
    return None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_image(url: str) -> tuple[bytes, str]:
    context = ssl.create_default_context()
    # macOS system roots are useful when framework Python has no CA bundle.
    if not context.get_ca_certs() and Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
    request = Request(url, headers={"Accept": "image/*", "Referer": "https://www.xiaohongshu.com/"})
    with opener.open(request, timeout=15) as response:
        if not image_url({"urlDefault": response.geturl()}):
            raise ValueError("unexpected_host")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("image_too_large")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = "jpg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        extension = "webp"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        extension = "gif"
    else:
        raise ValueError("not_supported_image")
    return data, extension


def prepare_images(root: Path, note_id: str, indices: list[int]) -> dict:
    if (not indices or len(indices) > MAX_IMAGES or len(set(indices)) != len(indices)
            or any(type(index) is not int or index < 1 for index in indices)):
        raise ValueError("每次选择1至3个不同的正整数图片序号")
    path = cache_path(root, note_id)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["expires_at"] < time.time():
            raise ValueError()
        rows = cached["images"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("图片链接缓存缺失或已过期，请重新读取该笔记详情") from None
    if any(index > len(rows) for index in indices):
        raise ValueError("所选图片序号超出笔记图片数量")
    files = []
    for index in indices:
        row = rows[index - 1]
        url = image_url(row) if isinstance(row, dict) else None
        item = {"image_index": index, "status": "unavailable"}
        if url:
            try:
                data, extension = fetch_image(url)
                directory = Path(tempfile.mkdtemp(prefix="travel-xhs-image-"))
                target = directory / f"image-{index}.{extension}"
                target.write_bytes(data)
                target.chmod(0o600)
                item.update(status="ready_for_reading", local_path=str(target))
            except Exception:
                # Errors can contain signed URLs; never return the raw exception.
                item["reason"] = "图片读取失败或格式不支持，未识别图片文字"
        else:
            item["reason"] = "没有可读取的小红书 CDN 图片链接"
        files.append(item)
    count = sum(item["status"] == "ready_for_reading" for item in files)
    return {"note_id": note_id, "status": "ready_for_reading" if count == len(files) else "partial" if count else "unavailable",
            "images": files, "text_extracted": False,
            "next_step": "用 view_image 查看 local_path；只归档相关摘要、原帖链接和图片序号，不归档图片或临时路径"}
