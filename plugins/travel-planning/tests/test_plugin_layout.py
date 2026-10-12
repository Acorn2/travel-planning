from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]


class PluginLayoutTest(unittest.TestCase):
    def test_user_visible_skills_are_registered(self) -> None:
        skill_files = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "skills").rglob("SKILL.md"))
        self.assertEqual(
            skill_files,
            [
                "skills/amap-maps/SKILL.md",
                "skills/flyai/SKILL.md",
                "skills/food-discovery/SKILL.md",
                "skills/travel-planning/SKILL.md",
                "skills/variflight/SKILL.md",
                "skills/xiaohongshu/SKILL.md",
            ],
        )

    def test_mcp_skills_guide_existing_servers_without_provider_scripts(self) -> None:
        self.assertTrue((ROOT / "skills/amap-maps/references/tool-routing.md").is_file())
        self.assertTrue((ROOT / "skills/variflight/references/tool-routing.md").is_file())
        self.assertFalse((ROOT / "skills/amap-maps/scripts").exists())
        self.assertFalse((ROOT / "skills/variflight/scripts").exists())

    def test_flyai_is_one_vendored_skill(self) -> None:
        self.assertTrue((ROOT / "scripts/providers/flyai_cli.py").is_file())
        self.assertTrue((ROOT / "skills/flyai/references/upstream.lock.json").is_file())

    def test_expected_mcp_servers_are_registered(self) -> None:
        manifest = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        servers = manifest["mcpServers"]
        self.assertEqual(
            set(servers),
            {"amap-maps", "variflight-aviation", "variflight-tripmatch", "xiaohongshu-mcp"},
        )
        self.assertEqual(
            servers["amap-maps"]["args"],
            ["./scripts/providers/amap_mcp.py"],
        )
        self.assertEqual(
            servers["variflight-aviation"]["args"],
            [
                "./scripts/providers/variflight_mcp.py",
                "aviation",
            ],
        )
        self.assertEqual(
            servers["variflight-tripmatch"]["args"],
            [
                "./scripts/providers/variflight_mcp.py",
                "tripmatch",
            ],
        )
        self.assertEqual(
            servers["xiaohongshu-mcp"],
            {"type": "http", "url": "http://127.0.0.1:18060/mcp"},
        )

    def test_xiaohongshu_uses_pinned_http_mcp_without_extension_vendor(self) -> None:
        required = (
            ROOT / "skills/xiaohongshu/scripts/setup.py",
            ROOT / "skills/xiaohongshu/references/upstream.lock.json",
            ROOT / "skills/xiaohongshu/references/tool-routing.md",
        )
        self.assertTrue(all(path.is_file() for path in required))
        self.assertFalse((ROOT / "skills/xiaohongshu/assets").exists())
        self.assertFalse((ROOT / "skills/xiaohongshu/scripts/upstream").exists())

    def test_runtime_secrets_are_ignored_by_source_control(self) -> None:
        self.assertTrue((ROOT / "config/sources.example.env").is_file())
        repository_ignore = REPOSITORY_ROOT / ".gitignore"
        if repository_ignore.is_file():
            ignored = repository_ignore.read_text(encoding="utf-8").splitlines()
            self.assertIn("**/config/sources.local.env", ignored)
            # A distributable checkout must not require a private credential file.
        self.assertFalse((ROOT / ".travel-tools").exists())

    def test_provider_runtime_is_not_nested_inside_skills(self) -> None:
        self.assertFalse((ROOT / "skills/flyai/scripts").exists())
        self.assertFalse((ROOT / "skills/travel-planning/config").exists())
        self.assertFalse(
            (ROOT / "skills/travel-planning/scripts/amap_mcp_server.py").exists()
        )
        self.assertFalse(
            (ROOT / "skills/travel-planning/scripts/variflight_mcp_server.py").exists()
        )

    def test_travel_planning_has_generic_assembler_contract(self) -> None:
        skill = ROOT / "skills" / "travel-planning"
        self.assertTrue((skill / "scripts" / "assemble_itinerary.py").is_file())
        schema = json.loads((skill / "schemas" / "itinerary-plan.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "itinerary-plan/v1")

    def test_travel_planning_ships_compiled_vue_frontend(self) -> None:
        frontend = ROOT / "skills" / "travel-planning" / "assets" / "frontend"
        web = ROOT / "web"
        self.assertTrue((web / "package.json").is_file())
        self.assertTrue((web / "package-lock.json").is_file())
        self.assertTrue((web / "src" / "App.vue").is_file())
        self.assertIn("v-show", (web / "src" / "App.vue").read_text(encoding="utf-8"))
        self.assertTrue((frontend / "itinerary-app.js").is_file())
        self.assertTrue((frontend / "itinerary-app.css").is_file())

    def test_legacy_top_level_integration_directories_are_absent(self) -> None:
        self.assertFalse((ROOT / "integrations").exists())
        self.assertFalse((ROOT / "third_party").exists())


if __name__ == "__main__":
    unittest.main()
