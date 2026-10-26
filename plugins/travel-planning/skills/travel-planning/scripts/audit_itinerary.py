#!/usr/bin/env python3
"""Audit a merged itinerary before rendering the final HTML artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


RENDERER_PATH = Path(__file__).with_name("render_itinerary.py")
SPEC = importlib.util.spec_from_file_location("render_itinerary", RENDERER_PATH)
assert SPEC and SPEC.loader
render_itinerary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render_itinerary)

COMMENT_SPEC = importlib.util.spec_from_file_location("xhs_comment_contract", Path(__file__).with_name("xhs_comment_contract.py"))
assert COMMENT_SPEC and COMMENT_SPEC.loader
xhs_comment_contract = importlib.util.module_from_spec(COMMENT_SPEC)
COMMENT_SPEC.loader.exec_module(xhs_comment_contract)

REPORT_PHRASES = ("综合考虑", "总体而言", "值得一去", "丰富体验", "感受当地", "合理安排", "行程亮点")
TRANSPORT_COVERAGE_SORTS = {2, 3, 4, 6, 7}
STRONG_AVAILABILITY_CLAIMS = {"available", "confirmed", "verified", "booked", "有房", "可订", "已确认"}
COMMUNITY_EVIDENCE_STATUSES = {"available", "partial", "unavailable", "to_recheck"}
COMMUNITY_CONFIDENCE_LEVELS = {"low", "medium"}
COMMUNITY_TARGET_TYPES = {
    "attraction", "restaurant", "lodging", "place", "transport_hub",
    "route_anchor", "transport_edge", "meal", "route", "weather", "trip",
}
COMMUNITY_RECHECK_KEYWORDS = (
    "开放", "施工", "营业", "价格", "预约", "购票", "交通", "停车", "安全",
    "open", "construction", "hours", "price", "reservation", "ticket",
    "transport", "parking", "safety",
)


def minute_of(value: Any) -> int | None:
    text = str(value or "")
    parts = text.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def minute_range(value: Any) -> tuple[int, int] | None:
    text = str(value or "").replace("—", "–").replace("-", "–")
    parts = [part.strip() for part in text.split("–")]
    if len(parts) != 2:
        return None
    start, end = minute_of(parts[0]), minute_of(parts[1])
    if start is None or end is None or end < start:
        return None
    return start, end


def event_text(event: dict[str, Any]) -> str:
    values: list[str] = []
    for field in ("title", "subtitle"):
        if event.get(field):
            values.append(str(event[field]))
    for field in ("details", "tips"):
        items = event.get(field)
        if isinstance(items, list):
            values.extend(item for item in items if isinstance(item, str))
    return " ".join(values)


def transport_coverage_key(snapshot: dict[str, Any]) -> str | None:
    if snapshot.get("snapshot_kind") != "quote" or snapshot.get("product_type") not in {"flight", "train"}:
        return None
    query = snapshot.get("query") or {}
    if query.get("transport_no"):
        return None
    identifying = {
        key: query.get(key)
        for key in ("origin", "destination", "dep_date", "journey_type", "seat_class_name")
    }
    return json.dumps(identifying, ensure_ascii=False, sort_keys=True)


def normalize_community_evidence(item: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"community_evidence[{index}] 必须是对象")
    value = dict(item)
    if not value.get("insight") and value.get("claim"):
        value["insight"] = value["claim"]
    if not value.get("community_confidence") and value.get("confidence"):
        value["community_confidence"] = value["confidence"]
    return value


def community_evidence_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("target_entity_id") or ""),
        str(item.get("target_entity_type") or ""),
        str(item.get("topic") or "").strip().casefold(),
    )


def audit_community_evidence(
    data: dict[str, Any],
    planning: dict[str, Any],
    phase: str | None,
) -> tuple[list[str], list[str], dict[str, int]]:
    blocking: list[str] = []
    warnings: list[str] = []
    evidence_items = planning.get("community_evidence") or []
    conflicts = planning.get("community_evidence_conflicts") or []
    sources = [source for source in data.get("sources") or [] if isinstance(source, dict)]
    source_ids = {
        str(source.get("id"))
        for source in sources
        if source.get("id")
    }
    xhs_source_ids: set[str] = set()
    for source in sources:
        if source.get("kind") != "community":
            continue
        provider = str(source.get("provider") or "")
        if "小红书" not in provider and "xiaohongshu" not in provider.lower() and "小红书" not in str(source.get("title") or ""):
            continue
        xhs_source_ids.add(str(source.get("id")))
        parsed = urlparse(str(source.get("url") or ""))
        if parsed.hostname in {"www.xiaohongshu.com", "xiaohongshu.com"} and parsed.path.rstrip("/") in {"", "/explore"}:
            message = f"小红书来源“{source.get('title') or source.get('id')}”仅链接平台首页，不能作为已读原帖引用"
            if phase == "final":
                blocking.append(message)
            else:
                warnings.append(message)
    known_entity_ids = {
        str(item.get("id"))
        for collection_name in (
            "attractions", "restaurants", "restaurant_snapshots",
            "transport_edges", "intercity_options", "lodging_options",
            "meal_options", "weather",
        )
        for item in planning.get(collection_name) or []
        if isinstance(item, dict) and item.get("id")
    }
    for item in planning.get("restaurants") or []:
        if isinstance(item, dict) and item.get("restaurant_id"):
            known_entity_ids.add(str(item["restaurant_id"]))

    normalized: list[dict[str, Any]] = []
    needs_recheck_count = 0
    official_warning_count = 0
    source_closure_issue_count = 0
    for index, raw_item in enumerate(evidence_items):
        try:
            item = normalize_community_evidence(raw_item, index)
        except ValueError as error:
            blocking.append(str(error))
            continue
        normalized.append(item)
        prefix = f"社区证据[{index}]"
        required = (
            "target_entity_id", "target_entity_type", "provider", "checked_at",
            "topic", "insight", "support_count", "contradiction_count",
            "community_confidence", "needs_official_recheck", "source_ids",
        )
        missing = [field for field in required if item.get(field) in (None, "")]
        if missing:
            blocking.append(f"{prefix}缺少字段：{'、'.join(missing)}")
            continue
        target_id = str(item["target_entity_id"])
        if target_id not in known_entity_ids:
            blocking.append(f"{prefix}引用了未装配的目标实体：{target_id}")
        if item["target_entity_type"] not in COMMUNITY_TARGET_TYPES:
            blocking.append(f"{prefix}的目标实体类型无效：{item['target_entity_type']}")
        if not str(item["provider"]).strip():
            blocking.append(f"{prefix}.provider 不能为空")
        if item.get("status") and item["status"] not in COMMUNITY_EVIDENCE_STATUSES:
            blocking.append(f"{prefix}.status 无效")
        if item.get("community_confidence") not in COMMUNITY_CONFIDENCE_LEVELS:
            blocking.append(f"{prefix}的社区置信度无效或越界")
        if item.get("community_confidence") == "high":
            blocking.append(f"{prefix}不得使用 high 社区置信度")
        for field in ("support_count", "contradiction_count"):
            value = item.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                blocking.append(f"{prefix}.{field} 必须是非负整数")
        if item.get("contradiction_count", 0) > 0 and item.get("community_confidence") != "low":
            blocking.append(f"{prefix}存在冲突但社区置信度不是 low")
        if item.get("support_count", 0) <= 1 and item.get("community_confidence") != "low":
            blocking.append(f"{prefix}只有单条证据但社区置信度不是 low")
        if not isinstance(item.get("needs_official_recheck"), bool):
            blocking.append(f"{prefix}.needs_official_recheck 必须是布尔值")
        item_source_ids = item.get("source_ids")
        if not isinstance(item_source_ids, list) or not item_source_ids:
            blocking.append(f"{prefix}.source_ids 必须是非空数组")
        else:
            missing_sources = sorted(str(value) for value in item_source_ids if str(value) not in source_ids)
            if missing_sources:
                source_closure_issue_count += 1
                blocking.append(f"{prefix}引用了未登记来源：{'、'.join(missing_sources)}")
        try:
            checked_at = datetime.fromisoformat(str(item["checked_at"]).replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.astimezone()
        except (KeyError, TypeError, ValueError):
            blocking.append(f"{prefix}.checked_at 时间格式无效")
        searchable = f"{item.get('topic', '')} {item.get('insight', '')}".casefold()
        critical_topic = any(keyword.casefold() in searchable for keyword in COMMUNITY_RECHECK_KEYWORDS)
        if critical_topic and item.get("needs_official_recheck") is not True:
            official_warning_count += 1
            message = f"{prefix}涉及开放、营业、价格、预约、交通或安全，但未标记官方复核"
            if phase == "final":
                blocking.append(message)
            else:
                warnings.append(message)
        elif item.get("needs_official_recheck") is True:
            needs_recheck_count += 1
            official_warning_count += 1
            warnings.append(f"{prefix}仍需官方或实时来源复核：{item.get('topic')}")

    attraction_ids = {
        str(event.get("attraction_id"))
        for day in data.get("days") or []
        for event in day.get("events") or []
        if isinstance(event, dict) and event.get("type") == "attraction" and event.get("attraction_id")
    }
    attractions_with_xhs = {
        str(item.get("target_entity_id"))
        for item in normalized
        if str(item.get("target_entity_id")) in attraction_ids
        and xhs_source_ids.intersection(map(str, item.get("source_ids") or []))
    }
    if xhs_source_ids and attraction_ids and not attractions_with_xhs:
        warnings.append("行程已使用小红书，但所有采用景点都缺少已读原帖的玩法体验依据；请补查或记录具体不可用原因")

    evidence_by_key = {community_evidence_key(item): item for item in normalized}
    medium_conflict_count = 0
    for index, conflict in enumerate(conflicts):
        if not isinstance(conflict, dict):
            blocking.append(f"community_evidence_conflicts[{index}] 必须是对象")
            continue
        key = (
            str(conflict.get("target_entity_id") or ""),
            str(conflict.get("target_entity_type") or ""),
            str(conflict.get("topic") or "").strip().casefold(),
        )
        selected = evidence_by_key.get(key) or conflict.get("selected_value") or {}
        if selected.get("community_confidence") == "medium":
            medium_conflict_count += 1
            blocking.append(
                f"社区证据主题“{conflict.get('topic') or key[2]}”存在冲突，不得保持 medium 置信度"
            )
        conflict_sources = [
            *(conflict.get("previous_source_ids") or []),
            *(conflict.get("incoming_source_ids") or []),
            *(conflict.get("selected_source_ids") or []),
        ]
        missing_conflict_sources = sorted(
            str(value) for value in conflict_sources if str(value) not in source_ids
        )
        if missing_conflict_sources:
            source_closure_issue_count += 1
            blocking.append(
                f"社区证据冲突引用了未登记来源：{'、'.join(dict.fromkeys(missing_conflict_sources))}"
            )

    return blocking, warnings, {
        "evidence_count": len(evidence_items),
        "conflict_count": len(conflicts),
        "needs_official_recheck_count": needs_recheck_count,
        "official_recheck_warning_count": official_warning_count,
        "source_closure_issue_count": source_closure_issue_count,
        "medium_conflict_count": medium_conflict_count,
        "attractions_with_xhs_evidence_count": len(attractions_with_xhs),
    }


def audit(data: dict[str, Any], workspace: Path | None = None) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    try:
        render_itinerary.validate_data(data, workspace)
    except ValueError as error:
        blocking.append(str(error))

    planning = data.get("planning") or {}
    research_blocking, research_warnings = render_itinerary.research_evidence.audit_research(data)
    blocking.extend(research_blocking)
    warnings.extend(research_warnings)
    community_blocking, community_warnings, community_stats = audit_community_evidence(
        data, planning, (data.get("workflow") or {}).get("phase")
    )
    blocking.extend(community_blocking)
    warnings.extend(community_warnings)
    comment_blocking, comment_warnings, comment_stats = xhs_comment_contract.audit_reviews(data, workspace)
    blocking.extend(comment_blocking)
    warnings.extend(comment_warnings)
    community_stats["comment_research"] = comment_stats
    meals = {item.get("id"): item for item in planning.get("meal_options") or []}
    source_snapshots = {
        str(item.get("snapshot_id")): item
        for item in planning.get("source_snapshots") or []
        if item.get("snapshot_id")
    }
    inventory_candidates = {
        str(item.get("id")): item
        for item in [
            *(planning.get("transport_edges") or []),
            *(planning.get("intercity_options") or []),
            *(planning.get("lodging_options") or []),
        ]
        if item.get("id")
    }
    selected_inventory_refs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    checked_event_ids: list[str] = []
    used_meal_ids: set[str] = set()

    for day in data.get("days") or []:
        events = day.get("events") or []
        day_name = day.get("date") or day.get("label") or "未命名日期"
        previous_end: int | None = None
        timed_events: list[tuple[int, int, dict[str, Any]]] = []
        for event in events:
            if event.get("id"):
                checked_event_ids.append(str(event["id"]))
            elif (data.get("workflow") or {}).get("phase") in {"confirmed_planning", "final"}:
                blocking.append(f"{day_name} 事件“{event.get('title') or '未命名'}”缺少稳定 id")
            if event.get("type") == "attraction":
                execution = event.get("execution") or {}
                checkpoints = execution.get("checkpoints") or []
                if not checkpoints:
                    blocking.append(f"{day_name} 景点“{event.get('title') or '未命名'}”缺少必达卡点和内部顺序")
                orders = [point.get("order") for point in checkpoints]
                if checkpoints and orders != list(range(1, len(checkpoints) + 1)):
                    blocking.append(f"{day_name} 景点“{event.get('title') or '未命名'}”的 checkpoint order 必须从1连续递增")
                checkpoint_previous_end: int | None = None
                event_start = minute_of(event.get("time"))
                event_end = minute_of(event.get("end_time"))
                for point in checkpoints:
                    point_start = minute_of(point.get("time"))
                    point_end = minute_of(point.get("end_time"))
                    point_name = point.get("name") or point.get("id") or "未命名节点"
                    if point_start is None or point_end is None:
                        blocking.append(f"{day_name} 景点节点“{point_name}”必须提供 HH:MM 的 time 和 end_time")
                        continue
                    if point_end < point_start:
                        blocking.append(f"{day_name} 景点节点“{point_name}”的 end_time 早于 time")
                    if checkpoint_previous_end is not None and point_start < checkpoint_previous_end:
                        blocking.append(f"{day_name} 景点节点“{point_name}”与前一节点时间重叠")
                    if event_start is not None and point_start < event_start:
                        blocking.append(f"{day_name} 景点节点“{point_name}”早于外层景点事件")
                    if event_end is not None and point_end > event_end:
                        blocking.append(f"{day_name} 景点节点“{point_name}”晚于外层景点事件")
                    checkpoint_previous_end = point_end
                    for image in point.get("images") or []:
                        required_image = {"url", "alt", "source_url", "license"}
                        missing_image = sorted(field for field in required_image if not image.get(field))
                        if missing_image or not (image.get("author") or image.get("source_label")):
                            blocking.append(f"{day_name} 景点节点“{point_name}”的图片缺少 alt、作者/机构、许可或原始页面")
                    if point.get("meal_id"):
                        used_meal_ids.add(str(point["meal_id"]))
            start = minute_of(event.get("time"))
            end = minute_of(event.get("end_time"))
            if start is None:
                blocking.append(f"{day_name} 事件“{event.get('title') or '未命名'}”的 time 必须是 HH:MM")
                continue
            if previous_end is not None and start < previous_end:
                blocking.append(f"{day_name} 事件“{event.get('title') or '未命名'}”与前一事件时间重叠或顺序倒置")
            if end is not None and end < start:
                blocking.append(f"{day_name} 事件“{event.get('title') or '未命名'}”的 end_time 早于 time")
            timed_events.append((start, end if end is not None else start, event))
            previous_end = end if end is not None else start

            if event.get("type") == "meal" and event.get("meal_id"):
                meal_id = str(event["meal_id"])
                used_meal_ids.add(meal_id)
                meal = meals.get(meal_id) or {}
                allowed = minute_range(meal.get("time_window"))
                if end is None:
                    blocking.append(f"{day_name} 餐饮事件“{event.get('title') or meal_id}”必须提供 end_time")
                elif allowed and (start < allowed[0] or end > allowed[1]):
                    blocking.append(
                        f"{day_name} 餐饮事件“{event.get('title') or meal_id}”的事件时间超出 meal_option.time_window"
                    )

            candidate_id = event.get("route_id") if event.get("type") == "transport" else event.get("lodging_id") if event.get("type") == "lodging" else None
            candidate = inventory_candidates.get(str(candidate_id or "")) or {}
            for ref in candidate.get("inventory_refs") or []:
                selected_inventory_refs.append((candidate, ref))

            text = event_text(event)
            found = [phrase for phrase in REPORT_PHRASES if phrase in text]
            if found:
                warnings.append(f"{day_name} 事件“{event.get('title') or '未命名'}”含汇报式措辞：{'、'.join(found)}")

        if not timed_events:
            continue
        day_start = min(start for start, _, _ in timed_events)
        day_end = max(end for _, end, _ in timed_events)
        exemptions = day.get("meal_exemptions") or {}
        for meal_type, center in (("午餐", 12 * 60 + 30), ("晚餐", 19 * 60)):
            if not (day_start <= center <= day_end):
                continue
            exemption = exemptions.get(meal_type)
            if exemption:
                if not planning.get("research_contract"):
                    continue
                registry = {s.get("id"): s for s in data.get("sources") or []}
                valid_exemption = False
                if isinstance(exemption, dict) and exemption.get("kind") in {"onboard", "service_area", "restricted_supply", "user_arranged"}:
                    try:
                        if not all(exemption.get(k) for k in ("reason", "plan", "recheck_at")):
                            raise ValueError("缺少补给安排")
                        render_itinerary.research_evidence.timestamp(exemption["recheck_at"])
                        if exemption["kind"] == "user_arranged":
                            confirmation = exemption.get("user_confirmation") or {}
                            if not all(confirmation.get(k) for k in ("archive_ref", "quoted_instruction", "confirmed_at")):
                                raise ValueError("缺少用户明确安排的归档记录")
                            ref = str(confirmation["archive_ref"])
                            if not ref.startswith("evidence/main/") or ".." in ref or not ref.endswith(".json"):
                                raise ValueError("用户安排必须引用当前工作区主 Agent 的证据档案")
                            render_itinerary.research_evidence.timestamp(confirmation["confirmed_at"])
                        else:
                            ids = exemption.get("source_ids")
                            if not isinstance(ids, list) or not ids:
                                raise ValueError("缺少补给来源")
                            for sid in ids:
                                if sid not in registry:
                                    raise ValueError("补给来源未登记")
                                render_itinerary.research_evidence.validate_source(registry[sid])
                        valid_exemption = True
                    except ValueError:
                        pass
                if valid_exemption:
                    continue
                blocking.append(f"{day_name} {meal_type}豁免必须有明确场景、可执行补给、来源与复核时间，不能用自选或待核代替研究")
            meal_option_type = {"早餐": "breakfast", "午餐": "lunch", "晚餐": "dinner"}[meal_type]
            has_meal = any(
                (event.get("type") == "meal" and (meals.get(event.get("meal_id")) or {}).get("meal_type") in (meal_type, meal_option_type))
                or (event.get("type") == "food_walk" and (event.get("food_walk") or {}).get("meal_type") == meal_type)
                for _, _, event in timed_events
            )
            if not has_meal:
                blocking.append(f"{day_name} 跨过{meal_type}窗口但没有绑定 meal_id 的{meal_type}事件或明确替代该餐的逛吃事件")

    blocking = list(dict.fromkeys(blocking))
    unused_meals = sorted(str(meal_id) for meal_id in meals if meal_id not in used_meal_ids)
    if unused_meals:
        warnings.append(f"存在未绑定到事件或景点节点的 meal_options：{'、'.join(unused_meals)}")
    warnings = list(dict.fromkeys(warnings))
    now_value = datetime.now().astimezone()
    expired_snapshot_ids: set[str] = set()
    checked_inventory_refs: set[tuple[str, str]] = set()
    coverage_by_query: dict[str, set[int]] = {}
    for snapshot in source_snapshots.values():
        key = transport_coverage_key(snapshot)
        sort_type = (snapshot.get("query") or {}).get("sort_type")
        if key and isinstance(sort_type, int):
            coverage_by_query.setdefault(key, set()).add(sort_type)
    selected_coverage_keys: set[str] = set()
    required_sorts_by_key: dict[str, set[int]] = {}
    coverage_gaps: list[str] = []
    lodging_verification_issues: list[str] = []
    for candidate, ref in selected_inventory_refs:
        coverage_snapshot = source_snapshots.get(str(ref.get("snapshot_id") or "")) or {}
        candidate_key = transport_coverage_key(coverage_snapshot)
        if candidate_key:
            try:
                sorts = render_itinerary.research_evidence.transport_comparison_sorts(candidate)
                if candidate.get("selection_context") == "local_transfer":
                    raise ValueError("跨城报价候选不能用 local_transfer 跳过比较场景")
                required_sorts_by_key.setdefault(candidate_key, set()).update(sorts)
            except ValueError as error:
                blocking.append(str(error))
        snapshot_id = str(ref.get("snapshot_id") or "")
        offer_id = str(ref.get("offer_id") or "")
        if (snapshot_id, offer_id) in checked_inventory_refs:
            continue
        checked_inventory_refs.add((snapshot_id, offer_id))
        snapshot = source_snapshots.get(snapshot_id) or {}
        item = next(
            (entry for entry in snapshot.get("items") or [] if str(entry.get("offer_id") or "") == offer_id),
            {},
        )
        coverage_key = transport_coverage_key(snapshot)
        if coverage_key:
            selected_coverage_keys.add(coverage_key)
        if snapshot.get("product_type") == "hotel":
            requested = (snapshot.get("query") or {}).get("requested_occupancy") or {}
            requirement = candidate.get("room_requirement") or {}
            requested_rooms = requested.get("rooms")
            requested_adults = requested.get("adults")
            if not requested_rooms or not requested_adults:
                lodging_verification_issues.append(
                    f"住宿候选“{candidate.get('id') or '未命名'}”的报价快照没有记录成人数和房间数"
                )
            elif requirement and (
                requested_rooms != requirement.get("rooms")
                or requested_adults != requirement.get("travelers")
            ):
                lodging_verification_issues.append(
                    f"住宿候选“{candidate.get('id') or '未命名'}”的报价人数/房间数与行程需求不一致"
                )
            availability = item.get("availability") or {}
            selection_status = str(candidate.get("selection_status") or "").casefold()
            if availability.get("remaining") is None and selection_status in STRONG_AVAILABILITY_CLAIMS:
                blocking.append(
                    f"住宿候选“{candidate.get('id') or '未命名'}”没有多间同房型库存证据，不能标记为有房或已确认"
                )
            location_verification = candidate.get("location_verification") or {}
            if location_verification.get("status") != "verified":
                lodging_verification_issues.append(
                    f"住宿候选“{candidate.get('id') or '未命名'}”尚未完成酒店全名、城市/行政区、地址和坐标核验"
                )
        expires_at = (snapshot.get("freshness") or {}).get("expires_at")
        if expires_at:
            try:
                expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if expires < now_value:
                    expired_snapshot_ids.add(snapshot_id)
            except ValueError:
                pass
        if snapshot.get("product_type") == "train" and (
            candidate.get("country_code") == "CN"
            or (candidate.get("rail_verification") or {}).get("channel") == "12306"
        ):
            verification = candidate.get("rail_verification") or {}
            if not verification.get("action_link") or verification.get("channel") != "12306":
                blocking.append(f"铁路候选“{candidate.get('id') or '未命名'}”缺少 12306 最终复核入口")
            elif (data.get("workflow") or {}).get("phase") == "final" and verification.get("status") != "verified":
                blocking.append(f"最终铁路候选“{candidate.get('id') or '未命名'}”尚未完成 12306 复核")
    for key in sorted(selected_coverage_keys):
        missing = sorted(required_sorts_by_key.get(key, TRANSPORT_COVERAGE_SORTS) - coverage_by_query.get(key, set()))
        if missing:
            coverage_gaps.append(f"所选开放式交通查询缺少排序覆盖：{','.join(str(value) for value in missing)}")
    phase = (data.get("workflow") or {}).get("phase")
    for message in coverage_gaps + lodging_verification_issues:
        if phase == "final":
            blocking.append(message)
        else:
            warnings.append(message)
    if expired_snapshot_ids:
        message = f"已选酒旅报价或运行快照已过期：{'、'.join(sorted(expired_snapshot_ids))}"
        if (data.get("workflow") or {}).get("phase") == "final":
            blocking.append(message)
        else:
            warnings.append(message)
    blocking = list(dict.fromkeys(blocking))
    warnings = list(dict.fromkeys(warnings))
    return {
        "status": "pass" if not blocking else "fail",
        "blocking": blocking,
        "warnings": warnings,
        "restaurant_audit": {
            "meal_slot_count": len(meals),
            "used_meal_slot_count": len(used_meal_ids),
            "candidate_reference_count": sum(len(meal.get("candidate_ids") or []) for meal in meals.values()),
            "restaurant_count": len(planning.get("restaurants") or []),
            "snapshot_count": len(planning.get("restaurant_snapshots") or []),
            "baseline_route_count": len(planning.get("meal_baseline_routes") or []),
            "route_evaluation_count": len(planning.get("meal_route_evaluations") or []),
            "community_reference_count": sum(
                len((snapshot.get("community_consensus") or {}).get("references") or [])
                for snapshot in planning.get("restaurant_snapshots") or []
            ),
            "source_registry_count": len(data.get("sources") or []),
        },
        "community_audit": community_stats,
        "inventory_audit": {
            "snapshot_count": len(source_snapshots),
            "selected_reference_count": len(checked_inventory_refs),
            "expired_snapshot_ids": sorted(expired_snapshot_ids),
            "transport_coverage_gaps": coverage_gaps,
            "lodging_verification_issues": lodging_verification_issues,
        },
        "checked_event_ids": checked_event_ids,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workspace", type=Path, help="可选执行计时工作区；默认从输入路径寻找")
    args = parser.parse_args()
    workspace = args.workspace or render_itinerary.execution_log.infer_workspace(args.input)
    with render_itinerary.execution_log.measured(workspace, "audit", "audit") as span:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = audit(data, workspace)
        span["status"] = "success" if result["status"] == "pass" else "failed"
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    raise SystemExit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
