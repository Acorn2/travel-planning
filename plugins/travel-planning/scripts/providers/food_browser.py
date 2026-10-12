"""Bounded reads of Douyin and Dianping in dedicated local browser profiles.

Selectors informed by cn-scraper-mcp and mcp-dianping; see food-discovery's
references/integrations.md. No upstream signature API or account writes.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse


PLATFORMS = {"douyin": "https://www.douyin.com/", "dianping": "https://www.dianping.com/"}
ROOT = Path.home() / ".local" / "share" / "travel-planning" / "food-discovery"
PLAYWRIGHT_VERSION = "1.63.0"


class FoodError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def text(value: object, maximum: int = 240) -> str:
    return " ".join(str(value or "").split())[:maximum]


def public_url(value: object, platform: str, *, kind: str | None = None) -> str | None:
    """Only return observed public links, stripped of query tokens and fragments."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(urljoin(PLATFORMS[platform], value))
        valid = parsed.scheme == "https" and parsed.hostname == urlparse(PLATFORMS[platform]).hostname and not parsed.port and not parsed.username
    except ValueError:
        return None
    if not valid:
        return None
    pattern = {"shop": r"/shop/[A-Za-z0-9]+/?", "video": r"/video/\d+/?", "city": r"/[a-z][a-z0-9-]*/?"}.get(kind)
    if pattern and not re.fullmatch(pattern, parsed.path):
        return None
    return f"https://{parsed.hostname}{parsed.path}"


def normalize_items(rows: list[dict], platform: str, limit: int) -> list[dict]:
    result, seen = [], set()
    kind = "video" if platform == "douyin" else "shop"
    for row in rows:
        url = public_url(row.get("url"), platform, kind=kind)
        title = text(row.get("title") or row.get("name"))
        if not url or not title or url in seen:
            continue
        seen.add(url)
        result.append({"id": url.rstrip("/").rsplit("/", 1)[-1], "url": url,
                       "title": title, "author": text(row.get("author"), 80),
                       "published_at_text": text(row.get("published_at_text"), 40),
                       "summary": text(row.get("summary"), 320)})
        if len(result) >= limit:
            break
    return result


