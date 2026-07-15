import json
import os
import unittest

from search_engine import (
    SEARCH_ALGORITHM_VERSION,
    classify_intent,
    expand_tokens,
    learning_score,
    normalized_keyword_score,
    preference_score,
    resolve_scope,
    semantic_score,
    tokenize,
    weighted_score,
)


class SearchEngineTests(unittest.TestCase):
    def test_evaluation_cases_keep_intent_scope_and_terms_stable(self):
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "search_cases.json")
        with open(fixture, encoding="utf-8") as handle:
            cases = json.load(handle)
        for case in cases:
            with self.subTest(question=case["question"]):
                intent = classify_intent(case["question"])
                scope, _ = resolve_scope("auto", case.get("search_filter", {}), intent)
                terms = tokenize(case["question"])
                self.assertEqual(intent, case["expected_intent"])
                self.assertEqual(scope, case["expected_scope"])
                for required in case["required_terms"]:
                    self.assertIn(required.lower(), terms)

    def test_aliases_expand_without_replacing_original_term(self):
        expanded = expand_tokens(["죽상경화증"], {"죽상경화증": ["atherosclerosis", "동맥경화성병변"]})
        self.assertEqual(expanded[0], "죽상경화증")
        self.assertIn("atherosclerosis", expanded)
        self.assertIn("동맥경화성병변", expanded)

    def test_hybrid_score_prefers_semantically_relevant_candidate(self):
        semantic_candidate = weighted_score({
            "semantic": semantic_score(0.1), "keyword": 0.2, "concept": 0.0, "learning": 0.0, "preference": 0.0,
        })
        keyword_candidate = weighted_score({
            "semantic": 0.0, "keyword": normalized_keyword_score("심부전 심부전", ["심부전"]),
            "concept": 0.0, "learning": 0.0, "preference": 0.0,
        })
        self.assertGreater(semantic_candidate, keyword_candidate)

    def test_personalization_is_bounded_and_cannot_override_relevance(self):
        profile = {"course_counts": {"병태생리학1": 100}, "concept_counts": {"심부전": 100}}
        personalized = weighted_score({
            "semantic": 0.0, "keyword": 0.0, "concept": 0.0,
            "learning": learning_score({"learning_state": "REVIEW", "review_priority": 100}),
            "preference": preference_score(profile, "병태생리학1", "심부전"),
        })
        relevant = weighted_score({
            "semantic": 0.9, "keyword": 0.8, "concept": 0.8, "learning": 0.0, "preference": 0.0,
        })
        self.assertLessEqual(personalized, 0.20)
        self.assertGreater(relevant, personalized)

    def test_algorithm_version_is_explicit(self):
        self.assertEqual(SEARCH_ALGORITHM_VERSION, "hybrid_personalized_v1")


if __name__ == "__main__":
    unittest.main()
