# Codex 스펙 — 학습 세션 & 간격 반복 (Phase 4–5)

목표: LinkNote 개념 그래프를 "지도 보기"에서 **학습 전략 도구**로 완성한다.
Phase 1–3(화면)은 Claude가 `web/concept-graph.html`에서 완료했다. 이 문서는 **데이터·로직(Codex 소유)** 인 Phase 4–5의 스펙과 착수 프롬프트다.

## 역할 경계 (TEAM_ROLES.md)
- Codex: `rag.py`, `api_server.py`의 로직·데이터, `data/*.json` 스키마, 새 엔드포인트.
- Claude: 화면(`web/*.html`)만. 아래 엔드포인트가 나오면 Claude가 UI를 붙인다.
- **불변 규칙(기존 문서 준수):** 기존 검색/임베딩/답변 함수 시그니처 유지, ChromaDB 초기화 금지, 한국어 출력 유지, 비용 드는 LLM 작업은 수동 트리거만. `review_priority`/`learning_state` 등 기존 필드는 제거하지 말고 확장만.

## 현재 상태 (읽고 시작할 것)
- `GET /concept-graph/overview` 가 노드마다 `learning_state`(NEW/LEARNING/REVIEW/MASTERED), `review_priority`(0–100 휴리스틱), `review_reason[]`, `recall_count`, `last_recalled_at`, `missing_links_count` 를 내려준다.
- 이 값들은 `_recall_metadata_for_scope()`(recall_traces 집계)와 `_learning_state_from_recall()`·`_review_priority_for_state()`(api_server.py 1217~)에서 계산된다. **현재는 마지막 설명 경과일·missing links 기반 휴리스틱이고, "다음 복습일" 개념이 없다.**
- recall 기록은 `_load_recall_traces()`/`_save_recall_traces()`로 `data/`에 JSON 저장된다.

---

## Phase 4 — 학습 세션 (Learning Session)

한 개념씩 순서대로 "설명해보기"를 진행하고 진행 상태를 저장하는 흐름.

### 4-1. 세션 생성
- `POST /learning-session/start`
  - body: `{ "scope": {"course"?, "unit"?}, "size": 7 }` (기본 size=7)
  - 동작: `overview`와 동일 로직으로 `review_priority` 상위 N개 개념을 뽑아 세션을 만든다.
  - `data/learning_sessions.json`에 저장. 스키마:
    ```json
    { "id":"sess_<uuid>", "user_id":"...", "created_at":"ISO",
      "scope":{"course":null,"unit":null},
      "items":[ {"concept_id":"...","concept":"신부전","course":"...","unit":"...",
                 "state_at_start":"REVIEW","status":"pending"} ],
      "cursor":0, "completed_at":null }
    ```
  - 응답: 세션 객체 전체.

### 4-2. 세션 진행/기록
- `POST /learning-session/{id}/advance`
  - body: `{ "concept_id":"...", "result":"explained" | "skipped" }`
  - 동작: 해당 item.status 갱신, cursor+1. `result:"explained"`면 기존 recall trace 저장 경로(`_save_recall_traces`)와 연결해 recall_count가 증가하도록 한다(별도 설명 텍스트가 있으면 recall-feedback 흐름 재사용).
  - 마지막 item이면 `completed_at` 설정.
  - 응답: 갱신된 세션 + `next_item`(없으면 null).

### 4-3. 세션 조회
- `GET /learning-session/current` — 미완료 세션 있으면 반환, 없으면 `{ "session": null }`.
- `GET /learning-session/{id}` — 단건.

### 4-4. UI 연동 지점 (Claude가 붙일 것)
- `concept-graph.html`의 큐 카드 "설명해보기"를 세션 흐름으로: "복습 시작" → 개념 1개씩 → 완료 표시 → 다음.
- 필요한 응답 필드: 위 스키마의 `items`, `cursor`, `next_item`, 각 item의 `state_at_start`.

---

## Phase 5 — 간격 반복 (Spaced Repetition, SM-2 기반)

`review_priority` 휴리스틱을 **"다음 복습일" 스케줄**로 승격한다.

### 5-1. 개념별 복습 스케줄 상태
- 새 파일 `data/review_schedule.json`. 개념 키(`user_id::concept_id`)마다:
  ```json
  { "concept_id":"...", "user_id":"...",
    "ease":2.5, "interval_days":0, "repetitions":0,
    "last_reviewed_at":"ISO", "due_at":"ISO" }
  ```
