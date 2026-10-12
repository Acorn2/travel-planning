from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "travel-planning" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import source_adapters


class McpStdioPipeTest(unittest.TestCase):
    def start_server(self, script: str) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.addCleanup(source_adapters._stop_mcp_process, process)
        return process

    def test_notification_and_response_in_same_write(self) -> None:
        process = self.start_server(
            'import sys,time; sys.stdout.write(\'invalid JSON\\n[]\\n'
            '{"jsonrpc":"2.0","method":"notifications/message"}\\n'
            '{"jsonrpc":"2.0","id":99,"result":{}}\\n'
            '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\\n\'); '
            'sys.stdout.flush(); time.sleep(3)'
        )
        self.assertEqual(source_adapters._read_response(process, 1, 1)["result"], {"ok": True})

    def test_partial_line_obeys_timeout(self) -> None:
        process = self.start_server(
            'import sys,time; sys.stdout.write("{"); sys.stdout.flush(); time.sleep(2)'
        )
        started = time.monotonic()
        with self.assertRaises(source_adapters.AdapterError) as raised:
            source_adapters._read_response(process, 1, 0.4)
        self.assertEqual(raised.exception.failure_kind, "timeout")
        self.assertLess(time.monotonic() - started, 1.5)
        source_adapters._stop_mcp_process(process)
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout.closed)
        self.assertFalse(process._travel_mcp_reader.thread.is_alive())

    def test_queued_response_is_retained_for_next_request(self) -> None:
        process = self.start_server(
            'import sys,time; sys.stdout.write(\'{"id":1,"result":{}}\\n'
            '{"id":2,"result":{"next":true}}\\n\'); sys.stdout.flush(); time.sleep(3)'
        )
        self.assertEqual(source_adapters._read_response(process, 1, 1)["id"], 1)
        self.assertEqual(source_adapters._read_response(process, 2, 1)["result"], {"next": True})

    def test_fragmented_utf8_and_newline(self) -> None:
        process = self.start_server(
            'import sys,time; payload=\'{"id":1,"result":{"name":"宜昌"}}\\n\'.encode("utf-8"); '
            'split=payload.index("宜".encode("utf-8"))+1; '
            'sys.stdout.buffer.write(payload[:split]); sys.stdout.flush(); time.sleep(0.05); '
            'sys.stdout.buffer.write(payload[split:-1]); sys.stdout.flush(); time.sleep(0.05); '
            'sys.stdout.buffer.write(payload[-1:]); sys.stdout.flush(); time.sleep(3)'
        )
        self.assertEqual(source_adapters._read_response(process, 1, 1)["result"], {"name": "宜昌"})

    def test_notifications_do_not_extend_deadline(self) -> None:
        process = self.start_server(
            'import sys,time\nfor _ in range(100):\n'
            ' sys.stdout.write(\'{"method":"notifications/message"}\\n\'); '
            'sys.stdout.flush(); time.sleep(0.05)\n'
        )
        started = time.monotonic()
        with self.assertRaises(source_adapters.AdapterError) as raised:
            source_adapters._read_response(process, 1, 0.4)
        self.assertEqual(raised.exception.failure_kind, "timeout")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_response_at_eof_without_final_newline(self) -> None:
        process = self.start_server('print(\'{"id":1,"result":{}}\', end="", flush=True)')
        self.assertEqual(source_adapters._read_response(process, 1, 1)["id"], 1)

    def test_eof_without_response_returns_promptly(self) -> None:
        process = self.start_server('print(\'{"method":"notifications/message"}\', flush=True)')
        started = time.monotonic()
        with self.assertRaises(source_adapters.AdapterError) as raised:
            source_adapters._read_response(process, 1, 3)
        self.assertEqual(raised.exception.failure_kind, "timeout")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_probe_and_call_with_real_stdio_server(self) -> None:
        script = '''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if request["method"] == "initialize":
        result = {"protocolVersion": "2024-11-05"}
    elif request["method"] == "tools/list":
        result = {"tools": [{"name": "test_tool"}]}
    else:
        result = {"structuredContent": {"name": "宜昌"}}
    notification = {"method": "notifications/message"}
    response = {"id": request["id"], "result": result}
    sys.stdout.write(json.dumps(notification) + "\\n" + json.dumps(response, ensure_ascii=False) + "\\n")
    sys.stdout.flush()
'''
        command = [sys.executable, "-c", script]
        self.assertEqual(source_adapters.probe_mcp_stdio(command, dict(os.environ), 1, {"test_tool"})["status"], "ready")
        self.assertEqual(source_adapters.call_mcp_stdio(command, "test_tool", {}, dict(os.environ), 1), {"name": "宜昌"})


