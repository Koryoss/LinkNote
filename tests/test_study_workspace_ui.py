from pathlib import Path
import unittest


class StudyWorkspaceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "web" / "study-workspace.html").read_text(encoding="utf-8")
        cls.script = (root / "web" / "study-workspace.js").read_text(encoding="utf-8")

    def test_scope_navigation_exposes_all_library_levels(self):
        self.assertIn('id="scopeNav"', self.html)
        self.assertIn('aria-label="학습 자료 경로"', self.html)
        for level in ("semester", "course", "unit", "filename"):
            self.assertIn("['%s', scope.%s" % (level, level), self.script)
        self.assertIn("getJSON('/library')", self.script)
        self.assertIn("getJSON('/units?'", self.script)
        self.assertIn("workspaceUrl(nextScope)", self.script)


if __name__ == "__main__":
    unittest.main()
