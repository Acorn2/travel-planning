from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "package_plugin.py"
if not MODULE_PATH.is_file():
    raise unittest.SkipTest("打包脚本测试仅在源码仓库运行；安装包不包含仓库打包工具")
SPEC = importlib.util.spec_from_file_location("package_plugin", MODULE_PATH)
package_plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_plugin)


class PackagePluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plugin = package_plugin.DEFAULT_PLUGIN_DIR
        self.patch_root = patch.object(package_plugin, "REPOSITORY_ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        for name in ("README.md", "LICENSE", ".env.example", ".agents/plugins/marketplace.json",
                     str(self.plugin / ".codex-plugin/plugin.json"), str(self.plugin / "skills/example/SKILL.md")):
            self.write(name)

    def write(self, name: str, value: str = "test fixture") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def test_tracked_private_files_are_excluded_even_without_ignore_rules(self) -> None:
        forbidden = [".env", ".env.production", "config/sources.local.env", "state/cookies.json",
                     "config/private.key", "state/service.json", ".travel-research/trip/manifest.json", ".DS_Store", ".idea/workspace.xml"]
        paths = [self.write(str(self.plugin / name)) for name in forbidden]
        template = self.write(str(self.plugin / "config/sources.example.env"))
        candidates = [path.relative_to(self.root) for path in [*paths, template]]
        listing = subprocess.CompletedProcess([], 0, stdout=b"\0".join(str(path).encode() for path in candidates) + b"\0")
        with patch.object(package_plugin.subprocess, "run", side_effect=[listing, subprocess.CompletedProcess([], 1, stdout=b"")]):
            included = package_plugin.package_files(self.plugin)
        self.assertEqual(included, [template.relative_to(self.root)])

    def test_ignore_rules_are_applied_to_cached_git_files(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write(".gitignore", "plugins/travel-planning/config/local.json\n")
        private = self.write(str(self.plugin / "config/local.json"))
        safe = self.plugin / "skills/example/SKILL.md"
        listing = subprocess.CompletedProcess([], 0, stdout=(str(private.relative_to(self.root)) + "\0" + str(safe) + "\0").encode())
        original_run = subprocess.run
        def run(command, **kwargs):
            return listing if "ls-files" in command else original_run(command, **kwargs)
        with patch.object(package_plugin.subprocess, "run", side_effect=run):
            self.assertEqual(package_plugin.package_files(self.plugin), [safe])

    def test_symlink_cannot_export_an_external_file(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / "private.env"
            target.write_text("synthetic secret", encoding="utf-8")
            link = self.root / self.plugin / "config/settings.json"
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
            listing = subprocess.CompletedProcess([], 0, stdout=(str(link.relative_to(self.root)) + "\0").encode())
            with patch.object(package_plugin.subprocess, "run", side_effect=[listing, subprocess.CompletedProcess([], 1, stdout=b"")]):
                with self.assertRaisesRegex(SystemExit, "符号链接"):
                    package_plugin.package_files(self.plugin)

    def test_archive_contains_templates_and_resources_but_no_private_files(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write(str(self.plugin / "config/sources.local.env"))
        self.write("assets/readme/demo.png", "synthetic screenshot fixture")
        output = Path("output/test.zip")
        package_plugin.build_archive(self.plugin, output, "test-bundle")
        with zipfile.ZipFile(self.root / output) as archive:
            names = archive.namelist()
        self.assertIn("test-bundle/.env.example", names)
        self.assertIn("test-bundle/assets/readme/demo.png", names)
        self.assertIn(f"test-bundle/{self.plugin}/skills/example/SKILL.md", names)
        self.assertFalse(any(name.endswith("sources.local.env") for name in names))


if __name__ == "__main__":
    unittest.main()
