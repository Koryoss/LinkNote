from pathlib import Path
import unittest


class GalleryUploadUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parents[1] / "web" / "gallery.html").read_text(encoding="utf-8")

    def test_upload_picker_supports_multiple_files_and_drag_drop(self):
        self.assertIn('id="up_file_picker"', self.html)
        self.assertIn("multiple hidden", self.html)
        self.assertIn('class="upload-dropzone"', self.html)
        self.assertIn("function uploadDrop(event)", self.html)

    def test_upload_uses_one_background_batch_and_shows_progress(self):
        self.assertIn("API+'/ingest/batch'", self.html)
        self.assertIn("function pollIngestJob(jobId)", self.html)
        self.assertIn("/ingest/jobs/", self.html)
        self.assertIn('id="up_progress"', self.html)

    def test_upload_semester_uses_existing_choice_buttons_and_2026_2_default(self):
        self.assertIn('id="up_sem_choices"', self.html)
        self.assertIn("const values = ['2026-2']", self.html)
        self.assertIn("sortSemestersLatestFirst", self.html)

    def test_oxidation_reduction_example_is_removed(self):
        self.assertNotIn("산화환원 관련 자료 찾아줘", self.html)

    def test_sun_is_only_drawn_for_a_subset_of_courses(self):
        self.assertIn("const sun = hash % 4 === 0 ?", self.html)

    def test_small_long_course_art_keeps_a_safe_area(self):
        self.assertIn("'간호대학생을위한정신약물의이해'", self.html)
        self.assertIn("'보건커뮤니케이션과건강교육'", self.html)
        self.assertIn("ART_SAFE_AREA_COURSES.has(normalized) ? '72%' : '82%'", self.html)
        self.assertIn("word-break:normal; overflow-wrap:anywhere", self.html)

    def test_current_document_can_be_moved_or_deleted(self):
        self.assertIn('onclick="openMoveFileModal(event)"', self.html)
        self.assertIn('onclick="deleteCurrentFile(event)"', self.html)
        self.assertIn('class="unit-file-row"', self.html)
        self.assertIn('onclick="openMoveFileFromButton(event)"', self.html)
        self.assertIn('onclick="deleteFileFromButton(event)"', self.html)
        self.assertIn('id="deleteFileModal"', self.html)
        self.assertIn("function submitDeleteFile()", self.html)
        self.assertIn("검색 청크와 개념 정보를 정리하고 있습니다.", self.html)
        self.assertIn("API+'/library/file/move'", self.html)
        self.assertIn("API+'/library/file'", self.html)
        self.assertIn("target_semester:targetSemester", self.html)

    def test_unit_cards_open_workspace_first_and_keep_concept_map_as_button(self):
        self.assertIn('onclick="openStudyWorkspaceForUnit(event.currentTarget)"', self.html)
        self.assertIn("function openStudyWorkspaceForUnit(card)", self.html)
        self.assertIn("/study-workspace.html?${q.toString()}", self.html)
        self.assertIn('class="unit-map-entry"', self.html)
        self.assertIn('onclick="openConceptMap(event)"', self.html)
        self.assertIn("function openConceptMap(event)", self.html)

    def test_unit_cards_prioritize_full_titles_without_repeated_workspace_label(self):
        self.assertIn("minmax(min(320px, 100%), 1fr)", self.html)
        self.assertIn("word-break: normal; overflow-wrap: anywhere", self.html)
        self.assertNotIn('class="ctag">⠿ 학습 작업대', self.html)

    def test_pdf_preview_request_includes_library_scope(self):
        self.assertIn("params.set('semester',state.semester)", self.html)
        self.assertIn("params.set('course',state.course)", self.html)
        self.assertIn("params.set('unit',state.unit)", self.html)
        self.assertIn('data-semester="${esc(x.semester||\'\')}"', self.html)
        self.assertIn('data-unit="${esc(x.unit||\'\')}"', self.html)

    def test_concept_occurrences_show_all_pages_and_control_pdf_preview(self):
        self.assertIn('id="concept_page_nav"', self.html)
        self.assertIn("function conceptOccurrences(n)", self.html)
        self.assertIn("function conceptPageInline(n)", self.html)
        self.assertIn("function showConceptOccurrence(index)", self.html)
        self.assertIn("function stepConceptOccurrence(delta)", self.html)
        self.assertIn("docShow(occurrence.filename, occurrence.page)", self.html)
        self.assertIn('aria-label="이전 출현 페이지"', self.html)
        self.assertIn('aria-label="다음 출현 페이지"', self.html)

    def test_large_concept_page_lists_are_compact_on_graph_nodes(self):
        self.assertIn("const CONCEPT_NODE_PAGE_LIMIT = 6;", self.html)
        self.assertIn("function conceptPagePreview(pages, limit)", self.html)
        self.assertIn("pages.slice(0, Math.max(1, Number(limit) || 1))", self.html)
        self.assertIn("외 ${hiddenCount}쪽", self.html)
        self.assertIn("conceptPagePreview(pages, CONCEPT_NODE_PAGE_LIMIT)", self.html)
        self.assertIn("총 ${pages.length}쪽", self.html)
        self.assertIn("activeConceptOccurrences.map", self.html)


if __name__ == "__main__":
    unittest.main()
