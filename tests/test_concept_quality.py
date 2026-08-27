import unittest

from concept_quality import (
    build_concept_coverage_report,
    build_quality_alias_groups,
    detect_concept_candidates,
)


class ConceptQualityTests(unittest.TestCase):
    def setUp(self):
        self.concepts = [{
            "name": "심실빈맥",
            "keyword": "ventricular tachycardia",
            "aliases": [],
        }]
        self.aliases = {"서맥": ["bradycardia"]}
        self.chunks = [{
            "filename": "[Week 10] 심혈관계 질환 II.pdf",
            "page": 4,
            "text": (
                "A|24(Bradycardia)\n"
                "4194(Tachycardia)\n"
                "분당609] 미만의느린심박동\n"
                "분당100회이상의빠른심박동\n"
                "서맥-빈맥증후군: 맥박이아주느리다가갑자기빠른맥박이나오는상태가반복"
            ),
        }]

    def test_compound_concept_adds_review_only_suffix_alias(self):
        groups = build_quality_alias_groups(self.concepts, self.aliases)
        self.assertIn(
            {"name": "빈맥", "aliases": ["tachycardia"], "rule": "compound_suffix_alias"},
            groups,
        )

    def test_detects_bilingual_arrhythmia_terms_without_ocr_prefix_noise(self):
        groups = build_quality_alias_groups(self.concepts, self.aliases)
        candidates = detect_concept_candidates(self.chunks, groups)
        by_name = {item["name"]: item for item in candidates}
        self.assertIn("서맥", by_name)
        self.assertIn("bradycardia", [x.lower() for x in by_name["서맥"]["aliases"]])
        self.assertIn("빈맥", by_name)
        self.assertIn("tachycardia", [x.lower() for x in by_name["빈맥"]["aliases"]])
        self.assertNotIn("결절", by_name)
        serialized = str(candidates)
        self.assertNotIn("4194", [item["name"] for item in candidates])
        self.assertNotIn("A|24", [item["name"] for item in candidates])
        self.assertIn("4194(Tachycardia)", serialized)

    def test_coverage_report_marks_generic_terms_missing_not_compound_match(self):
        report = build_concept_coverage_report(self.chunks, self.concepts, self.aliases)
        missing_names = {item["name"] for item in report["missing"]}
        self.assertIn("서맥", missing_names)
        self.assertIn("빈맥", missing_names)
        self.assertNotIn("심실빈맥", missing_names)

    def test_parenthetical_sentence_is_not_treated_as_an_english_term(self):
        chunks = [{
            "filename": "chemistry.pdf",
            "page": 1,
            "text": (
                "결정(the cations and anions clump together as a crystal)\n"
                "감염(group A beta hemolytic streptococcus)"
            ),
        }]
        candidates = detect_concept_candidates(chunks)
        names = {item["name"] for item in candidates}
        self.assertNotIn("the cations and anions clump together as a crystal", names)
        self.assertIn("group A beta hemolytic streptococcus", names)

    def test_structured_bilingual_pair_keeps_korean_name_and_english_alias(self):
        candidates = detect_concept_candidates([{
            "filename": "염증.pdf",
            "page": 13,
            "text": "[한영 병기] 백혈구 (Leukocytes)",
        }])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "백혈구")
        self.assertEqual(candidates[0]["aliases"], ["Leukocytes"])
        self.assertEqual(candidates[0]["rules"], ["bilingual_pair", "parenthetical_english"])

    def test_bilingual_pair_merges_an_earlier_english_only_candidate(self):
        candidates = detect_concept_candidates([{
            "filename": "염증.pdf",
            "page": 2,
            "text": "원인(Exogenous)\n[한영 병기] 외인성 요인 (Exogenous)",
        }])
        self.assertEqual([item["name"] for item in candidates], ["외인성 요인"])
        self.assertEqual(candidates[0]["aliases"], ["Exogenous"])

    def test_candidate_filter_removes_generic_items_and_ocr_sentence_fragments(self):
        rejected = []
        candidates = detect_concept_candidates([{
            "filename": "염증.pdf",
            "page": 18,
            "text": (
                "효능: 항염, 진통, 해열\n"
                "주의점: 위장장애\n"
                "으로진행: 만성 염증\n"
                "화기계: 흉터 조직 수축\n"
                "근골격계: 관절 운동 범위 제한\n"
                "성/생합성피부대체물활용하여화상부위드레싱: 일시적 보호막\n"
                "미란: 상피세포만 벗겨진 상태"
            ),
        }], rejected_candidates=rejected)
        names = {item["name"] for item in candidates}
        self.assertEqual(names, {"미란"})
        rejected_by_name = {item["name"]: item["reason"] for item in rejected}
        self.assertEqual(rejected_by_name["효능"], "generic_heading_or_item")
        self.assertEqual(rejected_by_name["주의점"], "generic_heading_or_item")
        self.assertEqual(rejected_by_name["으로진행"], "sentence_fragment")
        self.assertEqual(rejected_by_name["화기계"], "body_system_label")
        self.assertEqual(rejected_by_name["근골격계"], "body_system_label")

    def test_candidate_filter_drops_generic_english_items_but_keeps_medical_terms(self):
        rejected = []
        candidates = detect_concept_candidates([{
            "filename": "염증.pdf",
            "page": 28,
            "text": "체중(kg), 연령(age in years), 육아종(granuloma)",
        }], rejected_candidates=rejected)
        names = {item["name"] for item in candidates}
        self.assertEqual(names, {"granuloma"})
        self.assertEqual({item["name"] for item in rejected}, {"kg", "age in years"})


if __name__ == "__main__":
    unittest.main()
