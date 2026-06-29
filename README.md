# LinkNote

LinkNote is a lightweight local-first learning system for PDF-based study workflows.

The current project is focused on stabilizing a single-user/local evaluation flow before building a full multi-user service. It supports the core knowledge infrastructure pieces: PDF upload, chunking, ChromaDB indexing, retrieval-augmented generation, concept extraction, concept graph data, and gallery-style study UI experiments.

`user_id` may appear in code or metadata, but it is currently a temporary local identifier. It is not yet a full authentication account, production user database key, or multi-user storage boundary.

## Current Project Structure

LinkNote is currently organized as a lightweight local-first learning system.

```text
linknote/
├── api_server.py
├── app.py
├── auth.py
├── rag.py
├── pdf_loader.py
├── reset_db.py
├── requirements.txt
├── render.yaml
│
├── web/
│   ├── index.html
│   └── gallery.html
│
├── desktop/
│   ├── README.md
│   └── src-tauri/
│
├── providers/
│
├── data/
│   ├── concepts.json
│   ├── concept_index.json
│   ├── concept_links.json
│   └── recall_traces.json        # planned
│
├── chroma_db/
│
├── API_SERVER_README.md
├── API_SERVICE_BLUEPRINT.md
├── UPLOAD_GUIDE.md
├── PROJECT_ROADMAP.md
├── COPILOT_PROMPT_concepts.md
├── COPILOT_PROMPT_concept_graph.md
└── DEPLOY_RENDER.md
```

Note: the current `desktop-app` branch contains `web/app.js` and `web/gallery.html`. If `web/index.html` is restored or added on another branch, keep this section aligned with that branch's actual web entry files.

## Key Files

- `api_server.py`: FastAPI server entry point for web and desktop clients.
- `app.py`: Streamlit/local app surface and legacy local UI reference.
- `auth.py`: Lightweight local identity helper. This should stay small until full authentication is planned.
- `rag.py`: Retrieval and answer-generation logic.
- `pdf_loader.py`: PDF text extraction and ingestion support.
- `reset_db.py`: Local development utility for clearing generated state.
- `providers/`: Model provider layer for Ollama, OpenAI, or hybrid configurations.
- `web/`: Browser UI experiments, including the gallery view.
- `desktop/`: Desktop app shell and Tauri project.
- `data/`: Local JSON state for concepts, indexes, links, and future recall traces.
- `chroma_db/`: Local ChromaDB vector index storage.

## SCiyl-Inspired Direction

LinkNote is the knowledge infrastructure layer:

- ingest study materials
- chunk and index PDFs
- retrieve source-grounded context
- extract concepts
- build concept graph data
- provide web and desktop study surfaces

SCiyl inspires the active learning layer:

- ask the learner to explain concepts in their own words
- preserve thinking traces over time
- provide directional reflection rather than simple correct/incorrect grading
- eventually connect recall metadata back into the concept graph

The first version should remain lightweight and use the existing local `user_id` pattern. Full account management, authentication, and production user-specific storage are deferred.

## Planned Active Learning Phases

### Phase 1: Recall Trace

Planned only. Not implemented yet.

- Store a learner's own explanation for a concept.
- Suggested fields: `answer_text`, `concept`, `course`, `unit`, `semester`, `user_id`, `created_at`.
- Start with JSON or another local lightweight store.
- Do not add AI feedback in this phase.

### Phase 2: AI Reflection Feedback

Planned only. Not implemented yet.

AI feedback should be directional, not a strict grading system. It should identify:

- what was explained well
- missing or weakly connected concepts
- questions worth reconsidering
- source locations worth reviewing

The app should keep working even when `OPENAI_API_KEY` is not available.

### Phase 3: Personalized Concept Graph

Planned only. Not implemented yet.

Future concept graph metadata may include:

- `recall_count`
- `last_recalled_at`
- `weak_score`
- `missing_links`

## Existing Documentation

- `API_SERVER_README.md`: FastAPI server run instructions.
- `API_SERVICE_BLUEPRINT.md`: API service transition plan.
- `UPLOAD_GUIDE.md`: Upload workflow notes.
- `PROJECT_ROADMAP.md`: Broader product and architecture roadmap.
- `DEPLOY_RENDER.md`: Render deployment notes.
- `COPILOT_PROMPT_concepts.md`: Concept extraction prompt notes.
- `COPILOT_PROMPT_concept_graph.md`: Concept graph prompt notes.

## Current Priorities

- Keep the local-first learning workflow stable.
- Keep `user_id` lightweight until real authentication is needed.
- Avoid building a full account system too early.
- Keep SCiyl-inspired recall work as a planned layer until the knowledge infrastructure is stable.
- Do not implement recall APIs, database schema changes, or frontend recall controls until a later implementation PR.
