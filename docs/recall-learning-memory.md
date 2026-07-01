# Recall and Learning Memory

LinkNote's existing `설명해 보기` flow is now treated as the first version of a lightweight Learning Memory layer.

This layer is educational and reflective. It helps learners revisit how they explained a concept, what feedback they received, and what to focus on next. It is not a grading system and does not replace instructor judgment.

## Current Implementation

Implemented pieces:

- `POST /recall-traces` stores a learner's self-explanation in `data/recall_traces.json`.
- `GET /recall-traces` lists recent self-explanations for the authenticated user's server-derived `data_user_id`.
- `POST /recall-feedback` generates feedback for a successful explanation and stores one cumulative memory record.
- `GET /learning-memory/summary` returns a read-only summary for the authenticated user's Learning Memory.
- `web/learning-memory.html` displays the read-only Learning Memory page.
- My Page links to Learning Memory and shows high-level memory summary fields.

All protected recall and memory endpoints must derive ownership from:

```text
Authorization token -> current_uid() -> data_user_id
```

Frontend-provided `user_id` must not control protected ownership.

## Stored Feedback Fields

New feedback records remain backward compatible with existing `data/recall_traces.json` entries. Older records may not contain every field.

Successful `설명해 보기` feedback calls may store:

```json
{
  "id": "...",
  "user_id": "<server-derived data_user_id>",
  "semester": "2026-1",
  "course": "병리생리학1",
  "unit": "신부전",
  "concept": "사구체 여과율",
  "answer_text": "...",
  "feedback_text": "...",
  "good_points": [],
  "missing_links": [],
  "followup_question": "...",
  "improved_summary": "...",
  "review_hint": "...",
  "feedback_type": "explain_concept",
  "created_at": "..."
}
```

If feedback persistence fails, the API should still return generated feedback to the user and log the persistence error.

## Learning Memory Summary

`GET /learning-memory/summary` is read-only and rule-based. It does not require an OpenAI call.

The summary includes:

- total saved memories
- number of concepts explained
- recent memories
- weak concepts inferred from repeated missing links
- frequent missing links
- weekly summary text
- exam review focus candidates
- last memory timestamp

The summary is intentionally lightweight so it can work in local and desktop environments without additional infrastructure.

## Compatibility Notes

- Existing recall trace records remain valid.
- Existing uploaded PDFs, ChromaDB data, concepts, concept graph files, timetable data, and user mappings must not be moved or rebuilt for Learning Memory.
- The current implementation remains JSON-backed and local-first.
- A future production version may move Learning Memory to a database, but should preserve the same ownership rule: token-derived `data_user_id` only.

