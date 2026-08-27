# LinkNote Copilot Free 작업 지시서

## 사용 원칙

- 역할: 빌드 역할(코드 구현)
- `AGENTS.md`와 `material_registry.csv`는 존재하지만 별도 `SPEC` 파일은 없다.
- 한 번에 아래 작업 하나만 수행한다. 8단계를 한 프롬프트로 요청하지 않는다.
- 기존 사용자 데이터와 `data/*.json`, `chroma_db`, `data/uploads`는 직접 수정하지 않는다.
- 관련 없는 기존 변경을 되돌리거나 포맷하지 않는다.
- 실제 API 응답과 테스트를 근거로 구현하고, 예시 데이터를 제품 코드에 하드코딩하지 않는다.
- 완료 후 변경 파일, 테스트 결과, 남은 위험을 짧게 보고한다.

## Copilot에 맡길 범위

| 작업 | Copilot Free 적합도 | 검토 주체 |
| --- | --- | --- |
| 1. `concept_notes` 저장 구조·API·단위 테스트 | 높음 | Codex가 데이터 격리·삭제 범위 검토 |
| 2. 자료별 페이지·개념 작업대 API | 중간 | Codex가 실제 Chunk·개념 정렬 검토 |
| 3. 3열 학습 작업대 HTML/CSS 골격 | 높음 | 사용자와 Codex가 화면 검토 |
| 4. 출현 페이지·PDF 미리보기 연결 | 중간 | Codex가 파일 소유권·페이지 이동 검토 |
| 5. 노트 자동 저장 UI | 높음 | Codex가 실패·충돌 처리 검토 |
| 6. 선택 개념 1-hop 연결 | 중간 | Codex가 관계 의미·노드 제한 검토 |
| 7. 좁은 데스크탑 창 반응형 처리 | 높음 | 사용자와 Codex가 화면 검토 |
| 8. 기존 지도를 `연결 탐색`으로 변경 | 낮음 | 사용성 검증 후 Codex가 진행 |

---

## 작업 1 — 지금 Copilot에 보낼 프롬프트

```text
LinkNote 저장소에서 이번에는 concept_notes 저장 구조와 API만 구현해줘.
다른 구현 단계는 시작하지 마.

먼저 다음 파일을 읽어:
- AGENTS.md
- api_server.py
- tests/test_search_api.py
- data 저장 헬퍼와 Learning Memory CRUD 구현 부분

주의:
- 별도 SPEC 파일은 없다. AGENTS.md와 기존 API 패턴을 기준으로 한다.
- data/*.json, chroma_db, uploads의 실제 사용자 데이터는 수정하지 마.
- 기존의 관련 없는 변경을 되돌리거나 전체 파일을 포맷하지 마.
- user_id는 요청 body에서 받지 말고 current_uid 인증 결과만 사용해.
- Learning Memory와 concept note는 다른 데이터다. 기존 Learning Memory 구조를 재사용하거나 변경하지 마.

구현 요구사항:
1. 기본 경로는 DATA_DIR/concept_notes.json으로 한다.
2. 노트 식별자는 서버에서 UUID로 만든다.
3. 저장 필드:
   id, user_id, semester, course, unit, filename, concept,
   note_text, source_pages, created_at, updated_at
4. 같은 사용자의 같은 semester/course/unit/filename/concept 조합은 한 노트로 upsert한다.
5. 다른 사용자의 노트는 조회·수정·삭제할 수 없어야 한다.
6. API:
   GET /concept-notes
   PUT /concept-notes
   DELETE /concept-notes/{note_id}
7. GET 필터는 semester, course, unit, filename, concept를 선택적으로 지원한다.
8. note_text가 빈 문자열이면 허용하되 공백을 정리한다.
9. source_pages는 양의 정수만 중복 없이 오름차순으로 저장한다.
10. 파일 읽기·쓰기에는 threading.RLock을 사용해 기록 유실을 막는다.
11. 기존 JSON 저장 헬퍼와 FastAPI/Pydantic 스타일을 따른다.
12. 실제 데이터 파일이 아니라 임시 디렉터리를 사용하는 테스트를 추가한다.

필수 테스트:
- 한 사용자의 upsert가 중복 항목을 만들지 않음
- 사용자 간 노트 격리
- 다른 사용자 note_id 삭제 불가
- source_pages 정규화
- 필터 조회

완료 후:
- 변경 파일 목록
- 실행한 테스트와 결과
- 구현하지 않은 다음 단계
를 보고해. 테스트 실패를 숨기지 마.
```

