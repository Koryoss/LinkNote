from pathlib import Path
import unittest


class ConceptPageCompactionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parents[1] / "web" / "gallery.html").read_text(encoding="utf-8")

    def test_graph_node_page_lists_are_compact(self):
        self.assertIn("const CONCEPT_NODE_PAGE_LIMIT = 6;", self.html)
        self.assertIn("function conceptPagePreview(pages, limit)", self.html)
        self.assertIn("pages.slice(0, Math.max(1, Number(limit) || 1))", self.html)
        self.assertIn("외 ${hiddenCount}쪽", self.html)
        self.assertIn("conceptPagePreview(pages, CONCEPT_NODE_PAGE_LIMIT)", self.html)

    def test_full_occurrence_navigation_remains_available(self):
        self.assertIn('id="concept_page_nav"', self.html)
        self.assertIn("function conceptOccurrences(n)", self.html)
        self.assertIn("activeConceptOccurrences.map", self.html)
        self.assertIn("docShow(occurrence.filename, occurrence.page)", self.html)
        self.assertIn('aria-label="이전 출현 페이지"', self.html)
        self.assertIn('aria-label="다음 출현 페이지"', self.html)


if __name__ == "__main__":
    unittest.main()
