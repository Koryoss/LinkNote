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
- `feedback_type`

Learning Memory normalizes missing fields to empty strings or empty arrays so old records remain readable.

## Rule-Based Summary

The first version does not call GPT/OpenAI for page load.

`GET /learning-memory/summary` uses local rules:

- total memories from recall records
- concepts explained from unique concept names
- weak concepts from repeated missing links
- frequent missing links by count
- weekly summary from recent memories
- exam review focus from missing links and weak concepts

AI-generated summaries can be added later only when the user explicitly asks for them.

