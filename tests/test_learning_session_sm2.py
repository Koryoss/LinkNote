import os
import shutil
import tempfile
import unittest

import api_server


class LearningSessionSM2Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="linknote-tests-", dir="/tmp")
        self.original_data_dir = api_server.DATA_DIR
        self.original_recall_path = api_server.RECALL_TRACES_PATH
        self.original_sessions_path = getattr(api_server, "LEARNING_SESSIONS_PATH", None)
        self.original_schedule_path = getattr(api_server, "REVIEW_SCHEDULE_PATH", None)
        self.original_concept_index_path = api_server.CONCEPT_INDEX_PATH
        self.original_concept_links_path = api_server.CONCEPT_LINKS_PATH

        api_server.DATA_DIR = self.temp_dir
        api_server.RECALL_TRACES_PATH = os.path.join(self.temp_dir, "recall_traces.json")
        api_server.LEARNING_SESSIONS_PATH = os.path.join(self.temp_dir, "learning_sessions.json")
        api_server.REVIEW_SCHEDULE_PATH = os.path.join(self.temp_dir, "review_schedule.json")
        api_server.CONCEPT_INDEX_PATH = os.path.join(self.temp_dir, "concept_index.json")
        api_server.CONCEPT_LINKS_PATH = os.path.join(self.temp_dir, "concept_links.json")

        for path in [
            api_server.RECALL_TRACES_PATH,
            api_server.LEARNING_SESSIONS_PATH,
            api_server.REVIEW_SCHEDULE_PATH,
            api_server.CONCEPT_INDEX_PATH,
            api_server.CONCEPT_LINKS_PATH,
        ]:
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("[]")

    def tearDown(self):
        api_server.DATA_DIR = self.original_data_dir
        api_server.RECALL_TRACES_PATH = self.original_recall_path
        api_server.LEARNING_SESSIONS_PATH = self.original_sessions_path
        api_server.REVIEW_SCHEDULE_PATH = self.original_schedule_path
        api_server.CONCEPT_INDEX_PATH = self.original_concept_index_path
        api_server.CONCEPT_LINKS_PATH = self.original_concept_links_path
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_apply_sm2_schedule_for_quality_2_3_and_5(self):
        entry = {"ease": 2.5, "interval_days": 0, "repetitions": 0, "due_at": None}
        after_quality_2 = api_server._apply_sm2_schedule(entry, 2)
        self.assertEqual(after_quality_2["repetitions"], 0)
        self.assertEqual(after_quality_2["interval_days"], 1)

        after_quality_3 = api_server._apply_sm2_schedule({**entry, "interval_days": 0, "repetitions": 0}, 3)
        self.assertEqual(after_quality_3["repetitions"], 1)
        self.assertEqual(after_quality_3["interval_days"], 1)

        after_quality_5 = api_server._apply_sm2_schedule({"ease": 2.5, "interval_days": 1, "repetitions": 1, "due_at": None}, 5)
        self.assertEqual(after_quality_5["repetitions"], 2)
        self.assertEqual(after_quality_5["interval_days"], 6)

    def test_learning_session_flow_updates_recall_trace_and_schedule(self):
        with open(api_server.CONCEPT_INDEX_PATH, "w", encoding="utf-8") as handle:
            json = __import__("json")
            json.dump([
                {"id": "c1", "name": "신부전", "keyword": "신부전", "course": "병리생리학1", "unit": "신장", "semester": "2026-1", "weight": 1, "user_id": "user-1"},
            ], handle, ensure_ascii=False)

        session = api_server._create_learning_session("user-1", {"course": None, "unit": None}, size=1)
        self.assertEqual(session["cursor"], 0)
        self.assertEqual(len(session["items"]), 1)

        updated = api_server._advance_learning_session(session, "user-1", session["items"][0]["concept_id"], "explained")
        self.assertEqual(updated["items"][0]["status"], "explained")
        self.assertEqual(updated["cursor"], 1)
        self.assertTrue(api_server._load_recall_traces())

        first_grade = api_server._grade_review_for_concept("user-1", "c1", 5)
        self.assertEqual(first_grade["repetitions"], 1)
        self.assertEqual(first_grade["interval_days"], 1)

        second_grade = api_server._grade_review_for_concept("user-1", "c1", 5)
        self.assertEqual(second_grade["repetitions"], 2)
        self.assertEqual(second_grade["interval_days"], 6)


if __name__ == "__main__":
    unittest.main()
