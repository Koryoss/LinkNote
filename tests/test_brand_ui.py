from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRAND_PAGES = (
    "gallery.html",
    "study.html",
    "study-workspace.html",
    "concept-graph.html",
    "learning-memory.html",
    "mypage.html",
    "clinical-reflection.html",
)


class BrandUiTests(unittest.TestCase):
    def test_top_left_brand_uses_one_fixed_size_on_every_page(self):
        for filename in BRAND_PAGES:
            with self.subTest(filename=filename):
                html = (ROOT / "web" / filename).read_text(encoding="utf-8")
                rule = re.search(r"(?:\.topbar )?\.brand\s*\{([^}]+)\}", html)
                self.assertIsNotNone(rule)
                declarations = rule.group(1).replace(" ", "")
                self.assertIn("flex:00auto", declarations)
                self.assertIn("font-size:20px", declarations)
                self.assertIn("line-height:24px", declarations)
                self.assertIn("font-weight:800", declarations)
                self.assertIn("white-space:nowrap", declarations)


if __name__ == "__main__":
    unittest.main()
