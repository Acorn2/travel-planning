"""Persist bounded comment evidence and require an explicit planning decision."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

EXECUTION_SPEC = importlib.util.spec_from_file_location("execution_log", Path(__file__).with_name("execution_log.py"))
assert EXECUTION_SPEC and EXECUTION_SPEC.loader
execution_log = importlib.util.module_from_spec(EXECUTION_SPEC)
EXECUTION_SPEC.loader.exec_module(execution_log)


def note_id_from_url(url: Any) -> str | None:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return None
    if parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}:
        return None
    match = re.fullmatch(r"/(?:explore|discovery/item)/([a-zA-Z0-9_-]+)/?", parsed.path)
    return match.group(1) if match else None


def store_capture(root: Path, payload: dict[str, Any], task_id: str | None = None) -> None:
    if payload.get("status") != "community_reported":
        return
    record = {
        "schema_version": "travel-xhs-comment-review/v1",
        "note_id": payload["note"]["note_id"],
        "source_url": payload["note"]["source_url"],
        "checked_at": payload["checked_at"],
        "collection": deepcopy(payload["comment_collection"]),
        "analysis": deepcopy(payload["comment_analysis"]),
        "comment_insights": deepcopy(payload["comment_insights"]),
        "discussion_samples": deepcopy(payload.get("discussion_samples") or []),
        "review_status": "pending",
    }
    if execution_log.unsafe_payload(record):
        raise execution_log.ExecutionError("评论证据含临时令牌或敏感字段，拒绝归档")
    task = execution_log.identifier(task_id or "main")
    digest = execution_log.fingerprint(record)
    path = root / "evidence" / task / "xhs-comments" / f"{digest}.json"
    # Reuse never changes checked_at or overwrites an earlier observation.
    if not path.exists():
        execution_log.write_json(path, record)


def load_reviews(root: Path, decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep excerpts private; final JSON carries counts and decisions only."""
    if not isinstance(decisions, list) or any(not isinstance(item, dict) or not item.get("note_id") for item in decisions):
        raise ValueError("xhs_comment_reviews 必须是带 note_id 的对象数组")
    if len({str(item["note_id"]) for item in decisions}) != len(decisions):
        raise ValueError("同一笔记的评论语义审查不能重复")
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "evidence").glob("*/xhs-comments/*.json")):
        record = execution_log.read_json(path)
        if not isinstance(record, dict) or record.get("schema_version") != "travel-xhs-comment-review/v1":
            raise ValueError(f"评论证据文件无效：{path}")
        note_id = str(record.get("note_id") or "")
        if not note_id or execution_log.unsafe_payload(record):
            raise ValueError(f"评论证据缺少笔记身份或含敏感字段：{path}")
        captured_at = execution_log.parse_stamp(record.get("checked_at", ""))
        previous = latest.get(note_id)
        if previous is None or captured_at > execution_log.parse_stamp(previous["checked_at"]):
            latest[note_id] = record
    supplied = {str(item.get("note_id")): item for item in decisions if isinstance(item, dict)}
    result = []
    for note_id, record in latest.items():
        value = {key: deepcopy(record[key]) for key in
                 ("note_id", "source_url", "checked_at", "collection", "analysis", "review_status")}
        value["insight_ids"] = [item["insight_id"] for item in record["comment_insights"]]
        decision = supplied.pop(note_id, {})
        # A semantic review must be tied to the exact observation it read.
        if decision.get("checked_at") == record["checked_at"]:
            for key in ("review_status", "reason", "insight_decisions"):
                if key in decision:
                    value[key] = deepcopy(decision[key])
        result.append(value)
    if any(item.get("review_status") != "unavailable" for item in supplied.values()):
        raise ValueError("评论审查缺少对应抓取证据，请先带 --workspace 读取 xhs-detail；不得手填抓取数量")
    result.extend({key: deepcopy(item[key]) for key in ("note_id", "source_url", "checked_at", "review_status", "reason")
                   if key in item} for item in supplied.values())
    return result


