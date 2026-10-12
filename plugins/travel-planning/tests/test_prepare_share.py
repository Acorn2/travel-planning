from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "skills" / "travel-planning" / "scripts" / "prepare_share.py"
SPEC = importlib.util.spec_from_file_location("prepare_share", MODULE_PATH)
assert SPEC and SPEC.loader
prepare_share = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_share)


class PrepareShareTest(unittest.TestCase):
    def test_copies_standalone_page_as_index_without_changing_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "itinerary.html"
            source.write_text('<html><style>body{color:red}</style><script>ok()</script><img src="https://example.com/a.jpg"></html>', encoding="utf-8")
            target = prepare_share.prepare_share(source, Path(temp) / "shared")
            self.assertEqual(target.name, "index.html")
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(prepare_share.prepare_share(source, target.parent), target)

    def test_rejects_local_assets_and_does_not_overwrite_existing_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "itinerary.html"
            output = Path(temp) / "shared"
            source.write_text('<html><script src="app.js"></script></html>', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "本地资源"):
                prepare_share.prepare_share(source, output)
            self.assertFalse(output.exists())
            source.write_text("<html>new</html>", encoding="utf-8")
            output.mkdir()
            (output / "index.html").write_text("<html>old</html>", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                prepare_share.prepare_share(source, output)
            self.assertEqual((output / "index.html").read_text(), "<html>old</html>")
            prepare_share.prepare_share(source, output, replace=True)
            self.assertEqual((output / "index.html").read_text(), "<html>new</html>")


if __name__ == "__main__":
    unittest.main()
