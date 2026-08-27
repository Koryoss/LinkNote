import asyncio
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
