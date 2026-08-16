import asyncio
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
            "CONCEPT_NOTES_PATH": api_server.CONCEPT_NOTES_PATH,
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

    def test_concept_note_upsert_does_not_create_duplicates(self):
        payload = self._concept_note_payload()
        first = api_server._upsert_concept_note("user-1", payload)
        payload["note_text"] = "수정된 노트"
        second = api_server._upsert_concept_note("user-1", payload)

        notes = api_server._get_concept_notes_for_user("user-1")
        self.assertEqual(len(notes), 1)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(notes[0]["note_text"], "수정된 노트")

    def test_concept_notes_are_isolated_by_user(self):
        note = api_server._upsert_concept_note(
            "user-1",
            self._concept_note_payload(),
        )
        self.assertEqual(
            api_server._get_concept_notes_for_user("user-2"),
            [],
        )
        self.assertFalse(
            api_server._delete_concept_note_for_user("user-2", note["id"])
        )
        self.assertEqual(
            api_server._get_concept_notes_for_user("user-1")[0]["id"],
            note["id"],
        )
        with self.assertRaises(api_server.HTTPException) as context:
            asyncio.run(
                api_server.concept_notes_delete(
                    note["id"],
                    data_user_id="user-2",
                )
            )
        self.assertEqual(context.exception.status_code, 404)

    def test_concept_note_source_pages_are_normalized(self):
        payload = self._concept_note_payload()
        payload["source_pages"] = [5, 2, 2, 0, -1, "3", "잘못된 값"]
        note = api_server._upsert_concept_note("user-1", payload)
        self.assertEqual(note["source_pages"], [2, 3, 5])

    def test_concept_note_filters_are_optional_and_composable(self):
        api_server._upsert_concept_note(
            "user-1",
            self._concept_note_payload(concept="급성 염증"),
        )
        api_server._upsert_concept_note(
            "user-1",
            self._concept_note_payload(concept="만성 염증"),
        )
        api_server._upsert_concept_note(
            "user-1",
            self._concept_note_payload(course="약리학", concept="염증 치료"),
        )

        course_notes = api_server._get_concept_notes_for_user(
            "user-1",
            {"course": "병태생리학"},
        )
        concept_notes = api_server._get_concept_notes_for_user(
            "user-1",
            {"course": "병태생리학", "concept": "급성 염증"},
        )
        self.assertEqual(len(course_notes), 2)
        self.assertEqual(len(concept_notes), 1)

    def test_concept_note_preserves_multiline_structure(self):
        payload = self._concept_note_payload()
        payload["note_text"] = (
            "  1. 혈관 반응\r\n"
            "  - 혈관 확장\r\n\r\n"
            "2. 세포 반응  "
        )
        note = api_server._upsert_concept_note("user-1", payload)
        self.assertEqual(
            note["note_text"],
            "1. 혈관 반응\n  - 혈관 확장\n\n2. 세포 반응",
        )

    def test_concept_note_requires_complete_identity(self):
        payload = self._concept_note_payload()
        payload["unit"] = ""
        with self.assertRaises(api_server.HTTPException) as context:
            api_server._upsert_concept_note("user-1", payload)
        self.assertEqual(context.exception.status_code, 400)

    @staticmethod
    def _concept_note_payload(**overrides):
        payload = {
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "염증과 치유",
            "filename": "염증과 치유.pdf",
            "concept": "급성 염증",
            "note_text": "노트",
            "source_pages": [3],
        }
        payload.update(overrides)
        return payload


if __name__ == "__main__":
    unittest.main()
