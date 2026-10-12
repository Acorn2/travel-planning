from __future__ import annotations

import importlib.util
from contextlib import redirect_stdout
import io
import json
import ssl
import os
import socket
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/travel-planning/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("publish_itinerary", SCRIPTS / "publish_itinerary.py")
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


class FakeNetlify:
    def __init__(self):
        self.calls = []
        self.site_id = "test-site"
        self.deploy_id = None
        self.html = b""
        self.marker = {}
        self.bad_page = False
        self.fail_poll = False

    def api(self, method, path, data=None, content_type=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/sites":
            return {"id": self.site_id}
        if method == "POST":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.html = archive.read("index.html")
                self.marker = json.loads(archive.read("_trip.json"))
            self.deploy_id = "deploy-" + str(len(self.calls))
            return {"id": self.deploy_id}
        if path.startswith("/deploys/"):
            if self.fail_poll:
                raise RuntimeError("network interrupted")
            return {"id": self.deploy_id, "site_id": self.site_id, "state": "ready"}
        return {"id": self.site_id, "ssl_url": "https://dedicated-trip.netlify.app",
                "published_deploy": {"id": self.deploy_id} if self.deploy_id else None}

    def public_bytes(self, url):
        if url.endswith("_trip.json"):
            return json.dumps(self.marker).encode()
        return b"wrong version" if self.bad_page else self.html

    def uploads(self):
        return [path for method, path in self.calls if method == "POST" and path.endswith("/deploys")]


class PublicationConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env_file = self.root / ".env"
        self.state = self.root / ".travel-research/publication/site.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_env_is_resolved_from_project_state_instead_of_plugin_working_directory(self):
        plugin = self.root / "plugins/travel-planning"
        plugin.mkdir(parents=True)
        with patch.object(publication.Path, "cwd", return_value=plugin):
            self.assertEqual(publication.publication_env_file(self.state), self.env_file.resolve())
        old_state = self.root / ".travel-research/wudangshan-2026-10-06/artifacts/publication.json"
        self.assertEqual(publication.publication_env_file(old_state), self.env_file.resolve())

    def test_project_config_supports_quotes_comments_and_only_extracts_publication_fields(self):
        self.env_file.write_text('# local only\nNETLIFY_AUTH_TOKEN="local-test-token" # comment\n'
                                 "export NETLIFY_SITE_ID='fixed-site'\nDATABASE_PASSWORD=unrelated-test-value\n")
        settings = publication.load_publication_settings(self.env_file, {})
        self.assertEqual(settings, {"NETLIFY_AUTH_TOKEN": "local-test-token", "NETLIFY_SITE_ID": "fixed-site"})
        self.assertNotIn("DATABASE_PASSWORD", settings)

    def test_nonempty_project_values_override_stale_terminal_values(self):
        self.env_file.write_text("NETLIFY_AUTH_TOKEN=current-test-token\nNETLIFY_SITE_ID=fixed-site\n")
        settings = publication.load_publication_settings(self.env_file, {"NETLIFY_AUTH_TOKEN": "old-test-token"})
        self.assertEqual(settings["NETLIFY_AUTH_TOKEN"], "current-test-token")

    def test_missing_or_empty_file_retains_environment_compatibility(self):
        inherited = {"NETLIFY_AUTH_TOKEN": "terminal-test-token", "NETLIFY_SITE_ID": "fixed-site", "OTHER": "unused"}
        self.assertEqual(publication.load_publication_settings(self.env_file, inherited),
                         {"NETLIFY_AUTH_TOKEN": "terminal-test-token", "NETLIFY_SITE_ID": "fixed-site"})
        self.env_file.write_text("NETLIFY_AUTH_TOKEN=\nNETLIFY_SITE_ID=\n")
        self.assertEqual(publication.load_publication_settings(self.env_file, inherited)["NETLIFY_AUTH_TOKEN"], "terminal-test-token")
        with self.assertRaisesRegex(ValueError, "NETLIFY_AUTH_TOKEN"):
            publication.Netlify(publication.load_publication_settings(self.env_file, {}).get("NETLIFY_AUTH_TOKEN", ""))

    def test_invalid_config_does_not_execute_shell_or_include_values_in_errors(self):
        for value in ('"private-test-value', '$(touch forbidden-test-file)', '${PRIVATE_TEST_VALUE}',
                      '`touch forbidden-test-file`', 'private-test-value another-value'):
            with self.subTest(value=value):
                self.env_file.write_text("NETLIFY_AUTH_TOKEN=" + value + "\n")
                with self.assertRaises(ValueError) as caught:
                    publication.load_publication_settings(self.env_file, {})
                self.assertNotIn(value, str(caught.exception))
        self.assertFalse((self.root / "forbidden-test-file").exists())

    def test_cli_uses_local_credentials_and_site_id_without_export_or_printing_token(self):
        self.env_file.write_text("NETLIFY_AUTH_TOKEN=local-test-token\nNETLIFY_SITE_ID=fixed-site\n")
        client = Mock()
        result = {"url": "https://fixed-site.netlify.app/trips/wudangshan-2026-10-06/",
                  "history_url": "https://fixed-site.netlify.app/history/"}
        arguments = ["publish_itinerary.py", "publish", str(self.root / "release"),
                     "--state-file", str(self.state), "--confirm-public"]
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", arguments), \
                patch.object(publication, "Netlify", return_value=client) as netlify, \
                patch.object(publication, "publish", return_value=result) as publish, redirect_stdout(output):
            publication.main()
        netlify.assert_called_once_with("local-test-token")
        self.assertEqual(publish.call_args.kwargs["site_id"], "fixed-site")
        self.assertTrue(publish.call_args.kwargs["confirmed"])
        self.assertNotIn("local-test-token", output.getvalue())
        self.assertIn("全部规划历史：https://fixed-site.netlify.app/history/", output.getvalue())

    def test_explicit_env_file_works_for_custom_state_layout(self):
        self.env_file.write_text("NETLIFY_AUTH_TOKEN=local-test-token\n")
        arguments = ["publish_itinerary.py", "status", "--state-file", str(self.root / "custom/state.json"),
                     "--env-file", str(self.env_file)]
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", arguments), \
                patch.object(publication, "Netlify") as netlify, \
                patch.object(publication, "status", return_value={"url": "https://fixed-site.netlify.app/"}), \
                redirect_stdout(io.StringIO()):
            publication.main()
        netlify.assert_called_once_with("local-test-token")

    def test_config_is_never_copied_into_a_trip_or_site_archive(self):
        self.env_file.write_text("NETLIFY_AUTH_TOKEN=private-test-token\nNETLIFY_SITE_ID=fixed-site\n")
        settings = publication.load_publication_settings(self.env_file, {})
        source = self.root / "index.html"
        source.write_text("<html><head></head><body>Reviewed trip</body></html>")
        trip_release = self.root / "trip-release"
        site_release = self.root / "site-release"
        publication.prepare(source, "wudangshan-2026-10-06", trip_release)
        publication.prepare_site(trip_release, site_release)
        for directory in (trip_release, site_release):
            _, archive = publication.load_release(directory)
            self.assertNotIn(settings["NETLIFY_AUTH_TOKEN"], (directory / "review.json").read_text())
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                self.assertNotIn(".env", bundle.namelist())
                self.assertTrue(all(settings["NETLIFY_AUTH_TOKEN"].encode() not in bundle.read(name)
                                    for name in bundle.namelist()))


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "index.html"
        self.source.write_text('<html><head><title>武当山</title></head><body>行程</body></html>', encoding="utf-8")
        self.release = self.root / "release-1"
        self.state = self.root / "private-state.json"
        self.trip_id = "wudangshan-2026-10-06"
        publication.prepare(self.source, self.trip_id, self.release)
        self.client = FakeNetlify()

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, **options):
        return publication.publish(self.release, self.state, self.client, confirmed=True, **options)

    def test_prepare_is_offline_allowlisted_and_preserves_prior_release(self):
        (self.root / ".env").write_text("private-value")
        review, data = publication.load_release(self.release)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {"index.html", "_trip.json", "_headers"})
            self.assertIn(b"itinerary-release-at", archive.read("index.html"))
            self.assertNotIn(b"private-value", data)
        self.assertEqual(review["trip_id"], self.trip_id)
        with self.assertRaises(FileExistsError):
            publication.prepare(self.source, self.trip_id, self.release)

    def test_publish_requires_confirmation_before_any_network(self):
        with self.assertRaisesRegex(ValueError, "明确授权"):
            publication.publish(self.release, self.state, self.client, site_id="test-site")
        self.assertEqual(self.client.calls, [])

    def test_refuses_changed_reviewed_page_or_archive(self):
        (self.release / "public/index.html").write_text("changed")
        with self.assertRaisesRegex(ValueError, "预览文件"):
            self.upload(site_id="test-site")
        self.assertEqual(self.client.calls, [])
        (self.release / "site.zip").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "发布包已改变"):
            self.upload(site_id="test-site")

    def test_first_publish_update_and_repeat_preserve_url_and_site(self):
        first = self.upload(site_id="test-site")
        self.assertEqual(first["status"], "published")
        self.assertEqual(first["url"], "https://dedicated-trip.netlify.app/")
        repeat = self.upload()
        self.assertEqual(repeat["deploy_id"], first["deploy_id"])
        self.assertEqual(len(self.client.uploads()), 1)
        self.source.write_text('<html><head></head><body>updated</body></html>')
        next_release = self.root / "release-2"
        publication.prepare(self.source, self.trip_id, next_release)
        update = publication.publish(next_release, self.state, self.client, confirmed=True)
        self.assertEqual(update["url"], first["url"])
        self.assertEqual(update["site_id"], first["site_id"])
        self.assertEqual(update["previous_deploy_id"], first["deploy_id"])
        self.assertEqual(len(self.client.uploads()), 2)

    def test_site_creation_binds_before_upload_and_is_not_repeated(self):
        self.client.fail_poll = True
        with self.assertRaises(RuntimeError):
            self.upload(create_site=True)
        stored = publication.read_json(self.state)
        self.assertEqual(stored["site_id"], "test-site")
        self.assertEqual(stored["status"], "deploying")
        with self.assertRaisesRegex(ValueError, "先运行 status"):
            self.upload()
        self.client.fail_poll = False
        result = publication.finish_deploy(self.client, stored, self.state)
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(self.client.uploads()), 1)
        self.assertEqual(self.client.calls.count(("POST", "/sites")), 1)

    def test_refuses_rebinding_or_reusing_other_trip_state(self):
        self.upload(site_id="test-site")
        with self.assertRaisesRegex(ValueError, "不得切换站点"):
            self.upload(site_id="other-site")
        with self.assertRaisesRegex(ValueError, "不得切换站点"):
            self.upload(create_site=True)
        state = publication.read_json(self.state)
        state["trip_id"] = "other-2026-10-06"
        publication.save_json(self.state, state)
        with self.assertRaisesRegex(ValueError, "其他行程"):
            self.upload()
        self.assertEqual(len(self.client.uploads()), 1)

    def test_refuses_overwriting_an_unrelated_existing_site(self):
        self.client.deploy_id = "someone-elses-deploy"
        self.client.marker = {"schema": "travel-publication/v1", "trip_id": "other-trip"}
        with self.assertRaisesRegex(ValueError, "禁止覆盖"):
            self.upload(site_id="test-site")
        self.assertFalse(self.client.uploads())
        self.assertFalse(self.state.exists())

    def test_public_verification_failure_does_not_report_success(self):
        self.client.bad_page = True
        with self.assertRaisesRegex(RuntimeError, "公网页面"):
            self.upload(site_id="test-site")
        stored = publication.read_json(self.state)
        self.assertEqual(stored["status"], "ready_unverified")
        self.assertNotIn("url", stored)
        self.assertEqual(stored["candidate_url"], "https://dedicated-trip.netlify.app/")
        self.client.bad_page = False
        verified = publication.finish_deploy(self.client, stored, self.state)
        self.assertEqual(verified["status"], "published")
        self.assertEqual(len(self.client.uploads()), 1)

    def test_unknown_upload_result_is_preserved_and_not_retried(self):
        original_api = self.client.api
        def disconnected(method, path, *args):
            if method == "POST":
                raise RuntimeError("connection lost")
            return original_api(method, path, *args)
        self.client.api = disconnected
        with self.assertRaises(RuntimeError):
            self.upload(site_id="test-site")
        self.assertEqual(publication.read_json(self.state)["status"], "upload_unknown")
        with self.assertRaisesRegex(ValueError, "先运行 status"):
            self.upload()

    def test_missing_credentials_and_local_resources_fail_cleanly(self):
        with self.assertRaisesRegex(ValueError, "NETLIFY_AUTH_TOKEN"):
            publication.Netlify("")
        self.source.write_text('<head></head><script src="private.js"></script>')
        with self.assertRaisesRegex(ValueError, "本地资源"):
            publication.prepare(self.source, self.trip_id, self.root / "local-assets")
        self.assertFalse((self.root / "local-assets").exists())

    def test_site_creation_lost_response_does_not_create_again(self):
        def disconnected(*args):
            raise RuntimeError("connection lost")
        self.client.api = disconnected
        with self.assertRaises(RuntimeError):
            self.upload(create_site=True)
        self.assertEqual(publication.read_json(self.state)["status"], "creation_unknown")
        with self.assertRaisesRegex(ValueError, "不会重复创建"):
            self.upload(create_site=True)

    def test_authenticated_api_headers_never_reach_public_verification(self):
        from unittest.mock import Mock
        client = publication.Netlify("test-placeholder-token")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"public page"
        client.opener = Mock()
        client.opener.open.return_value = response
        self.assertEqual(client.public_bytes("https://dedicated-trip.netlify.app/"), b"public page")
        request = client.opener.open.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertIsNone(publication.NoRedirect().redirect_request(request, None, 302, "", {}, "https://other.example/"))

    def test_missing_default_ca_uses_existing_certifi_with_verification_enabled(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        bundle_context = Mock(wraps=context)
        bundle_context.load_verify_locations.return_value = None
        certificate_package = Mock()
        certificate_package.where.return_value = "/already-installed/cacert.pem"
        paths = Mock(cafile=None, capath=None)
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"certifi": certificate_package}), \
                patch.object(publication.ssl, "create_default_context", return_value=bundle_context), \
                patch.object(publication.ssl, "get_default_verify_paths", return_value=paths):
            self.assertIs(publication.create_ssl_context(), bundle_context)
        bundle_context.load_verify_locations.assert_called_once_with(cafile="/already-installed/cacert.pem")
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_explicit_ca_configuration_is_not_replaced(self):
        context = Mock()
        context.cert_store_stats.return_value = {"x509_ca": 0}
        paths = Mock(cafile=None, capath=None)
        certificate_package = Mock()
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/explicit/ca.pem"}, clear=True), \
                patch.dict(sys.modules, {"certifi": certificate_package}), \
                patch.object(publication.ssl, "create_default_context", return_value=context), \
                patch.object(publication.ssl, "get_default_verify_paths", return_value=paths):
            publication.create_ssl_context()
        certificate_package.where.assert_not_called()
        context.load_verify_locations.assert_not_called()

    def test_tls_failure_is_distinguished_without_revealing_headers_or_retrying(self):
        client = publication.Netlify("secret-placeholder")
        client.opener = Mock()
        client.opener.open.side_effect = URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
        with self.assertRaises(publication.RequestNotSentError) as caught:
            client.api("POST", "/sites", b"{}")
        self.assertNotIn("secret-placeholder", str(caught.exception))
        self.assertEqual(client.opener.open.call_count, 1)

    def test_creation_tls_failure_can_be_retried_after_ca_fix(self):
        original_api = self.client.api
        def tls_failure(*args):
            raise publication.RequestNotSentError("certificate validation failed")
        self.client.api = tls_failure
        with self.assertRaises(publication.RequestNotSentError):
            self.upload(create_site=True)
        self.assertEqual(publication.read_json(self.state)["status"], "creation_not_sent")
        self.client.api = original_api
        result = self.upload(create_site=True)
        self.assertEqual(result["status"], "published")
        self.assertEqual(self.client.calls.count(("POST", "/sites")), 1)

    def test_upload_tls_failure_keeps_existing_site_and_can_be_retried(self):
        original_api = self.client.api
        def tls_failure(method, path, *args):
            if method == "POST" and path.endswith("/deploys"):
                raise publication.RequestNotSentError("certificate validation failed")
            return original_api(method, path, *args)
        self.client.api = tls_failure
        with self.assertRaises(publication.RequestNotSentError):
            self.upload(site_id="test-site")
        self.assertEqual(publication.read_json(self.state)["status"], "bound")
        self.client.api = original_api
        result = self.upload()
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(self.client.uploads()), 1)

    def test_missing_certifi_does_not_disable_verification(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        paths = Mock(cafile=None, capath=None)
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"certifi": None}), \
                patch.object(publication.ssl, "create_default_context", return_value=context), \
                patch.object(publication.ssl, "get_default_verify_paths", return_value=paths):
            self.assertIs(publication.create_ssl_context(), context)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_public_http_failure_identifies_url_and_status_without_retry(self):
        client = publication.Netlify("secret-placeholder")
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError("https://dedicated-trip.netlify.app/", 404, "Not Found", {}, None)
        with self.assertRaisesRegex(RuntimeError, "HTTP 404") as caught:
            client.public_bytes("https://dedicated-trip.netlify.app/")
        self.assertIn("https://dedicated-trip.netlify.app/", str(caught.exception))
        self.assertNotIn("secret-placeholder", str(caught.exception))
        self.assertEqual(client.opener.open.call_count, 1)

    def test_public_network_failures_report_specific_category(self):
        client = publication.Netlify("secret-placeholder")
        for reason, label in [(socket.gaierror(-2, "unknown host"), "DNS"),
                              (ssl.SSLCertVerificationError(1, "untrusted"), "TLS 证书"),
                              (TimeoutError(), "连接超时"), (ConnectionResetError(), "连接被重置")]:
            with self.subTest(label=label):
                client.opener = Mock()
                client.opener.open.side_effect = URLError(reason)
                with self.assertRaisesRegex(RuntimeError, label):
                    client.public_bytes("https://dedicated-trip.netlify.app/")

    def test_netlify_login_protection_is_identified_without_sending_credentials(self):
        client = publication.Netlify("secret-placeholder")
        client.opener = Mock()
        body = io.BytesIO(b'<title>Login Redirect</title><script>app.netlify.com/edge-access</script>')
        client.opener.open.side_effect = HTTPError("https://dedicated-trip.netlify.app/", 401, "Unauthorized", {}, body)
        with self.assertRaisesRegex(RuntimeError, "访客登录保护") as caught:
            client.public_bytes("https://dedicated-trip.netlify.app/")
        self.assertIn("Public", str(caught.exception))
        self.assertNotIn("secret-placeholder", str(caught.exception))
        self.assertIsNone(client.opener.open.call_args.args[0].get_header("Authorization"))

    def test_status_recovers_public_read_failure_without_new_upload(self):
        original_read = self.client.public_bytes
        def unavailable(url):
            raise RuntimeError("HTTP 503")
        self.client.public_bytes = unavailable
        with self.assertRaisesRegex(RuntimeError, "503"):
            self.upload(create_site=True)
        stored = publication.read_json(self.state)
        self.assertEqual(stored["status"], "ready_unverified")
        self.assertEqual(stored["candidate_url"], "https://dedicated-trip.netlify.app/")
        self.client.public_bytes = original_read
        result = publication.finish_deploy(self.client, stored, self.state)
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(self.client.uploads()), 1)
        self.assertEqual(self.client.calls.count(("POST", "/sites")), 1)


if __name__ == "__main__":
    unittest.main()
