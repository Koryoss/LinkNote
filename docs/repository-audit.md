# Repository Audit

This audit was performed with `rg` and `git ls-files` against the local LinkNote repository.

No files were deleted or moved as part of this audit.

## Import And Reference Summary

Core runtime references:

- `api_server.py` imports `pdf_loader.extract_pdf_text`.
- `api_server.py` imports RAG, concept, graph, chunk, unit, rename, and library helpers from `rag.py`.
- `api_server.py` imports `auth.py` for token verification, local registration/login, and Google login.
- `retrieval.py` imports `providers.hybrid_provider`.
- `providers/hybrid_provider.py` imports OpenAI answer generation and selects OpenAI or Ollama embedding.
- `rebuild_index.py` imports `pdf_loader.extract_pdf_text` and `rag.add_pdf_pages_to_db`.
- `fix_course.py` imports the ChromaDB `collection` from `rag.py`.

UI/API references:

- `web/gallery.html` calls `/library`, `/timetable`, `/units`, `/concepts`, `/ask`, `/file`, and auth endpoints.
- `api_server.py` serves `web/gallery.html` at `/`.
- `desktop/src-tauri/src/lib.rs` starts `uvicorn api_server:app` on port `8000`.

Documentation references:

- `DEPLOY_RENDER.md`, `UPLOAD_GUIDE.md`, `API_SERVER_README.md`, `API_SERVICE_BLUEPRINT.md`, `PROJECT_ROADMAP.md`, `OVERVIEW.md`, and `TEAM_ROLES.md` describe overlapping parts of the current runtime and deployment model.

## Prompt File Candidates

Current root prompt files:

- `COPILOT_PROMPT_concepts.md`
- `COPILOT_PROMPT_concept_graph.md`

Current role:

- These are development prompts for concept extraction and concept graph work.
- They are not runtime-loaded files according to the current reference audit.
- They document implementation history and expected behavior for `/reindex-concepts`, `/concepts`, `/reindex-graph`, and `/concept-graph`.

Recommended future structure:

```text
prompts/
├── concepts.md
└── concept_graph.md
```

Recommended action:

- Do not move them in this PR.
- In a cleanup PR, move them into `prompts/` and update README/doc links.
- Add `prompts/recall_feedback.md` only when Phase 2 AI reflection feedback work begins.

## Data And ChromaDB Boundaries

`git ls-files data chroma_db` returned no tracked files.

`.gitignore` excludes:

- `data/`
- `chroma_db/`

Local `data/` currently contains generated/local state such as:

- `concepts.json`
- `concept_index.json`
- `concept_links.json`
- `concepts_6units_backup.json`
- `timetable.json`
- `users.json`
- `uploads/`

Boundary decision:

- `data/` is local/generated runtime state.
- `data/uploads/` is local uploaded user material and should not be committed.
- `data/users.json` is local auth state and should not be committed.
- `data/concepts.json`, `data/concept_index.json`, and `data/concept_links.json` are generated learning artifacts and should not be committed unless a deliberate example fixture policy is created later.
- `chroma_db/` is generated vector index storage and should not be committed.

If examples are needed later, create a separate tracked location:

```text
examples/
└── data/
```

or:

```text
fixtures/
└── concept_graph/
```

Do not mix fixtures with real local learner data.

## Cleanup Candidates

Keep for now:

- `API_SERVER_README.md`
- `DEPLOY_RENDER.md`
- `UPLOAD_GUIDE.md`
- `PROJECT_ROADMAP.md`
- `API_SERVICE_BLUEPRINT.md`
- `TEAM_ROLES.md`
- `OVERVIEW.md`
- `PROD_CHECKLIST.md`

Merge candidates:

- `API_SERVER_README.md` can eventually merge into `docs/api.md`.
- `DEPLOY_RENDER.md` can eventually merge into `docs/deployment.md`.
- `UPLOAD_GUIDE.md` can eventually become a user workflow doc.
- `COPILOT_PROMPT_concepts.md` and `COPILOT_PROMPT_concept_graph.md` can move to `prompts/`.

Do not delete yet:

- `rebuild_index.py`
- `reindex_all.py`
- `reindex_all.sh`
- `fix_course.py`
- `migrate_to_openai_embeddings.py`
- `check_all.sh`

These scripts are development utilities and may still be useful for local recovery, migration, and verification.

## Recall Implementation Status

This audit originally marked Recall Trace as future work. The current backend and gallery UI now include a lightweight local recall layer:

- `POST /recall-traces` stores a learner explanation in `data/recall_traces.json`.
- `GET /recall-traces` lists explanation traces for the authenticated user's server-derived `data_user_id`.
- `POST /recall-feedback` generates SCiyl-style feedback and appends successful feedback calls as `feedback_type: "explain_concept"` records.
- `web/gallery.html` contains the existing `설명해보기` panel and calls the recall endpoints.
- `GET /me/summary` counts successful explanation feedback records for My Page.

The implementation remains local-first and JSON-backed. Future work may still add a full Recall History page or production storage, but the endpoint/UI presence above should be treated as implemented in the current codebase.

