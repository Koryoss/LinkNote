# LinkNote Architecture Notes

This document records small architecture decisions that affect data ownership and local file access.

## Frontend Entry Points

- `web/gallery.html` is the current main UI served at `/`.
- `web/mypage.html` is the read-only My Page.
- `web/concept-graph.html` is the read-only Concept Graph destination.
- `web/app.js` is a legacy experimental frontend and should not be used for authenticated production flow.

## Ownership For Protected Data

Protected APIs must derive the active learning-data namespace from the auth token:

```text
Authorization token -> current_user/current_uid() -> data_user_id
```

Frontend-provided `user_id` must not control protected data access. Any remaining `user_id` request fields are compatibility-only unless a route explicitly documents them as response/stored metadata.

The active Gallery UI should not send `user_id` in protected API query strings, JSON bodies, or upload forms. Upload, question, recall, library, timetable, concept, and chunk requests should rely on the `Authorization` header. Hidden or displayed user labels in the frontend are presentation/legacy compatibility only and are not authority for data access.

New protected features should not introduce frontend-provided `user_id` request fields. If a backend model keeps a `user_id` property for compatibility, it should be optional/deprecated and overwritten or ignored in favor of `current_uid()`.

## Question Modes

The gallery question UI separates search from generated answers:

- `빠른 검색` calls `POST /ask/search`.
- `AI 답변` calls the existing `POST /ask` flow.

`/ask/search` uses `Authorization` -> `current_uid()` -> `data_user_id`, searches only the current user's owned chunks, concepts, and Recall/Learning Memory records, and returns related sources without calling GPT chat/completion. The current implementation is local keyword/metadata search, so it also does not require `OPENAI_API_KEY` for page load or search-only results.

Search-only results are cached in `data/search_cache.json` by `data_user_id`, normalized question, search filter, and scope. Sensitive patient/clinical-looking queries are not cached. Repeating the same owned search can return `from_cache = true`.

Single-document style search uses the selected semester/course/unit/file filter when present. Multi-document search ignores the UI course filter and searches across the current user's owned materials.

## Concept Graph Destination

My Page and Learning Memory link to `web/concept-graph.html` instead of the gallery root hash. The page reads `GET /concept-graph/overview`, which derives ownership from `Authorization` -> `current_uid()` -> `data_user_id`.

The graph destination is read-only. It uses existing `data/concept_index.json`, `data/concept_links.json`, and recall metadata. Viewing the graph does not call GPT, OpenAI embeddings, reindex ChromaDB, or rebuild concept graph data.

The default Concept Graph view caps visible nodes and prioritizes learner-useful node types: Weak, Core, Bridge, Recent, Recalled, and New. `GET /concept-graph/overview` computes the ranking metadata on the backend from existing graph files and Learning Memory metadata, then the frontend renders that read-only result.

Each overview node may include deterministic learning metadata such as `degree`, `weighted_degree`, `connected_count`, `centrality_score`, `bridge_score`, `memory_score`, `review_score`, `priority_score`, `node_types`, `why_shown`, and `recommended_action`. Each overview edge may include `normalized_weight`, `edge_type`, and a human-readable `reason`. These fields are computed without GPT/OpenAI calls and without rebuilding or reindexing stored graph data.

### Concept Graph Progressive Disclosure

The Concept Graph page does not show the full graph first. The default mode is `Review Map`, a small learning map focused on what the learner should review now. It prioritizes `review_score`, `weak_score`, missing links, recall history, and not-yet-explained concepts, with core/bridge metadata used as tie-breakers.

Primary graph modes are Review Map, Core Map, Connection Map, Learning Memory Map, and New Concepts. Full Graph is an advanced action and remains capped so the page stays readable. The purpose is learning navigation, not complete visualization. Viewing or switching graph modes does not call GPT/OpenAI.

## PDF Preview Access

`GET /file` accepts a query token because PDF iframe previews cannot attach custom request headers.

The backend still resolves that query token to the current `data_user_id`, validates the requested filename, checks the user's existing library/chunk metadata for ownership, and only then resolves a physical file in `data/uploads`.

Existing uploads remain in place. The storage layout is not changed:

- metadata may store the original filename, such as `lecture.pdf`
- `data/uploads` may store a UUID-prefixed file, such as `<uuid>_lecture.pdf`

The server may match the exact stored filename or UUID-prefixed suffix only after ownership has been verified. Unknown filenames, missing/invalid tokens, path traversal, and files not owned by the current `data_user_id` must not be served.

## Data Preservation

Ownership hardening must not delete, move, rename, rebuild, re-index, clean up, or migrate existing uploaded files or analysis data.
