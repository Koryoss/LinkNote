import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import api_server
from rag import get_filter_label


class StudyApiTests(unittest.TestCase):
    def test_study_source_exposes_exact_owned_navigation_scope(self):
        source = api_server._study_source({
            "title": "감정의 이해",
            "filename": "03주.pdf",
            "stored_filename": "owned_03주.pdf",
            "semester": "2026-2",
            "course": "인간관계와의사소통",
            "unit": "03주 정서지능과 감정이해",
            "page": 7,
            "distance": 0.1,
            "text": "정서지능의 정의",
        })
        self.assertEqual(source["semester"], "2026-2")
        self.assertEqual(source["course"], "인간관계와의사소통")
        self.assertEqual(source["unit"], "03주 정서지능과 감정이해")
        self.assertEqual(source["filename"], "03주.pdf")
        self.assertEqual(source["stored_filename"], "owned_03주.pdf")
        self.assertEqual(source["page_num"], 7)
        self.assertEqual(source["source_id"], "")

    def test_scope_label_includes_unit_between_course_and_file(self):
        self.assertEqual(
            get_filter_label({
                "semester": "2026-2",
                "course": "인간관계와의사소통",
                "unit": "03주",
                "filename": "03주.pdf",
            }),
            "2026-2 / 인간관계와의사소통 / 03주 / 03주.pdf",
        )

    def test_claim_search_uses_selected_scope_without_generation_when_empty(self):
        search_filter = api_server.SearchFilter(
            semester="2026-2",
            course="인간관계와의사소통",
            unit="03주",
            filename="03주.pdf",
        )
        request = api_server.StudyClaimRequest(claim="감정은 학습에 영향을 준다", search_filter=search_filter)
        with patch.object(api_server, "search_relevant_chunks", return_value=[]) as search, \
                patch.object(api_server, "generate_openai_answer") as generate:
            result = asyncio.run(api_server.study_claim(
                request,
                user={"email": "student@example.com", "data_user_id": "user-1"},
            ))
        search.assert_called_once_with(
            "감정은 학습에 영향을 준다",
            n_results=5,
            search_filter=api_server._model_to_dict(search_filter),
            user_id="user-1",
        )
        generate.assert_not_called()
        self.assertEqual(result["scope_label"], "2026-2 / 인간관계와의사소통 / 03주 / 03주.pdf")

    def test_claim_save_keeps_only_verified_owned_sources_and_edited_fields(self):
        source = api_server.StudyClaimSourcePayload(
            source_id="chunk-1",
            doc_title="사용자가 바꾼 제목",
            filename="03주.pdf",
            stored_filename="forged.pdf",
            semester="2026-2",
            course="인간관계와의사소통",
            unit="03주",
            page_num=7,
            similarity=91,
            excerpt="사용자가 바꾼 발췌",
        )
        payload = api_server.StudyClaimSavePayload(
            claim="편집한 주장",
            source_summary="편집한 요약",
            strength="중",
            application_context="편집한 활용 맥락",
            safety_note="편집한 안전 메모",
            search_filter=api_server.SearchFilter(semester="2026-2", course="인간관계와의사소통", unit="03주"),
            sources=[source],
        )
        owned_chunk = {
            "id": "chunk-1", "title": "감정의 이해", "filename": "03주.pdf",
            "stored_filename": "owned.pdf", "semester": "2026-2",
            "course": "인간관계와의사소통", "unit": "03주", "page": 7,
            "text": "소유한 자료 발췌",
        }
        saved = {}
        with patch.object(api_server, "get_chunks", return_value={"items": [owned_chunk]}), \
                patch.object(api_server, "_load_json_file", return_value={}), \
                patch.object(api_server, "_save_json_file", side_effect=lambda _, data: saved.update(data)):
            result = asyncio.run(api_server.study_claim_save(
                payload,
                user={"email": "student@example.com", "data_user_id": "user-1"},
            ))
        entry = result["claim"]
        self.assertEqual(entry["claim"], "편집한 주장")
        self.assertEqual(entry["strength"], "중")
        self.assertEqual(entry["scope_label"], "2026-2 / 인간관계와의사소통 / 03주")
        self.assertEqual(entry["sources"][0]["doc_title"], "감정의 이해")
        self.assertEqual(entry["sources"][0]["stored_filename"], "owned.pdf")
        self.assertEqual(saved["user-1"][0]["sources"][0]["source_id"], "chunk-1")

    def test_claim_save_rejects_source_outside_selected_scope(self):
        payload = api_server.StudyClaimSavePayload(
            claim="범위를 벗어난 주장",
            search_filter=api_server.SearchFilter(semester="2026-2"),
            sources=[api_server.StudyClaimSourcePayload(
                source_id="chunk-1", filename="03주.pdf", semester="2025-2",
                course="인간관계와의사소통", unit="03주", page_num=1,
            )],
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api_server.study_claim_save(
                payload,
                user={"email": "student@example.com", "data_user_id": "user-1"},
            ))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_claim_without_sources_is_saved_as_unverified(self):
        payload = api_server.StudyClaimSavePayload(claim="출처 없는 주장", strength="강")
        with patch.object(api_server, "_load_json_file", return_value={}), \
                patch.object(api_server, "_save_json_file"):
            result = asyncio.run(api_server.study_claim_save(
                payload,
                user={"email": "student@example.com", "data_user_id": "user-1"},
            ))
        self.assertEqual(result["claim"]["strength"], "출처 미확인")
        self.assertEqual(result["claim"]["sources"], [])

    def test_claim_save_accepts_owned_careflow_source_without_unit(self):
        payload = api_server.StudyClaimSavePayload(
            claim="단원 없는 가져오기 자료의 주장",
            sources=[api_server.StudyClaimSourcePayload(
                source_id="careflow-1", filename="paper.pdf", semester="CareFlow",
                course="스터디 논문", unit="", page_num=2,
            )],
        )
        owned_chunk = {
            "id": "careflow-1", "title": "논문", "filename": "paper.pdf",
            "semester": "CareFlow", "course": "스터디 논문", "unit": "",
            "page": 2, "text": "가져온 논문의 근거",
        }
        with patch.object(api_server, "get_chunks", return_value={"items": [owned_chunk]}), \
                patch.object(api_server, "_load_json_file", return_value={}), \
                patch.object(api_server, "_save_json_file"):
            result = asyncio.run(api_server.study_claim_save(
                payload,
                user={"email": "student@example.com", "data_user_id": "user-1"},
            ))
        self.assertEqual(result["claim"]["sources"][0]["source_id"], "careflow-1")


if __name__ == "__main__":
    unittest.main()
