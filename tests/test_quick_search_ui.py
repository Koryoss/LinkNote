import os
import unittest


class QuickSearchUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "web", "gallery.html")
        with open(path, encoding="utf-8") as handle:
            cls.html = handle.read()

    def test_clickable_concept_results_open_the_workspace_source_page(self):
        self.assertIn("function studyWorkspaceSourceHref(x)", self.html)
        self.assertIn("function renderSearchConceptResult(x)", self.html)
        self.assertIn('href="${esc(studyWorkspaceSourceHref(x))}"', self.html)
        self.assertIn('onclick="recordSearchConceptLink(this)"', self.html)
        self.assertNotIn("window.location.href=`/concept-graph.html?concept=", self.html)

    def test_concepts_without_verified_sources_are_not_linked(self):
        self.assertIn("if(!x.source_available)", self.html)
        self.assertIn("search-item-concept-unlinked", self.html)

    def test_search_results_show_evidence_types_and_missing_definition_notice(self):
        self.assertIn("x.evidence_label", self.html)
        self.assertIn("d.definition_found === false", self.html)
        self.assertIn("정의형 원문은 확인되지 않아", self.html)


if __name__ == "__main__":
    unittest.main()
