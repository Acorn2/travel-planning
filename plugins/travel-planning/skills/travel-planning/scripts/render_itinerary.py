#!/usr/bin/env python3
"""将行程 JSON 渲染为可独立打开的响应式 HTML 页面。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import html
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse


EVIDENCE_SPEC = importlib.util.spec_from_file_location("research_evidence", Path(__file__).with_name("research_evidence.py"))
assert EVIDENCE_SPEC and EVIDENCE_SPEC.loader
research_evidence = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(research_evidence)

EXECUTION_SPEC = importlib.util.spec_from_file_location("execution_log", Path(__file__).with_name("execution_log.py"))
assert EXECUTION_SPEC and EXECUTION_SPEC.loader
execution_log = importlib.util.module_from_spec(EXECUTION_SPEC)
EXECUTION_SPEC.loader.exec_module(execution_log)

COMMENT_SPEC = importlib.util.spec_from_file_location("xhs_comment_contract", Path(__file__).with_name("xhs_comment_contract.py"))
assert COMMENT_SPEC and COMMENT_SPEC.loader
xhs_comment_contract = importlib.util.module_from_spec(COMMENT_SPEC)
COMMENT_SPEC.loader.exec_module(xhs_comment_contract)


TYPES = {
    "attraction": ("景点", "◇"), "transport": ("交通", "↗"),
    "meal": ("餐饮", "◌"), "lodging": ("住宿", "⌂"),
    "rest": ("休息", "—"), "note": ("提示", "i"), "food_walk": ("逛吃", "◌"),
}
WEATHER_ICONS = {
    "clear_day": "☀️", "clear_night": "🌙", "partly_cloudy": "🌤️", "cloudy": "☁️",
    "fog": "🌫️", "drizzle": "🌦️", "rain": "🌧️", "heavy_rain": "🌧️",
    "snow": "🌨️", "thunderstorm": "⛈️", "wind": "💨", "dust": "🌪️",
    "warning": "⚠️", "unknown": "🌤️",
}
LEGACY_CHECKPOINT_PLACEHOLDERS = {
    "先完成核心点，再根据排队、天气和体力决定是否停留。",
}
DUPLICATE_MEAL_SUMMARY_FIELDS = {
    "location", "signature_dishes", "per_person", "opening_hours",
    "queue_note", "why_here", "fallback",
}
PROVIDER_DISPLAY_NAMES = {
    "amap": "高德",
    "gaode": "高德",
    "高德地图": "高德",
    "xiaohongshu": "小红书",
    "meituan": "美团",
}
FRONTEND_ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "frontend"
VISUAL_MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_VISUAL_BYTES = 2_000_000


def load_frontend_asset(name: str, closing_tag: str) -> str:
    """Load a compiled Vue asset and keep it safe for inline HTML delivery."""
    path = FRONTEND_ASSET_ROOT / name
    if not path.is_file():
        raise RuntimeError(
            f"缺少已编译的页面资源：{path}。请在插件 web 目录运行 npm ci && npm run build。"
        )
    return path.read_text(encoding="utf-8").replace(
        f"</{closing_tag}", f"<\\/{closing_tag}"
    )

def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def compact_date_range(value: Any) -> str:
    """Keep the trip dates readable in the narrow magazine cover facts grid."""
    raw = str(value or "")
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})\s*[—–~至]\s*(\d{4})-(\d{2})-(\d{2})",
        raw,
    )
    if not match:
        return raw
    start_year, start_month, start_day, end_year, end_month, end_day = match.groups()
    end = f"{end_month}.{end_day}" if start_year == end_year else f"{end_year}.{end_month}.{end_day}"
    return f"{start_year}.{start_month}.{start_day}—{end}"


def has_reusable_image_credit(image: dict[str, Any]) -> bool:
    """Only promote attributed, licensed event images into editorial covers."""
    return bool(
        str(image.get("url") or "").startswith("https://")
        and str(image.get("source_url") or "").startswith("https://")
        and image.get("license")
        and (image.get("source_label") or image.get("author"))
    )


def load_visual_manifest(path: Path) -> dict[str, Any]:
    """Embed optional, reviewed artwork so the delivered itinerary stays one file."""
    root = path.resolve().parent
    manifest = json.loads(path.read_text(encoding="utf-8"))

    def load_asset(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict) or not item.get("path"):
            return {}
        if not item.get("alt") or not item.get("credit"):
            raise ValueError("视觉素材必须提供替代文本和页面可见的来源说明")
        relative = Path(str(item["path"]))
        candidate = (root / relative).resolve()
        if relative.is_absolute() or not candidate.is_relative_to(root):
            raise ValueError(f"视觉素材必须位于清单目录内：{relative}")
        mime = VISUAL_MIME_TYPES.get(candidate.suffix.lower())
        if not mime or not candidate.is_file():
            raise ValueError(f"视觉素材格式或路径无效：{relative}")
        raw = candidate.read_bytes()
        if len(raw) > MAX_VISUAL_BYTES:
            raise ValueError(f"视觉素材超过 2 MB：{relative}")
        ratio = item.get("aspect_ratio", 1.5)
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0.1 <= ratio <= 10:
            raise ValueError("aspect_ratio 必须是 0.1 到 10 之间的数字")
        crop = item.get("crop")
        if crop is not None:
            if not isinstance(crop, dict):
                raise ValueError("crop 必须包含 columns、rows 和 index")
            columns, rows, index = (crop.get(key) for key in ("columns", "rows", "index"))
            if not all(type(value) is int for value in (columns, rows, index)) or not (
                1 <= columns <= 8 and 1 <= rows <= 8 and 0 <= index < columns * rows
            ):
                raise ValueError("crop 网格或索引无效")
        return {
            "src": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
            "alt": str(item.get("alt") or "旅行氛围插画"),
            "credit": str(item.get("credit") or ""),
            "aspect_ratio": ratio,
            "crop": crop,
        }

    return {
        "cover": load_asset(manifest.get("cover")),
        "food": load_asset(manifest.get("food")),
        "experiences": {
            str(title): load_asset(item)
            for title, item in (manifest.get("experiences") or {}).items()
        },
        "days": {str(date): load_asset(item) for date, item in (manifest.get("days") or {}).items()},
        "food_items": {str(name): load_asset(item) for name, item in (manifest.get("food_items") or {}).items()},
    }


def prepare_artwork(visuals: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Keep local bitmaps once, outside the Vue mount, with reusable SVG crops."""
    symbols: dict[str, str] = {}

    def prepare(asset: dict[str, Any]) -> dict[str, Any]:
        src = str(asset.get("src") or "")
        if not re.fullmatch(r"data:image/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+", src):
            return dict(asset)
        ratio = float(asset.get("aspect_ratio", 1.5))
        width, height = ratio * 100, 100
        symbol_id = "wc-art-" + hashlib.sha256(f"{ratio}:{src}".encode()).hexdigest()[:20]
        symbols[symbol_id] = f'<symbol id="{symbol_id}" viewBox="0 0 {width:g} {height}"><image href="{src}" width="{width:g}" height="{height}" preserveAspectRatio="xMidYMid slice"/></symbol>'
        crop = asset.get("crop") or {}
        columns, rows, index = (crop.get("columns", 1), crop.get("rows", 1), crop.get("index", 0))
        box = f"{(index % columns) * width / columns:g} {(index // columns) * height / rows:g} {width / columns:g} {height / rows:g}"
        return {**asset, "symbol_id": symbol_id, "view_box": box, "width": width, "height": height}

    prepared = {key: prepare(visuals.get(key) or {}) for key in ("cover", "food")}
    for key in ("experiences", "days", "food_items"):
        prepared[key] = {name: prepare(asset) for name, asset in (visuals.get(key) or {}).items()}
    library = '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" aria-hidden="true" style="position:absolute;overflow:hidden"><defs>' + "".join(symbols.values()) + "</defs></svg>" if symbols else ""
    return prepared, library


def first_event_visual(days: list[dict[str, Any]]) -> dict[str, str]:
    events = [event for day in days for event in day.get("events") or []]
    for event_type in ("attraction", None):
        for event in events:
            if event_type and event.get("type") != event_type:
                continue
            for image in event.get("images") or []:
                if has_reusable_image_credit(image):
                    url = str(image["url"])
                    credit = " · ".join(str(image.get(key)) for key in ("source_label", "author", "license") if image.get(key))
                    return {
                        "src": url,
                        "alt": str(image.get("alt") or event.get("title") or "旅行实景"),
                        "credit": credit,
                        "source_url": str(image.get("source_url") or ""),
                    }
    return {}


def render_visual(asset: dict[str, Any], class_name: str, *, eager: bool = False, source_link: bool = True) -> str:
    src = str(asset.get("src") or "")
    if not (src.startswith("https://") or re.match(r"^data:image/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$", src)):
        return ""
    loading = "eager" if eager else "lazy"
    credit = f'<figcaption>{esc(asset["credit"])}</figcaption>' if asset.get("credit") else ""
    picture = f'<img src="{esc(src)}" alt="{esc(asset.get("alt"))}" loading="{loading}" decoding="async">'
    if re.fullmatch(r"wc-art-[a-f0-9]{20}", str(asset.get("symbol_id") or "")):
        picture = f'<svg class="wc-painting" viewBox="{esc(asset["view_box"])}" preserveAspectRatio="xMidYMid slice" role="img" aria-label="{esc(asset.get("alt"))}"><use href="#{asset["symbol_id"]}" width="{asset["width"]:g}" height="{asset["height"]:g}"/></svg>'
    source_url = str(asset.get("source_url") or "")
    if source_link and source_url.startswith("https://"):
        picture = f'<a href="{esc(source_url)}" target="_blank" rel="noopener noreferrer">{picture}</a>'
    return f'<figure class="{class_name}">{picture}{credit}</figure>'


def provider_display_name(value: Any) -> str:
    """Use traveler-facing provider names without leaking internal IDs."""
    text = str(value or "").strip()
    return PROVIDER_DISPLAY_NAMES.get(text.casefold(), text)


def transport_mode_display_name(value: Any) -> str:
    text = str(value or "").strip()
    return {"flight": "航班", "train": "铁路", "rail": "铁路"}.get(text.casefold(), text)


def traveler_status(value: Any) -> str:
    """Research verification is not a booking confirmation."""
    text = str(value or "").strip()
    labels = {
        "to_recheck": "需确认", "verified": "信息已核验",
        "official_confirmed": "官方信息已核验", "platform_reported": "平台查询结果",
        "estimated": "估算", "not_applicable": "无需处理", "quote_only": "报价候选，未预订",
        "pending": "待办理", "book_now": "请提前办理", "confirmed": "已确认",
        "booked": "已预订", "reserved": "已预约", "completed": "已完成",
        "cancelled": "已取消", "unavailable": "信息未取得", "free": "免费",
    }
    return labels.get(text, "状态需确认" if re.fullmatch(r"[a-z_]+", text) else text)


def approximate_minutes(value: Any) -> str:
    try:
        number = float(value)
        if math.isfinite(number) and number >= 0:
            return f"约{math.ceil(number)}分钟"
    except (TypeError, ValueError):
        pass
    return "时间需复核"


def is_routine_event(event: dict[str, Any]) -> bool:
    """Only compress routine actions; a park visit classified as rest stays prominent."""
    if event.get("type") not in {"rest", "note"}:
        return False
    title = str(event.get("title") or "")
    return bool(re.search(
        r"^早餐(?:与|$)|^酒店休息$|^休息$|出站取行李|实名进站与候车|出站与找地铁入口|值机.*安检|^取行李并前往机场$",
        title,
    )) and not any(event.get(key) for key in ("admission", "execution", "images", "booking_task_ids"))


def render_list(items: list[Any], class_name: str = "detail-list") -> str:
    if not items:
        return ""
    return f'<ul class="{class_name}">' + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>"


def render_actions(actions: list[dict[str, Any]], extra_html: str = "") -> str:
    """将已核验的 HTTPS 外部入口渲染为跳转按钮。"""
    links = []
    for action in actions or []:
        url = str(action.get("url") or "")
        if not url.startswith("https://"):
            continue
        hint = " · ".join(x for x in [provider_display_name(action.get("provider")), action.get("checked_at"), action.get("disclaimer")] if x)
        links.append(f'<a class="action-link" href="{esc(url)}" target="_blank" rel="noopener noreferrer" title="{esc(hint)}">{esc(action.get("label") or "打开链接")} ↗</a>')
    content = "".join(links) + extra_html
    return f'<div class="actions">{content}</div>' if content else ""


def render_wechat_action(wechat: dict[str, Any]) -> str:
    """Render a verified WeChat reservation guide or a copy-to-search fallback."""
    if not wechat:
        return ""
    account_name = str(wechat.get("account_name") or "").strip()
    if not account_name:
        return ""
    menu_path = str(wechat.get("menu_path") or "").strip()
    hint = " · ".join(
        item for item in (
            f"公众号：{account_name}",
            f"微信内路径：{menu_path}" if menu_path else "",
            f"核验于 {wechat.get('checked_at')}" if wechat.get("checked_at") else "",
        ) if item
    )
    guide_url = str(wechat.get("guide_url") or "")
    if guide_url.startswith("https://"):
        return (
            f'<a class="action-link action-link-wechat" href="{esc(guide_url)}" target="_blank" '
            f'rel="noopener noreferrer" title="{esc(hint)}">公众号预约 ↗</a>'
        )
    return (
        f'<button class="action-link action-link-wechat" type="button" '
        f'data-wechat-account="{esc(account_name)}" data-wechat-menu="{esc(menu_path)}" '
        f'title="{esc(hint)}">复制公众号名称</button>'
        '<span class="wechat-account-status" role="status" aria-live="polite"></span>'
    )


