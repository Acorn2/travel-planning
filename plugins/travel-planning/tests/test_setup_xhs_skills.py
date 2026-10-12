from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "skills" / "xiaohongshu" / "scripts" / "setup.py"
SPEC = importlib.util.spec_from_file_location("setup_xhs_mcp", MODULE_PATH)
assert SPEC and SPEC.loader
setup_xhs_mcp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_xhs_mcp)


class SetupXhsMcpTest(unittest.TestCase):
    def test_invalid_pids_never_address_process_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "service.pid"
            for value in ("-1", "0", "1", "not-a-pid"):
                pid_file.write_text(value, encoding="utf-8")
                with self.subTest(value=value), patch.object(setup_xhs_mcp, "PID_FILE", pid_file), patch.object(setup_xhs_mcp.os, "kill") as kill:
                    self.assertIsNone(setup_xhs_mcp._read_pid())
                    self.assertFalse(setup_xhs_mcp._process_alive(-1))
                    kill.assert_not_called()

    def test_stop_refuses_live_pid_without_matching_identity(self) -> None:
        identity = {"pid": 12345, "started_at": "Mon Oct 12 10:00:00 2026", "command": "/test/server -headless=true -port=127.0.0.1:18060"}
        for record in (None, {**identity, "started_at": "Mon Oct 12 09:00:00 2026"}, {**identity, "command": "/test/other"}):
            with tempfile.TemporaryDirectory() as directory:
                pid_file = Path(directory) / "service.pid"
                pid_file.write_text("12345", encoding="utf-8")
                if record is not None:
                    pid_file.with_suffix(".json").write_text(json.dumps(record), encoding="utf-8")
                with (
                    self.subTest(record=record),
                    patch.object(setup_xhs_mcp, "PID_FILE", pid_file),
                    patch.object(setup_xhs_mcp, "_binary", return_value=Path("/test/server")),
                    patch.object(setup_xhs_mcp, "_process_alive", return_value=True),
                    patch.object(setup_xhs_mcp.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=identity["started_at"] + " " + identity["command"])),
                    patch.object(setup_xhs_mcp.os, "kill") as kill,
                ):
                    with self.assertRaises(setup_xhs_mcp.SetupError):
                        setup_xhs_mcp.stop(Namespace())
                    kill.assert_not_called()
                    self.assertTrue(pid_file.exists())

    def test_stop_signals_only_matching_process_and_clears_records(self) -> None:
        identity = {"pid": 12345, "started_at": "Mon Oct 12 10:00:00 2026", "command": "/test/server -headless=true -port=127.0.0.1:18060"}
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "service.pid"
            pid_file.write_text("12345", encoding="utf-8")
            record_file = pid_file.with_suffix(".json")
            record_file.write_text(json.dumps(identity), encoding="utf-8")
            with (
                patch.object(setup_xhs_mcp, "PID_FILE", pid_file),
                patch.object(setup_xhs_mcp, "_binary", return_value=Path("/test/server")),
                patch.object(setup_xhs_mcp, "_process_alive", side_effect=[True, False]),
                patch.object(setup_xhs_mcp, "_process_identity", side_effect=[identity, identity, None]),
                patch.object(setup_xhs_mcp.os, "kill") as kill,
            ):
                self.assertEqual(setup_xhs_mcp.stop(Namespace())["status"], "stopped")
                kill.assert_called_once_with(12345, signal.SIGTERM)
            self.assertFalse(pid_file.exists())
            self.assertFalse(record_file.exists())

    def test_identity_change_before_signal_is_not_stopped(self) -> None:
        identity = {"pid": 12345, "started_at": "Mon Oct 12 10:00:00 2026", "command": "/test/server -headless=true -port=127.0.0.1:18060"}
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "service.pid"
            pid_file.write_text("12345", encoding="utf-8")
            pid_file.with_suffix(".json").write_text(json.dumps(identity), encoding="utf-8")
            with (
                patch.object(setup_xhs_mcp, "PID_FILE", pid_file),
                patch.object(setup_xhs_mcp, "_binary", return_value=Path("/test/server")),
                patch.object(setup_xhs_mcp, "_process_alive", return_value=True),
                patch.object(setup_xhs_mcp, "_process_identity", side_effect=[identity, {**identity, "started_at": "Mon Oct 12 11:00:00 2026"}]),
                patch.object(setup_xhs_mcp.os, "kill") as kill,
            ):
                with self.assertRaises(setup_xhs_mcp.SetupError):
                    setup_xhs_mcp.stop(Namespace())
                kill.assert_not_called()

    def test_start_records_identity_with_private_permissions(self) -> None:
        identity = {"pid": 12345, "started_at": "Mon Oct 12 10:00:00 2026", "command": "/test/server -headless=true -port=127.0.0.1:18060"}
        process = MagicMock(pid=12345)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(setup_xhs_mcp, "PID_FILE", root / "service.pid"),
                patch.object(setup_xhs_mcp, "STATE_DIR", root),
                patch.object(setup_xhs_mcp, "LOG_FILE", root / "service.log"),
                patch.object(setup_xhs_mcp, "COOKIE_FILE", root / "cookies.json"),
                patch.object(setup_xhs_mcp, "_binary", return_value=Path("/test/server")),
                patch.object(setup_xhs_mcp, "_verify_binary", return_value=True),
                patch.object(setup_xhs_mcp, "_health", side_effect=[False, True, True]),
                patch.object(setup_xhs_mcp, "_process_alive", side_effect=[False, True]),
                patch.object(setup_xhs_mcp, "_process_identity", return_value=identity),
                patch.object(setup_xhs_mcp.subprocess, "Popen", return_value=process),
            ):
                self.assertTrue(setup_xhs_mcp.start(Namespace(timeout=5))["service"]["managed_process_alive"])
                self.assertEqual(json.loads((root / "service.json").read_text()), identity)
                self.assertEqual((root / "service.json").stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(setup_xhs_mcp.platform.system() in {"Darwin", "Linux"}, "小红书预编译运行时仅支持 macOS/Linux")
    def test_real_process_identity_can_be_read_without_signaling(self) -> None:
        identity = setup_xhs_mcp._process_identity(os.getpid())
        self.assertIsNotNone(identity)
        self.assertEqual(identity["pid"], os.getpid())
        self.assertTrue(identity["started_at"])
        self.assertTrue(identity["command"])

    def test_status_reports_verified_pinned_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "service.pid"
            with (
                patch.object(setup_xhs_mcp, "PID_FILE", pid_file),
                patch.object(setup_xhs_mcp, "DATA_ROOT", root),
                patch.object(setup_xhs_mcp, "STATE_DIR", root),
                patch.object(setup_xhs_mcp, "COOKIE_FILE", root / "cookies.json"),
                patch.object(setup_xhs_mcp, "LOG_FILE", root / "service.log"),
                patch.object(setup_xhs_mcp, "_verify_binary", return_value=True),
                patch.object(setup_xhs_mcp, "_health", return_value=False),
            ):
                result = setup_xhs_mcp.status(Namespace())
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["provider"], "xpzouying/xiaohongshu-mcp")
        self.assertEqual(result["version"], "v2.5.0")
        self.assertEqual(result["commit"], setup_xhs_mcp.UPSTREAM_COMMIT)
        self.assertTrue(result["assets_verified"]["server"])
        self.assertFalse(result["service"]["healthy"])

    def test_download_rejects_digest_mismatch(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self, _: int) -> bytes:
                if getattr(self, "done", False):
                    return b""
                self.done = True
                return b"unexpected"

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "binary"
            with patch.object(setup_xhs_mcp, "urlopen", return_value=Response()):
                with self.assertRaises(setup_xhs_mcp.SetupError):
                    setup_xhs_mcp._download("binary", "0" * 64, destination)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".download").exists())


if __name__ == "__main__":
    unittest.main()
