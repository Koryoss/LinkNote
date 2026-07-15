# LinkNote Architecture Notes

This document describes the current LinkNote repository shape and records a file audit for the next SCiyl-inspired active learning stage. It is intentionally documentation-only: no recall API, schema, UI button, or runtime behavior is implemented here.

## Current Development Stage

LinkNote is currently in a local-first, single-user evaluation stage.

The active development surface is centered on:

- PDF upload and ingestion
- PDF chunking
- ChromaDB indexing
- Retrieval-augmented generation (RAG)
- Concept extraction
- Concept graph construction
- My Library and study UI experiments

`user_id` exists in parts of the codebase, but it should be treated as a temporary identifier for local testing and evaluation. It is not yet a full authentication account, multi-user storage boundary, or production user database key.

The near-term priority is stabilizing the learning workflow: document ingestion, retrieval, concept extraction, graph navigation, and user-facing study flows. A full account system, authentication model, and user-specific database design are intentionally deferred to a later phase.

## Current File And Folder Roles

The following inventory is based on the current LinkNote structure targeted by this documentation pass. Before deleting or moving files, verify references with `rg` and run the app/test flow.

| Path | Role |
| --- | --- |
| `api_server.py` | Main backend API entry point for local/server execution. Expected to coordinate upload, RAG, concept, graph, and UI-facing endpoints. |
| `app.py` | App entry point or earlier prototype runtime. Keep until its relationship to `api_server.py` is confirmed. |
| `auth.py` | Lightweight identity helper. At this stage, `user_id` should remain a temporary local identifier rather than a full auth system. |
| `rag.py` | Retrieval and answer-generation logic around indexed document chunks. |
| `search_engine.py` | Deterministic search intent, alias expansion, hybrid scoring, score reasons, and bounded personalization. |
| `pdf_loader.py` | PDF ingestion and text/chunk extraction support. |
| `reset_db.py` | Local database/index reset utility for development. Treat as a dev tool, not production runtime behavior. |
| `requirements.txt` | Python dependency list for backend/local runtime. |
| `render.yaml` | Render deployment configuration. Keep aligned with the actual deploy target. |
| `web/` | Web frontend surface. Likely contains the browser UI for gallery, graph, and study workflows. |
| `desktop/` | Desktop app surface and packaging/runtime code. |
| `providers/` | Provider abstraction layer for model APIs, storage providers, or other integrations. |
| `data/` | Local JSON or generated data artifacts. Future lightweight recall traces can start here, but this pass does not add them. |
| `chroma_db/` | Local ChromaDB persistence directory for indexed chunks/embeddings. Usually should be treated as generated local state. |
| Existing documentation files | README, notes, prompt docs, deployment notes, or experiment records. These should be merged into `docs/` or `prompts/` when still useful. |

Current search-only state is generated locally: `data/search_cache.json`, `data/search_events.json`, and `data/search_profiles.json` are runtime data under the already ignored `data/` boundary. They must not be committed or treated as shared evaluation fixtures. Stable search evaluation inputs belong under `tests/fixtures/`.

## File Audit

This audit does not delete or move files. It classifies current repository areas by likely responsibility and recommends the next cleanup action.

### A. Core Runtime Files

| Path | Current role | Reference status | Move/delete impact | Recommended action |
| --- | --- | --- | --- | --- |
| `api_server.py` | Backend API runtime and local server entry point. | Expected to be directly executed or deployed. Confirm with README/deploy scripts. | High impact if renamed or moved. | Keep in root until a service/module split is planned and tested. |
| `app.py` | App/prototype entry point or alternate runtime. | Needs verification against README, Render, and local run commands. | Medium to high if still used by local workflows. | Keep for now; document whether it is current, legacy, or Streamlit/prototype. |
| `auth.py` | Lightweight user identity helper. | Expected import from API or UI-serving code. | Medium; changing semantics could break local `user_id` flows. | Keep lightweight. Do not expand into full auth during the recall planning phase. |
| `rag.py` | RAG retrieval and answer logic. | Expected import from API server or app entry point. | High; core learning workflow depends on it. | Keep. Later move under `services/rag.py` only after import migration. |
| `pdf_loader.py` | PDF text extraction/chunk input helper. | Expected import from ingestion endpoints/scripts. | High for upload/indexing. | Keep. Later move under a services or ingestion module after tests. |
| `requirements.txt` | Python dependencies. | Used by local setup and deployment. | High if missing or stale. | Keep in root. Audit dependency drift separately. |
| `render.yaml` | Deployment config. | Used by Render deployments if active. | Medium/high if Render is current deploy path. | Keep; document active deployment path in `docs/deployment.md` later. |
| `web/` | Web UI. | Runtime frontend. | High. | Keep. Do not reorganize until route/build commands are documented. |
| `desktop/` | Desktop app. | Runtime desktop surface. | High if actively used. | Keep. Document desktop-specific build and dev commands. |
| `providers/` | Integration/provider abstraction. | Likely imported by backend or desktop/web bridges. | Medium/high. | Keep; add provider contract docs later. |
| `data/` | Local generated and seed data. | May be read by concept graph or prototypes. | Medium; deleting can remove local evaluation data. | Keep; separate generated, seed, and future recall trace files. |
| `chroma_db/` | Local vector index persistence. | Read by ChromaDB runtime. | Medium; deleting resets local index. | Keep as generated storage. Consider `.gitignore` if committed accidentally. |

