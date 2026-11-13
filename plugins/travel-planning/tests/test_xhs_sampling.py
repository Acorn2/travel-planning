from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_research_sources import research_sources as s

contract = s.xhs_search_contract
comments = s.xhs_comment_contract


class XhsRouteSamplingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.route = {"id": "confirmed-route", "cities": ["成都", "重庆"]}
        s.execution_log.write_json(self.root / "manifest.json", {
            "workspace_version": 1, "xhs_detail_sampling_version": 1,
            "xhs_comment_research_version": 2, "phase": "confirmed_planning",
            "selected_route_id": self.route["id"]})
        s.execution_log.write_json(self.root / "brief.json", {"destination": "成都、重庆", "origin": "北京"})
        s.execution_log.write_json(self.root / "selected-route.json", self.route)
        self.decisions = []

    def note_id(self, city, scope, index):
        return s.execution_log.fingerprint(city)[:8] + f"-{scope}-{index}"

    def discover(self, city, *, eat=10, play=5):
        for scope, count in (("eat", eat), ("play", play)):
            contract.store_search(self.root, {
                "search_design": {"destination": city, "scope": scope, "stage": "discovery"},
                "query": {"keyword": contract.discovery_keyword(city, scope)},
                "checked_at": "2026-10-26T10:00:00Z", "search_status": "success",
                "returned_count": count, "count": 0, "candidate_note_ids": [self.note_id(city, scope, i) for i in range(count)],
                "results": []})

    def payload(self, note_id):
        prepared = s.build_xhs_comment_insights([])
        return {"status": "community_reported", "text_content_version": comments.TEXT_CONTENT_VERSION,
                "note": {"note_id": note_id, "source_url": f"https://www.xiaohongshu.com/explore/{note_id}",
                         "title": "测试笔记", "description": "测试正文", "description_excerpt": "测试正文"},
                "checked_at": s.checked_at(), "comment_collection": {"mode": "expanded"},
                "comment_analysis": prepared["comment_analysis"], "comment_insights": [],
                "thread_manifest": [], "review_threads": [], "discussion_samples": [],
                "image_reading": {"image_count": 0, "status": "no_images"}}

    def finish_city(self, city, *, eat=10, play=5):
        self.discover(city, eat=eat, play=play)
        for scope, count in (("eat", eat), ("play", play)):
            for i in range(count):
                note_id = self.note_id(city, scope, i)
                payload = self.payload(note_id)
                comments.store_capture(self.root, payload)
                capture = comments.latest_captures(self.root)[note_id]
                self.decisions.append({"note_id": note_id, "checked_at": capture["checked_at"],
                    "capture_id": capture["capture_id"], "review_status": "reviewed", "reviewed_thread_ids": [],
                    "reason": "已审查空评论样本，无可采用信息", "insight_decisions": []})
        s.execution_log.write_json(self.root / "state/itinerary-plan.json", {
            "planning": {"xhs_comment_reviews": self.decisions}})

    def data(self):
        return {"workflow": {"phase": "final", "selected_route_id": self.route["id"]},
                "route_proposals": [deepcopy(self.route), {"id": "not-selected", "cities": ["成都"]}],
                "planning": {"xhs_search_design_version": 1, "xhs_detail_sampling_version": 1,
                    "xhs_search_records": contract.records(self.root),
                    "xhs_comment_reviews": comments.load_reviews(self.root, self.decisions)}}

    def test_missing_city_is_blocked_even_without_any_search_for_it(self):
        self.finish_city("成都")
        issues = contract.audit_searches(self.data())[0]
        self.assertTrue(any("重庆" in issue and "0/15" in issue for issue in issues), issues)
        self.assertFalse(any("成都 小红书详情采样未达标" in issue for issue in issues))

    def test_each_city_must_meet_total_and_both_topic_quotas(self):
        self.finish_city("成都")
        self.finish_city("重庆", eat=11, play=4)
        issues = contract.audit_searches(self.data())[0]
        self.assertTrue(any("重庆" in issue and "玩 4/5" in issue for issue in issues), issues)

    def test_all_confirmed_cities_pass_and_unselected_city_is_not_required(self):
        self.finish_city("成都")
        self.finish_city("重庆")
        data = self.data()
        data["route_proposals"][1]["cities"] = ["杭州"]
        self.assertEqual(contract.audit_searches(data)[0], [])
        progress = contract.sampling_progress(self.root)
        self.assertEqual(progress["required_destinations"], ["成都", "重庆"])
        self.assertTrue(progress["complete"])
        self.assertEqual(set(progress["by_destination"]), {"成都", "重庆"})
        with patch.object(s, "xhs_detail") as detail:
            report = s.xhs_sample(s.build_parser().parse_args(["xhs-sample", "--workspace", str(self.root)]))
        detail.assert_not_called()
        self.assertEqual(report["status"], "complete")

    def test_default_sampler_advances_to_next_incomplete_route_city(self):
        self.finish_city("成都")
        self.discover("重庆")
        with patch.object(s, "xhs_detail", side_effect=lambda args: self.payload(args.note_id)) as detail, \
             patch.object(s.time, "sleep"):
            report = s.xhs_sample(s.build_parser().parse_args(["xhs-sample", "--workspace", str(self.root)]))
        self.assertEqual(report["status"], "awaiting_review")
        self.assertEqual(detail.call_args.args[0].note_id, self.note_id("重庆", "eat", 0))
        self.assertEqual(report["destination"], "重庆")
        self.assertEqual(report["by_destination"]["成都"]["sampled_total"], 15)
        self.assertEqual(report["by_destination"]["重庆"]["sampled_total"], 1)
        self.assertFalse(report["complete"])
        self.assertIn("note", report["review_packet"])

    def test_explicit_completed_city_does_not_hide_other_city_or_overread(self):
        self.finish_city("成都")
        with patch.object(s, "xhs_detail") as detail:
            report = s.xhs_sample(s.build_parser().parse_args([
                "xhs-sample", "--workspace", str(self.root), "--destination", "成都"]))
        detail.assert_not_called()
        self.assertEqual(report["status"], "incomplete")
        self.assertTrue(report["destination_complete"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["by_destination"]["重庆"]["deficit_total"], 15)

    def test_cli_returns_incomplete_for_single_city_completion_without_upstream_calls(self):
        self.finish_city("成都")
        argv = ["research_sources.py", "xhs-sample", "--workspace", str(self.root), "--destination", "成都"]
        with patch.object(sys, "argv", argv), patch.object(s, "output") as output, \
             patch.object(s, "xhs_mcp_call") as upstream:
            self.assertEqual(s.main(), 2)
        upstream.assert_not_called()
        report = output.call_args.args[0]
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["next_action"], "xhs-sample")
        self.assertEqual(report["required_destinations"], ["成都", "重庆"])

    def test_city_outside_confirmed_route_is_rejected_before_reading(self):
        with patch.object(s, "xhs_mcp_call") as upstream, self.assertRaises(s.SourceError):
            s.xhs_sample(s.build_parser().parse_args([
                "xhs-sample", "--workspace", str(self.root), "--destination", "杭州"]))
        upstream.assert_not_called()

    def test_state_lists_every_confirmed_city_and_its_missing_topics(self):
        self.finish_city("成都")
        state = contract.observation_state(self.root)
        self.assertFalse(state["detail_sampling"]["complete"])
        missing = [t for t in state["topics"] if t["destination"] == "重庆"]
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(t["next_action"] == "run_discovery" for t in missing))

    def test_missing_or_malformed_confirmed_city_list_never_falls_back_to_searches(self):
        self.finish_city("成都")
        for cities in (None, [], "成都、重庆", ["成都", None], ["成都、重庆"]):
            with self.subTest(cities=cities):
                route = {"id": self.route["id"], "cities": cities}
                s.execution_log.write_json(self.root / "selected-route.json", route)
                with self.assertRaises(ValueError):
                    contract.sampling_progress(self.root)
                data = self.data()
                data["route_proposals"][0] = route
                self.assertTrue(contract.audit_searches(data)[0])

    def test_snapshot_cannot_drop_a_city_from_current_confirmed_route(self):
        self.finish_city("成都")
        self.finish_city("重庆")
        data = self.data()
        changed = {"id": self.route["id"], "cities": ["成都", "重庆", "杭州"]}
        s.execution_log.write_json(self.root / "selected-route.json", changed)
        issues = contract.audit_searches(data, self.root)[0]
        self.assertTrue(any("杭州" in issue for issue in issues), issues)

    def test_unknown_selected_route_id_is_not_replaced_by_another_proposal(self):
        self.finish_city("成都")
        self.finish_city("重庆")
        data = self.data()
        data["workflow"]["selected_route_id"] = "missing-route"
        self.assertTrue(contract.audit_searches(data)[0])

    def test_removing_search_or_sampling_version_cannot_skip_route_city_gate(self):
        self.finish_city("成都")
        data = self.data()
        data["planning"].pop("xhs_search_design_version")
        self.assertTrue(any("重庆" in issue for issue in contract.audit_searches(data)[0]))
        data["planning"].pop("xhs_detail_sampling_version")
        self.assertTrue(any("重庆" in issue for issue in contract.audit_searches(data, self.root)[0]))

    def test_state_before_confirmation_reports_route_requirement_without_network(self):
        s.execution_log.write_json(self.root / "selected-route.json", None)
        with patch.object(s, "xhs_mcp_call") as upstream:
            state = contract.observation_state(self.root)
        self.assertTrue(state["detail_sampling"]["route_required"])
        self.assertFalse(state["detail_sampling"]["complete"])
        self.assertEqual(state["topics"], [])
        upstream.assert_not_called()

    def test_repeated_city_visits_share_one_quota(self):
        s.execution_log.write_json(self.root / "selected-route.json", {
            "id": self.route["id"], "cities": [" 成都 ", "重庆", "成都"]})
        self.assertEqual(contract.sampling_progress(self.root)["required_destinations"], ["成都", "重庆"])

    def test_renderer_and_final_auditor_enforce_missing_city(self):
        self.finish_city("成都")
        scripts = Path(s.__file__).parent
        spec = importlib.util.spec_from_file_location("sampling_audit", scripts / "audit_itinerary.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = json.loads((scripts.parent / "assets/example-itinerary.json").read_text())
        case = self.data()
        data["workflow"] = case["workflow"]
        data["route_proposals"] = case["route_proposals"]
        data["planning"].update(case["planning"])
        with self.assertRaisesRegex(ValueError, "重庆"):
            module.render_itinerary.validate_data(data, self.root)
        self.assertTrue(any("重庆" in issue for issue in module.audit(data, self.root)["blocking"]))


if __name__ == "__main__":
    unittest.main()
