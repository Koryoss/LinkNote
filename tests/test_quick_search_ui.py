import os
import unittest


class QuickSearchUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "web", "gallery.html")
        with open(path, encoding="utf-8") as handle:
            cls.html = handle.read()

    def test_concepts_show_explanations_not_internal_match_reasons(self):
        self.assertIn("관련 개념 · 설명", self.html)
        self.assertIn("x.explanation||'설명을 찾지 못했습니다.'", self.html)
        concept_card = self.html.split("관련 개념 · 설명", 1)[1].split("근거 문서 · PDF 원문", 1)[0]
        self.assertNotIn("x.reason", concept_card)

    def test_pdf_result_has_explicit_workspace_page_link(self):
        self.assertIn("function studyWorkspaceSourceHref(x)", self.html)
        self.assertIn("filename:x.filename||'', page:String(x.page||1)", self.html)
        self.assertIn("원문 p.${esc(x.page || 1)} 열기 →", self.html)
        self.assertIn("onclick=\"recordSearchSourceLink(this)\"", self.html)

    def test_concepts_and_sources_have_distinct_visual_treatments(self):
        self.assertIn("search-card search-card-concepts", self.html)
        self.assertIn("search-card search-card-sources", self.html)


if __name__ == "__main__":
    unittest.main()
