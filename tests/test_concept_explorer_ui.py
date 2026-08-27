import os
import unittest


class ConceptExplorerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "web", "concept-graph.html")
        with open(path, encoding="utf-8") as handle:
            cls.html = handle.read()

    def test_explorer_is_the_default_view(self):
        self.assertIn("let currentMode = 'understand';", self.html)
        self.assertIn("let currentView = 'map';", self.html)
        self.assertIn("<h1>개념 탐색기</h1>", self.html)

    def test_focus_map_limits_neighbors_and_keeps_full_map_optional(self):
        self.assertIn("const MODE_LIMITS = { understand:9, connection:9, review:9", self.html)
        self.assertIn(".slice(0, 8)", self.html)
        self.assertIn("onclick=\"setMode('full')\">전체 보기", self.html)

    def test_three_panel_explorer_supports_search_and_relationship_selection(self):
        self.assertIn("grid-template-columns:220px minmax(0, 1fr) 340px", self.html)
        self.assertIn("class=\"concept-search\"", self.html)
        self.assertIn("class=\"edge-hit\"", self.html)
        self.assertIn("function showEdgeDetails(edge)", self.html)
        self.assertIn("연결선을 눌러보세요", self.html)

    def test_selected_concept_loads_free_source_evidence(self):
        self.assertIn("quickSearchNode(node.id);", self.html)
        self.assertIn("관련 자료는 선택한 개념을 기준으로 자동 검색", self.html)
        self.assertIn("GPT 답변 생성 없음", self.html)
        self.assertIn("x.relevance_label || '관련성 높음'", self.html)


if __name__ == "__main__":
    unittest.main()
