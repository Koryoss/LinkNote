import json
import os
import unittest

import rag


class ConceptMapSchemaTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [{
            "filename": "[Week 10] 심혈관계 질환 II.pdf",
            "page": 4,
            "text": (
                "A|24(Bradycardia)\n4194(Tachycardia)\n"
                "분당 60회 미만의 느린 심박동\n분당 100회 이상의 빠른 심박동"
            ),
        }]
        self.candidates = [{
            "name": "서맥",
            "aliases": ["bradycardia"],
            "occurrences": [{
                "filename": "[Week 10] 심혈관계 질환 II.pdf",
                "page": 4,
                "surface": "Bradycardia",
                "candidate_rule": "user_alias",
            }],
        }]

    def test_prompt_contains_candidates_page_markers_and_evidence_contract(self):
        prompt = rag.build_candidate_aware_map_prompt(self.chunks, self.candidates)
        self.assertIn("[FILE: [Week 10] 심혈관계 질환 II.pdf | PAGE: 4]", prompt)
        self.assertIn('"name":"서맥"', prompt)
        self.assertIn('"surface":"Bradycardia"', prompt)
        self.assertIn('"evidence_span"', prompt)
        self.assertIn("근거가 없는 후보를 억지로 채택하지 않는다", prompt)
        self.assertIn("전체 개수 상한은 없다", prompt)
        self.assertNotIn("최대 16개", prompt)

    def test_fixture_response_preserves_model_evidence_for_later_validation(self):
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "concept_map_response.json")
        with open(fixture, encoding="utf-8") as handle:
            raw = json.dumps(json.load(handle), ensure_ascii=False)
        parsed = rag._parse_concept_items(raw, self.chunks)
        by_name = {item["name"]: item for item in parsed}
        self.assertEqual(set(by_name), {"서맥", "빈맥"})
        self.assertEqual(by_name["서맥"]["model_evidence"][0]["page"], 4)
        self.assertEqual(by_name["서맥"]["model_evidence"][0]["surface"], "Bradycardia")
        self.assertEqual(by_name["빈맥"]["model_evidence"][0]["evidence_span"], "분당 100회 이상의 빠른 심박동")

    def test_prompt_builder_does_not_call_generation_provider(self):
        prompt = rag.build_candidate_aware_map_prompt(self.chunks, self.candidates)
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), len(self.chunks[0]["text"]))

    def test_prompt_does_not_drop_candidates_after_the_fortieth_item(self):
        candidates = [
            {"name": f"후보-{index}", "aliases": [], "occurrences": []}
            for index in range(46)
        ]
        prompt = rag.build_candidate_aware_map_prompt(self.chunks, candidates)
        self.assertIn('"name":"후보-45"', prompt)


if __name__ == "__main__":
    unittest.main()
