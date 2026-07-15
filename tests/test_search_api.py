import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_server


class SearchApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="linknote-search-api-", dir="/tmp")
        self.original_paths = {
            "SEARCH_CACHE_PATH": api_server.SEARCH_CACHE_PATH,
            "SEARCH_EVENTS_PATH": api_server.SEARCH_EVENTS_PATH,
            "SEARCH_PROFILES_PATH": api_server.SEARCH_PROFILES_PATH,
        }
        for name in self.original_paths:
            setattr(api_server, name, os.path.join(self.temp_dir, name.lower() + ".json"))

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(api_server, name, value)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_response_exposes_intent_scope_and_algorithm_metadata(self):
        request = api_server.AskSearchRequest(question="심부전이 뭐야?", scope="auto", limit=3)
        with patch.object(api_server, "_iter_user_concepts_with_context", return_value=[]), \
                patch.object(api_server, "_load_recall_traces", return_value=[]), \
                patch.object(api_server, "_search_sources", return_value=([], True)):
            result = api_server._build_search_only_response("user-1", request)
        self.assertEqual(result["intent"], "definition")
        self.assertEqual(result["scope"], "multi")
        self.assertEqual(result["algorithm_version"], "hybrid_personalized_v1")
        self.assertTrue(result["semantic_search_used"])
        self.assertTrue(result["search_id"])

    def test_search_event_updates_only_authenticated_user_profile(self):
        payload = api_server.SearchEventCreate(
            search_id="search-1",
            event_type="helpful",
            course="병태생리학1",
            concept="심부전",
            question="심부전 복습",
        )
        api_server._record_search_event("user-1", payload)
        profile = api_server._search_profile("user-1")
        self.assertEqual(profile["course_counts"]["병태생리학1"], 1)
        self.assertEqual(profile["concept_counts"]["심부전"], 1)
        self.assertEqual(api_server._search_profile("user-2"), {})

    def test_user_alias_is_saved_without_cross_user_leakage(self):
        saved = api_server._save_search_alias("user-1", "죽상경화증", "atherosclerosis")
        self.assertIn("atherosclerosis", saved["aliases"])
        self.assertIn("죽상경화증", api_server._search_profile("user-1")["aliases"])
        self.assertEqual(api_server._search_profile("user-2"), {})


if __name__ == "__main__":
    unittest.main()
