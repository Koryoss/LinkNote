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
        self.assertIn('id="documentContext"', self.html)
        self.assertIn('aria-label="현재 단원과 자료"', self.html)
        for level in ("semester", "course"):
            self.assertIn("['%s', scope.%s" % (level, level), self.script)
        top_navigation = self.script.split("function renderScopeNavigation()", 1)[1].split("function renderDocumentContext()", 1)[0]
        self.assertIn("['semester', scope.semester", top_navigation)
        self.assertIn("['course', scope.course", top_navigation)
        self.assertNotIn("['unit', scope.unit", top_navigation)
        self.assertNotIn("['filename', scope.filename", top_navigation)
        self.assertIn("esc(scope.course || '과목') + ' · 단원</h1>'", self.script)
        self.assertIn('data-scope-level="unit"', self.script)
        self.assertIn('data-scope-level="filename"', self.script)
        self.assertIn("getJSON('/library')", self.script)
        self.assertIn("getJSON('/units?'", self.script)
        self.assertIn("workspaceUrl(nextScope)", self.script)

    def test_source_preview_refits_with_panel_width_and_shows_page_number(self):
        self.assertIn('class="source-page-indicator"', self.script)
        self.assertIn("Logic.sourcePageLabel(state.activePage, state.pages)", self.script)
        self.assertIn("Logic.sourceViewHash(state.activePage)", self.script)
        self.assertIn("function scheduleSourceRefit()", self.script)
        self.assertIn("scheduleSourceRefit();", self.script)
        self.assertIn(".source-page-indicator", self.html)

    def test_source_panel_does_not_repeat_a_full_document_button(self):
        self.assertNotIn('id="openFullBtn"', self.html)
        self.assertNotIn("function openFull()", self.script)

    def test_exact_source_page_from_study_evidence_is_selected(self):
        self.assertIn("var requestedPage = Number(params.get('page')) || null;", self.script)
        self.assertIn("var requestedPageExists = requestedPage && state.pages.some", self.script)
        self.assertIn("state.activePage = requestedPageExists", self.script)


if __name__ == "__main__":
    unittest.main()
