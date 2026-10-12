from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "travel-planning"
MODULE_PATH = SKILL_ROOT / "scripts" / "audit_itinerary.py"
SPEC = importlib.util.spec_from_file_location("audit_itinerary", MODULE_PATH)
assert SPEC and SPEC.loader
audit_itinerary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_itinerary)


def add_expired_inventory(data: dict) -> str:
    snapshot_id = "0123456789abcdef01234567"
    data["planning"]["source_snapshots"] = [{
        "schema_version": "travel-source-snapshot/v1", "snapshot_id": snapshot_id,
        "snapshot_kind": "quote", "status": "platform_reported",
        "provider": {"id": "fliggy_flyai", "name": "飞猪 FlyAI"},
        "product_type": "flight", "tool": "search-flight", "query": {},
        "freshness": {"checked_at": "2000-01-01T10:00:00+08:00", "expires_at": "2000-01-01T10:30:00+08:00", "dynamic": True},
        "items": [{"offer_id": "mu1", "name": "MU1", "price": {"display": "¥500"}, "availability": {}, "action_link": None}],
        "count": 1, "raw_response_hash": "sha256:" + "a" * 64,
        "source": {"title": "飞猪", "url": "https://example.com", "kind": "official_platform_api"}, "disclaimer": "复核",
    }]
    route = next(item for item in data["planning"]["transport_edges"] if item["id"] == "r1")
    route["inventory_refs"] = [{"snapshot_id": snapshot_id, "offer_id": "mu1", "role": "candidate_quote"}]
    return snapshot_id


