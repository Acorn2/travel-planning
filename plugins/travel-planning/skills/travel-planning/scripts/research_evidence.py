"""Provider-independent evidence gates for new multi-source travel plans.

Legacy plans remain readable. These gates check recorded evidence, not whether an
agent actually browsed a page or whether a subjective recommendation is correct.
"""
from __future__ import annotations

from datetime import date, datetime
import importlib.util
from pathlib import Path
from math import isfinite
from typing import Any
from urllib.parse import urlparse

SEARCH_SPEC = importlib.util.spec_from_file_location("xhs_search_contract", Path(__file__).with_name("xhs_search_contract.py"))
assert SEARCH_SPEC and SEARCH_SPEC.loader
xhs_search_contract = importlib.util.module_from_spec(SEARCH_SPEC)
SEARCH_SPEC.loader.exec_module(xhs_search_contract)


CHANNELS = {"web_search", "browser", "mcp", "api", "user"}
SOURCE_KINDS = {"official", "government", "transport_official", "merchant", "editorial", "travel_guide", "community", "map", "restaurant_platform", "booking_platform", "weather_api"}
EVIDENCE_STATUSES = {"verified", "platform_reported", "community_reported", "partial", "estimated", "to_recheck"}


def validate_event_prose(event: dict[str, Any]) -> None:
    """User-facing event notes are text arrays, never raw provider objects."""
    for field in ("details", "tips"):
        if field not in event:
            continue
        value = event[field]
        path = f"事件 {event.get('id') or '未注明'}.{field}"
        if not isinstance(value, list):
            raise ValueError(f"{path} 必须是文字数组")
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{path}[{index}] 必须是非空文字，不能包含原始对象")


def timestamp(value: Any) -> None:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("证据查询时间必须是 ISO 日期或时间") from error


def source_identity(source: dict[str, Any]) -> str:
    """Different acquisition channels do not turn one original into two sources."""
    parsed = urlparse(str(source.get("origin_url") or source.get("url") or ""))
    return str(source.get("independence_key") or str(source.get("publisher") or "").strip().casefold() or f"{parsed.hostname}{parsed.path.rstrip('/')}")


def validate_source(source: dict[str, Any]) -> None:
    if source.get("acquisition") not in CHANNELS:
        raise ValueError("多来源研究来源缺少有效 acquisition 获取渠道")
    if source.get("accessed") != "full":
        raise ValueError("采用来源必须读过原页面；搜索摘要不能作为完整证据")
    if source.get("kind") not in SOURCE_KINDS:
        raise ValueError("多来源研究来源 kind 无效")
    if not source.get("title") or not source.get("publisher"):
        raise ValueError("多来源研究来源必须记录标题及原始发布者 publisher")
    if urlparse(str(source.get("url") or "")).scheme != "https" or not urlparse(str(source.get("url") or "")).hostname:
        raise ValueError("多来源研究来源必须使用 HTTPS")
    timestamp(source.get("checked_at"))
    if source.get("published_at"):
        timestamp(source["published_at"])
        if str(source["published_at"])[:10] > str(source["checked_at"])[:10]:
            raise ValueError("来源发布时间不得晚于查询时间")


