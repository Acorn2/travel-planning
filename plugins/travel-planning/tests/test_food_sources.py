from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROVIDERS = Path(__file__).resolve().parents[1] / "scripts" / "providers"
sys.path.insert(0, str(PROVIDERS))
import food_sources as food


class FoodSourcesTest(unittest.TestCase):
    def test_transport_without_runtime_and_credentials(self):
        with tempfile.TemporaryDirectory() as home:
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "food_status", "arguments": {"platform": "douyin"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "publish_video", "arguments": {}}},
            ]
            run = subprocess.run([sys.executable, str(PROVIDERS / "food_sources.py"), "stdio"],
                                 input="\n".join(json.dumps(v) for v in messages) + "\n", text=True,
                                 capture_output=True, env={**os.environ, "HOME": home}, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            replies = [json.loads(v) for v in run.stdout.splitlines()]
            self.assertEqual([v["id"] for v in replies], [1, 2, 3, 4])
            tools = replies[1]["result"]["tools"]
            self.assertEqual({v["name"] for v in tools}, set(food.TOOLS))
            self.assertTrue(all(v["annotations"]["readOnlyHint"] for v in tools))
            self.assertFalse(json.loads(replies[2]["result"]["content"][0]["text"])["live_verified"])
            self.assertTrue(replies[3]["result"]["isError"])

    def test_tokenized_links_and_private_fields_do_not_survive(self):
        rows = [{"url": "https://www.douyin.com/video/123?token=private#secret", "title": "宜昌小吃",
                 "cookie": "secret", "video_url": "https://cdn.invalid/private", "xsec_token": "secret"},
                {"url": "https://www.douyin.com/video/123", "title": "重复"},
                {"url": "https://www.douyin.com.evil.test/video/456", "title": "其他域名"}]
        normalized = food.backend.normalize_items(rows, "douyin", 3)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["url"], "https://www.douyin.com/video/123")
        self.assertNotIn("secret", json.dumps(normalized))
        self.assertNotIn("cookie", normalized[0])

    def test_parameters_cannot_expand_scope(self):
        server = food.FoodServer()
        for name, args in [
            ("douyin_search", {"keyword": "宜昌", "limit": 50}),
            ("douyin_search", {"keyword": "宜昌", "limit": True}),
            ("douyin_search", {"keyword": "宜昌", "download": True}),
            ("dianping_search", {"keyword": "小吃"}),
            ("food_status", {"platform": "meituan"}),
            ("delete_cookies", {}),
        ]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                server.call(name, args)

    def test_detail_must_use_search_results_from_this_session(self):
        server = food.FoodServer()
        url = "https://www.dianping.com/shop/abc123"
        with self.assertRaises(ValueError):
            server.call("dianping_detail", {"url": url})
        with patch.object(food.backend, "dianping_search", return_value={"items": [{"url": url}]}):
            server.call("dianping_search", {"city": "宜昌", "keyword": "小吃"})
        with patch.object(food.backend, "dianping_detail", return_value={"item": {"url": url}, "missing_fields": ["address"]}):
            output = server.call("dianping_detail", {"url": url})
            self.assertEqual(output["status"], "partial")
            self.assertIn("checked_at", output)
        self.assertEqual(server.calls, 2)

    def test_challenge_stops_one_platform_without_retries(self):
        server = food.FoodServer()
        with patch.object(food.backend, "douyin_search", side_effect=food.backend.FoodError("risk_control", "停止")) as query:
            for _ in range(2):
                with self.assertRaises(food.backend.FoodError):
                    server.call("douyin_search", {"keyword": "宜昌小吃"})
            self.assertEqual(query.call_count, 1)
        with patch.object(food.backend, "dianping_search", return_value={"items": [{"url": "https://www.dianping.com/shop/123"}]}):
            self.assertEqual(server.call("dianping_search", {"city": "宜昌", "keyword": "小吃"})["platform"], "dianping")

    def test_browser_errors_do_not_leak_private_values(self):
        server = food.FoodServer()
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "douyin_search", "arguments": {"keyword": "小吃"}}}
        with patch.object(food.backend, "douyin_search", side_effect=RuntimeError("Cookie: private-value")):
            response = food.rpc(server, request)
        self.assertNotIn("private-value", json.dumps(response))
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(server.blocked["douyin"], "query_failed")

    def test_budget_and_empty_parser_result(self):
        server = food.FoodServer()
        server.calls = 20
        with patch.object(food.backend, "douyin_search") as query:
            with self.assertRaises(food.backend.FoodError) as error:
                server.call("douyin_search", {"keyword": "小吃"})
            self.assertEqual(error.exception.kind, "query_budget_exhausted")
            query.assert_not_called()
        with self.assertRaises(food.backend.FoodError) as error:
            food.backend.result("dianping", {"items": []})
        self.assertEqual(error.exception.kind, "layout_changed")

    def test_unknown_city_does_not_search_beijing(self):
        page = unittest.mock.MagicMock()
        page.evaluate.return_value = []
        with patch.object(food.backend, "browser") as session, patch.object(food.backend, "visit") as visit:
            session.return_value.__enter__.return_value = page
            with self.assertRaises(food.backend.FoodError) as error:
                food.backend.dianping_search("宜昌", "小吃", 3)
            self.assertEqual(error.exception.kind, "city_not_found")
            self.assertEqual(visit.call_count, 1)
            page.locator.assert_not_called()


@unittest.skipUnless(os.environ.get("TRAVEL_FOOD_BROWSER_TESTS") == "1" and importlib.util.find_spec("playwright"),
                     "opt-in Chrome fixture verification; no account or platform requests")
