from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
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
        insight = next(i for i in payload["comment_insights"] if "施工" in i["claim"])
        self.assertEqual(insight["support_count"], 1)
        self.assertEqual(payload["comment_analysis"]["duplicate_count"], 0)
        self.assertEqual(insight["evidence"][0]["parent_comment_id"], "c1")
        self.assertIn("预约", insight["evidence"][0]["parent_excerpt"])

    def test_rhetorical_correction_survives_question_filter(self):
        payload, _ = self.detail()
        self.assertTrue(any("40分钟" in i["claim"] for i in payload["comment_insights"]))
        self.assertFalse(any(i["claim"].startswith("请问") for i in payload["comment_insights"]))

    def test_short_reply_inherits_question_topic(self):
        comments = sources.normalize_xhs_comments({"comments": {"list": [{"id": "q", "content": "景区需要预约吗？",
            "subComments": [{"id": "a", "content": "已经取消了，不用了", "createTime": 1791936000000}]}]}}, True)
        result = sources.build_xhs_comment_insights(comments, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
        self.assertEqual(result["comment_insights"][0]["topic"], "预约、购票与检票")
        self.assertEqual(result["comment_insights"][0]["evidence"][0]["parent_comment_id"], "q")

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
            review = {"note_id": "note1", "checked_at": stored["checked_at"], "review_status": "reviewed", "reason": "评论纠正正文"}
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
        return {"workflow": {"phase": "final"}, "sources": [{"id": "s1", "url": payload["note"]["source_url"]}],
            "planning": {"xhs_comment_research_version": 1, "community_evidence": [], "xhs_comment_reviews": [{
                "note_id": "note1", "source_url": payload["note"]["source_url"], "checked_at": payload["checked_at"],
                "collection": payload["comment_collection"], "analysis": payload["comment_analysis"], "review_status": "reviewed",
                "reason": "读过问题和回复；施工影响入口，排队与正文冲突", "insight_ids": [i["insight_id"] for i in payload["comment_insights"]],
                "insight_decisions": [{"insight_id": i["insight_id"], "decision": "rejected", "reason": "与本次区域不符"}
                                      for i in payload["comment_insights"]]}]}}

    def test_gate_blocks_missing_pending_and_undecided_reviews(self):
        for change in (lambda p: p.update(xhs_comment_reviews=[]),
                       lambda p: p["xhs_comment_reviews"][0].update(review_status="pending"),
                       lambda p: p["xhs_comment_reviews"][0].update(insight_decisions=[])):
            data = self.plan()
            change(data["planning"])
            self.assertTrue(contract.audit_reviews(data)[0])

    def test_gate_requires_adopted_comments_to_enter_planning_evidence(self):
        data = self.plan()
        review = data["planning"]["xhs_comment_reviews"][0]
        decision = review["insight_decisions"][0]
        decision.update(decision="needs_recheck", reason="入口施工可能改变路线")
        self.assertTrue(contract.audit_reviews(data)[0])
        data["planning"]["community_evidence"] = [{"comment_note_id": "note1", "comment_insight_ids": [decision["insight_id"]],
            "source_ids": ["s1"], "needs_official_recheck": True}]
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
        decision.update(decision="needs_recheck", reason="评论纠正正文入口，需官方复核")
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
