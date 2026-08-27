import unittest

from concept_audit import audit_unit, audit_workspace


class ConceptAuditTests(unittest.TestCase):
    def test_unit_audit_finds_missing_candidate_and_legacy_evidence(self):
        chunks = [{
            "filename": "week10.pdf",
            "page": 4,
            "text": "서맥(Bradycardia): 느린 심박동\n빈맥(Tachycardia): 빠른 심박동",
        }]
        concepts = [{"name": "서맥", "keyword": "Bradycardia", "weight": 4}]
        result = audit_unit(chunks, concepts, {"서맥": ["bradycardia"]})
        self.assertGreaterEqual(result["missing_candidate_count"], 1)
        self.assertIn("서맥", result["missing_evidence_metadata_concepts"])
        self.assertEqual(result["source_ungrounded_concepts"], [])

    def test_unit_audit_reports_identity_conflict(self):
        chunks = [{"filename": "x.pdf", "page": 1, "text": "Alpha Beta"}]
        concepts = [
            {"name": "개념A", "keyword": "Alpha", "aliases": ["shared"]},
            {"name": "개념B", "keyword": "Beta", "aliases": ["shared"]},
        ]
        result = audit_unit(chunks, concepts)
        self.assertEqual(result["identity_conflicts"][0]["term"], "shared")

    def test_workspace_audit_reports_scope_key_mismatch_without_writing(self):
        chunks = {("2026-1", "과목", "청크 단원"): [{"filename": "x", "page": 1, "text": "본문"}]}
        concepts = {("2026-1", "과목", "개념 단원"): [{"name": "본문", "keyword": "본문"}]}
        result = audit_workspace(chunks, concepts)
        self.assertEqual(result["summary"]["chunk_only_scope_count"], 1)
        self.assertEqual(result["summary"]["concept_only_scope_count"], 1)
        self.assertEqual(result["summary"]["external_api_calls"], 0)

    def test_korean_registry_scope_with_latin_only_chunks_flags_language_loss(self):
        scope = ("2026-1", "병태생리학", "염증")
        chunks = {scope: [{"filename": "x", "page": 1, "text": "Inflammation"},
                          {"filename": "x", "page": 2, "text": "Vasodilation"},
                          {"filename": "x", "page": 3, "text": "Chemotaxis"}]}
        concepts = {scope: [{"name": "염증", "keyword": "Inflammation"}]}
        result = audit_workspace(
            chunks,
            concepts,
            registry_scopes={scope},
            expected_languages={scope: "ko"},
        )
        self.assertTrue(result["units"][0]["suspected_language_loss"])
        self.assertEqual(result["summary"]["suspected_language_loss_unit_count"], 1)


if __name__ == "__main__":
    unittest.main()
