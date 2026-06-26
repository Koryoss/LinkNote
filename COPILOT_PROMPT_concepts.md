# Copilot 작업 프롬프트 — 개념(concept) 추출 + /concepts 실데이터화

대상: `study-rag-api`. 목표: 단원별 핵심 개념을 추출해 `data/concepts.json` 에 저장하고,
`/concepts` 엔드포인트가 그 데이터를 반환하도록 만든다.
프론트(`web/gallery.html`)가 이미 이 형식을 기다리고 있으니 **응답 스키마를 정확히** 맞춘다.

## 절대 제약
- `rag.py` 의 검색/임베딩/답변 핵심 로직은 변경 금지. **개념 추출 "호출"만 추가**한다.
- ChromaDB 데이터를 삭제/초기화하지 않는다.
- 개념 추출은 OpenAI(`generate_answer`)를 쓰므로 비용이 든다. **ingest 때 자동 실행하지 말고**,
  아래 수동 엔드포인트로만 돌린다.
- 한국어 출력 규칙 유지.

## 현재 상황 (참고)
- 데이터는 이미 들어가 있다. user_id 예: `정유진`, 과목 예: `병태생리학 1`,
  단원(unit) 예: `급성 신부전, 만성 신부전`. (chunk metadata에 user_id/semester/course/unit/page 있음)
- `rag.py` 에 단원 chunk를 가져오는 `get_chunks(user_id, ..., search_filter=..., full=True)` 가 이미 있다.
- `rag.py` 에 개념 키워드를 뽑는 비공개 함수 `_extract_connection_concepts(question, chunks)` 가 있다(참고용).

## 1) rag.py 에 `build_concepts_for_unit(...)` 추가
시그니처:
```
def build_concepts_for_unit(user_id, semester, course, unit) -> list[dict]
```
처리:
1. `_build_where_filter({"semester":semester,"course":course}, user_id=user_id)` 로 해당 chunk를 가져오되,
   unit이 일치하는 것만 추린다(메타데이터 `unit` == 인자 unit). chunk의 `text`, `page` 를 모은다.
2. 모은 본문을 합쳐 LLM(`generate_answer`)에 **아래 프롬프트**로 핵심 개념과 중요도를 요청한다:
   ```
   다음은 한 단원의 강의자료다. 이 단원의 핵심 개념을 8개 이내로 뽑아라.
   - 반드시 한국어. 설명 금지.
   - 각 개념의 중요도를 1~5 정수로 매겨라(5가 가장 중요).
   - 반드시 아래 JSON 배열 형식만 출력: [{"name":"개념","importance":5}, ...]
   자료:
   <합친 본문 일부, 너무 길면 앞 6000자>
   ```
3. LLM 응답을 JSON 파싱한다(실패 시 빈 배열 반환, 서버가 죽지 않게 try/except).
4. 후처리(파이썬에서 결정적으로 계산):
   - `weight` = importance(1~5).
   - `page` = 그 개념명이 처음 등장하는 chunk의 page(본문 substring 검색, 없으면 null).
   - `links` = 같은 chunk에 함께 등장하는 다른 개념들의 name 목록(중복 제거).
5. 반환 형식(이 키 이름 그대로):
   ```
   [ {"name":"신부전","weight":5,"page":3,"links":["체액조절","만성신부전"]}, ... ]
   ```

## 2) `data/concepts.json` 저장 구조
```
{ "정유진": { "2026-1": { "병태생리학 1": {
    "급성 신부전, 만성 신부전": [ {"name":...,"weight":...,"page":...,"links":[...]} ]
} } } }
```
- 저장/로드 헬퍼를 api_server.py(또는 작은 모듈)에 둔다. 파일 없으면 빈 dict.

## 3) 엔드포인트

### POST /reindex-concepts  (수동 트리거, 비용 발생)
요청: `{ "user_id":"정유진", "semester":"2026-1", "course":"병태생리학 1", "unit":"급성 신부전, 만성 신부전" }`
- unit 생략 시: 그 과목의 모든 unit을 순회해 재생성.
- `build_concepts_for_unit` 결과를 `data/concepts.json` 에 저장.
- 응답: `{ "ok":true, "units_indexed": 1, "concepts_count": 7 }`

### GET /concepts  (기존 스텁 교체)
쿼리: `user_id, semester, course, unit`
- `data/concepts.json` 에서 해당 unit 개념을 읽어 반환.
- 있으면: `{ "status":"ready", "concepts":[ {name,weight,page,links} ] }`
- 없으면: `{ "status":"empty", "concepts":[] }`
  (⚠ 더 이상 `not_ready` 하드코딩 금지)

## 4) 확인
- 서버 실행 후:
  `POST /reindex-concepts` 로 `정유진 / 2026-1 / 병태생리학 1 / 급성 신부전, 만성 신부전` 한 단원 인덱싱.
- 그 다음 `GET /concepts?user_id=정유진&semester=2026-1&course=병태생리학 1&unit=급성 신부전, 만성 신부전`
  가 `status:"ready"` 와 개념 배열을 돌려주는지 확인.
- `web/gallery.html` 에서 해당 단원을 열면 "예시" 뱃지 없이 실제 개념 지도가 떠야 한다.
- OPENAI_API_KEY 가 없으면 /reindex-concepts 만 실패하고(명확한 에러 메시지), 나머지 기능은 정상이어야 한다.
