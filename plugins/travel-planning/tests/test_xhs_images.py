from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_research_sources import research_sources
import xhs_images


class XhsImagesTest(unittest.TestCase):
    def test_empty_body_keeps_images_without_exposing_links(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "tokens.json"
            cache.write_text(json.dumps({"notes": {"n": {"xsec_token": "private"}}}))
            detail = {"data": {"note": {"noteId": "n", "desc": "", "imageList": [
                {"urlDefault": "https://sns-webpic.xhscdn.com/a?secret=private"}]}}}
            with patch.object(research_sources, "xhs_mcp_token_cache_path", return_value=cache), \
                 patch.object(research_sources, "require_xhs_login"), \
                 patch.object(research_sources, "read_xhs_mcp_token_cache", return_value=json.loads(cache.read_text())), \
                 patch.object(research_sources, "xhs_mcp_call", return_value=detail):
                result = research_sources.xhs_detail(Namespace(note_id="n"))
            self.assertEqual(result["image_reading"]["image_count"], 1)
            self.assertTrue(result["image_reading"]["body_missing"])
            self.assertEqual(result["image_reading"]["status"], "not_read")
            self.assertNotIn("private", json.dumps(result))
            self.assertNotIn("xhscdn", json.dumps(result))
            self.assertEqual(xhs_images.cache_path(Path(directory), "n").stat().st_mode & 0o777, 0o600)

    def test_selects_original_image_index_without_refetching_note(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xhs_images.cache_images(root, "n", [{"urlDefault": f"https://sns-webpic.xhscdn.com/{i}"} for i in range(3)])
            with patch.object(xhs_images, "fetch_image", return_value=(b"image", "png")) as fetch, \
                 patch.object(xhs_images.tempfile, "mkdtemp", return_value=str(root)):
                result = xhs_images.prepare_images(root, "n", [3, 1])
            self.assertEqual([item["image_index"] for item in result["images"]], [3, 1])
            self.assertEqual(fetch.call_args_list[0].args[0], "https://sns-webpic.xhscdn.com/2")
            self.assertEqual(result["status"], "ready_for_reading")
            self.assertFalse(result["text_extracted"])
            self.assertEqual((root / "image-3.png").read_bytes(), b"image")
            self.assertNotIn("xhscdn", json.dumps(result))

    def test_rejects_unsafe_host_credentials_and_redirects(self):
        for url in ["https://127.0.0.1/a", "https://sns.xhscdn.com.evil.test/a", "https://evil.test/a", "https://name:secret@sns.xhscdn.com/a", "https://sns.xhscdn.com:443/a"]:
            self.assertIsNone(xhs_images.image_url({"urlDefault": url}))
        self.assertEqual(xhs_images.image_url({"urlDefault": "http://sns.xhscdn.com/a#x"}), "https://sns.xhscdn.com/a")
        self.assertIsNone(xhs_images.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_budget_indices_and_cache_expiry_prevent_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xhs_images.cache_images(root, "n", [{"urlDefault": "https://sns.xhscdn.com/a"}])
            with patch.object(xhs_images, "fetch_image") as fetch:
                for indices in [[1, 2, 3, 4], [0], [1, 1], [2], [True], []]:
                    with self.assertRaises(ValueError): xhs_images.prepare_images(root, "n", indices)
                with patch.object(xhs_images.time, "time", return_value=10**12):
                    with self.assertRaises(ValueError): xhs_images.prepare_images(root, "n", [1])
                fetch.assert_not_called()

    def test_failed_download_does_not_leak_signed_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xhs_images.cache_images(root, "n", [{"urlDefault": "https://sns.xhscdn.com/a?secret=private"}])
            with patch.object(xhs_images, "fetch_image", side_effect=RuntimeError("private signed URL")):
                result = xhs_images.prepare_images(root, "n", [1])
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("private", json.dumps(result))
            self.assertNotIn("local_path", result["images"][0])

    def test_download_rejects_html_and_oversized_images(self):
        response = MagicMock()
        response.geturl.return_value = "https://sns.xhscdn.com/a"
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch.object(xhs_images, "build_opener", return_value=opener):
            response.read.return_value = b"<html>login required</html>"
            with self.assertRaises(ValueError): xhs_images.fetch_image("https://sns.xhscdn.com/a")
            response.read.return_value = b"\x89PNG\r\n\x1a\n" + b"0" * xhs_images.MAX_BYTES
            with self.assertRaises(ValueError): xhs_images.fetch_image("https://sns.xhscdn.com/a")


if __name__ == "__main__":
    unittest.main()