## 작업 1 검수 기준

- 기존 `/learning-memory` 동작을 변경하지 않았다.
- `user_id`가 클라이언트 입력으로 신뢰되지 않는다.
- 다른 사용자 기록이 삭제되지 않는다.
- 테스트가 실제 `data/concept_notes.json`을 건드리지 않는다.
- 앱 서버 재시작 전에는 실제 데이터 마이그레이션을 실행하지 않는다.

---

## 작업 2 — 작업 1 승인 후 사용할 프롬프트

```text
작업 1이 승인되었다. 이번에는 학습 작업대용 읽기 API만 구현해줘.
UI는 만들지 마.

GET /study-workspace 요청에 필수 query인 semester, course, unit, filename을 받고,
current_uid로 확인한 현재 사용자의 자료에 대해서만 다음 구조를 반환해:

- scope: semester, course, unit, filename
- pages: page, title, text_preview, concepts
- concepts: name, definition, first_page, pages, occurrences, note

구조 규칙:
1. pages는 같은 page의 여러 chunk를 하나로 합치고 숫자 오름차순으로 정렬한다.
2. text_preview는 해당 페이지 chunk의 원래 순서를 유지해 합치되 최대 700자로 제한한다.
3. 페이지 제목은 확실한 제목 데이터가 있을 때만 사용하고, 불확실하면 `p.{page}`로 표시한다.
4. pages[].concepts는 저장된 개념의 occurrences/pages를 기준으로 연결한다.
   새로운 키워드 추출이나 의미 추론을 하지 마.
5. concepts는 first_page 오름차순, 같은 first_page에서는 저장된 개념 순서를 유지한다.
6. concepts[].note는 같은 사용자와 동일 semester/course/unit/filename/concept의
   concept note 한 건 또는 null이다. 별도 단일 note 필드는 만들지 마.
7. 원본 파일이 없더라도 소유 chunk가 있으면 작업대 데이터는 반환하고,
   원문 미리보기 가능 여부를 source_available boolean으로 구분한다.

기존 get_chunks, /concepts, _augment_concepts_with_page_locations,
/file 소유권 확인 방식을 재사용해. 별도 SPEC은 없다.

이 endpoint는 읽기 전용이다. LLM/GPT API, 임베딩 생성, OCR, 재추출,
개념 저장, 재색인을 호출하지 말고 실제 사용자 데이터도 수정하지 마.
임시 경로와 mock을 쓰는 단위 테스트를 추가해.

필수 테스트:
- 여러 chunk가 한 page 항목으로 합쳐지고 페이지가 숫자순으로 정렬됨
- 개념이 최초 등장 페이지 순서로 정렬됨
- 동일 범위의 concept note가 해당 concepts[].note에만 포함됨
- 다른 사용자의 chunk, 개념, 노트가 노출되지 않음
- 다른 사용자가 소유한 filename으로 조회하면 404
- endpoint 호출 중 OpenAI provider와 저장 헬퍼가 호출되지 않음

완료 후 변경 파일, 테스트 결과, 불확실한 제목 추출 한계를 보고해.
```

## 이후 작업의 짧은 지시

### 작업 3

`web/study-workspace.html`의 3열 골격만 만든다. 예시 학습 데이터를 하드코딩하지 않고 빈 상태·로딩·오류 상태까지 구현한다.

### 작업 4

`/study-workspace` 응답, 기존 `/file` 미리보기, 개념 `occurrences` 페이지 이동을 연결한다. 파일 소유권 로직은 변경하지 않는다.

### 작업 5

노트 입력을 500ms debounce로 `PUT /concept-notes`에 자동 저장한다. `저장 중…`, `저장됨`, `저장 실패`를 실제 요청 상태에 맞게 표시한다.

### 작업 6

선택 개념의 기존 연결 중 최대 6개만 표시한다. `선수 개념`이라는 새 의미를 추론하지 말고 현재 관계 라벨을 그대로 사용한다.

### 작업 7

1240px 이하에서는 목차를 접을 수 있게 하고, 900px 이하에서는 원문을 탭/드로어로 전환한다. 키보드 포커스와 `prefers-reduced-motion`을 유지한다.

### 작업 8

학생 사용성 검증과 사용자 승인을 받은 뒤에만 진행한다. 기존 Full Knowledge Map 데이터나 API를 삭제하지 않고, 기본 CTA 문구와 진입점만 `연결 탐색`으로 변경한다.
