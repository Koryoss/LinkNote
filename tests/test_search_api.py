import asyncio
import os
import asyncio
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_server
from fastapi import Response
import rag


class SearchApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="linknote-search-api-", dir="/tmp")
        self.original_paths = {
            "SEARCH_CACHE_PATH": api_server.SEARCH_CACHE_PATH,
            "SEARCH_EVENTS_PATH": api_server.SEARCH_EVENTS_PATH,
            "SEARCH_PROFILES_PATH": api_server.SEARCH_PROFILES_PATH,
            "QUESTION_HISTORY_PATH": api_server.QUESTION_HISTORY_PATH,
            "LECTURE_NOTES_PATH": api_server.LECTURE_NOTES_PATH,
            "CONCEPTS_PATH": api_server.CONCEPTS_PATH,
            "UPLOAD_EVENTS_PATH": api_server.UPLOAD_EVENTS_PATH,
            "CONCEPT_NOTES_PATH": api_server.CONCEPT_NOTES_PATH,
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
        self.assertEqual(result["algorithm_version"], "hybrid_personalized_v5")
        self.assertTrue(result["semantic_search_used"])
        self.assertTrue(result["search_id"])

    def test_related_concept_exposes_grounded_explanation_instead_of_match_reason(self):
        concepts = [{
            "name": "글리코겐 합성효소 키나제 3",
            "keyword": "GSK-3",
            "aliases": ["glycogen synthase kinase 3"],
            "definition": "리튬의 작용과 관련된 세포 내 신호전달 효소입니다.",
            "semester": "2026-1",
            "course": "정신약물의 이해",
            "unit": "기분조절제",
            "filename": "mood.pdf",
            "pages": [18],
        }]
        result = api_server._search_related_concepts(
            "user-1", ["kinase", "키나제"], {}, "multi", 3,
            concepts, {}, {},
        )
        self.assertEqual(result[0]["explanation"], "리튬의 작용과 관련된 세포 내 신호전달 효소입니다.")
        self.assertEqual(result[0]["filename"], "mood.pdf")
        self.assertEqual(result[0]["page"], 18)

    def test_semantic_only_source_below_strict_cutoff_is_excluded(self):
        chunks = {"items": [{
            "id": "irrelevant-1", "semester": "2026-1", "course": "인간관계와의사소통",
            "unit": "심리학적 유형", "filename": "mbti.pdf", "page": 13,
            "text": "Jung의 심리학적 유형과 MBTI의 역사",
        }]}
        with patch.object(api_server, "get_chunks", return_value=chunks), \
                patch.object(api_server, "search_relevant_chunks", return_value=[{
                    "id": "irrelevant-1", "distance": (1 / 0.75) - 1,
                }]):
            sources, semantic_used = api_server._search_sources(
                "user-1", "kinase 관련 설명 찾아줘", ["kinase"], {}, "multi", 6, [], {}, {},
            )
        self.assertTrue(semantic_used)
        self.assertEqual(sources, [])

    def test_compound_concept_does_not_expand_one_word_to_neighbouring_terms(self):
        concepts = [{
            "name": "ERK 신호전달 경로",
            "keyword": "ERK pathway",
            "aliases": ["extracellular signal-regulated kinase"],
        }]
        aliases = api_server._build_user_alias_map("user-1", concepts, {})
        self.assertNotIn("kinase", aliases)

    def test_single_term_alias_still_expands_to_its_equivalent(self):
        concepts = [{"name": "죽상경화증", "aliases": ["atherosclerosis"]}]
        aliases = api_server._build_user_alias_map("user-1", concepts, {})
        self.assertIn("atherosclerosis", aliases["죽상경화증"])
        self.assertIn("죽상경화증", aliases["atherosclerosis"])

    def test_missing_definition_uses_matching_pdf_excerpt(self):
        concepts = [{
            "concept": "BCR-ABL 티로신 키나제", "course": "약물기전과효과", "unit": "항암제",
            "filename": "40-48장.pdf", "page": 38, "explanation": "", "links": [],
        }]
        sources = [{
            "filename": "40-48장.pdf", "page": 38, "score": 82.0,
            "matched_concepts": ["BCR-ABL 티로신 키나제"],
            "chunk_preview": "BCR-ABL 티로신 키나제는 만성골수성백혈병의 표적입니다.",
        }]
        enriched = api_server._attach_search_concept_explanations(concepts, sources)
        self.assertEqual(enriched[0]["explanation_kind"], "source_excerpt")
        self.assertIn("만성골수성백혈병", enriched[0]["explanation"])

    def test_blank_upload_unit_falls_back_to_pdf_filename(self):
        self.assertEqual(
            api_server._effective_unit_name("", "1주차 정보기술의 간호 도입 최신 동향.pdf"),
            "1주차 정보기술의 간호 도입 최신 동향",
        )
        self.assertEqual(api_server._effective_unit_name("직접 입력 단원", "자료.pdf"), "직접 입력 단원")

    def test_library_semesters_are_sorted_latest_first(self):
        result = api_server._normalize_library_overview({
            "total_chunks": 0,
            "semesters": {"2025-2": {}, "2026-1": {}, "2026-2": {}},
        })
        self.assertEqual([item.semester for item in result.semesters], ["2026-2", "2026-1", "2025-2"])

    def test_identical_legacy_upload_duplicates_are_safe_to_preview(self):
        first = os.path.join(self.temp_dir, "first_1-7강.pdf")
        second = os.path.join(self.temp_dir, "second_1-7강.pdf")
        with open(first, "wb") as file:
            file.write(b"same pdf bytes")
        with open(second, "wb") as file:
            file.write(b"same pdf bytes")

        self.assertEqual(api_server._pick_unambiguous_upload_path([second, first]), first)

    def test_different_uploads_with_the_same_display_name_stay_ambiguous(self):
        first = os.path.join(self.temp_dir, "first_1-7강.pdf")
        second = os.path.join(self.temp_dir, "second_1-7강.pdf")
        with open(first, "wb") as file:
            file.write(b"course a")
        with open(second, "wb") as file:
            file.write(b"course b")

        self.assertIsNone(api_server._pick_unambiguous_upload_path([first, second]))

    def test_upload_events_are_persisted_with_user_scope(self):
        api_server._record_upload_event(
            "user-1", "의약화학.pdf", "2026-2", "의약화학의 기초", "1주차", "received"
        )
        events = api_server._load_json_file(api_server.UPLOAD_EVENTS_PATH)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["user_id"], "user-1")
        self.assertEqual(events[0]["course"], "의약화학의 기초")

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

    def test_batch_indexes_two_files_then_extracts_same_unit_once(self):
        prepared = [
            {"filename": "a.pdf", "stored_filename": "one_a.pdf", "destination": "/tmp/one_a.pdf"},
            {"filename": "b.pdf", "stored_filename": "two_b.pdf", "destination": "/tmp/two_b.pdf"},
        ]
        with patch.object(api_server, "_update_ingest_job"), \
                patch.object(api_server, "_record_upload_event"), \
                patch.object(api_server, "extract_pdf_text", return_value=[{"page": 1, "text": "본문"}]), \
                patch.object(api_server, "add_pdf_pages_to_db") as add_pages, \
                patch.object(api_server, "build_concepts_for_unit", return_value=[{"name": "서맥"}]) as concepts, \
                patch.object(api_server, "_invalidate_search_cache_for_user"), \
                patch.object(api_server, "_load_json_file", return_value={}), \
                patch.object(api_server, "_save_json_file"):
            api_server._process_ingest_batch_job(
                "job", "user", "2026-2", "약리학", "", "공통 단원", prepared
            )

        self.assertEqual(add_pages.call_count, 2)
        concepts.assert_called_once_with("user", "2026-2", "약리학", "공통 단원")

    def test_search_cache_can_be_invalidated_for_only_one_user(self):
        api_server._save_search_cache({
            "a": {"user_id": "user-1", "result": {}},
            "b": {"user_id": "user-2", "result": {}},
        })
        self.assertEqual(api_server._invalidate_search_cache_for_user("user-1"), 1)
        self.assertNotIn("a", api_server._load_search_cache())
        self.assertIn("b", api_server._load_search_cache())

    def test_concept_unit_key_move_does_not_overwrite_target(self):
        api_server._save_json_file(api_server.CONCEPTS_PATH, {
            "user-1": {"2026-1": {"병태생리학": {
                "예전 단원": [{"name": "서맥"}],
            }}},
        })
        moved = api_server._move_concept_unit_key(
            "user-1", "2026-1", "병태생리학", "예전 단원", "현재 단원"
        )
        self.assertEqual(moved, {"status": "moved", "concept_count": 1})
        units = api_server._load_json_file(api_server.CONCEPTS_PATH)["user-1"]["2026-1"]["병태생리학"]
        self.assertNotIn("예전 단원", units)
        self.assertEqual(units["현재 단원"], [{"name": "서맥"}])

        units["다음 단원"] = [{"name": "빈맥"}]
        api_server._save_json_file(api_server.CONCEPTS_PATH, {
            "user-1": {"2026-1": {"병태생리학": units}},
        })
        conflict = api_server._move_concept_unit_key(
            "user-1", "2026-1", "병태생리학", "현재 단원", "다음 단원"
        )
        self.assertEqual(conflict["status"], "target_conflict")
        preserved = api_server._load_json_file(api_server.CONCEPTS_PATH)["user-1"]["2026-1"]["병태생리학"]
        self.assertEqual(preserved["현재 단원"], [{"name": "서맥"}])
        self.assertEqual(preserved["다음 단원"], [{"name": "빈맥"}])

    def test_user_alias_is_saved_without_cross_user_leakage(self):
        saved = api_server._save_search_alias("user-1", "죽상경화증", "atherosclerosis")
        self.assertIn("atherosclerosis", saved["aliases"])
        self.assertIn("죽상경화증", api_server._search_profile("user-1")["aliases"])
        self.assertEqual(api_server._search_profile("user-2"), {})

    def test_concept_page_locations_merge_saved_evidence_with_every_matching_page(self):
        concepts = [{
            "name": "염증 매개물질",
            "keyword": "화학매개물질",
            "aliases": ["chemical mediator"],
            "occurrences": [{"filename": "염증.pptx", "page": 1, "surface": "화학매개물질"}],
        }]
        chunks = [
            {"filename": "염증.pptx", "page": 1, "text": "목차: 화학매개물질"},
            {"filename": "염증.pptx", "page": 8, "text": "염증 반응에서 화학매개물질"},
            {"filename": "염증.pptx", "page": 12, "text": "Chemical Mediator의 작용"},
        ]

        with patch.object(api_server, "concept_source_chunks_for_unit", return_value=chunks):
            result = api_server._augment_concepts_with_page_locations(
                concepts, "user-1", "2026-1", "병태생리학 1", "염증과 치유"
            )

        self.assertEqual(result[0]["pages"], [1, 8, 12])
        self.assertEqual([item["page"] for item in result[0]["occurrences"]], [1, 8, 12])
        self.assertEqual(result[0]["occurrences"][0]["surface"], "화학매개물질")

    def test_augment_concepts_filters_invalid_pages_and_keeps_positive_integers(self):
        concepts = [{
            "name": "TestConcept",
            "keyword": "TestConcept",
            "occurrences": [
                {"filename": "f.pdf", "page": "bad"},
                {"filename": "f.pdf", "page": None},
                {"filename": "f.pdf", "page": 0},
                {"filename": "f.pdf", "page": -3},
                {"filename": "f.pdf", "page": 5},
            ],
        }]
        # no discovered occurrences
        with patch.object(api_server, "concept_source_chunks_for_unit", return_value=[]):
            result = api_server._augment_concepts_with_page_locations(concepts, "user-1", "s", "c", "u")
        self.assertEqual(len(result), 1)
        occs = result[0].get("occurrences") or []
        self.assertEqual([o.get("page") for o in occs], [5])

    def test_study_workspace_returns_pages_and_concepts_and_source_flag(self):
        user = "user-1"
        semester = "2026-1"
        course = "병태생리학"
        unit = "순환계"
        filename = "순환계.pdf"

        # chunks representing two pages
        chunks = [
            {"filename": filename, "page": 1, "chunk_index": 0, "text": "서론: 심부전의 정의"},
            {"filename": filename, "page": 2, "chunk_index": 0, "text": "심부전의 병태생리"},
        ]

        # concept source chunks for augmenting occurrences
        source_chunks = list(chunks)

        # a simple concept that appears on page 2
        concepts_store = {
            user: {
                semester: {
                    course: {
                        unit: [
                            {"name": "심부전", "keyword": "심부전", "definition": "심장의 펌프 기능 저하", "occurrences": []}
                        ]
                    }
                }
            }
        }

        with patch.object(api_server, "get_chunks", return_value={"items": chunks, "total": len(chunks)}), \
             patch.object(api_server, "concept_source_chunks_for_unit", return_value=source_chunks), \
             patch.object(api_server, "_load_json_file", return_value=concepts_store), \
             patch.object(api_server, "_matching_upload_paths", return_value=[]), \
             patch.object(api_server, "_pick_unambiguous_upload_path", return_value=None):
            import asyncio
            resp = asyncio.run(api_server.study_workspace(semester, course, unit, filename, data_user_id=user))

        self.assertIn("scope", resp)
        self.assertEqual(resp["scope"]["filename"], filename)
        self.assertIn("pages", resp)
        self.assertEqual([p["page"] for p in resp["pages"]], [1, 2])
        self.assertIn("concepts", resp)
        # concept should be returned only if occurrences are detected in this file
        self.assertTrue(isinstance(resp["concepts"], list))
        self.assertIn("source_available", resp)
        self.assertFalse(resp["source_available"])

    def test_study_workspace_requires_owned_chunks(self):
        user = "user-1"
        with patch.object(api_server, "get_chunks", return_value={"items": [], "total": 0}):
            import asyncio
            with self.assertRaises(api_server.HTTPException) as ctx:
                asyncio.run(api_server.study_workspace("2026-1", "병태생리학", "순환계", "nofile.pdf", data_user_id=user))
            self.assertEqual(ctx.exception.status_code, 404)

    def test_study_workspace_merges_chunks_and_limits_preview_to_700(self):
        user = "user-1"
        semester = "2026-1"
        course = "과목"
        unit = "단원"
        filename = "big.pdf"
        part1 = "A" * 400
        part2 = "B" * 400
        chunks = [
            {"filename": filename, "page": 1, "chunk_index": 0, "chunk_preview": part1},
            {"filename": filename, "page": 1, "chunk_index": 1, "chunk_preview": part2},
        ]
        source_chunks = list(chunks)
        concepts_store = {
            user: {semester: {course: {unit: [{"name": "BigConcept", "keyword": "BigConcept", "occurrences": []}]}}}
        }
        with patch.object(api_server, "get_chunks", return_value={"items": chunks, "total": len(chunks)}), \
             patch.object(api_server, "concept_source_chunks_for_unit", return_value=source_chunks), \
             patch.object(api_server, "_load_json_file", return_value=concepts_store), \
             patch.object(api_server, "_matching_upload_paths", return_value=[]), \
             patch.object(api_server, "_pick_unambiguous_upload_path", return_value=None), \
             patch.object(api_server, "generate_openai_answer") as gen_openai, \
             patch.object(api_server, "_save_json_file") as save_json:
            import asyncio
            resp = asyncio.run(api_server.study_workspace(semester, course, unit, filename, data_user_id=user))
        # merged preview should be truncated to 700
        self.assertIn("pages", resp)
        self.assertEqual(len(resp["pages"][0]["text_preview"]), 700)
        gen_openai.assert_not_called()
        save_json.assert_not_called()

    def test_study_workspace_sorts_pages_and_handles_invalid_page_and_chunk_index(self):
        user = "user-1"
        semester = "2026-1"
        course = "과목"
        unit = "단원"
        filename = "mix.pdf"
        chunks = [
            {"filename": filename, "page": 10, "chunk_index": 2, "text": "Page10"},
            {"filename": filename, "page": 2, "chunk_index": 1, "text": "Page2"},
            {"filename": filename, "page": "x", "chunk_index": 0, "text": "BadPage"},
            {"filename": filename, "page": 0, "chunk_index": 0, "text": "ZeroPage"},
            {"filename": filename, "page": -5, "chunk_index": -1, "text": "NegPage"},
            {"filename": filename, "page": 2, "chunk_index": "bad", "text": "BadIndex"},
        ]
        source_chunks = list(chunks)
        concepts_store = {user: {semester: {course: {unit: []}}}}
        with patch.object(api_server, "get_chunks", return_value={"items": chunks, "total": len(chunks)}), \
             patch.object(api_server, "concept_source_chunks_for_unit", return_value=source_chunks), \
             patch.object(api_server, "_load_json_file", return_value=concepts_store), \
             patch.object(api_server, "_matching_upload_paths", return_value=[]), \
             patch.object(api_server, "_pick_unambiguous_upload_path", return_value=None):
            import asyncio
            resp = asyncio.run(api_server.study_workspace(semester, course, unit, filename, data_user_id=user))
        # only pages 2 and 10 should remain, in numeric order
        self.assertEqual([p["page"] for p in resp["pages"]], [2, 10])
        # ensure BadIndex did not cause 500 and ordering used safe default (BadIndex -> 0)
        self.assertTrue(any("Page2" in p["text_preview"] for p in resp["pages"]))

    def test_study_workspace_concepts_first_page_sort_and_notes_and_user_isolation(self):
        user1 = "user-1"
        user2 = "user-2"
        semester = "2026-1"
        course = "과목"
        unit = "단원"
        filename = "file.pdf"
        # chunks for user1
        chunks = [
            {"filename": filename, "page": 2, "chunk_index": 0, "text": "content p2"},
            {"filename": filename, "page": 10, "chunk_index": 0, "text": "content p10"},
        ]
        # concepts file contains two concepts, one with occurrences on page 10 and one on page 2
        concepts_store = {
            user1: {
                semester: {
                    course: {
                        unit: [
                            {"name": "C10", "keyword": "C10", "occurrences": [{"filename": filename, "page": 10}]},
                            {"name": "C2", "keyword": "C2", "occurrences": [{"filename": filename, "page": 2}]},
                        ]
                    }
                }
            },
            user2: {
                semester: {
                    course: {
                        unit: [
                            {"name": "Other", "keyword": "Other", "occurrences": [{"filename": filename, "page": 1}]}
                        ]
                    }
                }
            }
        }
        # create a concept note for C2 for user1
        note_payload = {"semester": semester, "course": course, "unit": unit, "filename": filename, "concept": "C2", "note_text": "note1", "source_pages": [2]}
        api_server._upsert_concept_note(user1, note_payload)

        original_loader = api_server._load_json_file
        def _load_json_side(path):
            if path == api_server.CONCEPTS_PATH:
                return concepts_store
            return original_loader(path)
        with patch.object(api_server, "get_chunks", return_value={"items": chunks, "total": len(chunks)}), \
             patch.object(api_server, "concept_source_chunks_for_unit", return_value=chunks), \
             patch.object(api_server, "_load_json_file", side_effect=_load_json_side), \
             patch.object(api_server, "_matching_upload_paths", return_value=[]), \
             patch.object(api_server, "_pick_unambiguous_upload_path", return_value=None):
            import asyncio
            resp = asyncio.run(api_server.study_workspace(semester, course, unit, filename, data_user_id=user1))

        # concepts should be sorted by first_page (2 then 10)
        self.assertEqual([c["name"] for c in resp["concepts"]], ["C2", "C10"])
        # pages[].concepts should list associated concept names
        pages_map = {p["page"]: p for p in resp["pages"]}
        self.assertIn("C2", pages_map[2]["concepts"])
        self.assertIn("C10", pages_map[10]["concepts"])
        # concept note included for C2
        c2 = next(c for c in resp["concepts"] if c["name"] == "C2")
        self.assertIsNotNone(c2.get("note"))
        self.assertEqual(c2["note"]["note_text"], "note1")

    def test_study_workspace_returns_404_for_wrong_scope_with_same_filename(self):
        user = "user-1"
        with patch.object(api_server, "get_chunks", return_value={"items": [], "total": 0}):
            import asyncio
            with self.assertRaises(api_server.HTTPException) as ctx:
                asyncio.run(api_server.study_workspace("2026-1", "과목", "단원", "same.pdf", data_user_id=user))
            self.assertEqual(ctx.exception.status_code, 404)

    def test_openai_and_save_helpers_not_called_for_study_workspace(self):
        user = "user-1"
        semester = "2026-1"
        course = "과목"
        unit = "단원"
        filename = "nofeature.pdf"
        chunks = [{"filename": filename, "page": 1, "chunk_index": 0, "text": "t"}]
        concepts_store = {user: {semester: {course: {unit: []}}}}
        with patch.object(api_server, "get_chunks", return_value={"items": chunks, "total": len(chunks)}), \
             patch.object(api_server, "concept_source_chunks_for_unit", return_value=chunks), \
             patch.object(api_server, "_load_json_file", return_value=concepts_store), \
             patch.object(api_server, "generate_openai_answer") as gen_openai, \
             patch.object(api_server, "_save_json_file") as save_json, \
             patch.object(api_server, "_save_recall_traces") as save_traces:
            import asyncio
            resp = asyncio.run(api_server.study_workspace(semester, course, unit, filename, data_user_id=user))
        gen_openai.assert_not_called()
        save_json.assert_not_called()
        save_traces.assert_not_called()

    def test_serve_file_checks_ownership_and_returns_404_or_file(self):
        token = "token"
        # the simple mocking here only verifies the HTTP layer behavior; ownership logic
        # is verified in a separate dedicated test below that exercises _resolve_owned_upload_path directly.
        with patch.object(api_server, "_uid_from_token", return_value="user-1"), \
             patch.object(api_server, "_resolve_owned_upload_path", return_value=None):
            import asyncio
            with self.assertRaises(api_server.HTTPException) as ctx:
                asyncio.run(api_server.serve_file("nofile.pdf", token=token, semester="s", course="c", unit="u"))
            self.assertEqual(ctx.exception.status_code, 404)

        # when resolve returns an actual file path, FileResponse should be returned
        tempf = os.path.join(self.temp_dir, "f.pdf")
        with open(tempf, "wb") as f:
            f.write(b"pdf")
        with patch.object(api_server, "_uid_from_token", return_value="user-1"), \
             patch.object(api_server, "_resolve_owned_upload_path", return_value=tempf):
            import asyncio
            resp = asyncio.run(api_server.serve_file("f.pdf", token=token, semester="s", course="c", unit="u"))
        # starlette.responses.FileResponse class exposed in module
        self.assertTrue(hasattr(api_server, "FileResponse"))
        self.assertEqual(type(resp).__name__, api_server.FileResponse.__name__)

    def test_resolve_owned_upload_path_with_real_uploads_and_scope(self):
        # Patch UPLOAD_DIR to the temp dir used by the test so files are written/read there
        api_server.UPLOAD_DIR = self.temp_dir

        # create an owned stored file for user-1
        owned_stored = os.path.join(self.temp_dir, 'stored_f.pdf')
        with open(owned_stored, 'wb') as f:
            f.write(b'owned')

        # create another user's upload (should not be considered owned)
        other_stored = os.path.join(self.temp_dir, 'other_f.pdf')
        with open(other_stored, 'wb') as f:
            f.write(b'other')

        def fake_get_chunks(user_id=None, limit=None, offset=None, search_filter=None, full=None):
            # Only return a stored_filename for the exact matching user and full scope
            sf = dict(search_filter or {})
            if user_id == 'user-1' and sf.get('semester') == 's' and sf.get('course') == 'c' and sf.get('unit') == 'u' and sf.get('filename') == 'f.pdf':
                # stored_filename is the base name as stored in DB
                return {'items': [{'stored_filename': 'stored_f.pdf'}], 'total': 1}
            # other scopes: no items
            return {'items': [], 'total': 0}

        with patch.object(api_server, 'get_chunks', side_effect=fake_get_chunks):
            path = api_server._resolve_owned_upload_path(data_user_id='user-1', requested_filename='f.pdf', semester='s', course='c', unit='u')
        # Should return the actual stored file path we created
        self.assertEqual(os.path.realpath(path), os.path.realpath(owned_stored))

        # Wrong user should get None
        with patch.object(api_server, 'get_chunks', side_effect=fake_get_chunks):
            path2 = api_server._resolve_owned_upload_path(data_user_id='user-2', requested_filename='f.pdf', semester='s', course='c', unit='u')
        self.assertIsNone(path2)

        # Wrong semester should get None
        with patch.object(api_server, 'get_chunks', side_effect=fake_get_chunks):
            path3 = api_server._resolve_owned_upload_path(data_user_id='user-1', requested_filename='f.pdf', semester='wrong', course='c', unit='u')
        self.assertIsNone(path3)

        # Now simulate owned chunk referencing a stored filename that does NOT exist and global has other user's file only -> should return None
        def fake_get_chunks_missing(user_id=None, limit=None, offset=None, search_filter=None, full=None):
            sf = dict(search_filter or {})
            if user_id == 'user-1' and sf.get('semester') == 's' and sf.get('course') == 'c' and sf.get('unit') == 'u' and sf.get('filename') == 'g.pdf':
                return {'items': [{'stored_filename': 'missing_f.pdf'}], 'total': 1}
            return {'items': [], 'total': 0}

        # create a global-only file (simulating other user's upload) with display name matching g.pdf via stored filename
        global_only = os.path.join(self.temp_dir, 'user2_g.pdf')
        with open(global_only, 'wb') as f:
            f.write(b'user2')

        with patch.object(api_server, 'get_chunks', side_effect=fake_get_chunks_missing):
            res = api_server._resolve_owned_upload_path(data_user_id='user-1', requested_filename='g.pdf', semester='s', course='c', unit='u')
        self.assertIsNone(res)

    def test_concept_keyword_is_used_as_a_bidirectional_alias(self):
        alias_map = api_server._build_user_alias_map(
            "user-1",
            [{"name": "서맥", "keyword": "Bradycardia"}],
            {},
        )
        self.assertIn("bradycardia", alias_map["서맥"])
        self.assertIn("서맥", alias_map["bradycardia"])

    def test_question_history_is_isolated_by_authenticated_user(self):
        api_server._record_question_history(
            "user-1",
            "심부전이 뭐야?",
            "ai_answer",
            answer="심부전에 대한 답변",
            result_count=2,
        )
        api_server._record_question_history("user-2", "다른 질문", "quick_search")

        items = api_server._question_history_for_user("user-1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["question"], "심부전이 뭐야?")
        self.assertEqual(items[0]["answer"], "심부전에 대한 답변")
        self.assertEqual(items[0]["result_count"], 2)

    def test_question_history_delete_preserves_other_users(self):
        own = api_server._record_question_history("user-1", "내 검색", "quick_search")
        api_server._record_question_history("user-1", "내 질문", "ai_answer")
        api_server._record_question_history("user-2", "다른 사용자 질문", "ai_answer")

        self.assertEqual(api_server._delete_question_history_for_user("user-1", own["id"]), 1)
        self.assertEqual([item["question"] for item in api_server._question_history_for_user("user-1")], ["내 질문"])
        self.assertEqual(api_server._delete_question_history_for_user("user-1"), 1)
        self.assertEqual(api_server._question_history_for_user("user-1"), [])
        self.assertEqual(len(api_server._question_history_for_user("user-2")), 1)

    def test_post_delete_all_question_history_preserves_other_users(self):
        api_server._record_question_history("user-1", "내 검색", "quick_search")
        api_server._record_question_history("user-1", "내 질문", "ai_answer")
        api_server._record_question_history("user-2", "다른 사용자 질문", "ai_answer")

        result = asyncio.run(api_server.post_delete_all_question_history(data_user_id="user-1"))

        self.assertEqual(result, {"ok": True, "deleted": 2})
        self.assertEqual(api_server._question_history_for_user("user-1"), [])
        self.assertEqual(len(api_server._question_history_for_user("user-2")), 1)

    def test_question_history_response_is_never_cached(self):
        api_server._record_question_history("user-1", "내 검색", "quick_search")
        response = Response()

        result = asyncio.run(api_server.question_history(response, limit=30, data_user_id="user-1"))

        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(result["total"], 1)

    def test_question_history_save_failure_is_not_reported_as_success(self):
        with patch("api_server.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(api_server.HTTPException) as raised:
                api_server._record_question_history("user-1", "내 검색", "quick_search")

        self.assertEqual(raised.exception.status_code, 500)

    def test_quick_search_history_summary_keeps_concepts_documents_and_pages(self):
        summary = api_server._search_history_summary({
            "related_concepts": [{"concept": "서맥"}, {"concept": "빈맥"}],
            "sources": [{"filename": "심혈관계 질환 II.pdf", "page": 4}],
            "learning_memory_matches": [{"concept": "동방결절"}],
        })

        self.assertIn("관련 개념: 서맥, 빈맥", summary)
        self.assertIn("관련 문서: 심혈관계 질환 II.pdf p.4", summary)
        self.assertIn("Learning Memory: 동방결절", summary)

    def test_source_search_does_not_reward_a_query_unrelated_concept(self):
        chunk = {
            "id": "chunk-1",
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "순환계",
            "filename": "순환계.pdf",
            "page": 1,
            "chunk_index": 0,
            "text": "고혈압의 정의와 치료를 설명합니다.",
        }
        concepts = [{"name": "고혈압", "course": "병태생리학", "unit": "순환계"}]
        with patch.object(api_server, "get_chunks", return_value={"items": [chunk]}), \
                patch.object(api_server, "search_relevant_chunks", return_value=[
                    {"id": "chunk-1", "distance": 0.8}
                ]):
            sources, _ = api_server._search_sources(
                "user-1", "심부전이 뭐야?", ["심부전"], {}, "multi", 5, concepts, {}, {}
            )
        self.assertEqual(sources, [])

    def test_chunk_preview_focuses_on_the_matching_context(self):
        preview = api_server._focused_chunk_preview(
            "OCR noise\nBradycardia\n분당 60회 미만의 느린 심박동\nTachycardia\n분당 100회 이상의 빠른 심박동",
            ["서맥", "bradycardia"],
        )
        self.assertIn("Bradycardia", preview)
        self.assertIn("분당 60회 미만", preview)
        self.assertNotIn("OCR noise", preview)

    def test_document_source_fills_concept_when_batch_extraction_missed_it(self):
        concepts = api_server._document_backed_concepts(
            ["서맥"],
            {"서맥": ["bradycardia"]},
            [{
                "id": "chunk-1",
                "course": "병태생리학 1",
                "unit": "심장 부정맥, 염증과 감염",
                "chunk_preview": "서맥 (Bradycardia): 분당 60회 미만의 느린 심박동",
                "score": 72.5,
            }],
            5,
        )
        self.assertEqual(len(concepts), 1)
        self.assertEqual(concepts[0]["concept"], "서맥 (Bradycardia)")
        self.assertEqual(concepts[0]["origin"], "document")

    def test_chunk_preview_cleans_bilingual_ocr_definition(self):
        preview = api_server._focused_chunk_preview(
            "A|24(Bradycardia)\n분당609] 미만의느린심박동\n4194(Tachycardia)\n분당100회이상의빠른심박동",
            ["서맥", "bradycardia"],
        )
        self.assertIn("서맥 (Bradycardia)", preview)
        self.assertIn("빈맥 (Tachycardia)", preview)
        self.assertIn("분당 60회 미만의 느린 심박동", preview)
        self.assertIn("분당 100회 이상의 빠른 심박동", preview)
        self.assertNotIn("4194", preview)
        self.assertEqual(
            preview,
            "서맥 (Bradycardia): 분당 60회 미만의 느린 심박동 · "
            "빈맥 (Tachycardia): 분당 100회 이상의 빠른 심박동",
        )

    def test_source_search_keeps_one_definition_focused_result_per_page(self):
        chunks = [
            {
                "id": "chunk-1", "course": "병태생리학", "unit": "부정맥",
                "filename": "심혈관.pdf", "page": 4, "chunk_index": 0,
                "text": "서맥-빈맥증후군과 동정지",
            },
            {
                "id": "chunk-2", "course": "병태생리학", "unit": "부정맥",
                "filename": "심혈관.pdf", "page": 4, "chunk_index": 1,
                "text": "Bradycardia\n분당 60회 미만의 느린 심박동\n증상: 어지러움과 실신",
            },
        ]
        with patch.object(api_server, "get_chunks", return_value={"items": chunks}), \
                patch.object(api_server, "search_relevant_chunks", return_value=[]):
            sources, _ = api_server._search_sources(
                "user-1", "서맥", ["서맥", "bradycardia"], {}, "multi", 5, [], {}, {}
            )
        self.assertEqual(len(sources), 1)
        self.assertIn("분당 60회 미만", sources[0]["chunk_preview"])

    def test_source_search_keeps_direct_query_concept_matches(self):
        chunk = {
            "id": "chunk-1",
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "순환계",
            "filename": "순환계.pdf",
            "page": 1,
            "chunk_index": 0,
            "text": "심부전은 심장이 충분한 혈액을 내보내지 못하는 상태입니다.",
        }
        concepts = [{"name": "심부전", "course": "병태생리학", "unit": "순환계"}]
        with patch.object(api_server, "get_chunks", return_value={"items": [chunk]}), \
                patch.object(api_server, "search_relevant_chunks", return_value=[]):
            sources, _ = api_server._search_sources(
                "user-1", "심부전이 뭐야?", ["심부전"], {}, "multi", 5, concepts, {}, {}
            )
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["relevance_label"], "직접 일치")

    def test_concept_notes_upsert_no_duplicate(self):
        user = "user-1"
        payload = {
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "순환계",
            "filename": "순환계.pdf",
            "concept": "심부전",
            "note_text": "note",
            "source_pages": [2, 1, 2],
        }
        note1 = api_server._upsert_concept_note(user, payload)
        note2 = api_server._upsert_concept_note(user, payload)
        notes = api_server._get_concept_notes_for_user(user)
        self.assertEqual(len(notes), 1)
        self.assertEqual(note1["id"], note2["id"])

    def test_concept_notes_user_isolation_and_delete_permissions(self):
        user1 = "user-1"
        user2 = "user-2"
        payload = {
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "순환계",
            "filename": "순환계.pdf",
            "concept": "심부전",
            "note_text": "u1 note",
            "source_pages": [1, 3],
        }
        note = api_server._upsert_concept_note(user1, payload)
        notes_user2 = api_server._get_concept_notes_for_user(user2)
        self.assertEqual(notes_user2, [])
        deleted = api_server._delete_concept_note_for_user(user2, note["id"])
        self.assertFalse(deleted)
        deleted_owner = api_server._delete_concept_note_for_user(user1, note["id"])
        self.assertTrue(deleted_owner)
        self.assertEqual(api_server._get_concept_notes_for_user(user1), [])

    def test_concept_notes_source_pages_normalization(self):
        user = "user-1"
        payload = {
            "semester": "2026-1",
            "course": "A",
            "unit": "U",
            "filename": "F",
            "concept": "C",
            "note_text": "",
            "source_pages": [3, 3, -1, '2', 0, 5],
        }
        note = api_server._upsert_concept_note(user, payload)
        self.assertEqual(note["source_pages"], [2, 3, 5])
        self.assertEqual(note["note_text"], "")

    def test_concept_notes_preserve_multiline_note_structure(self):
        note = api_server._upsert_concept_note("user-1", {
            "semester": "2026-1",
            "course": "병태생리학",
            "unit": "염증과 치유",
            "filename": "염증과 치유.pdf",
            "concept": "급성 염증",
            "note_text": "  1. 혈관 반응\r\n  - 혈관 확장\r\n\r\n2. 세포 반응  ",
            "source_pages": [3],
        })
        self.assertEqual(
            note["note_text"],
            "1. 혈관 반응\n  - 혈관 확장\n\n2. 세포 반응",
        )


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

    def test_concept_notes_require_complete_identity(self):
        with self.assertRaises(api_server.HTTPException) as context:
            api_server._upsert_concept_note("user-1", {
                "semester": "2026-1",
                "course": "병태생리학",
                "unit": "",
                "filename": "염증과 치유.pdf",
                "concept": "급성 염증",
                "note_text": "",
                "source_pages": [],
            })
        self.assertEqual(context.exception.status_code, 400)

    def test_concept_notes_filtering(self):
        user = "user-1"
        api_server._upsert_concept_note(user, {"semester": "s1", "course": "c1", "unit": "u1", "filename": "f1", "concept": "a", "note_text": "", "source_pages": []})
        api_server._upsert_concept_note(user, {"semester": "s1", "course": "c1", "unit": "u1", "filename": "f2", "concept": "b", "note_text": "", "source_pages": []})
        api_server._upsert_concept_note(user, {"semester": "s2", "course": "c2", "unit": "u2", "filename": "f3", "concept": "a", "note_text": "", "source_pages": []})
        res = api_server._get_concept_notes_for_user(user, {"semester": "s1", "course": "c1"})
        self.assertEqual(len(res), 2)
        res2 = api_server._get_concept_notes_for_user(user, {"concept": "a"})
        self.assertEqual(len(res2), 2)

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

    def test_resolve_owned_upload_path_uses_exact_scope_legacy_upload_event(self):
        legacy_path = os.path.join(self.temp_dir, "legacy_g.pdf")
        with open(legacy_path, "wb") as source_file:
            source_file.write(b"owned legacy source")
        api_server._record_upload_event(
            "user-1", "g.pdf", "s", "c", "u", "complete", pages=3
        )
        legacy_chunks = {"items": [{}], "total": 1}
        with patch.object(api_server, "get_chunks", return_value=legacy_chunks):
            resolved = api_server._resolve_owned_upload_path("user-1", "g.pdf", "s", "c", "u")
            wrong_user = api_server._resolve_owned_upload_path("user-2", "g.pdf", "s", "c", "u")
            wrong_scope = api_server._resolve_owned_upload_path("user-1", "g.pdf", "wrong", "c", "u")
        self.assertEqual(os.path.realpath(resolved), os.path.realpath(legacy_path))
        self.assertIsNone(wrong_user)
        self.assertIsNone(wrong_scope)

    def test_resolve_owned_legacy_upload_keeps_different_files_ambiguous(self):
        for stored_name, content in (("first_g.pdf", b"one"), ("second_g.pdf", b"two")):
            with open(os.path.join(self.temp_dir, stored_name), "wb") as source_file:
                source_file.write(content)
        api_server._record_upload_event("user-1", "g.pdf", "s", "c", "u", "received")
        with patch.object(api_server, "get_chunks", return_value={"items": [{}], "total": 1}):
            resolved = api_server._resolve_owned_upload_path("user-1", "g.pdf", "s", "c", "u")
        self.assertIsNone(resolved)

    def test_ingest_metadata_records_stored_filename_and_unit_filter(self):
        captured = {}
        with patch.object(rag, "embed_texts", return_value=[[0.1, 0.2]]), \
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
