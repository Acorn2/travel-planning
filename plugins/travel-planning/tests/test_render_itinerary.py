from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "travel-planning"
MODULE_PATH = SKILL_ROOT / "scripts" / "render_itinerary.py"
SPEC = importlib.util.spec_from_file_location("render_itinerary", MODULE_PATH)
assert SPEC and SPEC.loader
render_itinerary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render_itinerary)


def add_inventory_binding(data: dict, expires_at: str = "2099-01-01T10:30:00+08:00") -> str:
    snapshot_id = "0123456789abcdef01234567"
    data["planning"]["source_snapshots"] = [{
        "schema_version": "travel-source-snapshot/v1", "snapshot_id": snapshot_id,
        "snapshot_kind": "quote", "status": "platform_reported",
        "provider": {"id": "fliggy_flyai", "name": "飞猪 FlyAI"},
        "product_type": "train", "tool": "search-train", "query": {"dep_date": "2026-10-17"},
        "freshness": {"checked_at": "2026-09-21T10:00:00+08:00", "expires_at": expires_at, "dynamic": True},
        "items": [{
            "offer_id": "g1234-second", "name": "G1234",
            "price": {"amount": 73, "currency": "CNY", "display": "¥73", "basis": "per_adult"},
            "availability": {"status": "provider_returned", "remaining": None},
            "action_link": "https://example.com/train/g1234",
        }],
        "count": 1, "raw_response_hash": "sha256:" + "a" * 64,
        "source": {"title": "飞猪", "url": "https://example.com", "kind": "official_platform_api"},
        "disclaimer": "下单前复核",
    }]
    route = next(item for item in data["planning"]["transport_edges"] if item["id"] == "r1")
    route["inventory_refs"] = [{"snapshot_id": snapshot_id, "offer_id": "g1234-second", "role": "candidate_quote"}]
    return snapshot_id


