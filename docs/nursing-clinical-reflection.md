# Nursing Clinical Reflection Plan

This document describes a future nursing-practice learning track for LinkNote users whose `student_track` is `nursing`.

Clinical Reflection is planned as an educational add-on. It should not change existing LinkNote features such as PDF upload, RAG question answering, concept extraction, concept graph, My Page, or explanation feedback.

## Future Workflow

1. **실습 상황 입력**
   - The student enters a short, de-identified practice situation.
   - The input should focus on learning context and clinical cues, not patient identity.

2. **학생의 간호 판단 입력**
   - The student writes what they noticed, what they thought was happening, and what nursing action or priority they considered.

3. **기존 업로드 자료/RAG/개념 그래프와 연결**
   - LinkNote may search the student's uploaded PDFs and concept graph for related prior concepts.
   - Results should point back to existing materials, pages, courses, units, and concepts where possible.

4. **교육용 피드백 제공**
   - Feedback should help the student reflect on reasoning, missed cues, and concept links.
   - Feedback must be framed as learning support, not diagnosis, treatment direction, or clinical instruction.

5. **시험 포인트 연결**
   - The system may identify exam-relevant concepts connected to the scenario.
   - These links should support study planning and review.

## Proposed Data Model

```json
{
  "id": "...",
  "user_id": "<data_user_id>",
  "date": "...",
  "course": "...",
  "clinical_area": "...",
  "patient_context": "...",
  "student_reasoning": "...",
  "linked_concepts": [],
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

Ownership should use the authenticated account's server-derived `data_user_id`. Future APIs must not trust a frontend-provided `user_id` for ownership.

## Safety Notice

Clinical Reflection must be educational only.

Users must not enter patient-identifying information, including:

- 환자 이름
- 등록번호
- 주민등록번호
- 병원 ID
- 전화번호
- 정확한 병실번호
- any other direct or indirect personal identifier

This feature does not replace clinical judgment, instructor guidance, hospital policy, or licensed medical decision-making.

## Not Implemented In This PR

This PR intentionally does not add:

- `POST /clinical-reflections`
- AI clinical feedback generation
- patient data storage
- a full Clinical Reflection UI
- clinical decision support behavior

The current UI only exposes a nursing-only placeholder entry point for future work.
