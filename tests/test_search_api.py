import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_server
import rag


class SearchApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="linknote-search-api-", dir="/tmp")
        self.original_paths = {
            "SEARCH_CACHE_PATH": api_server.SEARCH_CACHE_PATH,
            "SEARCH_EVENTS_PATH": api_server.SEARCH_EVENTS_PATH,
            "SEARCH_PROFILES_PATH": api_server.SEARCH_PROFILES_PATH,
            "CONCEPT_NOTES_PATH": api_server.CONCEPT_NOTES_PATH,
            "LECTURE_NOTES_PATH": api_server.LECTURE_NOTES_PATH,
            "CONCEPTS_PATH": api_server.CONCEPTS_PATH,
        }
        self.original_upload_dir = api_server.UPLOAD_DIR
        for name in self.original_paths:
            setattr(api_server, name, os.path.join(self.temp_dir, name.lower() + ".json"))
        api_server.UPLOAD_DIR = self.temp_dir

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(api_server, name, value)
        api_server.UPLOAD_DIR = self.original_upload_dir
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

    def test_lecture_note_upsert_is_scoped_to_page_range(self):
        payload = self._lecture_note_payload()
        first = api_server._upsert_lecture_note("user-1", payload)
        payload["note_text"] = "교수님 강조 내용\n시험에 출제"
        second = api_server._upsert_lecture_note("user-1", payload)
        other_range = api_server._upsert_lecture_note(
            "user-1", self._lecture_note_payload(start_page=8, end_page=10)
        )

        notes = api_server._get_lecture_notes_for_user("user-1")
        self.assertEqual(len(notes), 2)
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(second["id"], other_range["id"])
        self.assertEqual(second["note_text"], "교수님 강조 내용\n시험에 출제")

    def test_lecture_notes_are_user_isolated_and_filterable(self):
        note = api_server._upsert_lecture_note("user-1", self._lecture_note_payload())
        api_server._upsert_lecture_note(
            "user-1", self._lecture_note_payload(course="약리학", filename="약리.pdf")
        )
        self.assertEqual(api_server._get_lecture_notes_for_user("user-2"), [])
        self.assertEqual(
            len(api_server._get_lecture_notes_for_user("user-1", {"course": "병태생리학"})),
            1,
        )
        self.assertFalse(api_server._delete_lecture_note_for_user("user-2", note["id"]))
        self.assertTrue(api_server._delete_lecture_note_for_user("user-1", note["id"]))

    def test_lecture_note_normalizes_tags_and_rejects_invalid_range(self):
        note = api_server._upsert_lecture_note(
            "user-1",
            self._lecture_note_payload(tags=["question", "exam", "unknown", "EXAM"]),
        )
        self.assertEqual(note["tags"], ["exam", "question"])
        with self.assertRaises(api_server.HTTPException) as context:
            api_server._upsert_lecture_note(
                "user-1", self._lecture_note_payload(start_page=5, end_page=4)
            )
        self.assertEqual(context.exception.status_code, 400)

    def test_page_locations_merge_all_pages_and_ignore_invalid_pages(self):
        concepts = [{
            "name": "염증 매개물질",
            "keyword": "화학매개물질",
            "aliases": ["chemical mediator"],
            "occurrences": [
                {"filename": "염증.pptx", "page": 1, "surface": "화학매개물질"},
                {"filename": "염증.pptx", "page": "bad"},
                {"filename": "염증.pptx", "page": 0},
            ],
        }]
        chunks = [
            {"filename": "염증.pptx", "page": 1, "text": "목차: 화학매개물질"},
            {"filename": "염증.pptx", "page": 8, "text": "염증 반응에서 화학매개물질"},
            {"filename": "염증.pptx", "page": 12, "text": "Chemical Mediator의 작용"},
            {"filename": "염증.pptx", "page": "bad", "text": "화학매개물질"},
        ]
        with patch.object(api_server, "concept_source_chunks_for_unit", return_value=chunks):
            result = api_server._augment_concepts_with_page_locations(
                concepts, "user-1", "2026-1", "병태생리학", "염증과 치유"
            )
        self.assertEqual(result[0]["pages"], [1, 8, 12])
        self.assertEqual(result[0]["occurrences"][0]["surface"], "화학매개물질")

    def test_study_workspace_returns_ordered_pages_concepts_and_note(self):
        scope = {
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "순환계",
            "filename": "순환계.pdf",
        }
        stored_name = "owned_순환계.pdf"
        stored_path = os.path.join(self.temp_dir, stored_name)
        with open(stored_path, "wb") as source_file:
            source_file.write(b"pdf")
        chunks = [
            {**scope, "page": 10, "chunk_index": 0, "text": "C10", "stored_filename": stored_name},
            {**scope, "page": 2, "chunk_index": 1, "text": "C2 " + "B" * 400, "stored_filename": stored_name},
            {**scope, "page": 2, "chunk_index": "bad", "text": "C2 " + "A" * 400, "stored_filename": stored_name},
            {**scope, "page": "bad", "chunk_index": 0, "text": "ignored", "stored_filename": stored_name},
        ]
        concepts_store = {
            "user-1": {"2026-1": {"병태생리학": {"순환계": [
                {"name": "C10", "keyword": "C10", "occurrences": []},
                {"name": "C2", "keyword": "C2", "occurrences": []},
            ]}}}
        }
        api_server._save_json_file(api_server.CONCEPTS_PATH, concepts_store)
        api_server._upsert_concept_note("user-1", {
            **scope,
            "concept": "C2",
            "note_text": "내 노트",
            "source_pages": [2],
        })

        def owned_chunks(user_id=None, search_filter=None, **_kwargs):
            self.assertEqual(user_id, "user-1")
            self.assertEqual(search_filter, scope)
            return {"items": chunks, "total": len(chunks)}

        with patch.object(api_server, "get_chunks", side_effect=owned_chunks), \
                patch.object(api_server, "concept_source_chunks_for_unit", return_value=chunks):
            result = asyncio.run(api_server.study_workspace(**scope, data_user_id="user-1"))

        self.assertEqual([page["page"] for page in result["pages"]], [2, 10])
        self.assertEqual(len(result["pages"][0]["text_preview"]), 700)
        self.assertEqual([concept["name"] for concept in result["concepts"]], ["C2", "C10"])
        self.assertEqual(result["concepts"][0]["pages"], [2])
        self.assertEqual(result["concepts"][0]["note"]["note_text"], "내 노트")
        self.assertEqual(result["pages"][0]["concepts"], ["C2"])
        self.assertTrue(result["source_available"])

    def test_study_workspace_rejects_wrong_scope_with_same_filename(self):
        with patch.object(api_server, "get_chunks", return_value={"items": [], "total": 0}):
            with self.assertRaises(api_server.HTTPException) as context:
                asyncio.run(api_server.study_workspace(
                    "2026-1", "다른 과목", "다른 단원", "same.pdf", data_user_id="user-1"
                ))
        self.assertEqual(context.exception.status_code, 404)

    def test_resolve_owned_upload_path_uses_real_owned_stored_file(self):
        stored_name = "stored_f.pdf"
        stored_path = os.path.join(self.temp_dir, stored_name)
        with open(stored_path, "wb") as source_file:
            source_file.write(b"owned")

        def exact_chunks(user_id=None, search_filter=None, **_kwargs):
            if user_id == "user-1" and search_filter == {
                "filename": "f.pdf", "semester": "s", "course": "c", "unit": "u"
            }:
                return {"items": [{"stored_filename": stored_name}], "total": 1}
            return {"items": [], "total": 0}

        with patch.object(api_server, "get_chunks", side_effect=exact_chunks):
            resolved = api_server._resolve_owned_upload_path("user-1", "f.pdf", "s", "c", "u")
            wrong_user = api_server._resolve_owned_upload_path("user-2", "f.pdf", "s", "c", "u")
            wrong_scope = api_server._resolve_owned_upload_path("user-1", "f.pdf", "wrong", "c", "u")
        self.assertEqual(os.path.realpath(resolved), os.path.realpath(stored_path))
        self.assertIsNone(wrong_user)
        self.assertIsNone(wrong_scope)

    def test_resolve_owned_upload_path_never_uses_unattributed_legacy_match(self):
        other_path = os.path.join(self.temp_dir, "other_g.pdf")
        with open(other_path, "wb") as source_file:
            source_file.write(b"other")
        with patch.object(api_server, "get_chunks", return_value={
            "items": [{"stored_filename": "missing_g.pdf"}], "total": 1
        }):
            missing_owned = api_server._resolve_owned_upload_path("user-1", "g.pdf", "s", "c", "u")
        with patch.object(api_server, "get_chunks", return_value={"items": [{}], "total": 1}):
            legacy = api_server._resolve_owned_upload_path("user-1", "g.pdf", "s", "c", "u")
        self.assertIsNone(missing_owned)
        self.assertIsNone(legacy)

    def test_ingest_metadata_records_stored_filename_and_unit_filter(self):
        captured = {}
        with patch.object(rag, "embed_text", return_value=[0.1, 0.2]), \
                patch.object(rag.collection, "upsert", side_effect=lambda **kwargs: captured.update(kwargs)):
            rag.add_pdf_pages_to_db(
                pages=[{"page": 1, "text": "본문"}],
                filename="자료.pdf",
                semester="2026-1",
                course="간호학",
                title="자료",
                user_id="user-1",
                unit="단원",
                stored_filename="uuid_자료.pdf",
            )
        self.assertEqual(captured["metadatas"][0]["stored_filename"], "uuid_자료.pdf")
        where_filter = rag._build_where_filter(
            {"semester": "2026-1", "course": "간호학", "unit": "단원", "filename": "자료.pdf"},
            user_id="user-1",
        )
        self.assertIn({"unit": "단원"}, where_filter["$and"])

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

    @staticmethod
    def _lecture_note_payload(**overrides):
        payload = {
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "염증과 치유",
            "filename": "염증과 치유.pdf",
            "start_page": 3,
            "end_page": 7,
            "note_text": "수업 필기",
            "tags": ["important"],
        }
        payload.update(overrides)
        return payload


if __name__ == "__main__":
    unittest.main()