@contextmanager
def profile_lock(platform: str):
    """Do not permit login and query processes to use one profile concurrently."""
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(ROOT / f"{platform}.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise FoodError("profile_busy", "专用浏览器正在登录或查询，请结束当前操作后再试") from None
    try:
        yield
    finally:
        handle.close()


@contextmanager
def browser(platform: str, *, login: bool = False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise FoodError("runtime_unavailable", "请先运行 food_sources.py install 安装可选本地运行时") from None
    with profile_lock(platform), sync_playwright() as driver:
        profile = ROOT / "profiles" / platform
        if not login and not profile.is_dir():
            raise FoodError("auth_required", f"请运行 food_sources.py login {platform} 并由本人登录")
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            context = driver.chromium.launch_persistent_context(
                str(profile), channel="chrome", headless=not login,
                viewport={"width": 1280, "height": 900},
            )
        except Exception:
            raise FoodError("browser_unavailable", "无法打开专用 Chrome；请确认已安装 Chrome 且登录窗口已关闭") from None
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(12000)
            page.set_default_navigation_timeout(20000)
            yield page
        finally:
            context.close()


def check_page(page, platform: str) -> None:
    host = urlparse(page.url).hostname or ""
    if host.startswith("account.") or "/login" in page.url or "/pclogin" in page.url:
        raise FoodError("auth_required", "平台要求登录，请在专用登录窗口完成后再查询")
    if host != urlparse(PLATFORMS[platform]).hostname:
        raise FoodError("unexpected_redirect", "页面跳到了查询范围外，已停止")
    state = page.evaluate("""() => ({
      challenge: !!document.querySelector('iframe[src*="captcha"], iframe[src*="verify"], #captcha, .captcha_verify_container, .verify-container'),
      body: (document.body?.innerText || '').slice(0, 800)
    })""")
    if state.get("challenge") or re.search(r"访问异常|验证后继续|安全验证|完成验证|拖动滑块|访问过于频繁", state.get("body", "")):
        raise FoodError("risk_control", "平台要求人工验证，已停止该来源；请由本人处理")
    if re.search(r"扫码登录后|登录后查看|请登录后|请先登录", state.get("body", "")):
        raise FoodError("auth_required", "平台要求登录，已停止该来源")


def visit(page, url: str, platform: str):
    response = page.goto(url, wait_until="domcontentloaded")
    if response and response.status >= 400:
        raise FoodError("upstream_error", f"平台返回 HTTP {response.status}")
    check_page(page, platform)


DOUYIN_ROWS = """() => Array.from(document.querySelectorAll('#search-result-container a[href*="/video/"]')).slice(0, 30).map(a => {
  const card = a.closest('[data-e2e*="search"]') || a.closest('li') || a.parentElement;
  const parts = (card?.innerText || a.innerText || '').split('\\n').map(s=>s.trim()).filter(Boolean);
  const author = card?.querySelector('[data-e2e="search-card-user"], [class*="author"]')?.innerText || parts.find(s=>s.startsWith('@')) || '';
  return {url:a.href, title:a.getAttribute('title') || a.innerText || parts[0], author,
    summary:parts.join(' · '), published_at_text:parts.find(s=>/\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}|天前|小时前/.test(s)) || ''};
})"""

DIANPING_ROWS = """() => Array.from(document.querySelectorAll('.shop-all-list ul li')).slice(0, 30).map(card => {
  const a = card.querySelector('.tit a[href*="/shop/"]');
  return {url:a?.href || '', name:card.querySelector('h4')?.innerText || '',
    summary:[card.querySelector('.tag-addr')?.innerText,card.querySelector('.mean-price')?.innerText].filter(Boolean).join(' · ')};
})"""


def wait_for_results(page, selector: str, platform: str) -> None:
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=10000)
    except Exception:
        check_page(page, platform)
        body = page.locator("body").inner_text(timeout=2000)
        if re.search(r"暂无相关|没有找到|没有搜索到|无搜索结果", body):
            raise FoodError("no_results", "平台明确返回无搜索结果") from None
        raise FoodError("layout_changed", "未取得预期的页面字段；可能是页面变化或访问限制") from None
    check_page(page, platform)


def douyin_search(keyword: str, limit: int) -> dict:
    with browser("douyin") as page:
        visit(page, PLATFORMS["douyin"], "douyin")
        search = page.locator('input[data-e2e="searchbar-input"], input[placeholder*="搜索"]').first
        search.fill(keyword)
        search.press("Enter")
        wait_for_results(page, '#search-result-container a[href*="/video/"]', "douyin")
        return {"items": normalize_items(page.evaluate(DOUYIN_ROWS), "douyin", limit),
                "query": {"keyword": keyword}, "missing_fields": ["视频口播内容", "精确发布时间需详情核对"]}


def douyin_detail(url: str) -> dict:
    with browser("douyin") as page:
        visit(page, url, "douyin")
        wait_for_results(page, 'h1, [data-e2e="video-desc"]', "douyin")
        fields = page.evaluate("""() => ({
          title: document.querySelector('h1, [data-e2e="video-desc"]')?.innerText || '',
          author: document.querySelector('[data-e2e="user-info"]')?.innerText || '',
          published_at_text: document.querySelector('[data-e2e="video-create-time"]')?.innerText || ''
        })""")
        return {"item": {"url": url, "title": text(fields.get("title"), 600),
                         "author": text(fields.get("author"), 80),
                         "published_at_text": text(fields.get("published_at_text"), 60)},
                "missing_fields": ["视频口播内容", "评论全文"]}


def dianping_search(city: str, keyword: str, limit: int) -> dict:
    with browser("dianping") as page:
        visit(page, "https://www.dianping.com/citylist", "dianping")
        try:
            page.get_by_role("link", name=city, exact=True).first.wait_for(state="visible", timeout=10000)
        except Exception:
            check_page(page, "dianping")
            raise FoodError("city_not_found", "平台城市列表未显示该城市；不会默认查询北京") from None
        matches = page.evaluate("""city => Array.from(document.querySelectorAll('a[href]')).filter(a => a.innerText.trim() === city).map(a=>a.href)""", city)
        city_urls = [u for u in (public_url(u, "dianping", kind="city") for u in matches) if u]
        if not city_urls:
            raise FoodError("city_not_found", "未能从平台城市列表取得该城市；不会默认查询北京")
        visit(page, city_urls[0], "dianping")
        search = page.locator('#J-search-input, #top-search-input, input[placeholder*="商户"], input[placeholder*="搜索"]').first
        search.fill(keyword)
        search.press("Enter")
        wait_for_results(page, '.shop-all-list ul li .tit a[href*="/shop/"]', "dianping")
        return {"items": normalize_items(page.evaluate(DIANPING_ROWS), "dianping", limit),
                "query": {"city": city, "city_url": city_urls[0], "keyword": keyword}}


def dianping_detail(url: str) -> dict:
    with browser("dianping") as page:
        visit(page, url, "dianping")
        wait_for_results(page, '.shopName', "dianping")
        fields = page.evaluate("""() => {
          const t = s => document.querySelector(s)?.innerText?.trim() || null;
          return {name:t('.shopName'), address:t('.addressText, .desc-addr-txt'),
            rating:t('.star-score'), review_count_text:t('.reviews'), per_person:t('.price'),
            opening_hours:t('.biz-time, .biz-txt'),
            recommended_dishes:Array.from(document.querySelectorAll('.recommend-name, .recommend .item-name')).slice(0,8).map(e=>e.innerText)};
        }""")
        item = {k: text(fields.get(k)) or None for k in ("name", "address", "rating", "review_count_text", "per_person", "opening_hours")}
        item["url"] = url
        item["recommended_dishes"] = [text(v, 60) for v in fields.get("recommended_dishes") or []][:8]
        return {"item": item, "missing_fields": [k for k, v in item.items() if not v] + ["完整评价", "宠物政策需联系门店确认"],
                "reviews_available": False}


def runtime_status(platform: str) -> dict:
    try:
        import playwright.sync_api  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    return {"platform": platform, "runtime_installed": installed,
            "profile_present": (ROOT / "profiles" / platform).is_dir(),
            "status": "not_checked" if installed else "unavailable",
            "live_verified": False, "experimental": True}


def result(platform: str, payload: dict) -> dict:
    if "items" in payload and not payload["items"]:
        raise FoodError("layout_changed", "页面有结果容器但未提取到有效条目，不能当作没有店或视频")
    missing = payload.get("missing_fields") or []
    return {"platform": platform, "status": "partial" if missing else "platform_reported",
            "experimental": True, "checked_at": datetime.now(timezone.utc).isoformat(),
            **payload}
