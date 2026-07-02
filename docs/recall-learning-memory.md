# Recall and Learning Memory

LinkNote's `설명해보기` flow creates recall traces. Learning Memory turns those traces and successful AI feedback into reusable study material for lecture review, weekly review, and exam preparation.

This is not only a history log. The goal is to help the learner reuse:

- their own explanations
- AI feedback from `설명해보기`
- missing links
- follow-up questions
- improved summaries and review hints when available

## Current Implementation

Implemented endpoints:

- `POST /recall-traces`: stores a learner's explanation.
- `GET /recall-traces`: lists explanation traces for the authenticated user's current concept scope.
- `POST /recall-feedback`: generates directional feedback and stores it with the trace.
- `GET /learning-memory`: lists normalized Learning Memory records.
- `GET /learning-memory/summary`: returns rule-based review summary data.
- `POST /learning-memory/ai-summary`: generates an optional GPT-based AI Summary after an explicit button click.
- `GET /learning-memory/ai-summaries`: lists previously generated AI Summaries without a new GPT call.

All protected endpoints derive ownership from:

```text
Authorization token -> current_uid() -> data_user_id
```

Frontend-provided `user_id` must not control access.

## Data Compatibility

Older recall records may contain only:

- `id`
- `user_id`
- `semester`
- `course`
- `unit`
- `concept`
- `answer_text`
- `created_at`

Newer feedback records may also contain:

- nested `feedback`
- `feedback_text`
- `good_points`
- `missing_links`
- `followup_question`
- `improved_summary`
- `review_hint`
- `source_hint`
- `has_ai_feedback`
- `feedback_created_at`
- `feedback_type`

Learning Memory normalizes missing fields to empty strings or empty arrays so old records remain readable.

## Memory Cards

Each Learning Memory card shows two study surfaces side by side on wide screens:

- `내 설명`: the learner's original `answer_text`
- `AI 피드백`: saved feedback from `/recall-feedback`

The AI feedback area can include:

- 좋았던 점
- 더 연결해볼 점
- 다시 생각해볼 질문
- 개선 요약
- 복습 힌트
- 다시 볼 자료 / source hint

If a memory does not have AI feedback yet, the page shows `아직 AI 피드백이 없습니다.` and an explicit `AI 피드백 생성` button. That button calls `POST /recall-feedback` using the current user's token-derived ownership and the memory's semester, course, unit, concept, and answer text.

## Rule-Based Summary

The first version does not call GPT/OpenAI for page load.

`GET /learning-memory/summary` uses local rules:

- total memories from recall records
- concepts explained from unique concept names
- review concepts from repeated missing links
- frequent missing links by count
- weekly summary from recent memories
- exam review focus from missing links and REVIEW-state concepts

## Optional AI Summary

Learning Memory page load remains free of GPT/OpenAI calls. The page can read:

- saved Learning Memory records
- rule-based summary data
- previously generated AI Summaries
- saved AI feedback generated from `/recall-feedback`

GPT is called only when the user explicitly clicks one of these buttons:

- 이번 주 요약 생성
- 과목 요약 생성
- 시험 대비 요약 생성
- 복습 개념 요약 생성
- AI 피드백 생성

Generated summaries are appended to `data/learning_memory_summaries.json` for reuse.

The AI Summary request can filter by:

- `summary_type`: `weekly`, `course`, `exam`, or `weak_concepts` (legacy wire value for review concepts)
- `course`
- `unit`
- `date_from` / `date_to`
- `concepts`
- `max_items`

Ownership is still server-derived:

```text
Authorization token -> current_uid() -> data_user_id
```

The frontend must not send or control `user_id`. If `OPENAI_API_KEY` is missing, `POST /learning-memory/ai-summary` returns a clear `503` error. Existing summaries can still be viewed without GPT.