def merge_actions(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge event and research links without duplicating the same URL/label."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for action in group or []:
            key = str(action.get("url") or "")
            if key not in seen:
                seen.add(key)
                merged.append(action)
    return merged


def render_route_handoff(route: dict[str, Any]) -> str:
    """Hand an exact destination to the traveler's preferred navigation device."""
    map_action = next(
        (
            action for action in route.get("action_links") or []
            if action.get("type") == "map"
            and urlparse(str(action.get("url") or "")).scheme == "https"
        ),
        None,
    )
    destination = route.get("destination") or {}
    destination_name = str(destination.get("name") or route.get("to") or "").strip()
    if not destination_name:
        return ""
    map_route = route.get("map_route") or {}
    map_mode = map_route.get("mode")
    handoff_label = "下一站 · 导航目的地" if map_mode == "car" else "下一站 · 到达地点"
    guidance = (
        "在手机或车机中核对目的地后，按实时导航和现场指引行驶。"
        if map_mode == "car" else
        "核对乘车站点、出入口和现场指引后继续行程。"
    )
    poi_id = destination.get("poi_id") or map_route.get("destination_id")
    provider = str(destination.get("poi_provider") or map_route.get("provider") or "").lower()
    if provider and provider not in {"amap", "gaode", "高德", "高德地图"}:
        poi_id = None
    if route.get("status") == "estimated":
        guidance = "路线耗时为估算，已按区间上界加缓冲安排；" + guidance
    map_url = (
        "https://uri.amap.com/poidetail?" + urlencode({"poiid": str(poi_id)})
        if poi_id else str((map_action or {}).get("url") or "")
    )
    link_label = "在高德打开地点" if poi_id else "在高德查看路线" if urlparse(map_url).hostname == "uri.amap.com" else "查看地点与路线"
    address = destination.get("physical_address") or destination.get("address") or ""
    link = (
        f'<a class="route-destination-link" href="{esc(map_url)}" target="_blank" '
        f'rel="noopener noreferrer">{link_label} ↗</a>'
        if map_url.startswith("https://") else ""
    )
    return f'''<section class="route-handoff" aria-label="导航目的地">
      <span class="route-handoff-label">{handoff_label}</span>
      <strong class="route-destination-name">{esc(destination_name)}</strong>
      {f'<span class="route-destination-address">{esc(address)}</span>' if address else ''}
      <div class="route-handoff-actions"><button type="button" class="route-copy" data-copy-destination="{esc(destination_name)}">复制目的地</button>{link}</div>
      <span class="route-copy-fallback">长按上方地点名称可复制到导航设备。</span>
      <span class="route-copy-status" role="status" aria-live="polite"></span>
      <small>{guidance}</small>
    </section>'''


def render_cost_items(
    items: list[dict[str, Any]],
    summary: str = "",
    *,
    embedded: bool = False,
) -> str:
    """Render the executable price breakdown inside the related event card."""
    if not items and not summary:
        return ""
    rows = []
    for item in items or []:
        role = item.get("pricing_role") or ("baseline" if item.get("required") else "optional")
        role_label = {"baseline": "计入基线", "optional": "可选", "alternative": "互斥方案"}.get(role, role)
        kind_label = {
            "base_ticket": "基础门票", "required_transport": "必需接驳", "optional_experience": "可选体验",
            "package": "套票", "transport": "交通", "lodging": "住宿", "meal": "餐饮", "other": "其他",
        }.get(item.get("kind"), item.get("kind"))
        status_label = {
            "official_confirmed": "官方确认", "platform_reported": "平台查询价",
            "estimated": "预算估算", "to_recheck": "待复核", "free": "免费",
        }.get(item.get("status"), item.get("status"))
        rows.append(f'''<li class="cost-row"><div><strong>{esc(item.get("name") or "费用")}</strong>
          <span>{esc(kind_label)} · {esc(role_label)} · {esc(status_label)}</span></div>
          <div class="cost-value"><strong>{esc(item.get("unit_price") or "未取得")}</strong><span>{esc(item.get("quantity"))} · {esc(item.get("subtotal"))}</span></div></li>''')
    class_name = "admission-cost" if embedded else "cost-breakdown"
    heading = "票价与费用" if embedded else "费用明细"
    return f'''<section class="{class_name}"><h4>{heading}</h4><ul>{"".join(rows)}</ul>
      {f'<p class="cost-summary">{esc(summary)}</p>' if summary else ''}</section>'''


def render_facts(items: list[tuple[str, Any]]) -> str:
    rows = "".join(f'<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>' for label, value in items if value)
    return f'<dl class="event-facts">{rows}</dl>' if rows else ""


def actionable_reservation(value: Any) -> str | None:
    """Return only reservation guidance that can change an advance plan."""
    text = str(value or "").strip()
    if not text or any(marker in text for marker in ("未取得", "未返回", "未知", "待复核")):
        return None
    return text


def amap_restaurant_actions(
    restaurant: dict[str, Any],
    route_evaluation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build one canonical AMap detail link from a verified AMap POI."""
    location = restaurant.get("location") or {}
    provider = str(location.get("poi_provider") or "").casefold()
    poi_id = str(location.get("poi_id") or "").strip()
    coordinates = str(location.get("coordinates") or "").strip()
    if provider not in {"amap", "gaode", "高德", "高德地图"} or not poi_id:
        return []
    common = {
        "provider": "高德地图",
        "checked_at": location.get("poi_verified_at"),
        "disclaimer": "评分、营业与门店状态以高德实时页为准",
    }
    return [{
        **common,
        "type": "restaurant",
        "label": "在高德查看门店",
        "url": "https://uri.amap.com/poidetail?" + urlencode({
            "poiid": poi_id,
            "src": "travel-planning",
            "callnative": "1",
        }),
    }]


def is_amap_restaurant_action(action: dict[str, Any]) -> bool:
    """Identify extra AMap store/navigation actions superseded by the canonical card link."""
    url = str(action.get("url") or "")
    parsed = urlparse(url)
    host = parsed.netloc.casefold().split(":", 1)[0]
    return host == "amap.com" or host.endswith(".amap.com")


def amap_nearby_restaurant_action(meal: dict[str, Any]) -> dict[str, Any] | None:
    """Open a Gaode restaurant search around the meal's verified route anchor."""
    anchor = meal.get("previous_anchor") or {}
    coordinates = str(anchor.get("coordinates") or "").strip()
    if not coordinates:
        return None
    explicit = next(
        (
            action for action in meal.get("action_links") or []
            if action.get("type") == "restaurant_search"
            and str(action.get("url") or "").startswith("https://ditu.amap.com/search?")
        ),
        None,
    )
    if explicit:
        return {
            **explicit,
            "label": f'在高德查看{anchor.get("name") or "当前地点"}附近餐厅',
            "provider": "高德",
        }
    try:
        longitude, latitude = (float(value) for value in coordinates.split(",", 1))
    except (TypeError, ValueError):
        return None
    geoobj = "|".join((
        f"{longitude - 0.025:.6f}", f"{latitude - 0.018:.6f}",
        f"{longitude + 0.025:.6f}", f"{latitude + 0.018:.6f}",
    ))
    return {
        "type": "restaurant_search",
        "label": f'在高德查看{anchor.get("name") or "当前地点"}附近餐厅',
        "provider": "高德",
        "checked_at": meal.get("checked_at"),
        "disclaimer": "营业、排队和门店状态以高德实时页为准",
        "url": "https://ditu.amap.com/search?" + urlencode({
            "query": "餐厅",
            "geoobj": geoobj,
            "_src": "around",
            "zoom": "15",
            "SPQ": "true",
        }),
    }


def render_restaurant_route_leg(leg: dict[str, Any]) -> str:
    """Render an explicit route endpoint pair with its map link in the heading."""
    origin = leg.get("origin") or {}
    destination = leg.get("destination") or {}
    route_label = f'{esc(origin.get("name"))} → {esc(destination.get("name"))}'
    map_url = str(leg.get("map_url") or "")
    if map_url.startswith("https://"):
        link_title = "在高德查看路线" if urlparse(map_url).hostname == "uri.amap.com" else "查看路线依据"
        route_heading = f'<a class="restaurant-route-link" href="{esc(map_url)}" target="_blank" rel="noopener noreferrer" title="{link_title}"><strong>{route_label} ↗</strong></a>'
    else:
        route_heading = f'<strong>{route_label}</strong>'
    return f'''<div class="restaurant-route-leg">
      {route_heading}
      <span>{esc(origin.get("physical_address"))} → {esc(destination.get("physical_address"))}</span>
      <small>{(esc(leg.get("distance_meters")) + "米 · ") if leg.get("distance_meters") is not None else ""}{"估算含缓冲 · " if leg.get("status") == "estimated" else ""}门到门{esc(approximate_minutes(leg.get("door_to_door_minutes")))}</small>
    </div>'''


def render_restaurant_candidate(
    restaurant: dict[str, Any],
    snapshot: dict[str, Any],
    route_evaluation: dict[str, Any],
    role: str,
    nearby_action: dict[str, Any] | None = None,
) -> str:
    """Render one source-backed restaurant candidate without blending platform scores."""
    location = restaurant.get("location") or {}
    operations = snapshot.get("operations") or {}
    signals = []
    for signal in snapshot.get("platform_signals") or []:
        if signal.get("status") == "unavailable":
            value = f'未取得（{signal.get("unavailable_reason") or "需手动复核"}）'
        else:
            rating = f'{signal.get("rating")}/{signal.get("scale")}' if signal.get("rating") is not None else "评分未取得"
            reviews = f'{signal.get("review_count")}条评价' if signal.get("review_count") is not None else "评价量未取得"
            value = f'{rating} · {reviews}'
        signals.append(f'<li><strong>{esc(provider_display_name(signal.get("platform")))}</strong><span>{esc(value)}</span><small>查询于 {esc(signal.get("checked_at"))}{(" · " + esc(signal.get("per_person"))) if signal.get("per_person") else ""}</small></li>')
    community = snapshot.get("community_consensus") or {}
    community_platform = provider_display_name(community.get("platform") or "社区")
    community_text = ""
    community_urls = {
        str(item.get("url") or "")
        for item in community.get("references") or []
        if str(item.get("url") or "").startswith("https://")
    }
    if community:
        community_links = "".join(
            f'<a href="{esc(item.get("url"))}" target="_blank" rel="noopener noreferrer">{esc(item.get("title") or "查看原帖")}{(" · " + esc(item.get("author"))) if item.get("author") else ""} ↗</a>'
            for item in community.get("references") or []
            if str(item.get("url") or "").startswith("https://")
        )
        manual_links = "".join(
            f'<a href="{esc(item.get("url"))}" target="_blank" rel="noopener noreferrer">{esc(item.get("label") or "在小红书搜索这家门店")} ↗</a>'
            for item in community.get("manual_action_links") or []
            if str(item.get("url") or "").startswith("https://")
        )
        if community.get("status") == "unavailable":
            community_text = f'''<div class="restaurant-community"><b>{esc(community_platform)}门店搜索</b>{f'<div class="restaurant-community-links">{manual_links}</div>' if manual_links else ''}</div>'''
        else:
            positive = "、".join(str(x) for x in community.get("positive") or []) or "未形成一致优点"
            negative = "、".join(str(x) for x in community.get("negative") or []) or "未形成一致风险"
            sample_note = " · 样本有限" if community.get("status") == "partial" else ""
            feedback_label = "样本反馈" if community.get("status") == "partial" else "常见反馈"
            community_text = f'''<div class="restaurant-community"><b>{esc(community_platform)}推荐 · {esc(str(community.get("notes_considered") or 0))} 篇近期门店原帖{sample_note}</b><p>{feedback_label}：{esc(positive)}；留意：{esc(negative)}</p>{f'<div class="restaurant-community-links">{community_links}</div>' if community_links else ''}</div>'''
    if snapshot.get("schema_version") == "restaurant-source-snapshot/v2":
        evidence_links = snapshot.get("evidence_links") or []
        lines = "".join(f'<p>{esc(item.get("summary"))}{" · 部分依据" if item.get("status") == "partial" else " · 到店前复核" if item.get("status") == "to_recheck" else ""}</p>' for item in snapshot.get("evidence") or [])
        links = "".join(f'<a href="{esc(item.get("url"))}" target="_blank" rel="noopener noreferrer">{esc(item.get("title") or "查看依据")} ↗</a>' for item in evidence_links)
        community_text = f'<div class="restaurant-community"><b>推荐与核验依据</b>{lines}<div class="restaurant-community-links">{links}</div></div>'
    visual = ""
    media = restaurant.get("media") or []
    display_images = [item for item in media if item.get("kind") == "display_image" and item.get("url")]
    if display_images:
        normalized = [{
            "url": item.get("url"), "alt": item.get("alt"), "source_url": item.get("source_url"),
            "source_label": item.get("source_label"), "author": item.get("author"),
            "license": item.get("license_or_permission"),
        } for item in display_images]
        visual = render_event_images(normalized[:2], str(restaurant.get("name") or "餐厅实景"))
    preview_actions = [{"type": "image_source", "label": item.get("alt") or "查看餐厅相册", "provider": item.get("source_label"), "url": item.get("source_url")} for item in media if item.get("kind") == "link_preview"]
    route_legs = []
    for leg_name in ("from_previous", "to_next"):
        leg = route_evaluation.get(leg_name) or {}
        route_legs.append(render_restaurant_route_leg(leg))
    route_facts = render_facts([
        ("额外绕行", approximate_minutes(route_evaluation.get("detour_minutes"))),
        ("停车/上下客", operations.get("parking")),
    ])
    heading = f'''<span class="restaurant-role">{esc(role)}</span><div><h4>{esc(restaurant.get("name"))}</h4><p>{esc(" · ".join(str(x) for x in restaurant.get("cuisine") or []))}</p></div><span class="restaurant-toggle" aria-hidden="true"></span>'''
    canonical_amap_actions = amap_restaurant_actions(restaurant, route_evaluation)
    supplementary_actions = [
        action for action in merge_actions(
            preview_actions,
            restaurant.get("action_links") or [],
            snapshot.get("action_links") or [],
        )
        if str(action.get("url") or "") not in community_urls
        and not (canonical_amap_actions and is_amap_restaurant_action(action))
    ]
    candidate_actions = merge_actions(
        canonical_amap_actions,
        supplementary_actions,
        [nearby_action] if nearby_action else [],
    )
    per_person = operations.get("per_person") or next((signal.get("per_person") for signal in snapshot.get("platform_signals") or [] if signal.get("per_person")), None)
    approach = (route_evaluation.get("from_previous") or {}).get("door_to_door_minutes")
    body = f'''<div class="restaurant-candidate-body">
      {render_facts([("特色菜", "、".join(str(x) for x in restaurant.get("signature_dishes") or [])), ("人均参考", per_person), ("准确位置", location.get("physical_address")), ("营业", operations.get("opening_hours")), ("到店耗时", approximate_minutes(approach) if approach is not None else None), ("预约方式", actionable_reservation(operations.get("reservation")))])}
      {render_actions(candidate_actions)}
      <details class="restaurant-more"><summary>查看前后路线与口碑依据</summary>{visual}
        <div class="restaurant-routes" aria-label="餐厅前后路线">{"".join(route_legs)}</div>{route_facts}
        {f'<ul class="restaurant-signals">{"".join(signals)}</ul>' if signals else ''}{community_text}
      </details></div>'''
    variant = "recommended" if role == "综合推荐" else "restaurant-candidate-backup"
    expanded = " open" if role == "综合推荐" else ""
    return f'''<details class="restaurant-candidate {variant}"{expanded}><summary class="restaurant-candidate-head">{heading}</summary>{body}</details>'''


def render_meal_candidates(
    meal: dict[str, Any],
    restaurants: dict[str, dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    route_evaluations: dict[str, dict[str, Any]],
) -> str:
    if meal.get("mode") == "area":
        area = meal.get("area_dining") or {}
        return render_facts(area_meal_facts(meal)) + render_actions(area.get("action_links") or [])
    candidates = sorted(meal.get("candidates") or [], key=lambda item: int(item.get("rank") or 999))
    if not candidates:
        return ""
    selected = meal.get("selected_candidate_id")
    selected_candidate = next(
        (item for item in candidates if str(item.get("restaurant_id") or "") == str(selected or "")),
        candidates[0],
    )
    restaurant_id = str(selected_candidate.get("restaurant_id") or "")
    nearby_action = amap_nearby_restaurant_action(meal)
    recommended_card = render_restaurant_candidate(
        restaurants.get(restaurant_id) or {},
        snapshots.get(str(selected_candidate.get("snapshot_id") or "")) or {},
        route_evaluations.get(str(selected_candidate.get("route_evaluation_id") or "")) or {},
        "综合推荐",
        nearby_action,
    )
    candidates_by_restaurant = {
        str(item.get("restaurant_id") or ""): item for item in candidates
    }
    backup_cards = []
    for fallback_id in meal.get("fallback_candidate_ids") or []:
        fallback_candidate = candidates_by_restaurant.get(str(fallback_id))
        if not fallback_candidate or str(fallback_id) == restaurant_id:
            continue
        backup_cards.append(render_restaurant_candidate(
            restaurants.get(str(fallback_id)) or {},
            snapshots.get(str(fallback_candidate.get("snapshot_id") or "")) or {},
            route_evaluations.get(str(fallback_candidate.get("route_evaluation_id") or "")) or {},
            "备选",
        ))
    return f'''<section class="restaurant-comparison" data-meal-id="{esc(meal.get('id'))}"><div class="restaurant-comparison-head"><span class="section-label">餐厅推荐</span></div>
      <div class="restaurant-grid">{recommended_card}</div>
      {f'<p class="meal-fallback">{esc(meal.get("fallback_rule"))}</p>' if meal.get("fallback_rule") else ''}
      {f'<div class="restaurant-backup-carousel" aria-label="备选餐厅，展开查看">{"".join(backup_cards)}</div>' if backup_cards else ''}</section>'''


def render_weather_badge(weather: dict[str, Any] | None) -> str:
    """Render weather as a compact linked status instead of another fact row."""
    if not weather:
        return ""
    links = [item for item in weather.get("action_links") or [] if str(item.get("url") or "").startswith("https://")]
    link = next((item for item in links if item.get("type") == "weather"), links[0] if links else None)
    if not link:
        return ""
    summary = " · ".join(str(value) for value in [weather.get("summary"), weather.get("temperature"), weather.get("warning")] if value)
    label = f'查看{weather.get("location") or "当地"}天气：{summary or "天气详情"}'
    icon = WEATHER_ICONS.get(str(weather.get("icon_code") or "unknown"), WEATHER_ICONS["unknown"])
    if weather.get("basis_type") == "seasonal":
        icon = "◌"
    return f'''<a class="weather-badge" href="{esc(link.get('url'))}" target="_blank" rel="noopener noreferrer" aria-label="{esc(label)}" title="{esc(label)}"><span aria-hidden="true">{icon}</span></a>'''


def render_admission_panel(event: dict[str, Any], booking_tasks: list[dict[str, Any]], attraction: dict[str, Any]) -> str:
    """Group all go/no-go admission information in one scannable block."""
    admission = event.get("admission") or {}
    if not admission:
        return ""
    official = attraction.get("official") or {}
    official_actions = []
    provider = attraction.get("name") or "景区官方"
    homepage_url = official.get("homepage_url")
    notice_url = official.get("notice_url")
    booking_url = official.get("booking_url")
    if homepage_url:
        official_actions.append({"type": "official_homepage", "label": "景区官网", "provider": provider, "url": homepage_url})
    if booking_url:
        booking_label = "官方预约说明" if official.get("booking_status") == "official_channel_listed" else "官方预约"
        if notice_url == booking_url:
            booking_label = "官方预约与公告"
        official_actions.append({"type": "official_booking", "label": booking_label, "provider": provider, "url": booking_url})
    if notice_url and notice_url != booking_url:
        official_actions.append({"type": "official_notice", "label": "临时公告", "provider": provider, "url": notice_url})
    wechat = official.get("wechat") or {}
    wechat_guide_url = str(wechat.get("guide_url") or "")
    booking_actions = merge_actions(official_actions, *(task.get("action_links") or [] for task in booking_tasks))
    if wechat_guide_url:
        booking_actions = [
            action for action in booking_actions
            if str(action.get("url") or "") != wechat_guide_url
        ]
    wechat_action = render_wechat_action(wechat)
    rows = [
        ("开放", admission.get("opening_hours")),
        ("停止入场", admission.get("last_entry")),
        ("预约方式", admission.get("reservation_method")),
        ("核验/入园", admission.get("entry_requirement")),
    ]
    facts = "".join(
        f'<div><span>{esc(label)}</span><strong>{esc(value)}</strong></div>'
        for label, value in rows if value
    )
    notice = admission.get("notice")
    preparation = "、".join(str(item) for item in event.get("preparation") or [])
    booking_html = render_booking_tasks(booking_tasks)
    cost_html = render_cost_items(
        event.get("cost_items") or [],
        event.get("cost_summary") or "",
        embedded=True,
    )
    return f'''<section class="admission-panel"><div class="section-label">开放与预约</div><div class="admission-grid">{facts}</div>
      {f'<p>{esc(notice)}</p>' if notice else ''}{f'<p class="preparation"><b>出发前准备</b>{esc(preparation)}</p>' if preparation else ''}
      {booking_html}{render_actions(booking_actions, wechat_action)}{cost_html}</section>'''


def render_checkpoints(
    event: dict[str, Any],
    meals: dict[str, dict[str, Any]],
    restaurants: dict[str, dict[str, Any]],
    restaurant_snapshots: dict[str, dict[str, Any]],
    meal_route_evaluations: dict[str, dict[str, Any]],
) -> str:
    """Render an attraction as a nested, time-based entry-to-exit itinerary."""
    execution = event.get("execution") or {}
    checkpoints = sorted(execution.get("checkpoints") or [], key=lambda item: item.get("order", 0))
    if not checkpoints:
        legacy = event.get("visit_order") or []
        checkpoints = [
            {"order": index, "name": name, "required": True, "instruction": "按顺序游览"}
            for index, name in enumerate(legacy, start=1)
        ]
    if not checkpoints:
        return ""
    rows = []
    entry = execution.get("entry") or {}
    exit_point = execution.get("exit") or {}
    last_index = len(checkpoints) - 1
    for index, point in enumerate(checkpoints):
        movement = point.get("move_from_previous") or {}
        move_text = " · ".join(str(value) for value in [movement.get("mode"), movement.get("duration")] if value)
        requirement = "必达" if point.get("required") else "可选"
        point_images = render_event_images((point.get("images") or [])[:3], str(point.get("name") or "景点节点")).replace('class="image-strip"', 'class="checkpoint-images"', 1)
        narration = str(point.get("narration") or "").strip()
        if narration in LEGACY_CHECKPOINT_PLACEHOLDERS:
            narration = ""
        entry_html = ""
        exit_html = ""
        if index == 0:
            entry_detail = " · ".join(
                str(value) for value in (entry.get("location_query"), entry.get("reason")) if value
            )
            entry_html = (
                f'<div class="checkpoint-endpoint checkpoint-entry"><span>入口</span>'
                f'<strong>{esc(entry.get("name"))}</strong>'
                f'{f"<small>{esc(entry_detail)}</small>" if entry_detail else ""}</div>'
            )
        if index == last_index:
            exit_label = f'{execution.get("leave_by")} 前离开' if execution.get("leave_by") else "出口"
            exit_html = (
                f'<div class="checkpoint-endpoint checkpoint-exit"><span>{esc(exit_label)}</span>'
                f'<strong>{esc(exit_point.get("name"))}</strong>'
                f'{f"<small>{esc(exit_point.get("location_query"))}</small>" if exit_point.get("location_query") else ""}</div>'
            )
        checkpoint_meal = meals.get(str(point.get("meal_id") or "")) if point.get("meal_id") else None
        meal_html = ""
        if checkpoint_meal:
            meal_html = render_meal_candidates(
                checkpoint_meal, restaurants, restaurant_snapshots, meal_route_evaluations,
            )
        rows.append(f'''<li class="checkpoint checkpoint-{esc(point.get('kind') or 'visit')}">
          <div class="checkpoint-time"><strong>{esc(point.get("time") or "顺序")}</strong>{f'<span>– {esc(point.get("end_time"))}</span>' if point.get("end_time") else ''}</div>
          <div class="checkpoint-content">{entry_html}{f'<p class="checkpoint-move">从上一点：{esc(move_text)}</p>' if move_text else ''}
            <div class="checkpoint-title"><strong>{esc(point.get("name"))}</strong><em>{requirement}</em></div>
            {f'<p class="checkpoint-instruction">{esc(point.get("instruction"))}</p>' if point.get("instruction") else ''}
            {f'<p class="checkpoint-narration"><b>现场看点</b>{esc(narration)}</p>' if narration else ''}
            {point_images}{meal_html}{render_actions(point.get("action_links") or [])}
            {f'<small>无法执行时：{esc(point.get("fallback"))}</small>' if point.get("fallback") else ''}
            {exit_html}
          </div></li>''')
    return f'''<section class="checkpoint-list"><div class="section-label">景区内怎么玩</div><ol>{"".join(rows)}</ol>
      {f'<p class="execution-fallback">整体备选：{esc(execution.get("fallback"))}</p>' if execution.get("fallback") else ''}</section>'''


def render_booking_tasks(tasks: list[dict[str, Any]]) -> str:
    """Render reservation instructions inside the attraction admission panel."""
    if not tasks:
        return ""
    cards = []
    for task in tasks:
        facts = render_facts([
            ("抢什么", task.get("product") or task.get("title")),
            ("人数", task.get("quantity")),
            ("何时操作", task.get("next_action_at") or task.get("sales_open_at")),
            ("放票规则", task.get("release_rule")),
            ("截止", task.get("deadline")),
            ("当前状态", traveler_status(task.get("status"))),
            ("具体动作", task.get("action")),
        ])
        cards.append(f'''<article><h5>{esc(task.get("title") or "预约/抢票")}</h5>{facts}</article>''')
    return f'<section class="admission-booking"><h4>预约行动</h4>{"".join(cards)}</section>'


def render_event_images(images: list[dict[str, Any]], title: str) -> str:
    figures = []
    for image in images:
        url = str(image.get("url") or "")
        if not url.startswith("https://"):
            continue
        picture = f'<img src="{esc(url)}" alt="{esc(image.get("alt") or title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest(\'figure\').remove()">'
        source_url = str(image.get("source_url") or "")
        if source_url.startswith("https://"):
            picture = f'<a href="{esc(source_url)}" target="_blank" rel="noopener noreferrer">{picture}</a>'
        attribution = " · ".join(
            str(value) for value in [image.get("source_label"), image.get("author"), image.get("license")]
            if value
        )
        figures.append(f'<figure class="image-item">{picture}{f"<figcaption>{esc(attribution)}</figcaption>" if attribution else ""}</figure>')
    return f'<div class="image-strip">{"".join(figures)}</div>' if figures else ""


def render_community_refs(items: list[dict[str, Any]]) -> str:
    cards = []
    for item in items or []:
        source_url = str(item.get("source_url") or "")
        if not source_url.startswith("https://"):
            continue
        interactions = item.get("interactions") or {}
        metrics = " · ".join(
            label for label in [
                f'赞 {interactions.get("likes")}' if interactions.get("likes") not in (None, "") else "",
                f'评 {interactions.get("comments")}' if interactions.get("comments") not in (None, "") else "",
                f'藏 {interactions.get("collections")}' if interactions.get("collections") not in (None, "") else "",
            ] if label
        )
        cards.append(f'''<a class="community-card" href="{esc(source_url)}" target="_blank" rel="noopener noreferrer"><strong>{esc(item.get("title") or "查看社区参考")}</strong><span>{esc(item.get("author"))}{(" · " + esc(metrics)) if metrics else ""}</span>{f'<small>{esc(item.get("reason"))}</small>' if item.get("reason") else ''}</a>''')
    if not cards:
        return ""
    return f'''<section class="community-refs"><h4>小红书近期体验参考</h4><p>用于体感、拥挤和避坑判断；开放、票价、预约及交通规则仍以官方来源为准。</p><div>{"".join(cards)}</div></section>'''


def validate_action_links(value: Any, path: str = "data") -> None:
    """Reject unsafe links anywhere in the structured plan, not only in events."""
    if isinstance(value, dict):
        actions = value.get("action_links")
        if actions is not None:
            if not isinstance(actions, list):
                raise ValueError(f"{path}.action_links 必须是数组")
            for index, action in enumerate(actions):
                if not isinstance(action, dict) or not str(action.get("url") or "").startswith("https://"):
                    raise ValueError(f"{path}.action_links[{index}] 必须使用 HTTPS")
        for key, child in value.items():
            validate_action_links(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_action_links(child, f"{path}[{index}]")


def parse_coordinates(value: Any, path: str) -> tuple[float, float]:
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", str(value or ""))
    if not match:
        raise ValueError(f"{path} 必须是 lng,lat 数值坐标")
    lng, lat = float(match.group(1)), float(match.group(2))
    if not math.isfinite(lng) or not math.isfinite(lat) or not -180 <= lng <= 180 or not -90 <= lat <= 90:
        raise ValueError(f"{path} 超出合法经纬度范围")
    return lng, lat


def normalized_place_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def point_distance_meters(first: tuple[float, float], second: tuple[float, float]) -> float:
    mean_lat = math.radians((first[1] + second[1]) / 2)
    dx = (first[0] - second[0]) * 111_320 * math.cos(mean_lat)
    dy = (first[1] - second[1]) * 110_540
    return math.hypot(dx, dy)


def iso_date(value: Any, path: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as error:
        raise ValueError(f"{path} 必须是 ISO 日期或时间") from error


def validate_inventory_research(data: dict[str, Any]) -> None:
    """Validate that normalized provider snapshots are bound to concrete trip candidates."""
    planning = data.get("planning") or {}
    snapshots = planning.get("source_snapshots") or []
    if not snapshots:
        return
    snapshot_map: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        if not snapshot_id or snapshot_id in snapshot_map:
            raise ValueError("酒旅 source_snapshots 的 snapshot_id 缺失或重复")
        if snapshot.get("schema_version") != "travel-source-snapshot/v1":
            raise ValueError(f"酒旅快照“{snapshot_id}”的 schema_version 无效")
        if snapshot.get("status") not in {"platform_reported", "no_results"}:
            raise ValueError(f"酒旅快照“{snapshot_id}”的 status 无效")
        freshness = snapshot.get("freshness") or {}
        if not freshness.get("checked_at") or not freshness.get("expires_at"):
            raise ValueError(f"酒旅快照“{snapshot_id}”缺少 checked_at 或 expires_at")
        checked = iso_date(freshness["checked_at"], f"酒旅快照“{snapshot_id}”.checked_at")
        expires = iso_date(freshness["expires_at"], f"酒旅快照“{snapshot_id}”.expires_at")
        if expires <= checked:
            raise ValueError(f"酒旅快照“{snapshot_id}”的 expires_at 必须晚于 checked_at")
        items = snapshot.get("items") or []
        if snapshot.get("count") != len(items):
            raise ValueError(f"酒旅快照“{snapshot_id}”的 count 与 items 不一致")
        offer_ids = [str(item.get("offer_id") or "") for item in items]
        if any(not offer_id for offer_id in offer_ids) or len(offer_ids) != len(set(offer_ids)):
            raise ValueError(f"酒旅快照“{snapshot_id}”的 offer_id 缺失或重复")
        snapshot_map[snapshot_id] = snapshot

    for group_name, candidates in (
        ("transport_edges", planning.get("transport_edges") or []),
        ("intercity_options", planning.get("intercity_options") or []),
        ("lodging_options", planning.get("lodging_options") or []),
    ):
        for candidate in candidates:
            for ref in candidate.get("inventory_refs") or []:
                if any(not ref.get(field) for field in ("snapshot_id", "offer_id", "role")):
                    raise ValueError(f"{group_name} 候选“{candidate.get('id') or '未命名'}”的 inventory_ref 不完整")
                snapshot = snapshot_map.get(str(ref["snapshot_id"]))
                if not snapshot:
                    raise ValueError(f"{group_name} 候选“{candidate.get('id') or '未命名'}”引用了不存在的酒旅快照")
                offer_ids = {str(item.get("offer_id")) for item in snapshot.get("items") or []}
                if str(ref["offer_id"]) not in offer_ids:
                    raise ValueError(f"{group_name} 候选“{candidate.get('id') or '未命名'}”的 offer_id 不属于引用快照")
                if ref.get("role") not in {"candidate_quote", "operational_check", "station_lookup"}:
                    raise ValueError(f"{group_name} 候选“{candidate.get('id') or '未命名'}”的 inventory_ref.role 无效")
                if ref.get("role") == "candidate_quote" and snapshot.get("snapshot_kind") != "quote":
                    raise ValueError(f"{group_name} 候选“{candidate.get('id') or '未命名'}”的报价引用不是 quote 快照")
                if group_name == "lodging_options" and snapshot.get("product_type") != "hotel":
                    raise ValueError("住宿候选只能引用 hotel 快照")
                if group_name in {"transport_edges", "intercity_options"} and snapshot.get("product_type") == "hotel":
                    raise ValueError("城际交通候选不能引用 hotel 快照")


def validate_restaurant_research_integrity(data: dict[str, Any]) -> None:
    """Cross-check identity, evidence, route baselines and source closure once for every meal."""
    planning = data.get("planning") or {}
    if planning.get("restaurant_research_version") == 4:
        registry = {item["id"]: item for item in data.get("sources") or []}
        dates = {event.get("meal_id"): day.get("date") for day in data.get("days") or [] for event in day.get("events") or [] if event.get("meal_id")}
        for day in data.get("days") or []:
            for event in day.get("events") or []:
                for point in (event.get("execution") or {}).get("checkpoints") or []:
                    if point.get("meal_id"):
                        dates[point["meal_id"]] = day.get("date")
        for meal in planning.get("meal_options") or []:
            if meal.get("id") in dates:
                research_evidence.validate_meal(meal, dates[meal["id"]], planning, registry)
        return
    if planning.get("restaurant_research_version") != 3:
        return
    restaurants = {str(item.get("id") or ""): item for item in planning.get("restaurants") or []}
    snapshots = {str(item.get("snapshot_id") or ""): item for item in planning.get("restaurant_snapshots") or []}
    evaluations = {str(item.get("id") or ""): item for item in planning.get("meal_route_evaluations") or []}
    baselines = {str(item.get("id") or ""): item for item in planning.get("meal_baseline_routes") or []}
    source_registry = {str(item.get("id") or ""): item for item in data.get("sources") or [] if item.get("id")}
    sources = set(source_registry)
    if not source_registry:
        raise ValueError("餐厅研究必须提供顶层 sources 来源注册表")

    referenced_sources: set[str] = set()
    def collect_sources(value: Any) -> None:
        if isinstance(value, dict):
            if "source_ids" in value:
                if not isinstance(value["source_ids"], list):
                    raise ValueError("餐厅研究中的 source_ids 必须是数组")
                referenced_sources.update(str(item) for item in value["source_ids"] if item)
            for child in value.values():
                collect_sources(child)
        elif isinstance(value, list):
            for child in value:
                collect_sources(child)

    research_objects = [
        planning.get("restaurants") or [], planning.get("restaurant_snapshots") or [],
        planning.get("meal_baseline_routes") or [], planning.get("meal_route_evaluations") or [],
        planning.get("meal_options") or [],
    ]
    collect_sources(research_objects)
    missing_sources = sorted(referenced_sources - sources)
    if missing_sources:
        raise ValueError(f"餐厅研究引用了不存在的 source_ids：{', '.join(missing_sources)}")

    restaurant_points: dict[str, tuple[float, float]] = {}
    for restaurant_id, restaurant in restaurants.items():
        location = restaurant.get("location") or {}
        if location.get("coordinate_system") not in {"GCJ-02", "WGS84"}:
            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”必须声明 GCJ-02 或 WGS84 坐标系")
        point = parse_coordinates(location.get("coordinates"), f"餐厅“{restaurant.get('name') or restaurant_id}”坐标")
        address = str(location.get("physical_address") or "").strip()
        poi_id = str(location.get("poi_id") or "").strip()
        placeholders = ("附近", "周边", "待定", "待确认", "unknown", "tbd", "景区内")
        if len(normalized_place_text(address)) < 6 or any(token in address.casefold() for token in placeholders):
            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”必须提供可定位的完整门牌地址")
        if normalized_place_text(poi_id) in {"", "unknown", "tbd", "none", "待定", "待确认"}:
            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”必须提供真实平台 POI ID")
        poi_provider = str(location.get("poi_provider") or "").casefold()
        poi_source_id = str(location.get("poi_source_id") or "")
        poi_source = source_registry.get(poi_source_id) or {}
        provider_ids = [str(item) for item in poi_source.get("provider_poi_ids") or []]
        if not poi_provider or not location.get("poi_verified_at") or poi_source_id not in (restaurant.get("source_ids") or []):
            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 POI 缺少 provider、核验时间或证据来源绑定")
        if poi_source.get("kind") not in {"map", "restaurant_platform"} or str(poi_source.get("provider") or "").casefold() != poi_provider or poi_id not in provider_ids:
            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 POI 与地图来源证据不一致")
        restaurant_points[restaurant_id] = point

    for meal in planning.get("meal_options") or []:
        meal_id = str(meal.get("id") or "")
        candidate_ids = [str(item) for item in meal.get("candidate_ids") or []]
        for anchor_name in ("previous_anchor", "next_anchor"):
            anchor = meal.get(anchor_name) or {}
            if not all(anchor.get(field) for field in ("name", "physical_address", "coordinates")):
                raise ValueError(f"餐饮“{meal_id}”的 {anchor_name} 必须提供名称、具体地址和坐标")
        max_detour = (meal.get("constraints") or {}).get("max_detour_minutes")
        if not isinstance(max_detour, (int, float)) or isinstance(max_detour, bool) or max_detour < 0:
            raise ValueError(f"餐饮“{meal_id}”必须提供非负数值 max_detour_minutes")
        baseline_id = str(meal.get("baseline_route_id") or "")
        baseline = baselines.get(baseline_id)
        if not baseline or baseline.get("meal_id") != meal_id:
            raise ValueError(f"餐饮“{meal_id}”必须引用本餐唯一的 baseline route")
        meal_baselines = [item for item in baselines.values() if item.get("meal_id") == meal_id]
        if len(meal_baselines) != 1:
            raise ValueError(f"餐饮“{meal_id}”必须且只能有一条统一 baseline route")
        for anchor_field, baseline_field in (("previous_anchor", "from_anchor"), ("next_anchor", "to_anchor")):
            anchor = meal.get(anchor_field) or {}
            endpoint = baseline.get(baseline_field) or {}
            if (
                any(not endpoint.get(field) for field in ("name", "physical_address", "coordinates"))
                or any(endpoint.get(field) != anchor.get(field) for field in ("name", "physical_address"))
                or parse_coordinates(endpoint.get("coordinates"), f"{baseline_id}.{baseline_field}") != parse_coordinates(anchor.get("coordinates"), f"{meal_id}.{anchor_field}")
            ):
                raise ValueError(f"餐饮“{meal_id}”的 baseline route 锚点与餐窗不一致")
        baseline_required = {"mode", "routing_policy", "departure_at", "distance_meters", "duration_minutes", "door_to_door_minutes", "map_url", "checked_at", "source_ids"}
        if any(baseline.get(field) in (None, "", []) for field in baseline_required) or not str(baseline.get("map_url") or "").startswith("https://"):
            raise ValueError(f"餐饮“{meal_id}”的 baseline route 缺少同口径路线证据")

        seen_pois: set[str] = set()
        seen_addresses: set[str] = set()
        seen_name_points: list[tuple[str, tuple[float, float]]] = []
        bindings = {str(item.get("restaurant_id") or ""): item for item in meal.get("candidates") or []}
        for restaurant_id in candidate_ids:
            restaurant = restaurants.get(restaurant_id) or {}
            location = restaurant.get("location") or {}
            poi_key = normalized_place_text(location.get("poi_id"))
            address_key = normalized_place_text(location.get("physical_address"))
            name_key = normalized_place_text(restaurant.get("name"))
            point = restaurant_points.get(restaurant_id)
            if poi_key in seen_pois or address_key in seen_addresses or any(name_key == old_name and point and point_distance_meters(point, old_point) <= 30 for old_name, old_point in seen_name_points):
                raise ValueError(f"餐饮“{meal_id}”包含重复餐厅实体，不能靠不同 ID 充当候选")
            seen_pois.add(poi_key)
            seen_addresses.add(address_key)
            if point:
                seen_name_points.append((name_key, point))

            binding = bindings.get(restaurant_id) or {}
            snapshot = snapshots.get(str(binding.get("snapshot_id") or "")) or {}
            community = snapshot.get("community_consensus") or {}
            if community.get("status") == "unavailable":
                required_unavailable = {"query_runs", "failure_kind", "unavailable_reason", "manual_action_links", "recheck_at", "checked_at"}
                if any(community.get(field) in (None, "", []) for field in required_unavailable):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”社区不可用时必须记录查询、失败类型、手动入口和复核时间")
                if any(not str(action.get("url") or "").startswith("https://") for action in community.get("manual_action_links") or []):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区手动查询入口必须使用 HTTPS")
            else:
                references = community.get("references") or []
                if not community.get("query_runs"):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区研究必须记录实际查询条件")
                note_ids: set[str] = set()
                urls: set[str] = set()
                authors: set[str] = set()
                checked = iso_date(community.get("checked_at"), f"{restaurant_id}.community.checked_at")
                for reference in references:
                    required_reference = {"note_id", "title", "url", "author", "published_at", "checked_at", "source_id"}
                    if any(reference.get(field) in (None, "") for field in required_reference):
                        raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区原帖缺少 ID、作者、发布时间、查询时间或来源")
                    if str(reference["source_id"]) not in sources:
                        raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区原帖引用了不存在的来源")
                    host = (urlparse(str(reference["url"])).hostname or "").casefold()
                    platform = provider_display_name(reference.get("platform") or community.get("platform"))
                    domain = {"小红书": "xiaohongshu.com"}.get(platform)
                    if domain and not (host == domain or host.endswith("." + domain)):
                        raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的{platform}原帖 URL 域名不匹配")
                    if not domain and "小红书" in platform:
                        raise ValueError("混合社区来源须逐条标注原帖 platform，小红书原帖使用 platform=小红书")
                    published = iso_date(reference["published_at"], f"{restaurant_id}.community.published_at")
                    reference_checked = iso_date(reference["checked_at"], f"{restaurant_id}.community.reference.checked_at")
                    if published > checked or published < checked - timedelta(days=366):
                        raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区原帖不在查询前12个月内")
                    if reference_checked < published:
                        raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区原帖查询时间早于发布时间")
                    if reference["note_id"] in note_ids or reference["url"] in urls:
                        raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区原帖不能重复")
                    note_ids.add(str(reference["note_id"]))
                    urls.add(str(reference["url"]))
                    authors.add(normalized_place_text(reference["author"]))
                minimum = 3 if restaurant_id == meal.get("selected_candidate_id") else 2
                if int(community.get("recent_note_count") or 0) != len(references) or int(community.get("notes_considered") or 0) < len(references):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区统计与原帖明细不一致")
                if community.get("status") == "partial" and (not references or community.get("confidence") != "low"):
                    raise ValueError("社区 partial 必须有至少一条实际原帖且 confidence=low")
                if community.get("status") != "partial" and len(references) >= minimum and len(authors) < 2:
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区参考必须来自至少2位作者")

            evaluation = evaluations.get(str(binding.get("route_evaluation_id") or "")) or {}
            if evaluation.get("baseline_route_id") != baseline_id:
                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”必须引用本餐统一 baseline route")
            basis = evaluation.get("comparison_basis") or {}
            if any(basis.get(field) != baseline.get(field) for field in ("mode", "routing_policy", "departure_at")):
                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的候选路线与 baseline 口径不一致")
            restaurant_endpoint = {"name": restaurant.get("name"), **(restaurant.get("location") or {})}
            expected_endpoints = (("from_previous", meal.get("previous_anchor") or {}, restaurant_endpoint), ("to_next", restaurant_endpoint, meal.get("next_anchor") or {}))
            for leg_name, expected_origin, expected_destination in expected_endpoints:
                leg = evaluation.get(leg_name) or {}
                if not leg.get("route_id"):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 {leg_name} 缺少 route_id")
                if not isinstance(leg.get("door_to_door_minutes"), (int, float)) or isinstance(leg.get("door_to_door_minutes"), bool):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 {leg_name} 缺少数值门到门耗时")
                origin = leg.get("origin") or {}
                destination = leg.get("destination") or {}
                if any(not endpoint.get(field) for endpoint in (origin, destination) for field in ("name", "physical_address", "coordinates")):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 {leg_name} 端点必须提供名称、具体地址和坐标")
                if any(origin.get(field) != expected_origin.get(field) for field in ("name", "physical_address")) or parse_coordinates(origin.get("coordinates"), f"{leg_name}.origin") != parse_coordinates(expected_origin.get("coordinates"), f"{leg_name}.expected_origin"):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 {leg_name} 起点与餐窗锚点不一致")
                if any(destination.get(field) != expected_destination.get(field) for field in ("name", "physical_address")) or parse_coordinates(destination.get("coordinates"), f"{leg_name}.destination") != parse_coordinates(expected_destination.get("coordinates"), f"{leg_name}.expected_destination"):
                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 {leg_name} 终点与餐窗锚点不一致")
            if not isinstance(evaluation.get("total_door_to_door_minutes"), (int, float)) or not isinstance(evaluation.get("baseline_door_to_door_minutes"), (int, float)) or not isinstance(baseline.get("door_to_door_minutes"), (int, float)):
                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”缺少可比较的数值路线耗时")
            total = float((evaluation.get("from_previous") or {}).get("door_to_door_minutes")) + float((evaluation.get("to_next") or {}).get("door_to_door_minutes"))
            if abs(total - float(evaluation.get("total_door_to_door_minutes"))) > 0.01:
                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的候选总耗时必须等于两腿门到门耗时之和")
            if abs(float(evaluation.get("baseline_door_to_door_minutes")) - float(baseline.get("door_to_door_minutes"))) > 0.01:
                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 baseline 耗时与统一基准路线不一致")


def validate_embedded_meal(
    title: str,
    meal_id: str,
    applicable_date: str,
    planning: dict[str, Any],
    meals: dict[str, dict[str, Any]],
    restaurants: dict[str, dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    evaluations: dict[str, dict[str, Any]],
) -> None:
    """Apply the versioned restaurant gate to checkpoint meals."""
    if planning.get("restaurant_research_version") == 4:
        # Full source and route validation is performed by the integrity gate.
        if meal_id not in meals:
            raise ValueError(f"景点节点“{title}”引用了无效 meal_id")
        return
    if planning.get("restaurant_research_version") != 3:
        raise ValueError("正式规划必须使用 restaurant_research_version=3 的餐厅候选研究契约")
    meal = meals.get(meal_id)
    if not meal:
        raise ValueError(f"景点节点“{title}”引用了无效 meal_id")
    duplicate_fields = sorted(DUPLICATE_MEAL_SUMMARY_FIELDS.intersection(meal))
    if duplicate_fields:
        raise ValueError(f"景点节点“{title}”的嵌入用餐不得复制候选摘要字段：{', '.join(duplicate_fields)}")
    required_display = {"name", "time_window"}
    missing_display = sorted(field for field in required_display if not meal.get(field))
    if missing_display:
        raise ValueError(f"景点节点“{title}”的嵌入用餐缺少展示字段：{', '.join(missing_display)}")
    required = {
        "previous_anchor", "next_anchor", "constraints", "candidate_ids",
        "selected_candidate_id", "fallback_candidate_ids", "candidates",
        "candidate_policy", "baseline_route_id", "selection_summary", "fallback_rule", "checked_at",
    }
    missing = sorted(field for field in required if field not in meal or meal.get(field) in (None, ""))
    if missing:
        raise ValueError(f"景点节点“{title}”的嵌入用餐缺少正式候选研究字段：{', '.join(missing)}")
    for anchor_name in ("previous_anchor", "next_anchor"):
        anchor = meal.get(anchor_name) or {}
        if not all(anchor.get(field) for field in ("name", "physical_address", "coordinates")):
            raise ValueError(f"景点节点“{title}”的 {anchor_name} 必须提供名称、具体地址和坐标")
    candidate_ids = [str(value) for value in meal.get("candidate_ids") or []]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"景点节点“{title}”的 candidate_ids 不能重复")
    policy = meal.get("candidate_policy") or {}
    status = policy.get("status")
    if status == "normal" and not 2 <= len(candidate_ids) <= 3:
        raise ValueError(f"景点节点“{title}”的正常用餐必须提供2至3个餐厅候选")
    if status == "constrained":
        required_policy = {"restriction_reason", "search_scope", "emergency_fallback", "source_ids"}
        if any(not policy.get(field) for field in required_policy):
            raise ValueError(f"景点节点“{title}”的受限候选必须记录搜索范围、原因、应急备选和来源")
    elif status != "normal":
        raise ValueError(f"景点节点“{title}”的 candidate_policy.status 无效")
    selected = str(meal.get("selected_candidate_id") or "")
    fallback_ids = [str(value) for value in meal.get("fallback_candidate_ids") or []]
    if selected not in candidate_ids or any(value not in candidate_ids or value == selected for value in fallback_ids):
        raise ValueError(f"景点节点“{title}”的主选或备选餐厅引用无效")
    if status == "normal" and not fallback_ids:
        raise ValueError(f"景点节点“{title}”的正常用餐必须提供结构化备选餐厅")
    bindings = {str(item.get("restaurant_id") or ""): item for item in meal.get("candidates") or []}
    if set(bindings) != set(candidate_ids):
        raise ValueError(f"景点节点“{title}”必须为每个候选绑定动态快照和双腿路线")
    ranks = [item.get("rank") for item in meal.get("candidates") or []]
    if any(not isinstance(rank, int) for rank in ranks) or sorted(ranks) != list(range(1, len(candidate_ids) + 1)) or bindings[selected].get("rank") != 1:
        raise ValueError(f"景点节点“{title}”的候选 rank 必须连续，且主选必须是 rank=1")
    if not isinstance(policy.get("searched_count"), int) or policy["searched_count"] < len(candidate_ids):
        raise ValueError(f"景点节点“{title}”必须记录实际搜索候选数")
    max_detour = (meal.get("constraints") or {}).get("max_detour_minutes")
    for restaurant_id in candidate_ids:
        restaurant = restaurants.get(restaurant_id) or {}
        location = restaurant.get("location") or {}
        if not restaurant.get("name") or not restaurant.get("signature_dishes") or not restaurant.get("media") or not restaurant.get("action_links") or not restaurant.get("source_ids"):
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少实体、特色菜、可视证据、链接或来源")
        if not all(location.get(field) for field in ("physical_address", "coordinates", "coordinate_system", "poi_id")):
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少准确地址、坐标或 POI")
        if not any(action.get("type") in {"restaurant", "map"} for action in restaurant.get("action_links") or []):
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少餐厅详情或地图入口")
        for media_index, media in enumerate(restaurant.get("media") or []):
            if media.get("kind") == "display_image":
                needed = {"url", "alt", "source_url", "license_or_permission", "checked_at"}
                if any(not media.get(field) for field in needed) or not (media.get("author") or media.get("source_label")):
                    raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”展示图片[{media_index}]缺少权利或来源信息")
            elif media.get("kind") == "link_preview":
                if not all(media.get(field) for field in ("source_url", "alt", "source_label")):
                    raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”链接预览[{media_index}]缺少来源")
            else:
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”media.kind 无效")
        binding = bindings[restaurant_id]
        snapshot = snapshots.get(str(binding.get("snapshot_id") or "")) or {}
        if snapshot.get("restaurant_id") != restaurant_id or snapshot.get("schema_version") != "restaurant-source-snapshot/v1":
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少匹配动态快照")
        if not snapshot.get("platform_signals") or not snapshot.get("community_consensus") or not snapshot.get("operations") or not snapshot.get("action_links") or not snapshot.get("source_ids") or not snapshot.get("expires_at"):
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少平台、社区、营业或复核数据")
        if snapshot.get("applicable_date") != applicable_date or snapshot.get("time_window") != meal.get("time_window"):
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”动态快照日期或用餐时段不一致")
        for signal in snapshot.get("platform_signals") or []:
            if not signal.get("platform") or not signal.get("checked_at") or not signal.get("source_ids"):
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”平台信号缺少平台、查询时间或来源")
            if signal.get("status") == "unavailable":
                if not signal.get("unavailable_reason"):
                    raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”未取得评分时必须说明原因")
            elif signal.get("rating") is None:
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”可用平台评分必须提供评分展示值")
        community = snapshot.get("community_consensus") or {}
        if not community.get("checked_at") or "notes_considered" not in community or "recent_note_count" not in community:
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少社区研究结论")
        if community.get("status") == "unavailable":
            if not community.get("unavailable_reason"):
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”必须说明社区来源不可用原因")
        else:
            minimum_notes = 1 if community.get("status") == "partial" else 3 if restaurant_id == selected else 2
            references = community.get("references") or []
            if int(community.get("notes_considered") or 0) < minimum_notes or int(community.get("recent_note_count") or 0) < minimum_notes or len(references) < minimum_notes:
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少近期社区原帖参考")
            if any(not ref.get("title") or not str(ref.get("url") or "").startswith("https://") for ref in references):
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”社区参考必须提供标题和 HTTPS 原帖链接")
        operations = snapshot.get("operations") or {}
        required_operations = {"opening_hours", "reservation", "queue", "parking", "status", "checked_at", "recheck_at"}
        if any(not operations.get(field) for field in required_operations):
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少营业、预约、排队、停车或复核时间")
        evaluation = evaluations.get(str(binding.get("route_evaluation_id") or "")) or {}
        if evaluation.get("restaurant_id") != restaurant_id or evaluation.get("meal_id") != meal_id:
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少匹配双腿路线")
        for leg_name in ("from_previous", "to_next"):
            leg = evaluation.get(leg_name) or {}
            if any(leg.get(field) is None for field in ("distance_meters", "duration_minutes", "door_to_door_minutes")) or not str(leg.get("map_url") or "").startswith("https://"):
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少完整 {leg_name} 路线")
            if any(not (leg.get(endpoint) or {}).get(field) for endpoint in ("origin", "destination") for field in ("name", "physical_address", "coordinates")):
                raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”的 {leg_name} 端点缺少名称、具体地址或坐标")
        route_numbers = ("total_door_to_door_minutes", "baseline_door_to_door_minutes", "detour_minutes")
        if any(evaluation.get(field) is None for field in route_numbers) or not evaluation.get("checked_at") or not evaluation.get("source_ids"):
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”缺少路线基准、绕行、查询时间或来源")
        if abs(float(evaluation["total_door_to_door_minutes"]) - float(evaluation["baseline_door_to_door_minutes"]) - float(evaluation["detour_minutes"])) > 0.01:
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”绕行计算不一致")
        if isinstance(max_detour, (int, float)) and float(evaluation["detour_minutes"]) > max_detour:
            raise ValueError(f"景点节点“{title}”的餐厅候选“{restaurant_id}”超过最大允许绕行")


def validate_food_walk(event: dict[str, Any], sources: set[str]) -> None:
    walk = event.get("food_walk")
    research_evidence.validate_food_walk_structure(walk)
    for field in ("entry", "exit"):
        point = walk[field]
        parse_coordinates(point["coordinates"], f"food_walk.{field}")
        if not str(point["map_url"]).startswith("https://"):
            raise ValueError("逛吃地图入口必须使用 HTTPS")
    for item in walk["food_items"]:
        if not set(item["source_ids"]).issubset(sources):
            raise ValueError("逛吃食物必须有名称和有效来源，不把城市特色直接写成该街有售")


def dining_fallback_text(value: Any, *, allow_legacy_text: bool = False) -> str:
    research_evidence.validate_dining_fallback(value, allow_legacy_text=allow_legacy_text)
    if isinstance(value, str):
        return value.strip()
    return f"{value['name'].strip()}（{value['physical_address'].strip()}）：{value['plan'].strip()}"


def food_walk_lines(event: dict[str, Any]) -> list[str]:
    walk = event.get("food_walk")
    research_evidence.validate_food_walk_structure(walk)
    return [
        f"从 {(walk.get('entry') or {}).get('name')}（{(walk.get('entry') or {}).get('physical_address')}）进入，至 {(walk.get('exit') or {}).get('name')}（{(walk.get('exit') or {}).get('physical_address')}）离开",
        "想吃：" + "、".join(str(item.get("name") or "") for item in walk.get("food_items") or []),
        f"人均预算约 {walk.get('budget_per_person')} 元（估算，实际以摊位标价为准）",
        "无法逛吃时：" + dining_fallback_text(walk["fallback"], allow_legacy_text=True),
        *([str(walk["pet_note"])] if walk.get("pet_note") else []),
    ]


def validate_data(data: dict[str, Any]) -> None:
    """校验研究数据与最终日程之间的引用关系。"""
    trip, days = data.get("trip") or {}, data.get("days") or []
    if not trip.get("title") or not days:
        raise ValueError("必须提供 trip.title，并且 days 至少包含一天行程")
    source_ids = {str(item.get("id")) for item in data.get("sources") or [] if item.get("id")}
    for day in days:
        for event in day.get("events") or []:
            research_evidence.validate_event_prose(event)
            if event.get("type") == "food_walk":
                validate_food_walk(event, source_ids)
    planning = data.get("planning") or {}
    if not planning:
        return
    workflow = data.get("workflow") or {}
    evidence_blocking, _ = research_evidence.audit_research(data)
    if evidence_blocking:
        raise ValueError("；".join(evidence_blocking))
    comment_blocking, _, _ = xhs_comment_contract.audit_reviews(data)
    if comment_blocking:
        raise ValueError("；".join(comment_blocking))
    if workflow.get("phase") == "final" and not workflow.get("selected_route_id"):
        raise ValueError("最终行程必须包含已确认的 selected_route_id")
    if workflow.get("phase") == "final" and not planning.get("readiness"):
        raise ValueError("最终行程必须包含 planning.readiness 行前就绪检查")
    readiness_statuses = {"verified", "platform_reported", "estimated", "to_recheck", "not_applicable"}
    for item in planning.get("readiness") or []:
        if item.get("status") not in readiness_statuses:
            raise ValueError(f"行前就绪项“{item.get('category') or '未命名'}”的 status 无效")
        if item.get("status") == "to_recheck" and not item.get("action_links"):
            raise ValueError(f"待复核的行前就绪项“{item.get('category') or '未命名'}”必须提供 action_links")
    validate_action_links(data)
    depth_enabled = type(planning.get("research_depth_version")) is int and planning["research_depth_version"] == 1
    if "research_depth_version" in planning and not depth_enabled:
        raise ValueError("研究深度版本必须是1")
    for candidate in [*(planning.get("transport_edges") or []), *(planning.get("intercity_options") or [])]:
        research_evidence.validate_depth(candidate, "transport", depth_enabled)
        if depth_enabled and not candidate.get("selection_context"):
            raise ValueError("新交通研究必须声明选择场景，不默认展开全部比较")
        if depth_enabled or candidate.get("selection_context"):
            research_evidence.transport_comparison_sorts(candidate)
    area_sources = {item["id"]: item for item in data.get("sources") or [] if item.get("id")}
    for lodging in planning.get("lodging_options") or []:
        if not isinstance(lodging, dict):
            raise ValueError("住宿建议必须是对象")
        if lodging.get("kind") not in (None, "area", "hotel"):
            raise ValueError("住宿 kind 必须是 area 或 hotel")
        research_evidence.validate_lodging_area(lodging, area_sources)
    validate_inventory_research(data)
    validate_restaurant_research_integrity(data)
    attraction_records = {x.get("id"): x for x in planning.get("attractions") or [] if x.get("id")}
    attraction_ids = set(attraction_records)
    transport_edges = planning.get("transport_edges") or []
    route_ids = {x.get("id") for x in transport_edges if x.get("id")}
    intercity_ids = {x.get("id") for x in planning.get("intercity_options") or [] if x.get("id")}
    lodging_ids = {x.get("id") for x in planning.get("lodging_options") or [] if x.get("id")}
    meal_ids = {x.get("id") for x in planning.get("meal_options") or [] if x.get("id")}
    meals = {x.get("id"): x for x in planning.get("meal_options") or [] if x.get("id")}
    restaurants = {x.get("id"): x for x in planning.get("restaurants") or [] if x.get("id")}
    restaurant_snapshots = {x.get("snapshot_id"): x for x in planning.get("restaurant_snapshots") or [] if x.get("snapshot_id")}
    meal_route_evaluations = {x.get("id"): x for x in planning.get("meal_route_evaluations") or [] if x.get("id")}
    weather_ids = {x.get("id") for x in planning.get("weather") or [] if x.get("id")}
    for weather in planning.get("weather") or []:
        research_evidence.validate_depth(weather, "weather", depth_enabled)
        if depth_enabled:
            if weather.get("basis_type") not in ("seasonal", "forecast") or not weather.get("risk_summary") or not weather.get("recheck_at"):
                raise ValueError("天气须说明季节/预报依据、风险及复核时间")
            if weather.get("basis_type") == "seasonal" and (weather.get("icon_code") != "unknown" or weather.get("status") not in ("estimated", "to_recheck")):
                raise ValueError("季节天气不能冒充已确认的逐日预报")
            if weather.get("research_depth") == "detailed" and not weather.get("impact_plan"):
                raise ValueError("详细天气研究必须有实际调整方案")
            dates = weather.get("dates") or ([weather["date"]] if weather.get("date") else [])
            if not isinstance(dates, list) or not dates or any(not isinstance(value, str) for value in dates):
                raise ValueError("天气必须声明适用日期")
            for value in dates:
                parsed = iso_date(value, "天气适用日期")
                if parsed.date().isoformat() != value:
                    raise ValueError("天气适用日期必须使用 YYYY-MM-DD")
            if not isinstance(weather.get("source_ids"), list) or not weather["source_ids"] or any(not isinstance(sid, str) for sid in weather["source_ids"]):
                raise ValueError("天气的季节或预报依据必须有实际已读来源")
            if weather.get("basis_type") == "forecast" and len(dates) != 1:
                raise ValueError("单日预报对象只能覆盖一个日期；同地点查询结果可拆成逐日对象复用")
            for sid in weather.get("source_ids") or []:
                if sid not in area_sources:
                    raise ValueError("天气引用了未登记来源")
                research_evidence.validate_source(area_sources[sid])
        if weather.get("icon_code") not in WEATHER_ICONS:
            raise ValueError(f"天气“{weather.get('id') or weather.get('date') or '未命名'}”的 icon_code 无效")
        required_weather = {"summary", "status", "checked_at", "action_links"}
        missing_weather = sorted(field for field in required_weather if not weather.get(field))
        if missing_weather:
            raise ValueError(f"天气“{weather.get('id') or weather.get('date') or '未命名'}”缺少字段：{', '.join(missing_weather)}")
        if not any(action.get("type") == "weather" for action in weather.get("action_links") or []):
            raise ValueError(f"天气“{weather.get('id') or weather.get('date') or '未命名'}”必须提供 weather 类型的查看入口")
    booking_tasks = {x.get("id"): x for x in planning.get("booking_tasks") or [] if x.get("id")}
    allowed_booking_priorities = {"book_now", "book_when_open", "recheck_later", "optional"}
    for task in booking_tasks.values():
        if task.get("priority") not in allowed_booking_priorities:
            raise ValueError(f"预约任务“{task.get('title') or task.get('id')}”的 priority 无效")
        if task.get("priority") in {"book_now", "book_when_open"} and not task.get("action_links"):
            raise ValueError(f"预约任务“{task.get('title') or task.get('id')}”必须提供可执行 action_links")
    if workflow.get("phase") in {"confirmed_planning", "final"}:
        for route in transport_edges:
            map_route = route.get("map_route") or {}
            multi_source = bool(planning.get("research_contract"))
            if multi_source:
                research_evidence.validate_route(route, {item["id"]: item for item in data.get("sources") or []})
            if not multi_source and (not map_route.get("origin") or not map_route.get("destination")):
                raise ValueError(f"交通段“{route.get('id') or '未命名'}”必须提供 map_route 起终点坐标")
            mode = map_route.get("mode")
            if not multi_source and mode not in {"car", "bus", "walk"}:
                raise ValueError(f"交通段“{route.get('id') or '未命名'}”的 map_route.mode 必须是 car、bus 或 walk")
            map_actions = [
                action for action in route.get("action_links") or []
                if action.get("type") == "map" and (multi_source or urlparse(str(action.get("url") or "")).hostname == "uri.amap.com")
            ]
            if not map_actions and not (multi_source and route.get("status") == "estimated" and route.get("action_links")):
                raise ValueError(f"交通段“{route.get('id') or '未命名'}”必须提供可独立打开的高德 URI 路线 action_link")
        daily_routes = planning.get("daily_routes") or []
        route_dates: set[str] = set()
        for route in daily_routes:
            route_date = str(route.get("date") or "")
            if not route_date:
                raise ValueError("每日完整路线缺少 date")
            if route_date in route_dates:
                raise ValueError(f"日期 {route_date} 只能配置一条每日完整路线")
            route_dates.add(route_date)
            stops = route.get("stops") or []
            if len(stops) < 2:
                raise ValueError(f"日期 {route_date} 的每日完整路线至少需要起点和终点")
            for stop_index, stop in enumerate(stops, 1):
                if not stop.get("name"):
                    raise ValueError(f"日期 {route_date} 的第{stop_index}个停靠点缺少名称")
                if stop.get("coordinates") and stop.get("coordinate_system") not in ({"GCJ-02", "WGS84"} if planning.get("research_contract") else {"GCJ-02"}):
                    raise ValueError(f"日期 {route_date} 的第{stop_index}个路线站点必须使用 GCJ-02 坐标")
            if route.get("mode") and route["mode"] not in {"car", "bus", "walk", "mixed"}:
                raise ValueError(f"日期 {route_date} 的停靠顺序 mode 必须是 car、bus、walk 或 mixed")
        if daily_routes:
            missing_dates = [str(day.get("date") or "") for day in days if str(day.get("date") or "") not in route_dates]
            if missing_dates:
                raise ValueError(f"每日完整路线缺少日期：{', '.join(missing_dates)}")
    event_ids: set[str] = set()
    for day in days:
        for event in day.get("events") or []:
            kind = event.get("type")
            title = event.get("title") or "未命名事件"
            if depth_enabled and event.get("weather_sensitive"):
                record = next((item for item in planning.get("weather") or [] if item.get("id") == event.get("weather_id")), {})
                if record.get("research_depth") != "detailed" or day.get("date") not in (record.get("dates") or [record.get("date")]):
                    raise ValueError("天气敏感事件必须引用适用日期的 detailed 天气与调整方案")
            event_id = str(event.get("id") or "")
            if workflow.get("phase") in {"confirmed_planning", "final"}:
                if not event_id:
                    raise ValueError(f"事件“{title}”缺少稳定 id")
                if event_id in event_ids:
                    raise ValueError(f"事件 id 重复：{event_id}")
                event_ids.add(event_id)
            if kind == "attraction" and event.get("attraction_id") not in attraction_ids:
                raise ValueError(f"景点事件“{title}”缺少有效的 attraction_id")
            if kind == "attraction" and workflow.get("phase") in {"confirmed_planning", "final"}:
                attraction = attraction_records[event.get("attraction_id")]
                official = attraction.get("official") or {}
                required_official = {"physical_address", "homepage_url", "notice_url", "checked_at"}
                missing_official = sorted(field for field in required_official if not official.get(field))
                if missing_official:
                    raise ValueError(f"景点“{attraction.get('name') or title}”的 official 缺少字段：{', '.join(missing_official)}")
                for field in ("homepage_url", "notice_url", "booking_url"):
                    value = official.get(field)
                    if value and not str(value).startswith("https://"):
                        raise ValueError(f"景点“{attraction.get('name') or title}”的 official.{field} 必须使用 HTTPS")
                wechat = official.get("wechat")
                if wechat is not None:
                    if not isinstance(wechat, dict):
                        raise ValueError(f"景点“{attraction.get('name') or title}”的 official.wechat 必须是对象")
                    required_wechat = {"account_name", "menu_path", "checked_at"}
                    missing_wechat = sorted(field for field in required_wechat if not wechat.get(field))
                    if missing_wechat:
                        raise ValueError(
                            f"景点“{attraction.get('name') or title}”的 official.wechat 缺少字段："
                            f"{', '.join(missing_wechat)}"
                        )
                    guide_url = str(wechat.get("guide_url") or "")
                    if guide_url:
                        guide_host = (urlparse(guide_url).hostname or "").casefold()
                        if not guide_url.startswith("https://") or guide_host != "mp.weixin.qq.com":
                            raise ValueError(
                                f"景点“{attraction.get('name') or title}”的 official.wechat.guide_url "
                                "必须使用 https://mp.weixin.qq.com"
                            )
                reservation_state = event.get("reservation_required")
                has_booking_guidance = (
                    official.get("booking_status") == "official_channel_listed"
                    and official.get("notice_url")
                )
                has_wechat_guide = bool((wechat or {}).get("guide_url"))
                if reservation_state is True and not (official.get("booking_url") or has_booking_guidance or has_wechat_guide):
                    raise ValueError(
                        f"需要预约的景点“{attraction.get('name') or title}”必须提供 official.booking_url，"
                        "official.wechat.guide_url，或以 official_channel_listed + notice_url 标明官方预约说明"
                    )
                if reservation_state is False and not official.get("booking_url") and official.get("booking_status") != "not_applicable":
                    raise ValueError(f"无需预约的景点“{attraction.get('name') or title}”必须将 official.booking_status 标记为 not_applicable")
                admission = event.get("admission") or {}
                required_admission = {"opening_hours", "last_entry", "reservation_method", "entry_requirement", "notice"}
                missing_admission = sorted(field for field in required_admission if not admission.get(field))
                if missing_admission:
                    raise ValueError(f"景点事件“{title}”的 admission 缺少字段：{', '.join(missing_admission)}")
                if event.get("weather_id") not in weather_ids:
                    raise ValueError(f"景点事件“{title}”必须引用有效的 weather_id")
                if depth_enabled:
                    record = next(item for item in planning.get("weather") or [] if item.get("id") == event["weather_id"])
                    if day.get("date") not in (record.get("dates") or [record.get("date")]):
                        raise ValueError("天气对象不能引用到适用日期之外")
                execution = event.get("execution") or {}
                entry = execution.get("entry") or {}
                exit_point = execution.get("exit") or {}
                if not all(entry.get(key) for key in ("name", "location_query", "reason")):
                    raise ValueError(f"景点事件“{title}”必须提供 execution.entry 的名称、定位词和选择理由")
                if not all(exit_point.get(key) for key in ("name", "location_query")):
                    raise ValueError(f"景点事件“{title}”必须提供 execution.exit 的名称和定位词")
                checkpoints = execution.get("checkpoints") or []
                if not checkpoints or not any(point.get("required") for point in checkpoints):
                    raise ValueError(f"景点事件“{title}”必须提供含必达点的 execution.checkpoints")
                for point_index, point in enumerate(checkpoints):
                    required_point_fields = {"id", "order", "time", "end_time", "kind", "name", "required", "instruction"}
                    missing_point = sorted(field for field in required_point_fields if field not in point or point.get(field) in (None, ""))
                    if missing_point:
                        raise ValueError(f"景点事件“{title}”的 checkpoint[{point_index}] 缺少字段：{', '.join(missing_point)}")
                    if point.get("meal_id") and point.get("meal_id") not in meal_ids:
                        raise ValueError(f"景点事件“{title}”的 checkpoint[{point_index}] 引用了无效 meal_id")
                    if point.get("meal_id"):
                        validate_embedded_meal(
                            str(point.get("name") or f"{title} checkpoint[{point_index}]"),
                            str(point["meal_id"]),
                            str(day.get("date") or ""),
                            planning,
                            meals,
                            restaurants,
                            restaurant_snapshots,
                            meal_route_evaluations,
                        )
                task_ids = event.get("booking_task_ids") or []
                if reservation_state in {True, "to_recheck"} and not task_ids:
                    raise ValueError(f"需要预约的景点事件“{title}”必须提供 booking_task_ids")
                for task_id in task_ids:
                    task = booking_tasks.get(task_id)
                    if not task:
                        raise ValueError(f"景点事件“{title}”引用了无效的 booking_task_id：{task_id}")
                    if task.get("event_id") != event_id or task.get("attraction_id") != event.get("attraction_id"):
                        raise ValueError(f"预约任务“{task_id}”必须同时绑定事件和景点")
                costs = event.get("cost_items") or []
                if not costs or not any(item.get("kind") == "base_ticket" for item in costs):
                    raise ValueError(f"景点事件“{title}”必须在事件内提供基础门票 cost_items")
                required = {"unit_price", "quantity", "subtotal", "pricing_role", "required", "status", "source_ids"}
                for cost_index, item in enumerate(costs):
                    missing = sorted(key for key in required if key not in item)
                    if missing:
                        raise ValueError(f"景点事件“{title}”的 cost_items[{cost_index}] 缺少字段：{', '.join(missing)}")
            if kind == "transport":
                route_id = event.get("route_id")
                if route_id not in route_ids and route_id not in intercity_ids:
                    raise ValueError(f"交通事件“{title}”缺少有效的 route_id")
            if kind == "lodging" and event.get("lodging_id") not in lodging_ids:
                raise ValueError(f"住宿事件“{title}”缺少有效的 lodging_id")
            if kind == "meal":
                meal_id = event.get("meal_id")
                if workflow.get("phase") in {"confirmed_planning", "final"} and meal_id not in meal_ids:
                    raise ValueError(f"餐饮事件“{title}”必须引用有效的 meal_id")
                meal = meals.get(meal_id) or {}
                if meal.get("mode") == "area":
                    research_evidence.validate_area_meal(meal, day.get("date"), area_sources)
                if workflow.get("phase") in {"confirmed_planning", "final"}:
                    if planning.get("restaurant_research_version") == 4:
                        duplicate_fields = sorted(DUPLICATE_MEAL_SUMMARY_FIELDS.intersection(meal))
                        if duplicate_fields:
                            raise ValueError("餐饮事件不得复制候选摘要字段：" + ", ".join(duplicate_fields))
                        # v4 is validated once, including source closure and both route legs.
                        continue
                    if planning.get("restaurant_research_version") != 3:
                        raise ValueError("正式规划必须使用 restaurant_research_version=3 的餐厅候选研究契约")
                    duplicate_fields = sorted(DUPLICATE_MEAL_SUMMARY_FIELDS.intersection(meal))
                    if duplicate_fields:
                        raise ValueError(f"餐饮事件“{title}”不得复制候选摘要字段：{', '.join(duplicate_fields)}")
                    required_meal_fields = {"name", "time_window"}
                    missing = sorted(field for field in required_meal_fields if not meal.get(field))
                    if missing:
                        raise ValueError(f"餐饮事件“{title}”的 meal_option 缺少字段：{', '.join(missing)}")
                    required_research = {
                        "previous_anchor", "next_anchor", "constraints", "candidate_ids",
                        "selected_candidate_id", "fallback_candidate_ids", "candidates",
                        "candidate_policy", "baseline_route_id", "selection_summary", "fallback_rule", "checked_at",
                    }
                    missing_research = sorted(field for field in required_research if field not in meal or meal.get(field) in (None, ""))
                    if missing_research:
                        raise ValueError(f"餐饮事件“{title}”缺少正式候选研究字段：{', '.join(missing_research)}")
                    for anchor_name in ("previous_anchor", "next_anchor"):
                        anchor = meal.get(anchor_name) or {}
                        if not all(anchor.get(field) for field in ("name", "physical_address", "coordinates")):
                            raise ValueError(f"餐饮事件“{title}”的 {anchor_name} 必须提供名称、具体地址和坐标")
                    candidate_ids = [str(value) for value in meal.get("candidate_ids") or []]
                    if len(candidate_ids) != len(set(candidate_ids)):
                        raise ValueError(f"餐饮事件“{title}”的 candidate_ids 不能重复")
                    policy = meal.get("candidate_policy") or {}
                    policy_status = policy.get("status")
                    if policy_status == "normal" and not 2 <= len(candidate_ids) <= 3:
                        raise ValueError(f"餐饮事件“{title}”的正常正餐必须提供2至3个餐厅候选")
                    if policy_status == "constrained":
                        required_policy = {"restriction_reason", "search_scope", "emergency_fallback", "source_ids"}
                        missing_policy = sorted(field for field in required_policy if not policy.get(field))
                        if missing_policy:
                            raise ValueError(f"餐饮事件“{title}”的受限候选声明缺少字段：{', '.join(missing_policy)}")
                    elif policy_status != "normal":
                        raise ValueError(f"餐饮事件“{title}”的 candidate_policy.status 无效")
                    selected = str(meal.get("selected_candidate_id") or "")
                    if selected not in candidate_ids:
                        raise ValueError(f"餐饮事件“{title}”的 selected_candidate_id 必须属于 candidate_ids")
                    fallback_ids = [str(value) for value in meal.get("fallback_candidate_ids") or []]
                    if policy_status == "normal" and not fallback_ids:
                        raise ValueError(f"餐饮事件“{title}”必须提供至少一个结构化备选餐厅")
                    if any(value not in candidate_ids or value == selected for value in fallback_ids):
                        raise ValueError(f"餐饮事件“{title}”的备选必须属于候选且不同于主选")
                    bindings = {str(item.get("restaurant_id") or ""): item for item in meal.get("candidates") or []}
                    if set(bindings) != set(candidate_ids):
                        raise ValueError(f"餐饮事件“{title}”必须为每个 candidate_id 提供快照与路线绑定")
                    ranks = [item.get("rank") for item in meal.get("candidates") or []]
                    if any(not isinstance(rank, int) for rank in ranks) or sorted(ranks) != list(range(1, len(candidate_ids) + 1)):
                        raise ValueError(f"餐饮事件“{title}”的候选 rank 必须从1连续递增且不能重复")
                    if bindings[selected].get("rank") != 1:
                        raise ValueError(f"餐饮事件“{title}”的 selected_candidate_id 必须是 rank=1 的主选")
                    searched_count = policy.get("searched_count")
                    if not isinstance(searched_count, int) or searched_count < len(candidate_ids):
                        raise ValueError(f"餐饮事件“{title}”必须记录不小于有效候选数的 searched_count")
                    for restaurant_id in candidate_ids:
                        restaurant = restaurants.get(restaurant_id)
                        if not restaurant:
                            raise ValueError(f"餐饮事件“{title}”引用了无效餐厅：{restaurant_id}")
                        location = restaurant.get("location") or {}
                        required_restaurant = {"name", "signature_dishes", "media", "action_links", "source_ids"}
                        missing_restaurant = sorted(field for field in required_restaurant if not restaurant.get(field))
                        if missing_restaurant or not all(location.get(field) for field in ("physical_address", "coordinates", "coordinate_system", "poi_id")):
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”缺少准确位置、特色菜、可视证据、链接或来源")
                        if not any(action.get("type") in {"restaurant", "map"} for action in restaurant.get("action_links") or []):
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”必须提供餐厅详情或地图入口")
                        for media_index, media in enumerate(restaurant.get("media") or []):
                            kind_media = media.get("kind")
                            if kind_media == "display_image":
                                needed = {"url", "alt", "source_url", "license_or_permission", "checked_at"}
                                missing_media = sorted(field for field in needed if not media.get(field))
                                if missing_media or not (media.get("author") or media.get("source_label")):
                                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的展示图片[{media_index}]缺少权利或来源信息")
                            elif kind_media == "link_preview":
                                if not all(media.get(field) for field in ("source_url", "alt", "source_label")):
                                    raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的链接预览[{media_index}]缺少来源")
                            else:
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 media.kind 无效")
                        binding = bindings[restaurant_id]
                        snapshot = restaurant_snapshots.get(binding.get("snapshot_id"))
                        if not snapshot or snapshot.get("restaurant_id") != restaurant_id:
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”缺少匹配的动态快照")
                        if snapshot.get("schema_version") != "restaurant-source-snapshot/v1":
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的动态快照版本无效")
                        if not snapshot.get("checked_at") or not snapshot.get("expires_at") or not snapshot.get("source_ids") or not snapshot.get("action_links"):
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的动态快照缺少查询时间、过期时间、来源或复核入口")
                        if snapshot.get("applicable_date") != day.get("date") or snapshot.get("time_window") != meal.get("time_window"):
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的动态快照日期或用餐时段与事件不一致")
                        signals = snapshot.get("platform_signals") or []
                        if not signals:
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”缺少分平台评分信号")
                        for signal in signals:
                            if not signal.get("platform") or not signal.get("checked_at") or not signal.get("source_ids"):
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的平台信号缺少平台、查询时间或来源")
                            if signal.get("status") != "unavailable" and signal.get("rating") is None:
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的可用平台评分必须提供评分展示值")
                            if signal.get("status") == "unavailable" and not signal.get("unavailable_reason"):
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”未取得评分时必须说明原因")
                        community = snapshot.get("community_consensus") or {}
                        if not community.get("checked_at") or "notes_considered" not in community or "recent_note_count" not in community:
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”缺少社区研究结论")
                        notes_considered = int(community.get("notes_considered") or 0)
                        recent_note_count = int(community.get("recent_note_count") or 0)
                        if community.get("status") == "unavailable":
                            if not community.get("unavailable_reason"):
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”社区来源不可用时必须说明原因")
                        else:
                            minimum_notes = 1 if community.get("status") == "partial" else 3 if restaurant_id == selected else 2
                            references = community.get("references") or []
                            if notes_considered < minimum_notes or recent_note_count < minimum_notes or len(references) < minimum_notes:
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”必须提供至少{minimum_notes}条近期社区参考及原帖链接")
                            if any(not ref.get("title") or not str(ref.get("url") or "").startswith("https://") for ref in references):
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的社区参考必须提供标题和 HTTPS 原帖链接")
                        if restaurant_id == selected and policy_status == "normal" and notes_considered < 3 and community.get("status") not in {"unavailable", "partial"}:
                            raise ValueError(f"主选餐厅“{restaurant.get('name') or restaurant_id}”必须交叉至少3条近期社区内容，或明确记录来源不可用")
                        operations = snapshot.get("operations") or {}
                        required_operations = {"opening_hours", "reservation", "queue", "parking", "status", "checked_at", "recheck_at"}
                        if any(not operations.get(field) for field in required_operations):
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”缺少营业、预约、排队、停车或复核时间")
                        evaluation = meal_route_evaluations.get(binding.get("route_evaluation_id"))
                        if not evaluation or evaluation.get("restaurant_id") != restaurant_id or evaluation.get("meal_id") != meal_id:
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”缺少匹配的双腿路线评估")
                        route_numbers = {"total_door_to_door_minutes", "baseline_door_to_door_minutes", "detour_minutes"}
                        if any(evaluation.get(field) is None for field in route_numbers) or not evaluation.get("checked_at") or not evaluation.get("source_ids"):
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的路线评估缺少总耗时、同口径基准、绕行、查询时间或来源")
                        if abs(
                            float(evaluation["total_door_to_door_minutes"])
                            - float(evaluation["baseline_door_to_door_minutes"])
                            - float(evaluation["detour_minutes"])
                        ) > 0.01:
                            raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的额外绕行必须等于候选总耗时减同口径基准耗时")
                        for leg_name in ("from_previous", "to_next"):
                            leg = evaluation.get(leg_name) or {}
                            needed_leg = {"distance_meters", "duration_minutes", "door_to_door_minutes", "map_url"}
                            if any(leg.get(field) is None for field in needed_leg) or not str(leg.get("map_url") or "").startswith("https://"):
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 {leg_name} 路线数据不完整")
                            if any(not (leg.get(endpoint) or {}).get(field) for endpoint in ("origin", "destination") for field in ("name", "physical_address", "coordinates")):
                                raise ValueError(f"餐厅“{restaurant.get('name') or restaurant_id}”的 {leg_name} 端点缺少名称、具体地址或坐标")
                        max_detour = (meal.get("constraints") or {}).get("max_detour_minutes")
                        if isinstance(max_detour, (int, float)) and evaluation.get("detour_minutes") > max_detour:
                            raise ValueError(f"餐厅候选“{restaurant.get('name') or restaurant_id}”超过本餐最大允许绕行，不能保留为主选或备选")


def render_inventory_refs(
    candidate: dict[str, Any] | None,
    snapshots: dict[str, dict[str, Any]],
) -> str:
    if not candidate:
        return ""
    rows = []
    for ref in candidate.get("inventory_refs") or []:
        snapshot = snapshots.get(str(ref.get("snapshot_id") or "")) or {}
        item = next(
            (
                value for value in snapshot.get("items") or []
                if str(value.get("offer_id") or "") == str(ref.get("offer_id") or "")
            ),
            {},
        )
        if not snapshot or not item:
            continue
        provider = snapshot.get("provider") or {}
        freshness = snapshot.get("freshness") or {}
        price = item.get("price") or {}
        price_text = price.get("display")
        if not price_text and price.get("amount") is not None:
            price_text = f'{price.get("currency") or ""} {price.get("amount")}'.strip()
        link = item.get("action_link")
        action = (
            f'<a href="{esc(link)}" target="_blank" rel="noopener noreferrer">查看供应商结果 ↗</a>'
            if link else ""
        )
        rows.append(
            f'''<li><strong>{esc(provider.get("name"))} · {esc(item.get("name"))}</strong>
            <span>{esc(price_text or "本次未返回可解析价格")} · 查询于 {esc(freshness.get("checked_at"))}</span>
            <small>有效至 {esc(freshness.get("expires_at"))} · 下单前重新核验</small>{action}</li>'''
        )
    if not rows:
        return ""
    return f'<details class="inventory-proof"><summary>报价与核验依据</summary><ul>{"".join(rows)}</ul></details>'


def overview_fact(label: str, value: Any) -> str:
    """Render one compact, labeled fact for the overview table."""
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        value = "、".join(str(item) for item in value if item)
    if not value:
        return ""
    return f'<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>'


def overview_location(
    event: dict[str, Any],
    route: dict[str, Any] | None,
    meal: dict[str, Any] | None,
    restaurants: dict[str, dict[str, Any]],
    lodging: dict[str, Any] | None,
) -> tuple[str, str]:
    """Choose the concrete place a traveler needs to recognize at a glance."""
    kind = str(event.get("type") or "note")
    if kind == "food_walk":
        return str(event.get("title") or "逛吃"), str(((event.get("food_walk") or {}).get("entry") or {}).get("physical_address") or "")
    if kind == "transport" and route:
        return (
            f'{route.get("from") or "起点"} → {route.get("to") or "终点"}',
            str(route.get("departure_station") or route.get("arrival_station") or ""),
        )
    if kind == "meal" and meal:
        if meal.get("mode") == "area":
            area = meal.get("area_dining") or {}
            return str(area.get("name") or meal.get("name")), str(area.get("physical_address") or "")
        restaurant = restaurants.get(str(meal.get("selected_candidate_id") or "")) or {}
        return (
            str(restaurant.get("name") or event.get("title") or "用餐点"),
            str((restaurant.get("location") or {}).get("physical_address") or event.get("subtitle") or ""),
        )
    if kind == "lodging" and lodging:
        location = lodging.get("location") or {}
        return (
            str(lodging.get("name") or lodging.get("hotel_name") or event.get("title") or "住宿"),
            str(location.get("physical_address") or lodging.get("area") or event.get("area") or ""),
        )
    return (
        str(event.get("title") or "未命名安排"),
        str(event.get("area") or event.get("subtitle") or ""),
    )


def render_overview_event(
    event: dict[str, Any],
    attraction: dict[str, Any] | None,
    route: dict[str, Any] | None,
    meal: dict[str, Any] | None,
    restaurants: dict[str, dict[str, Any]],
    restaurant_snapshots: dict[str, dict[str, Any]],
    meal_route_evaluations: dict[str, dict[str, Any]],
    lodging: dict[str, Any] | None,
) -> str:
    """Render a dense three-column row whose facts adapt to the event type."""
    kind = str(event.get("type") or "note")
    label, _ = TYPES.get(kind, TYPES["note"])
    location, address = overview_location(event, route, meal, restaurants, lodging)
    facts: list[str] = []
    if kind == "attraction":
        execution = event.get("execution") or {}
        checkpoints = execution.get("checkpoints") or []
        checkpoint_text = " → ".join(
            f'{point.get("time")} {point.get("name")}' if point.get("time") else str(point.get("name") or "")
            for point in checkpoints if point.get("name")
        )
        admission = event.get("admission") or {}
        facts.extend([
            overview_fact("特色", (attraction or {}).get("seasonal_highlights") or event.get("subtitle")),
            overview_fact("游览", checkpoint_text),
            overview_fact("开放", " · ".join(str(value) for value in [admission.get("opening_hours"), f'停止入场 {admission.get("last_entry")}' if admission.get("last_entry") else ""] if value)),
            overview_fact("入口", (execution.get("entry") or {}).get("name") or event.get("entrance")),
            overview_fact("离开", " · ".join(str(value) for value in [(execution.get("exit") or {}).get("name"), f'最晚 {execution.get("leave_by")}' if execution.get("leave_by") else ""] if value)),
            overview_fact("预约", admission.get("reservation_method") or event.get("reservation")),
            overview_fact("费用", event.get("cost_summary") or event.get("cost")),
            overview_fact("备选", execution.get("fallback")),
        ])
    elif kind == "transport" and route:
        facts.extend([
            overview_fact("方式", transport_mode_display_name(route.get("mode") or event.get("transport_mode"))),
            overview_fact("线路", route.get("route") or route.get("service_or_train")),
            overview_fact("门到门", route.get("door_to_door_duration") or event.get("duration")),
            overview_fact("费用", route.get("cost") or route.get("fare") or event.get("cost_summary") or event.get("cost")),
            overview_fact("备选", route.get("fallback")),
        ])
    elif kind == "meal" and meal and meal.get("mode") == "area":
        facts.extend(overview_fact(label, value) for label, value in area_meal_facts(meal))
    elif kind == "meal" and meal:
        selected_id = str(meal.get("selected_candidate_id") or "")
        restaurant = restaurants.get(selected_id) or {}
        candidate = next(
            (item for item in meal.get("candidates") or [] if str(item.get("restaurant_id") or "") == selected_id),
            {},
        )
        snapshot = restaurant_snapshots.get(str(candidate.get("snapshot_id") or "")) or {}
        evaluation = meal_route_evaluations.get(str(candidate.get("route_evaluation_id") or "")) or {}
        per_person = next(
            (signal.get("per_person") for signal in snapshot.get("platform_signals") or [] if signal.get("per_person")),
            None,
        )
        facts.extend([
            overview_fact("特色", restaurant.get("signature_dishes")),
            overview_fact("菜系", restaurant.get("cuisine")),
            overview_fact("人均", per_person),
            overview_fact("营业", (snapshot.get("operations") or {}).get("opening_hours")),
            overview_fact("绕行", f'约 {evaluation.get("detour_minutes")} 分钟' if evaluation.get("detour_minutes") is not None else None),
        ])
    elif kind == "lodging" and lodging and lodging.get("kind") == "area":
        facts.extend(overview_fact(label, value) for label, value in lodging_area_facts(lodging))
    elif kind == "lodging" and lodging:
        occupancy = lodging.get("requested_occupancy") or {}
        occupancy_text = " · ".join(
            str(value) for value in [
                f'{occupancy.get("adults")}人' if occupancy.get("adults") else "",
                f'{occupancy.get("rooms")}间' if occupancy.get("rooms") else "",
                occupancy.get("bed_type") or lodging.get("bed_type"),
                f'{lodging.get("nights")}晚' if lodging.get("nights") else "",
            ] if value
        )
        facts.extend([
            overview_fact("入住", occupancy_text or event.get("duration")),
            overview_fact("价格", lodging.get("price") or event.get("cost_summary") or event.get("cost")),
            overview_fact("行李", lodging.get("luggage_storage")),
            overview_fact("下一站", lodging.get("next_stop_duration")),
        ])
    else:
        facts.extend([
            overview_fact("安排", event.get("subtitle")),
            overview_fact("时长", event.get("duration")),
            overview_fact("要点", event.get("details")),
            overview_fact("提醒", event.get("tips")),
            overview_fact("费用", event.get("cost_summary") or event.get("cost")),
        ])
    time_text = f'<strong>{esc(event.get("time") or "—")}</strong>'
    if event.get("end_time"):
        time_text += f'<span>{esc(event.get("end_time"))}</span>'
    return f'''<tr class="overview-event overview-{esc(kind)}">
      <td class="overview-time">{time_text}</td>
      <td class="overview-place"><span class="overview-type">{esc(label)}</span><strong>{esc(location)}</strong>{f'<small>{esc(address)}</small>' if address else ''}</td>
      <td><dl class="overview-facts">{"".join(facts)}</dl></td>
    </tr>'''


def render_overview(
    days: list[dict[str, Any]],
    attractions: dict[str, dict[str, Any]],
    routes: dict[str, dict[str, Any]],
    meals: dict[str, dict[str, Any]],
    restaurants: dict[str, dict[str, Any]],
    restaurant_snapshots: dict[str, dict[str, Any]],
    meal_route_evaluations: dict[str, dict[str, Any]],
    lodgings: dict[str, dict[str, Any]],
) -> str:
    """Render the secondary, print-friendly overview of the full itinerary."""
    bodies = []
    for index, day in enumerate(days):
        heading_id = f"overview-day-{index}"
        day_meta = " · ".join(
            str(value) for value in [day.get("label") or f"D{index + 1}", day.get("date"), day.get("title") or day.get("theme")] if value
        )
        summary = " · ".join(str(value) for value in [day.get("summary"), day.get("weather"), day.get("cost_summary")] if value)
        rows = []
        for event in day.get("events") or []:
            rows.append(render_overview_event(
                event,
                attractions.get(str(event.get("attraction_id") or "")),
                routes.get(str(event.get("route_id") or "")),
                meals.get(str(event.get("meal_id") or "")),
                restaurants,
                restaurant_snapshots,
                meal_route_evaluations,
                lodgings.get(str(event.get("lodging_id") or "")),
            ))
        bodies.append(f'''<tbody aria-labelledby="{heading_id}">
          <tr class="overview-day-row"><th id="{heading_id}" colspan="3"><strong>{esc(day_meta)}</strong>{f'<span>{esc(summary)}</span>' if summary else ''}</th></tr>
          {''.join(rows) if rows else '<tr><td colspan="3" class="overview-empty">这一天还没有安排。</td></tr>'}
        </tbody>''')
    return f'''<section class="itinerary-overview" id="overview-view" data-itinerary-view="overview" aria-labelledby="overview-heading">
      <header class="overview-heading"><span class="eyebrow">紧凑版</span><h2 id="overview-heading">行程一览</h2><p>按时间、地点与执行要点汇总；左右滑动可查看完整表格。</p></header>
      <div class="overview-table-wrap" role="region" aria-label="行程一览表" tabindex="0"><table class="overview-table">
        <colgroup><col class="overview-time-col"><col class="overview-place-col"><col></colgroup>
        <thead><tr><th scope="col">时间</th><th scope="col">地点</th><th scope="col">关键信息</th></tr></thead>{''.join(bodies)}
      </table></div>
    </section>'''


def render_route_overview(
    days: list[dict[str, Any]],
    daily_routes: list[dict[str, Any]],
) -> str:
    """Show ordered stops without implying one navigation mode for the day."""
    routes_by_date = {
        str(route["date"]): route for route in daily_routes
        if route.get("date") and len(route.get("stops") or []) >= 2
    }
    day_sections = []
    for index, day in enumerate(days):
        route = routes_by_date.get(str(day.get("date") or ""))
        stops = (route or {}).get("stops") or []
        stop_items = []
        for number, stop in enumerate(stops, 1):
            arrival = stop.get("arrival_mode") if number > 1 else ""
            arrival_html = f'<span class="route-stop-mode">到达方式：{esc(arrival)}</span>' if arrival else ""
            note_html = f'<small>{esc(stop.get("note"))}</small>' if stop.get("note") else ""
            stop_items.append(
                f'<li><span class="route-stop-number">{number}</span><div><strong>{esc(stop.get("name") or f"第{number}站")}</strong>'
                f'{arrival_html}{note_html}'
                '</div></li>'
            )
        route_html = (
            f'<section class="route-sequence"><h4>{esc(route.get("title") or "当天停靠顺序")}</h4>'
            f'<ol class="route-stop-list" aria-label="当天停靠顺序">{"".join(stop_items)}</ol>'
            f'<p>{esc(route.get("assumption") or "各段交通方式以详细行程和现场指引为准。")}</p></section>'
            if route else ""
        )
        heading_id = f"route-day-{index}-heading"
        day_meta = " · ".join(
            str(value) for value in [day.get("label") or f"D{index + 1}", day.get("date")] if value
        )
        stop_count = len(stops)
        count_text = f"{stop_count} 个停靠点" if route_html else "暂无停靠点"
        day_sections.append(f'''<section class="route-day" aria-labelledby="{heading_id}">
          <header class="route-day-heading"><div><span class="eyebrow">{esc(day_meta)}</span><h3 id="{heading_id}">{esc(day.get("title") or day.get("theme") or "当日路线")}</h3></div><span>{esc(count_text)}</span></header>
          {route_html if route_html else '<p class="route-day-empty">当天没有可展示的停靠顺序。</p>'}
        </section>''')
    return f'''<section class="itinerary-routes" id="route-view" data-itinerary-view="routes" aria-labelledby="route-heading">
      <header class="route-view-heading"><span class="eyebrow">按天查看</span><h2 id="route-heading">停靠顺序</h2><p>按当天执行顺序查看地点；导航时从详细行程打开单段目的地。</p></header>
      {''.join(day_sections) if day_sections else '<p class="route-day-empty">当前行程还没有可展示的停靠顺序。</p>'}
    </section>'''


def day_highlights(
    day: dict[str, Any],
    meals: dict[str, dict[str, Any]],
    restaurants: dict[str, dict[str, Any]],
    routes: dict[str, dict[str, Any]],
) -> list[str]:
    stops = []
    for event in day.get("events") or []:
        if is_routine_event(event):
            continue
        kind = event.get("type")
        if kind == "transport":
            route = routes.get(str(event.get("route_id") or "")) or {}
            if str(route.get("mode") or "").lower() not in {"flight", "train", "rail"}:
                continue
        if kind == "note":
            continue
        title = str(event.get("title") or "")
        if kind == "meal":
            meal = meals.get(str(event.get("meal_id") or "")) or {}
            restaurant = restaurants.get(str(meal.get("selected_candidate_id") or "")) or {}
            title = restaurant.get("name") or title
        if title and (not stops or stops[-1] != title):
            stops.append(title)
    return stops


def day_focus_items(
    day: dict[str, Any],
    meals: dict[str, dict[str, Any]],
    restaurants: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Show actual experiences and food choices before routine itinerary steps."""
    experiences: list[str] = []
    food: list[str] = []
    for event in day.get("events") or []:
        kind = event.get("type")
        title = str(event.get("title") or "").strip()
        if kind == "attraction" and title:
            experiences.append(title)
        elif kind == "rest" and title and re.search(r"散步|漫步|慢逛|逛街|徒步|公园|喝茶|骑行|游览|博物馆|展览", title):
            experiences.append(re.sub(r"^睡到自然醒[，,]\s*", "", title))
        elif kind == "meal":
            meal = meals.get(str(event.get("meal_id") or "")) or {}
            restaurant = restaurants.get(str(meal.get("selected_candidate_id") or "")) or {}
            name = str(restaurant.get("name") or title).strip()
            meal_type = str(meal.get("meal_type") or "").strip()
            if name:
                food.append(f"{meal_type}：{name}" if meal_type and not name.startswith(meal_type) else name)
        elif kind == "food_walk":
            walk = event.get("food_walk") or {}
            for item in walk.get("food_items") or []:
                name = str(item.get("name") or "").strip()
                if name:
                    food.append(name)
        elif kind == "note" and title and re.search(r"早餐|午餐|晚餐|简餐|小吃|用餐|吃饭|餐厅", title):
            food.append(title)

    if not food:
        for event in day.get("events") or []:
            for item in event.get("cost_items") or []:
                name = str(item.get("name") or "").replace("预算估算", "").strip()
                if item.get("kind") == "meal" and re.search(r"早餐|午餐|晚餐|简餐|小吃|用餐", name):
                    food.append(name)

    if not experiences:
        transports = [str(event.get("title") or "") for event in day.get("events") or [] if event.get("type") == "transport"]
        if any("返" in title for title in transports):
            experiences.append("返程日，留足路上休息")
        elif transports:
            experiences.append("抵达日，不安排景点")
        else:
            experiences.append("当天无固定景点，按体力自由安排")
    return list(dict.fromkeys(experiences)), list(dict.fromkeys(food))


def render_handbook_overview(
    days: list[dict[str, Any]],
    booking_tasks: dict[str, dict[str, Any]],
    full_overview: str,
    route_overview: str,
    notebook_route: str = "",
) -> str:
    summaries = []
    for index, day in enumerate(days):
        events = day.get("events") or []
        tasks = [booking_tasks[task_id] for event in events for task_id in event.get("booking_task_ids") or [] if task_id in booking_tasks]
        reminder = "；".join(str(task.get("title") or "预约需提前办理") for task in tasks)
        timing = "–".join(str(value) for value in [events[0].get("time") if events else None, (events[-1].get("end_time") or events[-1].get("time")) if events else None] if value)
        summaries.append(f'''<article class="handbook-day">
          <span class="handbook-day-number" aria-hidden="true">{index + 1:02d}</span><div class="handbook-day-content">
          <div class="handbook-day-meta"><span class="eyebrow">{esc(day.get("label") or f"D{index + 1}")} · {esc(day.get("date"))}</span><small>{esc(timing)}</small></div>
          <h3>{esc(day.get("title") or day.get("theme") or "当日安排")}</h3>
          {f'<p>{esc(day.get("summary"))}</p>' if day.get("summary") else ''}
          {f'<p class="day-reminder"><b>提前准备</b>{esc(reminder)}</p>' if reminder else ''}
          <a class="day-open-link" href="#day-{index}" data-day-target="day-{index}">查看当天安排 ↗</a>
          <label class="day-open-fallback" for="itinerary-view-detail">查看每日行程 ↗</label></div>
        </article>''')
    full_overview = full_overview.replace('id="overview-view" data-itinerary-view="overview"', 'id="full-overview"')
    route_overview = route_overview.replace(' data-itinerary-view="routes"', '')
    return f'''<section class="handbook-overview" id="overview-view" data-itinerary-view="overview" aria-labelledby="handbook-heading">
      <header class="overview-heading"><span class="chapter-number">03 / THE ROUTE</span><h2 id="handbook-heading">把旅程摊开看</h2><p>先看每天的主题与节奏，再决定从哪天读起。</p></header>
      <div class="notebook-section">{notebook_route}</div>
      <div class="handbook-days">{"".join(summaries)}</div>
      <a class="preparation-shortcut day-open-link" href="#prepare-view" data-open-view="prepare">查看订票、住宿与预约准备 →</a>
      <label class="preparation-shortcut day-open-fallback" for="itinerary-view-prepare">查看订票、住宿与预约准备 →</label>
      <details class="reference-details"><summary>查看完整行程表</summary>{full_overview}</details>
      <details class="reference-details"><summary>查看完整停靠顺序</summary>{route_overview}</details>
    </section>'''


def visual_for_event(event: dict[str, Any], visuals: dict[str, Any]) -> dict[str, Any]:
    assets = visuals.get("experiences") or {}
    return assets.get(str(event.get("id") or "")) or assets.get(str(event.get("title") or "")) or first_event_visual([{"events": [event]}])


def visual_for_day(day: dict[str, Any], visuals: dict[str, Any]) -> dict[str, Any]:
    asset = (visuals.get("days") or {}).get(str(day.get("date") or ""))
    if asset:
        return asset
    for event in day.get("events") or []:
        asset = visual_for_event(event, visuals)
        if asset:
            return asset
    return {}


def notebook_day_link(index: int, text: str = "翻到这一天") -> str:
    return f'<a class="notebook-day-link day-open-link" href="#day-{index}" data-day-target="day-{index}">{esc(text)} <span aria-hidden="true">↗</span></a><label class="day-open-fallback notebook-day-link" for="itinerary-view-detail">查看每日行程 ↗</label>'


def render_notebook_route(days: list[dict[str, Any]], visuals: dict[str, Any], meals: dict[str, Any], restaurants: dict[str, Any]) -> str:
    tickets = []
    for index, day in enumerate(days):
        experiences, _ = day_focus_items(day, meals, restaurants)
        # Use original titles and summaries; no invented route, transport or pace.
        title = day.get("title") or day.get("theme") or f"第 {index + 1} 天"
        summary = day.get("summary") or " · ".join(experiences)
        media = render_visual(visual_for_day(day, visuals), "route-scenery", source_link=False)
        tickets.append(f'<li><a class="route-ticket day-open-link{ "" if media else " text-only"}" href="#day-{index}" data-day-target="day-{index}"><span class="route-day">D{index + 1}</span><div><small>{esc(day.get("date"))}</small><h3>{esc(title)}</h3><p>{esc(summary)}</p></div>{media}<span class="ticket-arrow" aria-hidden="true">↗</span></a><label class="day-open-fallback" for="itinerary-view-detail">查看每日行程 ↗</label></li>')
    columns = min(5, max(1, len(days)))
    return f'<ol class="notebook-route{ " route-many" if len(days) > 5 else ""}" style="--route-columns:{columns}" aria-label="{len(days)}日行程示意">{"".join(tickets)}</ol><div class="route-footnote"><span>行程顺序示意，非导航地图</span><span>点一天，查看具体安排</span></div>'


def render_day_postcard(day: dict[str, Any], index: int, visuals: dict[str, Any], highlights: list[str], booking_tasks: dict[str, Any]) -> str:
    media = render_visual(visual_for_day(day, visuals), "day-postcard-media")
    # Without an illustration keep the original day heading instead of duplicating it.
    if not media:
        return ""
    tasks = [booking_tasks[task_id] for event in day.get("events") or [] for task_id in event.get("booking_task_ids") or [] if task_id in booking_tasks]
    reminder = "；".join(str(task.get("title") or "") for task in tasks if task.get("title"))
    return f'<aside class="day-postcard" aria-label="D{index + 1} 每日旅行摘要">{media}<div class="day-postcard-copy"><span class="notebook-kicker">D{index + 1} / 今日手账</span><h3>{esc(day.get("title") or day.get("theme") or "今日安排")}</h3><p>{esc(day.get("summary") or " → ".join(highlights))}</p>{f"<p class=\"day-important\">出发前：{esc(reminder)}</p>" if reminder else ""}</div></aside>'


def render_trip_focus(
    days: list[dict[str, Any]],
    meals: dict[str, dict[str, Any]],
    restaurants: dict[str, dict[str, Any]],
    planning: dict[str, Any] | None = None,
    visuals: dict[str, Any] | None = None,
) -> str:
    visuals, planning = visuals or {}, planning or {}
    experiences: list[tuple[int, dict[str, Any]]] = []
    secondary: list[tuple[int, dict[str, Any]]] = []
    foods: dict[str, tuple[int | None, str]] = {}
    for index, day in enumerate(days):
        chosen = []
        day_experiences, _ = day_focus_items(day, meals, restaurants)
        for event in day.get("events") or []:
            if event.get("type") == "attraction" or event.get("title") in day_experiences:
                chosen.append((index, event))
            if event.get("type") == "food_walk":
                for item in (event.get("food_walk") or {}).get("food_items") or []:
                    name = str(item.get("name") or "").strip()
                    if name:
                        foods.setdefault(name, (index, str(item.get("note") or item.get("summary") or "")))
        if chosen:
            experiences.append(chosen[0])
            secondary.extend(chosen[1:])
    for item in planning.get("food_highlights") or []:
        name = str(item.get("name") or "").strip()
        if name:
            # Preserve parentheses containing candidate status and source limitations.
            day_index = None
            for index, day in enumerate(days):
                for event in day.get("events") or []:
                    meal = meals.get(str(event.get("meal_id") or "")) or {}
                    restaurant = restaurants.get(str(meal.get("selected_candidate_id") or "")) or {}
                    restaurant_name = str(restaurant.get("name") or "").strip()
                    if restaurant_name and re.split(r"[（(]", name)[0].strip() == re.split(r"[（(]", restaurant_name)[0].strip():
                        day_index = index
                        break
                if day_index is not None:
                    break
            foods.setdefault(name, (day_index, str(item.get("summary") or "")))
    if not foods:
        for index, day in enumerate(days):
            for name in day_focus_items(day, meals, restaurants)[1]:
                foods.setdefault(name, (index, ""))
    stories = []
    seen = set()
    attractions = {str(item.get("id")): item for item in planning.get("attractions") or []}
    for index, event in experiences + secondary:
        identity = event.get("attraction_id") or event.get("id") or event.get("title")
        if identity in seen:
            continue
        seen.add(identity)
        media = render_visual(visual_for_event(event, visuals), "story-media")
        attraction = attractions.get(str(event.get("attraction_id") or "")) or {}
        summary = event.get("subtitle") or attraction.get("summary") or ""
        if event.get("type") != "attraction":
            summary = summary or (event.get("details") or [""])[0]
        tips = event.get("tips") or [] if event.get("type") != "attraction" else []
        note = str(tips[0]) if tips else ""
        if event.get("type") == "attraction":
            checkpoints = (event.get("execution") or {}).get("checkpoints") or []
            names = [str(point["name"]) for point in checkpoints if point.get("name") and point.get("kind") not in {"entry", "exit"}]
            summary = " → ".join(names) or summary
            note = str(checkpoints[0].get("instruction") or "") if checkpoints else ""
        stories.append(f'<article class="notebook-story{ "" if media else " text-only"}">{media}<div class="story-copy"><span class="story-number">{len(stories) + 1:02d} / D{index + 1}</span><h3>{esc(event.get("title"))}</h3>{f"<p>{esc(summary)}</p>" if summary else ""}{f"<div class=\"story-note\">{esc(note)}</div>" if note else ""}{notebook_day_link(index)}</div></article>')
        if len(stories) == 3:
            break
    stickers = []
    food_assets = visuals.get("food_items") or {}
    ordered_foods = sorted(foods.items(), key=lambda item: not bool(food_assets.get(item[0])))
    primary_foods, remaining_foods = ordered_foods[:4], ordered_foods[4:]
    for name, (index, note) in primary_foods:
        media = render_visual((visuals.get("food_items") or {}).get(name) or {}, "sticker-picture")
        link = notebook_day_link(index, "查看吃法与地点") if index is not None else '<a class="notebook-day-link day-open-link" href="#detail-view" data-open-view="detail">查看每日用餐 ↗</a><label class="day-open-fallback notebook-day-link" for="itinerary-view-detail">查看每日用餐 ↗</label>'
        stickers.append(f'<article class="food-sticker">{media}<span class="sticker-day">{f"D{index + 1} · " if index is not None else ""}行程餐饮线索</span><h3>{esc(name)}</h3>{f"<p>{esc(note)}</p>" if note else ""}{link}</article>')
    food_media = render_visual(visuals.get("food") or {}, "food-collection-media") if stickers else ""
    more_food = '<details class="food-more"><summary>更多餐饮线索</summary><ul>' + "".join(f'<li><strong>{esc(name)}</strong>{f"<p>{esc(note)}</p>" if note else ""}{notebook_day_link(index, "查看当天用餐") if index is not None else ""}</li>' for name, (index, note) in remaining_foods) + '</ul></details>' if remaining_foods else ""
    return f'''<section id="focus-view" data-itinerary-view="focus" aria-labelledby="focus-heading">
      <section class="notebook-section"><header class="notebook-section-head"><div><span class="notebook-kicker">01 / 把旅程摊开</span><h2 id="focus-heading">{len(days)}天，一页页走过</h2><p>先看看每天的主题，再翻到当天查看时间、地点与准备。</p></div></header>{render_notebook_route(days, visuals, meals, restaurants)}</section>
      <section class="notebook-section" aria-labelledby="notebook-experiences"><header class="notebook-section-head"><div><span class="notebook-kicker">02 / 值得期待的体验</span><h2 id="notebook-experiences">这趟旅行，想这样度过</h2></div></header><div class="notebook-stories">{"".join(stories) or '<p>游玩安排可在每日行程查看。</p>'}</div></section>
      <section class="notebook-section notebook-food" aria-labelledby="food-heading"><header class="notebook-section-head"><div><span class="notebook-kicker">03 / 地方风味贴纸册</span><h2 id="food-heading">把当地的味道，夹进手账</h2><p>候选与待确认事项沿用原行程记录，具体安排见对应日期。</p></div></header>{food_media}<div class="food-stickers">{"".join(stickers) or '<p>当前未列出餐饮线索，需补充用餐安排。</p>'}</div>{more_food}</section>
      <div class="notebook-closing"><p>把需要确认的事，提前办好。</p><a class="day-open-link" href="#prepare-view" data-open-view="prepare">翻到出发准备 ↗</a><label class="day-open-fallback" for="itinerary-view-prepare">查看出发准备 ↗</label></div>
    </section>'''


def preparation_sort_key(task: dict[str, Any]) -> tuple[str, int]:
    value = str(task.get("next_action_at") or task.get("sales_open_at") or task.get("deadline") or "")
    iso = re.search(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?", value)
    month_days = re.findall(r"(\d{1,2})月(\d{1,2})日", value)
    target = str(task.get("target_date") or "")
    if iso:
        when = iso.group()
    elif month_days and re.match(r"\d{4}-", target):
        when = min(f"{target[:4]}-{int(month):02d}-{int(day):02d}" for month, day in month_days)
    else:
        when = target or "9999"
    return (when, 0 if task.get("priority") == "book_now" else 1)


def traveler_time(value: Any) -> str:
    text = str(value or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", text):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return text


def render_preparation(planning: dict[str, Any]) -> str:
    tasks = []
    for task in sorted(planning.get("booking_tasks") or [], key=preparation_sort_key):
        timing = render_facts([
            ("何时办理", traveler_time(task.get("next_action_at") or task.get("sales_open_at"))),
            ("办理期限", traveler_time(task.get("deadline"))),
            ("使用日期", task.get("target_date")),
            ("预约时段", task.get("target_session")),
            ("放票规则", task.get("release_rule")),
        ])
        tasks.append(f'''<li><div class="preparation-title"><h3>{esc(task.get("title") or "预约与购票")}</h3><span>{esc(traveler_status(task.get("status")))}</span></div>
          {timing}{f'<p>{esc(task.get("action"))}</p>' if task.get("action") else ''}{render_actions(task.get("action_links") or [])}</li>''')
    categories = {"transport": "交通订票", "booking": "景点预约与复核", "lodging": "住宿确认", "weather": "天气与穿着", "travelers": "同行与饮食", "emergency": "应急联系", "documents": "证件准备", "luggage": "行李安排"}
    readiness = []
    for item in sorted(planning.get("readiness") or [], key=preparation_sort_key):
        readiness.append(f'''<li><div class="preparation-title"><h3>{esc(categories.get(str(item.get("category")), "出行检查"))}</h3><span>{esc(traveler_status(item.get("status")))}</span></div>
          {f'<p class="preparation-deadline">处理时间：{esc(traveler_time(item.get("deadline")))}</p>' if item.get("deadline") else ''}
          <p>{esc(item.get("summary"))}</p>{render_actions(item.get("action_links") or [])}</li>''')
    weather_notes = []
    for weather in planning.get("weather") or []:
        if weather.get("research_depth"):
            facts = render_facts([("适用日期", "、".join(weather.get("dates") or [weather.get("date") or ""])),
                ("依据", "季节资料，非逐日预报" if weather.get("basis_type") == "seasonal" else "日期预报"),
                ("风险", weather.get("risk_summary")), ("调整方案", weather.get("impact_plan")),
                ("复核时间", weather.get("recheck_at"))])
            weather_notes.append(f'<li><h3>{esc(weather.get("location") or "天气与活动")}</h3>{facts}{render_actions(weather.get("action_links") or [])}</li>')
    return f'''<section class="itinerary-prepare" id="prepare-view" data-itinerary-view="prepare" aria-labelledby="prepare-heading">
      <header class="overview-heading"><span class="chapter-number">05 / BEFORE YOU GO</span><h2 id="prepare-heading">出发前，逐项确认</h2><p>先办有期限的事，再核对交通、住宿和当天条件。这里显示规划时记录的状态，请以实际办理结果为准。</p></header>
      {f'<h3 class="preparation-section-title">预约与购票</h3><ul class="preparation-list">{"".join(tasks)}</ul>' if tasks else '<p>当前没有单独列出的预约任务；仍需核对每日行程中的入场要求。</p>'}
      {f'<h3 class="preparation-section-title">交通、住宿与其他检查</h3><ul class="preparation-list">{"".join(readiness)}</ul>' if readiness else '<p>当前没有额外的行前检查项。</p>'}
      {f'<h3 class="preparation-section-title">天气与活动调整</h3><ul class="preparation-list">{"".join(weather_notes)}</ul>' if weather_notes else ''}
    </section>'''


def area_meal_facts(meal: dict[str, Any]) -> list[tuple[str, Any]]:
    area = meal.get("area_dining") or {}
    return [
        ("用餐区域", area.get("name")), ("位置", area.get("physical_address")),
        ("吃法", "、".join(area.get("food_options") or [])),
        ("预算预留", f'人均约 {area.get("budget_per_person")} 元（区域安排，未选具体门店）'),
        ("适配", area.get("why_here")), ("供给依据", area.get("availability_basis")),
        ("交通估算", area.get("route_note")),
        ("用餐备选", dining_fallback_text(area.get("fallback"))),
    ]


def lodging_area_facts(lodging: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("住宿区域", lodging.get("area")),
        ("大概位置", (lodging.get("location") or {}).get("address")),
        ("住宿安排", f'{lodging.get("check_in")} 至 {lodging.get("check_out")} · {lodging.get("nights")}晚'),
        ("选址理由", lodging.get("why_here")),
        ("预算预留", research_evidence.lodging_budget_summary(lodging)),
        ("选店条件", "；".join(lodging.get("selection_notes") or [])),
        ("交通估算", lodging.get("route_note")),
    ]


def render_event(
    event: dict[str, Any],
    idx: int,
    attraction: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    meal: dict[str, Any] | None = None,
    booking_tasks: list[dict[str, Any]] | None = None,
    weather: dict[str, Any] | None = None,
    meals: dict[str, dict[str, Any]] | None = None,
    restaurants: dict[str, dict[str, Any]] | None = None,
    restaurant_snapshots: dict[str, dict[str, Any]] | None = None,
    meal_route_evaluations: dict[str, dict[str, Any]] | None = None,
    lodging: dict[str, Any] | None = None,
    source_snapshots: dict[str, dict[str, Any]] | None = None,
) -> str:
    kind = event.get("type", "note")
    research_evidence.validate_event_prose(event)
    label, icon = TYPES.get(kind, TYPES["note"])
    images = [image for image in event.get("images") or [] if image.get("url")]
    image_html = render_event_images(images[:3], str(event.get("title") or "景点实景"))
    map_html = f'<a class="map-link" href="{esc(event["map_url"])}" target="_blank" rel="noopener noreferrer">打开地图 ↗</a>' if event.get("map_url") and kind != "transport" else ""
    weather_html = render_weather_badge(weather)
    merged_actions = (
        []
        if kind == "meal"
        else merge_actions(event.get("action_links") or [], (attraction or {}).get("action_links") or [], (route or {}).get("action_links") or [], (lodging or {}).get("action_links") or [])
    )
    if kind == "food_walk":
        walk = event.get("food_walk") or {}
        merged_actions = merge_actions(merged_actions, [
            {"type": "map", "label": f"{label} · {point.get('name')}", "url": point.get("map_url")}
            for label, point in (("进入街区", walk.get("entry") or {}), ("离开街区", walk.get("exit") or {}))
            if point.get("map_url")
        ])
    route_handoff_html = render_route_handoff(route) if kind == "transport" and route else ""
    if route_handoff_html:
        merged_actions = [action for action in merged_actions if action.get("type") != "map"]
    actions_html = render_actions(merged_actions)
    subtitle = (
        f'<p class="subtitle">{esc(event.get("subtitle"))}</p>'
        if event.get("subtitle") and event.get("type") != "meal" else ""
    )
    # Attraction execution guidance belongs to checkpoints; rendering legacy
    # event-level notes here would duplicate the card's actionable timeline.
    details = "" if kind == "attraction" else render_list([*(food_walk_lines(event) if kind == "food_walk" else []), *(event.get("details") or [])])
    tips = "" if kind == "attraction" else render_list(event.get("tips") or [], "tip-list")
    context_html = ""
    if kind == "attraction":
        context_html = render_facts([
            ("建议游览时长", event.get("duration") or (attraction or {}).get("visit_duration")),
            ("所在区域", event.get("area") or (attraction or {}).get("area")),
        ])
    elif kind == "transport" and route:
        route_facts = [
            ("交通方式", transport_mode_display_name(route.get("mode") or event.get("transport_mode"))),
            ("出发地", route.get("from")),
            ("预计门到门", route.get("door_to_door_duration")),
            ("费用", route.get("cost")),
            ("备选", route.get("fallback")),
        ]
        if (route.get("map_route") or {}).get("mode") not in {"car", "walk"}:
            route_facts.insert(2, ("乘车与换乘", route.get("route")))
        context_html = render_facts(route_facts)
    elif kind == "meal" and meal:
        context_html = render_meal_candidates(
            meal,
            restaurants or {},
            restaurant_snapshots or {},
            meal_route_evaluations or {},
        )
    elif kind == "lodging" and lodging and lodging.get("kind") == "area":
        context_html = render_facts(lodging_area_facts(lodging))
    else:
        context_html = render_facts([
            ("时长", event.get("duration")),
            ("所在区域", event.get("area")),
            ("预约状态", event.get("reservation")),
            ("推荐入口", event.get("entrance")),
            ("交通方式", event.get("transport_mode")),
            ("费用", event.get("cost")),
        ])
    cost_html = "" if kind == "attraction" else render_cost_items(
        event.get("cost_items") or [], event.get("cost_summary") or ""
    )
    if cost_html:
        cost_html = f'<details class="event-evidence"><summary>查看费用明细</summary>{cost_html}</details>'
    inventory_html = render_inventory_refs(
        route if kind == "transport" else lodging if kind == "lodging" else None,
        source_snapshots or {},
    )
    admission_html = render_admission_panel(event, booking_tasks or [], attraction or {}) if kind == "attraction" else ""
    checkpoint_html = render_checkpoints(
        event,
        meals or {},
        restaurants or {},
        restaurant_snapshots or {},
        meal_route_evaluations or {},
    ) if kind == "attraction" else ""
    community_html = render_community_refs(event.get("community_refs") or [])
    notes_html = ""
    if details or tips:
        notes_html = f'''<div class="event-notes">
          {f'<section><h4>执行细节</h4>{details}</section>' if details else ''}
          {f'<section><h4>注意事项</h4>{tips}</section>' if tips else ''}
        </div>'''
    end_time = f'<span class="end-time">– {esc(event.get("end_time"))}</span>' if event.get("end_time") else ""
    compact_class = " event-compact" if is_routine_event(event) else ""
    return f'''<article class="event event-{esc(kind)}{compact_class}" id="event-{idx}">
      <div class="rail"><span class="dot">{icon}</span></div>
      <div class="event-body"><div class="time"><strong>{esc(event.get("time"))}</strong>{end_time}</div>
      <div class="card"><div class="card-top"><span class="type-label">{label}</span><div class="card-tools">{weather_html}{map_html}</div></div>
        <h3>{esc(event.get("title"))}</h3>{subtitle}{image_html}
        {context_html}{admission_html}{checkpoint_html}{route_handoff_html}{inventory_html}{cost_html}{community_html}{actions_html}{notes_html}</div>
      </div>
    </article>'''


def render_day(
    day: dict[str, Any],
    day_idx: int,
    counter: list[int],
    attractions: dict[str, dict[str, Any]],
    routes: dict[str, dict[str, Any]],
    meals: dict[str, dict[str, Any]],
    booking_tasks: dict[str, dict[str, Any]],
    weather: dict[str, dict[str, Any]],
    restaurants: dict[str, dict[str, Any]],
    restaurant_snapshots: dict[str, dict[str, Any]],
    meal_route_evaluations: dict[str, dict[str, Any]],
    lodgings: dict[str, dict[str, Any]],
    source_snapshots: dict[str, dict[str, Any]],
    visuals: dict[str, Any] | None = None,
) -> str:
    events = []
    for event in day.get("events") or []:
        counter[0] += 1
        events.append(render_event(
            event,
            counter[0],
            attractions.get(str(event.get("attraction_id") or "")),
            routes.get(str(event.get("route_id") or "")),
            meals.get(str(event.get("meal_id") or "")),
            [booking_tasks[task_id] for task_id in event.get("booking_task_ids") or [] if task_id in booking_tasks],
            weather.get(str(event.get("weather_id") or "")),
            meals,
            restaurants,
            restaurant_snapshots,
            meal_route_evaluations,
            lodgings.get(str(event.get("lodging_id") or "")),
            source_snapshots,
        ))
    weather = f'<p class="day-weather">{esc(day.get("weather"))}</p>' if day.get("weather") else ""
    cost = f'<p class="day-cost">{esc(day.get("cost_summary"))}</p>' if day.get("cost_summary") else ""
    highlights = day_highlights(day, meals, restaurants, routes)
    postcard = render_day_postcard(day, day_idx, visuals or {}, highlights, booking_tasks)
    return f'''<details class="day" id="day-{day_idx}"{' open' if day_idx == 0 else ''}>
      <summary class="day-heading"><span class="day-dot" aria-hidden="true"></span><div class="day-heading-content"><div><span class="eyebrow">{esc(day.get("label") or f"D{day_idx + 1}")} · {esc(day.get("date"))}</span><h2>{esc(day.get("title") or day.get("theme") or "当日安排")}</h2>{weather}{cost}</div><p>{esc(day.get("summary"))}</p></div></summary>
      <div class="day-content">{postcard}{f'<p class="daily-route-preview"><b>今天这样走</b>{esc(" → ".join(highlights))}</p>' if highlights else ''}
      {''.join(events) if events else '<p class="empty">这一天还没有安排。</p>'}</div></details>'''


def render_portable_guide(data: dict[str, Any]) -> str:
    """Derive an offline handbook without copying research cards into the PDF."""
    planning, trip = data.get("planning") or {}, data.get("trip") or {}
    days = data.get("days") or []
    for day in days:
        for event in day.get("events") or []:
            research_evidence.validate_event_prose(event)
    def index(key: str, id_key: str = "id") -> dict[str, dict[str, Any]]:
        return {str(item.get(id_key)): item for item in planning.get(key) or [] if item.get(id_key)}

    attractions, meals, restaurants = index("attractions"), index("meal_options"), index("restaurants")
    snapshots, lodgings = index("restaurant_snapshots", "snapshot_id"), index("lodging_options")
    routes = {**index("intercity_options"), **index("transport_edges")}
    booking_tasks = index("booking_tasks")

    def required_cost(event: dict[str, Any]) -> str:
        prices = []
        for item in event.get("cost_items") or []:
            if not (item.get("required") or item.get("pricing_role") == "baseline"):
                continue
            name = str(item.get("name") or "必要费用")
            quantity = f' ×{item["quantity"]}' if item.get("quantity") is not None else ""
            if item.get("subtotal"):
                prices.append(f'{name}{quantity}，合计 {item["subtotal"]}')
            elif item.get("unit_price"):
                prices.append(f'{name}{quantity}，单价 {item["unit_price"]}')
        summary = event.get("cost_summary") or event.get("cost")
        if summary:
            prices.append(str(summary))
        return "；".join(prices)

    def map_link(name: str, actions: list[dict[str, Any]], fallback: str = "", location: dict[str, Any] | None = None) -> str:
        location = location or {}
        if not fallback and str(location.get("poi_provider") or "").lower() in {"amap", "gaode", "高德"} and location.get("poi_id"):
            fallback = "https://uri.amap.com/poidetail?" + urlencode({"poiid": location["poi_id"]})
        url = next((str(a.get("url")) for a in actions if (a.get("type") == "map" or is_amap_restaurant_action(a)) and str(a.get("url") or "").startswith("https://")), fallback)
        if not url.startswith("https://"):
            return ""
        return f'<a class="portable-map" href="{esc(url)}" data-print-map="{esc(name)}">查看地点</a>'

    overview, day_sections = [], []
    for number, day in enumerate(days, 1):
        label = str(day.get("label") or f"D{number}")
        meta = " · ".join(str(value) for value in [label, day.get("date")] if value)
        highlights = day_highlights(day, meals, restaurants, routes)
        overview.append(f'<li><b>{esc(meta)} · {esc(day.get("title") or day.get("theme") or "当日安排")}</b><span>{esc(" → ".join(highlights))}</span></li>')
        events = []
        for event in day.get("events") or []:
            kind = event.get("type")
            route = routes.get(str(event.get("route_id"))) or {}
            attraction = attractions.get(str(event.get("attraction_id"))) or {}
            meal = meals.get(str(event.get("meal_id"))) or {}
            lodging = lodgings.get(str(event.get("lodging_id"))) or {}
            title, address = overview_location(event, route, meal, restaurants, lodging)
            if kind == "lodging":
                title = str(event.get("title") or title)
            facts: list[tuple[str, Any]] = []
            qr = ""
            if kind == "food_walk":
                walk = event.get("food_walk") or {}
                facts.extend(("逛吃", line) for line in food_walk_lines(event))
                qr = map_link(title, [], str((walk.get("entry") or {}).get("map_url") or ""))
            if kind == "attraction":
                execution, admission = event.get("execution") or {}, event.get("admission") or {}
                entry, exit_point = execution.get("entry") or {}, execution.get("exit") or {}
                location = attraction.get("location") or {}
                address = str(entry.get("physical_address") or entry.get("address") or location.get("physical_address") or location.get("address") or address)
                facts.extend([
                    ("入口", entry.get("name") or event.get("entrance")),
                    ("开放", admission.get("opening_hours")),
                    ("停止入场", admission.get("last_entry")),
                    ("预约 / 入园", "；".join(str(v) for v in [admission.get("reservation_method"), admission.get("entry_requirement")] if v)),
                    ("出口 / 离开", "；".join(str(v) for v in [exit_point.get("name"), f'最晚 {execution.get("leave_by")}' if execution.get("leave_by") else ""] if v)),
                    ("费用参考", required_cost(event)),
                    ("提醒", admission.get("notice")),
                    ("携带 / 准备", event.get("preparation")),
                    ("无法成行时", execution.get("fallback")),
                ])
                for task_id in event.get("booking_task_ids") or []:
                    task = booking_tasks.get(str(task_id)) or {}
                    facts.append(("预约期限", task.get("deadline")))
                qr = map_link(title, [*(event.get("action_links") or []), *(attraction.get("action_links") or [])], str(event.get("map_url") or ""), location)
            elif kind == "meal" and meal and meal.get("mode") == "area":
                facts.extend(area_meal_facts(meal))
                qr = map_link(title, (meal.get("area_dining") or {}).get("action_links") or [])
            elif kind == "meal" and meal:
                selected = str(meal.get("selected_candidate_id") or "")
                restaurant = restaurants.get(selected) or {}
                candidate = next((c for c in meal.get("candidates") or [] if str(c.get("restaurant_id")) == selected), {})
                snapshot = snapshots.get(str(candidate.get("snapshot_id"))) or {}
                operations = snapshot.get("operations") or {}
                per_person = next((s.get("per_person") for s in snapshot.get("platform_signals") or [] if s.get("per_person")), "")
                backup_text = []
                for candidate_id in meal.get("fallback_candidate_ids") or []:
                    backup = restaurants.get(str(candidate_id)) or {}
                    if backup.get("name"):
                        backup_text.append(" · ".join(str(v) for v in [backup.get("name"), (backup.get("location") or {}).get("physical_address")] if v))
                facts.extend([
                    ("特色菜", restaurant.get("signature_dishes")),
                    ("人均 / 营业", "；".join(str(v) for v in [per_person, operations.get("opening_hours")] if v)),
                    ("预约", actionable_reservation(operations.get("reservation"))),
                    ("切换条件", meal.get("fallback_rule")),
                    ("备选地点", "；".join(backup_text)),
                ])
                qr = map_link(title, amap_restaurant_actions(restaurant, {}))
            elif kind == "transport" and route:
                destination = route.get("destination") or {}
                address = str(destination.get("physical_address") or destination.get("address") or "")
                facts.extend([
                    ("乘车", route.get("route") or route.get("service_or_train")),
                    ("门到门", route.get("door_to_door_duration")),
                    ("费用参考", route.get("cost") or route.get("fare")),
                    ("备选", route.get("fallback")),
                ])
                if str(route.get("mode") or "").lower() in {"flight", "train", "rail"}:
                    qr = map_link(str(destination.get("name") or route.get("to") or title), route.get("action_links") or [])
            elif kind == "lodging" and lodging:
                if lodging.get("kind") == "area":
                    facts.extend(lodging_area_facts(lodging))
                else:
                    facts.extend([
                        ("酒店", lodging.get("name") or lodging.get("hotel_name")),
                        ("费用参考", lodging.get("price") or event.get("cost_summary")),
                        ("行李", lodging.get("luggage_storage") or lodging.get("luggage_storage_policy")),
                    ])
                qr = map_link(title, lodging.get("action_links") or [])
            else:
                facts.append(("安排", event.get("subtitle")))
            if kind != "attraction":
                facts.extend([("要点", event.get("details")), ("提醒", event.get("tips"))])
            time = "–".join(str(v) for v in [event.get("time"), event.get("end_time")] if v)
            events.append(f'''<article class="portable-event" data-portable-event="{esc(event.get("id"))}">
              <div class="portable-event-main"><header><time>{esc(time)}</time><h3>{esc(title)}</h3></header>
              {f'<p class="portable-address">{esc(address)}</p>' if address else ''}
              <dl class="portable-facts">{"".join(overview_fact(k, v) for k, v in facts)}</dl></div>{qr}</article>''')
        day_sections.append(f'''<section class="portable-day"><header class="portable-day-heading"><span>{esc(meta)}</span><h2>{esc(day.get("title") or day.get("theme") or "当日安排")}</h2><p>{esc(day.get("summary"))}</p></header>{"".join(events)}</section>''')
    preparation = render_preparation(planning).replace('id="prepare-view"', 'id="portable-preparation"').replace('data-itinerary-view="prepare"', '').replace('prepare-heading', 'portable-prepare-heading')
    trip_meta = " · ".join(str(v) for v in [trip.get("date_range"), trip.get("travelers")] if v)
    return f'''<section class="portable-guide" id="portable-guide" aria-label="随身简版旅行手册">
      <header class="portable-cover"><span class="eyebrow">旅行手册 · 随身简版</span><h1>{esc(trip.get("title"))}</h1><p>{esc(trip_meta)}</p><p>{esc(trip.get("budget"))}</p>
      <p>保留每日安排与执行提醒；完整路线和核验依据可在网页查看。地图链接和二维码需要联网，离线时请按地址查阅已下载的地图。</p></header>
      <ol class="portable-overview">{"".join(overview)}</ol>{"".join(day_sections)}{preparation}
      <p class="portable-footnote">资料核验时间：{esc(trip.get("updated_at") or "未注明")}。报价、班次、开放与预约须在出发前复核；规划信息不代表已预订。</p></section>'''


def build(data: dict[str, Any], visuals: dict[str, Any] | None = None) -> str:
    visuals, artwork_library = prepare_artwork(visuals or {})
    validate_data(data)
    frontend_css = load_frontend_asset("itinerary-app.css", "style")
    frontend_js = load_frontend_asset("itinerary-app.js", "script")
    frontend_notices = (FRONTEND_ASSET_ROOT.parent / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    trip, days = data.get("trip") or {}, data.get("days") or []
    planning = data.get("planning") or {}
    attractions = {str(x.get("id")): x for x in planning.get("attractions") or [] if x.get("id")}
    routes = {str(x.get("id")): x for x in [*(planning.get("transport_edges") or []), *(planning.get("intercity_options") or [])] if x.get("id")}
    meals = {str(x.get("id")): x for x in planning.get("meal_options") or [] if x.get("id")}
    restaurants = {str(x.get("id")): x for x in planning.get("restaurants") or [] if x.get("id")}
    restaurant_snapshots = {str(x.get("snapshot_id")): x for x in planning.get("restaurant_snapshots") or [] if x.get("snapshot_id")}
    source_registry = {x.get("id"): x for x in data.get("sources") or []}
    restaurant_snapshots = {key: {**snapshot, "evidence_links": [
        {"url": source_registry[sid]["url"], "title": source_registry[sid]["title"]}
        for sid in dict.fromkeys(sid for item in snapshot.get("evidence") or [] for sid in item.get("source_ids") or [])
        if sid in source_registry
    ]} for key, snapshot in restaurant_snapshots.items()}
    meal_route_evaluations = {str(x.get("id")): x for x in planning.get("meal_route_evaluations") or [] if x.get("id")}
    lodgings = {str(x.get("id")): x for x in planning.get("lodging_options") or [] if x.get("id")}
    source_snapshots = {str(x.get("snapshot_id")): x for x in planning.get("source_snapshots") or [] if x.get("snapshot_id")}
    booking_tasks = {str(x.get("id")): x for x in planning.get("booking_tasks") or [] if x.get("id")}
    weather = {str(x.get("id")): x for x in planning.get("weather") or [] if x.get("id")}
    counter = [0]
    day_html = "".join(render_day(
        day, i, counter, attractions, routes, meals, booking_tasks, weather,
        restaurants, restaurant_snapshots, meal_route_evaluations,
        lodgings, source_snapshots, visuals,
    ) for i, day in enumerate(days))
    overview_html = render_overview(
        days, attractions, routes, meals, restaurants, restaurant_snapshots,
        meal_route_evaluations, lodgings,
    )
    route_overview_html = render_route_overview(days, planning.get("daily_routes") or [])
    overview_html = render_handbook_overview(days, booking_tasks, overview_html, route_overview_html, render_notebook_route(days, visuals, meals, restaurants))
    focus_html = render_trip_focus(days, meals, restaurants, data.get("planning") or {}, visuals)
    preparation_html = render_preparation(planning)
    portable_html = render_portable_guide(data)
    day_navigation = '<nav class="day-picker" aria-label="选择旅行日期">' + "".join(
        f'<a href="#day-{i}" data-day-target="day-{i}"><strong>D{i + 1}</strong><span>{esc(day.get("date"))}</span></a>'
        for i, day in enumerate(days)
    ) + '</nav>'
    sources_html = "".join(f'<li><a href="{esc(s.get("url"))}" target="_blank" rel="noopener noreferrer">{esc(s.get("title") or s.get("url"))}</a><span>{esc(s.get("note"))}{(" · 核验于 " + esc(s.get("checked_at"))) if s.get("checked_at") else ""}</span></li>' for s in data.get("sources") or [] if s.get("url"))
    trip_facts = "".join(
        f'<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>'
        for label, value in [
            ("目的地", trip.get("destination")),
            ("出行日期", compact_date_range(trip.get("date_range"))),
            ("同行安排", trip.get("travelers")),
            ("预算参考", trip.get("budget")),
        ] if value
    )
    cover = render_visual(visuals.get("cover") or first_event_visual(days), "hero-cover-media", eager=True)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(trip.get("title"))}</title>
<style>
:root{{--yellow:#ffd92f;--ink:#20201d;--muted:#6f706b;--line:#e7e5de;--paper:#fff;--wash:#f6f5f0;--accent:#ff6b35;--shadow:0 8px 26px rgba(40,38,25,.09)}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--wash);color:var(--ink);font:15px/1.6 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}a{{color:inherit}}button{{font:inherit}}.hero{{position:sticky;top:0;z-index:20;background:var(--yellow);box-shadow:0 2px 14px rgba(60,52,0,.12)}}.hero-inner{{max-width:980px;margin:auto;padding:18px 24px 0}}.kicker{{font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}}h1{{font-size:clamp(25px,5vw,42px);line-height:1.08;margin:6px 0 7px}}.trip-subtitle{{margin:0;font-weight:650}}.meta{{margin:5px 0 14px;font-size:13px;color:#554b00}}.tabs{{display:flex;gap:8px;overflow:auto;padding:0 0 12px;scrollbar-width:none}}.tabs::-webkit-scrollbar{{display:none}}.day-tab{{min-width:112px;border:0;border-radius:13px;padding:9px 12px;background:rgba(255,255,255,.52);text-align:left;cursor:pointer;color:#574e0c}}.day-tab strong,.day-tab span{{display:block}}.day-tab span{{font-size:11px;opacity:.75}}.day-tab.active{{background:#24231f;color:#fff}}main{{max-width:840px;margin:28px auto;padding:0 22px 80px}}.overview,.sources{{background:#fff;border-radius:18px;padding:20px 22px;box-shadow:var(--shadow);margin-bottom:24px}}.overview h2,.sources h2{{font-size:17px;margin:0 0 8px}}.assumption-list{{margin:0;padding-left:20px;color:var(--muted)}}.day{{scroll-margin-top:170px;margin-bottom:42px}}.day-heading{{display:flex;align-items:end;justify-content:space-between;gap:20px;margin:0 0 14px 106px}}.day-heading h2{{margin:2px 0 0;font-size:24px;line-height:1.2}}.day-heading p{{margin:0;color:var(--muted);text-align:right}}.day-weather,.day-cost{{text-align:left!important;font-size:12px;margin-top:4px!important}}.day-weather{{color:#35627c!important}}.day-cost{{color:#8a521f!important}}.eyebrow{{font-size:12px;color:var(--accent);font-weight:800;letter-spacing:.08em}}.event{{display:grid;grid-template-columns:82px 24px 1fr;align-items:stretch}}.time{{padding:22px 12px 0 0;text-align:right;font-variant-numeric:tabular-nums}}.time strong{{display:block}}.end-time{{display:block;font-size:11px;color:var(--muted)}}.rail{{position:relative;display:flex;justify-content:center}}.rail:before{{content:"";position:absolute;width:2px;background:var(--line);top:0;bottom:0}}.dot{{position:relative;z-index:1;margin-top:22px;width:25px;height:25px;border:2px solid #fff;border-radius:50%;display:grid;place-items:center;background:#24231f;color:#fff;font-size:11px;box-shadow:0 0 0 2px var(--line)}}.card{{background:var(--paper);border-radius:18px;padding:18px;margin:0 0 14px 13px;box-shadow:var(--shadow);min-width:0}}.card-top{{display:flex;justify-content:space-between;gap:12px;align-items:center}}.type-label{{font-size:11px;font-weight:800;color:var(--accent);letter-spacing:.08em}}.map-link{{font-size:12px;color:#5c5d58;text-decoration:none}}.card h3{{font-size:19px;line-height:1.25;margin:8px 0 3px}}.subtitle{{color:var(--muted);margin:0 0 12px}}.image-strip{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:6px;margin:13px 0;overflow:hidden;border-radius:12px;background:#ecebe6;min-height:150px}}.image-strip img{{width:100%;height:150px;object-fit:cover}}.event-facts{{margin:13px 0 0;border-top:1px solid var(--line);padding-top:9px}}.event-facts>div{{display:grid;grid-template-columns:92px 1fr;gap:10px;padding:4px 0}}.event-facts dt{{font-weight:750;font-size:12px}}.event-facts dd{{margin:0;color:#565752;font-size:13px}}.route-handoff{{margin-top:13px;border:1px solid #d7e4dc;background:#f7fbf8;border-radius:13px;padding:14px;display:grid;gap:5px}}.route-handoff-label{{font-size:11px;color:#557364;font-weight:800}}.route-destination-name{{font-size:16px;line-height:1.35;overflow-wrap:anywhere}}.route-destination-address{{font-size:12px;color:#53615a;overflow-wrap:anywhere}}.route-handoff-actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}}.route-copy,.route-destination-link{{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:7px 12px;border:1px solid #167743;border-radius:9px;background:#fff;color:#126536;text-decoration:none;font-size:12px;font-weight:750;cursor:pointer}}.route-destination-link{{background:#167743;color:#fff}}.route-copy-status{{font-size:11px;color:#126536}}.route-handoff small{{font-size:11px;color:#66706b}}.cost-breakdown{{margin-top:13px;border:1px solid #eadfb2;background:#fffdf4;border-radius:12px;padding:11px 12px}}.cost-breakdown h4{{margin:0 0 6px;font-size:13px}}.cost-breakdown ul{{list-style:none;margin:0;padding:0}}.cost-row{{display:flex;justify-content:space-between;gap:12px;border-top:1px dashed #ded6b6;padding:7px 0}}.cost-row span,.cost-summary{{display:block;color:var(--muted);font-size:11px;margin:0}}.cost-value{{text-align:right;white-space:nowrap}}.cost-summary{{border-top:1px solid #ded6b6;padding-top:7px;color:#72511a;font-weight:700}}.event-notes{{margin-top:13px;border-top:1px solid var(--line);padding-top:9px}}.event-notes section+section{{margin-top:10px}}.event-notes h4{{margin:0;font-size:13px}}.actions{{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}}.action-link{{display:inline-flex;align-items:center;min-height:44px;border:0;padding:7px 10px;border-radius:9px;background:#24231f;color:#fff;text-decoration:none;font-size:12px;font-weight:700;cursor:pointer}}.action-link:hover{{background:#000}}.action-link-wechat{{background:#168347}}.wechat-account-status{{align-self:center;color:#3f6250;font-size:11px}}.detail-list,.tip-list{{padding-left:20px;margin:9px 0;color:#52534f}}.tip-list{{background:#fff8d4;border-radius:10px;padding:9px 12px 9px 30px}}.sources ul{{padding-left:20px;margin:0}}.sources li{{margin:8px 0}}.sources li span{{display:block;color:var(--muted);font-size:12px}}.footer{{text-align:center;color:var(--muted);font-size:12px;padding-top:8px}}:focus-visible{{outline:3px solid #1668dc;outline-offset:3px}}
/* Dates and times are compact markers in one continuous timeline. */
.hero{{position:relative;top:auto}}.hero-inner{{padding-bottom:18px}}.day{{position:relative;scroll-margin-top:24px;margin-bottom:42px}}.day:before{{content:"";position:absolute;left:11px;top:18px;bottom:-30px;width:2px;background:var(--line)}}.day:last-of-type:before{{bottom:18px}}.day-heading{{position:relative;display:grid;grid-template-columns:24px minmax(0,1fr);gap:14px;align-items:start;margin:0 0 14px}}.day-dot{{position:relative;z-index:1;width:18px;height:18px;margin:5px 0 0 3px;border:4px solid var(--wash);border-radius:50%;background:var(--accent);box-shadow:0 0 0 2px #f0a184}}.day-heading-content{{display:flex;align-items:end;justify-content:space-between;gap:20px;min-width:0}}.day-heading h2{{margin:2px 0 0;font-size:24px;line-height:1.2}}.day-heading p{{margin:0;color:var(--muted);text-align:right}}.event{{position:relative;display:grid;grid-template-columns:24px minmax(0,1fr);gap:14px;align-items:start}}.event-body{{min-width:0}}.rail{{position:relative;display:flex;justify-content:center}}.rail:before{{display:none}}.dot{{margin-top:7px;width:24px;height:24px}}.time{{display:flex;align-items:baseline;gap:4px;min-height:31px;padding:4px 0 6px;text-align:left;font-variant-numeric:tabular-nums;color:var(--ink)}}.time strong{{display:inline;font-size:13px}}.end-time{{display:inline;font-size:11px;color:var(--muted)}}.card{{margin:0 0 18px;padding:18px}}
@media(max-width:620px){{.hero-inner{{padding:14px}}h1{{font-size:27px}}.meta{{white-space:normal}}main{{padding:0 10px 60px;margin-top:18px}}.overview{{margin:0 4px 20px}}.day:before{{left:10px}}.day-heading,.event{{grid-template-columns:22px minmax(0,1fr);gap:9px}}.day-heading-content{{display:block}}.day-heading p{{text-align:left;margin-top:4px}}.day-dot{{margin-left:2px}}.dot{{width:21px;height:21px;font-size:9px}}.time{{padding-top:2px}}.time strong{{font-size:12px}}.card{{margin-left:0;padding:15px 13px;border-radius:15px}}.card h3{{font-size:17px}}.image-strip,.image-strip img{{height:118px;min-height:118px}}}}
@media print{{.hero{{position:static}}.tabs,.map-link{{display:none}}body{{background:#fff}}main{{max-width:none}}.card,.overview,.sources{{box-shadow:none;border:1px solid #ddd}}.event,.card{{break-inside:avoid}}}}@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
.image-item{{position:relative;margin:0;min-width:0}}.image-item>a{{display:block;height:100%}}.image-item figcaption{{position:absolute;left:6px;right:6px;bottom:6px;padding:4px 6px;border-radius:6px;background:rgba(0,0,0,.68);color:#fff;font-size:10px;line-height:1.35}}.community-refs{{margin-top:13px;border-top:1px solid var(--line);padding-top:9px}}.community-refs h4{{margin:0;font-size:13px}}.community-refs>p{{margin:2px 0 8px;color:var(--muted);font-size:11px}}.community-refs>div{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}}.community-card{{display:block;border:1px solid #eadfe2;border-radius:10px;background:#fff8fa;padding:9px;text-decoration:none;min-width:0}}.community-card strong,.community-card span,.community-card small{{display:block}}.community-card strong{{font-size:12px;line-height:1.45}}.community-card span,.community-card small{{margin-top:3px;color:var(--muted);font-size:10px}}@media(max-width:620px){{.community-refs>div{{grid-template-columns:1fr}}}}
.checkpoint-list{{margin-top:13px;border:1px solid #d9e1dc;border-radius:12px;background:#f8fbf9;padding:12px}}.checkpoint-list h4{{margin:0 0 8px;font-size:13px}}.checkpoint-list ol{{list-style:none;margin:10px 0 0;padding:0;counter-reset:none}}.checkpoint-list li{{padding:9px 0;border-top:1px solid #e1e8e3}}.checkpoint-title{{display:flex;align-items:center;gap:8px}}.checkpoint-title>span{{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#1e6a45;color:#fff;font-size:11px;font-weight:800}}.checkpoint-title strong{{flex:1}}.checkpoint-title em{{font-style:normal;font-size:11px;color:#7a4a16}}.checkpoint-list p,.checkpoint-list small{{display:block;margin:3px 0 0 30px;color:#515752;font-size:12px}}.checkpoint-list .checkpoint-meta{{color:#1e6a45;font-weight:700}}.execution-fallback{{border-top:1px dashed #cbd8cf;padding-top:8px!important;margin-top:8px!important}}
.card-tools{{display:flex;align-items:center;gap:8px}}.weather-badge{{display:grid;place-items:center;width:42px;height:42px;border:1px solid #dfe4dd;border-radius:50%;background:#f8faf7;text-decoration:none;font-size:21px;box-shadow:0 2px 8px rgba(41,63,50,.08)}}.weather-badge:hover{{border-color:#739281;background:#eef6f0}}.section-label{{font-size:11px;font-weight:850;letter-spacing:.08em;color:#2b6848;text-transform:uppercase}}.admission-panel{{margin-top:13px;border-left:4px solid #316b9b;border-radius:4px 12px 12px 4px;background:#f2f7fb;padding:12px 14px}}.admission-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 20px;margin-top:9px}}.admission-grid span,.admission-grid strong{{display:block}}.admission-grid span{{font-size:10px;color:#62717c}}.admission-grid strong{{margin-top:2px;font-size:13px;line-height:1.45}}.admission-panel>p{{margin:9px 0 0;border-top:1px solid #dce8f0;padding-top:8px;color:#4f616d;font-size:12px}}.admission-panel .preparation b{{display:block;color:#285f8d;font-size:10px}}.admission-panel .actions{{margin-top:9px}}.admission-panel .action-link{{background:#285f8d}}.admission-panel .action-link-wechat{{background:#168347}}.admission-booking,.admission-cost{{margin-top:11px;border-top:1px solid #d5e3ed;padding-top:10px}}.admission-booking h4,.admission-cost h4{{margin:0 0 7px;color:#285f8d;font-size:12px}}.admission-booking article+article{{border-top:1px dashed #ccdce7;margin-top:9px;padding-top:9px}}.admission-booking h5{{margin:0;font-size:13px}}.admission-booking .event-facts{{border-top:0;margin-top:4px;padding-top:0}}.admission-cost ul{{list-style:none;margin:0;padding:0}}
.checkpoint-list{{padding:14px;background:#f8faf7}}.checkpoint-list ol{{position:relative;margin-top:8px}}.checkpoint-list ol:before{{content:"";position:absolute;left:56px;top:18px;bottom:18px;width:2px;background:#cfdcd3}}.checkpoint-list li.checkpoint{{position:relative;display:grid;grid-template-columns:46px 1fr;gap:22px;border-top:0;padding:8px 0}}.checkpoint-time{{padding-top:3px;text-align:right;font-variant-numeric:tabular-nums}}.checkpoint-time strong,.checkpoint-time span{{display:block}}.checkpoint-time strong{{font-size:12px}}.checkpoint-time span{{font-size:10px;color:#737873}}.checkpoint-content{{position:relative;border:1px solid #dfe7e1;border-radius:11px;background:#fff;padding:11px 12px;min-width:0}}.checkpoint-content:before{{content:"";position:absolute;left:-18px;top:16px;width:9px;height:9px;border:3px solid #f8faf7;border-radius:50%;background:#27704b;box-shadow:0 0 0 1px #79a58c}}.checkpoint-endpoint{{display:grid;grid-template-columns:auto 1fr;align-items:baseline;gap:3px 8px;margin:-2px 0 8px;padding-bottom:7px;border-bottom:1px solid #e4e9e5}}.checkpoint-endpoint span{{border-radius:999px;background:#e7f2eb;padding:2px 7px;color:#226a47;font-size:10px;font-weight:800}}.checkpoint-endpoint strong{{font-size:12px}}.checkpoint-endpoint small{{grid-column:2;margin:0!important;color:#6c716d!important;font-size:10px!important}}.checkpoint-exit{{margin:9px 0 -2px;padding:8px 0 0;border-top:1px solid #e4e9e5;border-bottom:0}}.checkpoint-title strong{{font-size:13px}}.checkpoint-title em{{margin-left:auto}}.checkpoint-list .checkpoint-content p,.checkpoint-list .checkpoint-content small{{margin-left:0}}.checkpoint-move{{margin:0 0 4px!important;color:#27704b!important;font-size:10px!important;font-weight:750}}.checkpoint-instruction{{font-size:12px!important;color:#333a35!important}}.checkpoint-narration{{margin-top:7px!important;border-left:2px solid #edbd45;padding-left:8px;color:#595c57!important}}.checkpoint-narration b{{display:block;margin-bottom:1px;color:#7b5a05;font-size:10px}}.checkpoint-images{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:5px;margin-top:8px;overflow:hidden;border-radius:8px;background:#eef1ed}}.checkpoint-images .image-item,.checkpoint-images img{{height:92px}}.checkpoint-images img{{width:100%;object-fit:cover}}.checkpoint-meal{{margin-top:8px;border-radius:9px;background:#fff6d9;padding:9px 10px}}.checkpoint-meal strong,.checkpoint-meal p,.checkpoint-meal small{{margin:0!important}}.checkpoint-meal strong{{font-size:11px}}.checkpoint-meal p,.checkpoint-meal small{{font-size:10px!important;color:#655d45!important}}.checkpoint-meal .actions{{margin-top:6px}}.checkpoint-meal .action-link{{padding:5px 7px;background:#725b13;font-size:10px}}.execution-fallback{{margin-left:0!important}}
@media(max-width:620px){{.weather-badge{{width:44px;height:44px}}.admission-grid{{grid-template-columns:1fr}}.checkpoint-list{{padding:11px 9px}}.checkpoint-list ol:before{{left:46px}}.checkpoint-list li.checkpoint{{grid-template-columns:38px 1fr;gap:17px}}.checkpoint-time strong{{font-size:11px}}.checkpoint-content:before{{left:-14px}}.checkpoint-endpoint{{grid-template-columns:auto 1fr}}.checkpoint-images .image-item,.checkpoint-images img{{height:76px}}}}
@media print{{.restaurant-sort-tabs,.restaurant-select{{display:none}}}}
.restaurant-comparison{{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}}.restaurant-comparison-head strong,.restaurant-comparison-head small{{display:block}}.restaurant-comparison-head strong{{margin-top:3px;font-size:13px}}.restaurant-comparison-head small{{margin-top:3px;color:var(--muted)}}.restaurant-comparison-head p{{margin:7px 0 0;color:var(--muted);font-size:12px}}.restaurant-sort-tabs{{display:flex;gap:6px;margin-top:11px;overflow:auto;padding:2px;scrollbar-width:none}}.restaurant-sort-tabs::-webkit-scrollbar{{display:none}}.restaurant-sort-tabs button{{flex:none;min-height:44px;border:1px solid #d8d8d0;border-radius:999px;background:#fff;padding:8px 12px;color:#555751;font-size:12px;font-weight:750;cursor:pointer}}.restaurant-sort-tabs button[aria-pressed="true"]{{border-color:#236c48;background:#236c48;color:#fff}}.restaurant-sort-tabs button:disabled{{cursor:not-allowed;opacity:.45}}.restaurant-sort-note,.restaurant-current-choice{{margin:5px 2px 0;color:var(--muted);font-size:11px}}.restaurant-current-choice{{color:#17643d;font-weight:750}}.restaurant-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}}.restaurant-candidate{{border:1px solid #e2ddd0;border-radius:13px;background:#faf9f5;padding:11px;min-width:0}}.restaurant-candidate.recommended{{grid-column:1/-1;border-color:#d9b72f;background:#fffdf1}}.restaurant-candidate.selected{{box-shadow:0 0 0 2px #2d7b50}}.restaurant-candidate-head{{display:flex;align-items:flex-start;gap:9px;min-height:44px;cursor:pointer;list-style:none}}.restaurant-candidate-head::-webkit-details-marker{{display:none}}.restaurant-role,.restaurant-choice-status{{flex:none;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:800}}.restaurant-role{{background:#5e625d;color:#fff}}.restaurant-candidate.recommended .restaurant-role{{background:#9b6b00}}.restaurant-choice-status{{margin-left:auto;background:#e3f3e9;color:#17643d}}.restaurant-candidate:not(.selected) .restaurant-choice-status{{display:none}}.restaurant-candidate-head h4,.restaurant-candidate-head p{{margin:0}}.restaurant-candidate-head h4{{font-size:14px}}.restaurant-candidate-head p{{color:var(--muted);font-size:11px}}.restaurant-candidate .image-strip,.restaurant-candidate .image-strip img{{height:110px;min-height:110px}}.restaurant-candidate .event-facts>div{{grid-template-columns:76px 1fr}}.restaurant-signals{{list-style:none;margin:9px 0 0;padding:0;border-top:1px dashed #ddd6c4}}.restaurant-signals li{{padding:6px 0;border-bottom:1px dashed #e6e0d2}}.restaurant-signals strong,.restaurant-signals span,.restaurant-signals small{{display:block}}.restaurant-signals span{{font-size:12px}}.restaurant-signals small{{color:var(--muted);font-size:10px}}.restaurant-community{{margin:8px 0 0;border-radius:8px;background:#fff3f6;padding:8px;font-size:11px;color:#67545a}}.restaurant-community-links{{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}}.restaurant-community-links a{{border-bottom:1px solid #bd8492;color:#754957;font-size:10px;text-decoration:none}}.restaurant-community-links a:hover{{color:#382029;border-bottom-color:#382029}}.restaurant-select{{min-height:44px;margin-top:10px;border:1px solid #236c48;border-radius:9px;background:#fff;color:#17643d;padding:8px 12px;font-size:12px;font-weight:800;cursor:pointer}}.restaurant-select[aria-pressed="true"]{{background:#236c48;color:#fff}}.inventory-proof{{margin-top:13px;border:1px solid #d5e1ef;border-radius:11px;background:#f5f9fe;padding:10px 12px}}.inventory-proof h4{{margin:0 0 5px;font-size:12px;color:#285f8d}}.inventory-proof ul{{list-style:none;margin:0;padding:0}}.inventory-proof li+li{{border-top:1px dashed #d5e1ef;margin-top:7px;padding-top:7px}}.inventory-proof strong,.inventory-proof span,.inventory-proof small{{display:block;font-size:11px}}.inventory-proof span,.inventory-proof small{{color:var(--muted)}}.inventory-proof a{{font-size:11px;color:#285f8d}}@media(max-width:620px){{.restaurant-grid{{grid-template-columns:1fr}}.restaurant-candidate.recommended{{grid-column:auto}}}}
.restaurant-backup-carousel{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(340px,420px);gap:10px;margin-top:10px;overflow-x:auto;padding:2px 2px 9px;scroll-snap-type:x proximity;scrollbar-width:thin}}.restaurant-backup-carousel>.restaurant-candidate{{scroll-snap-align:start}}.restaurant-toggle{{flex:none;color:#62645f;font-size:11px;font-weight:750}}.restaurant-toggle:after{{content:"展开"}}.restaurant-candidate[open] .restaurant-toggle:after{{content:"收起"}}.restaurant-candidate[open]>summary{{border-bottom:1px solid #e2ddd0;padding-bottom:9px}}.restaurant-candidate-body{{margin-top:9px}}.restaurant-candidate.recommended .restaurant-candidate-body{{margin-top:9px}}@media(max-width:620px){{.restaurant-backup-carousel{{grid-auto-columns:calc(100vw - 66px)}}}}
.restaurant-routes{{margin-top:10px;border-top:1px solid var(--line)}}.restaurant-route-leg{{padding:8px 0;border-bottom:1px dashed #ddd6c4}}.restaurant-route-leg strong,.restaurant-route-leg span,.restaurant-route-leg small{{display:block;overflow-wrap:anywhere}}.restaurant-route-leg strong{{font-size:12px}}.restaurant-route-link{{display:inline-block;color:inherit;text-decoration:none}}.restaurant-route-link:hover,.restaurant-route-link:focus-visible{{text-decoration:underline;text-underline-offset:3px}}.restaurant-route-leg span{{margin-top:2px;color:#565752;font-size:11px}}.restaurant-route-leg small{{margin-top:2px;color:var(--muted);font-size:10px}}
.event-facts>div{{grid-template-columns:92px minmax(0,1fr)}}.event-facts dd{{min-width:0;overflow-wrap:anywhere}}.restaurant-candidate .event-facts>div{{grid-template-columns:76px minmax(0,1fr)}}
/* Keep the header and every PC itinerary view on one stable content grid. */
:root{{--page-width:1224px;--page-gutter:22px}}.hero-inner{{width:100%;max-width:var(--page-width);padding-left:var(--page-gutter);padding-right:var(--page-gutter)}}main{{width:100%;max-width:var(--page-width)!important;padding-left:var(--page-gutter);padding-right:var(--page-gutter)}}.itinerary-overview{{max-width:none!important}}@media(max-width:620px){{.hero-inner{{padding-left:14px;padding-right:14px}}main{{padding-left:10px;padding-right:10px}}}}
.view-switcher{{display:inline-grid;grid-template-columns:repeat(3,1fr);gap:3px;margin:2px 0 16px;padding:3px;border:1px solid rgba(74,64,0,.18);border-radius:11px;background:rgba(255,255,255,.48)}}.view-switcher button{{min-width:104px;min-height:44px;border:0;border-radius:8px;background:transparent;padding:8px 14px;color:#62570c;font-size:12px;font-weight:800;cursor:pointer}}.view-switcher button[aria-selected="true"]{{background:#24231f;color:#fff;box-shadow:0 2px 8px rgba(45,39,0,.16)}}[hidden]{{display:none!important}}html[data-itinerary-view="overview"] main{{max-width:1224px}}html[data-itinerary-view="routes"] main{{max-width:980px}}.itinerary-overview{{max-width:1180px;margin:0 auto}}.overview-heading,.route-view-heading{{margin:0 0 14px}}.overview-heading h2,.route-view-heading h2{{margin:2px 0 3px;font-size:24px;line-height:1.2}}.overview-heading p,.route-view-heading p{{margin:0;color:var(--muted);font-size:12px}}.overview-table-wrap{{overflow:auto;border:1px solid #cfcec6;background:#fff;box-shadow:var(--shadow);scrollbar-gutter:stable}}.overview-table{{width:100%;min-width:760px;border-collapse:collapse;table-layout:fixed;font-size:11px;line-height:1.42}}.overview-time-col{{width:92px}}.overview-place-col{{width:230px}}.overview-table th,.overview-table td{{border-right:1px solid #deddd6;border-bottom:1px solid #deddd6;padding:7px 9px;text-align:left;vertical-align:top}}.overview-table tr>*:last-child{{border-right:0}}.overview-table thead th{{position:sticky;top:0;z-index:2;background:#24231f;color:#fff;font-size:10px;letter-spacing:.08em}}.overview-day-row th{{padding:8px 9px;background:#fff0a3;color:#2f2b16}}.overview-day-row strong,.overview-day-row span{{display:block}}.overview-day-row strong{{font-size:12px}}.overview-day-row span{{margin-top:1px;color:#70631d;font-size:10px;font-weight:500}}.overview-event:nth-child(odd) td{{background:#fbfbf8}}.overview-time{{font-variant-numeric:tabular-nums;white-space:nowrap}}.overview-time strong,.overview-time span{{display:block}}.overview-time strong{{font-size:12px}}.overview-time span{{color:var(--muted);font-size:10px}}.overview-place>.overview-type,.overview-place>strong,.overview-place>small{{display:block}}.overview-type{{margin-bottom:2px;color:var(--accent);font-size:9px;font-weight:850;letter-spacing:.08em}}.overview-place>strong{{font-size:12px;line-height:1.35}}.overview-place>small{{margin-top:2px;color:var(--muted);font-size:9px;overflow-wrap:anywhere}}.overview-facts{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 14px;margin:0}}.overview-facts>div{{display:grid;grid-template-columns:44px minmax(0,1fr);gap:5px;min-width:0}}.overview-facts dt{{color:#77786f;font-size:9px;font-weight:750}}.overview-facts dd{{min-width:0;margin:0;color:#30312e;font-size:10px;overflow-wrap:anywhere}}.overview-empty{{color:var(--muted);text-align:center!important}}.route-day{{margin:0 0 26px}}.route-day-heading{{display:flex;align-items:end;justify-content:space-between;gap:16px;margin-bottom:10px}}.route-day-heading h3{{margin:2px 0 0;font-size:19px;line-height:1.25}}.route-day-heading>span{{color:var(--muted);font-size:12px}}.route-sequence{{padding:18px;border:1px solid #d7e4dc;border-radius:14px;background:#fff;box-shadow:var(--shadow)}}.route-sequence h4{{margin:0 0 12px;font-size:15px}}.route-sequence>p{{margin:12px 0 0;color:var(--muted);font-size:12px}}.route-day-empty{{margin:0;border:1px dashed #cfd6d0;border-radius:12px;background:#fff;padding:24px;text-align:center;color:var(--muted)}}@media(max-width:620px){{.view-switcher{{display:grid;width:100%}}.view-switcher button{{min-width:0;padding-inline:8px}}.itinerary-overview,.itinerary-routes{{margin:0 2px}}.overview-heading,.route-view-heading{{padding:0 2px}}.overview-table{{min-width:690px}}.overview-table-wrap{{box-shadow:none}}.route-day-heading{{align-items:start}}}}@media print{{.view-switcher{{display:none}}.itinerary-overview{{max-width:none}}.overview-table-wrap{{overflow:visible;border-color:#aaa;box-shadow:none}}.overview-table{{min-width:0;font-size:9px}}.overview-table thead th{{position:static}}}}
[data-itinerary-view][hidden] *::before,[data-itinerary-view][hidden] *::after{{content:none!important}}
html[data-itinerary-view="routes"] .sources,html[data-itinerary-view="routes"] .footer{{display:none}}
.route-stop-list{{display:grid;gap:0;margin:0;padding:0;list-style:none}}.route-stop-list li{{position:relative;display:grid;grid-template-columns:28px minmax(0,1fr);gap:10px;min-width:0;padding:0 0 16px}}.route-stop-list li:not(:last-child):before{{content:"";position:absolute;top:27px;bottom:0;left:13px;width:2px;background:#b9d2c2}}.route-stop-number{{position:relative;z-index:1;display:grid;width:28px;height:28px;place-items:center;border-radius:50%;background:#167743;color:#fff;font-size:11px;font-weight:850}}.route-stop-list li:first-child .route-stop-number,.route-stop-list li:last-child .route-stop-number{{background:#24231f}}.route-stop-list li>div{{min-width:0;padding-top:2px}}.route-stop-list strong,.route-stop-list small,.route-stop-mode{{display:block}}.route-stop-list strong{{font-size:13px;line-height:1.4}}.route-stop-list small,.route-stop-mode{{margin-top:2px;color:var(--muted);font-size:11px}}@media print{{.view-switcher,.route-handoff-actions{{display:none!important}}#detail-view,#detail-view[hidden]{{display:block!important}}#overview-view,#route-view{{display:none!important}}.itinerary-tail,.itinerary-tail[hidden]{{display:block!important}}}}
{frontend_css}
</style></head><body class="travel-notebook">{artwork_library}<input class="itinerary-view-state" type="radio" name="itinerary-view" id="itinerary-view-focus" checked aria-label="显示玩什么吃什么"><input class="itinerary-view-state" type="radio" name="itinerary-view" id="itinerary-view-overview" aria-label="显示全程概览"><input class="itinerary-view-state" type="radio" name="itinerary-view" id="itinerary-view-detail" aria-label="显示每日行程"><input class="itinerary-view-state" type="radio" name="itinerary-view" id="itinerary-view-prepare" aria-label="显示出发准备"><header class="hero"><div class="hero-inner"><div class="hero-topline"><span class="hero-brand">旅行手账 <span>·</span> {esc(trip.get("destination") or "旅行提案")}</span><div id="itinerary-tools"></div></div><noscript><p class="tools-fallback">使用浏览器的打印功能可保存随身简版 PDF。</p></noscript><div class="trip-header"><div class="hero-image-panel{' has-media' if cover else ''}">{cover}<span class="hero-image-label">{('旅行氛围插画' if visuals.get('cover') else '旅行影像') if cover else '旅行手记'} · {esc(trip.get('destination') or '目的地')}</span></div><div class="trip-intro"><span class="notebook-seal" aria-hidden="true">旅行<br>手账</span><span class="kicker">一本旅行手账 <span>·</span> {esc(trip.get('destination') or '目的地')}</span><h1>{esc(trip.get("title"))}</h1><p class="trip-subtitle">{esc(trip.get("subtitle"))}</p><dl class="trip-facts" aria-label="旅行信息">{trip_facts}</dl><span class="hero-scroll-note">翻开手账，看看这趟怎么玩 <span aria-hidden="true">↓</span></span></div></div><div class="trip-toolbar"><div id="itinerary-view-controls"><nav class="view-switcher" data-fallback-view-switcher aria-label="行程展示方式"><label for="itinerary-view-focus">玩什么·吃什么</label><label for="itinerary-view-overview">全程概览</label><label for="itinerary-view-detail">每日行程</label><label for="itinerary-view-prepare">出发准备</label></nav></div></div></div></header>
<main>{focus_html}<section id="detail-view" data-itinerary-view="detail"><header class="overview-heading detail-intro"><span class="chapter-number">04 / DAY BY DAY</span><h2>每天怎么走</h2><p>时间、路线、吃饭和需要提前确认的事，都放在当天。</p></header>{day_navigation}{day_html}</section>{overview_html}{preparation_html}{f'<details class="sources"><summary>信息来源（{len(data.get("sources") or [])} 条）</summary><ul>{sources_html}</ul></details>' if sources_html else ''}<p class="footer">最后更新：{esc(trip.get("updated_at") or "未注明")} · 出发前请再次核对时刻、价格与开放状态</p></main>
{portable_html}
<script>/*
{frontend_notices}
*/
{frontend_js}</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--visuals", type=Path, help="可选的本地视觉素材清单；图片会内联进单文件 HTML")
    parser.add_argument("--workspace", type=Path, help="可选执行计时工作区；默认从输入路径寻找")
    args = parser.parse_args()
    workspace = args.workspace or execution_log.infer_workspace(args.input)
    with execution_log.measured(workspace, "rendering", "render"):
        data = json.loads(args.input.read_text(encoding="utf-8"))
        visuals = load_visual_manifest(args.visuals) if args.visuals else None
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(build(data, visuals), encoding="utf-8")
    print(f"已生成：{args.output}")


if __name__ == "__main__":
    main()
