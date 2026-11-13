from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_multi_source_research as multi
import test_xhs_sampling as sampling

s = sampling.s
contract = s.xhs_search_contract
comments = s.xhs_comment_contract
workspace = multi.workspace


class XhsOptionalSourceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = sampling.XhsRouteSamplingTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.set_mode("optional")

    def set_mode(self, mode):
        manifest = s.execution_log.read_json(self.root / "manifest.json")
        manifest["xhs_research_mode"] = mode
        s.execution_log.write_json(self.root / "manifest.json", manifest)

    def data(self):
        data = self.fixture.data()
        data["planning"]["xhs_research_mode"] = "optional"
        return data

    def failed_search(self):
        contract.store_search(self.root, {
            "search_design": {"destination": "成都", "scope": "eat", "stage": "discovery"},
            "query": {"keyword": "成都美食推荐"}, "checked_at": "2026-11-13T10:00:00Z",
            "search_status": "failed", "failure_kind": "login_required"})

    def web_data(self):
        data = multi.web_only_plan()
        case = self.data()
        data["workflow"] = case["workflow"]
        data["route_proposals"] = case["route_proposals"]
        data["planning"].update(case["planning"])
        data["planning"]["research_contract"]["capabilities"]["xiaohongshu"] = "not_requested"
        return data

    def test_unused_optional_source_has_no_fifteen_note_requirement(self):
        self.assertEqual(contract.audit_searches(self.data(), self.root)[0], [])
        self.assertEqual(contract.audit_searches(self.data())[0], [])

    def test_service_readiness_does_not_enable_quota_without_citations(self):
        data = self.data()
        data["planning"]["research_contract"] = {"capabilities": {"xiaohongshu": "ready"}}
        with patch.object(s, "xhs_mcp_call") as upstream:
            self.assertEqual(contract.audit_searches(data, self.root)[0], [])
        upstream.assert_not_called()

    def test_optional_state_does_not_demand_quota_for_a_successful_search(self):
        self.fixture.discover("成都")
        state = contract.observation_state(self.root)
        topics = [topic for topic in state["topics"] if topic["destination"] == "成都"]
        self.assertEqual(state["xhs_research_mode"], "optional")
        self.assertTrue(all(topic["next_action"] == "review_source_usage" for topic in topics))

    def test_failed_optional_source_can_fall_back_with_reason(self):
        self.failed_search()
        data = self.data()
        data["planning"]["research_contract"] = {
            "capabilities": {"xiaohongshu": "unavailable"},
            "xiaohongshu_fallback_reason": "本轮登录失效；改用已读公开游记和官方资料，没有引用小红书。"}
        blocking, warnings = contract.audit_searches(data, self.root)
        self.assertEqual(blocking, [])
        self.assertTrue(any("降级" in warning for warning in warnings))
        self.assertEqual(data["planning"]["xhs_search_records"][0]["status"], "failed")

    def test_failed_or_unavailable_source_must_explain_fallback(self):
        for fail_search in (False, True):
            with self.subTest(fail_search=fail_search):
                if fail_search:
                    self.failed_search()
                data = self.data()
                data["planning"]["research_contract"] = {"capabilities": {"xiaohongshu": "unavailable"}}
                issues = contract.audit_searches(data, self.root)[0]
                self.assertTrue(any("降级原因" in issue for issue in issues), issues)

    def test_detail_failure_or_unavailable_comments_require_fallback_reason(self):
        for failure in ("detail", "comments"):
            data = self.data()
            data["planning"]["research_contract"] = {"capabilities": {"xiaohongshu": "ready"}, "queries": []}
            if failure == "detail":
                data["planning"]["research_contract"]["queries"].append({
                    "provider": "xiaohongshu", "tool": "get_feed_detail", "status": "failed", "reason": "详情格式异常"})
            else:
                data["planning"]["xhs_comment_reviews"] = [{"note_id": "unavailable", "review_status": "unavailable"}]
            with self.subTest(failure=failure):
                self.assertTrue(any("降级原因" in issue for issue in contract.audit_searches(data, self.root)[0]))

    def test_cited_optional_source_still_requires_each_city_quota(self):
        data = self.data()
        data["sources"] = [{"id": "xhs-1", "url": "https://www.xiaohongshu.com/explore/n1"}]
        issues = contract.audit_searches(data, self.root)[0]
        self.assertTrue(any("成都" in issue and "0/15" in issue for issue in issues), issues)
        self.assertTrue(any("重庆" in issue and "0/15" in issue for issue in issues), issues)

    def test_provider_shortlink_and_comment_binding_cannot_hide_source_use(self):
        for source in ({"id": "x", "url": "https://xhslink.com/example"},
                       {"id": "x", "provider": "小红书", "url": "https://example.com/forwarded"},
                       {"id": "x", "url": "https://www.xiaohongshu.com/explore/n1", "origin_url": "https://example.com/original"}):
            data = self.data()
            data["sources"] = [source]
            self.assertTrue(any("0/15" in issue for issue in contract.audit_searches(data, self.root)[0]))
        data = self.data()
        data["planning"]["community_evidence"] = [{"comment_note_id": "n1", "comment_insight_ids": ["i1"]}]
        self.assertTrue(any("0/15" in issue for issue in contract.audit_searches(data, self.root)[0]))

    def test_required_mode_cannot_be_waived_by_source_failure(self):
        self.set_mode("required")
        self.failed_search()
        data = self.data()
        data["planning"]["xhs_research_mode"] = "required"
        data["planning"]["research_contract"] = {
            "capabilities": {"xiaohongshu": "unavailable"}, "xiaohongshu_fallback_reason": "服务不可用"}
        self.assertTrue(any("0/15" in issue for issue in contract.audit_searches(data, self.root)[0]))

    def test_legacy_mandatory_contract_does_not_silently_become_optional(self):
        manifest = s.execution_log.read_json(self.root / "manifest.json")
        manifest.pop("xhs_research_mode")
        s.execution_log.write_json(self.root / "manifest.json", manifest)
        data = self.data()
        data["planning"].pop("xhs_research_mode")
        self.assertTrue(any("0/15" in issue for issue in contract.audit_searches(data, self.root)[0]))

    def test_mode_deletion_downgrade_and_invalid_values_are_blocked(self):
        for value in (None, "disabled", False):
            data = self.data()
            if value is None:
                data["planning"].pop("xhs_research_mode")
            else:
                data["planning"]["xhs_research_mode"] = value
            with self.subTest(value=value):
                self.assertTrue(any("模式" in issue for issue in contract.audit_searches(data, self.root)[0]))
        self.set_mode("required")
        self.assertTrue(any("模式" in issue for issue in contract.audit_searches(self.data(), self.root)[0]))

    def test_optional_mode_does_not_waive_pending_comment_reviews(self):
        payload = self.fixture.payload("pending-note")
        comments.store_capture(self.root, payload)
        data = self.data()
        self.assertEqual(contract.audit_searches(data, self.root)[0], [])
        blocking, _, stats = comments.audit_reviews(data, self.root)
        self.assertTrue(blocking)
        self.assertEqual(stats["pending_note_count"], 1)

    def test_reviewed_but_unused_notes_do_not_require_fifteen_samples(self):
        self.fixture.finish_city("成都", eat=1, play=1)
        data = self.data()
        self.assertEqual(contract.audit_searches(data, self.root)[0], [])
        self.assertEqual(comments.audit_reviews(data, self.root)[0], [])

    def test_new_web_only_plan_passes_renderer_and_final_auditor(self):
        data = self.web_data()
        multi.render.validate_data(data, self.root)
        self.assertEqual(multi.audit.audit(data, self.root)["blocking"], [])
        self.assertIn("推荐与核验依据", multi.render.build(data))

    def test_fallback_does_not_waive_other_source_evidence_requirements(self):
        data = self.web_data()
        data["planning"]["research_contract"]["queries"] = []
        with self.assertRaisesRegex(ValueError, "网页搜索"):
            multi.render.validate_data(data, self.root)

    def test_not_requested_status_is_invalid_when_xhs_is_cited(self):
        data = self.web_data()
        data["sources"].append({"id": "xhs", "provider": "xiaohongshu", "url": "https://www.xiaohongshu.com/explore/n1"})
        self.assertTrue(any("未请求" in issue for issue in multi.evidence.audit_research(data, self.root)[0]))

    def test_explicit_mode_change_requires_reason_and_makes_old_page_stale(self):
        data = self.web_data()
        s.execution_log.write_json(self.root / "artifacts/itinerary.json", data)
        page = self.root / "artifacts/itinerary.html"
        html = ('<html><head><meta name="xhs-review-digest" content="' + comments.review_digest(data)
                + '"><meta name="xhs-policy-digest" content="' + contract.policy_digest(data) + '"></head></html>')
        page.write_text(html)
        comments.validate_page_evidence(page, html)
        before = (self.root / "manifest.json").read_bytes()
        with self.assertRaises(workspace.WorkspaceError):
            workspace.set_xhs_mode(Namespace(workspace=str(self.root), mode="required", reason=""))
        self.assertEqual((self.root / "manifest.json").read_bytes(), before)
        workspace.set_xhs_mode(Namespace(workspace=str(self.root), mode="required", reason="用户要求必须使用小红书"))
        with self.assertRaisesRegex(ValueError, "模式"):
            comments.validate_page_evidence(page, html)

    def test_reassembly_after_mode_change_still_requires_new_html(self):
        self.fixture.finish_city("成都")
        self.fixture.finish_city("重庆")
        self.set_mode("required")
        data = self.web_data()
        data["planning"]["xhs_research_mode"] = "required"
        s.execution_log.write_json(self.root / "artifacts/itinerary.json", data)
        page = self.root / "artifacts/itinerary.html"
        html = ('<html><head><meta name="xhs-review-digest" content="' + comments.review_digest(data)
                + '"><meta name="xhs-policy-digest" content="' + contract.policy_digest(data) + '"></head></html>')
        page.write_text(html)
        comments.validate_page_evidence(page, html)
        workspace.set_xhs_mode(Namespace(workspace=str(self.root), mode="optional", reason="用户取消必须使用小红书"))
        data["planning"]["xhs_research_mode"] = "optional"
        s.execution_log.write_json(self.root / "artifacts/itinerary.json", data)
        with self.assertRaisesRegex(ValueError, "模式"):
            comments.validate_page_evidence(page, html)

    def test_removing_xhs_citation_requires_rerender_even_with_same_reviews(self):
        self.fixture.finish_city("成都")
        self.fixture.finish_city("重庆")
        data = self.web_data()
        note_id = self.fixture.note_id("成都", "eat", 0)
        data["sources"].append({"id": "xhs", "url": f"https://www.xiaohongshu.com/explore/{note_id}"})
        data["planning"]["research_contract"]["capabilities"]["xiaohongshu"] = "ready"
        path = self.root / "artifacts/itinerary.json"
        s.execution_log.write_json(path, data)
        page = self.root / "artifacts/itinerary.html"
        html = ('<html><head><meta name="xhs-review-digest" content="' + comments.review_digest(data)
                + '"><meta name="xhs-policy-digest" content="' + contract.policy_digest(data) + '"></head></html>')
        page.write_text(html)
        comments.validate_page_evidence(page, html)
        data["sources"] = [source for source in data["sources"] if source["id"] != "xhs"]
        s.execution_log.write_json(path, data)
        with self.assertRaisesRegex(ValueError, "引用状态"):
            comments.validate_page_evidence(page, html)

    def test_restaurant_note_references_also_trigger_quota(self):
        data = self.data()
        data["planning"]["restaurant_snapshots"] = [{"community_consensus": {
            "platform": "小红书", "references": [{"url": "https://www.xiaohongshu.com/explore/n1"}]}}]
        self.assertTrue(any("0/15" in issue for issue in contract.audit_searches(data, self.root)[0]))

    def test_optional_citations_pass_after_every_city_is_fully_sampled(self):
        self.fixture.finish_city("成都")
        self.fixture.finish_city("重庆")
        data = self.data()
        data["sources"] = [{"id": "x", "url": "https://www.xiaohongshu.com/explore/"
                            + self.fixture.note_id("成都", "eat", 0)}]
        self.assertEqual(contract.audit_searches(data, self.root)[0], [])

    def test_mode_change_cli_preserves_observations(self):
        self.fixture.finish_city("成都", eat=1, play=1)
        before = {str(path): path.read_bytes() for path in (self.root / "evidence").glob("*/*/*.json")}
        argv = ["research_workspace.py", "set-xhs-mode", "--workspace", str(self.root),
                "--mode", "required", "--reason", "用户明确要求小红书研究"]
        with patch.object(sys, "argv", argv), patch.object(workspace, "output"):
            self.assertEqual(workspace.main(), 0)
        for name, contents in before.items():
            self.assertEqual(Path(name).read_bytes(), contents)
        self.assertEqual(s.execution_log.read_json(self.root / "manifest.json")["xhs_research_mode"], "required")

    def test_explicit_mode_on_legacy_workspace_enables_the_full_contract(self):
        manifest = s.execution_log.read_json(self.root / "manifest.json")
        manifest.pop("xhs_detail_sampling_version")
        manifest.pop("xhs_research_mode")
        s.execution_log.write_json(self.root / "manifest.json", manifest)
        workspace.set_xhs_mode(Namespace(workspace=str(self.root), mode="required", reason="用户要求补做完整社区研究"))
        manifest = s.execution_log.read_json(self.root / "manifest.json")
        self.assertEqual(manifest["xhs_detail_sampling_version"], 1)
        self.assertEqual(manifest["xhs_search_design_version"], 1)
        data = self.data()
        data["planning"]["xhs_research_mode"] = "required"
        self.assertTrue(any("0/15" in issue for issue in contract.audit_searches(data, self.root)[0]))

    def test_standalone_explicit_mode_cannot_drop_sampling_version(self):
        data = self.data()
        data["planning"].pop("xhs_detail_sampling_version")
        self.assertTrue(any("xhs_detail_sampling_version" in issue for issue in contract.audit_searches(data)[0]))


if __name__ == "__main__":
    unittest.main()
