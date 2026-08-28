import asyncio
import os
import shutil
import tempfile
import unittest

import api_server


class LearningMemoryDeleteApiTests(unittest.TestCase):
    """Legacy delete routes and the current POST-only bulk routes.

    Added because DELETE-with-JSON-body (the original /learning-memory route)
    doesn't reliably deliver its body in every WebView — Tauri's WKWebView
    included. These two routes avoid a body on DELETE entirely; the legacy
    body-based route is kept for older installed builds and is covered here
    too so it isn't silently broken by this change.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="linknote-memory-delete-", dir="/tmp")
        self.original_recall_path = api_server.RECALL_TRACES_PATH
        api_server.RECALL_TRACES_PATH = os.path.join(self.temp_dir, "recall_traces.json")

    def tearDown(self):
        api_server.RECALL_TRACES_PATH = self.original_recall_path
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed(self, traces):
        api_server._save_recall_traces(traces)

    def _explanation(self, trace_id, user_id, concept="심부전"):
        return {
            "id": trace_id, "user_id": user_id, "semester": "2026-1",
            "course": "병태생리학", "unit": "순환계", "concept": concept,
            "answer_text": "설명", "created_at": "2026-08-27T00:00:00+00:00",
            "feedback_type": "",
        }

    def _feedback(self, feedback_id, user_id, source_trace_id):
        return {
            "id": feedback_id, "user_id": user_id, "source_trace_id": source_trace_id,
            "feedback_type": "explain_concept", "created_at": "2026-08-27T00:01:00+00:00",
        }

    # ---- DELETE /learning-memory/all ----

    def test_delete_all_removes_owned_traces_and_linked_feedback(self):
        self._seed([
            self._explanation("t1", "user-1"),
            self._feedback("f1", "user-1", "t1"),
            self._explanation("t2", "user-2"),
        ])
        result = asyncio.run(api_server.delete_all_learning_memories(data_user_id="user-1"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted_count"], 2)  # t1 + its linked feedback f1
        remaining = api_server._load_recall_traces()
        self.assertEqual([item["id"] for item in remaining], ["t2"])

    def test_delete_all_is_a_no_op_error_free_when_user_has_nothing(self):
        self._seed([self._explanation("t1", "user-2")])
        result = asyncio.run(api_server.delete_all_learning_memories(data_user_id="user-1"))
        self.assertEqual(result, {"ok": True, "deleted_count": 0})
        self.assertEqual(len(api_server._load_recall_traces()), 1)

    def test_delete_all_never_cascades_into_another_users_feedback(self):
        # Contrived id collision: user-2 has a feedback row whose
        # source_trace_id happens to equal user-1's trace id. The cascade
        # must stay within data_user_id's own items, not follow the id
        # across the user boundary.
        self._seed([
            self._explanation("t1", "user-1"),
            self._feedback("f1", "user-1", "t1"),
            self._feedback("f-other", "user-2", "t1"),
        ])
        result = asyncio.run(api_server.delete_all_learning_memories(data_user_id="user-1"))
        self.assertEqual(result["deleted_count"], 2)  # t1 + f1 only, not f-other
        remaining = {item["id"] for item in api_server._load_recall_traces()}
        self.assertEqual(remaining, {"f-other"})

    # ---- POST /learning-memory/delete ----

    def test_post_delete_all_removes_owned_traces_and_linked_feedback(self):
        self._seed([
            self._explanation("t1", "user-1"),
            self._feedback("f1", "user-1", "t1"),
            self._explanation("t2", "user-2"),
        ])
        result = asyncio.run(api_server.post_delete_all_learning_memories(data_user_id="user-1"))
        self.assertEqual(result, {"ok": True, "deleted_count": 2})
        self.assertEqual([item["id"] for item in api_server._load_recall_traces()], ["t2"])

    def test_selected_delete_removes_only_requested_owned_ids(self):
        self._seed([
            self._explanation("t1", "user-1"),
            self._explanation("t2", "user-1"),
            self._explanation("t3", "user-1"),
        ])
        payload = api_server.LearningMemorySelectedDeleteRequest(ids=["t1", "t3"])
        result = asyncio.run(api_server.delete_selected_learning_memories(payload, data_user_id="user-1"))
        self.assertEqual(result["deleted_count"], 2)
        remaining = {item["id"] for item in api_server._load_recall_traces()}
        self.assertEqual(remaining, {"t2"})

    def test_selected_delete_skips_ids_owned_by_another_user_without_erroring(self):
        self._seed([
            self._explanation("mine", "user-1"),
            self._explanation("theirs", "user-2"),
        ])
        payload = api_server.LearningMemorySelectedDeleteRequest(ids=["mine", "theirs", "nonexistent"])
        result = asyncio.run(api_server.delete_selected_learning_memories(payload, data_user_id="user-1"))
        self.assertEqual(result["deleted_count"], 1)
        remaining = {item["id"] for item in api_server._load_recall_traces()}
        self.assertEqual(remaining, {"theirs"})

    def test_selected_delete_cascades_to_linked_feedback(self):
        self._seed([
            self._explanation("t1", "user-1"),
            self._feedback("f1", "user-1", "t1"),
        ])
        payload = api_server.LearningMemorySelectedDeleteRequest(ids=["t1"])
        result = asyncio.run(api_server.delete_selected_learning_memories(payload, data_user_id="user-1"))
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(api_server._load_recall_traces(), [])

    def test_selected_delete_requires_at_least_one_id(self):
        payload = api_server.LearningMemorySelectedDeleteRequest(ids=[])
        with self.assertRaises(api_server.HTTPException) as ctx:
            asyncio.run(api_server.delete_selected_learning_memories(payload, data_user_id="user-1"))
        self.assertEqual(ctx.exception.status_code, 400)

    # ---- Legacy DELETE /learning-memory (body-based) stays working ----

    def test_legacy_body_based_route_still_deletes_all(self):
        self._seed([self._explanation("t1", "user-1")])
        payload = api_server.LearningMemoryBulkDeleteRequest(delete_all=True)
        result = asyncio.run(api_server.delete_learning_memories(payload, data_user_id="user-1"))
        self.assertEqual(result["deleted_count"], 1)

    def test_legacy_body_based_route_still_404s_on_unowned_id(self):
        self._seed([self._explanation("theirs", "user-2")])
        payload = api_server.LearningMemoryBulkDeleteRequest(ids=["theirs"])
        with self.assertRaises(api_server.HTTPException) as ctx:
            asyncio.run(api_server.delete_learning_memories(payload, data_user_id="user-1"))
        self.assertEqual(ctx.exception.status_code, 404)

    # ---- Route registration order ----

    def test_all_route_is_registered_before_the_dynamic_id_route(self):
        delete_paths = [
            route.path for route in api_server.app.routes
            if getattr(route, "path", "").startswith("/learning-memory")
            and "DELETE" in getattr(route, "methods", set())
        ]
        self.assertIn("/learning-memory/all", delete_paths)
        self.assertIn("/learning-memory/{memory_id}", delete_paths)
        self.assertLess(
            delete_paths.index("/learning-memory/all"),
            delete_paths.index("/learning-memory/{memory_id}"),
            "/learning-memory/all must be registered before /learning-memory/{memory_id} "
            "or FastAPI will match 'all' as a memory_id.",
        )

    def test_post_delete_all_route_is_registered(self):
        post_paths = [
            route.path for route in api_server.app.routes
            if "POST" in getattr(route, "methods", set())
        ]
        self.assertIn("/learning-memory/delete-all", post_paths)


if __name__ == "__main__":
    unittest.main()
