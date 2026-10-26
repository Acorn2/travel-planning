from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/travel-planning/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sources = load("research_sources")
contract = sources.xhs_comment_contract
audit = load("audit_itinerary")


def upstream_response():
    # v2.5.0 types.go: FeedDetailResponse.Comments is CommentList, not []Comment.
    return {"data": {"note": {"noteId": "note1", "title": "测试攻略", "desc": "正文称东门省时",
        "xsecToken": "private-test-token", "imageList": [],
        "interactInfo": {"commentCount": "99"}}, "comments": {"hasMore": True, "list": [
        {"id": "c1", "content": "请问东门需要预约吗？", "createTime": 1791936000000,
         "subCommentCount": "2", "subComments": [
             {"id": "r1", "content": "我今天现场去过，东门施工，需要从南门进入。", "createTime": 1791936000000},
             {"id": "r2", "content": "谢谢", "createTime": 1791936000000}]},
        {"id": "c2", "content": "我今天现场等了40分钟，这也叫不排队？", "createTime": 1791936000000}
    ]}}}


class XhsCommentsTest(unittest.TestCase):
    def detail(self, raw=None, args=None):
        with patch.object(sources, "require_xhs_login"), patch.object(sources, "read_xhs_mcp_token_cache", return_value={
            "notes": {"note1": {"xsec_token": "private-test-token", "source_url": "https://www.xiaohongshu.com/explore/note1"}}
        }), patch.object(sources, "xhs_mcp_call", return_value=raw if raw is not None else upstream_response()) as call:
            payload = sources.xhs_detail(args or sources.build_parser().parse_args(["xhs-detail", "--note-id", "note1"]))
            return payload, call.call_args.args[1]

    def test_real_upstream_container_and_default_replies(self):
        payload, args = self.detail()
        self.assertEqual(args["load_all_comments"], True)
        self.assertEqual(args["click_more_replies"], True)
        self.assertEqual(args["limit"], 20)
        self.assertEqual(args["reply_limit"], 50)
        analysis = payload["comment_analysis"]
        self.assertEqual(analysis["top_level_count"], 2)
        self.assertEqual(analysis["reply_count"], 2)
        self.assertEqual(analysis["input_count"], 4)
        self.assertEqual(payload["comment_collection"]["reported_total"], "99")
        self.assertTrue(payload["comment_collection"]["has_more"])
        self.assertNotIn("private-test-token", json.dumps(payload))

    def test_reply_is_not_double_counted_and_keeps_parent(self):
        payload, _ = self.detail()
        thread = next(t for t in payload["review_threads"] if t["thread_id"] == "c1")
        self.assertEqual(payload["comment_analysis"]["duplicate_count"], 0)
        self.assertEqual(thread["comments"][1]["parent_comment_id"], "c1")
        self.assertIn("预约", thread["comments"][1]["parent_excerpt"])

    def test_rhetorical_correction_and_question_are_both_reviewable(self):
        payload, _ = self.detail()
        texts = [c["content"] for t in payload["review_threads"] for c in t["comments"]]
        self.assertTrue(any("40分钟" in text for text in texts))
        self.assertTrue(any(text.startswith("请问") for text in texts))
        self.assertEqual(payload["comment_insights"], [])

    def test_short_reply_keeps_question_context_and_topic_hint(self):
        comments = sources.normalize_xhs_comments({"comments": {"list": [{"id": "q", "content": "景区需要预约吗？",
            "subComments": [{"id": "a", "content": "已经取消了，不用了", "createTime": 1791936000000}]}]}}, True)
        result = sources.build_xhs_comment_insights(comments, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
        reply = result["review_threads"][0]["comments"][1]
        self.assertEqual(reply["reading_hint"]["topic"], "预约、购票与检票")
        self.assertEqual(reply["parent_comment_id"], "q")

    def test_unknown_comment_shape_fails_instead_of_fake_zero(self):
        for container in ({"unexpected": [1]}, "not-a-list", None):
            raw = upstream_response()
            raw["data"]["comments"] = container
            with self.subTest(container=container), self.assertRaises(sources.SourceError):
                self.detail(raw)

    def test_valid_empty_comments_have_real_zero(self):
        raw = upstream_response()
        raw["data"]["comments"] = {"list": [], "hasMore": False}
        raw["data"]["note"]["interactInfo"]["commentCount"] = "0"
        payload, _ = self.detail(raw)
        self.assertEqual(payload["comment_analysis"]["input_count"], 0)
        raw["data"]["comments"]["list"] = None
        self.assertEqual(self.detail(raw)[0]["comment_analysis"]["input_count"], 0)

    def test_questions_without_punctuation_and_chitchat_are_not_evidence(self):
        now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        for text in ("住在宜昌东站附近 有什么好吃的吗 正餐 小吃都可以", "嗯嗯 我去搜搜看"):
            result = sources.build_xhs_comment_insights([{"content": text, "published_at": "2026-10-14T00:00:00Z"}], now)
            self.assertEqual(result["comment_insights"], [])

    def test_malformed_comment_elements_do_not_silently_disappear(self):
        raw = upstream_response()
        raw["data"]["comments"]["list"] = [42]
        with self.assertRaises(sources.SourceError):
            self.detail(raw)

    def test_sample_mode_and_hot_thread_threshold_are_explicit(self):
        args = sources.build_parser().parse_args(["xhs-detail", "--note-id", "note1", "--sample-comments"])
        payload, sent = self.detail(args=args)
        self.assertFalse(sent["load_all_comments"])
        self.assertNotIn("click_more_replies", sent)
        self.assertEqual(payload["comment_analysis"]["reply_count"], 0)
        self.assertEqual(payload["comment_collection"]["unexpanded_reply_threads"], 1)
        args = sources.build_parser().parse_args(["xhs-detail", "--note-id", "note1", "--reply-limit", "500"])
        self.assertEqual(self.detail(args=args)[1]["reply_limit"], 500)

    def test_capture_persists_after_cache_expiry_and_reuse_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1})
            args = sources.build_parser().parse_args(["xhs-detail", "--note-id", "note1", "--workspace", str(root)])
            with patch.object(sources, "require_xhs_login"), patch.object(sources, "read_xhs_mcp_token_cache", return_value={
                "notes": {"note1": {"xsec_token": "private-test-token", "source_url": "https://www.xiaohongshu.com/explore/note1"}}
            }), patch.object(sources, "xhs_mcp_call", return_value=upstream_response()) as call:
                for _ in range(2):
                    sources.run_query(args, "xhs-detail", sources.command_parameters(args), lambda: sources.xhs_detail(args), "xiaohongshu")
                self.assertEqual(call.call_count, 1)
            paths = list((root / "evidence/main/xhs-comments").glob("*.json"))
            self.assertEqual(len(paths), 1)
            stored = json.loads(paths[0].read_text())
            self.assertNotIn("private-test-token", paths[0].read_text())
            self.assertNotIn('"comments"', paths[0].read_text())
            self.assertEqual(stored["analysis"]["input_count"], 4)
            review = {"note_id": "note1", "checked_at": stored["checked_at"], "review_status": "reviewed", "reason": "评论纠正正文", "capture_id": stored["capture_id"],
                      "reviewed_thread_ids": [t["thread_id"] for t in stored["thread_manifest"]], "insight_decisions": []}
            public = contract.load_reviews(root, [review])[0]
            self.assertEqual(public["review_status"], "reviewed")
            self.assertNotIn("discussion_samples", public)
            self.assertNotIn("comment_insights", public)
            stale = {**review, "checked_at": "2000-01-01T00:00:00Z"}
            self.assertEqual(contract.load_reviews(root, [stale])[0]["review_status"], "pending")

    def test_no_fabricated_count_without_capture(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "缺少对应抓取证据"):
            contract.load_reviews(Path(temp), [{"note_id": "note1", "review_status": "reviewed"}])

    def plan(self):
        payload, _ = self.detail()
        analysis = {**payload["comment_analysis"], "group_count": 2}
        decisions = [
            {"insight_id": "semantic-entry", "subject": "景区东门", "claim": "一条回复称东门施工", "comment_ids": ["r1"],
             "decision": "rejected", "reason": "与本次区域不符"},
            {"insight_id": "semantic-wait", "subject": "景区排队", "claim": "一条评论称等候40分钟", "comment_ids": ["c2"],
             "decision": "rejected", "reason": "与本次时段不符"}]
        return {"workflow": {"phase": "final"}, "sources": [{"id": "s1", "url": payload["note"]["source_url"]}],
            "planning": {"xhs_comment_research_version": 2, "community_evidence": [], "xhs_comment_reviews": [{
                "note_id": "note1", "source_url": payload["note"]["source_url"], "checked_at": payload["checked_at"],
                "capture_id": "fixture", "thread_manifest": payload["thread_manifest"],
                "reviewed_thread_ids": [t["thread_id"] for t in payload["thread_manifest"]],
                "collection": payload["comment_collection"], "analysis": analysis, "review_status": "reviewed",
                "reason": "读过问题和回复；施工影响入口，排队与正文冲突", "insight_ids": [i["insight_id"] for i in decisions],
                "insight_decisions": decisions}]}}

    def test_gate_blocks_missing_pending_and_undecided_reviews(self):
        for change in (lambda p: p.update(xhs_comment_reviews=[]),
                       lambda p: p["xhs_comment_reviews"][0].update(review_status="pending"),
                       lambda p: p["xhs_comment_reviews"][0].update(insight_decisions=[])):
            data = self.plan()
            change(data["planning"])
            self.assertTrue(contract.audit_reviews(data)[0])

    def test_gate_covers_collected_notes_absent_from_sources(self):
        data = self.plan()
        second = deepcopy(data["planning"]["xhs_comment_reviews"][0])
        second.update(note_id="second", source_url="https://www.xiaohongshu.com/explore/second")
        data["planning"]["xhs_comment_reviews"].append(second)
        for index in range(13):
            review = deepcopy(data["planning"]["xhs_comment_reviews"][0])
            review.update(note_id=f"extra-{index}", source_url=f"https://www.xiaohongshu.com/explore/extra-{index}",
                          review_status="pending")
            data["planning"]["xhs_comment_reviews"].append(review)
        blocking, _, stats = contract.audit_reviews(data)
        self.assertEqual(stats["pending_note_count"], 13)
        self.assertEqual(stats["note_count"], 15)
        self.assertEqual(sum("仍未语义审查" in item for item in blocking), 13)

    def test_low_score_question_and_advertising_complaint_reach_thread_review(self):
        comments = [{"comment_id": "q", "content": "广告太多，实际不能从这里进吗？", "replies": [
            {"comment_id": "a", "content": "不行，走另一边"}]}]
        result = sources.build_xhs_comment_insights(comments)
        self.assertEqual(len(result["review_threads"]), 1)
        self.assertEqual([c["comment_id"] for c in result["review_threads"][0]["comments"]], ["q", "a"])
        self.assertEqual(result["comment_insights"], [])

    def capture(self, root, *, note_id="note1", checked_at="2026-10-26T10:00:00Z", purpose="research"):
        payload, _ = self.detail()
        payload["note"].update(note_id=note_id, source_url=f"https://www.xiaohongshu.com/explore/{note_id}")
        payload.update(checked_at=checked_at, capture_purpose=purpose, diagnostic_reason="协议诊断" if purpose == "diagnostic" else None)
        contract.store_capture(root, payload)
        sources.execution_log.write_json(root / "state/execution/cache" / ("fixture-" + note_id + ".json"), {
            "payload": payload, "payload_sha256": sources.execution_log.fingerprint(payload), "expires_at": "2000-01-01T00:00:00Z"})
        return payload, contract.latest_captures(root)[note_id]

    def reviewed(self, capture):
        return {"note_id": capture["note_id"], "checked_at": capture["checked_at"], "capture_id": capture["capture_id"],
                "review_status": "reviewed", "reason": "所有取得的问答都已阅读，无适用于本次旅行的信息", "insight_decisions": [],
                "reviewed_thread_ids": [t["thread_id"] for t in capture["thread_manifest"]]}

    def test_all_reviewed_with_zero_adoptions_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            decisions = [self.reviewed(self.capture(root, note_id=f"n{i}")[1]) for i in range(15)]
            reviews = contract.load_reviews(root, decisions)
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2, "xhs_comment_reviews": reviews}}
            blocking, _, stats = contract.audit_reviews(data, root)
            self.assertEqual(blocking, [])
            self.assertEqual(stats["reviewed_note_count"], 15)
            self.assertEqual(stats["adopted_insight_count"], 0)

    def test_declared_review_without_full_thread_coverage_is_blocked(self):
        data = self.plan()
        data["planning"]["xhs_comment_reviews"][0]["reviewed_thread_ids"] = ["c1"]
        self.assertTrue(any("讨论串尚未全部" in b for b in contract.audit_reviews(data)[0]))
        self.assertFalse(contract.review_is_complete(data["planning"]["xhs_comment_reviews"][0]))

    def test_adoption_requires_planning_effect_and_valid_comment_ids(self):
        data = self.plan()
        decision = data["planning"]["xhs_comment_reviews"][0]["insight_decisions"][0]
        decision.update(decision="adopted", comment_ids=["invented"])
        blocking = contract.audit_reviews(data)[0]
        self.assertTrue(any("实际评论引用" in b for b in blocking))
        self.assertTrue(any("具体安排或取舍" in b for b in blocking))

    def test_replies_do_not_inflate_independent_support(self):
        data = self.plan()
        decision = data["planning"]["xhs_comment_reviews"][0]["insight_decisions"][0]
        decision.update(decision="needs_recheck", comment_ids=["c1", "r1"], recheck_action="核公告")
        data["planning"]["community_evidence"] = [{"comment_note_id": "note1", "comment_insight_ids": [decision["insight_id"]],
            "source_ids": ["s1"], "support_count": 2, "needs_official_recheck": True}]
        self.assertTrue(any("独立讨论串" in b for b in contract.audit_reviews(data)[0]))

    def test_combined_insights_use_union_of_independent_threads(self):
        data = self.plan()
        review = data["planning"]["xhs_comment_reviews"][0]
        for decision in review["insight_decisions"]:
            decision.update(decision="adopted", planning_effect="调整景区游览安排")
        data["planning"]["community_evidence"] = [{"comment_note_id": "note1",
            "comment_insight_ids": review["insight_ids"], "source_ids": ["s1"], "support_count": 2}]
        self.assertEqual(contract.audit_reviews(data)[0], [])
        # Even when several insights cite the root and its reply, it is still one thread.
        review["insight_decisions"][1]["comment_ids"] = ["c1", "r2"]
        self.assertTrue(any("独立讨论串" in b for b in contract.audit_reviews(data)[0]))
        data["planning"]["community_evidence"][0]["support_count"] = 1
        self.assertEqual(contract.audit_reviews(data)[0], [])

    def test_unbound_or_rejected_insights_do_not_raise_support_limit(self):
        for extra in ("unknown", "rejected", "unbound"):
            with self.subTest(extra=extra):
                data = self.plan()
                review = data["planning"]["xhs_comment_reviews"][0]
                first, second = review["insight_decisions"]
                first.update(decision="adopted", planning_effect="调整入口安排")
                if extra == "unbound":
                    second.update(decision="adopted", planning_effect="保留候场缓冲")
                bound_ids = [first["insight_id"]]
                if extra != "unbound":
                    bound_ids.append("unknown" if extra == "unknown" else second["insight_id"])
                data["planning"]["community_evidence"] = [{"comment_note_id": "note1",
                    "comment_insight_ids": bound_ids, "source_ids": ["s1"], "support_count": 2}]
                self.assertTrue(any("独立讨论串" in b for b in contract.audit_reviews(data)[0]))

    def test_diagnostic_is_excluded_and_cannot_hide_research(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, research = self.capture(root)
            self.capture(root, checked_at="2026-10-26T11:00:00Z", purpose="diagnostic")
            self.assertEqual(contract.latest_captures(root)["note1"]["capture_id"], research["capture_id"])
            self.capture(root, note_id="diag", purpose="diagnostic")
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2,
                    "xhs_comment_reviews": contract.load_reviews(root, [self.reviewed(research)])}}
            blocking, _, stats = contract.audit_reviews(data)
            self.assertEqual(blocking, [])
            self.assertEqual(stats["note_count"], 1)
            self.assertEqual(stats["diagnostic_note_count"], 1)
            data["sources"] = [{"id": "diag", "url": "https://www.xiaohongshu.com/explore/diag"}]
            self.assertTrue(any("诊断记录" in b for b in contract.audit_reviews(data)[0]))

    def test_new_observation_invalidates_old_review_and_assembly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, old = self.capture(root)
            decisions = [self.reviewed(old)]
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2,
                    "xhs_comment_reviews": contract.load_reviews(root, decisions)}}
            self.assertEqual(contract.workspace_issues(data, root), [])
            self.capture(root, checked_at="2026-10-26T11:00:00Z")
            self.assertTrue(any("产物过期" in b for b in contract.audit_reviews(data, root)[0]))
            self.assertEqual(contract.load_reviews(root, decisions)[0]["review_status"], "pending")

    def test_local_review_pages_keep_threads_and_never_call_upstream(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(sources, "xhs_mcp_call") as upstream:
            root = Path(temp)
            self.capture(root)
            first = contract.review_packet(root, limit=1)
            second = contract.review_packet(root, offset=first["next_offset"], limit=1)
            self.assertEqual(first["status"], "pending_review")
            all_threads = first["review_threads"] + second["review_threads"]
            self.assertEqual({t["thread_id"] for t in all_threads}, {"c1", "c2"})
            self.assertEqual(len(next(t for t in all_threads if t["thread_id"] == "c1")["comments"]), 3)
            self.assertIsNone(second["next_offset"])
            upstream.assert_not_called()

    def test_changed_semantic_decision_invalidates_assembly_without_refetch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, capture = self.capture(root)
            decision = self.reviewed(capture)
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2,
                    "xhs_comment_reviews": contract.load_reviews(root, [decision])}}
            decision["reason"] = "重新审查后确认此前讨论指向另一分店，全部排除"
            sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": [decision]}})
            self.assertTrue(any("语义审查已变更" in b for b in contract.audit_reviews(data, root)[0]))

    def test_undecided_insight_remains_in_review_queue_and_stops_sampling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            sources.execution_log.write_json(root / "brief.json", {"destination": "测试城"})
            _, capture = self.capture(root)
            decision = self.reviewed(capture)
            decision["insight_decisions"] = [{"insight_id": "entry", "subject": "东门", "claim": "一条回复称施工", "comment_ids": ["r1"]}]
            sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": [decision]}})
            loaded = contract.load_reviews(root, [decision])
            self.assertFalse(contract.review_is_complete(loaded[0]))
            packet = contract.review_packet(root)
            self.assertEqual(packet["status"], "pending_review")
            args = sources.build_parser().parse_args(["xhs-sample", "--workspace", str(root)])
            with patch.object(sources, "xhs_mcp_call") as upstream:
                self.assertEqual(sources.xhs_sample(args)["status"], "awaiting_review")
                upstream.assert_not_called()
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2, "xhs_comment_reviews": loaded}}
            self.assertEqual(contract.audit_reviews(data, root)[2]["pending_note_count"], packet["pending_note_count"])

    def test_queue_and_auditor_share_basic_decision_validation(self):
        changes = [
            {"decision": {}}, {"reason": " "}, {"subject": ""}, {"claim": ""}, {"comment_ids": ["invented"]},
            {"decision": "adopted"}, {"decision": "needs_recheck"},
        ]
        for change in changes:
            with self.subTest(change=change):
                data = self.plan()
                review = data["planning"]["xhs_comment_reviews"][0]
                review["insight_decisions"][0].update(change)
                self.assertFalse(contract.review_is_complete(review))
                self.assertEqual(contract.review_progress([review])["pending_note_count"], 1)
                self.assertEqual(contract.audit_reviews(data)[2]["pending_note_count"], 1)

    def test_completed_review_and_missing_planning_binding_are_distinct(self):
        data = self.plan()
        review = data["planning"]["xhs_comment_reviews"][0]
        review["insight_decisions"][0].update(decision="adopted", planning_effect="排除东门入场路线")
        self.assertTrue(contract.review_is_complete(review))
        blocking, _, stats = contract.audit_reviews(data)
        self.assertTrue(any("未绑定" in b for b in blocking))
        self.assertEqual(stats["pending_note_count"], 0)
        self.assertEqual(stats["reviewed_note_count"], 1)
        self.assertEqual(stats["evidence_issue_note_count"], 1)

    def test_failure_record_add_update_and_removal_make_old_page_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            failure = {"note_id": "failed", "source_url": "https://www.xiaohongshu.com/explore/failed",
                       "checked_at": "2026-10-26T10:00:00Z", "review_status": "unavailable", "reason": "评论加载失败"}
            for before, after in (([], [failure]), ([failure], [{**failure, "reason": "再次读取失败"}]), ([failure], [])):
                with self.subTest(before=before, after=after):
                    sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": before}})
                    data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2,
                            "xhs_comment_reviews": contract.load_reviews(root, before)}}
                    sources.execution_log.write_json(root / "artifacts/itinerary.json", data)
                    page = root / "artifacts/itinerary.html"
                    html = '<html><head><meta name="xhs-review-digest" content="' + contract.review_digest(data) + '"></head></html>'
                    page.write_text(html)
                    sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": after}})
                    self.assertTrue(contract.workspace_issues(data, root))
                    with self.assertRaisesRegex(ValueError, "产物过期"):
                        contract.validate_page_evidence(page, html)

    def test_missing_context_is_not_reported_as_reviewed_or_zero_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload, _ = self.detail()
            contract.store_capture(root, payload)
            packet = contract.review_packet(root)
            self.assertEqual(packet["status"], "context_unavailable")
            self.assertEqual(packet["analysis"]["input_count"], 4)

    def test_new_workspace_sampling_yields_for_review_before_next_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            sources.execution_log.write_json(root / "brief.json", {"destination": "测试城"})
            self.capture(root)
            args = sources.build_parser().parse_args(["xhs-sample", "--workspace", str(root)])
            with patch.object(sources, "xhs_mcp_call") as upstream:
                report = sources.xhs_sample(args)
                upstream.assert_not_called()
            self.assertEqual(report["status"], "awaiting_review")
            self.assertEqual(report["processed_this_run"], 0)

    def test_sampling_reads_one_then_resumes_after_semantic_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            sources.execution_log.write_json(root / "brief.json", {"destination": "测试城"})
            for scope in ("eat", "play"):
                sources.xhs_search_contract.store_search(root, {"search_design": {"destination": "测试城", "scope": scope, "stage": "discovery"},
                    "query": {"keyword": "测试城" + ("美食推荐" if scope == "eat" else "旅游推荐")},
                    "checked_at": "2026-10-26T10:00:00Z", "search_status": "success", "returned_count": 20,
                    "candidate_note_ids": [f"{scope}-{i}" for i in range(20)], "results": []})
            payload, _ = self.detail()
            def detail(args):
                result = deepcopy(payload)
                result["note"].update(note_id=args.note_id, source_url=f"https://www.xiaohongshu.com/explore/{args.note_id}")
                return result
            args = sources.build_parser().parse_args(["xhs-sample", "--workspace", str(root)])
            with patch.object(sources, "xhs_detail", side_effect=detail) as read, patch.object(sources.time, "sleep"):
                first = sources.xhs_sample(args)
                self.assertEqual(first["status"], "awaiting_review")
                self.assertEqual(read.call_count, 1)
                capture = next(iter(contract.latest_captures(root).values()))
                sources.execution_log.write_json(root / "state/itinerary-plan.json", {
                    "planning": {"xhs_comment_reviews": [self.reviewed(capture)]}})
                second = sources.xhs_sample(args)
                self.assertEqual(second["status"], "awaiting_review")
                self.assertEqual(read.call_count, 2)
                self.assertEqual(second["sampled_total"], 2)
                state = sources.xhs_state(args)
                self.assertEqual(state["comment_review"]["pending_note_count"], 1)

    def test_unavailable_failure_is_reported_without_becoming_a_review_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            sources.execution_log.write_json(root / "brief.json", {"destination": "测试城"})
            failure = {"note_id": "failed", "source_url": "https://www.xiaohongshu.com/explore/failed",
                       "checked_at": "2026-10-26T10:00:00Z", "review_status": "unavailable", "reason": "评论加载失败"}
            sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": [failure]}})
            args = sources.build_parser().parse_args(["xhs-sample", "--workspace", str(root)])
            with patch.object(sources, "xhs_mcp_call") as upstream:
                report = sources.xhs_sample(args)
                self.assertEqual(report["status"], "incomplete")
                self.assertEqual(report["comment_review"]["pending_note_count"], 0)
                self.assertEqual(report["comment_review"]["unavailable_notes"], [failure])
                packet = contract.review_packet(root)
                self.assertEqual(packet["status"], "unavailable")
                self.assertEqual(packet["unavailable_notes"], [failure])
                self.assertNotIn("analysis", packet)
                explicit = contract.review_packet(root, "failed")
                self.assertEqual(explicit["status"], "unavailable")
                self.assertEqual(explicit["unavailable_notes"], [failure])
                state = sources.xhs_state(args)
                self.assertEqual(state["comment_review"]["unavailable_note_count"], 1)
                self.assertIsNone(state["comment_review"]["next_action"])
                upstream.assert_not_called()

    def test_sampler_skips_unavailable_note_without_counting_it_toward_quota(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            sources.execution_log.write_json(root / "brief.json", {"destination": "测试城"})
            for scope in ("eat", "play"):
                sources.xhs_search_contract.store_search(root, {"search_design": {"destination": "测试城", "scope": scope, "stage": "discovery"},
                    "query": {"keyword": "测试城" + ("美食推荐" if scope == "eat" else "旅游推荐")},
                    "checked_at": "2026-10-26T10:00:00Z", "search_status": "success", "returned_count": 2,
                    "candidate_note_ids": [f"{scope}-0", f"{scope}-1"], "results": []})
            failure = {"note_id": "eat-0", "source_url": "https://www.xiaohongshu.com/explore/eat-0",
                       "checked_at": "2026-10-26T10:00:00Z", "review_status": "unavailable", "reason": "评论加载失败"}
            sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": [failure]}})
            payload, _ = self.detail()
            def detail(args):
                result = deepcopy(payload)
                result["note"].update(note_id=args.note_id, source_url=f"https://www.xiaohongshu.com/explore/{args.note_id}")
                return result
            args = sources.build_parser().parse_args(["xhs-sample", "--workspace", str(root)])
            with patch.object(sources, "xhs_detail", side_effect=detail) as read, patch.object(sources.time, "sleep"):
                report = sources.xhs_sample(args)
            self.assertEqual(read.call_args.args[0].note_id, "eat-1")
            self.assertEqual(read.call_count, 1)
            self.assertEqual(report["sampled_total"], 1)
            self.assertEqual(report["comment_review"]["unavailable_note_count"], 1)
            self.assertEqual(report["comment_review"]["pending_note_ids"], ["eat-1"])
            self.assertEqual(contract.review_packet(root)["note_id"], "eat-1")

    def test_sample_then_direct_detail_reuses_observation_and_preserves_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            sources.execution_log.write_json(root / "brief.json", {"destination": "测试城"})
            for scope in ("eat", "play"):
                sources.xhs_search_contract.store_search(root, {"search_design": {"destination": "测试城", "scope": scope, "stage": "discovery"},
                    "query": {"keyword": "测试城" + ("美食推荐" if scope == "eat" else "旅游推荐")},
                    "checked_at": "2026-10-26T10:00:00Z", "search_status": "success", "returned_count": 1,
                    "candidate_note_ids": [f"{scope}-0"], "results": []})
            payload, _ = self.detail()
            payload["note"].update(note_id="eat-0", source_url="https://www.xiaohongshu.com/explore/eat-0")
            args = sources.build_parser().parse_args(["xhs-sample", "--workspace", str(root)])
            with patch.object(sources, "xhs_detail", return_value=payload) as read, patch.object(sources.time, "sleep"):
                sources.xhs_sample(args)
                capture = contract.latest_captures(root)["eat-0"]
                decisions = [self.reviewed(capture)]
                sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": decisions}})
                with patch.object(sys, "argv", ["research_sources.py", "xhs-detail", "--note-id", "eat-0", "--workspace", str(root)]), patch.object(sources, "output") as output:
                    self.assertEqual(sources.main(), 0)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(output.call_args.args[0]["execution"]["mode"], "reused")
                self.assertEqual(output.call_args.args[0]["checked_at"], capture["checked_at"])
            self.assertEqual(len(list(root.glob("evidence/*/xhs-comments/*.json"))), 1)
            self.assertEqual(contract.load_reviews(root, decisions)[0]["review_status"], "reviewed")

    def test_successful_explicit_retry_replaces_failure_with_pending_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            failure = {"note_id": "failed", "source_url": "https://www.xiaohongshu.com/explore/failed",
                       "checked_at": "2026-10-25T10:00:00Z", "review_status": "unavailable", "reason": "评论加载失败"}
            sources.execution_log.write_json(root / "state/itinerary-plan.json", {"planning": {"xhs_comment_reviews": [failure]}})
            self.capture(root, note_id="failed")
            packet = contract.review_packet(root)
            self.assertEqual(packet["status"], "pending_review")
            self.assertEqual(packet["pending_note_ids"], ["failed"])
            self.assertEqual(packet["unavailable_note_count"], 0)

    def test_detail_cache_keeps_diagnostic_purpose_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1})
            payload, _ = self.detail()
            def detail(args):
                result = deepcopy(payload)
                result.update(capture_purpose=args.capture_purpose, diagnostic_reason=args.diagnostic_reason)
                return result
            with patch.object(sources, "xhs_detail", side_effect=detail) as read:
                for purpose in ([], ["--capture-purpose", "diagnostic", "--diagnostic-reason", "协议诊断"], []):
                    argv = ["research_sources.py", "xhs-detail", "--note-id", "note1", "--workspace", str(root), *purpose]
                    with patch.object(sys, "argv", argv), patch.object(sources, "output"):
                        self.assertEqual(sources.main(), 0)
                self.assertEqual(read.call_count, 2)
            self.assertEqual(len(list(root.glob("evidence/*/xhs-comments/*.json"))), 2)
            self.assertEqual(contract.latest_captures(root)["note1"]["capture_purpose"], "research")

    def test_documented_review_schema_distinguishes_input_from_assembled_metadata(self):
        schema = json.loads((SCRIPTS.parent / "schemas/xhs-comment-review.schema.json").read_text())
        guide = (SCRIPTS.parent / "references/xhs-comment-research.md").read_text()
        example = json.loads(re.search(r"```json\n(.*?)\n```", guide, re.S).group(1))
        self.assertTrue(set(schema["required"]).issubset(example))
        for rule in schema["allOf"]:
            condition = rule["if"]
            if set(condition.get("required", [])).issubset(example) and all(
                example.get(key) == value.get("const") for key, value in condition.get("properties", {}).items()):
                self.assertTrue(set(rule["then"].get("required", [])).issubset(example))
        self.assertNotIn("thread_manifest", example)
        self.assertIn("thread_manifest", schema["$defs"]["assembled"]["allOf"][1]["then"]["required"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, capture = self.capture(root)
            decision = self.reviewed(capture)
            self.assertNotIn("thread_manifest", decision)
            assembled = contract.load_reviews(root, [decision])[0]
            self.assertEqual(assembled["thread_manifest"], capture["thread_manifest"])

    def test_share_and_release_preparation_reject_outdated_html(self):
        share = load("prepare_share")
        publisher = load("publish_itinerary")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            _, capture = self.capture(root)
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2,
                    "xhs_comment_reviews": contract.load_reviews(root, [self.reviewed(capture)])}}
            sources.execution_log.write_json(root / "artifacts/itinerary.json", data)
            page = root / "artifacts/itinerary.html"
            page.write_text('<html><head><meta name="xhs-review-digest" content="' + contract.review_digest(data) + '"></head></html>')
            share.prepare_share(page, root / "share")
            self.capture(root, checked_at="2026-10-26T11:00:00Z")
            with self.assertRaisesRegex(ValueError, "产物过期"):
                share.prepare_share(page, root / "other")
            with self.assertRaisesRegex(ValueError, "产物过期"):
                publisher.prepare(page, "test-2026-10-26", root / "release")
            # Reassembly alone cannot make the earlier HTML current.
            data["planning"]["xhs_comment_reviews"] = contract.load_reviews(root, [])
            sources.execution_log.write_json(root / "artifacts/itinerary.json", data)
            with self.assertRaisesRegex(ValueError, "HTML.*过期"):
                contract.validate_page_evidence(page, page.read_text())

    def test_custom_output_share_and_release_keep_freshness_checks(self):
        share = load("prepare_share")
        publisher = load("publish_itinerary")
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / ".travel-research/trip-2026-10-26"
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            _, capture = self.capture(root)
            decision = self.reviewed(capture)
            plan_path = root / "state/itinerary-plan.json"
            sources.execution_log.write_json(plan_path, {"planning": {"xhs_comment_reviews": [decision]}})
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2,
                    "xhs_comment_reviews": contract.load_reviews(root, [decision])}}
            output = base / "exports/custom.json"
            sources.execution_log.write_json(output, data)
            contract.record_plan_input(root, plan_path, data, output)
            page = root / "artifacts/itinerary.html"
            page.parent.mkdir(parents=True)
            page.write_text('<html><head><meta name="xhs-review-digest" content="' + contract.review_digest(data) + '"></head></html>')
            shared = share.prepare_share(page, base / "travel-plans/trip-2026-10-26")
            publisher.prepare(shared, "trip-2026-10-26", base / "release")
            self.assertFalse((root / "artifacts/itinerary.json").exists())
            self.assertNotIn(str(output), shared.read_text())
            decision["reason"] = "实际审查结论发生修改，旧页面须重新装配"
            sources.execution_log.write_json(plan_path, {"planning": {"xhs_comment_reviews": [decision]}})
            with self.assertRaisesRegex(ValueError, "产物过期"):
                share.prepare_share(page, base / "blocked-share")
            with self.assertRaisesRegex(ValueError, "产物过期"):
                publisher.prepare(shared, "trip-2026-10-26", base / "blocked-release")
            self.assertFalse((base / "blocked-share").exists())
            self.assertFalse((base / "blocked-release").exists())

    def test_old_input_record_output_fallback_and_invalid_explicit_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources.execution_log.write_json(root / "manifest.json", {"workspace_version": 1, "xhs_comment_research_version": 2})
            plan_path = root / "state/itinerary-plan.json"
            sources.execution_log.write_json(plan_path, {"planning": {"xhs_comment_reviews": []}})
            data = {"workflow": {"phase": "final"}, "planning": {"xhs_comment_research_version": 2, "xhs_comment_reviews": []}}
            sources.execution_log.write_json(root / "artifacts/itinerary.json", data)
            old_record = {"schema_version": "travel-xhs-comment-input/v1", "plan_path": str(plan_path.resolve()),
                          "review_digest": contract.review_digest(data)}
            record_path = root / "state/xhs-comment-input.json"
            sources.execution_log.write_json(record_path, old_record)
            page = root / "artifacts/itinerary.html"
            html = '<html><head><meta name="xhs-review-digest" content="' + contract.review_digest(data) + '"></head></html>'
            contract.validate_page_evidence(page, html)
            # Explicit output metadata must fail instead of using an unrelated default JSON.
            for value in (None, 42, "relative.json", str(root / "missing.json")):
                with self.subTest(output_path=value):
                    sources.execution_log.write_json(record_path, {**old_record, "output_path": value})
                    with self.assertRaises(ValueError):
                        contract.validate_page_evidence(page, html)
            output = root / "invalid.json"
            sources.execution_log.write_json(output, [])
            sources.execution_log.write_json(record_path, {**old_record, "output_path": str(output.resolve())})
            with self.assertRaisesRegex(ValueError, "实际装配输出格式无效"):
                contract.validate_page_evidence(page, html)

    def test_shared_queue_keyword_does_not_create_consensus(self):
        result = sources.build_xhs_comment_insights([
            {"comment_id": "a", "content": "甲店我等了20分钟"},
            {"comment_id": "b", "content": "乙店我等了40分钟"}])
        self.assertEqual(len(result["review_threads"]), 2)
        self.assertEqual(result["comment_insights"], [])

    def test_gate_requires_adopted_comments_to_enter_planning_evidence(self):
        data = self.plan()
        review = data["planning"]["xhs_comment_reviews"][0]
        decision = review["insight_decisions"][0]
        decision.update(decision="needs_recheck", reason="入口施工可能改变路线", recheck_action="核验当日运营公告")
        self.assertTrue(contract.audit_reviews(data)[0])
        data["planning"]["community_evidence"] = [{"comment_note_id": "note1", "comment_insight_ids": [decision["insight_id"]],
            "source_ids": ["s1"], "needs_official_recheck": True, "support_count": 1}]
        blocking, _, stats = contract.audit_reviews(data)
        self.assertEqual(blocking, [])
        self.assertEqual(stats["top_level_count"], 2)
        self.assertEqual(stats["reply_count"], 2)

    def test_gate_rejects_bad_counts_and_duplicate_note_reviews(self):
        data = self.plan()
        data["planning"]["xhs_comment_reviews"][0]["analysis"]["input_count"] = 99
        self.assertTrue(any("不闭合" in b for b in contract.audit_reviews(data)[0]))
        data = self.plan()
        data["planning"]["xhs_comment_reviews"].append(deepcopy(data["planning"]["xhs_comment_reviews"][0]))
        self.assertTrue(any("重复" in b for b in contract.audit_reviews(data)[0]))

    def test_unavailable_and_legacy_do_not_claim_zero_or_completed(self):
        data = self.plan()
        data["planning"]["xhs_comment_reviews"] = [{"note_id": "note1", "source_url": data["sources"][0]["url"],
            "checked_at": "2026-10-14T10:00:00+08:00", "review_status": "unavailable", "reason": "评论加载失败"}]
        blocking, warnings, stats = contract.audit_reviews(data)
        self.assertEqual(blocking, [])
        self.assertTrue(any("未取得" in w for w in warnings))
        self.assertEqual(stats["eligible_count"], 0)
        data["planning"].pop("xhs_comment_research_version")
        data["planning"]["xhs_comment_reviews"] = []
        self.assertEqual(contract.audit_reviews(data)[0], [])
        self.assertTrue(contract.audit_reviews(data)[1])

    def test_final_auditor_invokes_comment_gate(self):
        data = json.loads((SCRIPTS.parent / "assets/example-itinerary.json").read_text())
        data["workflow"]["phase"] = "final"
        data["planning"]["xhs_comment_research_version"] = 1
        data["sources"].append({"id": "s1", "kind": "community", "provider": "xiaohongshu", "url": "https://www.xiaohongshu.com/explore/note1"})
        self.assertTrue(any("缺少评论抓取统计" in b for b in audit.audit(data)["blocking"]))
        with self.assertRaisesRegex(ValueError, "缺少评论抓取统计"):
            audit.render_itinerary.validate_data(data)

    def test_comment_correction_survives_final_audit_and_html_rendering(self):
        data = json.loads((SCRIPTS.parent / "assets/example-itinerary.json").read_text())
        for source in data["sources"]:
            if "xiaohongshu.com" in str(source.get("url")):
                source.update(url="https://fixture.invalid/community", provider="public_web_ugc", title="测试亲历资料")
        sample = self.plan()
        data["workflow"]["phase"] = "final"
        data["sources"].append({"id": "s1", "title": "测试原帖", "kind": "community", "provider": "xiaohongshu",
            "url": sample["sources"][0]["url"]})
        data["planning"].update(sample["planning"])
        decision = data["planning"]["xhs_comment_reviews"][0]["insight_decisions"][0]
        decision.update(decision="needs_recheck", reason="评论纠正正文入口，需官方复核", recheck_action="核验景区公告")
        data["planning"]["community_evidence"] = [{"target_entity_id": data["planning"]["attractions"][0]["id"],
            "target_entity_type": "attraction", "provider": "xiaohongshu", "checked_at": "2026-10-14T00:00:00Z",
            "topic": "入口施工", "insight": "评论称东门施工，改走南门的建议需核公告", "support_count": 1,
            "contradiction_count": 0, "community_confidence": "low", "needs_official_recheck": True,
            "source_ids": ["s1"], "comment_note_id": "note1", "comment_insight_ids": [decision["insight_id"]]}]
        result = audit.audit(data)
        self.assertEqual(result["blocking"], [])
        self.assertEqual(result["community_audit"]["comment_research"]["input_count"], 4)
        # Semantic decisions are rendered through event text, not private raw comments.
        data["days"][0]["events"][1]["preparation"].append("评论提示入口施工，出发前核对公告与南门路线")
        html = audit.render_itinerary.build(data)
        self.assertTrue("评论提示入口施工" in html)
        self.assertNotIn("private-test-token", html)


if __name__ == "__main__":
    unittest.main()
