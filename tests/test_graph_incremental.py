import json
import os
import tempfile
import unittest
from unittest.mock import patch

import rag


class IncrementalConceptGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = rag.DATA_DIR
        rag.DATA_DIR = self.temp_dir.name
        self.scope = ("user", "2026-2", "병태생리학 1", "혈액 및 림프계 질환")

    def tearDown(self):
        rag.DATA_DIR = self.original_data_dir
        self.temp_dir.cleanup()

    def _write(self, name, payload):
        with open(os.path.join(self.temp_dir.name, name), "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)

    def _read(self, name):
        with open(os.path.join(self.temp_dir.name, name), encoding="utf-8") as file:
            return json.load(file)

    def test_scope_embedding_replaces_only_target_nodes(self):
        user, semester, course, unit = self.scope
        self._write("concepts.json", {
            user: {semester: {course: {unit: [
                {"name": "급성 골수성 백혈병", "keyword": "AML", "weight": 4},
                {"name": "만성 림프구성 백혈병", "keyword": "CLL", "weight": 3},
            ]}}}
        })
        self._write("concept_index.json", [
            {"id": f"{user}::{semester}::{course}::{unit}::old", "user_id": user,
             "semester": semester, "course": course, "unit": unit, "embedding": [0, 1]},
            {"id": "keep", "user_id": user, "semester": semester,
             "course": "다른 과목", "unit": "다른 단원", "embedding": [1, 0]},
        ])

        with patch.object(rag, "embed_text", side_effect=[[1, 0], [0, 1]]) as embed:
            result = rag.build_concept_embeddings_for_scope(*self.scope)

        index = self._read("concept_index.json")
        self.assertEqual(embed.call_count, 2)
        self.assertEqual(result["replaced_count"], 1)
        self.assertEqual(len(result["target_nodes"]), 2)
        self.assertIn("keep", {node["id"] for node in index})
        self.assertNotIn(f"{user}::{semester}::{course}::{unit}::old", {node["id"] for node in index})

    def test_scope_links_preserve_unrelated_edges_and_use_no_llm(self):
        user, semester, course, unit = self.scope
        target_id = f"{user}::{semester}::{course}::{unit}::AML"
        other_id = f"{user}::{semester}::면역학::면역::염증"
        self._write("concept_index.json", [
            {"id": target_id, "user_id": user, "semester": semester, "course": course,
             "unit": unit, "name": "급성 골수성 백혈병", "weight": 4, "embedding": [1, 0]},
            {"id": other_id, "user_id": user, "semester": semester, "course": "면역학",
             "unit": "면역", "name": "염증", "weight": 4, "embedding": [0.9, 0.435889894]},
        ])
        self._write("concept_links.json", {"user_id": user, "edges": [
            {"a": "keep-a", "b": "keep-b", "score": 0.8},
            {"a": f"{user}::{semester}::{course}::{unit}::old", "b": other_id, "score": 0.8},
        ]})

        with patch.object(rag, "_verify_and_get_reason_with_llm") as verify:
            result = rag.build_cross_links_for_scope(*self.scope)

        edges = self._read("concept_links.json")["edges"]
        verify.assert_not_called()
        self.assertEqual(result["removed_edge_count"], 1)
        self.assertTrue(any(edge.get("a") == "keep-a" for edge in edges))
        self.assertTrue(any({edge.get("a"), edge.get("b")} == {target_id, other_id} for edge in edges))

    def test_move_graph_scope_reuses_embedding_and_remaps_edge(self):
        user, semester, course, unit = self.scope
        old_id = f"{user}::{semester}::{course}::{unit}::백혈병"
        neighbor_id = f"{user}::{semester}::면역학::면역::염증"
        target_scope = ("2026-2", "새 과목", "새 단원")
        new_id = f"{user}::{target_scope[0]}::{target_scope[1]}::{target_scope[2]}::백혈병"
        self._write("concept_index.json", [
            {"id": old_id, "user_id": user, "semester": semester, "course": course,
             "unit": unit, "name": "백혈병", "embedding": [0.1, 0.9]},
            {"id": neighbor_id, "user_id": user, "semester": semester, "course": "면역학",
             "unit": "면역", "name": "염증", "embedding": [0.2, 0.8]},
        ])
        self._write("concept_links.json", {
            "user_id": user, "edges": [{"a": old_id, "b": neighbor_id, "score": 0.8}],
        })

        result = rag.move_graph_scope(user, (semester, course, unit), target_scope)

        nodes = self._read("concept_index.json")
        edges = self._read("concept_links.json")["edges"]
        moved = next(node for node in nodes if node["id"] == new_id)
        self.assertEqual(moved["embedding"], [0.1, 0.9])
        self.assertEqual(result["moved_nodes"], 1)
        self.assertEqual(edges[0]["a"], new_id)

    def test_remove_graph_scope_preserves_other_nodes_and_edges(self):
        user, semester, course, unit = self.scope
        removed_id = f"{user}::{semester}::{course}::{unit}::백혈병"
        keep_a, keep_b = "keep-a", "keep-b"
        self._write("concept_index.json", [
            {"id": removed_id, "user_id": user, "semester": semester,
             "course": course, "unit": unit},
            {"id": keep_a, "user_id": user, "semester": semester,
             "course": "면역학", "unit": "면역"},
        ])
        self._write("concept_links.json", {"user_id": user, "edges": [
            {"a": removed_id, "b": keep_a, "score": 0.8},
            {"a": keep_a, "b": keep_b, "score": 0.7},
        ]})

        result = rag.remove_graph_scope(user, semester, course, unit)

        self.assertEqual(result, {"removed_nodes": 1, "removed_edges": 1})
        self.assertEqual([node["id"] for node in self._read("concept_index.json")], [keep_a])
        self.assertEqual(self._read("concept_links.json")["edges"], [
            {"a": keep_a, "b": keep_b, "score": 0.7},
        ])


if __name__ == "__main__":
    unittest.main()
