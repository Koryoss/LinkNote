from pathlib import Path
import re
import unittest


class StudyWorkspaceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "web" / "study-workspace.html").read_text(encoding="utf-8")
        cls.script = (root / "web" / "study-workspace.js").read_text(encoding="utf-8")

    def _top_navigation_source(self):
        return self.script.split("function renderScopeNavigation()", 1)[1].split("function renderDocumentContext()", 1)[0]

    def _document_context_source(self):
        return self.script.split("function renderDocumentContext()", 1)[1].split("function closeScopeMenu(", 1)[0]

    def test_scope_navigation_exposes_all_library_levels(self):
        self.assertIn('id="scopeNav"', self.html)
        self.assertIn('aria-label="학습 자료 경로"', self.html)
        self.assertIn('id="documentContext"', self.html)
        self.assertIn('aria-label="현재 단원과 자료"', self.html)
        for level in ("semester", "course"):
            self.assertIn("['%s', scope.%s" % (level, level), self.script)
        top_navigation = self._top_navigation_source()
        self.assertIn("['semester', scope.semester", top_navigation)
        self.assertIn("['course', scope.course", top_navigation)
        self.assertNotIn("['unit', scope.unit", top_navigation)
        self.assertNotIn("['filename', scope.filename", top_navigation)
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

    def test_top_breadcrumb_never_contains_unit_or_material_pickers(self):
        # 상단 경로(첫 번째 줄)에는 홈·학기·과목 pill만 있어야 하고, 단원·자료
        # 선택기가 어떤 화면 폭에서도 이 줄에 섞이면 안 된다.
        top_navigation = self._top_navigation_source()
        self.assertNotIn("data-scope-level=\"unit\"", top_navigation)
        self.assertNotIn("data-scope-level=\"filename\"", top_navigation)
        self.assertNotIn("scope.unit", top_navigation)
        self.assertNotIn("scope.filename", top_navigation)

    def test_unit_and_material_pickers_live_in_the_second_area(self):
        # 단원·자료 선택기는 상단 breadcrumb가 아니라 #documentContext(두 번째
        # 영역) 안에서만 렌더링되어야 하고, 실제 단원 변경 기능(openScopeMenu)으로
        # 연결되어야 한다.
        context_source = self._document_context_source()
        self.assertIn('data-scope-level="unit"', context_source)
        self.assertIn('data-scope-level="filename"', context_source)
        self.assertIn("openScopeMenu(button.dataset.scopeLevel, button, event)", context_source)

    def test_second_area_title_does_not_repeat_the_full_course_name(self):
        # 상단 과목 pill에 이미 과목 전체 이름이 보이므로, 아래 제목에서 과목명을
        # 다시 반복하거나 정보 없는 "· 단원" 같은 고정 문구만 남기면 안 된다.
        # 실제 현재 단원명(scope.unit)을 그대로 표시해야 한다.
        context_source = self._document_context_source()
        self.assertNotIn("scope.course", context_source)
        self.assertNotIn("단원</h1>", context_source)
        self.assertIn("esc(scope.unit ||", context_source)
        self.assertIn("scope-row-title", context_source)
        self.assertIn("esc(scope.filename ||", context_source)
        self.assertIn("scope-row-file", context_source)

    def test_workspace_brand_size_has_no_responsive_override(self):
        # 로고 크기는 모든 화면에서 고정이어야 하므로, 899px 미디어쿼리 블록
        # 안에 .brand 규칙이 따로 있으면 안 된다.
        mobile_block = re.search(r"@media \(max-width: 899px\) \{(.*)\}\s*</style>", self.html, re.S)
        self.assertIsNotNone(mobile_block)
        self.assertNotIn(".brand", mobile_block.group(1))

    def test_mobile_layout_keeps_breadcrumb_and_second_area_separate(self):
        # 375px 등 좁은 화면에서도 상단 breadcrumb(.scope-nav)과 단원·자료
        # 영역(.document-context)은 서로 다른 규칙으로 각자 줄바꿈되어야 하며
        # 하나로 합쳐지면 안 된다.
        mobile_block = re.search(r"@media \(max-width: 899px\) \{(.*)\}\s*</style>", self.html, re.S)
        self.assertIsNotNone(mobile_block)
        mobile_css = mobile_block.group(1)
        self.assertIn(".scope-nav { order: 3; flex-basis: 100%; }", mobile_css)
        self.assertIn(".document-context {", mobile_css)
        self.assertIn("overflow-x: auto", self.html)  # 상단 경로 가로 스크롤 유지

    def test_hidden_mobile_drawer_scrims_stay_hidden(self):
        # .mobile-only가 모바일에서 display:inline-flex로 바뀌더라도 hidden인
        # 목차·원문 배경막이 먼저 노출되어 화면 전체를 가리면 안 된다.
        self.assertIn(".mobile-only[hidden] { display: none !important; }", self.html)
        self.assertIn('id="tocScrim"', self.html)
        self.assertIn('id="sourceScrim"', self.html)

    def test_exact_source_page_from_study_evidence_is_selected(self):
        self.assertIn("var requestedPage = Number(params.get('page')) || null;", self.script)
        self.assertIn("var requestedPageExists = requestedPage && state.pages.some", self.script)
        self.assertIn("state.activePage = requestedPageExists", self.script)


if __name__ == "__main__":
    unittest.main()
