from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("xhs_runtime_build", ROOT / "skills/xiaohongshu/scripts/build_runtime.py")
assert SPEC and SPEC.loader
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class XhsRuntimeBuildTest(unittest.TestCase):
    def test_manifest_requires_matching_patch_and_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "server"
            binary.write_bytes(b"verified local build")
            manifest = binary.with_suffix(".build.json")
            self.assertFalse(build.verified_build(binary))
            manifest.write_text(json.dumps({"repair_fingerprint": build.repair_fingerprint(),
                                            "binary_sha256": build.sha256(binary)}))
            self.assertTrue(build.verified_build(binary))
            binary.write_bytes(b"unexpected replacement")
            self.assertFalse(build.verified_build(binary))

    def test_manifest_for_old_patch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "server"
            binary.write_bytes(b"build")
            binary.with_suffix(".build.json").write_text(json.dumps(
                {"repair_fingerprint": "old", "binary_sha256": build.sha256(binary)}))
            self.assertFalse(build.verified_build(binary))

    def test_download_rejects_unverified_content(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "archive.tar.gz"
            def fake_run(*args, **kwargs):
                destination.with_suffix(".download").write_bytes(b"wrong download")
            with patch.object(build.subprocess, "run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "SHA256"):
                    build.download("https://example.test/source", "0" * 64, destination)
            self.assertFalse(destination.exists())

    def test_extract_rejects_traversal_and_links(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "source.tar"
                with tarfile.open(archive, "w") as tar:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = "../escape" if kind != tarfile.REGTYPE else ""
                    member.size = 1 if kind == tarfile.REGTYPE else 0
                    tar.addfile(member, io.BytesIO(b"x") if member.size else None)
                with self.assertRaisesRegex(RuntimeError, "不安全"):
                    build.extract(archive, root / "out")
                self.assertFalse((root / "escape").exists())

    def test_runtime_endpoints_and_source_lock_agree(self):
        lock = json.loads(build.LOCK.read_text())
        mcp = json.loads((ROOT / ".mcp.json").read_text())
        self.assertEqual(mcp["mcpServers"]["xiaohongshu-mcp"]["url"], lock["endpoint"])
        self.assertEqual(lock["local_repair"]["version"], "search-ready-v1")
        self.assertIn("waitSearchReady", (build.SKILL_ROOT / lock["local_repair"]["patch"]).read_text())


if __name__ == "__main__":
    unittest.main()
