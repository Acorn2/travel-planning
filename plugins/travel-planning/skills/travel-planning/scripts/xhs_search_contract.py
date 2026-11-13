"""Open discovery before targeted travel searches, with durable query records."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

SPEC = importlib.util.spec_from_file_location("execution_log", Path(__file__).with_name("execution_log.py"))
assert SPEC and SPEC.loader
execution_log = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(execution_log)

SUFFIXES = {"eat": ("美食推荐", "小吃推荐"), "play": ("旅游推荐", "旅游攻略")}
DETAIL_SAMPLE_TOTAL = 15
DETAIL_SAMPLE_PER_SCOPE = 5
RESEARCH_MODES = {"optional", "required"}
DURATION = re.compile(r"(?:[0-9０-９一二两三四五六七八九十百]+\s*(?:天|日|晚|夜)|[0-9]+\s*[-–~至]\s*[0-9]+\s*(?:天|日)|\b[0-9]+\s*(?:days?|nights?)\b)", re.I)


def discovery_keyword(destination: str, scope: str) -> str:
    destination = str(destination or "").strip()
    if not destination or DURATION.search(destination):
        raise ValueError("开放发现需要目的地名称，不带旅行天数")
    return destination + SUFFIXES[scope][0]


def is_open_keyword(keyword: str, destination: str, scope: str) -> bool:
    compact = re.sub(r"\s+", "", keyword)
    return compact in {re.sub(r"\s+", "", destination) + suffix for suffix in SUFFIXES[scope]}


def route_cities(route: Any) -> list[str]:
    """Use explicit destination cities, never infer cities from route prose or searches."""
    cities = route.get("cities") if isinstance(route, dict) else None
    if (not isinstance(cities, list) or not cities
            or any(not isinstance(city, str) or not city.strip() or re.search(r"[、,，;；]", city) for city in cities)):
        raise ValueError("已确认路线须提供非空 cities 数组，每项为一个目的地城市；不能用 city_order、片区或已搜索城市推断")
    return list(dict.fromkeys(city.strip() for city in cities))


def research_mode(data: dict[str, Any] | None = None, workspace: Path | None = None) -> str:
    settings = ((execution_log.read_json(workspace / "manifest.json") or {}) if workspace is not None
                else (data or {}).get("planning") or {})
    mode = settings.get("xhs_research_mode", "required" if settings.get("xhs_detail_sampling_version") == 1 else "optional")
    if not isinstance(mode, str) or mode not in RESEARCH_MODES:
        raise ValueError("小红书研究模式必须为 optional 或 required")
    return mode


def xhs_attribution(item: dict[str, Any]) -> bool:
    provider = str(item.get("provider") or item.get("platform") or "").casefold()
    if "xiaohongshu" in provider or "小红书" in provider:
        return True
    for key in ("origin_url", "url"):
        try:
            host = (urlparse(str(item.get(key) or "")).hostname or "").casefold()
        except ValueError:
            continue
        if any(host == domain or host.endswith("." + domain) for domain in ("xiaohongshu.com", "xhslink.com")):
            return True
    return False


def xhs_is_cited(data: dict[str, Any]) -> bool:
    if any(xhs_attribution(item) for item in data.get("sources") or [] if isinstance(item, dict)):
        return True
    planning = data.get("planning") or {}
    if any(item.get("comment_note_id") or xhs_attribution(item)
           for item in planning.get("community_evidence") or [] if isinstance(item, dict)):
        return True
    for snapshot in planning.get("restaurant_snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        community = snapshot.get("community_consensus") or {}
        for reference in community.get("references") or []:
            if isinstance(reference, dict) and xhs_attribution({"platform": community.get("platform"), **reference}):
                return True
    return False


def quota_required(data: dict[str, Any], workspace: Path | None = None) -> bool:
    return research_mode(data, workspace) == "required" or xhs_is_cited(data)


def policy_digest(data: dict[str, Any]) -> str:
    planning = data.get("planning") or {}
    return execution_log.fingerprint({"mode": research_mode(data), "cited": xhs_is_cited(data),
        "fallback_reason": (planning.get("research_contract") or {}).get("xiaohongshu_fallback_reason")})


def required_destinations(root: Path) -> list[str]:
    manifest = execution_log.read_json(root / "manifest.json") or {}
    route = execution_log.read_json(root / "selected-route.json")
    strict = manifest.get("xhs_detail_sampling_version") == 1
    if route is not None and (strict or isinstance(route, dict) and route.get("cities")):
        if not isinstance(route, dict) or not route.get("id"):
            raise ValueError("已确认路线缺少有效 id")
        if manifest.get("selected_route_id") and route["id"] != manifest["selected_route_id"]:
            raise ValueError("selected-route.json 与工作区已确认路线 ID 不一致")
        return route_cities(route)
    if strict:
        raise ValueError("缺少已确认路线 selected-route.json；请先确认路线并提供 cities 城市名单，再进行正式采样")
    destination = (execution_log.read_json(root / "brief.json") or {}).get("destination")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("小红书详情采样缺少目的地")
    return [destination.strip()]  # Legacy workspaces have no route-backed sampling contract.


def records(root: Path) -> list[dict[str, Any]]:
    items = []
    for path in sorted((root / "evidence").glob("*/xhs-searches/*.json")):
        item = execution_log.read_json(path)
        if not isinstance(item, dict) or item.get("schema_version") != "travel-xhs-search/v1":
            raise ValueError(f"小红书搜索记录无效：{path}")
        if execution_log.unsafe_payload(item):
            raise ValueError("小红书搜索记录含敏感字段")
        items.append(item)
    # The same cached observation can be referenced by different tasks.
    unique = {item["id"]: item for item in items}
    return sorted(unique.values(), key=lambda item: (
        execution_log.parse_stamp(item["checked_at"]), item.get("observed_at_ns", 0)))


def source_catalog(root: Path) -> dict[str, dict[str, Any]]:
    """Registered research sources include candidates that were later rejected."""
    catalog = {}
    for path in sorted((root / "sources").rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise ValueError("研究来源目录必须是带 ID 的对象")
                catalog[item["id"]] = item
    return catalog


def validate_search(args: Any) -> dict[str, Any] | None:
    if not getattr(args, "workspace", None):
        return None  # Preserve the standalone, general-purpose MCP wrapper.
    root = execution_log.workspace_root(args.workspace)
    brief = execution_log.read_json(root / "brief.json") or {}
    destination = getattr(args, "destination", None) or brief.get("destination")
    stage = getattr(args, "search_stage", "discovery")
    scope = getattr(args, "topic", None)
    if not scope:
        matches = [s for s in SUFFIXES if destination and is_open_keyword(args.keyword, destination, s)]
        scope = matches[0] if matches else None
    if scope not in SUFFIXES:
        raise ValueError("旅行小红书搜索需声明 --topic eat/play；先用 xhs-discover 发现选择")
    if not destination:
        raise ValueError("旅行小红书搜索缺少目的地；使用 --destination 或工作区 brief.destination")
    meta = {"destination": destination, "scope": scope, "stage": stage,
            "target_source_id": getattr(args, "target_source_id", None),
            "user_request": getattr(args, "user_request", None)}
    if DURATION.search(args.keyword) and not meta["user_request"]:
        raise ValueError("小红书旅行搜索不带天数或几天几夜；天数用于筛选排程，不用于发现关键词")
    if stage == "discovery":
        if not is_open_keyword(args.keyword, destination, scope):
            raise ValueError(f"先开放发现，不预设菜品或景点；使用 {discovery_keyword(destination, scope)}")
    elif meta["user_request"]:
        if not str(meta["user_request"]).strip():
            raise ValueError("user_request 必须记录用户明确指定的查询要求")
    else:
        source_id = meta["target_source_id"]
        if not source_id:
            raise ValueError("具体对象补查须用 --target-source-id 引用发现来源，或 --user-request 记录用户指定")
        catalog = source_catalog(root)
        if source_id not in catalog:
            raise ValueError("具体对象的发现来源未登记，不能先定答案再补证据")
        source = catalog[source_id]
        parsed = urlparse(str(source.get("url") or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("具体对象的发现来源须有真实 HTTPS 原文链接")
        meta["target_source"] = {"id": source_id, "url": source["url"]}
        if not any(r.get("scope") == scope and r.get("destination") == destination
                   and r.get("stage") == "discovery" and r.get("status") == "success" for r in records(root)):
            raise ValueError("具体对象补查之前缺少本目的地同主题的成功开放发现")
    return meta


def store_search(root: Path, payload: dict[str, Any], task_id: str | None = None) -> None:
    meta = payload.get("search_design")
    if not isinstance(meta, dict):
        return
    value = {"schema_version": "travel-xhs-search/v1", **meta,
             "keyword": payload["query"]["keyword"], "parameters": payload["query"],
             "checked_at": payload["checked_at"], "status": payload.get("search_status", "failed"),
             "observed_at_ns": payload.get("observed_at_ns", 0),
             "returned_count": payload.get("returned_count"), "selected_count": payload.get("count"),
             "candidate_note_ids": payload.get("candidate_note_ids", []),
             "selected_note_ids": [r["note_id"] for r in payload.get("results", []) if r.get("note_id")]}
    if payload.get("failure_kind"):
        value["failure_kind"] = payload["failure_kind"]
    if payload.get("search_attempts"):
        value["search_attempts"] = payload["search_attempts"]
    if execution_log.unsafe_payload(value):
        raise ValueError("搜索记录含敏感字段，拒绝保存")
    value["id"] = "xhs-" + execution_log.fingerprint(value)[:24]
    task = execution_log.identifier(task_id or "main")
    path = root / "evidence" / task / "xhs-searches" / f"{value['id']}.json"
    if not path.exists():
        execution_log.write_json(path, value)


def _valid_detail_capture(item: Any) -> bool:
    if not isinstance(item, dict) or not item.get("note_id"):
        return False
    if item.get("capture_purpose") == "diagnostic":
        return False
    note_id = str(item["note_id"])
    parsed = urlparse(str(item.get("source_url") or ""))
    match = re.fullmatch(r"/(?:explore|discovery/item)/([a-zA-Z0-9_-]+)/?", parsed.path)
    if parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"} or not match or match.group(1) != note_id:
        return False
    if not isinstance(item.get("collection"), dict) or item["collection"].get("mode") not in {"expanded", "sampled"}:
        return False
    analysis = item.get("analysis")
    fields = ("input_count", "top_level_count", "reply_count", "eligible_count", "filtered_count", "group_count")
    if not isinstance(analysis, dict) or any(type(analysis.get(key)) is not int or analysis[key] < 0 for key in fields):
        return False
    if (analysis["input_count"] != analysis["top_level_count"] + analysis["reply_count"]
            or analysis["input_count"] != analysis["eligible_count"] + analysis["filtered_count"]):
        return False
    try:
        execution_log.parse_stamp(item.get("checked_at", ""))
    except (ValueError, TypeError, AttributeError):
        return False
    return item.get("review_status") in {"pending", "reviewed"}


def _candidate_ids(searches: list[dict[str, Any]], destination: str, scope: str | None = None) -> dict[str, list[str]]:
    result = {key: [] for key in SUFFIXES}
    seen = {key: set() for key in SUFFIXES}
    for item in reversed(searches):
        item_scope = item.get("scope")
        if (item.get("destination") != destination or item.get("stage") != "discovery"
                or item.get("status") != "success" or item_scope not in SUFFIXES
                or (scope and item_scope != scope)
                or not is_open_keyword(str(item.get("keyword") or ""), destination, item_scope)):
            continue
        for raw_id in item.get("candidate_note_ids") or []:
            if not isinstance(raw_id, str):
                continue
            note_id = raw_id.strip()
            if note_id and note_id not in seen[item_scope]:
                seen[item_scope].add(note_id)
                result[item_scope].append(note_id)
    return result


def _latest_discoveries(searches: list[dict[str, Any]], destination: str) -> dict[str, dict[str, Any]]:
    latest = {}
    for item in searches:
        scope = item.get("scope")
        if item.get("destination") == destination and item.get("stage") == "discovery" and scope in SUFFIXES:
            latest[scope] = item
    return latest


def _progress_for_destination(destination: str, searches: list[dict[str, Any]], captures: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = _candidate_ids(searches, destination)
    sampled_by_scope = {
        scope: sorted(set(note_ids).intersection(captures))
        for scope, note_ids in candidates.items()
    }
    sampled_unique = set().union(*sampled_by_scope.values())
    latest = _latest_discoveries(searches, destination)
    counts = {scope: len(sampled_by_scope[scope]) for scope in SUFFIXES}
    deficits = {scope: max(0, DETAIL_SAMPLE_PER_SCOPE - counts[scope]) for scope in SUFFIXES}
    remaining = {scope: [note_id for note_id in candidates[scope] if note_id not in captures]
                 for scope in SUFFIXES}
    return {
        "destination": destination,
        "target_total": DETAIL_SAMPLE_TOTAL,
        "target_per_scope": DETAIL_SAMPLE_PER_SCOPE,
        "sampled_total": len(sampled_unique),
        "sampled_by_scope": counts,
        "deficit_total": max(0, DETAIL_SAMPLE_TOTAL - len(sampled_unique)),
        "deficit_by_scope": deficits,
        "candidate_count_by_scope": {scope: len(candidates[scope]) for scope in SUFFIXES},
        "remaining_candidate_count_by_scope": {scope: len(remaining[scope]) for scope in SUFFIXES},
        "remaining_candidate_ids_by_scope": remaining,
        "latest_search_by_scope": {
            scope: {key: item[key] for key in ("status", "failure_kind", "checked_at", "id") if key in item}
            for scope, item in latest.items()
        },
        "complete": len(sampled_unique) >= DETAIL_SAMPLE_TOTAL and all(value == 0 for value in deficits.values()),
    }


def sampling_progress(root: Path, destination: str | None = None) -> dict[str, Any]:
    destinations = required_destinations(root)
    strict = (execution_log.read_json(root / "manifest.json") or {}).get("xhs_detail_sampling_version") == 1
    if destination and destination not in destinations:
        route = execution_log.read_json(root / "selected-route.json")
        if strict or isinstance(route, dict) and route.get("cities"):
            raise ValueError("采样城市不在已确认路线 cities 中，不能替换逐城验收名单")
        destinations = [destination]
    searches = records(root)
    captures: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "evidence").glob("*/xhs-comments/*.json")):
        item = execution_log.read_json(path)
        if not isinstance(item, dict) or item.get("schema_version") != "travel-xhs-comment-review/v1":
            raise ValueError(f"小红书详情观察无效：{path}")
        if execution_log.unsafe_payload(item):
            raise ValueError("小红书详情观察含敏感字段")
        if not _valid_detail_capture(item):
            continue
        note_id = str(item["note_id"])
        previous = captures.get(note_id)
        if previous is None or execution_log.parse_stamp(item["checked_at"]) > execution_log.parse_stamp(previous["checked_at"]):
            captures[note_id] = item
    all_progress = {city: _progress_for_destination(city, searches, captures) for city in destinations}
    active = destination or next((city for city, value in all_progress.items() if not value["complete"]), destinations[0])
    progress = all_progress[active]
    public = {city: {key: value for key, value in item.items()
                    if key not in {"remaining_candidate_ids_by_scope", "latest_search_by_scope"}}
              for city, item in all_progress.items()}
    return {"status": "sampling_progress", **progress, "destination_complete": progress["complete"],
            "required_destinations": destinations, "by_destination": public,
            "complete": all(item["complete"] for item in all_progress.values())}


def audit_detail_sample(data: dict[str, Any], workspace: Path | None = None) -> list[str]:
    """Block final v1 itineraries until unique, detail-backed note targets are met."""
    planning = data.get("planning") or {}
    searches = planning.get("xhs_search_records") or []
    reviews = planning.get("xhs_comment_reviews") or []
    if not isinstance(searches, list) or any(not isinstance(item, dict) for item in searches):
        return ["小红书详情采样无法验收：xhs_search_records 必须是对象数组"]
    if not isinstance(reviews, list) or any(not isinstance(item, dict) for item in reviews):
        return ["小红书详情采样无法验收：xhs_comment_reviews 必须是对象数组"]
    selected_id = (data.get("workflow") or {}).get("selected_route_id")
    proposals = data.get("route_proposals") or []
    if not isinstance(proposals, list):
        return ["小红书逐城采样无法验收：route_proposals 必须是数组"]
    selected = [route for route in proposals if isinstance(route, dict) and selected_id and route.get("id") == selected_id]
    issues = []
    try:
        if len(selected) != 1:
            raise ValueError("缺少唯一匹配 selected_route_id 的已确认路线")
        destinations = route_cities(selected[0])
    except ValueError as error:
        return [f"小红书逐城采样无法验收：{error}"]
    if workspace is not None:
        try:
            current = execution_log.read_json(workspace / "selected-route.json")
            if not isinstance(current, dict) or current.get("id") != selected_id:
                raise ValueError("当前工作区已确认路线与行程路线 ID 不一致")
            actual = route_cities(current)
        except ValueError as error:
            return [f"小红书逐城采样无法验收：{error}"]
        if actual != destinations:
            issues.append("已确认路线城市名单已变更或未完整装配，请重新装配；当前应研究：" + "、".join(actual))
        destinations = actual
    captures = {str(item.get("note_id")): item for item in reviews if _valid_detail_capture(item)}
    for destination in destinations:
        progress = _progress_for_destination(destination, searches, captures)
        if progress["complete"]:
            continue
        eat, play = progress["sampled_by_scope"]["eat"], progress["sampled_by_scope"]["play"]
        issues.append(
            f"{destination} 小红书详情采样未达标：已实际读取 {progress['sampled_total']}/{DETAIL_SAMPLE_TOTAL} 篇独立笔记，"
            f"吃 {eat}/{DETAIL_SAMPLE_PER_SCOPE}、玩 {play}/{DETAIL_SAMPLE_PER_SCOPE}；"
            f"总量还差 {progress['deficit_total']} 篇，吃/玩分别还差 "
            f"{progress['deficit_by_scope']['eat']}/{progress['deficit_by_scope']['play']} 篇"
        )
    return issues


def observation_state(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Summarize the latest evidence, without presenting history as a live probe."""
    now = now or datetime.now(timezone.utc)
    destination = (execution_log.read_json(root / "brief.json") or {}).get("destination")
    route_issue = None
    try:
        destinations = required_destinations(root)
    except ValueError as error:
        destinations = []
        route_issue = str(error)
    search_records = records(root)
    latest = {}
    for item in search_records:
        if item.get("destination") in destinations and item.get("stage") == "discovery":
            latest[(item["destination"], item.get("scope"))] = item
    captures = {}
    for path in sorted((root / "evidence").glob("*/xhs-comments/*.json")):
        item = execution_log.read_json(path)
        if (not isinstance(item, dict) or item.get("schema_version") != "travel-xhs-comment-review/v1"
                or not item.get("note_id") or not isinstance(item.get("analysis"), dict)
                or not isinstance(item.get("collection"), dict) or execution_log.unsafe_payload(item)):
            raise ValueError("小红书详情观察无效或含敏感字段")
        if item.get("capture_purpose") == "diagnostic":
            continue
        previous = captures.get(item.get("note_id"))
        if previous is None or execution_log.parse_stamp(item["checked_at"]) > execution_log.parse_stamp(previous["checked_at"]):
            captures[item["note_id"]] = item
    manifest = execution_log.read_json(root / "manifest.json") or {}
    sampling_enabled = manifest.get("xhs_detail_sampling_version") == 1
    mode = research_mode(workspace=root)
    progress = None
    if sampling_enabled:
        progress = (sampling_progress(root) if not route_issue else
                    {"complete": False, "route_required": True, "required_destinations": [], "reason": route_issue})
    topics = []
    for city, scope in ((city, scope) for city in destinations for scope in SUFFIXES):
        item = latest.get((city, scope))
        value = {"destination": city, "scope": scope, "keyword": discovery_keyword(city, scope),
                 "latest_search": None, "can_reuse_search_result": False, "detail_observations": []}
        if item is None:
            value["next_action"] = "run_discovery"
        else:
            age = (now - execution_log.parse_stamp(item["checked_at"])).total_seconds()
            recent = 0 <= age <= 600
            value["latest_search"] = {key: item[key] for key in
                ("id", "checked_at", "status", "keyword", "returned_count", "failure_kind") if key in item}
            value["latest_search"]["age_seconds"] = round(age)
            value["can_reuse_search_result"] = recent and item["status"] == "success"
            value["detail_observations"] = [{"note_id": note_id, "checked_at": captures[note_id]["checked_at"],
                "analysis": captures[note_id]["analysis"], "collection": captures[note_id]["collection"]}
                for note_id in item.get("candidate_note_ids", []) if note_id in captures]
            if item["status"] == "success":
                city_progress = (progress or {}).get("by_destination", {}).get(city)
                if city_progress and not city_progress["complete"]:
                    value["next_action"] = "run_xhs_sample" if mode == "required" else "review_source_usage"
                else:
                    value["next_action"] = "review_detail_evidence" if value["detail_observations"] else "read_details_and_comments"
            elif item["status"] == "no_results":
                value["next_action"] = "review_empty_result" if recent else "refresh_discovery"
            elif item.get("failure_kind") in {"login_required", "verification_required", "quota_exceeded"}:
                value["next_action"] = "check_login_or_platform_restriction"
            else:
                value["next_action"] = "inspect_current_failure" if recent else "refresh_discovery"
        topics.append(value)
    return {"status": "observations_only", "generated_at": now.isoformat(), "live_service_checked": False,
            "destination": destination, "topics": topics,
            "required_destinations": destinations,
            "xhs_research_mode": mode,
            "detail_sampling": progress,
            "notice": "数量进度不等于最终交付门槛：optional 模式未引用小红书时无需15篇，正式引用或 required 模式才验收配额。历史失败不能证明当前服务不可用；搜索列表、正文与评论分别验收。未调用详情不能说评论读取失败。"}


