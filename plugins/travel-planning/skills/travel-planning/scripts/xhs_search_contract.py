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
DURATION = re.compile(r"(?:[0-9０-９一二两三四五六七八九十百]+\s*(?:天|日|晚|夜)|[0-9]+\s*[-–~至]\s*[0-9]+\s*(?:天|日)|\b[0-9]+\s*(?:days?|nights?)\b)", re.I)


def discovery_keyword(destination: str, scope: str) -> str:
    destination = str(destination or "").strip()
    if not destination or DURATION.search(destination):
        raise ValueError("开放发现需要目的地名称，不带旅行天数")
    return destination + SUFFIXES[scope][0]


def is_open_keyword(keyword: str, destination: str, scope: str) -> bool:
    compact = re.sub(r"\s+", "", keyword)
    return compact in {re.sub(r"\s+", "", destination) + suffix for suffix in SUFFIXES[scope]}


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
        source_ids = set()
        for path in (root / "sources").glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    source_ids.add(json.loads(line).get("id"))
        if source_id not in source_ids:
            raise ValueError("具体对象的发现来源未登记，不能先定答案再补证据")
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
    brief = execution_log.read_json(root / "brief.json") or {}
    destination = str(destination or brief.get("destination") or "").strip()
    if not destination:
        raise ValueError("小红书详情采样缺少目的地")
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
    progress = _progress_for_destination(destination, searches, captures)
    return {"status": "sampling_progress", **progress}


def audit_detail_sample(data: dict[str, Any]) -> list[str]:
    """Block final v1 itineraries until unique, detail-backed note targets are met."""
    planning = data.get("planning") or {}
    searches = planning.get("xhs_search_records") or []
    reviews = planning.get("xhs_comment_reviews") or []
    if not isinstance(searches, list) or any(not isinstance(item, dict) for item in searches):
        return ["小红书详情采样无法验收：xhs_search_records 必须是对象数组"]
    if not isinstance(reviews, list) or any(not isinstance(item, dict) for item in reviews):
        return ["小红书详情采样无法验收：xhs_comment_reviews 必须是对象数组"]
    destinations = sorted({str(item.get("destination") or "").strip() for item in searches
                           if item.get("stage") == "discovery" and str(item.get("destination") or "").strip()})
    if not destinations:
        return [f"小红书详情采样未达标：缺少目的地开放发现记录；目标为每个目的地独立笔记至少 {DETAIL_SAMPLE_TOTAL} 篇，吃/玩各至少 {DETAIL_SAMPLE_PER_SCOPE} 篇"]
    captures = {str(item.get("note_id")): item for item in reviews if _valid_detail_capture(item)}
    issues = []
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
    search_records = records(root)
    latest = {}
    for item in search_records:
        if item.get("destination") == destination and item.get("stage") == "discovery":
            latest[item.get("scope")] = item
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
    progress = sampling_progress(root, destination) if sampling_enabled and destination else None
    topics = []
    for scope in SUFFIXES:
        item = latest.get(scope)
        value = {"scope": scope, "keyword": discovery_keyword(destination, scope) if destination else None,
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
                value["next_action"] = "run_xhs_sample" if progress and not progress.get("complete") else (
                    "review_detail_evidence" if value["detail_observations"] else "read_details_and_comments")
            elif item["status"] == "no_results":
                value["next_action"] = "review_empty_result" if recent else "refresh_discovery"
            elif item.get("failure_kind") in {"login_required", "verification_required", "quota_exceeded"}:
                value["next_action"] = "check_login_or_platform_restriction"
            else:
                value["next_action"] = "inspect_current_failure" if recent else "refresh_discovery"
        topics.append(value)
    return {"status": "observations_only", "generated_at": now.isoformat(), "live_service_checked": False,
            "destination": destination, "topics": topics,
            "detail_sampling": progress,
            "notice": "历史失败不能证明当前服务不可用；搜索列表、正文与评论分别验收。未调用详情不能说评论读取失败。"}


def audit_searches(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    planning = data.get("planning") or {}
    if planning.get("xhs_search_design_version") != 1:
        return [], []
    items = planning.get("xhs_search_records") or []
    issues = []
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
            if not user_request and ((destination, scope) not in seen_open or item.get("target_source_id") not in source_ids):
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
        if planning.get("xhs_detail_sampling_version") == 1:
            issues.extend(audit_detail_sample(data))
        return issues, []
    return [], issues
