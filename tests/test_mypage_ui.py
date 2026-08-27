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


if __name__ == "__main__":
    unittest.main()