def validate_evidence(items: Any, sources: dict[str, dict[str, Any]], required: set[str] | None = None) -> None:
    if not isinstance(items, list) or not items:
        raise ValueError("多来源推荐必须提供非空 evidence 证据")
    aspects = set()
    for item in items:
        if not isinstance(item, dict) or item.get("aspect") not in {"identity", "menu", "operations", "admission", "experience", "route"} or not item.get("summary"):
            raise ValueError("证据必须记录 aspect 和具体 summary")
        aspects.add(item["aspect"])
        if item.get("status") not in EVIDENCE_STATUSES:
            raise ValueError("多来源证据 status 无效")
        timestamp(item.get("checked_at"))
        ids = item.get("source_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(sid, str) or sid not in sources for sid in ids):
            raise ValueError("证据引用了未登记来源或缺少 source_ids")
        for sid in ids:
            validate_source(sources[sid])
        if item.get("status") in {"to_recheck", "estimated", "partial"}:
            if not item.get("reason"):
                raise ValueError("待核、估算或部分证据必须说明具体 reason")
        if item.get("status") == "to_recheck":
            if not item.get("recheck_at") or not item.get("action_links"):
                raise ValueError("待核证据必须记录复核时间和可执行入口")
            if any(urlparse(str(link.get("url") or "")).scheme != "https" for link in item["action_links"]):
                raise ValueError("待核证据复核入口必须使用 HTTPS")
        if item.get("critical") and item.get("status") in {"to_recheck", "estimated", "partial"}:
            raise ValueError("影响采用安排的关键证据未解决，须补查或调整行程")
        if item.get("applicable_date"):
            timestamp(item["applicable_date"])
        if item["aspect"] in {"admission", "operations"} and item.get("status") == "verified":
            if not any(sources[sid].get("kind") in {"official", "government", "transport_official", "merchant"} for sid in ids):
                raise ValueError("开放或营业 verified 必须有适用的官方、运营方或商家证据")
    if required and not required.issubset(aspects):
        raise ValueError("缺少推荐证据主题：" + "、".join(sorted(required - aspects)))


def validate_links(links: Any) -> None:
    if not isinstance(links, list) or not links:
        raise ValueError("采用对象须有可执行的 HTTPS 入口")
    for link in links:
        if not isinstance(link, dict):
            raise ValueError("操作入口须为对象")
        parsed = urlparse(str(link.get("url") or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("操作入口须使用完整 HTTPS URL")


def validate_coordinates(location: dict[str, Any]) -> None:
    if not location.get("coordinates"):
        return
    if location.get("coordinate_system") not in {"GCJ-02", "WGS84"}:
        raise ValueError("已提供坐标必须声明坐标系")
    try:
        lng, lat = (float(n) for n in str(location["coordinates"]).split(","))
        if not isfinite(lng) or not isfinite(lat) or not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError()
    except (ValueError, TypeError) as error:
        raise ValueError("坐标必须为有效的经度,纬度") from error


def validate_location(restaurant: dict[str, Any], sources: dict[str, dict[str, Any]]) -> None:
    location = restaurant.get("location") or {}
    address = str(location.get("physical_address") or "")
    if len(address.strip()) < 6 or any(word in address.lower() for word in ("附近", "周边", "待定", "unknown", "待确认")):
        raise ValueError("餐厅必须提供具体经营地址")
    verification = location.get("verification") or {}
    if not verification.get("checked_at") or not verification.get("source_ids"):
        raise ValueError("无 POI 的餐厅也必须有 location.verification 地址核验记录")
    timestamp(verification["checked_at"])
    for sid in verification["source_ids"]:
        if sid not in sources:
            raise ValueError("餐厅地址引用了未登记来源")
        validate_source(sources[sid])
        if sources[sid].get("kind") not in {"official", "merchant", "map", "restaurant_platform"}:
            raise ValueError("餐厅身份须有商家、官方或地点平台证据")
    validate_coordinates(location)
    if location.get("poi_id"):
        sid = location.get("poi_source_id")
        source = sources.get(sid) or {}
        if (not location.get("poi_provider") or not location.get("poi_verified_at")
                or source.get("provider") != location.get("poi_provider")
                or location["poi_id"] not in (source.get("provider_poi_ids") or [])):
            raise ValueError("餐厅 POI 必须与原始平台证据匹配")


def validate_route(route: dict[str, Any], sources: dict[str, dict[str, Any]]) -> None:
    if route.get("status") not in {"verified", "platform_reported", "estimated"}:
        raise ValueError("多来源路线必须标明 verified、platform_reported 或 estimated")
    endpoints = [route.get(key) for key in ("from_anchor", "to_anchor", "origin", "destination")]
    if any(isinstance(point, dict) and point.get("precision") == "area" for point in endpoints):
        if route.get("status") != "estimated":
            raise ValueError("住宿区域锚点的交通必须按区域估算，不能标为已核验酒店路线")
    ids = route.get("source_ids") or []
    if not ids or any(not isinstance(sid, str) or sid not in sources for sid in ids):
        raise ValueError("路线缺少可追溯的来源")
    for sid in ids:
        validate_source(sources[sid])
    timestamp(route.get("checked_at"))
    if route.get("map_url"):
        validate_links([{"url": route["map_url"]}])
    if route.get("action_links"):
        validate_links(route["action_links"])
    minutes = route.get("door_to_door_minutes")
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not isfinite(minutes) or minutes < 0:
        raise ValueError("路线门到门时间必须是非负数")
    if route.get("status") == "estimated":
        bounds = route.get("duration_range_minutes")
        buffer = route.get("buffer_minutes")
        if (not route.get("basis") or not isinstance(bounds, list) or len(bounds) != 2
                or any(not isinstance(n, (int, float)) or isinstance(n, bool) or not isfinite(n) for n in bounds)
                or bounds[0] < 0 or bounds[1] < bounds[0]
                or not isinstance(buffer, (int, float)) or isinstance(buffer, bool) or not isfinite(buffer) or buffer < 0
                or minutes < bounds[1] + buffer):
            raise ValueError("估算路线必须有依据、耗时区间、缓冲并按上界加缓冲排程")
    elif not any(sources[sid].get("kind") in {"map", "transport_official", "official"} for sid in ids):
        raise ValueError("精确路线必须有地图或运营方证据，不能把攻略估算标成核验")


def same_endpoint(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    validate_coordinates(actual)
    validate_coordinates(expected)
    return (all(actual.get(k) and actual.get(k) == expected.get(k) for k in ("name", "physical_address"))
            and (expected.get("precision") != "area" or actual.get("precision") == "area")
            and (not expected.get("coordinates") or (actual.get("coordinates") == expected["coordinates"] and actual.get("coordinate_system") == expected.get("coordinate_system"))))


def validate_depth(item: dict[str, Any], domain: str, required: bool = False) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"{domain} 研究对象必须是对象")
    if not required and "research_depth" not in item:
        return
    if item.get("research_depth") not in ("basic", "detailed") or not isinstance(item.get("depth_reason"), str) or not item["depth_reason"].strip():
        raise ValueError(f"{domain} 缺少研究深度或决策依据")
    factors = item.get("decision_factors")
    allowed = {"routine", "user_priority", "fixed_time", "limited_supply", "dietary_constraints", "complex_transfer", "weather_sensitive", "cost_tradeoff"}
    if not isinstance(factors, list) or not factors or any(not isinstance(value, str) or value not in allowed for value in factors):
        raise ValueError(f"{domain} 缺少有效决策因素")
    if len(factors) != len(set(factors)):
        raise ValueError(f"{domain} 决策因素不能重复")
    critical = {"meal": {"user_priority", "fixed_time", "limited_supply", "dietary_constraints"},
                "transport": {"fixed_time", "complex_transfer"}, "weather": {"weather_sensitive"}}.get(domain, set())
    if item["research_depth"] == "basic" and critical.intersection(factors):
        raise ValueError(f"{domain} 关键决策因素需要 detailed 研究")


def validate_dining_fallback(value: Any, *, allow_legacy_text: bool = False) -> None:
    """Keep dining fallback data typed before assembly, auditing or display."""
    if allow_legacy_text and isinstance(value, str) and value.strip():
        return
    fields = {"name", "physical_address", "plan"}
    if (not isinstance(value, dict) or set(value) != fields
            or any(not isinstance(value.get(key), str) or not value[key].strip() for key in fields)):
        raise ValueError("用餐备选须包含非空文字 name、physical_address、plan；旧逛吃备选仅兼容非空文字")


def validate_food_walk_structure(walk: Any) -> None:
    if not isinstance(walk, dict):
        raise ValueError("food_walk 必须是对象")
    if walk.get("meal_type") not in ("小吃", "午餐", "晚餐"):
        raise ValueError("逛吃事件必须声明 meal_type=小吃/午餐/晚餐")
    for field in ("entry", "exit"):
        point = walk.get(field)
        if (not isinstance(point, dict) or any(not isinstance(point.get(key), str) or not point[key].strip()
                for key in ("name", "physical_address", "coordinates", "map_url"))):
            raise ValueError(f"逛吃 {field} 必须有文字名称、具体地址、坐标和地图入口")
    foods = walk.get("food_items")
    minimum = 1 if walk["meal_type"] == "小吃" else 2
    if (not isinstance(foods, list) or not minimum <= len(foods) <= 4
            or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
                or not isinstance(item.get("source_ids"), list) or not item["source_ids"]
                or any(not isinstance(sid, str) or not sid.strip() for sid in item["source_ids"]) for item in foods)):
        raise ValueError("逛吃须提供1至4种有名称和来源的食物，替代正餐时至少2种")
    budget = walk.get("budget_per_person")
    if type(budget) not in (int, float) or not isfinite(budget) or budget <= 0:
        raise ValueError("逛吃须提供正数人均预算估算")
    validate_dining_fallback(walk.get("fallback"), allow_legacy_text=True)
    if "pet_note" in walk and not isinstance(walk["pet_note"], str):
        raise ValueError("逛吃 pet_note 必须是文字")


def validate_area_meal(meal: dict[str, Any], applicable_date: str | None = None,
                       sources: dict[str, dict[str, Any]] | None = None) -> None:
    validate_depth(meal, "meal", True)
    if meal.get("research_depth") != "basic" or set(meal["decision_factors"]) != {"routine"}:
        raise ValueError("区域用餐仅用于普通日常餐窗，重要餐不能降为区域建议")
    if any(not isinstance(meal.get(key), str) or not meal[key].strip() for key in ("id", "name", "time_window", "applicable_date", "checked_at")):
        raise ValueError("区域餐窗缺少身份、日期或时间窗")
    try:
        parsed = date.fromisoformat(meal["applicable_date"])
    except ValueError as error:
        raise ValueError("区域餐窗日期无效") from error
    if parsed.isoformat() != meal["applicable_date"] or (applicable_date and meal["applicable_date"] != applicable_date):
        raise ValueError("区域餐窗日期与实际事件不一致")
    timestamp(meal["checked_at"])
    if meal.get("meal_type") not in ("breakfast", "lunch", "dinner"):
        raise ValueError("区域餐窗必须声明餐别")
    if any(key in meal for key in ("candidate_ids", "selected_candidate_id", "candidates", "baseline_route_id")):
        raise ValueError("区域餐窗不能冒充已选门店或候选排序")
    area = meal.get("area_dining")
    if not isinstance(area, dict) or any(not isinstance(area.get(key), str) or not area[key].strip()
        for key in ("name", "physical_address", "why_here", "availability_basis", "route_note")):
        raise ValueError("区域餐窗缺少可定位范围、供给依据或衔接说明")
    options = area.get("food_options")
    if not isinstance(options, list) or not options or any(not isinstance(value, str) or not value.strip() for value in options):
        raise ValueError("区域餐窗必须说明有来源的正餐吃法")
    budget = area.get("budget_per_person")
    if type(budget) not in (int, float) or not isfinite(budget) or budget <= 0:
        raise ValueError("区域餐窗必须有正数的人均预算预留")
    validate_dining_fallback(area.get("fallback"))
    links = area.get("action_links")
    if not isinstance(links, list) or not links or any(not isinstance(link, dict) for link in links):
        raise ValueError("区域餐窗必须有定位入口")
    validate_links(links)
    if not any(link.get("type") == "map" for link in links):
        raise ValueError("区域餐窗必须有 map 入口，不使用酒店或商户预订页代替")
    ids = area.get("source_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(sid, str) or not sid.strip() for sid in ids):
        raise ValueError("区域餐窗必须引用已读区域供给与备用依据")
    if sources is not None:
        for sid in ids:
            if sid not in sources:
                raise ValueError("区域餐窗引用了未登记来源")
            validate_source(sources[sid])


def transport_comparison_sorts(candidate: dict[str, Any]) -> set[int]:
    context = candidate.get("selection_context")
    if context is None:
        return {2, 3, 4, 6, 7}
    if (context not in ("local_transfer", "specific_service", "user_selected", "compare_options")
            or not isinstance(candidate.get("selection_basis"), str) or not candidate["selection_basis"].strip()):
        raise ValueError("交通选择必须说明场景和依据")
    if context == "specific_service" and not (candidate.get("service_id") or candidate.get("service_or_train")):
        raise ValueError("指定交通服务必须提供班次或服务标识")
    if context != "compare_options":
        return set()
    dimensions = candidate.get("comparison_dimensions")
    mapping = {"time": {4, 6, 7}, "price": {3}, "recommendation": {2}}
    if not isinstance(dimensions, list) or not dimensions or any(not isinstance(value, str) or value not in mapping for value in dimensions):
        raise ValueError("开放交通比较必须声明 time/price/recommendation 维度")
    return set().union(*(mapping[value] for value in dimensions))


def validate_meal(meal: dict[str, Any], date: str, planning: dict[str, Any], sources: dict[str, dict[str, Any]]) -> None:
    if meal.get("mode") == "area":
        validate_area_meal(meal, date, sources)
        return
    validate_depth(meal, "meal", planning.get("research_depth_version") == 1)
    ids = meal.get("candidate_ids") or []
    policy = meal.get("candidate_policy") or {}
    if not ids or len(ids) != len(set(ids)) or policy.get("status") not in {"normal", "constrained"}:
        raise ValueError("餐窗必须有去重候选与明确候选策略")
    if policy["status"] == "normal" and not 2 <= len(ids) <= 3:
        raise ValueError("正常餐窗须有主推与备选两个实体候选")
    if policy["status"] == "constrained" and any(not policy.get(k) for k in ("restriction_reason", "search_scope", "emergency_fallback", "source_ids")):
        raise ValueError("受限餐窗须记录限制、搜索范围、补给和证据")
    selected, fallbacks = meal.get("selected_candidate_id"), meal.get("fallback_candidate_ids") or []
    if selected not in ids or (policy["status"] == "normal" and not fallbacks) or any(sid not in ids or sid == selected for sid in fallbacks):
        raise ValueError("餐窗缺少合法主选或结构化备选")
    if any(not meal.get(k) for k in ("name", "time_window", "selection_summary", "fallback_rule", "checked_at")):
        raise ValueError("餐窗缺少时间窗、选择理由或切换条件")
    for k in ("previous_anchor", "next_anchor"):
        if not all((meal.get(k) or {}).get(field) for field in ("name", "physical_address")):
            raise ValueError("餐窗锚点必须有名称和具体地址")
    restaurants = {r["id"]: r for r in planning.get("restaurants") or []}
    snapshots = {s["snapshot_id"]: s for s in planning.get("restaurant_snapshots") or []}
    evaluations = {r["id"]: r for r in planning.get("meal_route_evaluations") or []}
    baselines = [b for b in planning.get("meal_baseline_routes") or [] if b.get("meal_id") == meal.get("id")]
    if len(baselines) != 1 or baselines[0].get("id") != meal.get("baseline_route_id"):
        raise ValueError("每餐必须引用唯一同口径基准路线")
    baseline = baselines[0]
    if not same_endpoint(baseline.get("from_anchor") or {}, meal["previous_anchor"]) or not same_endpoint(baseline.get("to_anchor") or {}, meal["next_anchor"]):
        raise ValueError("基准路线与餐窗锚点不一致")
    validate_route(baseline, sources)
    bindings = meal.get("candidates") or []
    if (len(bindings) != len(ids) or {b.get("restaurant_id") for b in bindings} != set(ids)
            or sorted(b.get("rank", 0) for b in bindings) != list(range(1, len(ids) + 1))):
        raise ValueError("每个餐厅候选必须有完整连续排名和快照路线绑定")
    seen = set()
    for binding in bindings:
        rid = binding["restaurant_id"]
        restaurant = restaurants.get(rid) or {}
        if not all(restaurant.get(k) for k in ("name", "signature_dishes", "media", "action_links", "source_ids")):
            raise ValueError("餐厅候选缺少身份、特色菜、合法可视证据或入口")
        validate_links(restaurant["action_links"])
        if any(sid not in sources for sid in restaurant["source_ids"]):
            raise ValueError("餐厅引用了未登记来源")
        for media in restaurant["media"]:
            validate_links([{"url": media.get("source_url")}])
            if media.get("kind") == "display_image":
                if (any(not media.get(k) for k in ("url", "alt", "source_url", "license_or_permission", "checked_at"))
                        or not (media.get("author") or media.get("source_label"))):
                    raise ValueError("餐厅图片缺少授权或来源信息")
            elif media.get("kind") == "link_preview":
                if any(not media.get(k) for k in ("source_url", "alt", "source_label")):
                    raise ValueError("餐厅链接预览缺少原页面与说明")
            else:
                raise ValueError("餐厅 media.kind 无效")
        validate_location(restaurant, sources)
        identity = restaurant["location"]["physical_address"].strip().casefold()
        if identity in seen:
            raise ValueError("同一经营地址不能充当多个餐厅候选")
        seen.add(identity)
        if rid == selected and binding["rank"] != 1:
            raise ValueError("主选餐厅必须是 rank=1")
        snapshot = snapshots.get(binding.get("snapshot_id")) or {}
        if snapshot.get("schema_version") != "restaurant-source-snapshot/v2" or snapshot.get("restaurant_id") != rid:
            raise ValueError("餐厅 v4 必须绑定匹配的 v2 动态快照")
        if snapshot.get("applicable_date") != date or snapshot.get("time_window") != meal["time_window"]:
            raise ValueError("餐厅快照日期或餐窗不匹配")
        if not snapshot.get("expires_at") or not snapshot.get("operations") or not snapshot.get("action_links"):
            raise ValueError("餐厅快照必须有运营信息、有效期和复核入口")
        validate_links(snapshot["action_links"])
        timestamp(snapshot["checked_at"])
        timestamp(snapshot["expires_at"])
        if snapshot["expires_at"][:10] < snapshot["checked_at"][:10]:
            raise ValueError("餐厅快照有效期不得早于查询时间")
        if any(not snapshot["operations"].get(k) for k in ("opening_hours", "status", "checked_at", "recheck_at")):
            raise ValueError("餐厅营业信息必须有状态、查询时间与复核安排")
        if any(e.get("applicable_date") and e["applicable_date"] != date for e in snapshot.get("evidence") or []):
            raise ValueError("餐厅证据适用日期与本餐不一致")
        if any(e.get("aspect") in {"identity", "menu"} and e.get("status") in {"to_recheck", "estimated", "partial"} for e in snapshot.get("evidence") or []):
            raise ValueError("餐厅身份与具体菜单必须先核实，不能用未核线索充当正式候选")
        validate_evidence(snapshot.get("evidence"), sources, {"identity", "menu", "operations", "experience"})
        if any(e.get("aspect") == "operations" and e.get("status") in {"partial", "estimated", "to_recheck"} for e in snapshot["evidence"]):
            raise ValueError("采用餐厅的营业适用性尚未确认，须补查或换候选")
        evaluation = evaluations.get(binding.get("route_evaluation_id")) or {}
        if evaluation.get("restaurant_id") != rid or evaluation.get("meal_id") != meal.get("id") or evaluation.get("baseline_route_id") != baseline.get("id"):
            raise ValueError("餐厅候选路线绑定不一致")
        basis = evaluation.get("comparison_basis") or {}
        if any(not baseline.get(k) or basis.get(k) != baseline[k] for k in ("mode", "routing_policy", "departure_at")):
            raise ValueError("候选与基准路线必须使用相同方式、策略和出发时刻")
        endpoint = {"name": restaurant["name"], **restaurant["location"]}
        for key, origin, destination in (("from_previous", meal["previous_anchor"], endpoint), ("to_next", endpoint, meal["next_anchor"])):
            leg = evaluation.get(key) or {}
            if not same_endpoint(leg.get("origin") or {}, origin) or not same_endpoint(leg.get("destination") or {}, destination):
                raise ValueError("候选双腿路线必须构成上一锚点到餐厅再到下一锚点")
            validate_route(leg, sources)
        total = sum(evaluation[k]["door_to_door_minutes"] for k in ("from_previous", "to_next"))
        if any(not isinstance(evaluation.get(k), (int, float)) or isinstance(evaluation.get(k), bool) or not isfinite(evaluation[k]) for k in ("total_door_to_door_minutes", "baseline_door_to_door_minutes", "detour_minutes")):
            raise ValueError("候选总耗时和绕行必须是数值")
        detour = total - baseline["door_to_door_minutes"]
        if abs(total - evaluation["total_door_to_door_minutes"]) > .01 or abs(baseline["door_to_door_minutes"] - evaluation["baseline_door_to_door_minutes"]) > .01 or abs(detour - evaluation["detour_minutes"]) > .01:
            raise ValueError("候选双腿总耗时与统一基准绕行计算不一致")
        maximum = (meal.get("constraints") or {}).get("max_detour_minutes")
        if not isinstance(maximum, (int, float)) or isinstance(maximum, bool) or not isfinite(maximum) or maximum < 0 or detour > maximum:
            raise ValueError("候选超过最大允许绕行或缺少绕行约束")


def validate_lodging_area(item: dict[str, Any], sources: dict[str, dict[str, Any]] | None = None) -> None:
    """An area is a geographic recommendation, never a hotel quote or identity."""
    if not isinstance(item, dict):
        raise ValueError("住宿建议必须是对象")
    if item.get("kind") != "area":
        return
    label = item.get("id") or "未命名"
    if any(not isinstance(item.get(key), str) or not item[key].strip()
           for key in ("id", "name", "area", "why_here", "check_in", "check_out", "route_note")):
        raise ValueError(f"住宿区域 {label} 缺少范围、选址理由、日期或区域路线说明")
    try:
        start, end = date.fromisoformat(item["check_in"]), date.fromisoformat(item["check_out"])
    except ValueError as error:
        raise ValueError(f"住宿区域 {label} 入住和离开日期必须是 ISO 日期") from error
    if start.isoformat() != item["check_in"] or end.isoformat() != item["check_out"]:
        raise ValueError(f"住宿区域 {label} 日期必须使用 YYYY-MM-DD")
    if type(item.get("nights")) is not int or item["nights"] < 1 or (end - start).days != item["nights"]:
        raise ValueError(f"住宿区域 {label} 夜数与入住、离开日期不一致")
    location = item.get("location")
    if (not isinstance(location, dict) or location.get("precision") != "area"
            or not isinstance(location.get("address"), str) or not location["address"].strip()):
        raise ValueError(f"住宿区域 {label} 必须提供区域代表地标并标明 precision=area")
    verification = item.get("location_verification") or {}
    if not isinstance(verification, dict) or verification.get("status") == "verified":
        raise ValueError(f"住宿区域 {label} 不能标为已核验酒店身份")
    if any(key in item for key in ("inventory_refs", "quote", "price", "hotel_name", "occupancy", "requested_occupancy", "bed_type")):
        raise ValueError(f"住宿区域 {label} 不能包含酒店报价、库存或房型字段")
    actions = item.get("action_links") or []
    if not isinstance(actions, list) or any(not isinstance(action, dict) for action in actions):
        raise ValueError(f"住宿区域 {label} 操作入口必须是对象数组")
    if any(action.get("type") == "hotel" for action in actions):
        raise ValueError(f"住宿区域 {label} 不能提供酒店预订入口")
    notes = item.get("selection_notes", [])
    if not isinstance(notes, list) or any(not isinstance(note, str) or not note.strip() for note in notes):
        raise ValueError(f"住宿区域 {label} 选店条件必须是文字数组")
    ids = item.get("source_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(sid, str) or not sid.strip() for sid in ids):
        raise ValueError(f"住宿区域 {label} 必须引用区域选址依据")
    if sources is not None:
        for sid in ids:
            if sid not in sources:
                raise ValueError(f"住宿区域 {label} 引用了未登记来源")
            validate_source(sources[sid])
    if "budget" in item:
        budget = item["budget"]
        if (not isinstance(budget, dict) or budget.get("status") != "estimated"
                or not isinstance(budget.get("currency"), str) or not budget["currency"].strip()
                or not isinstance(budget.get("basis"), str) or not budget["basis"].strip()
                or type(budget.get("rooms")) is not int or budget["rooms"] < 1):
            raise ValueError(f"住宿区域 {label} 预算须注明预留状态、币种、房间数和依据")
        values = budget.get("nightly_range")
        if (not isinstance(values, list) or len(values) != 2
                or any(type(value) not in (int, float) or not isfinite(value) or value < 0 for value in values)
                or values[1] < values[0]):
            raise ValueError(f"住宿区域 {label} 每间每晚预算必须是有序非负区间")


def lodging_budget_summary(item: dict[str, Any]) -> str:
    budget = item.get("budget") or {}
    values = budget.get("nightly_range")
    if not values:
        return "选定酒店后确认预算"
    currency = "¥" if budget["currency"] == "CNY" else budget["currency"] + " "
    rooms, nights = budget["rooms"], item["nights"]
    low, high = values
    return (f"每间每晚预留 {currency}{low:g}–{high:g}；"
            f"暂按{rooms}间×{nights}晚，合计预留 {currency}{low * rooms * nights:g}–{high * rooms * nights:g}（非酒店报价）")


def source_provider(source: dict[str, Any]) -> str | None:
    name = str(source.get("provider") or "").casefold()
    host = (urlparse(str(source.get("origin_url") or source.get("url") or "")).hostname or "").casefold()
    for provider, label, domain in (("xiaohongshu", "小红书", "xiaohongshu.com"), ("amap", "高德", "amap.com")):
        if provider in name or label in name or host == domain or host.endswith("." + domain):
            return provider
    return None


def sensitive_parameters(value: Any) -> bool:
    """Check field names without ever including their values in diagnostics."""
    forbidden = {"apikey", "amapapikey", "amapmapsapikey", "cookie", "cookies", "authorization",
                 "password", "token", "accesstoken", "refreshtoken", "xsectoken"}
    if isinstance(value, dict):
        return any(str(key).casefold().replace("_", "").replace("-", "") in forbidden
                   or sensitive_parameters(item) for key, item in value.items())
    if isinstance(value, list):
        return any(sensitive_parameters(item) for item in value)
    return False


def audit_query_design(contract: dict[str, Any], sources: dict[str, dict[str, Any]]) -> list[str]:
    """Validate traceability; keyword relevance remains a semantic review."""
    if "query_design_version" not in contract:
        return []
    if type(contract["query_design_version"]) is not int or contract["query_design_version"] != 1:
        return ["查询设计 query_design_version 必须是1"]
    blocking = []
    queries = contract["queries"]
    ids = [q.get("id") for q in queries if isinstance(q.get("id"), str) and q["id"].strip()]
    if len(set(ids)) != len(ids):
        blocking.append("单次查询 ID 不能重复，不得合并多次调用")
    for q in queries:
        label = q.get("id") if isinstance(q.get("id"), str) else "未命名"
        if any(not isinstance(q.get(k), str) or not q[k].strip()
               for k in ("id", "purpose", "provider", "tool", "outcome")):
            blocking.append(f"查询 {label} 缺少问题、平台、工具或结果取舍")
        if q.get("stage") not in ("discovery", "detail", "verification"):
            blocking.append(f"查询 {label} 缺少发现、详情或核验阶段")
        parameters = q.get("parameters")
        if not isinstance(parameters, dict) or not parameters:
            blocking.append(f"查询 {label} 必须记录实际非敏感参数")
        else:
            if sensitive_parameters(parameters):
                blocking.append(f"查询 {label} 参数包含禁止归档的敏感字段")
            for key in ("q", "keyword", "keywords"):
                if key in parameters and parameters[key] != q.get("query"):
                    blocking.append(f"查询 {label} 关键词与实际参数不一致，不得事后拼接")
        if "result_count" in q and (not isinstance(q["result_count"], int)
                                    or isinstance(q["result_count"], bool) or q["result_count"] < 0):
            blocking.append(f"查询 {label} result_count 必须是非负整数")
        if "parent_query_id" in q and (not isinstance(q["parent_query_id"], str)
                                      or q["parent_query_id"] not in ids or q["parent_query_id"] == q.get("id")):
            blocking.append(f"查询 {label} 补查引用无效")
        source_ids = q.get("source_ids") or []
        if not isinstance(source_ids, list):
            continue
        for sid in source_ids:
            if not isinstance(sid, str) or sid not in sources:
                continue
            acquisition = sources[sid].get("acquisition")
            allowed = ("web_search", "browser") if q.get("channel") in ("web_search", "browser") else (q.get("channel"),)
            if acquisition not in allowed:
                blocking.append(f"查询 {label} 与来源 {sid} 的获取渠道不一致")
            provider = source_provider(sources[sid])
            if q.get("channel") == "mcp" and provider and q.get("provider") != provider:
                blocking.append(f"查询 {label} 与来源 {sid} 的平台归属不一致")
    for sid, source in sources.items():
        provider = source_provider(source)
        if source.get("acquisition") == "mcp" and provider:
            if not any(q.get("channel") == "mcp" and q.get("provider") == provider
                       and isinstance(q.get("source_ids"), list) and sid in q["source_ids"] for q in queries):
                blocking.append(f"来源 {sid} 缺少对应 {provider} 实际查询记录")
    return blocking


def audit_research(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    planning = data.get("planning") or {}
    search_blocking, search_warnings = xhs_search_contract.audit_searches(data)
    contract = planning.get("research_contract")
    if contract is None:
        if planning.get("restaurant_research_version") == 4:
            return ["餐厅 v4 必须记录 research_contract 多来源研究契约"], []
        return search_blocking, search_warnings
    blocking, warnings = search_blocking, search_warnings
    if not isinstance(contract, dict) or contract.get("version") != 1:
        return ["多来源研究契约 version 必须是1"], []
    for key in ("queries", "decisions"):
        if not isinstance(contract.get(key), list) or any(not isinstance(item, dict) for item in contract[key]):
            return [f"多来源研究 {key} 必须是对象数组"], []
    if not isinstance(contract.get("capabilities"), dict):
        return ["多来源研究 capabilities 必须是对象"], []
    if any(not isinstance(item, dict) or not item.get("id") for item in data.get("sources") or []):
        return ["多来源研究来源必须是带 ID 的对象"], []
    sources = {s.get("id"): s for s in data.get("sources") or []}
    if len(sources) != len(data.get("sources") or []):
        blocking.append("来源 ID 不能重复")
    capabilities = contract.get("capabilities") or {}
    if any(capabilities.get(k) not in {"ready", "unavailable"} for k in ("web_search", "xiaohongshu", "amap")):
        blocking.append("须分别记录网页搜索、小红书、高德的实际能力状态")
    queries = contract.get("queries") or []
    blocking.extend(audit_query_design(contract, sources))
    web_queries = [q for q in queries if q.get("channel") == "web_search"]
    if capabilities.get("web_search") == "ready" and not web_queries:
        blocking.append("网页搜索可用时必须主动研究，不能仅使用小红书和高德")
    for q in queries:
        if (not isinstance(q.get("scope"), str) or q["scope"] not in {"play", "eat", "operations", "route", "general"}
                or not isinstance(q.get("channel"), str) or q["channel"] not in CHANNELS
                or not isinstance(q.get("query"), str) or not q["query"].strip()
                or q.get("status") not in ("success", "no_results", "failed")):
            blocking.append("研究查询缺少渠道、查询条件或执行状态")
        try:
            timestamp(q.get("checked_at"))
        except ValueError as error:
            blocking.append(str(error))
        if q.get("status") != "success" and not q.get("reason"):
            blocking.append("失败或无结果的查询必须说明原因")
        if q.get("status") == "success" and not q.get("source_ids") and (contract.get("query_design_version") != 1 or q.get("stage") != "discovery"):
            blocking.append("成功研究查询必须引用实际读过的来源")
        if not isinstance(q.get("source_ids", []), list):
            blocking.append("研究查询 source_ids 必须是数组")
            continue
        if any(not isinstance(sid, str) or sid not in sources for sid in q.get("source_ids") or []):
            blocking.append("研究查询引用了未登记来源")
        if q.get("status") == "success":
            for sid in q.get("source_ids") or []:
                if isinstance(sid, str) and sid in sources:
                    try:
                        validate_source(sources[sid])
                    except ValueError as error:
                        blocking.append(f"研究查询 {q.get('query')}：{error}")
        if q.get("status") == "success" and q.get("channel") == "web_search" and q.get("source_ids"):
            if not any(sources.get(sid, {}).get("acquisition") in {"web_search", "browser"} for sid in q.get("source_ids") or []):
                blocking.append("网页研究必须有实际公开原页面来源，不能只绑定 MCP 结果")
    adopted = set()
    attraction_targets = set()
    for day in data.get("days") or []:
        for event in day.get("events") or []:
            if event.get("type") == "attraction":
                adopted.add(event.get("attraction_id"))
                attraction_targets.add(event.get("attraction_id"))
            elif event.get("type") == "food_walk":
                adopted.add(event.get("id"))
            elif event.get("experience") is True:
                adopted.add(event.get("id"))
            if event.get("meal_id"):
                meal = next((m for m in planning.get("meal_options") or [] if m.get("id") == event["meal_id"]), {})
                adopted.add(meal.get("selected_candidate_id"))
            for point in (event.get("execution") or {}).get("checkpoints") or []:
                if point.get("meal_id"):
                    meal = next((m for m in planning.get("meal_options") or [] if m.get("id") == point["meal_id"]), {})
                    adopted.add(meal.get("selected_candidate_id"))
    if capabilities.get("web_search") == "ready":
        required_scopes = set()
        if attraction_targets or any(e.get("type") == "food_walk" or e.get("experience") is True for d in data.get("days") or [] for e in d.get("events") or []):
            required_scopes.add("play")
        if planning.get("meal_options") or any(e.get("type") == "food_walk" for d in data.get("days") or [] for e in d.get("events") or []):
            required_scopes.add("eat")
        if required_scopes - {q.get("scope") for q in web_queries if isinstance(q.get("scope"), str)}:
            blocking.append("网页研究必须覆盖本行程玩什么与吃什么的实际需求")
    decisions = contract.get("decisions") or []
    if len({d.get("target_id") for d in decisions}) != len(decisions):
        blocking.append("同一推荐对象只能有一个当前决策")
    for target in adopted - {None}:
        decision = next((d for d in decisions if d.get("target_id") == target), {})
        if not decision.get("user_value") or not decision.get("tradeoff"):
            blocking.append(f"推荐 {target} 缺少用户价值与取舍")
        try:
            required = {"experience", "admission", "identity"} if target in attraction_targets else {"experience"}
            validate_evidence(decision.get("evidence"), sources, required)
            if target in attraction_targets and any(e.get("aspect") == "admission" and e.get("status") in {"partial", "estimated", "to_recheck"} for e in decision["evidence"]):
                raise ValueError("采用景点的开放或入场适用性尚未确认，须补查或调整安排")
        except ValueError as error:
            blocking.append(f"推荐 {target}：{error}")
        if decision.get("conflicts") and not decision.get("resolution"):
            blocking.append(f"推荐 {target} 存在证据冲突但未记录裁决")
        evidence = decision.get("evidence") or []
        if not isinstance(evidence, list) or any(not isinstance(e, dict) or not isinstance(e.get("source_ids", []), list) for e in evidence):
            continue
        ids = {sid for e in evidence if e.get("aspect") == "experience" for sid in e.get("source_ids") or [] if isinstance(sid, str) and sid in sources}
        originals = {source_identity(sources[sid]) for sid in ids}
        original_urls = {(urlparse(str(sources[sid].get("origin_url") or sources[sid].get("url"))).hostname,
                          urlparse(str(sources[sid].get("origin_url") or sources[sid].get("url"))).path.rstrip("/")) for sid in ids}
        independent_count = min(len(originals), len(original_urls))
        if len(ids) > len(originals):
            warnings.append(f"推荐 {target} 存在同一发布者、原文或转载，不能累计为独立共识")
        if decision.get("consensus") and independent_count < 2:
            blocking.append(f"推荐 {target} 的共识缺少独立原始证据")
    if capabilities.get("web_search") == "unavailable":
        if not contract.get("web_unavailable_reason"):
            blocking.append("网页搜索不可用必须说明具体原因")
        warnings.append("网页搜索不可用，未取得的动态信息不能声称已核验")
    return blocking, warnings
