# LinkNote API Notes

This document summarizes the current FastAPI surface in `api_server.py`. It is a documentation snapshot, not a contract freeze.

## Runtime

Run locally:

```bash
uvicorn api_server:app --reload --port 8000
```

The desktop app starts the same backend through Tauri. Render starts it with:

```bash
uvicorn api_server:app --host 0.0.0.0 --port $PORT
```

## Identity Model

Most application endpoints depend on `current_uid`, which reads `Authorization: Bearer <token>` and resolves it through `auth.py`.

`auth.py` stores users in `data/users.json`. Each user has a `data_user_id`, and that value is used as the storage identifier for ChromaDB records and local JSON data.

Important current-state note:

- LinkNote now has lightweight local auth/token support.
- `data_user_id` still acts as the learning-data identifier.
- This is not yet a mature multi-user storage system with production-grade account boundaries, migrations, or separate user databases.
- The SCiyl-inspired recall layer should continue to use this lightweight identifier until the learning workflow is stable.

## Core Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/ask` | Answer a question from indexed source chunks. Supports normal and connection-focused modes. |
| `POST` | `/ingest` | Upload a PDF, extract text, chunk/index pages, and optionally build concepts for the submitted unit. |
| `GET` | `/library` | Return the current user's indexed library overview. |
| `DELETE` | `/library` | Delete indexed chunks matching a search filter for the current user. |
| `GET` | `/chunks` | Inspect indexed chunks with optional filters and pagination. |
| `GET` | `/file` | Serve an uploaded PDF file for preview. Uses a query token for iframe access. |
| `POST` | `/rename-unit` | Rename a unit in ChromaDB metadata and update concept JSON when present. |

## Timetable Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/timetable` | Return courses grouped by semester for the current user. |
| `GET` | `/timetable/entries` | Return raw timetable entries for the current user. |
| `POST` | `/timetable/entries` | Add a timetable entry. |
| `PUT` | `/timetable/entries` | Update a timetable entry by user-local index. |
| `DELETE` | `/timetable/entries` | Delete one or all timetable entries for the current user. |

## Concept And Graph Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/units` | Return detected units for a semester/course. |
| `POST` | `/reindex-concepts` | Rebuild concept extraction data into `data/concepts.json`. This may require `OPENAI_API_KEY`. |
| `GET` | `/concepts` | Return extracted concepts for a semester/course/unit. |
| `POST` | `/reindex-graph` | Build `data/concept_index.json` and `data/concept_links.json` from extracted concepts. |
| `GET` | `/concept-graph` | Return concept graph nodes and edges for visualization, including lightweight recall metadata. |

## Recall Trace Endpoints

These endpoints intentionally use the existing lightweight `user_id` string and do not create a new auth/user database. Phase 1 trace save/list does not call OpenAI and works without `OPENAI_API_KEY`; `/recall-feedback` is the Phase 2 AI endpoint and returns a clear error when the key is unavailable.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/recall-traces` | Store a learner's explanation for a concept in `data/recall_traces.json`. |
| `GET` | `/recall-traces` | List recent recall traces by `user_id`, `semester`, `course`, and `unit`; optional `concept` and `limit`. |
| `POST` | `/recall-feedback` | Generate SCiyl-style directional AI feedback for a saved recall answer. Requires `OPENAI_API_KEY`. |


Concept responses may include these recall metadata fields per node/concept:

- `recall_count`: number of saved recall traces for that concept in the same user/semester/course/unit scope.
- `last_recalled_at`: most recent saved recall timestamp, or `null`.
- `missing_links_count`: count of missing links from stored AI feedback attached to matching traces.
- `weak_score`: local rule-based score from 0-100 for internal prioritization. The UI should prefer state labels such as `미설명` or `설명 N회` instead of exposing the raw score.
- `feedback` and `feedback_created_at`: optional saved AI feedback attached to the trace after `/recall-feedback` is generated.
- `/recall-feedback` may receive optional `trace_id`; when present, feedback is persisted directly to that recall trace.

## Auth Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/auth/register` | Register a lightweight local user. |
| `POST` | `/auth/login` | Login and receive a token. |
| `GET` | `/auth/me` | Resolve the current token to a public user object. |
| `GET` | `/auth/config` | Return public Google client configuration. |
| `POST` | `/auth/google` | Login/register with a Google ID token. |

## Static UI

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Serve `web/gallery.html`. |
| static mount | `/...` | Serve files from `web/`. |

Current `desktop-app` branch files under `web/`:

- `web/gallery.html`
- `web/app.js`

Some older docs mention `web/index.html`; update those references when the web entry files are finalized.

## SCiyl Boundary

Phase 1 recall trace storage/query is implemented as a local JSON-backed layer only. Phase 2 adds directional recall feedback through `/recall-feedback`. The graph now includes lightweight local recall metadata (`recall_count`, `last_recalled_at`, `weak_score`) without adding auth/user DB expansion. `weak_score` is treated as internal prioritization metadata, while the UI uses learner-friendly state labels.

