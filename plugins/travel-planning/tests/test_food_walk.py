from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "travel-planning"
spec = importlib.util.spec_from_file_location("food_walk_audit", SKILL / "scripts" / "audit_itinerary.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
renderer = audit.render_itinerary


def example() -> dict:
    # Synthetic fixture, not an actual destination recommendation.
    return {
        "trip": {"title": "逛吃测试", "date_range": "2026-10-10", "travelers": "2人"},
        "sources": [{"id": "street-food", "title": "测试社区证据", "url": "https://example.com/food", "checked_at": "2026-10-09"}],
        "days": [{"date": "2026-10-10", "title": "街区午餐", "events": [{
            "id": "food-walk-1", "type": "food_walk", "title": "小吃街边走边吃", "time": "12:00", "end_time": "13:30",
            "food_walk": {
                "meal_type": "午餐", "budget_per_person": 60,
                "entry": {"name": "测试街南口", "physical_address": "测试市测试街1号", "coordinates": "111.28,30.69", "map_url": "https://uri.amap.com/marker?position=111.28,30.69"},
                "exit": {"name": "测试街北口", "physical_address": "测试市测试街20号", "coordinates": "111.28,30.70", "map_url": "https://uri.amap.com/marker?position=111.28,30.70"},
                "food_items": [{"name": "测试面食", "source_ids": ["street-food"]}, {"name": "测试蒸点", "source_ids": ["street-food"]}],
                "fallback": "下雨时到已核实的室内用餐点", "pet_note": "是否可携犬仍需确认",
            },
        }]}],
    }


class FoodWalkTest(unittest.TestCase):
    def test_structured_fallback_is_readable_in_html_and_portable_guide(self):
        data = example()
        fallback = {"name": "测试室内餐饮区", "physical_address": "测试市测试路2号", "plan": "下雨或摊位未营业时，在室内吃热食简餐。"}
        data["days"][0]["events"][0]["food_walk"]["fallback"] = fallback
        self.assertEqual(audit.audit(data)["blocking"], [])
        for output in (renderer.build(data), renderer.render_portable_guide(data)):
            for value in fallback.values():
                self.assertIn(value, output)
            for field in ("physical_address", "&#x27;name&#x27;", "&#x27;plan&#x27;"):
                self.assertFalse(field in output, f"Raw field leaked: {field}")

    def test_invalid_fallback_is_blocked_by_audit_and_renderer(self):
        for fallback in (None, "", "  ", [], ["室内简餐"], 60, True,
                         {"name": "室内餐饮区"},
                         {"name": "室内餐饮区", "physical_address": "测试路2号", "plan": {"text": "简餐"}},
                         {"name": "室内餐饮区", "physical_address": " ", "plan": "吃简餐"},
                         {"name": "室内餐饮区", "physical_address": "测试路2号", "plan": "吃简餐", "raw": {}}):
            data = example()
            data["days"][0]["events"][0]["food_walk"]["fallback"] = fallback
            with self.subTest(fallback=fallback):
                self.assertTrue(audit.audit(data)["blocking"])
                with self.assertRaises(ValueError):
                    renderer.build(data)

    def test_legacy_fallback_remains_readable(self):
        data = example()
        self.assertIn("下雨时到已核实的室内用餐点", renderer.build(data))
        self.assertIn("下雨时到已核实的室内用餐点", renderer.render_portable_guide(data))

    def test_fallback_text_is_escaped_when_displayed(self):
        data = example()
        data["days"][0]["events"][0]["food_walk"]["fallback"] = {
            "name": "<script>测试</script>", "physical_address": "测试路2号", "plan": "下雨时选热食简餐。"}
        for output in (renderer.build(data), renderer.render_portable_guide(data)):
            self.assertFalse("<script>测试</script>" in output)
            self.assertTrue("&lt;script&gt;测试&lt;/script&gt;" in output)

    def test_nested_food_walk_values_are_rejected_without_crashing_audit(self):
        for field, value in (("meal_type", []), ("entry", "地图原始文本"),
                             ("food_items", [{"name": {"text": "简餐"}}]),
                             ("pet_note", {"description": "待核"})):
            data = example()
            data["days"][0]["events"][0]["food_walk"][field] = value
            with self.subTest(field=field):
                self.assertTrue(audit.audit(data)["blocking"])

    def test_street_meal_passes_without_fictitious_restaurant_candidates(self):
        data = example()
        result = audit.audit(data)
        self.assertEqual(result["blocking"], [])
        html = renderer.build(data)
        for value in ("逛吃", "测试街南口", "测试街北口", "测试面食", "60", "是否可携犬仍需确认"):
            self.assertIn(value, html)
        portable = renderer.render_portable_guide(data)
        self.assertIn("测试蒸点", portable)
        self.assertIn("data-print-map", portable)
        self.assertNotIn("event-compact", renderer.render_event(data["days"][0]["events"][0], 0))

    def test_snack_only_does_not_satisfy_lunch(self):
        data = example()
        data["days"][0]["events"][0]["food_walk"]["meal_type"] = "小吃"
        self.assertTrue(any("午餐" in item for item in audit.audit(data)["blocking"]))

    def test_missing_street_fields_or_evidence_are_blocking(self):
        for field in ("entry", "food_items", "budget_per_person", "fallback"):
            data = example()
            del data["days"][0]["events"][0]["food_walk"][field]
            with self.subTest(field=field):
                self.assertTrue(audit.audit(data)["blocking"])
        data = example()
        data["sources"] = []
        self.assertTrue(audit.audit(data)["blocking"])

    def test_partial_community_evidence_is_not_presented_as_full_consensus(self):
        data = json.loads((SKILL / "assets" / "example-itinerary.json").read_text())
        meal = data["planning"]["meal_options"][0]
        snapshot_id = next(item["snapshot_id"] for item in meal["candidates"] if item["restaurant_id"] == meal["selected_candidate_id"])
        snapshot = next(item for item in data["planning"]["restaurant_snapshots"] if item["snapshot_id"] == snapshot_id)
        community = snapshot["community_consensus"]
        community.update(status="partial", confidence="low", references=community["references"][:1], notes_considered=1, recent_note_count=1)
        renderer.validate_data(data)
        self.assertIn("样本有限", renderer.build(data))
        community["confidence"] = "medium"
        with self.assertRaises(ValueError):
            renderer.validate_data(data)


if __name__ == "__main__":
    unittest.main()
