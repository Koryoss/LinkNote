from pathlib import Path
import unittest


class MyPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parents[1] / "web" / "mypage.html").read_text(encoding="utf-8")

    def test_dynamic_profile_requests_bypass_webview_cache(self):
        self.assertIn("cache:'no-store'", self.html)

    def test_question_history_delete_updates_the_visible_list_immediately(self):
        self.assertIn('data-question-history-id="${esc(item.id || \'\')}"', self.html)
        self.assertIn('data-history-id="${esc(item.id || \'\')}"', self.html)
        self.assertIn("deleteQuestionHistory(this.dataset.historyId)", self.html)
        self.assertIn("currentQuestions.find(candidate => candidate.id === historyId)", self.html)
        self.assertIn("removeQuestionHistoryFromView(item.id);", self.html)
        self.assertIn("function removeQuestionHistoryFromView(historyId)", self.html)
        self.assertIn("totalQuestionCount = Math.max(0, totalQuestionCount - 1);", self.html)

    def test_delete_all_clears_count_list_and_button(self):
        self.assertIn('id="questionHistoryTotal"', self.html)
        self.assertIn('id="questionHistoryList"', self.html)
        self.assertIn('id="deleteAllQuestionHistoryBtn"', self.html)
        self.assertIn("if(allButton) allButton.remove();", self.html)

    def test_delete_all_button_names_its_actual_scope(self):
        # "전체 삭제" alone doesn't say what's being deleted; the button and its
        # confirm text must both name the target (설명 기록 + AI 피드백).
        self.assertIn('id="deleteAllMemoriesBtn"', self.html)
        self.assertIn(">내 설명 기록 전체 삭제<", self.html)
        self.assertNotIn('>전체 삭제<', self.html)
        self.assertIn(
            "내 설명 기록 ${totalMemoryCount}개와 연결된 AI 피드백을 모두 삭제합니다. "
            "이 작업은 되돌릴 수 없습니다.",
            self.html,
        )
        self.assertIn(
            "선택한 설명 기록 ${ids.length}개와 연결된 AI 피드백을 삭제합니다. "
            "이 작업은 되돌릴 수 없습니다.",
            self.html,
        )

    def test_empty_delete_targets_show_guidance_not_a_silent_no_op(self):
        self.assertIn("alert('삭제할 설명 기록이 없습니다.')", self.html)

    def test_memory_deletes_use_post_or_bodyless_delete_not_delete_with_body(self):
        # DELETE-with-JSON-body isn't reliably delivered by every WebView
        # (Tauri's WKWebView included), so the new UI avoids it entirely.
        self.assertIn("await deleteJSON('/learning-memory/all')", self.html)
        self.assertIn("await postJSON('/learning-memory/delete', { ids })", self.html)
        self.assertNotIn("deleteJSON('/learning-memory', { delete_all:true })", self.html)
        self.assertNotIn("deleteJSON('/learning-memory', { ids })", self.html)

    def test_memory_delete_buttons_guard_against_duplicate_requests(self):
        self.assertIn("let memoryDeleteInFlight = false;", self.html)
        self.assertIn("if(memoryDeleteInFlight) return;", self.html)
        self.assertIn("memoryDeleteInFlight = true;", self.html)
        self.assertIn("memoryDeleteInFlight = false;", self.html)
        self.assertIn("btn.disabled = true;", self.html)


if __name__ == "__main__":
    unittest.main()
