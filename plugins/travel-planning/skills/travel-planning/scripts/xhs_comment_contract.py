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
        "capture_purpose": payload.get("capture_purpose", "research"),
    }
    if record["capture_purpose"] == "diagnostic":
        if not str(payload.get("diagnostic_reason") or "").strip():
            raise ValueError("诊断抓取必须记录具体原因")
        record["diagnostic_reason"] = payload["diagnostic_reason"]
    if payload.get("thread_manifest") is not None:
        record["thread_manifest"] = deepcopy(payload["thread_manifest"])
        record["capture_id"] = execution_log.fingerprint(record)
    if execution_log.unsafe_payload(record):
        raise execution_log.ExecutionError("评论证据含临时令牌或敏感字段，拒绝归档")
    task = execution_log.identifier(task_id or "main")
    digest = execution_log.fingerprint(record)
    path = root / "evidence" / task / "xhs-comments" / f"{digest}.json"
    # Reuse never changes checked_at or overwrites an earlier observation.
    if not path.exists():
        execution_log.write_json(path, record)


def latest_captures(root: Path) -> dict[str, dict[str, Any]]:
    """Use captured identity, never source selection, as the review inventory."""
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
        # A later diagnostic must not hide an existing research observation.
        research = record.get("capture_purpose", "research") == "research"
        previous_research = previous is not None and previous.get("capture_purpose", "research") == "research"
        if (previous is None or research and not previous_research
                or research == previous_research and captured_at > execution_log.parse_stamp(previous["checked_at"])):
            latest[note_id] = record
    return latest