class AuditItineraryTest(unittest.TestCase):
    def load_example(self) -> dict:
        return json.loads((SKILL_ROOT / "assets" / "example-itinerary.json").read_text(encoding="utf-8"))

    def test_example_passes(self) -> None:
        result = audit_itinerary.audit(self.load_example())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["blocking"], [])
        self.assertEqual(result["restaurant_audit"]["used_meal_slot_count"], 3)
        self.assertEqual(result["restaurant_audit"]["restaurant_count"], 5)

    def test_invalid_event_prose_is_blocked_without_crashing(self) -> None:
        for field in ("details", "tips"):
            for value in ([{"plan": "原始对象"}], {"plan": "原始对象"}, 60, True, [""]):
                data = self.load_example()
                data["days"][0]["events"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(any(field in item for item in audit_itinerary.audit(data)["blocking"]))

    def test_meal_windows_accept_chinese_and_english_labels(self) -> None:
        for label in ("午餐", "lunch"):
            data = self.load_example()
            for meal in data["planning"]["meal_options"]:
                if meal["meal_type"] == "午餐":
                    meal["meal_type"] = label
            with self.subTest(label=label):
                self.assertEqual(audit_itinerary.audit(data)["blocking"], [])

    def test_other_meal_labels_do_not_satisfy_lunch_window(self) -> None:
        for label in ("晚餐", "dinner", "茶歇", "breakfast"):
            data = self.load_example()
            for meal in data["planning"]["meal_options"]:
                if meal["meal_type"] == "午餐":
                    meal["meal_type"] = label
            with self.subTest(label=label):
                self.assertTrue(any("午餐窗口" in item for item in audit_itinerary.audit(data)["blocking"]))

    def test_dinner_window_requires_matching_meal_label(self) -> None:
        for label in ("晚餐", "dinner", "午餐", "lunch", "茶歇"):
            data = self.load_example()
            day = data["days"][0]
            meal_event = next(event for event in day["events"] if event.get("meal_id") == "m1")
            day["events"] = [meal_event]
            meal_event.update(time="18:30", end_time="19:30")
            meal = next(item for item in data["planning"]["meal_options"] if item["id"] == "m1")
            meal.update(meal_type=label, time_window="18:30–19:30")
            with self.subTest(label=label):
                gaps = [item for item in audit_itinerary.audit(data)["blocking"] if "晚餐窗口" in item]
                self.assertEqual(bool(gaps), label not in ("晚餐", "dinner"))

    def test_detects_time_overlap(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][2]["time"] = "10:00"
        result = audit_itinerary.audit(data)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("时间重叠" in item for item in result["blocking"]))

    def test_detects_missing_meal_in_active_window(self) -> None:
        data = self.load_example()
        data["days"][0]["events"] = [event for event in data["days"][0]["events"] if event["type"] != "meal"]
        data["days"][0]["events"][-1]["end_time"] = "13:00"
        result = audit_itinerary.audit(data)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("午餐窗口" in item for item in result["blocking"]))

    def test_meal_exemption_is_explicit(self) -> None:
        data = self.load_example()
        data["days"][0]["events"] = [event for event in data["days"][0]["events"] if event["type"] != "meal"]
        data["days"][0]["events"][-1]["end_time"] = "13:00"
        data["days"][0]["meal_exemptions"] = {"午餐": "12:00 前结束正式活动，用户自行安排"}
        result = audit_itinerary.audit(data)
        self.assertFalse(any("午餐窗口" in item for item in result["blocking"]))

    def test_detects_internal_checkpoint_overlap(self) -> None:
        data = self.load_example()
        data["days"][0]["events"][1]["execution"]["checkpoints"][1]["time"] = "09:20"
        result = audit_itinerary.audit(data)
        self.assertTrue(any("节点时间重叠" in item for item in result["blocking"]))

    def test_detects_meal_event_outside_researched_time_window(self) -> None:
        data = self.load_example()
        meal_event = data["days"][0]["events"][3]
        meal_event["time"] = "13:20"
        meal_event["end_time"] = "14:20"
        result = audit_itinerary.audit(data)
        self.assertTrue(any("超出 meal_option.time_window" in item for item in result["blocking"]))

    def test_warns_about_unbound_meal_research(self) -> None:
        data = self.load_example()
        data["planning"]["meal_options"].append({"id": "unused-meal"})
        result = audit_itinerary.audit(data)
        self.assertTrue(any("未绑定" in item and "unused-meal" in item for item in result["warnings"]))

    def test_confirmed_plan_warns_when_selected_inventory_snapshot_expired(self) -> None:
        data = self.load_example()
        snapshot_id = add_expired_inventory(data)
        result = audit_itinerary.audit(data)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(any(snapshot_id in item and "已过期" in item for item in result["warnings"]))
        self.assertEqual(result["inventory_audit"]["selected_reference_count"], 1)

    def test_final_plan_blocks_selected_transport_without_multi_sort_coverage(self) -> None:
        data = self.load_example()
        snapshot_id = add_expired_inventory(data)
        snapshot = next(
            item for item in data["planning"]["source_snapshots"]
            if item["snapshot_id"] == snapshot_id
        )
        snapshot["freshness"] = {
            "checked_at": "2099-01-01T10:00:00+08:00",
            "expires_at": "2099-01-01T10:30:00+08:00",
            "dynamic": True,
        }
        data["workflow"]["phase"] = "final"
        result = audit_itinerary.audit(data)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("排序覆盖" in item for item in result["blocking"]))
        self.assertTrue(result["inventory_audit"]["transport_coverage_gaps"])

    def test_audits_community_evidence_source_closure_and_recheck(self) -> None:
        data = self.load_example()
        restaurant_id = data["planning"]["restaurants"][0]["id"]
        data["sources"].append({
            "id": "xhs-restaurant-1",
            "title": "小红书评论",
            "url": "https://example.com/xhs-restaurant-1",
            "kind": "community",
            "provider": "xiaohongshu",
        })
        data["planning"]["community_evidence"] = [{
            "target_entity_id": restaurant_id,
            "target_entity_type": "restaurant",
            "provider": "xiaohongshu",
            "checked_at": "2026-09-25T10:00:00+08:00",
            "topic": "价格",
            "insight": "人均约100元",
            "support_count": 2,
            "contradiction_count": 0,
            "community_confidence": "medium",
            "needs_official_recheck": True,
            "source_ids": ["xhs-restaurant-1"],
        }]
        result = audit_itinerary.audit(data)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["community_audit"]["evidence_count"], 1)
        self.assertEqual(result["community_audit"]["official_recheck_warning_count"], 1)
        self.assertTrue(any("仍需官方" in item for item in result["warnings"]))

    def test_final_plan_rejects_xhs_homepage_as_note_source(self) -> None:
        data = self.load_example()
        data["workflow"]["phase"] = "final"
        data["sources"].append({
            "id": "xhs-note-homepage",
            "title": "小红书：宜昌美食笔记",
            "url": "https://www.xiaohongshu.com/",
            "kind": "community",
            "provider": "小红书",
        })
        result = audit_itinerary.audit(data)
        self.assertIn("仅链接平台首页", " ".join(result["blocking"]))
        self.assertIn("玩法体验依据", " ".join(result["warnings"]))

    def test_final_plan_blocks_medium_confidence_community_conflict(self) -> None:
        data = self.load_example()
        restaurant_id = data["planning"]["restaurants"][0]["id"]
        data["sources"].append({
            "id": "xhs-restaurant-1",
            "title": "小红书评论",
            "url": "https://example.com/xhs-restaurant-1",
            "kind": "community",
            "provider": "xiaohongshu",
        })
        data["planning"]["community_evidence"] = [{
            "target_entity_id": restaurant_id,
            "target_entity_type": "restaurant",
            "provider": "xiaohongshu",
            "checked_at": "2026-09-25T10:00:00+08:00",
            "topic": "排队与拥挤",
            "insight": "周末需要排队",
            "support_count": 3,
            "contradiction_count": 0,
            "community_confidence": "medium",
            "needs_official_recheck": True,
            "source_ids": ["xhs-restaurant-1"],
        }]
        data["planning"]["community_evidence_conflicts"] = [{
            "target_entity_id": restaurant_id,
            "target_entity_type": "restaurant",
            "topic": "排队与拥挤",
            "selected_value": {"community_confidence": "medium"},
            "previous_source_ids": ["xhs-restaurant-1"],
        }]
        result = audit_itinerary.audit(data)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("不得保持 medium" in item for item in result["blocking"]))


if __name__ == "__main__":
    unittest.main()
