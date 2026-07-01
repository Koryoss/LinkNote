# Nursing Clinical Reflection

This document describes a future nursing-practice learning mode for LinkNote users whose `student_track` is `nursing`.

Clinical Reflection is planned as an educational add-on. It must not change existing LinkNote features such as PDF upload, RAG question answering, concept extraction, concept graph, My Page, recall traces, or explanation feedback.

## Purpose

Nursing Clinical Reflection will help nursing students connect clinical practice experiences with previously learned concepts from their uploaded materials.

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

## Intended User Flow

1. Student enters a clinical practice situation.
2. Student writes their own nursing reasoning or judgment.
3. System retrieves related concepts/chunks from uploaded materials.
4. System suggests linked concepts.
5. System provides educational feedback.
6. Reflection is saved as learning history.
7. My Page summarizes clinical reflection activity.

## Proposed Data Model

```json
{
  "id": "...",
  "user_id": "<server-derived data_user_id>",
  "date": "...",
  "course": "성인간호학 실습",
  "clinical_area": "...",
  "patient_context": "...",
  "student_reasoning": "...",
  "linked_concepts": [],
  "related_sources": [],
  "feedback": {
    "good_points": [],
    "missed_connections": [],
    "clinical_cues_to_check": [],
    "exam_connections": [],
    "followup_question": ""
  },
  "created_at": "..."
}
```

Ownership rule:

- `user_id` must not come from the frontend.
- Future APIs must always use `Authorization` token -> `current_user` / `current_uid()` -> `data_user_id`.
- General users must not receive access to nursing-only reflection records unless their profile is explicitly changed to `student_track = "nursing"`.

## Future API Plan

These endpoints are design targets only. They are not implemented yet.

- `POST /clinical-reflections`
- `GET /clinical-reflections`
- `POST /clinical-reflections/feedback`

The future API should reuse existing LinkNote ownership rules and should not accept frontend-provided `user_id` as an authority for protected data access.

## Feedback Format

Future feedback should include:

1. 잘 연결한 점
2. 놓친 개념
3. 확인해야 할 임상 단서
4. 시험 연결 포인트
5. 다시 생각해볼 질문

## My Page Future Summary

Future My Page nursing fields may include:

- `clinical_reflection_count`
- `last_clinical_reflection_at`
- `frequently_linked_concepts`
- `weak_clinical_concepts`
- `recent_clinical_reflections`

These fields should be added only when the reflection storage/API layer exists.

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

## Current PR Boundary

This PR intentionally does not add:

- `POST /clinical-reflections`
- `GET /clinical-reflections`
- `POST /clinical-reflections/feedback`
- AI clinical feedback generation
- patient data storage
- a full Clinical Reflection page
- clinical decision support behavior

The current UI only exposes a nursing-only placeholder entry point for future work.
