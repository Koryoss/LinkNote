# Nursing Clinical Reflection

This document describes the first nursing-practice learning mode for LinkNote users whose `student_track` is `nursing`.

Clinical Reflection is an educational add-on. It does not change existing LinkNote features such as PDF upload, RAG question answering, concept extraction, concept graph, My Page, recall traces, or explanation feedback.

## Purpose

Nursing Clinical Reflection helps nursing students connect de-identified clinical practice experiences with previously learned concepts from their uploaded materials.

The goal is not to produce diagnosis, prognosis, treatment plans, medical orders, or clinical decision directives. The goal is to help a learner reflect on their reasoning and connect a practice situation back to study materials, concept graphs, and exam-relevant points.

## Difference from Concept Explanation

Concept Explanation / 설명해 보기:

- concept-level self explanation
- AI feedback about conceptual understanding
- focused on one concept, course, unit, and answer text

Clinical Reflection:

- case/practice-situation-level reflection
- starts from a de-identified nursing practice situation
- connects symptoms, assessment data, nursing judgment, pathophysiology, pharmacology, nursing process, and exam points
- links the reflection back to uploaded PDFs, RAG chunks, and concept graph data

## Implemented User Flow

1. Student enters a clinical practice situation.
2. Student writes a learning goal or reasoning focus.
3. System runs a rule-based identifier safety screen.
4. System retrieves related concepts, chunks, and Learning Memory records from the current user's materials.
5. Student explicitly submits the reflection, then GPT generates educational feedback.
6. Reflection is saved as learning history only after feedback succeeds.
7. My Page links nursing users to Clinical Reflection and shows recent saved reflection count.

## Data Model

```json
{
  "id": "...",
  "user_id": "<server-derived data_user_id>",
  "student_track": "nursing",
  "situation_text": "...",
  "learning_goal": "...",
  "selected_course": "성인간호학 실습",
  "selected_unit": "...",
  "related_concepts": [],
  "related_sources": [],
  "feedback": {
    "knowledge_connections": [],
    "nursing_process_links": [],
    "missed_assessment_cues": [],
    "safe_next_questions": [],
    "review_focus": [],
    "source_hints": [],
    "educational_summary": ""
  },
  "safety_flags": [],
  "created_at": "..."
}
```

Ownership rule:

- `user_id` must not come from the frontend.
- Future APIs must always use `Authorization` token -> `current_user` / `current_uid()` -> `data_user_id`.
- General users must not receive access to nursing-only reflection records unless their profile is explicitly changed to `student_track = "nursing"`.

## API

Implemented endpoints:

- `POST /clinical-reflection`
- `GET /clinical-reflections`

Both endpoints require a logged-in nursing user. They reuse existing LinkNote ownership rules and do not accept frontend-provided `user_id` as an authority for protected data access.

`POST /clinical-reflection` calls GPT only after:

1. authentication passes
2. `student_track == "nursing"`
3. `situation_text` is non-empty
4. the identifier safety screen passes
5. related current-user sources are retrieved

`GET /clinical-reflections` reads saved records only and does not call GPT.

## Feedback Format

Feedback includes:

1. `knowledge_connections`
2. `nursing_process_links`
3. `missed_assessment_cues`
4. `safe_next_questions`
5. `review_focus`
6. `source_hints`
7. `educational_summary`

## My Page Summary

My Page shows a nursing-only Clinical Reflection card with:

- saved reflection count from recent records
- latest reflection date
- link to `web/clinical-reflection.html`

Future versions may add deeper summaries such as frequently linked concepts and review-needed clinical concepts.

## Safety / Privacy Notice

Users must not enter patient-identifying information, including:

- 환자 이름
- 등록번호
- 주민등록번호
- 전화번호
- 정확한 병실번호
- 병원 ID
- any other direct or indirect personal identifier

This feature is for learning reflection only. It does not replace clinical judgment, instructor guidance, hospital policy, or licensed medical decision-making.

The system should provide educational feedback, not medical orders or clinical decision directives.

## Current Boundary

The first implementation intentionally does not add:

- clinical decision support behavior
- diagnosis, prognosis, treatment, prescription, or clinical order generation
- patient-identifiable data collection
- automatic GPT calls on page load
- access for non-nursing users

The UI exposes a nursing-only Clinical Reflection page. Page load and history viewing do not call GPT; feedback generation calls GPT only when the student submits a reflection.