class SourceAdaptersTest(unittest.TestCase):
    def test_probe_mcp_stdio_initializes_and_lists_expected_tools(self) -> None:
        process = MagicMock()
        process.poll.return_value = 0
        initialized = {
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "test-mcp", "version": "1.0"},
            }
        }
        listed = {"result": {"tools": [{"name": "maps_weather"}]}}
        with patch.object(source_adapters.subprocess, "Popen", return_value=process):
            with patch.object(source_adapters, "_send_message") as send:
                with patch.object(
                    source_adapters,
                    "_read_response",
                    side_effect=[initialized, listed],
                ):
                    result = source_adapters.probe_mcp_stdio(
                        ["mcp"], {}, 5, {"maps_weather"}
                    )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tools"], ["maps_weather"])
        self.assertEqual(send.call_args_list[2].args[1]["method"], "tools/list")

    def test_probe_mcp_stdio_rejects_missing_contract_tool(self) -> None:
        process = MagicMock()
        process.poll.return_value = 0
        with patch.object(source_adapters.subprocess, "Popen", return_value=process):
            with patch.object(source_adapters, "_send_message"):
                with patch.object(
                    source_adapters,
                    "_read_response",
                    side_effect=[{"result": {}}, {"result": {"tools": []}}],
                ):
                    with self.assertRaises(source_adapters.AdapterError) as raised:
                        source_adapters.probe_mcp_stdio(
                            ["mcp"], {}, 5, {"required_tool"}
                        )
        self.assertEqual(raised.exception.failure_kind, "contract_mismatch")

    def test_probe_mcp_http_initializes_and_lists_expected_tools(self) -> None:
        initialized = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "xiaohongshu-mcp", "version": "2.0.0"},
            },
        }
        listed = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": "check_login_status"}, {"name": "search_feeds"}]},
        }
        with patch.object(
            source_adapters,
            "_mcp_http_exchange",
            side_effect=[(initialized, None), (None, None), (listed, None)],
        ) as exchange:
            result = source_adapters.probe_mcp_http(
                "http://127.0.0.1:18060/mcp",
                5,
                {"check_login_status", "search_feeds"},
            )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tools"], ["check_login_status", "search_feeds"])
        self.assertEqual(exchange.call_args_list[2].args[1]["method"], "tools/list")

    def test_parse_mcp_http_sse_response(self) -> None:
        payload = source_adapters._parse_mcp_http_body(
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
        )
        self.assertEqual(payload["result"], {"ok": True})

    def test_provider_commands_are_version_pinned(self) -> None:
        with patch.object(source_adapters.shutil, "which", return_value="/usr/local/bin/npx"):
            for spec in source_adapters.PROVIDERS.values():
                command = source_adapters.provider_command(spec)
                self.assertIn(f"{spec.package}@{spec.version}", command)
                self.assertIn("--registry=https://registry.npmjs.org", command)

    def test_flyai_flight_is_normalized_to_snapshot(self) -> None:
        raw = {
            "data": {
                "itemList": [
                    {
                        "id": "offer-1",
                        "ticketPrice": "688.00",
                        "jumpUrl": "https://example.test/book/1",
                        "journeys": [
                            {
                                "segments": [
                                    {
                                        "transportType": "flight",
                                        "marketingTransportNo": "MU5101",
                                        "marketingTransportName": "东方航空",
                                        "depCityName": "上海",
                                        "depStationName": "虹桥机场",
                                        "depDateTime": "2026-10-03 08:00",
                                        "arrCityName": "北京",
                                        "arrStationName": "首都机场",
                                        "arrDateTime": "2026-10-03 10:10",
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        }
        adapter = source_adapters.FlyAiAdapter(source_adapters.PROVIDERS["fliggy_flyai"])
        with patch.object(source_adapters, "provider_command", return_value=["flyai"]):
            with patch.object(source_adapters, "run_json_cli", return_value=raw):
                result = adapter.query(
                    "flight",
                    "search-flight",
                    {"origin": "上海", "destination": "北京", "dep_date": "2026-10-03"},
                    {"origin": "上海", "destination": "北京", "date": "2026-10-03"},
                    30,
                    5,
                )
        self.assertEqual(result["schema_version"], "travel-source-snapshot/v1")
        self.assertEqual(result["snapshot_kind"], "quote")
        self.assertEqual(result["status"], "platform_reported")
        self.assertEqual(result["provider"]["id"], "fliggy_flyai")
        self.assertEqual(result["items"][0]["name"], "MU5101")
        self.assertEqual(result["items"][0]["price"]["amount"], 688.0)
        self.assertEqual(result["items"][0]["price"]["currency"], "CNY")
        self.assertEqual(result["items"][0]["action_link"], "https://example.test/book/1")
        self.assertTrue(result["raw_response_hash"].startswith("sha256:"))
        self.assertNotIn("itemList", result)

    def test_flyai_hotel_rejects_non_https_action_link(self) -> None:
        item = {
            "shId": "hotel-1",
            "name": "示例酒店",
            "price": "¥599",
            "detailUrl": "http://unsafe.example.test/hotel/1",
            "address": "西湖区",
            "score": "4.8",
        }
        normalized = source_adapters.normalize_flyai_item("hotel", item)
        self.assertEqual(normalized["price"]["currency"], "CNY")
        self.assertIsNone(normalized["action_link"])

    def test_obfuscated_trial_price_is_not_parsed_as_exact_amount(self) -> None:
        normalized = source_adapters.normalize_flyai_item(
            "hotel", {"name": "示例酒店", "price": "¥3xx"}
        )
        self.assertIsNone(normalized["price"]["amount"])
        self.assertEqual(normalized["price"]["currency"], "CNY")
        self.assertEqual(normalized["price"]["display"], "¥3xx")

    def test_variflight_requires_credential_before_starting_mcp(self) -> None:
        adapter = source_adapters.McpStdioAdapter(
            source_adapters.PROVIDERS["variflight_aviation"]
        )
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.env"
            with patch.dict(
                os.environ,
                {"TRAVEL_SOURCES_CONFIG": str(missing)},
                clear=True,
            ):
                with self.assertRaises(source_adapters.AdapterError) as raised:
                    adapter.query(
                        "flight",
                        "searchFlightsByNumber",
                        {"fnum": "MU2157", "date": "2026-10-03"},
                        {"fnum": "MU2157", "date": "2026-10-03"},
                        30,
                        5,
                    )
        self.assertEqual(raised.exception.failure_kind, "credential_missing")

    def test_variflight_snapshot_sanitizes_secret_fields(self) -> None:
        raw = {
            "flights": [
                {
                    "fnum": "MU2157",
                    "price": "CNY 720",
                    "apiKey": "must-not-leak",
                    "bookingUrl": "https://example.test/flight/MU2157",
                }
            ]
        }
        adapter = source_adapters.McpStdioAdapter(
            source_adapters.PROVIDERS["variflight_aviation"]
        )
        with patch.dict(os.environ, {"VARIFLIGHT_API_KEY": "secret-value"}, clear=True):
            with patch.object(source_adapters, "provider_command", return_value=["mcp"]):
                with patch.object(source_adapters, "call_mcp_stdio", return_value=raw):
                    result = adapter.query(
                        "flight",
                        "getFlightPriceByCities",
                        {"dep_city": "BJS", "arr_city": "SHA", "dep_date": "2026-10-03"},
                        {"origin": "BJS", "destination": "SHA", "date": "2026-10-03"},
                        30,
                        5,
                    )
        self.assertEqual(result["items"][0]["name"], "MU2157")
        self.assertNotIn("must-not-leak", str(result))
        self.assertNotIn("secret-value", str(result))
        self.assertNotIn("provider_fields", result["items"][0])

    def test_variflight_live_flight_shape_is_normalized(self) -> None:
        normalized = source_adapters.normalize_variflight_item(
            "flight",
            {
                "FlightNo": "MU5138",
                "FlightCompany": "中国东方航空股份有限公司",
                "FlightDep": "北京",
                "FlightDepAirport": "北京大兴",
                "FlightDepcode": "PKX",
                "FlightHTerminal": "",
                "FlightDeptimePlanDate": "2026-10-01 07:00:00",
                "FlightArr": "上海",
                "FlightArrAirport": "上海虹桥",
                "FlightArrcode": "SHA",
                "FlightTerminal": "T2",
                "FlightArrtimePlanDate": "2026-10-01 09:10:00",
                "FlightState": "计划",
                "OntimeRate": "100.00%",
                "ftype": "325",
                "FlightDuration": "130",
            },
        )
        self.assertEqual(normalized["name"], "MU5138")
        self.assertEqual(normalized["departure"]["station_code"], "PKX")
        self.assertEqual(normalized["arrival"]["station_code"], "SHA")
        self.assertEqual(normalized["operational"]["status"], "计划")
        self.assertEqual(normalized["total_duration"], "130")

    def test_variflight_live_train_shape_is_normalized(self) -> None:
        normalized = source_adapters.normalize_variflight_item(
            "train",
            {
                "trainNumber": "G565",
                "fromStation": "北京南",
                "fromTccode": "VNP",
                "fromTime": "07:07",
                "toStation": "上海虹桥",
                "toTccode": "AOH",
                "toTime": "13:12",
                "useTime": 365,
                "seatLists": [
                    {"seatName": "二等座", "seatPrice": 662, "ticketLeft": 10},
                    {"seatName": "一等座", "seatPrice": 1058, "ticketLeft": 3},
                ],
            },
        )
        self.assertEqual(normalized["name"], "G565")
        self.assertEqual(normalized["price"]["amount"], 662.0)
        self.assertEqual(normalized["availability"]["remaining"], 13)
        self.assertEqual(normalized["operational"]["seat_class"], "二等座")
        self.assertEqual(len(normalized["seat_options"]), 2)

    def test_variflight_station_lookup_shape_is_normalized(self) -> None:
        normalized = source_adapters.normalize_variflight_item(
            "train_station",
            {"station_name": "北京南", "station_code": "VNP", "city_name": "北京"},
        )
        self.assertEqual(normalized["offer_id"], "VNP")
        self.assertEqual(normalized["name"], "北京南")
        self.assertEqual(normalized["location"]["city"], "北京")

    def test_snapshot_has_nonempty_offer_id_and_expiry(self) -> None:
        normalized = source_adapters.normalize_flyai_item("hotel", {"price": "¥599"})
        self.assertTrue(normalized["offer_id"].startswith("provider-"))
        result = source_adapters.make_snapshot(
            source_adapters.PROVIDERS["fliggy_flyai"],
            "hotel",
            {"destination": "杭州"},
            {"data": {"itemList": []}},
            [],
            "search-hotel",
        )
        self.assertIsNotNone(result["freshness"]["expires_at"])
        self.assertGreater(
            source_adapters.datetime.fromisoformat(result["freshness"]["expires_at"]),
            source_adapters.datetime.fromisoformat(result["freshness"]["checked_at"]),
        )

    def test_unauthorized_is_distinct_from_missing_credential(self) -> None:
        self.assertEqual(
            source_adapters._classify_failure("HTTP 403 unauthorized"),
            "authorization_failed",
        )

    def test_mcp_metadata_only_response_is_no_results(self) -> None:
        adapter = source_adapters.McpStdioAdapter(
            source_adapters.PROVIDERS["variflight_tripmatch"]
        )
        with patch.dict(os.environ, {"VARIFLIGHT_API_KEY": "secret"}, clear=True):
            with patch.object(source_adapters, "provider_command", return_value=["mcp"]):
                with patch.object(source_adapters, "call_mcp_stdio", return_value={"code": 0, "message": "ok"}):
                    result = adapter.query(
                        "train", "searchTrainTickets", {"from": "北京", "to": "上海", "date": "2026-10-03"},
                        {"origin": "北京", "destination": "上海", "date": "2026-10-03"}, 30, 5,
                    )
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["items"], [])

    def test_generated_snapshot_covers_machine_schema_required_fields(self) -> None:
        schema_path = SCRIPTS_DIR.parent / "schemas" / "travel-source-snapshot.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        result = source_adapters.make_snapshot(
            source_adapters.PROVIDERS["fliggy_flyai"],
            "hotel",
            {"destination": "杭州"},
            {"data": {"itemList": []}},
            [],
            "search-hotel",
        )
        self.assertTrue(set(schema["required"]).issubset(result))
        self.assertEqual(result["snapshot_kind"], "quote")
        self.assertEqual(result["status"], "no_results")


if __name__ == "__main__":
    unittest.main()
