# LinkNote

LinkNote is a lightweight local-first learning system for PDF-based study workflows.

The current project is focused on stabilizing a single-user/local evaluation flow before building a full multi-user service. It supports the core knowledge infrastructure pieces: PDF upload, chunking, ChromaDB indexing, retrieval-augmented generation, concept extraction, concept graph data, and gallery-style study UI experiments.

`user_id` may appear in code or metadata, but it is currently a temporary local identifier. It is not yet a full authentication account, production user database key, or multi-user storage boundary.


## User Profile Tracks

LinkNote user accounts include a lightweight `student_track` profile field. Allowed values are:

- `general`: default mode for existing users and ordinary study workflows.
- `nursing`: additive mode for nursing students who may later use nursing-practice learning tools.

Existing users without `student_track` are treated as `general`. New email/password users can choose 일반 or 간호학과 during signup. Google signup currently defaults to `general`; profile editing can be added later. This field does not change `data_user_id`, ChromaDB ownership, PDF upload, RAG, concept extraction, concept graph, My Page, or explanation feedback behavior.

### Future Nursing Practice Mode

Nursing students may eventually get additional educational tools:

- Clinical Reflection
- practice situation input
- nursing reasoning feedback
- links to prior concepts from uploaded materials
- exam connection points
- My Page nursing practice summary

Clinical Reflection must be educational only. Users must not enter patient-identifying information such as patient name, resident number, hospital ID, phone number, exact room number, or other personal identifiers. This feature does not replace clinical judgment, instructor guidance, or hospital policy. See [Nursing Clinical Reflection Plan](docs/nursing-clinical-reflection.md).

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
│   └── recall_traces.json        # local recall trace store
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

Initial implementation added in the recall trace PR.

- Store a learner's own explanation for a concept.
- Suggested fields: `answer_text`, `concept`, `course`, `unit`, `semester`, `user_id`, `created_at`.
- Start with JSON or another local lightweight store.
- Do not add AI feedback in this phase.
- Current implementation uses `POST /recall-traces` and `GET /recall-traces` with the existing lightweight `user_id` string.

### Phase 2: AI Reflection Feedback

Initial implementation added in the recall feedback PR.

AI feedback is directional, not a strict grading system. It identifies:

- what was explained well
- missing or weakly connected concepts
- questions worth reconsidering
- source locations worth reviewing

The app keeps recall trace save/list working even when `OPENAI_API_KEY` is not available; `/recall-feedback` returns a clear error when the key is missing.

### Phase 3: Personalized Concept Graph

Initial lightweight implementation added after recall feedback.

Concept graph metadata now includes:

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
- Keep SCiyl-inspired recall work lightweight and local-first until the knowledge infrastructure is stable.
- Recall trace storage/query remains limited to JSON-backed `user_id` records. AI feedback and weak concept graph metadata are local-first extensions; full account-based multi-user storage remains later work.

## Maintainer Docs

- [API notes](docs/api.md)
- [Deployment notes](docs/deployment.md)
- [Repository audit](docs/repository-audit.md)

# LinkNote Development Roadmap

## Current Development Stage

LinkNote is currently focused on building a unified knowledge management system for lecture materials. The project has established the core infrastructure for document ingestion, concept extraction, semantic retrieval, and concept graph visualization.

### Completed Foundations

* PDF upload and document management
* Automatic chunk generation
* ChromaDB-based vector indexing
* Retrieval-Augmented Generation (RAG)
* Concept extraction from lecture materials
* Concept graph visualization
* Cross-course concept linking
* Gallery-based document browsing

At the current stage, LinkNote is designed primarily for **single-user development and evaluation**.

Although the API already accepts or derives a `user_id` / `data_user_id` value, this currently serves as a lightweight learning-data identifier rather than a complete authentication or multi-user storage system. Full account management hardening, production storage isolation, and user-specific databases are intentionally postponed until the core learning workflow has matured.

---

# Next Development Stage: SCiyl-inspired Active Learning Layer

With the knowledge infrastructure now in place, the next milestone is to transform LinkNote from a passive knowledge repository into an active learning environment.

This stage is inspired by ideas developed in **SCiyl**, where the emphasis is not simply on answering questions, but on externalizing and improving the learner's own thinking.

Rather than introducing a full production account system immediately, this layer will continue to operate using the existing lightweight `user_id` workflow.

---

## Phase 1 — Recall Trace

Introduce active recall for every concept.

Users can:

* Open a concept
* Explain it in their own words
* Save their explanation
* Review previous explanations over time

Each explanation becomes a **Recall Trace**, representing the learner's evolving understanding rather than a correctness score.

Initially, recall traces will be stored using the existing lightweight storage mechanism (JSON/local database), allowing rapid iteration before migrating to a production database.

This phase now has an initial local implementation for saving and listing recall traces. It intentionally does not add AI feedback, weak concept scoring, or a new auth/user database.

---

## Phase 2 — AI Reflection Feedback

Once recall traces exist, LinkNote provides an initial reflective AI feedback endpoint.

Instead of grading answers as "correct" or "incorrect", the AI will generate:

* strengths of the explanation
* missing conceptual connections
* related concepts worth reviewing
* one follow-up question encouraging deeper reasoning

The objective is to guide thinking rather than evaluate performance. The current implementation uses `POST /recall-feedback`, requires `OPENAI_API_KEY`, and keeps recall trace storage/query working when the key is absent.

---

## Phase 3 — Personalized Concept Graph

Recall traces now lightly inform the concept graph without adding a new account database.

Each concept node can contain learning metadata such as:

* recall count
* last recalled date
* weak concept score
* missing conceptual links

This lets the concept graph begin representing the learner's understanding while keeping the current local `user_id` structure. The UI should avoid raw score labels and show softer states such as `미설명` or `설명 N회`.

---

## Future Work

After the active learning workflow is stable, LinkNote will gradually introduce:

* Authentication hardening
* Multi-user storage
* Cloud synchronization
* Personalized learning analytics
* Long-term learning history
* Collaborative concept sharing

The active learning workflow will be finalized before expanding into a full-scale multi-user platform.

---

## Long-term Vision

The long-term vision of LinkNote is to combine:

* Knowledge Management (LinkNote)
* Active Recall (SCiyl)
* Concept Graph Learning
* Retrieval-Augmented Generation

into a single environment where learners not only organize knowledge, but continuously construct, revisit, and refine their own understanding.
