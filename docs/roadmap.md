# LinkNote Roadmap: SCiyl-Inspired Active Learning Layer

This roadmap defines how LinkNote can add a SCiyl-inspired active learning layer without prematurely building a full multi-user platform. The reference project for this direction is `Koryoss/SCiyl`.

This document is planning-only. Do not implement recall APIs, database schemas, frontend controls, or AI feedback flows as part of this documentation pass.

## Product Direction

LinkNote should be treated as the knowledge infrastructure layer:

- ingest learning materials
- chunk and index PDFs
- retrieve relevant source context
- extract concepts
- build and browse concept graphs
- expose study surfaces through web and desktop UI

SCiyl should inspire the active learning and thinking trace layer:

- ask the learner to recall concepts in their own words
- preserve the learner's thinking traces over time
- reflect on gaps and missing links
- use the traces to personalize concept graph metadata

At the current stage, this should begin with the existing lightweight `user_id` pattern. `user_id` is a local/test identifier, not a production authentication account. Full authentication, account management, and user-specific storage isolation are later-stage work.

## Phase 1: Recall Trace

Goal: let a learner create lightweight recall traces for concepts.

Planned behavior:

- For a selected concept, the learner writes an explanation in their own words.
- The app stores the recall answer without AI grading.
- Storage starts as JSON or another local lightweight store.
- The trace is keyed by the existing temporary `user_id` pattern.

Planned fields:

- `answer_text`
- `concept`
- `course`
- `unit`
- `semester`
- `user_id`
- `created_at`

Non-goals for Phase 1:

- No AI feedback.
- No correctness grading.
- No full account system.
- No production multi-user database.

## Phase 2: AI Reflection Feedback

Goal: provide directional reflection on stored recall answers.

The feedback should not behave like a strict correct/incorrect grader. It should help the learner think more clearly by returning:

- what was explained well
- missing or weakly connected concepts
- questions worth reconsidering
- relevant source locations to review

Reliability requirement:

- The app must continue to work when `OPENAI_API_KEY` is absent.
- AI feedback should degrade gracefully into a disabled, skipped, or pending state.
- Upload, indexing, concept graph, gallery, and local recall storage should not fail because AI feedback is unavailable.

## Phase 3: Personalized Concept Graph

Goal: connect learning metadata from recall traces back into the concept graph.

Potential metadata:

- `recall_count`
- `last_recalled_at`
- `weak_score`
- `missing_links`

Expected result:

- The concept graph becomes personalized without requiring full multi-user infrastructure.
- Concepts can be visually or algorithmically prioritized based on lightweight study history.
- The graph remains grounded in LinkNote's knowledge infrastructure while SCiyl-inspired traces add learning behavior over time.

## Deferred Work

The following should be intentionally deferred until the local-first workflow is stable:

- full authentication
- account signup/login flows
- production user database schema
- user-specific storage isolation
- collaborative or multi-tenant study spaces
- recall grading as correctness judgment

