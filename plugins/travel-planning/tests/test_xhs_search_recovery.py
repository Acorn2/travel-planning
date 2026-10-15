from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_research_sources import research_sources as s
import source_adapters


class XhsSearchRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.args = s.build_parser().parse_args(["xhs-search", "--keyword", "杭州美食推荐"])
        mocks = []
        for target, name in ((s, "require_xhs_login"), (s, "cache_xhs_mcp_tokens"), (s.time, "sleep")):
            patcher = patch.object(target, name)
            mocks.append(patcher.start())
            self.addCleanup(patcher.stop)
        self.login, self.cache, self.sleep = mocks
        self.response = {"feeds": [{"id": "note-1", "modelType": "note",
            "xsecToken": "private-access-value", "noteCard": {"displayTitle": "当地吃法"}}]}

    def test_server_deadline_is_classified_as_timeout(self):
        with self.assertRaises(s.AdapterError) as error:
            source_adapters._mcp_result_payload({"isError": True, "content": [{
                "type": "text", "text": "工具 search_feeds 执行时发生内部错误: context deadline exceeded"}]})
        self.assertEqual(error.exception.failure_kind, "timeout")

    def test_timeout_rechecks_live_login_and_retries_same_query_once(self):
        with patch.object(s, "xhs_mcp_call", side_effect=[
            s.AdapterError("timeout", "context deadline exceeded"), self.response,
        ]) as call:
            result = s.xhs_search(self.args)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0], call.call_args_list[1])
        self.login.assert_called_with(query_args=self.args, refresh=True)
        self.sleep.assert_called_once_with(2)
        self.assertEqual(result["count"], 1)
        self.assertEqual([a["status"] for a in result["search_attempts"]], ["failed", "success"])
        self.assertNotIn("private-access-value", json.dumps(result))

    def test_repeated_timeout_stops_after_two_attempts(self):
        with patch.object(s, "xhs_mcp_call", side_effect=s.AdapterError("timeout", "context deadline exceeded")) as call:
            with self.assertRaises(s.AdapterError) as error:
                s.xhs_search(self.args)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(error.exception.failure_kind, "timeout")
        self.assertEqual(len(error.exception.search_attempts), 2)
        self.cache.assert_not_called()

    def test_filter_control_failure_falls_back_without_claiming_filters_applied(self):
        self.args.publish_time = "half_year"
        with patch.object(s, "xhs_mcp_call", side_effect=[
            s.AdapterError("provider_error", "筛选面板里没有「发布时间」这一组"), self.response,
        ]) as call:
            result = s.xhs_search(self.args)
        self.assertEqual(call.call_args_list[0].args[1]["filters"], {"publish_time": "半年内"})
        self.assertEqual(call.call_args_list[1].args[1], {"keyword": "杭州美食推荐"})
        self.assertEqual(result["query"]["filters"]["publish_time"], "all")
        self.assertEqual(result["query"]["requested_filters"]["publish_time"], "half_year")
        self.assertTrue(result["warnings"])
        self.assertEqual(result["search_attempts"][0]["failure_kind"], "filter_unavailable")

    def test_filter_fallback_does_not_start_another_timeout_retry(self):
        self.args.sort_by = "latest"
        with patch.object(s, "xhs_mcp_call", side_effect=[
            s.AdapterError("provider_error", "筛选面板里没有「排序依据」这一组"),
            s.AdapterError("timeout", "context deadline exceeded"),
        ]) as call:
            with self.assertRaises(s.AdapterError):
                s.xhs_search(self.args)
        self.assertEqual(call.call_count, 2)

    def test_expired_login_verification_and_limits_are_never_retried(self):
        for message, expected in [
            ("登录已过期", "login_required"),
            ("not logged in", "login_required"),
            ("context deadline exceeded; 请完成验证码", "verification_required"),
            ("设备验证", "verification_required"),
            ("触发风控", "verification_required"),
            ("操作频繁", "verification_required"),
            ("429 rate limit", "quota_exceeded"),
        ]:
            with self.subTest(message=message), patch.object(s, "xhs_mcp_call",
                side_effect=s.AdapterError("provider_error", message)) as call:
                with self.assertRaises(s.AdapterError) as error:
                    s.xhs_search(self.args)
                self.assertEqual(error.exception.failure_kind, expected)
                call.assert_called_once()
        self.sleep.assert_not_called()

    def test_transport_failure_is_not_blindly_retried(self):
        with patch.object(s, "xhs_mcp_call", side_effect=s.AdapterError("runtime_unavailable", "connection refused")) as call:
            with self.assertRaises(s.AdapterError):
                s.xhs_search(self.args)
        call.assert_called_once()

    def test_login_expiry_during_recovery_stops_before_second_search(self):
        self.login.side_effect = [None, s.AdapterError("login_required", "请先登录")]
        with patch.object(s, "xhs_mcp_call", side_effect=s.AdapterError("timeout", "context deadline exceeded")) as call:
            with self.assertRaises(s.AdapterError) as error:
                s.xhs_search(self.args)
        self.assertEqual(error.exception.failure_kind, "login_required")
        self.assertEqual(len(error.exception.search_attempts), 1)
        call.assert_called_once()
        self.sleep.assert_not_called()

    def test_empty_results_are_not_retried(self):
        with patch.object(s, "xhs_mcp_call", return_value={"feeds": []}) as call:
            result = s.xhs_search(self.args)
        call.assert_called_once()
        self.assertEqual(result["search_status"], "no_results")
        self.assertEqual(result["search_attempts"][0]["status"], "no_results")

    def test_invalid_response_is_not_reported_as_empty_or_retried(self):
        with patch.object(s, "xhs_mcp_call", return_value={}) as call:
            with self.assertRaises(s.SourceError) as error:
                s.xhs_search(self.args)
        self.assertEqual(error.exception.search_attempts[0]["failure_kind"], "invalid_response")
        call.assert_called_once()

    def test_malformed_note_cards_are_not_reported_as_zero_results(self):
        for feeds in ([None], [{}], [{"id": "note", "noteCard": []}]):
            with self.subTest(feeds=feeds), patch.object(s, "xhs_mcp_call", return_value={"feeds": feeds}) as call:
                with self.assertRaises(s.SourceError):
                    s.xhs_search(self.args)
                call.assert_called_once()

    def test_recovered_and_failed_attempts_are_preserved_in_workspace(self):
        for final in (self.response, s.AdapterError("timeout", "private raw failure")):
            with self.subTest(recovered=isinstance(final, dict)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                s.execution_log.write_json(root / "manifest.json", {"workspace_version": 1})
                s.execution_log.write_json(root / "brief.json", {"destination": "杭州"})
                args = s.build_parser().parse_args(["xhs-discover", "--destination", "杭州", "--topic", "eat", "--workspace", directory])
                args.keyword = "杭州美食推荐"
                with patch.object(s, "xhs_mcp_call", side_effect=[s.AdapterError("timeout", "private raw failure"), final]):
                    if isinstance(final, dict):
                        s.run_query(args, "xhs-discover", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
                    else:
                        with self.assertRaises(s.AdapterError):
                            s.run_query(args, "xhs-discover", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
                record = s.xhs_search_contract.records(root)[0]
                self.assertEqual(len(record["search_attempts"]), 2)
                self.assertNotIn("private raw failure", json.dumps(record))
                self.assertNotIn("private-access-value", json.dumps(record))
                if not isinstance(final, dict):
                    self.assertIsNone(record["returned_count"])

    def test_unfiltered_fallback_is_not_cached_as_filtered_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            s.execution_log.write_json(root / "manifest.json", {"workspace_version": 1})
            s.execution_log.write_json(root / "brief.json", {"destination": "杭州"})
            args = s.build_parser().parse_args(["xhs-discover", "--destination", "杭州", "--topic", "eat",
                "--publish-time", "half_year", "--workspace", directory])
            args.keyword = "杭州美食推荐"
            with patch.object(s, "xhs_mcp_call", side_effect=[
                s.AdapterError("provider_error", "筛选面板里没有「发布时间」这一组"), self.response, self.response,
            ]) as call:
                first = s.run_query(args, "xhs-discover", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
                second = s.run_query(args, "xhs-discover", s.command_parameters(args), lambda: s.xhs_search(args), "xiaohongshu")
            self.assertIn("requested_filters", first["query"])
            self.assertNotIn("requested_filters", second["query"])
            self.assertEqual(call.call_count, 3)

    def test_cli_failure_reports_sanitized_attempts(self):
        argv = ["research_sources.py", "xhs-search", "--keyword", "杭州美食推荐"]
        with patch.object(s.sys, "argv", argv), patch.object(s, "output") as output, \
             patch.object(s, "xhs_mcp_call", side_effect=s.AdapterError("timeout", "private raw failure")):
            self.assertEqual(s.main(), 2)
        payload = output.call_args.args[0]
        self.assertEqual(payload["failure_kind"], "timeout")
        self.assertEqual(len(payload["search_attempts"]), 2)
        self.assertNotIn("private raw failure", json.dumps(payload))


class XhsLiveLoginCheckTest(unittest.TestCase):
    def test_real_http_mcp_error_rechecks_login_then_recovers(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if request["method"] == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if request["method"] == "initialize":
                    result = {"protocolVersion": "2025-03-26", "capabilities": {}}
                else:
                    name = request["params"]["name"]
                    calls.append(name)
                    if name == "check_login_status":
                        result = {"content": [{"type": "text", "text": "已登录"}]}
                    elif calls.count("search_feeds") == 1:
                        result = {"isError": True, "content": [{"type": "text", "text": "context deadline exceeded"}]}
                    else:
                        result = {"structuredContent": {"feeds": [{"id": "note-1", "noteCard": {"displayTitle": "本地夹具"}}]}}
                body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {"XHS_MCP_URL": f"http://127.0.0.1:{server.server_port}/mcp"}), \
                 patch.object(s, "cache_xhs_mcp_tokens"), patch.object(s.time, "sleep"):
                args = s.build_parser().parse_args(["xhs-search", "--keyword", "杭州美食推荐"])
                result = s.xhs_search(args)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(calls, ["check_login_status", "search_feeds", "check_login_status", "search_feeds"])
        self.assertEqual(result["search_status"], "success")
        self.assertEqual(result["search_attempts"][0]["failure_kind"], "timeout")
        self.assertEqual(len(result["search_attempts"]), 2)

    def test_recovery_refresh_bypasses_cached_health(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            s.execution_log.write_json(root / "manifest.json", {"workspace_version": 1})
            args = s.build_parser().parse_args(["xhs-search", "--keyword", "杭州美食推荐", "--workspace", directory])
            s.execution_log.store_result(root, s.health_key("xiaohongshu"), {"status": "ready", "checked_at": s.checked_at()})
            with patch.object(s, "xhs_login_status", return_value={"status": "login_required"}) as check:
                with self.assertRaises(s.AdapterError) as error:
                    s.require_xhs_login(query_args=args, refresh=True)
            check.assert_called_once()
            self.assertEqual(error.exception.failure_kind, "login_required")

    def test_english_not_logged_in_is_not_a_positive_login(self):
        with patch.object(s, "xhs_mcp_call", return_value={"text": "not logged in"}):
            self.assertEqual(s.xhs_login_status(s.argparse.Namespace())["status"], "login_required")

    def test_verification_during_login_is_not_cookie_expiry(self):
        with patch.object(s, "xhs_mcp_call", return_value={"text": "请完成设备验证"}):
            with self.assertRaises(s.AdapterError) as error:
                s.xhs_login_status(s.argparse.Namespace())
        self.assertEqual(error.exception.failure_kind, "verification_required")

    def test_expired_login_as_tool_error_is_recognized(self):
        with patch.object(s, "xhs_mcp_call", side_effect=s.AdapterError("provider_error", "登录已失效")):
            self.assertEqual(s.xhs_login_status(s.argparse.Namespace())["status"], "login_required")

    def test_unrecognized_login_response_is_not_claimed_as_cookie_expiry(self):
        with patch.object(s, "xhs_mcp_call", return_value={"text": "页面未加载"}):
            with self.assertRaises(s.AdapterError) as error:
                s.xhs_login_status(s.argparse.Namespace())
        self.assertEqual(error.exception.failure_kind, "invalid_response")


if __name__ == "__main__":
    unittest.main()