class RenderItineraryTest(unittest.TestCase):
    def load_example(self) -> dict:
        return json.loads((SKILL_ROOT / "assets" / "example-itinerary.json").read_text(encoding="utf-8"))

    def test_event_prose_rejects_raw_objects_and_scalar_values(self) -> None:
        for field in ("details", "tips"):
            for value in ({"plan": "原始对象"}, [{"plan": "原始对象"}], "单段文字", [1], [None], [" "]):
                data = self.load_example()
                data["days"][0]["events"][0][field] = value
                with self.subTest(field=field, value=value):
                    for render in (render_itinerary.build, render_itinerary.render_portable_guide):
                        with self.assertRaisesRegex(ValueError, field):
                            render(data)
                    with self.assertRaisesRegex(ValueError, field):
                        render_itinerary.render_event(data["days"][0]["events"][0], 0)

    def test_event_prose_remains_readable_and_html_escaped(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][0]
        event.update(details=["执行说明 <script>"], tips=["现场提醒 & 复核"])
        for render in (render_itinerary.build, render_itinerary.render_portable_guide):
            html = render(data)
            self.assertIn("执行说明 &lt;script&gt;", html)
            self.assertIn("现场提醒 &amp; 复核", html)

    def test_cover_dates_are_compact_without_changing_source_data(self) -> None:
        self.assertEqual(render_itinerary.compact_date_range("2026-10-12 — 2026-10-16"), "2026.10.12—10.16")
        self.assertEqual(render_itinerary.compact_date_range("2026-12-30 — 2027-01-02"), "2026.12.30—2027.01.02")
        self.assertEqual(render_itinerary.compact_date_range("日期待定"), "日期待定")

    def test_uncredited_event_image_is_not_promoted_to_cover(self) -> None:
        self.assertEqual(render_itinerary.first_event_visual([{"events": [{
            "type": "attraction", "images": [{"url": "https://example.com/photo.jpg"}]
        }]}]), {})

    def test_default_handbook_summarizes_days_without_repeating_event_cards(self) -> None:
        data = self.load_example()
        html = render_itinerary.build(data)
        summary = html.split('<section class="handbook-overview"', 1)[1].split('<details class="reference-details">', 1)[0]
        self.assertEqual(summary.count('class="handbook-day"'), len(data["days"]))
        self.assertNotIn('class="event ', summary)
        self.assertNotIn('class="restaurant-candidate ', summary)
        self.assertIn('href="#day-1" data-day-target="day-1"', summary)
        self.assertIn('for="itinerary-view-detail"', summary)
        self.assertIn('id="full-overview"', html)
        self.assertEqual(html.count('id="overview-view"'), 1)

    def test_daily_disclosures_keep_all_events_but_expand_only_first_day(self) -> None:
        data = self.load_example()
        html = render_itinerary.build(data)
        days = re.findall(r'<details class="day" id="day-(\d+)"( open)?>', html)
        self.assertEqual(len(days), len(data["days"]))
        self.assertEqual([day for day, opened in days if opened], ["0"])
        self.assertEqual(len(re.findall(r'<article class="event event-', html)), sum(len(day["events"]) for day in data["days"]))

    def test_routine_compression_preserves_park_experiences_and_hard_requirements(self) -> None:
        self.assertTrue(render_itinerary.is_routine_event({"type": "rest", "title": "早餐与准备"}))
        self.assertFalse(render_itinerary.is_routine_event({"type": "rest", "title": "人民公园喝茶、慢走"}))
        self.assertFalse(render_itinerary.is_routine_event({"type": "note", "title": "机场值机安检", "booking_task_ids": ["hard-requirement"]}))
        html = render_itinerary.render_event({"type": "note", "title": "机场值机安检", "details": ["晚点则取消下午景点"], "time": "09:00"}, 1)
        self.assertIn('event-compact', html)
        self.assertIn('晚点则取消下午景点', html)

    def test_preparation_uses_deadlines_and_does_not_claim_research_is_booked(self) -> None:
        html = render_itinerary.render_preparation({
            "booking_tasks": [
                {"title": "较晚预约", "deadline": "2026-10-12T18:00:00+08:00", "priority": "book_now", "status": "to_recheck"},
                {"title": "较早预约", "deadline": "2026-10-09T18:00:00+08:00", "status": "to_recheck"},
            ],
            "readiness": [{"category": "lodging", "status": "verified", "summary": "已核对酒店位置"}],
        })
        self.assertLess(html.index("较早预约"), html.index("较晚预约"))
        self.assertIn("2026-10-09 18:00", html)
        self.assertIn("信息已核验", html)
        self.assertNotIn("已预订", html)
        self.assertNotIn("to_recheck", html)

    def test_restaurant_evidence_is_collapsed_without_losing_routes(self) -> None:
        data = self.load_example()
        meal = data["planning"]["meal_options"][0]
        html = render_itinerary.render_meal_candidates(
            meal,
            {item["id"]: item for item in data["planning"]["restaurants"]},
            {item["snapshot_id"]: item for item in data["planning"]["restaurant_snapshots"]},
            {item["id"]: item for item in data["planning"]["meal_route_evaluations"]},
        )
        self.assertIn('<details class="restaurant-more"><summary>', html)
        self.assertIn('class="restaurant-candidate recommended" open', html)
        self.assertNotIn('class="restaurant-candidate restaurant-candidate-backup" open', html)
        self.assertIn('灵隐片区上一站出口 → 灵隐杭帮面馆 A', html)
        self.assertEqual(render_itinerary.approximate_minutes(8.72), "约9分钟")

    def test_readiness_is_rendered(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("西湖经典线", html)
        self.assertNotIn("先研究，再排行程", html)
        self.assertNotIn("天气与复核", html)
        self.assertNotIn('<section class="overview">', html)
        self.assertNotIn("规划说明", html)

    def test_dates_and_times_render_inside_one_compact_timeline(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn('class="day-dot"', html)
        self.assertIn('class="event-body"><div class="time">', html)
        self.assertIn("D1 · 周六 · 2026-10-17", html)
        self.assertNotIn('<nav class="tabs"', html)
        self.assertNotIn("const tabs=", html)
        self.assertNotIn("dayObserver", html)
        self.assertIn(".event{position:relative;display:grid;grid-template-columns:24px minmax(0,1fr)", html)

    def test_page_can_switch_between_detail_and_dense_overview(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn('id="itinerary-view-controls"', html)
        self.assertIn('id="itinerary-view-focus" checked', html)
        self.assertIn('<label for="itinerary-view-detail">每日行程</label>', html)
        self.assertIn('<label for="itinerary-view-overview">全程概览</label>', html)
        self.assertIn('<label for="itinerary-view-prepare">出发准备</label>', html)
        self.assertIn('id="itinerary-tools"', html)
        self.assertNotIn('data-print-itinerary type="button">打印 / 保存 PDF</button>', html)
        self.assertIn('id="detail-view" data-itinerary-view="detail"', html)
        self.assertIn('id="overview-view" data-itinerary-view="overview"', html)
        self.assertIn('id="prepare-view" data-itinerary-view="prepare"', html)
        self.assertNotIn('data-itinerary-view="overview" hidden', html)
        self.assertNotIn('data-itinerary-view="routes" hidden', html)
        self.assertIn('[data-itinerary-view][hidden] *::before', html)
        self.assertIn('html[data-itinerary-view="routes"] .sources', html)
        self.assertIn('<th scope="col">时间</th><th scope="col">地点</th><th scope="col">关键信息</th>', html)
        self.assertIn('data-itinerary-enhanced', html)
        self.assertIn('role:`tablist`', html)
        self.assertIn('`ArrowLeft`,`ArrowRight`,`Home`,`End`', html)
        self.assertIn('#itinerary-view-overview:checked', html)
        self.assertNotIn('?.', html)

    def test_portable_pdf_keeps_every_event_without_research_cards(self) -> None:
        data = self.load_example()
        html = render_itinerary.render_portable_guide(data)
        self.assertEqual(html.count('class="portable-day"'), len(data["days"]))
        self.assertEqual(html.count('data-portable-event='), sum(len(day["events"]) for day in data["days"]))
        self.assertIn('id="portable-preparation"', html)
        self.assertNotIn('id="prepare-view"', html)
        self.assertNotIn('class="restaurant-candidate ', html)
        self.assertNotIn('class="inventory-proof"', html)
        self.assertNotIn('class="restaurant-signals"', html)

    def test_portable_pdf_preserves_entry_cutoffs_fallback_and_booking_deadline(self) -> None:
        data = {"days": [{"date": "2026-10-10", "events": [{
            "id": "museum", "type": "attraction", "title": "博物馆",
            "attraction_id": "museum", "booking_task_ids": ["ticket"],
            "admission": {"last_entry": "16:00", "entry_requirement": "带身份证", "notice": "公告冲突须复核"},
            "execution": {"entry": {"name": "东门", "physical_address": "东门路8号"}, "exit": {"name": "南门"}, "leave_by": "17:00", "fallback": "迟到则取消"},
            "preparation": ["准备预约码"],
            "map_url": "https://uri.amap.com/poidetail?poiid=ABC&src=travel",
        }]}], "planning": {"booking_tasks": [{"id": "ticket", "deadline": "10月9日18:00前"}]}}
        html = render_itinerary.render_portable_guide(data)
        for value in ["东门路8号", "东门", "南门", "16:00", "17:00", "带身份证", "准备预约码", "公告冲突须复核", "迟到则取消", "10月9日18:00前"]:
            self.assertIn(value, html)
        self.assertIn('data-print-map="博物馆"', html)
        self.assertIn('poiid=ABC&amp;src=travel', html)

    def test_portable_pdf_keeps_primary_restaurant_and_locatable_backup(self) -> None:
        data = self.load_example()
        meal = data["planning"]["meal_options"][0]
        restaurants = {r["id"]: r for r in data["planning"]["restaurants"]}
        html = render_itinerary.render_portable_guide(data)
        for candidate_id in [meal["selected_candidate_id"], *meal["fallback_candidate_ids"]]:
            restaurant = restaurants[candidate_id]
            self.assertIn(restaurant["name"], html)
            self.assertIn(restaurant["location"]["physical_address"], html)
        self.assertIn(meal["fallback_rule"], html)

    def test_portable_pdf_keeps_lodging_actions_and_required_ticket_total(self) -> None:
        data = {"days": [{"events": [
            {"id": "checkout", "type": "lodging", "title": "办理退房并寄存行李", "lodging_id": "hotel"},
            {"id": "ticket", "type": "attraction", "title": "入馆", "cost_summary": "成人基础票两人合计", "cost_items": [{"name": "成人门票", "quantity": 2, "subtotal": "¥100", "required": True}]},
        ]}], "planning": {"lodging_options": [{"id": "hotel", "name": "示例酒店", "location": {"physical_address": "示例路8号"}}]}}
        html = render_itinerary.render_portable_guide(data)
        for value in ["办理退房并寄存行李", "示例酒店", "示例路8号", "成人门票 ×2，合计 ¥100", "成人基础票两人合计"]:
            self.assertIn(value, html)

    def test_vue_frontend_is_inlined_into_one_progressively_enhanced_html(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertEqual(html.count("<script>"), 1)
        self.assertNotIn("<script src=", html)
        self.assertIn("html:not([data-itinerary-enhanced=true])", html)
        self.assertIn("Vue", html)
        self.assertNotIn("process.env", html)

    def test_pc_views_share_one_content_width(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("--page-width:1224px;--page-gutter:22px", html)
        self.assertIn(".hero-inner{width:100%;max-width:var(--page-width)", html)
        self.assertIn("main{width:100%;max-width:var(--page-width)!important", html)
        self.assertIn(".itinerary-overview{max-width:none!important}", html)
        self.assertIn("main{padding-left:10px;padding-right:10px}", html)

    def test_route_view_renders_ordered_stops_per_day(self) -> None:
        html = render_itinerary.build(self.load_example())
        start = html.index('<section class="itinerary-routes"')
        end = html.index('<details class="sources"', start)
        route_view = html[start:end]
        first_day = route_view.index("D1 · 周六 · 2026-10-17")
        second_day = route_view.index("D2 · 周日 · 2026-10-18")
        self.assertLess(first_day, second_day)
        self.assertIn("西湖经典线", route_view[first_day:second_day])
        self.assertIn("酒店—湖滨—断桥—灵隐片区", route_view[first_day:second_day])
        self.assertIn("龙翔桥附近酒店", route_view[first_day:second_day])
        self.assertIn("湖滨公园入口", route_view[first_day:second_day])
        self.assertIn("断桥东侧", route_view[first_day:second_day])
        self.assertIn("灵隐片区", route_view[first_day:second_day])
        self.assertIn("4 个停靠点", route_view[first_day:second_day])
        self.assertIn("龙井村—午餐—酒店片区", route_view[second_day:])
        self.assertIn("3 个停靠点", route_view[second_day:])
        self.assertEqual(route_view.count('class="route-stop-list"'), 2)
        self.assertNotIn("<iframe", route_view)
        self.assertNotIn("via%5B", route_view)
        self.assertNotIn('class="event ', route_view)
        self.assertNotIn('class="overview-table"', route_view)

    def test_route_view_has_clear_empty_state_for_a_day_without_daily_route(self) -> None:
        data = self.load_example()
        data["planning"]["daily_routes"] = data["planning"]["daily_routes"][:1]
        html = render_itinerary.render_route_overview(
            data["days"],
            data["planning"]["daily_routes"],
        )
        second_day = html.index("D2 · 周日 · 2026-10-18")
        self.assertIn("暂无停靠点", html[second_day:])
        self.assertIn("当天没有可展示的停靠顺序。", html[second_day:])

    def test_overview_adapts_facts_and_omits_heavy_detail_content(self) -> None:
        html = render_itinerary.build(self.load_example())
        start = html.index('<section class="itinerary-overview"')
        end = html.index('<section class="itinerary-routes"', start)
        overview = html[start:end]
        self.assertIn('<span class="overview-type">景点</span>', overview)
        self.assertIn('<dt>特色</dt><dd>秋季湖景、清晨光线</dd>', overview)
        self.assertIn('<dt>游览</dt><dd>09:00 湖滨公园湖岸 → 09:35 沿湖步行至断桥东侧</dd>', overview)
        self.assertIn('<span class="overview-type">交通</span>', overview)
        self.assertIn('<dt>门到门</dt><dd>约 10–20 分钟</dd>', overview)
        self.assertIn('<span class="overview-type">餐饮</span>', overview)
        self.assertIn('<dt>特色</dt><dd>片儿川、小笼</dd>', overview)
        self.assertNotIn('<img', overview)
        self.assertNotIn('class="route-map"', overview)
        self.assertNotIn('class="action-link"', overview)

    def test_lodging_overview_uses_hotel_execution_fields(self) -> None:
        html = render_itinerary.render_overview_event(
            {"type": "lodging", "time": "15:00", "title": "办理入住", "duration": "2晚"},
            None,
            None,
            None,
            {},
            {},
            {},
            {
                "name": "西湖湖滨酒店",
                "area": "龙翔桥",
                "requested_occupancy": {"adults": 4, "rooms": 2, "bed_type": "双床"},
                "nights": 2,
                "price": "约¥980/间夜",
                "luggage_storage": "可寄存",
                "next_stop_duration": "步行10分钟",
            },
        )
        self.assertIn("西湖湖滨酒店", html)
        self.assertIn("4人 · 2间 · 双床 · 2晚", html)
        self.assertIn('<dt>价格</dt><dd>约¥980/间夜</dd>', html)
        self.assertIn('<dt>行李</dt><dd>可寄存</dd>', html)

    def test_bound_inventory_snapshot_is_validated_and_rendered_in_event(self) -> None:
        data = self.load_example()
        add_inventory_binding(data)
        html = render_itinerary.build(data)
        self.assertIn("报价与核验依据", html)
        self.assertIn("飞猪 FlyAI · G1234", html)
        self.assertIn("查看供应商结果", html)

    def test_inventory_binding_rejects_offer_from_another_snapshot(self) -> None:
        data = self.load_example()
        add_inventory_binding(data)
        route = next(item for item in data["planning"]["transport_edges"] if item["id"] == "r1")
        route["inventory_refs"][0]["offer_id"] = "not-in-snapshot"
        with self.assertRaisesRegex(ValueError, "offer_id 不属于引用快照"):
            render_itinerary.validate_data(data)

    def test_to_recheck_readiness_requires_action_link(self) -> None:
        data = self.load_example()
        data["planning"]["readiness"][0]["action_links"] = []
        with self.assertRaisesRegex(ValueError, "必须提供 action_links"):
            render_itinerary.validate_data(data)

    def test_rejects_non_https_action_anywhere(self) -> None:
        data = self.load_example()
        data["planning"]["booking_tasks"][0]["action_links"] = [
            {"label": "bad", "url": "http://example.com"}
        ]
        with self.assertRaisesRegex(ValueError, "必须使用 HTTPS"):
            render_itinerary.validate_data(data)

    def test_attraction_costs_render_inside_event(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][1]
        event["admission"]["opening_hours"] = "08:00–18:00"
        event["admission"]["last_entry"] = "17:30"
        event["cost_items"] = [{
            "name": "基础门票",
            "kind": "base_ticket",
            "unit_price": "80元/人",
            "quantity": "2人",
            "subtotal": "160元/2人",
            "pricing_role": "baseline",
            "required": True,
            "status": "official_confirmed",
            "source_ids": ["s1"],
        }]
        event["cost_summary"] = "基线160元/2人"
        html = render_itinerary.build(data)
        self.assertIn("票价与费用", html)
        self.assertIn("160元/2人", html)
        self.assertIn("08:00–18:00", html)
        self.assertIn("17:30", html)

    def test_admission_panel_merges_booking_actions_and_ticket_costs(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][1]
        attraction = data["planning"]["attractions"][0]
        attraction["official"]["wechat"] = {
            "account_name": "杭州西湖风景名胜区",
            "menu_path": "景区服务 → 预约服务",
            "guide_url": "https://mp.weixin.qq.com/s/example-reservation-guide",
            "checked_at": "2026-09-20",
        }
        booking_task = {
            "title": "核验并完成景点预约",
            "product": "2位成人实名预约",
            "quantity": "2人",
            "deadline": "出发前完成",
            "status": "action_required_now",
            "action": "通过官方渠道完成预约",
            "action_links": [{
                "type": "official_wechat",
                "label": "公众号预约",
                "provider": "杭州西湖风景名胜区",
                "url": "https://mp.weixin.qq.com/s/example-reservation-guide",
            }],
        }
        panel = render_itinerary.render_admission_panel(event, [booking_task], attraction)
        self.assertIn('class="admission-panel"', panel)
        self.assertIn('class="admission-booking"', panel)
        self.assertIn('class="admission-cost"', panel)
        self.assertIn("预约行动", panel)
        self.assertIn("票价与费用", panel)
        self.assertNotIn("预约与抢票", panel)
        self.assertNotIn('class="booking-callout"', panel)
        self.assertNotIn('class="cost-breakdown"', panel)
        self.assertEqual(panel.count("公众号预约 ↗"), 1)
        self.assertLess(panel.index("预约行动"), panel.index("票价与费用"))

    def test_attraction_does_not_repeat_booking_or_cost_after_admission_panel(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertNotIn('class="booking-callout"', html)
        self.assertNotIn("预约与抢票", html)
        self.assertNotIn('class="cost-breakdown"', html)
        self.assertIn('class="admission-cost"', html)

    def test_shared_booking_and_notice_url_keeps_booking_action_visible(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][1]
        attraction = data["planning"]["attractions"][0]
        shared_url = "https://example.com/official-booking-guide"
        attraction["official"]["booking_url"] = shared_url
        attraction["official"]["notice_url"] = shared_url
        panel = render_itinerary.render_admission_panel(event, [], attraction)
        self.assertIn("官方预约与公告 ↗", panel)
        self.assertNotIn("临时公告 ↗", panel)
        self.assertEqual(panel.count(shared_url), 1)

    def test_confirmed_attraction_requires_base_ticket_in_event(self) -> None:
        data = self.load_example()
        attraction_event = data["days"][0]["events"][1]
        attraction_event.pop("cost_items", None)
        with self.assertRaisesRegex(ValueError, "基础门票 cost_items"):
            render_itinerary.validate_data(data)

    def test_event_facts_are_labeled_instead_of_rendered_as_chips(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("建议游览时长", html)
        self.assertIn("所在区域", html)
        self.assertIn("开放与预约", html)
        self.assertIn("预约方式", html)
        self.assertNotIn('class="chip"', html)
        self.assertNotIn('class="chips"', html)

    def test_attraction_renders_checkpoints_and_not_report_reason(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("景区内怎么玩", html)
        self.assertIn("湖滨公园湖岸", html)
        self.assertIn("湖滨公园入口", html)
        self.assertIn("10:30 前离开", html)
        self.assertIn("断桥东侧", html)
        self.assertNotIn('class="route-endpoints"', html)
        self.assertIn("现场看点", html)
        self.assertNotIn("安排理由", html)

    def test_legacy_generic_checkpoint_narration_is_not_presented_as_research(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][1]
        event["execution"]["checkpoints"][0]["narration"] = (
            "先完成核心点，再根据排队、天气和体力决定是否停留。"
        )
        html = render_itinerary.render_checkpoints(event, {}, {}, {}, {})
        self.assertNotIn("先完成核心点", html)

    def test_entry_and_exit_render_inside_first_and_last_checkpoint(self) -> None:
        event = self.load_example()["days"][0]["events"][1]
        html = render_itinerary.render_checkpoints(event, {}, {}, {}, {})
        first_checkpoint = html.index("cp-d1-lakefront") if "cp-d1-lakefront" in html else html.index("湖滨公园湖岸")
        last_checkpoint = html.index("沿湖步行至断桥东侧")
        self.assertLess(html.index("湖滨公园入口"), first_checkpoint)
        self.assertGreater(html.index("10:30 前离开"), last_checkpoint)
        self.assertNotIn('class="route-endpoints"', html)

    def test_weather_is_a_corner_link_not_a_fact_row(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn('class="weather-badge"', html)
        self.assertIn("查看杭州西湖天气", html)
        self.assertIn("https://www.weather.com.cn/weather/101210101.shtml", html)
        self.assertNotIn("<dt>天气影响</dt>", html)

    def test_checkpoint_can_embed_meal_research(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertNotIn('class="checkpoint-meal"', html)
        self.assertIn("龙井村茶歇点", html)
        self.assertIn("龙井茶、桂花糕", html)

    def test_confirmed_attraction_requires_executable_checkpoints(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][1].pop("execution")
        with self.assertRaisesRegex(ValueError, "execution.entry"):
            render_itinerary.validate_data(data)

    def test_confirmed_attraction_requires_structured_admission(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][1]["admission"].pop("last_entry")
        with self.assertRaisesRegex(ValueError, "admission 缺少字段"):
            render_itinerary.validate_data(data)

    def test_confirmed_attraction_requires_separate_official_endpoints(self) -> None:
        data = self.load_example()
        data["planning"]["attractions"][0]["official"].pop("notice_url")
        with self.assertRaisesRegex(ValueError, "official 缺少字段"):
            render_itinerary.validate_data(data)

    def test_admission_panel_renders_official_notice_link(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("景区官网", html)
        self.assertIn("临时公告", html)
        self.assertIn("出发前准备", html)

    def test_admission_panel_renders_wechat_link_and_copy_fallback(self) -> None:
        data = self.load_example()
        data["planning"]["attractions"][0]["official"]["wechat"] = {
            "account_name": "杭州西湖风景名胜区",
            "menu_path": "景区服务 → 预约服务",
            "checked_at": "2026-09-20",
        }
        data["planning"]["attractions"][1]["official"]["wechat"] = {
            "account_name": "杭州西湖风景名胜区",
            "menu_path": "景区服务 → 预约服务",
            "guide_url": "https://mp.weixin.qq.com/s/example-reservation-guide",
            "checked_at": "2026-09-20",
        }
        html = render_itinerary.build(data)
        self.assertIn("公众号预约 ↗", html)
        self.assertIn("https://mp.weixin.qq.com/s/example-reservation-guide", html)
        self.assertIn("复制公众号名称", html)
        self.assertIn('data-wechat-account="杭州西湖风景名胜区"', html)
        self.assertIn("navigator.clipboard", html)

    def test_wechat_guide_must_use_official_wechat_domain(self) -> None:
        data = self.load_example()
        data["planning"]["attractions"][0]["official"]["wechat"] = {
            "account_name": "杭州西湖风景名胜区",
            "menu_path": "景区服务 → 预约服务",
            "guide_url": "https://example.com/fake",
            "checked_at": "2026-09-20",
        }
        with self.assertRaisesRegex(ValueError, "mp.weixin.qq.com"):
            render_itinerary.validate_data(data)

    def test_wechat_metadata_requires_account_menu_and_check_time(self) -> None:
        data = self.load_example()
        data["planning"]["attractions"][0]["official"]["wechat"] = {
            "account_name": "杭州西湖风景名胜区",
            "checked_at": "2026-09-20",
        }
        with self.assertRaisesRegex(ValueError, "official.wechat 缺少字段"):
            render_itinerary.validate_data(data)

    def test_checkpoint_requires_internal_time_range(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][1]["execution"]["checkpoints"][0].pop("end_time")
        with self.assertRaisesRegex(ValueError, r"checkpoint\[0\] 缺少字段"):
            render_itinerary.validate_data(data)

    def test_weather_requires_viewable_weather_link(self) -> None:
        data = self.load_example()
        data["planning"]["weather"][0]["action_links"][0]["type"] = "source"
        with self.assertRaisesRegex(ValueError, "weather 类型的查看入口"):
            render_itinerary.validate_data(data)

    def test_meal_event_goes_directly_to_candidate_cards(self) -> None:
        html = render_itinerary.build(self.load_example())
        timeline_html = html.split('<section class="itinerary-overview"', 1)[0]
        self.assertNotIn("<dt>用餐时间</dt>", timeline_html)
        self.assertNotIn("<dt>去哪里</dt>", timeline_html)
        self.assertNotIn("<dt>吃什么</dt>", timeline_html)
        self.assertNotIn("<dt>人均</dt>", timeline_html)
        self.assertNotIn("<dt>营业时间</dt>", timeline_html)
        self.assertNotIn("<dt>为什么顺路</dt>", timeline_html)
        self.assertIn("片儿川、小笼", timeline_html)
        self.assertIn("餐厅推荐", timeline_html)
        self.assertIn("综合推荐", timeline_html)
        self.assertIn("主选候位超过20分钟时切换餐厅 B", html)
        self.assertNotIn("主选评分样本更多，额外绕行4分钟", timeline_html)

    def test_meal_contract_omits_candidate_summary_duplicates(self) -> None:
        duplicate_fields = {
            "location", "signature_dishes", "per_person", "opening_hours",
            "queue_note", "why_here", "fallback",
        }
        for meal in self.load_example()["planning"]["meal_options"]:
            self.assertTrue(duplicate_fields.isdisjoint(meal))

    def test_formal_meal_rejects_candidate_summary_duplicates(self) -> None:
        data = self.load_example()
        data["planning"]["meal_options"][0]["why_here"] = "重复的顺路摘要"
        with self.assertRaisesRegex(ValueError, "不得复制候选摘要字段"):
            render_itinerary.validate_data(data)

    def test_advance_plan_hides_current_status_and_queue_noise(self) -> None:
        data = self.load_example()
        data["planning"]["transport_edges"][0]["live_status"] = "唯一实时拥堵状态"
        operations = data["planning"]["restaurant_snapshots"][0]["operations"]
        operations["queue"] = "唯一候位状态"
        operations["reservation"] = "电话预约"
        html = render_itinerary.build(data)
        self.assertNotIn("唯一实时拥堵状态", html)
        self.assertNotIn("唯一候位状态", html)
        self.assertNotIn("<dt>实时状态</dt>", html)
        self.assertNotIn("<dt>排队/预约</dt>", html)
        self.assertIn("<dt>预约方式</dt><dd>电话预约</dd>", html)

    def test_meal_recommendation_shows_sources_routes_and_original_posts(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("餐厅推荐", html)
        self.assertIn("灵隐杭帮面馆 A", html)
        self.assertIn("灵隐杭帮面馆 B", html)
        self.assertIn("综合推荐", html)
        self.assertNotIn('class="restaurant-backup-strip"', html)
        self.assertNotIn('class="restaurant-backup-link"', html)
        self.assertIn('<div class="restaurant-backup-carousel" aria-label="备选餐厅，展开查看">', html)
        self.assertIn('<details class="restaurant-candidate restaurant-candidate-backup">', html)
        self.assertIn('<span class="restaurant-role">备选</span><div><h4>灵隐杭帮面馆 B</h4>', html)
        self.assertIn("4.6/5 · 820条评价", html)
        self.assertIn("<b>小红书推荐 · 3 篇近期门店原帖</b>", html)
        self.assertIn("<dt>额外绕行</dt><dd>约4分钟</dd>", html)
        self.assertIn("灵隐片区上一站出口 → 灵隐杭帮面馆 A", html)
        self.assertIn("灵隐杭帮面馆 A → 下午路线入口", html)
        self.assertIn("浙江省杭州市西湖区灵隐路灵隐片区公交站出口 → 浙江省杭州市西湖区灵隐路88号A座", html)
        self.assertIn("浙江省杭州市西湖区灵隐路88号A座 → 浙江省杭州市西湖区灵隐路117号下午路线入口", html)
        self.assertIn('class="restaurant-route-link"', html)
        self.assertIn('title="在高德查看路线"><strong>灵隐片区上一站出口 → 灵隐杭帮面馆 A ↗</strong>', html)
        self.assertEqual(html.count("灵隐片区上一站出口 → 灵隐杭帮面馆 A"), 1)
        self.assertEqual(html.count("灵隐杭帮面馆 A → 下午路线入口"), 1)
        self.assertNotIn("<dt>从上一站</dt>", html)
        self.assertNotIn("<dt>去下一站</dt>", html)
        self.assertIn("查看灵隐杭帮面馆 A 官方相册", html)
        self.assertIn("https://www.xiaohongshu.com/explore/demo-lingyin-a-1", html)
        self.assertIn("示例作者甲", html)
        self.assertEqual(html.count("https://www.xiaohongshu.com/explore/demo-lingyin-a-1"), 1)
        self.assertIn('class="restaurant-candidate recommended" open', html)
        self.assertIn("restaurant-candidate-backup", html)
        self.assertIn('<summary class="restaurant-candidate-head">', html)
        self.assertIn('class="restaurant-toggle"', html)

    def test_restaurant_page_only_exposes_recommendation_and_nearby_search(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("综合推荐", html)
        self.assertIn("在高德查看灵隐片区上一站出口附近餐厅", html)
        self.assertIn("https://ditu.amap.com/search?", html)
        lunch_section = html.split('data-meal-id="m1"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn('<div class="restaurant-comparison-head"><span class="section-label">餐厅推荐</span><div class="actions">', lunch_section)
        self.assertIn('在高德查看门店 ↗</a><a class="action-link"', lunch_section)
        self.assertIn('class="restaurant-candidate restaurant-candidate-backup"', lunch_section)
        self.assertIn('class="restaurant-backup-carousel"', lunch_section)
        self.assertNotIn('class="restaurant-backup-strip"', lunch_section)
        self.assertNotIn("查看灵隐片区餐厅候选", html)
        self.assertNotIn('data-restaurant-sort="rank"', html)
        self.assertNotIn("路线最优", html)
        self.assertNotIn("附近可选", html)
        self.assertNotIn("comparators=", html)
        self.assertNotIn("data-restaurant-select", html)
        self.assertNotIn("已选为本餐", html)
        self.assertNotIn("选择这家", html)

    def test_verified_amap_poi_gets_one_detail_link_and_routes_handle_navigation(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn("https://uri.amap.com/poidetail?", html)
        self.assertIn("poiid=demo-lingyin-a", html)
        self.assertIn("在高德查看门店", html)
        self.assertNotIn("在高德导航到店", html)
        self.assertNotIn("coordinate=gaode", html)

    def test_restaurant_card_deduplicates_amap_aliases_and_event_link(self) -> None:
        data = self.load_example()
        restaurant = data["planning"]["restaurants"][0]
        snapshot = data["planning"]["restaurant_snapshots"][0]
        meal = data["planning"]["meal_options"][0]
        restaurant["action_links"].append({
            "type": "restaurant", "label": "高德门店别名", "provider": "高德地图",
            "url": "https://uri.amap.com/poidetail?id=demo-lingyin-a",
        })
        snapshot["action_links"].append({
            "type": "map", "label": "高德导航别名", "provider": "高德地图",
            "url": "https://uri.amap.com/navigation?to=120.102,30.240",
        })
        meal["action_links"] = [{
            "type": "restaurant", "label": "餐饮事件门店链接", "provider": "高德地图",
            "url": "https://uri.amap.com/poidetail?id=demo-lingyin-a&src=event",
        }]
        html = render_itinerary.build(data)
        self.assertNotIn("高德门店别名", html)
        self.assertNotIn("高德导航别名", html)
        self.assertNotIn("餐饮事件门店链接", html)
        self.assertIn("在高德查看门店", html)

    def test_embedded_checkpoint_meal_renders_full_candidate(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertNotIn('class="checkpoint-meal"', html)
        self.assertIn("龙井村茶歇点", html)
        self.assertIn("<dt>额外绕行</dt><dd>约1分钟</dd>", html)
        self.assertIn("小红书门店搜索", html)
        self.assertIn("在小红书搜索龙井村茶歇", html)

    def test_normal_meal_requires_two_to_three_candidates(self) -> None:
        data = self.load_example()
        meal = data["planning"]["meal_options"][0]
        meal["candidate_ids"] = [meal["candidate_ids"][0]]
        with self.assertRaisesRegex(ValueError, "2至3个餐厅候选"):
            render_itinerary.validate_data(data)

    def test_every_candidate_must_fit_detour_constraint(self) -> None:
        data = self.load_example()
        evaluation = data["planning"]["meal_route_evaluations"][1]
        evaluation["total_door_to_door_minutes"] = 34
        evaluation["detour_minutes"] = 16
        evaluation["to_next"]["door_to_door_minutes"] = 23
        with self.assertRaisesRegex(ValueError, "超过本餐最大允许绕行"):
            render_itinerary.validate_data(data)

    def test_route_detour_must_use_same_baseline(self) -> None:
        data = self.load_example()
        data["planning"]["meal_route_evaluations"][0]["detour_minutes"] = 7
        with self.assertRaisesRegex(ValueError, "额外绕行必须等于"):
            render_itinerary.validate_data(data)

    def test_platform_signal_keeps_rating_when_review_count_is_missing(self) -> None:
        data = self.load_example()
        signal = data["planning"]["restaurant_snapshots"][0]["platform_signals"][0]
        signal["review_count"] = None
        html = render_itinerary.build(data)
        self.assertIn("4.6/5 · 评价量未取得", html)

    def test_available_platform_signal_still_requires_rating_value(self) -> None:
        data = self.load_example()
        signal = data["planning"]["restaurant_snapshots"][0]["platform_signals"][0]
        signal["rating"] = None
        with self.assertRaisesRegex(ValueError, "必须提供评分展示值"):
            render_itinerary.validate_data(data)

    def test_restaurant_community_consensus_requires_original_note_links(self) -> None:
        data = self.load_example()
        community = data["planning"]["restaurant_snapshots"][0]["community_consensus"]
        community["references"] = community["references"][:1]
        with self.assertRaisesRegex(ValueError, "社区统计与原帖明细不一致|至少3条近期社区参考及原帖链接"):
            render_itinerary.validate_data(data)

    def test_restaurant_media_requires_original_source(self) -> None:
        data = self.load_example()
        data["planning"]["restaurants"][0]["media"][0].pop("source_url")
        with self.assertRaisesRegex(ValueError, "链接预览.*缺少来源"):
            render_itinerary.validate_data(data)

    def test_embedded_meal_is_subject_to_v3_route_gate(self) -> None:
        data = self.load_example()
        data["planning"]["meal_route_evaluations"][-1].pop("baseline_door_to_door_minutes")
        with self.assertRaisesRegex(ValueError, "缺少可比较的数值路线耗时"):
            render_itinerary.validate_data(data)

    def test_meal_anchor_requires_specific_address(self) -> None:
        data = self.load_example()
        data["planning"]["meal_options"][0]["previous_anchor"].pop("physical_address")
        with self.assertRaisesRegex(ValueError, "名称、具体地址和坐标"):
            render_itinerary.validate_data(data)

    def test_route_endpoint_requires_specific_address(self) -> None:
        data = self.load_example()
        data["planning"]["meal_route_evaluations"][0]["from_previous"]["origin"].pop("physical_address")
        with self.assertRaisesRegex(ValueError, "端点必须提供名称、具体地址和坐标"):
            render_itinerary.validate_data(data)

    def test_restaurant_rejects_vague_location_and_fake_poi(self) -> None:
        data = self.load_example()
        location = data["planning"]["restaurants"][0]["location"]
        location.update({"physical_address": "景区附近", "coordinates": "附近", "poi_id": "unknown"})
        with self.assertRaisesRegex(ValueError, "坐标.*lng,lat|完整门牌地址|真实平台 POI"):
            render_itinerary.validate_data(data)

    def test_restaurant_candidates_cannot_duplicate_same_poi(self) -> None:
        data = self.load_example()
        data["planning"]["restaurants"][1]["location"]["poi_id"] = data["planning"]["restaurants"][0]["location"]["poi_id"]
        with self.assertRaisesRegex(ValueError, "重复餐厅实体"):
            render_itinerary.validate_data(data)

    def test_restaurant_poi_must_match_map_source_evidence(self) -> None:
        data = self.load_example()
        data["planning"]["restaurants"][0]["location"]["poi_id"] = "FAKE-POI-123"
        with self.assertRaisesRegex(ValueError, "POI 与地图来源证据不一致"):
            render_itinerary.validate_data(data)

    def test_meal_requires_numeric_max_detour(self) -> None:
        data = self.load_example()
        data["planning"]["meal_options"][0]["constraints"].pop("max_detour_minutes")
        with self.assertRaisesRegex(ValueError, "数值 max_detour_minutes"):
            render_itinerary.validate_data(data)

    def test_unavailable_community_requires_search_and_manual_recheck(self) -> None:
        data = self.load_example()
        data["planning"]["restaurant_snapshots"][-1]["community_consensus"].pop("query_runs")
        with self.assertRaisesRegex(ValueError, "社区不可用时必须记录查询"):
            render_itinerary.validate_data(data)

    def test_community_reference_requires_recent_authored_original(self) -> None:
        data = self.load_example()
        reference = data["planning"]["restaurant_snapshots"][0]["community_consensus"]["references"][0]
        reference.pop("author")
        with self.assertRaisesRegex(ValueError, "社区原帖缺少 ID、作者"):
            render_itinerary.validate_data(data)

    def test_meal_route_must_reference_shared_baseline(self) -> None:
        data = self.load_example()
        data["planning"]["meal_route_evaluations"][0]["baseline_route_id"] = "made-up-baseline"
        with self.assertRaisesRegex(ValueError, "统一 baseline route"):
            render_itinerary.validate_data(data)

    def test_restaurant_sources_must_exist_in_registry(self) -> None:
        data = self.load_example()
        data["planning"]["restaurants"][0]["source_ids"] = ["missing-source"]
        with self.assertRaisesRegex(ValueError, "不存在的 source_ids"):
            render_itinerary.validate_data(data)

    def test_embedded_meal_rejects_unverified_platform_signal(self) -> None:
        data = self.load_example()
        snapshot = data["planning"]["restaurant_snapshots"][-1]
        snapshot["platform_signals"][0]["status"] = "unavailable"
        snapshot["platform_signals"][0].pop("unavailable_reason", None)
        with self.assertRaisesRegex(ValueError, "景点节点.*未取得评分时必须说明原因"):
            render_itinerary.validate_data(data)

    def test_embedded_meal_snapshot_must_match_trip_date(self) -> None:
        data = self.load_example()
        data["planning"]["restaurant_snapshots"][-1]["applicable_date"] = "2026-10-19"
        with self.assertRaisesRegex(ValueError, "景点节点.*动态快照日期或用餐时段不一致"):
            render_itinerary.validate_data(data)

    def test_confirmed_meal_requires_research_reference(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][3].pop("meal_id")
        with self.assertRaisesRegex(ValueError, "必须引用有效的 meal_id"):
            render_itinerary.validate_data(data)

    def test_required_booking_is_bound_to_attraction_event(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][1]
        event["reservation_required"] = True
        data["planning"]["attractions"][0]["official"]["booking_url"] = "https://westlake.hangzhou.gov.cn/"
        with self.assertRaisesRegex(ValueError, "必须提供 booking_task_ids"):
            render_itinerary.validate_data(data)

    def test_required_booking_accepts_official_channel_guidance(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][1]
        event["reservation_required"] = True
        official = data["planning"]["attractions"][0]["official"]
        official["booking_url"] = None
        official["booking_status"] = "official_channel_listed"
        event["booking_task_ids"] = ["b1"]
        data["planning"]["booking_tasks"][0]["event_id"] = event["id"]
        data["planning"]["booking_tasks"][0]["attraction_id"] = event["attraction_id"]
        render_itinerary.validate_data(data)

    def test_required_booking_accepts_verified_wechat_guide(self) -> None:
        data = self.load_example()
        event = data["days"][0]["events"][1]
        event["reservation_required"] = True
        official = data["planning"]["attractions"][0]["official"]
        official["booking_url"] = None
        official["booking_status"] = None
        official["wechat"] = {
            "account_name": "杭州西湖风景名胜区",
            "menu_path": "景区服务 → 预约服务",
            "guide_url": "https://mp.weixin.qq.com/s/verified-guide",
            "checked_at": "2026-09-20",
        }
        event["booking_task_ids"] = ["b1"]
        data["planning"]["booking_tasks"][0]["event_id"] = event["id"]
        data["planning"]["booking_tasks"][0]["attraction_id"] = event["attraction_id"]
        render_itinerary.validate_data(data)

    def test_attraction_legacy_details_do_not_duplicate_checkpoint_timeline(self) -> None:
        data = self.load_example()
        attraction_event = data["days"][0]["events"][1]
        attraction_event["details"] = ["唯一执行细节"]
        attraction_event["tips"] = ["唯一注意事项"]
        html = render_itinerary.build(data)
        self.assertNotIn("唯一执行细节", html)
        self.assertNotIn("唯一注意事项", html)
        self.assertIn("景区内怎么玩", html)

    def test_missing_event_images_do_not_create_fake_placeholders(self) -> None:
        data = self.load_example()
        for day in data["days"]:
            for event in day["events"]:
                event.pop("images", None)
        html = render_itinerary.build(data)
        self.assertNotIn("image-placeholder", html)
        self.assertNotIn("图片占位", html)

    def test_real_event_image_is_rendered_when_present(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][1]["images"] = [{
            "url": "https://example.com/west-lake.jpg",
            "alt": "西湖实景",
            "source_url": "https://commons.wikimedia.org/wiki/File:West_Lake.jpg",
            "source_label": "Wikimedia Commons",
            "author": "示例作者",
            "license": "CC BY 4.0",
        }]
        html = render_itinerary.build(data)
        self.assertIn('src="https://example.com/west-lake.jpg"', html)
        self.assertIn('alt="西湖实景"', html)
        self.assertIn("Wikimedia Commons · 示例作者 · CC BY 4.0", html)

    def test_reviewed_visual_manifest_embeds_artwork_in_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cover.jpg").write_bytes(b"\xff\xd8\xff\xd9")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"cover": {
                "path": "cover.jpg", "alt": "山水插画", "credit": "AI 生成，非实景"
            }}), encoding="utf-8")
            html = render_itinerary.build(self.load_example(), render_itinerary.load_visual_manifest(manifest))
        self.assertIn('href="data:image/jpeg;base64,/9j/2Q=="', html)
        self.assertIn("AI 生成，非实景", html)

    def test_visual_manifest_rejects_parent_directory_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "secret.jpg").write_bytes(b"private")
            visuals = root / "visuals"
            visuals.mkdir()
            manifest = visuals / "manifest.json"
            manifest.write_text(json.dumps({"cover": {
                "path": "../secret.jpg", "alt": "山水", "credit": "测试来源"
            }}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "视觉素材必须位于清单目录内"):
                render_itinerary.load_visual_manifest(manifest)

    def test_visual_manifest_requires_visible_credit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cover.jpg").write_bytes(b"\xff\xd8\xff\xd9")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"cover": {
                "path": "cover.jpg", "alt": "山水插画"
            }}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "来源说明"):
                render_itinerary.load_visual_manifest(manifest)

    def test_checkpoint_image_is_rendered_next_to_its_stop(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][1]["execution"]["checkpoints"][0]["images"] = [{
            "url": "https://example.com/lakefront.jpg",
            "alt": "湖滨公园湖岸示意",
            "source_url": "https://commons.wikimedia.org/",
            "source_label": "Wikimedia Commons",
            "author": "示例作者",
            "license": "CC BY 4.0",
        }]
        html = render_itinerary.build(data)
        self.assertIn('class="checkpoint-images"', html)
        self.assertIn("湖滨公园湖岸示意", html)

    def test_xiaohongshu_reference_renders_as_link_card_not_hotlinked_image(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][1]["community_refs"] = [{
            "title": "西湖近期体验",
            "author": "旅行者",
            "source_url": "https://www.xiaohongshu.com/explore/example-note",
            "interactions": {"likes": "12", "comments": "3", "collections": "8"},
            "reason": "用于判断周末拥挤程度",
        }]
        html = render_itinerary.build(data)
        self.assertIn("小红书近期体验参考", html)
        self.assertIn("西湖近期体验", html)
        self.assertIn("https://www.xiaohongshu.com/explore/example-note", html)
        self.assertNotIn("xhscdn.com", html)

    def test_fact_grid_allows_long_machine_values_to_wrap_on_mobile(self) -> None:
        html = render_itinerary.build(self.load_example())
        self.assertIn(
            ".event-facts>div{grid-template-columns:92px minmax(0,1fr)}",
            html,
        )
        self.assertIn(
            ".event-facts dd{min-width:0;overflow-wrap:anywhere}",
            html,
        )

    def test_transport_handoff_uses_exact_poi_without_embedding_navigation(self) -> None:
        data = self.load_example()
        route = data["planning"]["transport_edges"][0]
        route["destination"] = {
            "name": "武当山游客中心地上停车场",
            "physical_address": "湖北省十堰市丹江口市武当山游客中心",
            "poi_id": "B000A1",
        }
        route["map_route"]["destination_id"] = "B000A1"
        route["action_links"] = [{
            "type": "map", "label": "在高德查看完整路线", "provider": "高德地图",
            "url": "https://uri.amap.com/navigation?to=120.2,30.3,end&mode=walk",
        }]
        html = render_itinerary.build(data)
        self.assertIn('data-copy-destination="武当山游客中心地上停车场"', html)
        self.assertIn('https://uri.amap.com/poidetail?poiid=B000A1', html)
        self.assertEqual(html.count('https://uri.amap.com/poidetail?poiid=B000A1'), 1)
        self.assertIn("在高德打开地点", html)
        self.assertNotIn("<iframe", html)
        self.assertNotIn("route-map-fullscreen", html)
        self.assertNotIn("new IntersectionObserver", html)
        self.assertNotIn("在高德查看完整路线", html)

    def test_transport_handoff_falls_back_to_existing_map_link(self) -> None:
        route = self.load_example()["planning"]["transport_edges"][0]
        route["map_route"] = {}
        route["action_links"] = [{
            "type": "map", "label": "高德路线", "provider": "高德地图",
            "url": "https://uri.amap.com/navigation?to=120.2,30.3,end&mode=walk",
        }]
        html = render_itinerary.render_route_handoff(route)
        self.assertIn('class="route-handoff"', html)
        self.assertIn('class="route-copy"', html)
        self.assertIn("https://uri.amap.com/navigation?to=120.2,30.3,end&amp;mode=walk", html)

    def test_route_stop_arrival_mode_is_shown_when_supplied(self) -> None:
        data = self.load_example()
        data["planning"]["daily_routes"][0]["stops"][1]["arrival_mode"] = "步行"
        html = render_itinerary.render_route_overview(data["days"], data["planning"]["daily_routes"])
        self.assertIn("到达方式：步行", html)

    def test_stop_sequence_does_not_require_coordinates_or_single_mode(self) -> None:
        data = self.load_example()
        for route in data["planning"]["daily_routes"]:
            route.pop("mode", None)
            for stop in route["stops"]:
                stop.pop("coordinates", None)
                stop.pop("coordinate_system", None)
        html = render_itinerary.build(data)
        self.assertIn("到达方式：公交或网约车", html)

    def test_confirmed_local_transport_requires_map_coordinates_and_link(self) -> None:
        data = self.load_example()
        route = data["planning"]["transport_edges"][0]
        route.pop("map_route")
        with self.assertRaisesRegex(ValueError, "必须提供 map_route"):
            render_itinerary.validate_data(data)

        data = self.load_example()
        route = data["planning"]["transport_edges"][0]
        route["action_links"] = []
        with self.assertRaisesRegex(ValueError, "高德 URI 路线 action_link"):
            render_itinerary.validate_data(data)

        data = self.load_example()
        route = data["planning"]["transport_edges"][0]
        route["map_route"]["mode"] = "spaceship"
        with self.assertRaisesRegex(ValueError, "car、bus 或 walk"):
            render_itinerary.validate_data(data)

        data = self.load_example()
        route = data["planning"]["transport_edges"][0]
        route["action_links"][0]["url"] = "https://example.com/not-an-amap-route"
        with self.assertRaisesRegex(ValueError, "高德 URI 路线 action_link"):
            render_itinerary.validate_data(data)


if __name__ == "__main__":
    unittest.main()
