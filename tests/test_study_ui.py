from pathlib import Path
import unittest


class StudyUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "web" / "study.html").read_text(encoding="utf-8")

    def test_material_scope_picker_uses_owned_library_levels(self):
        for element_id in ("scopeSemester", "scopeCourse", "scopeUnit", "scopeFilename"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("api('/library'", self.html)
        self.assertIn("api('/units?'", self.html)
        self.assertIn("function studySearchFilter()", self.html)
        self.assertIn("for (const key of ['semester', 'course', 'unit', 'filename'])", self.html)

    def test_question_and_claim_share_one_guided_input(self):
        self.assertEqual(self.html.count('id="studyInput"'), 1)
        self.assertNotIn('id="askQ"', self.html)
        self.assertNotIn('id="claimQ"', self.html)
        self.assertIn("setStudyMode('ask')", self.html)
        self.assertIn("setStudyMode('claim')", self.html)
        self.assertIn("async function runStudy()", self.html)
        self.assertIn("search_filter: studySearchFilter()", self.html)

    def test_evidence_cards_link_to_exact_study_workspace_page(self):
        self.assertIn("function sourceWorkspaceUrl(source)", self.html)
        self.assertIn("'/study-workspace.html?'", self.html)
        self.assertIn("query.set('page', String(source.page_num))", self.html)
        self.assertIn("원문 p.${esc(s.page_num)} 열기", self.html)


if __name__ == "__main__":
    unittest.main()