- 초기값: ease=2.5, interval=0, repetitions=0, due_at=now (즉시 복습 대상).

### 5-2. 복습 결과 반영 (SM-2)
- `POST /review/grade`
  - body: `{ "concept_id":"...", "quality": 0..5 }` (0=완전히 잊음 … 5=완벽)
  - SM-2 규칙으로 갱신:
    - `quality < 3` → repetitions=0, interval=1 (내일 다시)
    - `quality >= 3` → repetitions+1;
      - repetitions==1 → interval=1
      - repetitions==2 → interval=6
      - 그 외 → interval = round(prev_interval × ease)
    - `ease = max(1.3, ease + (0.1 - (5-quality)×(0.08 + (5-quality)×0.02)))`
    - `due_at = now + interval_days`
  - **quality를 사용자에게 직접 숫자로 묻지 말 것.** UI에서는 "다시 / 애매 / 알아요" 3버튼 → 각각 quality 2/3/5로 매핑해 보낸다(매핑은 Claude UI에서 처리, 엔드포인트는 quality만 받음).
  - 응답: 갱신된 스케줄 항목 + `due_at`.

### 5-3. 오늘 복습할 항목
- `GET /review/due?limit=20`
  - `due_at <= now`인 개념을 `due_at` 오름차순으로 반환.
  - 각 항목에 기존 `learning_state`, `review_reason`을 함께 붙여 UI가 이유를 표시할 수 있게 한다.
  - **호환:** 스케줄이 아직 없는 개념은 `_review_priority_for_state`로 fallback 정렬(기존 동작 유지). 즉 review_schedule.json이 비어도 지금 화면은 그대로 동작해야 한다.

### 5-4. review_priority와의 관계
- `review_priority`는 유지하되, **schedule이 있으면 due 여부를 우선**하도록 `overview`에 `is_due`(bool), `due_at`을 추가한다(필드 추가만, 기존 필드 제거 금지).
- `_ranking_info()`의 `algorithm_version`을 `..._v3_sm2`로 올리고 `score_components`에 `due_at` 추가.

---

## 착수 프롬프트 (Codex에게 그대로 전달)

```
LinkNote study-rag-api에서 학습 세션과 간격 반복을 추가한다.
제약: 기존 검색/임베딩/답변 함수 시그니처 유지, ChromaDB 초기화 금지, 한국어 출력 유지,
      LLM 비용 작업은 수동 트리거만. 기존 review_priority·learning_state·review_reason 필드는
      제거하지 말고 확장만. 아직 스케줄 데이터가 없어도 현재 /concept-graph/overview 응답과
      web/concept-graph.html 화면이 그대로 동작해야 한다(하위호환 필수).

Phase 4 (먼저):
- data/learning_sessions.json 스키마와 함께
  POST /learning-session/start, POST /learning-session/{id}/advance,
  GET /learning-session/current, GET /learning-session/{id} 구현.
- start는 /concept-graph/overview와 동일한 우선순위 로직(review_priority 상위 N)을 재사용.
- advance의 result:"explained"는 기존 recall trace 저장 경로와 연결해 recall_count가 늘도록.

Phase 5 (그다음):
- data/review_schedule.json (ease/interval_days/repetitions/last_reviewed_at/due_at).
- POST /review/grade (quality 0..5, SM-2 공식은 스펙 문서 5-2 그대로),
  GET /review/due?limit= (due_at<=now 오름차순, 없으면 review_priority fallback).
- /concept-graph/overview 노드에 is_due, due_at 필드 추가(기존 필드 유지).
- _ranking_info() algorithm_version을 concept_graph_learning_state_v3_sm2로.

각 엔드포인트는 응답 예시 JSON을 docstring에 남기고,
PROJECT_ROADMAP.md에 "개념 그래프 → 학습 전략 도구 전환 (세션/간격반복)" 항목을 추가한다.
```

---

## 완료 정의 (Definition of Done)
- [ ] Phase 4 엔드포인트 4종 동작, learning_sessions.json 생성/갱신 확인.
- [ ] Phase 5 엔드포인트 2종 동작, SM-2 계산 단위 테스트(quality 2/3/5 케이스) 통과.
- [ ] review_schedule.json이 비어도 기존 화면 정상 동작(하위호환).
- [ ] overview에 is_due/due_at 추가되었고 기존 필드 그대로.
- [ ] PROJECT_ROADMAP.md 갱신.