def audit_reviews(data: dict[str, Any]) -> tuple[list[str], list[str], dict[str, int]]:
    planning = data.get("planning") or {}
    strict = planning.get("xhs_comment_research_version") == 1
    final = (data.get("workflow") or {}).get("phase") == "final"
    blocking: list[str] = []
    warnings: list[str] = []
    reviews = planning.get("xhs_comment_reviews") or []
    stats = {"note_count": 0, "top_level_count": 0, "reply_count": 0, "input_count": 0,
             "eligible_count": 0, "filtered_count": 0, "group_count": 0,
             "missing_review_count": 0, "unavailable_note_count": 0,
             "adopted_insight_count": 0, "rejected_insight_count": 0, "recheck_insight_count": 0}
    if not isinstance(reviews, list):
        return ["xhs_comment_reviews 必须是数组"], [], stats
    by_note: dict[str, dict[str, Any]] = {}
    for item in reviews:
        if not isinstance(item, dict) or not item.get("note_id"):
            blocking.append("小红书评论审查记录缺少 note_id")
            continue
        note_id = str(item["note_id"])
        if note_id in by_note:
            blocking.append(f"小红书笔记 {note_id} 的评论统计重复，不得重复累计")
        by_note[note_id] = item
    source_notes: dict[str, set[str]] = {}
    for source in data.get("sources") or []:
        if isinstance(source, dict):
            note_id = note_id_from_url(source.get("url"))
            if note_id:
                source_notes.setdefault(note_id, set()).add(str(source.get("id")))
    for note_id in source_notes:
        review = by_note.get(note_id)
        issues: list[str] = []
        if not review:
            stats["missing_review_count"] += 1
            issues.append("缺少评论抓取统计和语义审查，不能只用正文完成研究")
        else:
            stats["note_count"] += 1
            if note_id_from_url(review.get("source_url")) != note_id:
                issues.append("评论原帖链接与 note_id 不一致")
            try:
                execution_log.parse_stamp(review.get("checked_at", ""))
            except (ValueError, TypeError, AttributeError):
                issues.append("缺少有效的评论查询时间")
            status = review.get("review_status")
            if status == "unavailable":
                stats["unavailable_note_count"] += 1
                if not str(review.get("reason") or "").strip():
                    issues.append("评论不可用必须说明具体失败原因")
                if review.get("analysis"):
                    issues.append("未取得评论时不能填写抓取数量；已取得样本必须进行语义审查")
                warnings.append(f"小红书笔记 {note_id} 评论未取得，不能声称已完成评论研究")
            else:
                collection = review.get("collection") or {}
                if not isinstance(collection, dict) or collection.get("mode") not in {"expanded", "sampled"}:
                    issues.append("缺少实际评论读取范围")
                elif collection.get("unexpanded_reply_threads", 0):
                    warnings.append(f"小红书笔记 {note_id} 存在未读完的回复串，语义审查须说明局限")
                analysis = review.get("analysis") or {}
                fields = ("input_count", "top_level_count", "reply_count", "eligible_count", "filtered_count", "group_count")
                if not isinstance(analysis, dict) or any(type(analysis.get(k)) is not int or analysis[k] < 0 for k in fields):
                    issues.append("缺少实际一级评论、回复、有效和过滤数量")
                elif (analysis["input_count"] != analysis["top_level_count"] + analysis["reply_count"]
                      or analysis["input_count"] != analysis["eligible_count"] + analysis["filtered_count"]):
                    issues.append("评论统计数量不闭合")
                else:
                    for key in fields:
                        stats[key] += analysis[key]
                if status != "reviewed" or not str(review.get("reason") or "").strip():
                    issues.append("评论仍未语义审查，需记录讨论结论或无有效信号的理由")
                decisions = review.get("insight_decisions") or []
                if (not isinstance(decisions, list) or any(not isinstance(d, dict)
                    or not isinstance(d.get("insight_id"), str) for d in decisions)):
                    issues.append("insight_decisions 必须是数组")
                    decisions = []
                mapped = {d.get("insight_id"): d for d in decisions if isinstance(d, dict)}
                insight_ids = review.get("insight_ids")
                if not isinstance(insight_ids, list) or any(not isinstance(i, str) or not i for i in insight_ids):
                    issues.append("缺少有效的评论候选 ID 数组")
                    insight_ids = []
                if isinstance(analysis, dict) and analysis.get("group_count") != len(insight_ids):
                    issues.append("评论主题数量与候选 ID 数量不一致")
                if len(mapped) != len(decisions) or set(mapped) - set(insight_ids):
                    issues.append("评论洞察决策重复或引用了未采集的候选")
                for insight_id in insight_ids:
                    decision = mapped.get(insight_id, {})
                    action = decision.get("decision")
                    if action not in {"adopted", "rejected", "needs_recheck"} or not str(decision.get("reason") or "").strip():
                        issues.append(f"评论洞察 {insight_id} 未记录采纳、排除或复核理由")
                    if action in {"adopted", "rejected", "needs_recheck"}:
                        count_key = {"adopted": "adopted_insight_count", "rejected": "rejected_insight_count",
                                     "needs_recheck": "recheck_insight_count"}[action]
                        stats[count_key] += 1
                    if action in {"adopted", "needs_recheck"}:
                        evidence = [e for e in planning.get("community_evidence") or [] if isinstance(e, dict)
                                    and e.get("comment_note_id") == note_id
                                    and insight_id in (e.get("comment_insight_ids") or [])
                                    and source_notes[note_id].intersection(e.get("source_ids") or [])]
                        if not evidence:
                            issues.append(f"评论洞察 {insight_id} 未绑定 community_evidence 与原帖来源")
                        if action == "needs_recheck" and any(e.get("needs_official_recheck") is not True for e in evidence):
                            issues.append(f"评论洞察 {insight_id} 未标记复核")
        for issue in issues:
            (blocking if strict and final else warnings).append(f"小红书笔记 {note_id}：{issue}")
    return blocking, warnings, stats
