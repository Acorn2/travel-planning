from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_research_sources import research_sources as s

contract = s.xhs_search_contract


class XhsSearchDesignTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        s.execution_log.write_json(self.root / "manifest.json", {"workspace_version": 1})
        s.execution_log.write_json(self.root / "brief.json", {"destination": "宜昌", "date_range": "2026-10-15 至 2026-10-19"})

    def args(self, keyword="宜昌美食推荐", *extra):
        return s.build_parser().parse_args(["xhs-search", "--keyword", keyword, "--workspace", str(self.root), *extra])

    def response(self):
        return {"feeds": [{"id": f"note{i}", "xsecToken": "never-save-this", "modelType": "note",
            "noteCard": {"displayTitle": f"测试笔记{i}", "user": {}, "interactInfo": {}}} for i in range(5)]}

    def query(self, args=None):
        args = args or self.args()
        with patch.object(s, "require_xhs_login"), patch.object(s, "cache_xhs_mcp_tokens"), \
             patch.object(s, "xhs_mcp_call", return_value=self.response()) as call:
            payload = s.run_query(args, "xhs-search", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
            return payload, call

    def test_discover_cli_generates_open_keywords_without_duration(self):
        for topic, keyword in (("eat", "宜昌美食推荐"), ("play", "宜昌旅游推荐")):
            argv = ["research_sources.py", "xhs-discover", "--destination", "宜昌", "--topic", topic, "--workspace", str(self.root)]
            with patch.object(sys, "argv", argv), patch.object(s, "require_xhs_login"), \
                 patch.object(s, "cache_xhs_mcp_tokens"), patch.object(s, "xhs_mcp_call", return_value=self.response()) as call, \
                 patch.object(s, "output") as out:
                self.assertEqual(s.main(), 0)
                self.assertEqual(call.call_args.args[1], {"keyword": keyword})
                self.assertEqual(out.call_args.args[0]["search_design"]["stage"], "discovery")

    def test_wrong_discovery_and_duration_are_rejected_before_platform_call(self):
        for word in ("宜昌本地美食 亲历体验", "武汉 宜昌 自驾 4天 吃住自然慢游",
                     "三峡大坝 旅游区 坛子岭 自驾 游览", "宜昌五日游攻略", "宜昌4天3夜", "宜昌 5 days"):
            with self.subTest(word=word), patch.object(s, "xhs_mcp_call") as call:
                with self.assertRaises(s.SourceError):
                    s.run_query(self.args(word, "--topic", "eat"), "xhs-search", {}, lambda: s.xhs_search(self.args(word)), "xiaohongshu")
                call.assert_not_called()

    def test_spaced_open_queries_are_allowed(self):
        meta = contract.validate_search(self.args("宜昌 美食 推荐"))
        self.assertEqual(meta["scope"], "eat")

    def test_targeted_query_needs_real_discovery_and_registered_source(self):
        args = self.args("鱼豆吉口味评价", "--topic", "eat", "--search-stage", "detail", "--target-source-id", "food-origin")
        with self.assertRaisesRegex(ValueError, "未登记"):
            contract.validate_search(args)
        path = self.root / "sources/main.jsonl"
        path.parent.mkdir()
        path.write_text(json.dumps({"id": "food-origin", "summary": "已读发现资料提到鱼豆吉"}) + '\n')
        with self.assertRaisesRegex(ValueError, "缺少.*开放发现"):
            contract.validate_search(args)
        self.query()
        self.assertEqual(contract.validate_search(args)["stage"], "detail")

    def test_user_explicit_target_exception_records_request(self):
        args = self.args("宜昌五日游攻略", "--topic", "play", "--search-stage", "detail", "--user-request", "用户明确要比较五日游攻略")
        payload, _ = self.query(args)
        self.assertEqual(payload["search_design"]["user_request"], "用户明确要比较五日游攻略")
        self.assertEqual(contract.records(self.root)[0]["stage"], "detail")

    def test_records_actual_keyword_all_candidate_ids_and_selected_count(self):
        payload, _ = self.query(self.args("宜昌美食推荐", "--limit", "2"))
        self.assertEqual(payload["returned_count"], 5)
        self.assertEqual(payload["count"], 2)
        rec = contract.records(self.root)[0]
        self.assertEqual(rec["keyword"], "宜昌美食推荐")
        self.assertEqual(rec["returned_count"], 5)
        self.assertEqual(rec["selected_count"], 2)
        self.assertEqual(len(rec["candidate_note_ids"]), 5)
        self.assertNotIn("never-save-this", json.dumps(rec))

    def test_reuse_does_not_duplicate_query_history(self):
        args = self.args()
        with patch.object(s, "require_xhs_login"), patch.object(s, "cache_xhs_mcp_tokens"), patch.object(s, "xhs_mcp_call", return_value=self.response()) as call:
            first = s.run_query(args, "xhs-search", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
            second = s.run_query(args, "xhs-search", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(first["checked_at"], second["checked_at"])
        self.assertEqual(len(contract.records(self.root)), 1)

    def test_failure_records_keyword_without_fake_zero_or_raw_error(self):
        args = self.args()
        with patch.object(s, "require_xhs_login"), patch.object(s, "xhs_mcp_call", side_effect=s.SourceError("private error")):
            with self.assertRaises(s.SourceError):
                s.run_query(args, "xhs-search", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
        rec = contract.records(self.root)[0]
        self.assertEqual(rec["status"], "failed")
        self.assertIsNone(rec["returned_count"])
        self.assertEqual(rec["keyword"], "宜昌美食推荐")
        self.assertNotIn("private error", json.dumps(rec))

    def test_empty_search_is_distinct_from_missing_response_list(self):
        args = self.args()
        with patch.object(s, "require_xhs_login"), patch.object(s, "cache_xhs_mcp_tokens"), patch.object(s, "xhs_mcp_call", return_value={"feeds": []}):
            payload = s.run_query(args, "xhs-search", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
        self.assertEqual(payload["search_status"], "no_results")
        self.assertEqual(contract.records(self.root)[0]["returned_count"], 0)
        args.refresh = True
        with patch.object(s, "require_xhs_login"), patch.object(s, "xhs_mcp_call", return_value={}):
            with self.assertRaises(s.SourceError):
                s.run_query(args, "xhs-search", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
        self.assertEqual(contract.records(self.root)[-1]["status"], "failed")
        self.assertIsNone(contract.records(self.root)[-1]["returned_count"])

    def test_final_gate_blocks_bypassed_query_and_missing_logs(self):
        data = {"workflow": {"phase": "final"}, "planning": {"xhs_search_design_version": 1,
            "research_contract": {"queries": [{"provider": "xiaohongshu", "tool": "search_feeds", "parameters": {"keyword": "宜昌美食推荐"}}]}}, "sources": []}
        self.assertTrue(contract.audit_searches(data)[0])
        self.query()
        data["planning"]["xhs_search_records"] = contract.records(self.root)
        self.assertEqual(contract.audit_searches(data)[0], [])
        data["planning"]["xhs_search_records"][0]["keyword"] = "宜昌五日游攻略"
        self.assertTrue(contract.audit_searches(data)[0])
        data["planning"].pop("xhs_search_design_version")
        self.assertEqual(contract.audit_searches(data)[0], [])

    def test_failed_discovery_does_not_authorize_targeted_query(self):
        self.query()
        data = {"workflow": {"phase": "final"}, "sources": [{"id": "s1"}], "planning": {
            "xhs_search_design_version": 1, "xhs_search_records": contract.records(self.root)}}
        first = data["planning"]["xhs_search_records"][0]
        targeted = deepcopy(first)
        targeted.update(stage="detail", keyword="具体门店评价", target_source_id="s1")
        first["status"] = "failed"
        data["planning"]["xhs_search_records"].append(targeted)
        self.assertTrue(any("前置开放发现" in b for b in contract.audit_searches(data)[0]))

    def observation(self, status, captured, *, scope="eat", destination="宜昌", task="main", failure=None):
        payload = {"search_design": {"destination": destination, "scope": scope, "stage": "discovery"},
                   "query": {"keyword": destination + contract.SUFFIXES[scope][0]},
                   "checked_at": captured.isoformat(), "search_status": status,
                   "returned_count": 1 if status == "success" else None,
                   "count": 1 if status == "success" else None,
                   "candidate_note_ids": ["note0"] if status == "success" else [],
                   "results": [{"note_id": "note0"}] if status == "success" else []}
        if failure:
            payload["failure_kind"] = failure
        contract.store_search(self.root, payload, task)

    def test_state_missing_observations_does_not_probe_or_claim_failure(self):
        state = contract.observation_state(self.root)
        self.assertFalse(state["live_service_checked"])
        self.assertEqual([v["next_action"] for v in state["topics"]], ["run_discovery"] * 2)
        self.assertTrue(all(v["latest_search"] is None for v in state["topics"]))

    def test_state_historical_failure_requires_new_discovery(self):
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        self.observation("failed", now - timedelta(hours=3), failure="timeout")
        eat = contract.observation_state(self.root, now=now)["topics"][0]
        self.assertEqual(eat["next_action"], "refresh_discovery")
        self.assertFalse(eat["can_reuse_search_result"])
        self.assertEqual(eat["latest_search"]["age_seconds"], 10800)

    def test_state_success_in_another_task_supersedes_old_failure(self):
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        self.observation("failed", now - timedelta(hours=3), task="old")
        self.observation("success", now - timedelta(seconds=30), task="recovery")
        eat = contract.observation_state(self.root, now=now)["topics"][0]
        self.assertEqual(eat["latest_search"]["status"], "success")
        self.assertTrue(eat["can_reuse_search_result"])
        self.assertEqual(eat["next_action"], "read_details_and_comments")
        self.assertEqual(len(contract.records(self.root)), 2)

    def test_state_versioned_workspace_requires_sampling_with_partial_details(self):
        now = datetime.now(timezone.utc)
        s.execution_log.write_json(self.root / "manifest.json", {
            "workspace_version": 1, "xhs_detail_sampling_version": 1})
        self.observation("success", now - timedelta(seconds=10))
        s.execution_log.write_json(self.root / "evidence/main/xhs-comments/note.json", {
            "schema_version": "travel-xhs-comment-review/v1", "note_id": "note0",
            "source_url": "https://www.xiaohongshu.com/explore/note0",
            "checked_at": now.isoformat(), "review_status": "pending",
            "collection": {"mode": "expanded"},
            "analysis": {"input_count": 0, "top_level_count": 0, "reply_count": 0,
                         "eligible_count": 0, "filtered_count": 0, "group_count": 0}})

        state = contract.observation_state(self.root, now=now)

        self.assertEqual(state["detail_sampling"]["sampled_total"], 1)
        self.assertFalse(state["detail_sampling"]["complete"])
        self.assertEqual(state["topics"][0]["next_action"], "run_xhs_sample")
        self.assertEqual(len(state["topics"][0]["detail_observations"]), 1)

    def test_state_recent_failure_does_not_create_a_retry_loop(self):
        now = datetime.now(timezone.utc)
        self.observation("failed", now - timedelta(seconds=10), failure="timeout")
        eat = contract.observation_state(self.root, now=now)["topics"][0]
        self.assertEqual(eat["next_action"], "inspect_current_failure")
        self.assertFalse(eat["can_reuse_search_result"])

    def test_state_empty_search_is_not_a_service_failure(self):
        now = datetime.now(timezone.utc)
        self.observation("no_results", now - timedelta(seconds=10))
        eat = contract.observation_state(self.root, now=now)["topics"][0]
        self.assertEqual(eat["next_action"], "review_empty_result")
        self.assertFalse(eat["can_reuse_search_result"])

    def test_state_restrictions_require_inspection_even_when_old(self):
        now = datetime.now(timezone.utc)
        for failure in ("verification_required", "login_required", "quota_exceeded"):
            with self.subTest(failure=failure):
                self.observation("failed", now - timedelta(hours=2), task=failure, failure=failure)
                eat = contract.observation_state(self.root, now=now)["topics"][0]
                self.assertEqual(eat["next_action"], "check_login_or_platform_restriction")

    def test_state_comments_use_actual_counts_not_reported_total(self):
        now = datetime.now(timezone.utc)
        self.observation("success", now - timedelta(seconds=10))
        path = self.root / "evidence/recovery/xhs-comments/note.json"
        s.execution_log.write_json(path, {"schema_version": "travel-xhs-comment-review/v1",
            "note_id": "note0", "checked_at": now.isoformat(),
            "analysis": {"top_level_count": 20, "reply_count": 37, "input_count": 57},
            "collection": {"reported_total": "113", "has_more": True}})
        state = contract.observation_state(self.root, now=now)
        eat = state["topics"][0]
        self.assertIsNone(state["detail_sampling"])
        self.assertEqual(eat["next_action"], "review_detail_evidence")
        self.assertEqual(eat["detail_observations"][0]["analysis"]["input_count"], 57)

    def test_state_does_not_reuse_expired_or_future_success(self):
        now = datetime.now(timezone.utc)
        for delta in (timedelta(minutes=-11), timedelta(minutes=1)):
            with self.subTest(delta=delta):
                root = self.root / str(int(delta.total_seconds()))
                s.execution_log.write_json(root / "brief.json", {"destination": "宜昌"})
                original = self.root
                self.root = root
                try:
                    self.observation("success", now + delta)
                    eat = contract.observation_state(root, now=now)["topics"][0]
                    self.assertFalse(eat["can_reuse_search_result"])
                finally:
                    self.root = original

    def test_state_other_city_does_not_override_this_trip(self):
        now = datetime.now(timezone.utc)
        self.observation("failed", now - timedelta(hours=3))
        self.observation("success", now, destination="杭州")
        eat = contract.observation_state(self.root, now=now)["topics"][0]
        self.assertEqual(eat["latest_search"]["status"], "failed")

    def test_state_cli_never_calls_upstream(self):
        with (patch.object(sys, "argv", ["research_sources.py", "xhs-state", "--workspace", str(self.root)]),
              patch.object(s, "xhs_mcp_call") as call, patch.object(s, "output") as output):
            self.assertEqual(s.main(), 0)
        call.assert_not_called()
        self.assertFalse(output.call_args.args[0]["live_service_checked"])

    def test_rendering_cannot_bypass_search_gate(self):
        scripts = Path(s.__file__).parent
        spec = importlib.util.spec_from_file_location("render_test", scripts / "render_itinerary.py")
        render = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(render)
        data = json.loads((scripts.parent / "assets/example-itinerary.json").read_text())
        data["workflow"]["phase"] = "final"
        data["planning"]["xhs_search_design_version"] = 1
        data["planning"]["research_contract"] = {"version": 1, "capabilities": {}, "queries": [
            {"provider": "xiaohongshu", "tool": "search_feeds", "parameters": {"keyword": "宜昌五日游攻略"}}], "decisions": []}
        with self.assertRaisesRegex(ValueError, "缺少自动保存的查询记录"):
            render.validate_data(data)


if __name__ == "__main__":
    unittest.main()
