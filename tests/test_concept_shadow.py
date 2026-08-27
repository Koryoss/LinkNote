import json
import unittest
from unittest.mock import patch

import rag


class ConceptShadowExtractionTests(unittest.TestCase):
    def setUp(self):
        self.filename = "week10.pdf"
        self.chunks = [{
            "filename": self.filename,
            "page": 4,
            "chunk_index": 0,
            "text": (
                "서맥(Bradycardia): 분당 60회 미만의 느린 심박동\n"
                "빈맥(Tachycardia): 분당 100회 이상의 빠른 심박동\n"
                "동휴지기(sinus pause): 박동이 한두 개 빠졌다가 돌아옴\n"
                "동정지(sinus arrest): 3초 이상 전기신호가 나타나지 않음"
            ),
        }]
        self.candidates = [{
            "name": "서맥",
            "aliases": ["Bradycardia"],
            "occurrences": [{"filename": self.filename, "page": 4, "surface": "Bradycardia"}],
        }]

    def _map_response(self):
        concepts = [
            ("서맥", "Bradycardia"),
            ("빈맥", "Tachycardia"),
            ("동휴지기", "sinus pause"),
            ("동정지", "sinus arrest"),
        ]
        return json.dumps([
            {
                "name": name,
                "keyword": surface,
                "aliases": [],
                "importance": 4,
                "related": [],
                "evidence": [{
                    "filename": self.filename,
                    "page": 4,
                    "surface": surface,
                    "evidence_span": surface,
                }],
            }
            for name, surface in concepts
        ], ensure_ascii=False)

    def test_shadow_path_uses_one_map_and_one_group_call_without_persistence(self):
        grouping = json.dumps([
            {"keyword": "Bradycardia", "group": "심박수 이상"},
            {"keyword": "Tachycardia", "group": "심박수 이상"},
            {"keyword": "sinus pause", "group": "동방결절 기능장애"},
            {"keyword": "sinus arrest", "group": "동방결절 기능장애"},
        ], ensure_ascii=False)
        with patch("rag.generate_answer", side_effect=[self._map_response(), grouping]) as mocked:
            result = rag.shadow_extract_concepts_from_chunks(
                self.chunks, self.candidates, "심장 부정맥", seg_size=9000
            )

        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result["map_calls"], 1)
        self.assertEqual(result["group_calls"], 1)
        self.assertEqual({item["name"] for item in result["concepts"]},
                         {"서맥", "빈맥", "동휴지기", "동정지"})
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["rejected"], [])
        self.assertEqual(mocked.call_args_list[0].kwargs["max_tokens"], 2200)

    def test_map_failure_does_not_trigger_fallback_call(self):
        with patch("rag.generate_answer", side_effect=RuntimeError("provider unavailable")) as mocked:
            result = rag.shadow_extract_concepts_from_chunks(
                self.chunks, self.candidates, "심장 부정맥", seg_size=9000
            )

        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["concepts"], [])
        self.assertEqual(result["map_calls"], 1)
        self.assertEqual(result["group_calls"], 0)
        self.assertEqual(result["errors"][0]["segment"], 1)

    def test_segments_keep_each_page_intact(self):
        chunks = [
            {"filename": self.filename, "page": 1, "chunk_index": 0, "text": "a" * 5000},
            {"filename": self.filename, "page": 1, "chunk_index": 1, "text": "b" * 5000},
            {"filename": self.filename, "page": 2, "chunk_index": 0, "text": "c" * 1000},
        ]
        segments = rag._segment_concept_chunks(chunks, seg_size=9000)
        self.assertEqual(len(segments), 2)
        self.assertEqual({chunk["page"] for chunk in segments[0]}, {1})
        self.assertEqual({chunk["page"] for chunk in segments[1]}, {2})

    def test_large_page_set_gets_more_output_room_without_a_fixed_concept_cap(self):
        segment = [
            {"filename": self.filename, "page": page, "text": f"page {page}"}
            for page in range(1, 35)
        ]
        candidates = [{"name": f"개념 {index}"} for index in range(46)]
        self.assertEqual(rag._candidate_aware_max_tokens(segment, candidates), 6000)

    def test_candidate_prompt_rejects_explanatory_phrases_as_names(self):
        prompt = rag.build_candidate_aware_map_prompt(self.chunks, self.candidates)
        self.assertIn("설명·분류 문구를 name으로 만들지 말고", prompt)
        self.assertIn("1~2자인 표기는", prompt)
        self.assertIn("keyword에는 정의 문장", prompt)
        self.assertIn("백혈병의 alias에 급성백혈병을 넣지 않는다", prompt)
        self.assertIn("사진 출처나 그림 캡션에만 등장", prompt)


if __name__ == "__main__":
    unittest.main()