### B. Documentation To Keep Or Merge

| Path | Current role | Reference status | Move/delete impact | Recommended action |
| --- | --- | --- | --- | --- |
| `README.md` | Project entry documentation. | Primary human entry point. | High for onboarding. | Keep concise. Link to focused docs instead of carrying all architecture detail. |
| `docs/architecture.md` | Architecture and audit notes. | Human reference. | Low runtime impact. | Keep as the canonical structure overview. |
| `docs/roadmap.md` | Development roadmap and SCiyl-inspired phases. | Human planning reference. | Low runtime impact. | Keep as the canonical next-stage plan. |
| `docs/file-organization.md` | Cleanup criteria and target structure. | Human planning reference. | Low runtime impact. | Keep for future cleanup PRs. |
| Existing deployment notes | Deployment/run instructions. | Useful if still accurate. | Low runtime impact, high onboarding impact. | Merge into `docs/deployment.md` if duplicated across README/notes. |
| Existing API notes | Endpoint descriptions or examples. | Useful for backend/frontend coordination. | Low runtime impact. | Merge into `docs/api.md` when endpoints stabilize. |

### C. Prompt / AI Development Files

| Path | Current role | Reference status | Move/delete impact | Recommended action |
| --- | --- | --- | --- | --- |
| Concept extraction prompts | Prompts used to extract concepts from chunks. | May be embedded in Python files or standalone notes. | Medium if code reads prompt files directly. | Move or mirror into `prompts/concepts.md` after confirming references. |
| Concept graph prompts | Prompts for graph node/link generation. | May be embedded or documented. | Medium if runtime-loaded. | Move or mirror into `prompts/concept_graph.md`. |
| RAG answer prompts | Prompts for answer generation. | May be embedded in `rag.py` or provider code. | Medium/high if runtime-loaded. | Keep near code until prompt loading strategy is clear; document in `prompts/`. |
| Future recall feedback prompts | Not implemented yet. | No runtime reference should exist in this pass. | No current runtime impact. | Plan as `prompts/recall_feedback.md`, but do not wire into code yet. |
| Experiment prompt notes | Development-only prompt trials. | Usually not imported. | Low if archived correctly. | Move to `prompts/experiments/` or merge useful parts into canonical prompt docs. |

### D. Cleanup Candidates

| Path | Current role | Reference status | Move/delete impact | Recommended action |
| --- | --- | --- | --- | --- |
| Duplicate README sections | Repeated architecture, deployment, or roadmap text. | Human-only unless copied into scripts. | Low runtime impact. | Merge into focused docs and keep README short. |
| Old prototype docs | Historical experiment notes. | Likely human-only. | Low runtime impact. | Archive under `docs/archive/` if still useful; delete only after review. |
| Generated local artifacts in `data/` | Local outputs, test data, extracted concepts, graph JSON. | May be read by prototypes. | Medium if examples depend on them. | Split into `data/examples/`, `data/generated/`, or document regeneration steps. |
| Committed `chroma_db/` contents | Vector index state. | Runtime can use it locally, but it is generated. | Medium locally; low if regenerable. | Treat as storage, not source. Confirm `.gitignore` and regeneration workflow before cleanup. |
| One-off reset or migration scripts | Development utilities. | May not be imported. | Medium if maintainers rely on them manually. | Keep if used in the next 3 months; otherwise move to `scripts/` or archive. |
| Unreferenced prompt drafts | Prompt experiments not used by runtime. | Usually not imported. | Low. | Move to `prompts/experiments/` or remove after review. |

## Cleanup Verification Checklist

Before moving or deleting any file:

- Check whether it will be used in the next 3 months.
- Check whether it is directly required to run the app.
- Check whether any code imports or references it.
- Check whether the content duplicates README or `docs/`.
- Classify it as an experimental prompt, runtime prompt, developer note, or user-facing doc.
- Decide whether deletion is safe or whether the file should move to `docs/`, `prompts/`, `scripts/`, or `archive/`.
