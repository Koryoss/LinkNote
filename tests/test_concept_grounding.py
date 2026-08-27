import json
import os
import unittest

import rag
from concept_quality import (
    canonicalize_grounded_concepts,
    reconcile_with_existing_concepts,
    validate_model_evidence,
)


class ConceptGroundingTests(unittest.TestCase):
    def setUp(self):
        self.filename = "[Week 10] 심혈관계 질환 II.pdf"
        self.chunks = [{
            "filename": self.filename,
            "page": 4,
            "text": (
                "A|24(Bradycardia)\n4194(Tachycardia)\n"
                "분당 60회 미만의 느린 심박동\n분당 100회 이상의 빠른 심박동"
            ),
        }]

    def _fixture_items(self):
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "concept_map_response.json")
        with open(fixture, encoding="utf-8") as handle:
            return rag._parse_concept_items(json.dumps(json.load(handle), ensure_ascii=False), self.chunks)

    def test_fixture_evidence_is_grounded_to_exact_page(self):
        result = canonicalize_grounded_concepts(self._fixture_items(), self.chunks)
        self.assertEqual({item["name"] for item in result["accepted"]}, {"서맥", "빈맥"})
        self.assertEqual(result["rejected"], [])
        self.assertTrue(all(item["evidence_status"] == "direct_span" for item in result["accepted"]))

    def test_hallucinated_surface_is_rejected(self):
        concept = {
            "name": "심방세동",
            "keyword": "Atrial fibrillation",
            "model_evidence": [{
                "filename": self.filename,
                "page": 4,
                "surface": "Atrial fibrillation",
                "evidence_span": "심방세동",
            }],
        }
        validation = validate_model_evidence(concept, self.chunks)
        self.assertEqual(validation["grounded"], [])
        self.assertEqual(validation["rejected"][0]["reason"], "surface_not_found")

    def test_bilingual_duplicate_items_merge_to_korean_canonical_name(self):
        items = self._fixture_items()
        items.append({
            "name": "Bradycardia",
            "keyword": "Bradycardia",
            "aliases": ["서맥"],
            "weight": 3,
            "links": [],
            "model_evidence": [{
                "filename": self.filename,
                "page": 4,
                "surface": "Bradycardia",
                "evidence_span": "분당 60회 미만의 느린 심박동",
            }],
        })
        result = canonicalize_grounded_concepts(items, self.chunks)
        brady = [item for item in result["accepted"] if item["name"] == "서맥"]
        self.assertEqual(len(brady), 1)
        self.assertEqual(brady[0]["keyword"], "Bradycardia")

    def test_surface_match_survives_when_ocr_span_was_normalized_by_model(self):
        concept = {
            "name": "서맥",
            "keyword": "Bradycardia",
            "model_evidence": [{
                "filename": self.filename,
                "page": 4,
                "surface": "Bradycardia",
                "evidence_span": "OCR에 없는 정리된 설명",
            }],
        }
        validation = validate_model_evidence(concept, self.chunks)
        self.assertEqual(validation["grounded"][0]["status"], "surface_only")
        self.assertEqual(validation["grounded"][0]["evidence_span"], "")

    def test_model_alias_does_not_merge_two_distinct_concepts(self):
        items = [
            {
                "name": "심방 세동",
                "keyword": "atrial fibrillation",
                "aliases": ["심실세동"],
                "weight": 4,
                "links": [],
                "model_evidence": [{
                    "filename": self.filename,
                    "page": 4,
                    "surface": "Bradycardia",
                    "evidence_span": "Bradycardia",
                }],
            },
            {
                "name": "심실세동",
                "keyword": "ventricular fibrillation",
                "aliases": [],
                "weight": 4,
                "links": [],
                "model_evidence": [{
                    "filename": self.filename,
                    "page": 4,
                    "surface": "Tachycardia",
                    "evidence_span": "Tachycardia",
                }],
            },
        ]
        result = canonicalize_grounded_concepts(items, self.chunks)
        self.assertEqual(len(result["accepted"]), 2)

    def test_existing_name_is_reused_from_a_keyword_match(self):
        concepts = [{
            "name": "His 다발",
            "keyword": "His bundle",
            "aliases": ["bundle of His"],
            "weight": 4,
            "occurrences": [{
                "filename": self.filename,
                "page": 4,
                "surface": "His bundle",
                "status": "direct_span",
            }],
        }]
        existing = [{"name": "히스속", "keyword": "His bundle", "weight": 4}]
        result = reconcile_with_existing_concepts(concepts, existing)
        self.assertEqual(result["accepted"][0]["name"], "히스속")
        self.assertEqual(result["accepted"][0]["canonical_status"], "reused_existing")
        self.assertIn("His 다발", result["accepted"][0]["aliases"])

    def test_alias_and_evidence_owned_by_another_existing_concept_are_removed(self):
        concepts = [{
            "name": "심방 세동",
            "keyword": "atrial fibrillation, Af",
            "aliases": ["심실 세동", "ventricular fibrillation, V. fib", "V. fib"],
            "weight": 5,
            "occurrences": [
                {"filename": self.filename, "page": 5, "surface": "atrial fibrillation, Af", "status": "direct_span"},
                {"filename": self.filename, "page": 12, "surface": "ventricular fibrillation, V. fib", "status": "direct_span"},
            ],
        }]
        existing = [{"name": "심실세동", "keyword": "ventricular fibrillation", "weight": 5}]
        result = reconcile_with_existing_concepts(concepts, existing)
        item = result["accepted"][0]
        self.assertEqual(item["name"], "심방 세동")
        self.assertEqual(item["pages"], [5])
        self.assertEqual(item["aliases"], [])
        self.assertEqual(len(item["canonical_conflicts_removed"]["evidence"]), 1)

    def test_disease_suffix_name_variant_reuses_existing_canonical_name(self):
        concepts = [{
            "name": "파종성 혈관내 응고",
            "keyword": "Disseminated intravascular coagulation",
            "aliases": [],
            "weight": 5,
            "occurrences": [{
                "filename": "혈액.pdf",
                "page": 36,
                "surface": "Disseminated intravascular coagulation",
                "status": "direct_span",
            }],
        }]
        existing = [{"name": "파종성 혈관내 응고증", "keyword": "DIC", "weight": 5}]
        result = reconcile_with_existing_concepts(concepts, existing)
        item = result["accepted"][0]
        self.assertEqual(item["name"], "파종성 혈관내 응고증")
        self.assertEqual(item["canonical_status"], "reused_existing")
        self.assertEqual(item["canonical_match_rule"], "name_variant")
        self.assertIn("파종성 혈관내 응고", item["aliases"])

    def test_subtype_name_variant_reuses_existing_canonical_name(self):
        concepts = [{
            "name": "혈우병 A형",
            "keyword": "Hemophilia A",
            "aliases": [],
            "weight": 5,
            "occurrences": [{
                "filename": "혈액.pdf",
                "page": 32,
                "surface": "Hemophilia A",
                "status": "direct_span",
            }],
        }]
        existing = [{"name": "혈우병", "keyword": "Hemophilia", "weight": 5}]
        result = reconcile_with_existing_concepts(concepts, existing)
        item = result["accepted"][0]
        self.assertEqual(item["name"], "혈우병")
        self.assertEqual(item["canonical_match_rule"], "name_variant")
        self.assertNotIn("혈우병 A형", item["aliases"])
        self.assertIn("혈우병 A형", item["related_variants"])

    def test_bare_hemophilia_subtype_reuses_existing_canonical_name(self):
        concepts = [{
            "name": "혈우병 A", "keyword": "Hemophilia A", "aliases": [], "weight": 5,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 32, "surface": "Hemophilia A", "status": "direct_span",
            }],
        }]
        existing = [{"name": "혈우병", "keyword": "Hemophilia", "weight": 5}]
        result = reconcile_with_existing_concepts(concepts, existing)
        self.assertEqual(result["accepted"][0]["name"], "혈우병")
        self.assertEqual(result["accepted"][0]["canonical_match_rule"], "name_variant")
        self.assertIn("혈우병 A", result["accepted"][0]["related_variants"])

    def test_name_variant_does_not_use_generic_substring_containment(self):
        concepts = [{
            "name": "만성골수성백혈병",
            "keyword": "CML",
            "aliases": [],
            "weight": 5,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 28, "surface": "CML", "status": "direct_span",
            }],
        }]
        existing = [{"name": "백혈병", "keyword": "Leukemia", "weight": 5}]
        result = reconcile_with_existing_concepts(concepts, existing)
        self.assertEqual(result["accepted"][0]["canonical_status"], "new")

    def test_new_surface_only_concept_requires_review(self):
        concepts = [{
            "name": "빈혈 진단검사",
            "keyword": "단검사",
            "aliases": [],
            "weight": 3,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 6, "surface": "단검사", "status": "surface_only",
            }],
        }]
        result = reconcile_with_existing_concepts(concepts, [])
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["new_surface_only_requires_review"])

    def test_existing_surface_only_concept_can_be_reused(self):
        concepts = [{
            "name": "림프종",
            "keyword": "Lymphoma",
            "aliases": [],
            "weight": 4,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 22, "surface": "Lymphoma", "status": "surface_only",
            }],
        }]
        existing = [{"name": "림프종", "keyword": "Lymphoma", "weight": 4}]
        result = reconcile_with_existing_concepts(concepts, existing)
        self.assertEqual(result["accepted"][0]["canonical_status"], "reused_existing")
        self.assertEqual(result["accepted"][0]["evidence_status"], "surface_only")

    def _canonicalize_and_reconcile(self, concept, text, existing=()):
        chunks = [{"filename": "혈액.pdf", "page": 8, "text": text}]
        grounded = canonicalize_grounded_concepts([concept], chunks)
        return reconcile_with_existing_concepts(grounded["accepted"], existing)

    def test_generic_slide_heading_is_rejected_as_a_new_concept(self):
        concept = {
            "name": "주요혈액질환", "keyword": "주요혈액질환", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "주요혈액질환",
                "evidence_span": "주요혈액질환",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "주요혈액질환")
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["generic_heading_not_concept"])

    def test_truncated_new_keyword_requires_review_even_with_an_english_alias(self):
        concept = {
            "name": "악성빈혈", "keyword": "성빈혈", "aliases": ["Megaloblastic anemia"],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "성빈혈", "evidence_span": "성빈혈",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "성빈혈 Megaloblastic anemia")
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["truncated_keyword_requires_review"])

    def test_mixed_script_ocr_keyword_is_replaced_by_a_grounded_clean_alias(self):
        concept = {
            "name": "용혈성빈혈", "keyword": "적혈구의AALT파괴의증가", "aliases": ["Hemolytic anemia"],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "적혈구의AALT파괴의증가",
                "evidence_span": "적혈구의AALT파괴의증가",
            }],
        }
        result = self._canonicalize_and_reconcile(
            concept, "적혈구의AALT파괴의증가 Hemolytic anemia"
        )
        item = result["accepted"][0]
        self.assertEqual(item["keyword"], "Hemolytic anemia")
        self.assertEqual(item["ocr_keyword_replaced"], "적혈구의AALT파괴의증가")

    def test_near_name_ocr_mutation_on_existing_concept_uses_clean_alias(self):
        concept = {
            "name": "재생불량성 빈혈", "keyword": "재생불량성비형", "aliases": ["Aplastic anemia"],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "재생불량성비형",
                "evidence_span": "재생불량성비형",
            }],
        }
        existing = [{"name": "재생불량성 빈혈", "keyword": "재생불량성", "weight": 4}]
        result = self._canonicalize_and_reconcile(
            concept, "재생불량성비형 Aplastic anemia", existing
        )
        item = result["accepted"][0]
        self.assertEqual(item["name"], "재생불량성 빈혈")
        self.assertEqual(item["keyword"], "Aplastic anemia")

    def test_existing_keyword_is_restored_when_ocr_keyword_has_no_clean_source_term(self):
        concept = {
            "name": "철 결핍성 빈혈", "keyword": "철Ag 빈혈", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "철Ag 빈혈",
                "evidence_span": "OCR에 없는 정리 문장",
            }],
        }
        existing = [{"name": "철 결핍성 빈혈", "keyword": "철결핍", "weight": 5}]
        result = self._canonicalize_and_reconcile(concept, "철Ag 빈혈", existing)
        item = result["accepted"][0]
        self.assertEqual(item["keyword"], "철결핍")
        self.assertEqual(item["ocr_keyword_replaced"], "철Ag 빈혈")
        self.assertTrue(item["keyword_restored_from_existing"])

    def test_explanatory_phrase_is_held_for_review_when_new(self):
        concept = {
            "name": "조혈작용의감소", "keyword": "조혈작용의감소", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "조혈작용의감소",
                "evidence_span": "조혈작용의감소",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "조혈작용의감소")
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["explanatory_phrase_requires_review"])

    def test_standard_disease_name_is_not_treated_as_an_explanatory_phrase(self):
        concept = {
            "name": "적혈구증가증", "keyword": "Polycythemia", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "Polycythemia",
                "evidence_span": "Polycythemia",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "Polycythemia")
        self.assertEqual(result["accepted"][0]["name"], "적혈구증가증")

    def test_existing_explanatory_name_is_preserved(self):
        concept = {
            "name": "조혈작용의감소", "keyword": "조혈작용의감소", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "조혈작용의감소",
                "evidence_span": "조혈작용의감소",
            }],
        }
        existing = [{"name": "조혈작용의감소", "keyword": "조혈작용의감소", "weight": 3}]
        result = self._canonicalize_and_reconcile(concept, "조혈작용의감소", existing)
        self.assertEqual(result["accepted"][0]["canonical_status"], "reused_existing")

    def test_short_ocr_test_name_is_held_for_review(self):
        concept = {
            "name": "단검사", "keyword": "단검사", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "단검사", "evidence_span": "단검사",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "단검사")
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["ocr_fragment_name_requires_review"])

    def test_normal_length_test_name_is_not_held_as_an_ocr_fragment(self):
        concept = {
            "name": "혈액검사", "keyword": "혈액검사", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "혈액검사", "evidence_span": "혈액검사",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "혈액검사")
        self.assertEqual(result["accepted"][0]["name"], "혈액검사")

    def test_ambiguous_short_keyword_needs_a_long_grounded_term(self):
        concept = {
            "name": "철분대사", "keyword": "Fe", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "Fe", "evidence_span": "Fe",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "Fe")
        self.assertEqual(result["accepted"], [])
        self.assertEqual(
            result["rejected"][0]["reasons"], ["ambiguous_short_keyword_requires_review"]
        )

    def test_short_keyword_is_replaced_when_long_name_is_source_backed(self):
        concept = {
            "name": "철분대사", "keyword": "Fe", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "Fe", "evidence_span": "Fe",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "철분대사 Fe")
        item = result["accepted"][0]
        self.assertEqual(item["keyword"], "철분대사")
        self.assertEqual(item["ocr_keyword_replaced"], "Fe")

    def test_explanatory_keyword_is_replaced_by_a_grounded_name(self):
        concept = {
            "name": "용혈성빈혈", "keyword": "적혈구의파괴의증가", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "적혈구의파괴의증가",
                "evidence_span": "적혈구의파괴의증가",
            }],
        }
        result = self._canonicalize_and_reconcile(
            concept, "용혈성빈혈 적혈구의파괴의증가"
        )
        item = result["accepted"][0]
        self.assertEqual(item["keyword"], "용혈성빈혈")
        self.assertEqual(item["ocr_keyword_replaced"], "적혈구의파괴의증가")

    def test_explanatory_keyword_without_a_clean_term_is_held_for_review(self):
        concept = {
            "name": "용혈성빈혈", "keyword": "적혈구의파괴의증가", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "적혈구의파괴의증가",
                "evidence_span": "적혈구의파괴의증가",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "적혈구의파괴의증가")
        self.assertEqual(result["accepted"], [])
        self.assertEqual(
            result["rejected"][0]["reasons"], ["explanatory_keyword_requires_review"]
        )

    def test_verified_dictionary_normalizes_mean_cell_volume_name(self):
        concept = {
            "name": "적혈구용적평균", "keyword": "Mean cell volume", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "Mean cell volume",
                "evidence_span": "Mean cell volume",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "Mean cell volume")
        item = result["accepted"][0]
        self.assertEqual(item["name"], "평균적혈구용적")
        self.assertIn("적혈구용적평균", item["aliases"])
        self.assertEqual(item["term_override_rule"], "verified_dictionary")

    def test_verified_dictionary_normalizes_ataxia_name(self):
        concept = {
            "name": "운동실조증", "keyword": "ataxia", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8, "surface": "ataxia",
                "evidence_span": "ataxia",
            }],
        }
        result = self._canonicalize_and_reconcile(concept, "ataxia")
        item = result["accepted"][0]
        self.assertEqual(item["name"], "운동실조")
        self.assertIn("운동실조증", item["aliases"])

    def test_verified_preferred_term_rescues_explanatory_keyword(self):
        concept = {
            "name": "적혈구증가증",
            "keyword": "적혈구수가비정상적으로증가되어있는상태",
            "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8,
                "surface": "적혈구수가비정상적으로증가되어있는상태",
                "evidence_span": "적혈구수가비정상적으로증가되어있는상태",
            }],
        }
        result = self._canonicalize_and_reconcile(
            concept, "Polycythemia 적혈구수가비정상적으로증가되어있는상태"
        )
        item = result["accepted"][0]
        self.assertEqual(item["name"], "적혈구증가증")
        self.assertEqual(item["keyword"], "Polycythemia")
        self.assertEqual(item["term_override_rule"], "verified_dictionary")
        self.assertEqual(item["ocr_keyword_replaced"], "적혈구수가비정상적으로증가되어있는상태")

    def test_verified_term_does_not_rescue_when_missing_from_source(self):
        concept = {
            "name": "적혈구증가증",
            "keyword": "적혈구수가비정상적으로증가되어있는상태",
            "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 8,
                "surface": "적혈구수가비정상적으로증가되어있는상태",
                "evidence_span": "적혈구수가비정상적으로증가되어있는상태",
            }],
        }
        result = self._canonicalize_and_reconcile(
            concept, "적혈구수가비정상적으로증가되어있는상태"
        )
        self.assertEqual(result["accepted"], [])
        self.assertEqual(
            result["rejected"][0]["reasons"], ["explanatory_keyword_requires_review"]
        )

    def test_verified_aml_acronym_is_grounded_by_long_form_in_same_file(self):
        concept = {
            "name": "급성 골수성 백혈병",
            "keyword": "AML",
            "aliases": ["acute myelogenous leukemia"],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 26,
                "surface": "AML", "evidence_span": "급성 골수성 백혈병(AML)",
            }],
        }
        chunks = [
            {
                "filename": "혈액.pdf", "page": 23,
                "text": "(acute myelogenous leukemia; AML)",
            },
            {"filename": "혈액.pdf", "page": 26, "text": "(AML)"},
        ]
        grounded = canonicalize_grounded_concepts([concept], chunks)
        self.assertEqual(len(grounded["accepted"]), 1)
        item = reconcile_with_existing_concepts(grounded["accepted"], [])["accepted"][0]
        self.assertEqual(item["name"], "급성골수성백혈병")
        self.assertEqual(item["evidence_status"], "direct_span")
        self.assertEqual(item["occurrences"][0]["grounding_rule"], "verified_acronym_long_form")

    def test_verified_aml_acronym_stays_surface_only_without_long_form(self):
        concept = {
            "name": "급성골수성백혈병", "keyword": "AML", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 26,
                "surface": "AML", "evidence_span": "급성 골수성 백혈병(AML)",
            }],
        }
        chunks = [{"filename": "혈액.pdf", "page": 26, "text": "(AML)"}]
        grounded = canonicalize_grounded_concepts([concept], chunks)
        result = reconcile_with_existing_concepts(grounded["accepted"], [])
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["new_surface_only_requires_review"])

    def test_verified_aml_long_form_is_its_own_direct_span(self):
        concept = {
            "name": "급성골수성백혈병",
            "keyword": "acute myelogenous leukemia",
            "aliases": ["AML"],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 23,
                "surface": "acute myelogenous leukemia",
                "evidence_span": "급성 골수성 백혈병",
            }],
        }
        chunks = [{
            "filename": "혈액.pdf", "page": 23,
            "text": "(acute myelogenous leukemia; AML)",
        }]
        grounded = canonicalize_grounded_concepts([concept], chunks)
        item = reconcile_with_existing_concepts(grounded["accepted"], [])["accepted"][0]
        self.assertEqual(item["evidence_status"], "direct_span")
        self.assertEqual(item["occurrences"][0]["grounding_rule"], "verified_direct_surface")

    def test_exact_canonical_name_on_page_repairs_model_span(self):
        concept = {
            "name": "만성골수성백혈병", "keyword": "CML", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 28,
                "surface": "CML", "evidence_span": "잘못 생성된 설명",
            }],
        }
        chunks = [{
            "filename": "혈액.pdf", "page": 28,
            "text": "[희소 영역 OCR] 만성골수성백혈병 (CML)",
        }]
        grounded = canonicalize_grounded_concepts([concept], chunks)
        item = reconcile_with_existing_concepts(grounded["accepted"], [])["accepted"][0]
        self.assertEqual(item["evidence_status"], "direct_span")
        self.assertEqual(item["occurrences"][0]["grounding_rule"], "canonical_name_on_source_page")

    def test_concept_surface_bridges_to_exact_canonical_name_in_same_file(self):
        concept = {
            "name": "만성림프구성백혈병", "keyword": "CLL",
            "aliases": ["Chronic lymphocytic leukemia"],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 23,
                "surface": "CLL", "evidence_span": "잘못 생성된 설명",
            }],
        }
        chunks = [
            {"filename": "혈액.pdf", "page": 23, "text": "(CLL)"},
            {
                "filename": "혈액.pdf", "page": 27,
                "text": "[희소 영역 OCR] 만성림프구성백혈병",
            },
        ]
        grounded = canonicalize_grounded_concepts([concept], chunks)
        item = reconcile_with_existing_concepts(grounded["accepted"], [])["accepted"][0]
        self.assertEqual(item["pages"], [27])
        self.assertEqual(item["occurrences"][0]["grounding_rule"], "canonical_name_in_same_file")

    def test_unrelated_surface_does_not_use_canonical_heading_as_direct_span(self):
        concept = {
            "name": "만성골수성백혈병", "keyword": "CML", "aliases": [],
            "model_evidence": [{
                "filename": "혈액.pdf", "page": 28,
                "surface": "imatinib", "evidence_span": "잘못 생성된 설명",
            }],
        }
        chunks = [{
            "filename": "혈액.pdf", "page": 28,
            "text": "만성골수성백혈병(CML)의 치료에는 imatinib이 사용된다.",
        }]
        grounded = canonicalize_grounded_concepts([concept], chunks)
        self.assertEqual(grounded["accepted"][0]["occurrences"][0]["status"], "surface_only")

    def test_narrower_korean_and_english_types_move_out_of_aliases(self):
        concepts = [{
            "name": "림프종", "keyword": "Lymphoma",
            "aliases": ["호지킨림프종", "Hodgkin's lymphoma", "림프종양"],
            "weight": 4,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 29, "surface": "Lymphoma", "status": "direct_span",
            }],
        }]
        result = reconcile_with_existing_concepts(concepts, [])
        item = result["accepted"][0]
        self.assertEqual(item["aliases"], ["림프종양"])
        self.assertEqual(
            item["related_variants"], ["호지킨림프종", "Hodgkin's lymphoma"]
        )

    def test_english_subtype_uses_an_exact_english_alias_as_its_base(self):
        concepts = [{
            "name": "림프종", "keyword": "림프종",
            "aliases": ["Lymphoma", "Hodgkin's lymphoma"], "weight": 4,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 29, "surface": "림프종", "status": "direct_span",
            }],
        }]
        item = reconcile_with_existing_concepts(concepts, [])["accepted"][0]
        self.assertEqual(item["aliases"], ["Lymphoma"])
        self.assertEqual(item["related_variants"], ["Hodgkin's lymphoma"])

    def test_deficiency_mechanism_moves_to_related_descriptors(self):
        concepts = [{
            "name": "혈우병", "keyword": "Hemophilia A",
            "aliases": ["Factor VIII 결핍증"], "weight": 4,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 16, "surface": "Hemophilia A", "status": "direct_span",
            }],
        }]
        item = reconcile_with_existing_concepts(concepts, [])["accepted"][0]
        self.assertEqual(item["aliases"], [])
        self.assertEqual(item["related_descriptors"], ["Factor VIII 결핍증"])

    def test_deficiency_translation_stays_alias_when_canonical_is_deficiency(self):
        concepts = [{
            "name": "철 결핍성 빈혈", "keyword": "Iron deficiency anemia",
            "aliases": ["철분 결핍 빈혈"], "weight": 4,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 11,
                "surface": "Iron deficiency anemia", "status": "direct_span",
            }],
        }]
        item = reconcile_with_existing_concepts(concepts, [])["accepted"][0]
        self.assertEqual(item["aliases"], ["철분 결핍 빈혈"])
        self.assertEqual(item["related_descriptors"], [])

    def test_acute_and_chronic_leukemia_move_out_of_aliases(self):
        concepts = [{
            "name": "백혈병", "keyword": "Leukemia",
            "aliases": ["급성백혈병", "만성백혈병", "Acute leukemia", "Chronic leukemia"],
            "weight": 5,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 23, "surface": "Leukemia", "status": "direct_span",
            }],
        }]
        item = reconcile_with_existing_concepts(concepts, [])["accepted"][0]
        self.assertEqual(item["aliases"], [])
        self.assertEqual(len(item["related_variants"]), 4)

    def test_simple_disease_suffix_spelling_remains_an_alias(self):
        concepts = [{
            "name": "운동실조", "keyword": "ataxia", "aliases": ["운동실조증"], "weight": 3,
            "occurrences": [{
                "filename": "혈액.pdf", "page": 9, "surface": "ataxia", "status": "direct_span",
            }],
        }]
        item = reconcile_with_existing_concepts(concepts, [])["accepted"][0]
        self.assertEqual(item["aliases"], ["운동실조증"])
        self.assertEqual(item["related_variants"], [])


if __name__ == "__main__":
    unittest.main()
