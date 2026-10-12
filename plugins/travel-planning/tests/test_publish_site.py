from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/travel-planning/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("publication_site_test", SCRIPTS / "publish_itinerary.py")
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


class StaticHost:
    """ZIP uploads replace the entire site, as the real static host does."""
    def __init__(self):
        self.files = {}
        self.calls = []
        self.deploy_id = None
        self.fail_read = None
        self.fail_poll = False
        self.fail_upload = False

    def api(self, method, path, data=None, content_type=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/sites":
            return {"id": "shared-site"}
        if method == "POST":
            self.files = publication.release_files(data)
            self.deploy_id = f"deploy-{self.upload_count()}"
            if self.fail_upload:
                raise RuntimeError("lost write response")
            return {"id": self.deploy_id}
        if "/files?" in path:
            return [{"path": "/" + name} for name in self.files]
        if path.startswith("/deploys/"):
            if self.fail_poll:
                raise RuntimeError("read interrupted")
            return {"site_id": "shared-site", "state": "ready"}
        return {"id": "shared-site", "ssl_url": "https://travel-book.netlify.app/",
                "published_deploy": {"id": self.deploy_id} if self.deploy_id else None}

    def public_bytes(self, url):
        path = urlsplit(url).path.lstrip("/")
        if not path or path.endswith("/"):
            path += "index.html"
        if self.fail_read == path:
            return b"outdated page"
        if path not in self.files:
            raise RuntimeError("HTTP 404")
        return self.files[path]

    def upload_count(self):
        return sum(method == "POST" and path.endswith("/deploys") for method, path in self.calls)


class SitePublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "site.json"
        self.host = StaticHost()
        self.wudang = "wudangshan-2026-10-06"
        self.chengdu = "chengdu-2026-11-01"
        self.dali = "dali-2026-12-01"
        self.first = self.trip(self.wudang, "武当山")
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def trip(self, trip_id, content):
        directory = self.root / f"trip-{len(list(self.root.iterdir()))}"
        source = self.root / "source.html"
        source.write_text(f"<html><head><title>{content}</title></head><body>{content}</body></html>", encoding="utf-8")
        publication.prepare(source, trip_id, directory)
        return directory

    def aggregate(self, trip=None, base=None, state=None):
        self.counter += 1
        directory = self.root / f"site-release-{self.counter}"
        publication.prepare_site(trip or self.first, directory, base_release=base, state_file=state)
        return directory

    def upload(self, directory, **options):
        return publication.publish(directory, self.state, self.host, confirmed=True, **options)

    def migrate(self):
        self.host.files = publication.release_files((self.first / "site.zip").read_bytes())
        self.host.deploy_id = "legacy-deploy"
        directory = self.aggregate(base=self.first)
        self.upload(directory, site_id="shared-site")
        return directory

    def test_migration_preserves_root_bytes_and_adds_a_clean_trip_path(self):
        original = (self.first / "public/index.html").read_bytes()
        directory = self.migrate()
        review, _ = publication.load_release(directory)
        self.assertEqual(self.host.files["index.html"], original)
        self.assertEqual(self.host.files[f"trips/{self.wudang}/index.html"], original)
        self.assertNotIn("_trip.json", self.host.files)  # Old single-trip writer must fail closed.
        self.assertEqual(set(self.host.files), {"index.html", "_site.json", "_headers", "history/index.html",
                                              f"trips/{self.wudang}/index.html", f"trips/{self.wudang}/_trip.json"})
        self.assertIn(f'href="/trips/{self.wudang}/"'.encode(), self.host.files["history/index.html"])
        self.assertNotIn(b'href="/history/"', original)
        state = publication.read_json(self.state)
        self.assertEqual(state["url"], f"https://travel-book.netlify.app/trips/{self.wudang}/")
        self.assertEqual(state["history_url"], "https://travel-book.netlify.app/history/")
        self.assertEqual(state["previous_deploy_id"], "legacy-deploy")
        self.assertEqual(state["files"], review["files"])

    def test_new_trips_keep_old_trips_and_updates_keep_every_url(self):
        self.migrate()
        old = dict(self.host.files)
        second = self.aggregate(self.trip(self.chengdu, "成都"), state=self.state)
        second_state = self.upload(second)
        self.assertEqual(self.host.files["index.html"], old["index.html"])
        self.assertEqual(self.host.files[f"trips/{self.wudang}/index.html"], old[f"trips/{self.wudang}/index.html"])
        history = self.host.files["history/index.html"]
        self.assertIn(f'href="/trips/{self.chengdu}/"'.encode(), history)
        self.assertIn(f'href="/trips/{self.wudang}/"'.encode(), history)
        self.assertNotIn(f'href="/trips/{self.dali}/"'.encode(), history)
        third = self.aggregate(self.trip(self.dali, "大理"), state=self.state)
        third_state = self.upload(third)
        self.assertEqual(len(third_state["trip_urls"]), 3)
        self.assertEqual(second_state["trip_urls"][self.chengdu], third_state["trip_urls"][self.chengdu])
        before = dict(self.host.files)
        updated = self.aggregate(self.trip(self.wudang, "武当山更新"), state=self.state)
        final = self.upload(updated)
        self.assertEqual(final["trip_urls"], third_state["trip_urls"])
        self.assertNotEqual(self.host.files["index.html"], before["index.html"])
        self.assertNotEqual(self.host.files["history/index.html"], before["history/index.html"])
        self.assertIn("武当山更新".encode(), self.host.files["history/index.html"])
        for trip_id in (self.chengdu, self.dali):
            self.assertEqual(self.host.files[f"trips/{trip_id}/index.html"], before[f"trips/{trip_id}/index.html"])
        self.assertEqual(self.host.calls.count(("POST", "/sites")), 0)

    def test_a_new_site_is_created_once_and_repeat_does_not_upload(self):
        directory = self.aggregate()
        first = self.upload(directory, create_site=True)
        self.assertEqual(first["history_url"], "https://travel-book.netlify.app/history/")
        again = self.upload(directory)
        self.assertEqual(first["deploy_id"], again["deploy_id"])
        self.assertEqual(self.host.upload_count(), 1)
        self.assertEqual(self.host.calls.count(("POST", "/sites")), 1)

    def test_stale_snapshot_cannot_erase_a_later_trip(self):
        base = self.migrate()
        stale = self.aggregate(self.trip(self.dali, "大理"), base=base)
        fresh = self.aggregate(self.trip(self.chengdu, "成都"), base=base)
        self.upload(fresh)
        prior_files = dict(self.host.files)
        with self.assertRaisesRegex(ValueError, "基线"):
            self.upload(stale)
        self.assertEqual(self.host.files, prior_files)
        self.assertEqual(self.host.upload_count(), 2)

    def test_existing_site_requires_matching_base_and_cannot_be_rebound(self):
        self.migrate()
        with self.assertRaisesRegex(ValueError, "不得切换站点"):
            self.upload(self.aggregate(base=Path(publication.read_json(self.state)["release_dir"])), site_id="other-site")
        missing_base = self.aggregate(self.trip(self.chengdu, "成都"))
        other_state = self.root / "other-state.json"
        with self.assertRaisesRegex(ValueError, "禁止覆盖"):
            publication.publish(missing_base, other_state, self.host, site_id="shared-site", confirmed=True)
        self.assertFalse(other_state.exists())
        self.assertEqual(self.host.upload_count(), 1)

    def test_legacy_migration_refuses_a_different_remote_page(self):
        self.host.files = publication.release_files((self.first / "site.zip").read_bytes())
        self.host.files["index.html"] = b"someone else's page"
        self.host.deploy_id = "legacy-deploy"
        with self.assertRaisesRegex(RuntimeError, "基线|审阅版"):
            self.upload(self.aggregate(base=self.first), site_id="shared-site")
        self.assertEqual(self.host.upload_count(), 0)

    def test_unmanaged_remote_file_is_preserved_by_refusing_to_upload(self):
        base = self.migrate()
        self.host.files["unrelated/index.html"] = b"someone else's content"
        directory = self.aggregate(self.trip(self.chengdu, "成都"), base=base)
        with self.assertRaisesRegex(ValueError, "未纳入的内容"):
            self.upload(directory)
        self.assertEqual(self.host.files["unrelated/index.html"], b"someone else's content")
        self.assertEqual(self.host.upload_count(), 1)

    def test_remote_file_listing_handles_pagination(self):
        files = {f"page-{number}.html": "hash" for number in range(205)}
        class PagedClient:
            def __init__(self):
                self.calls = []
            def api(self, method, path):
                self.calls.append(path)
                page = int(path.rsplit("=", 1)[1])
                return [{"path": "/" + name} for name in list(files)[(page-1)*100:page*100]]
        client = PagedClient()
        publication.verify_remote_inventory(client, "shared-site", files)
        self.assertEqual(len(client.calls), 3)

    def test_an_old_single_trip_package_cannot_overwrite_a_migrated_site(self):
        self.migrate()
        with self.assertRaisesRegex(RuntimeError, "404"):
            publication.publish(self.first, self.root / "legacy-state.json", self.host,
                                site_id="shared-site", confirmed=True)
        self.assertEqual(self.host.upload_count(), 1)

    def test_old_trip_read_failure_prevents_success_and_status_recovers(self):
        self.migrate()
        directory = self.aggregate(self.trip(self.chengdu, "成都"), state=self.state)
        original_api = self.host.api
        def uploaded_then_bad_read(method, path, *args):
            result = original_api(method, path, *args)
            if method == "POST":
                self.host.fail_read = f"trips/{self.wudang}/index.html"
            return result
        self.host.api = uploaded_then_bad_read
        with self.assertRaisesRegex(RuntimeError, "审阅版"):
            self.upload(directory)
        state = publication.read_json(self.state)
        self.assertEqual(state["status"], "ready_unverified")
        self.assertNotIn("url", state)
        self.assertNotIn("trip_urls", state)
        self.assertNotIn("history_url", state)
        self.host.fail_read = None
        result = publication.status(self.state, self.host)
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(result["trip_urls"]), 2)
        self.assertEqual(result["history_url"], "https://travel-book.netlify.app/history/")
        self.assertEqual(self.host.upload_count(), 2)

    def test_netlify_html_insertions_do_not_hide_other_content_changes(self):
        self.migrate()
        original_read = self.host.public_bytes
        site = b"shared-site"
        comment = (b"\n<!-- This site is hosted on Netlify. Anyone can build and deploy a site\n"
                   b"     like this one for free: https://netlify.new/?utm_campaign=loops&utm_source=ai-legible"
                   b"&utm_medium=owned&utm_content=comment&utm_id=" + site + b"\n"
                   b"     Netlify hosting facts for this site: static/SSR served via Netlify Edge. -->")
        script = (b'<script async src="/.netlify/scripts/hud?variant=public" data-nf-variant="public" '
                  b'data-netlify-site-id="' + site + b'" data-app-host="app.netlify.com"></script>\n')
        def augmented(url):
            data = original_read(url)
            if data.endswith(b"</html>"):
                return data.replace(b"</head>", comment + b"</head>", 1) + script
            return data
        self.host.public_bytes = augmented
        directory = self.aggregate(self.trip(self.chengdu, "成都"), state=self.state)
        self.assertEqual(self.upload(directory)["status"], "published")
        self.host.files[f"trips/{self.wudang}/index.html"] += b"unexpected change"
        with self.assertRaisesRegex(RuntimeError, "审阅版"):
            self.upload(directory)

    def test_pending_or_unknown_upload_is_not_retried(self):
        self.host.fail_upload = True
        directory = self.aggregate()
        with self.assertRaisesRegex(RuntimeError, "lost write response"):
            self.upload(directory, site_id="shared-site")
        self.assertEqual(publication.read_json(self.state)["status"], "upload_unknown")
        with self.assertRaisesRegex(ValueError, "先运行 status"):
            self.upload(directory)
        self.assertEqual(self.host.upload_count(), 1)

    def test_auto_baseline_requires_published_state_and_detects_manual_override(self):
        with self.assertRaisesRegex(ValueError, "找不到站点状态"):
            self.aggregate(state=self.state)
        base = self.migrate()
        with self.assertRaisesRegex(ValueError, "最后验证"):
            self.aggregate(base=self.first, state=self.state)
        state = publication.read_json(self.state)
        state["status"] = "ready_unverified"
        publication.save_json(self.state, state)
        with self.assertRaisesRegex(ValueError, "先运行 status"):
            self.aggregate(state=self.state)
        self.assertTrue(base.is_dir())

    def test_preparation_is_offline_and_does_not_scan_private_or_unpublished_files(self):
        (self.root / ".env").write_text("never-upload-this")
        unused = self.trip(self.chengdu, "not approved for publishing")
        directory = self.aggregate()
        _, data = publication.load_release(directory)
        files = publication.release_files(data)
        self.assertFalse(self.host.calls)
        self.assertEqual(len(files), 6)
        self.assertNotIn(b"never-upload-this", b"".join(files.values()))
        self.assertNotIn(b"not approved for publishing", b"".join(files.values()))
        self.assertTrue(unused.is_dir())
        with self.assertRaises(FileExistsError):
            publication.prepare_site(self.first, directory)

    def test_history_escapes_trip_titles_and_uses_only_reviewed_paths(self):
        source = self.root / "special.html"
        source.write_text("<html><head><title>巴黎 &amp; &lt;script&gt;</title></head><body>test</body></html>",
                          encoding="utf-8")
        trip_id = "paris-2026-12-10"
        release = self.root / "special-trip"
        publication.prepare(source, trip_id, release)
        directory = self.aggregate(release)
        page = (directory / "public/history/index.html").read_text(encoding="utf-8")
        self.assertIn("巴黎 &amp; &lt;script&gt;", page)
        self.assertNotIn("巴黎 & <script>", page)
        self.assertIn(f'href="/trips/{trip_id}/"', page)
        self.assertNotIn("source.html", page)

    def test_published_history_snapshot_survives_a_template_change(self):
        directory = self.aggregate()
        original = (directory / "public/history/index.html").read_bytes()
        with patch.object(publication, "history_page", return_value=b"<html>new history template</html>"):
            publication.load_release(directory)
            updated = self.aggregate(self.trip(self.chengdu, "成都"), base=directory)
        self.assertEqual((directory / "public/history/index.html").read_bytes(), original)
        self.assertEqual((updated / "public/history/index.html").read_bytes(), b"<html>new history template</html>")

    def test_changed_preview_or_extra_private_file_is_rejected_before_network(self):
        directory = self.aggregate()
        (directory / "public/.env").write_text("never-upload-this")
        with self.assertRaisesRegex(ValueError, "未审阅文件"):
            self.upload(directory, site_id="shared-site")
        self.assertFalse(self.host.calls)
        other = self.aggregate()
        (other / "public" / f"trips/{self.wudang}/index.html").write_text("changed")
        with self.assertRaisesRegex(ValueError, "预览文件"):
            self.upload(other, site_id="shared-site")
        self.assertFalse(self.host.calls)

    def test_publication_lock_blocks_concurrent_publish_without_network(self):
        directory = self.aggregate()
        with publication.publication_lock(self.state):
            with self.assertRaisesRegex(RuntimeError, "已有发布"):
                self.upload(directory, site_id="shared-site")
        self.assertFalse(self.host.calls)
        self.assertFalse(self.state.with_name("site.json.lock").exists())
        self.upload(directory, site_id="shared-site")

    def test_publication_requires_confirmation_and_separate_state_from_legacy(self):
        directory = self.aggregate(base=self.first)
        with self.assertRaisesRegex(ValueError, "明确授权"):
            publication.publish(directory, self.state, self.host, site_id="shared-site")
        publication.save_json(self.state, {"trip_id": self.wudang, "status": "published", "site_id": "shared-site"})
        with self.assertRaisesRegex(ValueError, "整站专用状态"):
            self.upload(directory)
        self.assertFalse(self.host.calls)

    def test_remote_change_during_baseline_check_stops_upload(self):
        base = self.migrate()
        directory = self.aggregate(self.trip(self.chengdu, "成都"), base=base)
        original_read = self.host.public_bytes
        def other_writer(url):
            result = original_read(url)
            self.host.deploy_id = "another-deployment"
            return result
        self.host.public_bytes = other_writer
        with self.assertRaisesRegex(RuntimeError, "版本发生变化"):
            self.upload(directory)
        self.assertEqual(self.host.upload_count(), 1)


if __name__ == "__main__":
    unittest.main()
