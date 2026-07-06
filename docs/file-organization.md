# File Organization Plan

This document defines cleanup criteria and a gradual target structure for LinkNote. It is a plan, not a migration. Do not delete or move files as part of this documentation-only pass.

## Cleanup Criteria

Use these questions before changing any file location:

- Will this file be used in the next 3 months?
- Is it directly required to run the app?
- Is it imported or referenced by another file?
- Does it duplicate content that now belongs in README or `docs/`?
- Is it an experimental prompt or an actual user-facing document?
- Is deletion safe, or should it move to `docs/`, `prompts/`, `scripts/`, or `archive/`?

## Recommended Gradual Structure

The following structure is a direction for future cleanup PRs. It should be introduced incrementally after import references and run commands are verified.

```text
linknote/
├── README.md
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── roadmap.md
│   └── deployment.md
├── prompts/
│   ├── concepts.md
│   ├── concept_graph.md
│   └── recall_feedback.md
├── services/
│   ├── rag.py
│   ├── concepts.py
│   ├── graph.py
│   └── recall.py
├── web/
├── desktop/
├── providers/
├── storage/
│   ├── uploads/
│   └── chroma_db/
├── data/
│   ├── concepts.json
│   ├── concept_links.json
│   └── recall_traces.json
├── api_server.py
├── auth.py
└── requirements.txt
```

## Migration Notes

- Keep `api_server.py` in the root until server startup and deployment commands are stable.
- Move `rag.py` into `services/rag.py` only after imports are updated and tested.
- Add `services/recall.py` only when recall implementation begins in a later PR.
- Treat `chroma_db/` as generated storage, not core source code.
- Keep `data/` for small local JSON state and examples; avoid mixing large generated indexes with source-controlled examples.
- Create `prompts/` once prompt files are intentionally separated from runtime code.
- Prefer focused docs over a very long README.