class FoodBrowserFixtureTest(unittest.TestCase):
    def test_food_walk_page_views_fallback_and_print(self):
        from playwright.sync_api import sync_playwright
        from test_food_walk import example, renderer
        with tempfile.TemporaryDirectory() as root, sync_playwright() as driver:
            path = Path(root) / "index.html"
            path.write_text(renderer.build(example()), encoding="utf-8")
            browser = driver.chromium.launch(channel="chrome", headless=True)
            try:
                for javascript in (True, False):
                    context = browser.new_context(java_script_enabled=javascript)
                    context.route("https://**", lambda route: route.abort())
                    page = context.new_page()
                    page.goto(path.as_uri())
                    for width in (375, 1440):
                        page.set_viewport_size({"width": width, "height": 900})
                        for view, label in (("overview", "全程概览"), ("detail", "每日行程"), ("prepare", "出发准备")):
                            if javascript:
                                page.get_by_role("tab", name=label, exact=True).click()
                            else:
                                page.locator(f'nav[data-fallback-view-switcher] label[for="itinerary-view-{view}"]').click()
                            self.assertTrue(page.locator(f"#{view}-view").is_visible())
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
                        if javascript:
                            page.get_by_role("tab", name="每日行程", exact=True).click()
                        else:
                            page.locator('nav[data-fallback-view-switcher] label[for="itinerary-view-detail"]').click()
                        self.assertTrue(page.locator(".event-food_walk").is_visible())
                    page.emulate_media(media="print")
                    self.assertTrue(page.locator(".portable-guide").is_visible())
                    self.assertIn("测试面食", page.locator(".portable-guide").inner_text())
                    context.close()
            finally:
                browser.close()

    def test_douyin_search_text_and_challenge_stop(self):
        from contextlib import contextmanager
        from playwright.sync_api import sync_playwright
        fixture = '''<input data-e2e="searchbar-input" onkeydown="if(event.key==='Enter')document.querySelector('#search-result-container').innerHTML=document.querySelector('template').innerHTML">
        <div id="search-result-container"></div><template><div data-e2e="search-video"><a href="/video/123?token=hidden">宜昌测试小吃</a><div data-e2e="search-card-user">@测试作者</div><div>2026-10-09</div></div></template>'''
        with sync_playwright() as driver, tempfile.TemporaryDirectory() as root:
            context = driver.chromium.launch_persistent_context(root, channel="chrome", headless=True)
            context.route("**/*", lambda route: route.fulfill(body=fixture, content_type="text/html; charset=utf-8"))
            page = context.pages[0]

            @contextmanager
            def session(*args, **kwargs):
                yield page

            try:
                with patch.object(food.backend, "browser", session):
                    server = food.FoodServer()
                    result = server.call("douyin_search", {"keyword": "宜昌小吃", "limit": 3})
                    self.assertEqual(result["items"][0]["author"], "@测试作者")
                    self.assertEqual(result["items"][0]["published_at_text"], "2026-10-09")
                    self.assertNotIn("hidden", json.dumps(result))
                    page.set_content('<iframe src="https://www.douyin.com/captcha"></iframe>')
                    with self.assertRaises(food.backend.FoodError) as error:
                        food.backend.check_page(page, "douyin")
                    self.assertEqual(error.exception.kind, "risk_control")
            finally:
                context.close()

    def test_city_search_and_shop_dom_without_json_ld(self):
        from contextlib import contextmanager
        from playwright.sync_api import sync_playwright

        fixtures = {
            "/citylist": '<a href="/beijing">北京</a><a href="/yichang">宜昌</a>',
            "/yichang": '<input id="J-search-input" onkeydown="if(event.key===\'Enter\')location.href=\'/search/keyword/fixture\'">',
            "/search/keyword/fixture": '<div class="shop-all-list"><ul><li><div class="tit"><a href="/shop/store1?token=hidden"><h4>宜昌小吃店</h4></a></div><div class="tag-addr">西陵区</div><div class="mean-price">人均30元</div></li></ul></div>',
            "/shop/store1": '<h1 class="shopName">宜昌小吃店</h1><div class="addressText">宜昌市西陵区测试路1号</div><span class="star-score">4.5</span><span class="reviews">200条评价</span><span class="price">人均30元</span>',
        }
        from urllib.parse import urlparse
        with sync_playwright() as driver, tempfile.TemporaryDirectory() as root:
            context = driver.chromium.launch_persistent_context(root, channel="chrome", headless=True)
            context.route("**/*", lambda route: route.fulfill(body=fixtures.get(urlparse(route.request.url).path, "missing"), content_type="text/html; charset=utf-8"))
            page = context.pages[0]

            @contextmanager
            def session(*args, **kwargs):
                yield page

            try:
                with patch.object(food.backend, "browser", session):
                    server = food.FoodServer()
                    result = server.call("dianping_search", {"city": "宜昌", "keyword": "小吃", "limit": 3})
                    self.assertEqual(result["query"]["city_url"], "https://www.dianping.com/yichang")
                    self.assertEqual(result["items"][0]["title"], "宜昌小吃店")
                    detail = server.call("dianping_detail", {"url": result["items"][0]["url"]})
                    self.assertEqual(detail["item"]["address"], "宜昌市西陵区测试路1号")
                    self.assertIsNone(detail["item"]["opening_hours"])
                    self.assertFalse(detail["reviews_available"])
                    self.assertNotIn("hidden", json.dumps(result))
            finally:
                context.close()


if __name__ == "__main__":
    unittest.main()