def audit_searches(data: dict[str, Any], workspace: Path | None = None) -> tuple[list[str], list[str]]:
    planning = data.get("planning") or {}
    manifest = (execution_log.read_json(workspace / "manifest.json") or {}) if workspace is not None else {}
    mode_declared = "xhs_research_mode" in planning or "xhs_research_mode" in manifest
    sampling_enabled = (planning.get("xhs_detail_sampling_version") == 1
                        or manifest.get("xhs_detail_sampling_version") == 1 or mode_declared)
    if planning.get("xhs_search_design_version") != 1 and not sampling_enabled:
        return [], []
    items = planning.get("xhs_search_records") or []
    issues = []
    try:
        mode = research_mode(data, workspace)
        if "xhs_research_mode" in planning:
            research_mode(data)
    except ValueError as error:
        return [str(error)], []
    if "xhs_research_mode" in manifest and planning.get("xhs_research_mode") != mode:
        issues.append("行程小红书研究模式与工作区模式不一致或缺失，请重新装配与渲染")
    if sampling_enabled and planning.get("xhs_search_design_version") != 1:
        issues.append("逐城采样必须保留 xhs_search_design_version=1 与实际搜索记录，请重新装配")
    if manifest.get("xhs_detail_sampling_version") == 1 and planning.get("xhs_detail_sampling_version") != 1:
        issues.append("新工作区不能删除逐城采样版本以跳过验收，请重新装配")
    elif mode_declared and planning.get("xhs_detail_sampling_version") != 1:
        issues.append("声明小红书研究模式时须保留 xhs_detail_sampling_version=1 契约，请重新装配")
    if not isinstance(items, list) or any(not isinstance(r, dict) for r in items):
        return ["xhs_search_records 必须是对象数组"], []
    research_queries = (planning.get("research_contract") or {}).get("queries") or []
    used_xhs = any("xiaohongshu.com/explore/" in str(s.get("url")) for s in data.get("sources") or [] if isinstance(s, dict))
    if (used_xhs or any(q.get("provider") == "xiaohongshu" and "search" in str(q.get("tool")) for q in research_queries)) and not items:
        issues.append("小红书旅行搜索缺少自动保存的查询记录，不能只记录已采用原帖")
    for query in research_queries:
        if query.get("provider") == "xiaohongshu" and "search" in str(query.get("tool")):
            keyword = (query.get("parameters") or {}).get("keyword")
            if not any(item.get("keyword") == keyword for item in items):
                issues.append("研究契约的小红书关键词缺少对应实际搜索记录")
    seen_open = set()
    source_ids = {s.get("id") for s in data.get("sources") or [] if isinstance(s, dict)}
    catalog = source_catalog(workspace) if workspace is not None else None
    for item in items:
        destination, scope, keyword = item.get("destination"), item.get("scope"), item.get("keyword")
        if scope not in SUFFIXES or not isinstance(destination, str) or not isinstance(keyword, str):
            issues.append("小红书搜索记录缺少目的地、主题或关键词")
            continue
        user_request = isinstance(item.get("user_request"), str) and bool(item["user_request"].strip())
        if DURATION.search(keyword) and not user_request:
            issues.append("小红书关键词包含旅行天数，缺少用户明确要求")
        if item.get("stage") == "discovery":
            if not is_open_keyword(keyword, destination, scope):
                issues.append("小红书发现关键词预设了具体对象，必须先发现吃什么、玩什么")
            elif item.get("status") == "success":
                seen_open.add((destination, scope))
        elif item.get("stage") == "detail":
            target_id = item.get("target_source_id")
            target = item.get("target_source")
            registered = isinstance(target_id, str) and (target_id in source_ids or catalog is not None and target_id in catalog)
            if isinstance(target, dict):
                parsed = urlparse(str(target.get("url") or ""))
                bound = target.get("id") == target_id and parsed.scheme == "https" and bool(parsed.hostname)
                if catalog is not None:
                    bound = bound and target_id in catalog and catalog[target_id].get("url") == target.get("url")
                registered = registered or bound
                if not bound:
                    issues.append("具体对象查询的发现来源快照与实际来源不一致")
            if not user_request and ((destination, scope) not in seen_open or not registered):
                issues.append("小红书具体对象查询缺少前置开放发现或发现来源")
        else:
            issues.append("小红书搜索阶段无效")
        if item.get("status") in {"success", "no_results"}:
            returned, selected = item.get("returned_count"), item.get("selected_count")
            ids = item.get("candidate_note_ids")
            if (type(returned) is not int or type(selected) is not int or not isinstance(ids, list)
                or returned != len(ids) or selected < 0 or selected > returned):
                issues.append("小红书搜索返回数量或候选列表记录不完整")
    if (data.get("workflow") or {}).get("phase") == "final":
        if sampling_enabled and quota_required(data, workspace):
            issues.extend(audit_detail_sample(data, workspace))
        elif sampling_enabled:
            research = planning.get("research_contract") or {}
            unavailable = (research.get("capabilities") or {}).get("xiaohongshu") == "unavailable"
            failed = any(item.get("status") in {"failed", "no_results"} for item in items)
            failed = failed or any(xhs_attribution(query) and query.get("status") in {"failed", "no_results"}
                                   for query in research.get("queries") or [] if isinstance(query, dict))
            unavailable = unavailable or any(review.get("review_status") == "unavailable"
                                             for review in planning.get("xhs_comment_reviews") or [] if isinstance(review, dict))
            if unavailable or failed:
                reason = research.get("xiaohongshu_fallback_reason")
                if not isinstance(reason, str) or not reason.strip():
                    issues.append("小红书不可用或查询失败后未采用，须记录具体降级原因 xiaohongshu_fallback_reason")
                return issues, ["小红书为可选来源，本次未引用；保留降级原因，其他来源仍须通过研究验收"]
        return issues, []
    return [], issues