def load_reviews(root: Path, decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep excerpts private; final JSON carries observed counts and semantic decisions."""
    if not isinstance(decisions, list) or any(not isinstance(item, dict) or not item.get("note_id") for item in decisions):
        raise ValueError("xhs_comment_reviews 必须是带 note_id 的对象数组")
    if len({str(item["note_id"]) for item in decisions}) != len(decisions):
        raise ValueError("同一笔记的评论语义审查不能重复")
    supplied = {str(item.get("note_id")): item for item in decisions if isinstance(item, dict)}
    result = []
    for note_id, record in latest_captures(root).items():
        value = {key: deepcopy(record[key]) for key in
                 ("note_id", "source_url", "checked_at", "collection", "analysis", "review_status")}
        value["insight_ids"] = [item["insight_id"] for item in record["comment_insights"]]
        for key in ("capture_purpose", "diagnostic_reason", "capture_id", "thread_manifest"):
            if key in record:
                value[key] = deepcopy(record[key])
        decision = supplied.pop(note_id, {})
        # A semantic review must be tied to the exact observation it read.
        if (decision.get("checked_at") == record["checked_at"]
                and (not record.get("capture_id") or decision.get("capture_id") == record["capture_id"])):
            for key in ("review_status", "reason", "insight_decisions", "reviewed_thread_ids"):
                if key in decision:
                    value[key] = deepcopy(decision[key])
        if record.get("capture_id"):
            insights = value.get("insight_decisions") or []
            if not isinstance(insights, list) or any(not isinstance(i, dict) for i in insights):
                raise ValueError("语义洞察决策必须是对象数组")
            value["insight_ids"] = [i.get("insight_id") for i in insights]
            value["analysis"]["group_count"] = len(insights)
        result.append(value)
    if any(item.get("review_status") != "unavailable" for item in supplied.values()):
        raise ValueError("评论审查缺少对应抓取证据，请先带 --workspace 读取 xhs-detail；不得手填抓取数量")
    result.extend({key: deepcopy(item[key]) for key in ("note_id", "source_url", "checked_at", "review_status", "reason")
                   if key in item} for item in supplied.values())
    return result


def record_plan_input(root: Path, plan_path: Path, data: dict[str, Any], output_path: Path) -> None:
    """Bind a successfully written artifact without exposing local paths publicly."""
    execution_log.write_json(root / "state/xhs-comment-input.json", {
        "schema_version": "travel-xhs-comment-input/v1", "plan_path": str(plan_path.resolve()),
        "output_path": str(output_path.resolve()), "review_digest": review_digest(data),
    })


def plan_provenance(root: Path) -> dict[str, Any] | None:
    provenance_path = root / "state/xhs-comment-input.json"
    if not provenance_path.exists():
        return None
    provenance = execution_log.read_json(provenance_path)
    if not isinstance(provenance, dict) or provenance.get("schema_version") != "travel-xhs-comment-input/v1":
        raise ValueError("装配输入记录无效，请重新装配行程")
    for key in ("plan_path", "output_path"):
        if key == "output_path" and key not in provenance:
            continue  # Older input records used the default artifact path.
        if not isinstance(provenance.get(key), str) or not Path(provenance[key]).is_absolute():
            raise ValueError("装配输入记录无效，请重新装配行程")
    return provenance


def current_plan(root: Path, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    provenance = plan_provenance(root)
    path = root / "state/itinerary-plan.json"
    if provenance is not None:
        if data is not None and provenance.get("review_digest") != review_digest(data):
            raise ValueError("当前产物过期：与最近一次装配的评论审查版本不一致，请重新装配与渲染")
        path = Path(provenance["plan_path"])
        if not path.is_file():
            raise ValueError("实际装配计划已不可读取，不能确认当前产物版本；请恢复计划后重新装配")
    elif not path.exists():
        return None
    plan = execution_log.read_json(path)
    if not isinstance(plan, dict):
        raise ValueError("实际装配计划格式无效，不能确认当前产物版本")
    return plan


def workspace_issues(data: dict[str, Any], root: Path | None) -> list[str]:
    """An assembled document cannot know that evidence arrived after assembly."""
    if root is None:
        return []
    reviews = {r["note_id"]: r for r in (data.get("planning") or {}).get("xhs_comment_reviews", [])
               if isinstance(r, dict) and nonempty_text(r.get("note_id"))}
    try:
        plan = current_plan(root, data)
    except ValueError as error:
        return [str(error)]
    decisions = ((plan.get("planning") or {}).get("xhs_comment_reviews") or []) if plan is not None else None
    try:
        current = {r["note_id"]: r for r in load_reviews(root, decisions)} if decisions is not None else None
    except ValueError as error:
        return [f"实际装配计划的评论审查记录无效：{error}"]
    issues = []
    stale = set()
    for note_id, capture in latest_captures(root).items():
        if capture.get("capture_purpose") == "diagnostic":
            continue
        review = reviews.get(note_id, {})
        observed = {k: v for k, v in (capture.get("analysis") or {}).items() if k != "group_count"}
        assembled = {k: v for k, v in (review.get("analysis") or {}).items() if k != "group_count"}
        if (review.get("checked_at") != capture["checked_at"] or observed != assembled
                or review.get("collection") != capture.get("collection")
                or review.get("capture_id") != capture.get("capture_id")
                or review.get("capture_purpose", "research") != capture.get("capture_purpose", "research")
                or review.get("thread_manifest") != capture.get("thread_manifest")):
            issues.append(f"小红书笔记 {note_id}：研究证据已更新或未装配，当前产物过期；请完成审查后重新装配")
            stale.add(note_id)
    if current is not None:
        fields = ("source_url", "checked_at", "review_status", "reason", "insight_ids", "insight_decisions", "reviewed_thread_ids")
        for note_id in sorted(set(reviews) | set(current)):
            before, after = reviews.get(note_id), current.get(note_id)
            if note_id in stale or all(r is None or r.get("capture_purpose") == "diagnostic" for r in (before, after)):
                continue
            if before is None or after is None or any(before.get(key) != after.get(key) for key in fields):
                issues.append(f"小红书笔记 {note_id}：语义审查已变更（包含失败记录增删），当前产物过期；请重新装配与渲染")
    return issues


def review_digest(data: dict[str, Any]) -> str:
    return execution_log.fingerprint((data.get("planning") or {}).get("xhs_comment_reviews") or [])


ANALYSIS_FIELDS = ("input_count", "top_level_count", "reply_count", "eligible_count", "filtered_count", "group_count")


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def semantic_review(review: dict[str, Any]) -> bool:
    analysis = review.get("analysis")
    return bool(review.get("capture_id")) or isinstance(analysis, dict) and analysis.get("method") == "thread_semantic_v2"


def comment_threads(review: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    manifest = review.get("thread_manifest")
    for thread in manifest if isinstance(manifest, list) else []:
        if not isinstance(thread, dict) or not nonempty_text(thread.get("thread_id")):
            continue
        refs = thread.get("comment_refs")
        for comment in refs if isinstance(refs, list) else []:
            if isinstance(comment, dict) and nonempty_text(comment.get("comment_id")):
                result.setdefault(comment["comment_id"], set()).add(thread["thread_id"])
    return result


def review_issues(review: dict[str, Any]) -> list[str]:
    """Shared review validation; community binding happens after semantic review."""
    issues = []
    if note_id_from_url(review.get("source_url")) != review.get("note_id"):
        issues.append("评论原帖链接与 note_id 不一致")
    try:
        execution_log.parse_stamp(review.get("checked_at", ""))
    except (ValueError, TypeError, AttributeError):
        issues.append("缺少有效的评论查询时间")
    status = review.get("review_status")
    if status == "unavailable":
        if not nonempty_text(review.get("reason")):
            issues.append("评论不可用必须说明具体失败原因")
        if review.get("analysis"):
            issues.append("未取得评论时不能填写抓取数量；已取得样本必须进行语义审查")
        return issues
    collection = review.get("collection")
    if not isinstance(collection, dict) or collection.get("mode") not in ("expanded", "sampled"):
        issues.append("缺少实际评论读取范围")
    analysis = review.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    if any(type(analysis.get(k)) is not int or analysis[k] < 0 for k in ANALYSIS_FIELDS):
        issues.append("缺少实际一级评论、回复、有效和过滤数量")
    elif (analysis["input_count"] != analysis["top_level_count"] + analysis["reply_count"]
          or analysis["input_count"] != analysis["eligible_count"] + analysis["filtered_count"]):
        issues.append("评论统计数量不闭合")
    if status != "reviewed" or not nonempty_text(review.get("reason")):
        issues.append("评论仍未语义审查，需记录讨论结论或无有效信号的理由")
    semantic = semantic_review(review)
    threads = comment_threads(review)
    if semantic:
        manifest = review.get("thread_manifest")
        if not nonempty_text(review.get("capture_id")) or not isinstance(manifest, list):
            issues.append("缺少原观察身份与讨论串清单")
            manifest = []
        expected = [t.get("thread_id") for t in manifest if isinstance(t, dict)]
        covered = review.get("reviewed_thread_ids")
        if (len(expected) != len(manifest) or any(not nonempty_text(t) for t in expected)
                or len(set(expected)) != len(expected)
                or not isinstance(covered, list) or any(not isinstance(t, str) for t in covered)
                or len(set(covered or [])) != len(covered or []) or set(covered or []) != set(expected)):
            issues.append("本次取得的讨论串尚未全部审查，不能以少量摘录代替全部有限样本")
        ref_count = 0
        for thread in manifest:
            refs = thread.get("comment_refs") if isinstance(thread, dict) else None
            if not isinstance(refs, list) or any(not isinstance(c, dict) or not nonempty_text(c.get("comment_id")) for c in refs):
                issues.append("讨论串评论引用格式无效")
                continue
            ref_count += len(refs)
        duplicates = analysis.get("duplicate_count", 0)
        if type(duplicates) is not int or duplicates < 0 or ref_count + duplicates != analysis.get("input_count"):
            issues.append("讨论串清单与实际取得的评论数量不闭合")
        if any(len(ids) > 1 for ids in threads.values()):
            issues.append("同一评论被分配到多个讨论串，不能计为独立支持")
    decisions = review.get("insight_decisions")
    if not isinstance(decisions, list) or any(not isinstance(d, dict) or not nonempty_text(d.get("insight_id")) for d in decisions):
        issues.append("insight_decisions 必须是带有效 insight_id 的对象数组")
        decisions = []
    mapped = {d["insight_id"]: d for d in decisions}
    insight_ids = review.get("insight_ids")
    if not isinstance(insight_ids, list) or any(not nonempty_text(i) for i in insight_ids):
        issues.append("缺少有效的评论候选 ID 数组")
        insight_ids = []
    if analysis.get("group_count") != len(insight_ids):
        issues.append("评论主题数量与候选 ID 数量不一致")
    if len(mapped) != len(decisions) or set(mapped) - set(insight_ids):
        issues.append("评论洞察决策重复或引用了未采集的候选")
    for insight_id in insight_ids:
        decision = mapped.get(insight_id, {})
        action = decision.get("decision") if isinstance(decision.get("decision"), str) else None
        if action not in {"adopted", "rejected", "needs_recheck"} or not nonempty_text(decision.get("reason")):
            issues.append(f"评论洞察 {insight_id} 未记录采纳、排除或复核理由")
        if semantic:
            refs = decision.get("comment_ids")
            if (not nonempty_text(decision.get("subject")) or not nonempty_text(decision.get("claim"))
                    or not isinstance(refs, list) or not refs or any(not isinstance(c, str) for c in refs)
                    or len(set(refs or [])) != len(refs or []) or set(refs or []) - set(threads)):
                issues.append(f"评论洞察 {insight_id} 缺少明确对象、具体主张或实际评论引用")
            if action == "adopted" and not nonempty_text(decision.get("planning_effect")):
                issues.append(f"评论洞察 {insight_id} 缺少对具体安排或取舍的影响")
            if action == "needs_recheck" and not nonempty_text(decision.get("recheck_action")):
                issues.append(f"评论洞察 {insight_id} 缺少待核字段与后续动作")
    return issues


def review_is_complete(review: dict[str, Any]) -> bool:
    return review.get("review_status") == "reviewed" and not review_issues(review)


def review_progress(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Uncollected failures are gaps to report, not samples waiting to be read."""
    research = [r for r in reviews if r.get("capture_purpose") != "diagnostic"]
    unavailable = [r for r in research if r.get("review_status") == "unavailable" and not r.get("analysis")]
    unavailable_ids = {r["note_id"] for r in unavailable}
    pending = [r["note_id"] for r in research if r["note_id"] not in unavailable_ids and not review_is_complete(r)]
    return {"pending_note_count": len(pending), "pending_note_ids": pending,
            "unavailable_note_count": len(unavailable), "unavailable_notes": unavailable}


def validate_page_evidence(source: Path, html: str) -> None:
    """Check local handoff pages against the current assembly, without network access."""
    root = execution_log.infer_workspace(source)
    if root is None and source.parent.parent.name == "travel-plans":
        candidate = source.parent.parent.parent / ".travel-research" / source.parent.name
        if (candidate / "manifest.json").is_file():
            root = execution_log.workspace_root(candidate)
    if root is None:
        return
    provenance = plan_provenance(root) or {}
    path = Path(provenance["output_path"]) if "output_path" in provenance else root / "artifacts/itinerary.json"
    data = execution_log.read_json(path)
    if data is None:
        if "output_path" in provenance:
            raise ValueError("实际装配输出已不可读取，不能核对评论研究版本；请重新装配")
        if (execution_log.read_json(root / "manifest.json") or {}).get("xhs_comment_research_version") == 2:
            raise ValueError("缺少当前行程 JSON，不能核对评论研究版本")
        return
    if not isinstance(data, dict):
        raise ValueError("实际装配输出格式无效，请重新装配行程")
    issues = workspace_issues(data, root)
    if issues:
        raise ValueError("；".join(issues))
    if (data.get("planning") or {}).get("xhs_comment_research_version") == 2:
        marker = re.search(r'<meta name="xhs-review-digest" content="([a-f0-9]{64})">', html)
        if marker is None or marker.group(1) != review_digest(data):
            raise ValueError("HTML 的评论审查版本已过期或缺失，请重新审查并渲染后交付")
        blocking, _, _ = audit_reviews(data, root)
        if blocking:
            raise ValueError("；".join(blocking))


def review_packet(root: Path, note_id: str | None = None, offset: int = 0, limit: int = 5) -> dict[str, Any]:
    """Read an exact local observation, including expired cache; never silently refetch."""
    plan = current_plan(root) or {}
    reviews = load_reviews(root, (plan.get("planning") or {}).get("xhs_comment_reviews") or [])
    progress = review_progress(reviews)
    pending_ids = progress["pending_note_ids"]
    unavailable = progress["unavailable_notes"]
    if (not note_id and not pending_ids) or any(r["note_id"] == note_id for r in unavailable):
        return {"status": "unavailable" if unavailable else "complete", **progress,
                "live_service_checked": False,
                **({"notice": "失败记录尚无可审查样本，不计入采样配额；可先研究其他候选。重试需显式 xhs-detail --refresh，仍遵守平台限制。"}
                   if unavailable else {})}
    note_id = note_id or pending_ids[0]
    capture = latest_captures(root).get(note_id)
    if capture is None:
        raise ValueError("没有该笔记的评论观察，请先读取 xhs-detail")
    base = {"note_id": note_id, "checked_at": capture["checked_at"], "capture_id": capture.get("capture_id"),
            "collection": capture["collection"], "analysis": capture["analysis"], **progress,
            "thread_manifest": capture.get("thread_manifest", []), "live_service_checked": False,
            "legacy_comment_insights": capture.get("comment_insights", [])}
    for path in sorted((root / "state/execution/cache").glob("*.json")):
        cached = execution_log.read_json(path) or {}
        payload = cached.get("payload") or {}
        if not isinstance(payload, dict) or cached.get("payload_sha256") != execution_log.fingerprint(payload):
            continue
        if ((payload.get("note") or {}).get("note_id") != note_id or payload.get("checked_at") != capture["checked_at"]):
            continue
        # Compare the exact persisted observation, rather than trusting a timestamp alone.
        if (payload.get("thread_manifest") != capture.get("thread_manifest")
                or payload.get("comment_analysis") != capture.get("analysis")
                or payload.get("capture_purpose", "research") != capture.get("capture_purpose", "research")
                or execution_log.unsafe_payload(payload)):
            continue
        threads = payload.get("review_threads")
        if threads is None:
            if not isinstance(payload.get("comments"), list):
                continue
            threads = [{"thread_id": str(c.get("comment_id") or f"legacy-thread-{i}"),
                        "comments": [{k: v for k, v in c.items() if k != "replies"}, *(c.get("replies") or [])]}
                       for i, c in enumerate(payload["comments"])]
        batch = threads[offset:offset + limit]
        return {**base, "status": "pending_review", "review_threads": batch, "offset": offset,
                "next_offset": offset + len(batch) if offset + len(batch) < len(threads) else None,
                "notice": "阅读本观察的全部线程后才能标记 reviewed；缓存时间不刷新，不代表当前营业或规则"}
    return {**base, "status": "context_unavailable", "discussion_samples": capture.get("discussion_samples", []),
            "notice": "原观察的完整有限样本已不可恢复；必要摘录不能冒充全部已读，请定向重新读取并绑定新观察"}


def audit_reviews(data: dict[str, Any], root: Path | None = None) -> tuple[list[str], list[str], dict[str, int]]:
    planning = data.get("planning") or {}
    workspace_version = (execution_log.read_json(root / "manifest.json") or {}).get("xhs_comment_research_version") if root else None
    strict = planning.get("xhs_comment_research_version") in {1, 2} or workspace_version in {1, 2}
    final = (data.get("workflow") or {}).get("phase") == "final"
    blocking: list[str] = []
    warnings: list[str] = []
    reviews = planning.get("xhs_comment_reviews") or []
    stats = {"note_count": 0, **{key: 0 for key in ANALYSIS_FIELDS},
             "missing_review_count": 0, "unavailable_note_count": 0,
             "adopted_insight_count": 0, "rejected_insight_count": 0, "recheck_insight_count": 0,
             "reviewed_note_count": 0, "pending_note_count": 0, "diagnostic_note_count": 0,
             "reviewed_comment_count": 0, "evidence_issue_note_count": 0}
    if not isinstance(reviews, list):
        return ["xhs_comment_reviews 必须是数组"], [], stats
    by_note: dict[str, dict[str, Any]] = {}
    for item in reviews:
        if not isinstance(item, dict) or not nonempty_text(item.get("note_id")):
            blocking.append("小红书评论审查记录缺少 note_id")
            continue
        note_id = item["note_id"]
        if note_id in by_note:
            blocking.append(f"小红书笔记 {note_id} 的评论统计重复，不得重复累计")
        by_note[note_id] = item
    source_notes: dict[str, set[str]] = {}
    for source in data.get("sources") or []:
        if isinstance(source, dict):
            note_id = note_id_from_url(source.get("url"))
            if note_id:
                source_notes.setdefault(note_id, set()).add(str(source.get("id")))
    for issue in workspace_issues(data, root):
        (blocking if strict and final else warnings).append(issue)
    for note_id in sorted(set(source_notes) | set(by_note)):
        review = by_note.get(note_id)
        if review and review.get("capture_purpose") == "diagnostic":
            stats["diagnostic_note_count"] += 1
            if not nonempty_text(review.get("diagnostic_reason")) or note_id in source_notes:
                (blocking if strict and final else warnings).append(f"小红书笔记 {note_id}：诊断记录必须有原因且不能作为正式来源")
            continue
        if not review:
            stats["missing_review_count"] += 1
            issues = ["缺少评论抓取统计和语义审查，不能只用正文完成研究"]
        else:
            stats["note_count"] += 1
            issues = review_issues(review)
            if review.get("review_status") == "unavailable":
                stats["unavailable_note_count"] += 1
                warnings.append(f"小红书笔记 {note_id} 评论未取得，不能声称已完成评论研究")
            else:
                analysis = review.get("analysis") or {}
                if (isinstance(analysis, dict) and all(type(analysis.get(k)) is int and analysis[k] >= 0 for k in ANALYSIS_FIELDS)
                        and analysis["input_count"] == analysis["top_level_count"] + analysis["reply_count"]
                        and analysis["input_count"] == analysis["eligible_count"] + analysis["filtered_count"]):
                    for key in ANALYSIS_FIELDS:
                        stats[key] += analysis[key]
                if review.get("review_status") == "reviewed" and not issues:
                    stats["reviewed_note_count"] += 1
                    stats["reviewed_comment_count"] += analysis["input_count"]
                else:
                    stats["pending_note_count"] += 1
                collection = review.get("collection") or {}
                if isinstance(collection, dict) and collection.get("unexpanded_reply_threads", 0):
                    warnings.append(f"小红书笔记 {note_id} 存在未读完的回复串，语义审查须说明局限")
                decisions = review.get("insight_decisions") or []
                mapped = {d.get("insight_id"): d for d in decisions if isinstance(d, dict) and nonempty_text(d.get("insight_id"))} if isinstance(decisions, list) else {}
                insight_ids = review.get("insight_ids") or []
                if not isinstance(insight_ids, list) or any(not isinstance(i, str) for i in insight_ids):
                    insight_ids = []
                threads = comment_threads(review)
                evidence_issues = []
                bindings = [e for e in planning.get("community_evidence") or [] if isinstance(e, dict)
                            and e.get("comment_note_id") == note_id
                            and isinstance(e.get("comment_insight_ids"), list)
                            and source_notes.get(note_id, set()).intersection(e.get("source_ids") or [])]
                for insight_id in dict.fromkeys(insight_ids):
                    decision = mapped.get(insight_id, {})
                    action = decision.get("decision") if isinstance(decision.get("decision"), str) else None
                    count_key = {"adopted": "adopted_insight_count", "rejected": "rejected_insight_count", "needs_recheck": "recheck_insight_count"}.get(action)
                    if count_key:
                        stats[count_key] += 1
                    if action not in {"adopted", "needs_recheck"}:
                        continue
                    evidence = [e for e in bindings if insight_id in e["comment_insight_ids"]]
                    if not evidence:
                        evidence_issues.append(f"评论洞察 {insight_id} 未绑定 community_evidence 与原帖来源")
                    if action == "needs_recheck" and any(e.get("needs_official_recheck") is not True for e in evidence):
                        evidence_issues.append(f"评论洞察 {insight_id} 未标记复核")
                if semantic_review(review):
                    for evidence in bindings:
                        bound = [mapped[i] for i in evidence["comment_insight_ids"]
                                 if isinstance(i, str) and i in insight_ids and i in mapped
                                 and mapped[i].get("decision") in ("adopted", "needs_recheck")]
                        if not bound:
                            continue
                        independent = set()
                        for decision in bound:
                            refs = decision.get("comment_ids")
                            for comment_id in refs if isinstance(refs, list) else []:
                                if isinstance(comment_id, str):
                                    independent.update(threads.get(comment_id, set()))
                        support = evidence.get("support_count")
                        if type(support) is not int or not 0 <= support <= len(independent):
                            evidence_issues.append("评论社区证据支持数超过所绑定洞察的独立讨论串数，回复不能累加为共识")
                if evidence_issues:
                    stats["evidence_issue_note_count"] += 1
                    issues.extend(evidence_issues)
        for issue in issues:
            (blocking if strict and final else warnings).append(f"小红书笔记 {note_id}：{issue}")
    return blocking, warnings, stats
