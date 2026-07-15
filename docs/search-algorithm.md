# LinkNote Search Algorithm: Current Version

작성 기준: 2026-07-15, `main` 브랜치의 `hybrid_personalized_v1` 구현.

이 문서는 LinkNote가 질문을 처리할 때 사용하는 검색 흐름을 정리한 현재 버전 스냅샷이다. 검색은 사용 목적에 따라 빠른 검색, AI 답변, 연결 검색의 세 경로로 나뉜다.

## 1. 빠른 검색

빠른 검색은 GPT 답변을 생성하지 않고 현재 사용자가 저장한 학습 데이터에서 관련 항목을 찾는다. `POST /ask/search`가 이 흐름을 담당한다.

검색 대상:

- 업로드 자료의 chunk와 메타데이터
- `data/concepts.json`의 추출 개념
- `data/recall_traces.json`의 Learning Memory와 recall 기록

현재 처리 순서:

1. 인증 토큰에서 현재 사용자의 `data_user_id`를 결정한다.
2. 한국어 조사를 정규화하고 추출 개념 및 사용자별 alias로 검색어를 확장한다.
3. 질문 의도를 definition, comparison, mechanism, review, connection, source location, personal memory, general로 분류한다.
4. 사용자가 지정한 필터를 우선하고, 그렇지 않으면 질문 의도와 화면 맥락으로 single/multi 범위를 정한다.
5. ChromaDB 의미 후보와 키워드·개념 후보를 결합한다.
6. Learning Memory를 내 설명, AI 피드백, 개선 요약, Missing Links, Follow-up Question, strengths 필드로 나누어 검색한다.
7. 검색 결과에 최종 점수, 점수 구성 요소, 일치 필드, 검색 이유를 붙인다.
8. 사용자, 질문, 필터, 알고리즘 버전, 검색 프로필 revision을 기준으로 결과를 캐시한다. 민감한 검색은 캐시하지 않는다.

빠른 검색은 GPT 답변이나 LLM 재정렬을 호출하지 않는다. embedding provider가 동작하면 의미 검색을 사용하고, 사용할 수 없으면 전체 요청을 실패시키지 않고 키워드·개념·Learning Memory 결과로 fallback한다.

### Source ranking

| 구성 요소 | 비중 | 의미 |
| --- | ---: | --- |
| semantic | 40% | ChromaDB vector distance 기반 의미 유사도 |
| keyword | 25% | 질문 토큰과 자료명·과목·단원·본문 일치 |
| concept | 15% | 추출 개념 또는 alias와의 일치 |
| learning | 14% | Learning State와 Review Priority 기반 현재 학습 관련도 |
| preference | 6% | 사용자가 자주 연 학습 과목·개념 |

개인화 요소인 learning과 preference는 합계 20%를 넘지 않는다. 의미·키워드·개념 관련성이 없는 결과를 개인화만으로 노출하지 않는다.

## 2. AI 답변 검색

AI 답변은 `POST /ask`와 `rag.py`의 rich retrieval 경로를 사용한다. 저장된 자료에서 근거를 먼저 찾은 뒤 해당 context로 답변을 생성한다.

현재 검색 파이프라인:

1. 원문 질문을 embedding하여 ChromaDB에서 벡터 후보를 검색한다.
2. HyDE로 질문에 대한 짧은 가상 답변을 만들고, 이를 다시 embedding하여 추가 후보를 찾는다.
3. 질문의 핵심 단어가 실제 문서에 포함된 후보를 키워드 채널로 찾는다.
4. 각 채널의 순위를 Reciprocal Rank Fusion(RRF)으로 통합한다.
5. 상위 후보를 LLM으로 재평가하여 질문과의 관련도 순으로 정렬한다.
6. 한 파일의 chunk가 결과를 과도하게 차지하지 않도록 파일별 선택 수를 제한한다.
7. 최종 chunk를 근거 context로 사용해 AI 답변을 생성한다.

AI 답변은 의미 검색과 키워드 검색을 함께 사용해 표현 차이를 보완한다. 다만 HyDE, LLM 재정렬, 최종 답변 생성에는 설정된 AI provider와 API 환경이 필요하다.

## 3. 연결 검색

연결 검색은 기본 AI 답변 검색에 concept graph 탐색을 더한다. 질문에서 연결 개념을 찾고, 해당 개념과 연결된 다른 과목 또는 단원의 자료까지 검색 범위를 확장한다.

처리 방향:

- 현재 질문과 직접 관련된 자료를 rich retrieval로 검색한다.
- 검색 결과와 질문에서 핵심 연결 개념을 추출한다.
- concept graph의 연결 정보를 이용해 인접 개념과 관련 chunk를 찾는다.
- 직접 근거와 연결 근거를 함께 사용해 답변을 구성한다.

이 모드는 한 자료 안의 답을 찾는 것보다 과목 간 개념 관계를 탐색할 때 적합하다.

## 4. 검색 행동과 개인 사전

`POST /ask/search/events`는 `result_opened`, `search_refined`, `helpful`, `ai_answer_requested`, `explanation_started`만 기록한다. 결과 열기·도움됨·설명 시작 신호로 과목 및 개념 count를 갱신하며, count는 preference 6% 안에서만 사용한다. 민감한 질문 원문은 event에 저장하지 않는다.

사용자별 concept alias는 `data/search_profiles.json`에 저장한다. 추출 개념의 `aliases`/`synonyms`와 개인 alias를 함께 사용하며 다른 사용자의 검색 프로필과 섞지 않는다. 조회와 추가 API는 `GET /search/profile`, `POST /search/profile/aliases`다.

## 5. 평가와 회귀 기준

`tests/fixtures/search_cases.json`은 definition, personal memory, comparison, source location, connection, review 질문을 포함한다. 테스트는 다음을 고정한다.

- 질문 의도와 자동 검색 범위
- 한국어 조사 제거 후 핵심 concept token 유지
- alias 확장 시 원래 검색어 보존
- 의미 후보와 키워드 후보의 hybrid 순위
- 개인화 점수 20% 상한
- 사용자별 event/profile/alias 격리
- 응답의 `algorithm_version`, `intent`, `scope_reason`, `score_components`

## 6. 현재 원칙

현재 버전의 기본 원칙은 다음과 같다.

> 저장된 강의 자료, 추출 개념, Learning Memory를 먼저 검색하고, AI가 필요한 모드에서만 검색된 근거를 바탕으로 답변을 생성한다.

빠른 검색은 로컬 탐색, AI 답변은 근거 기반 RAG, 연결 검색은 concept graph 확장이라는 역할을 각각 유지한다.

## 7. 관련 구현 파일

- `api_server.py`: `/ask/search`, 검색 필터, 로컬 자료/개념/Learning Memory 검색, 캐시
- `search_engine.py`: intent, token/alias expansion, hybrid score, personalization 상한, score reason
- `rag.py`: ChromaDB 검색 진입점, 기본 AI 답변, 연결 검색
- `retrieval.py`: HyDE, 벡터/키워드 검색, RRF, LLM 재정렬, 결과 다양화
- `providers/`: embedding과 답변 생성 provider
- `data/search_cache.json`: 빠른 검색의 로컬 캐시이며 source-controlled 데이터가 아니다
- `data/search_events.json`: 최대 5,000개의 로컬 행동 이벤트이며 source-controlled 데이터가 아니다
- `data/search_profiles.json`: 사용자별 alias와 aggregate preference이며 source-controlled 데이터가 아니다
- `tests/fixtures/search_cases.json`: source-controlled 검색 평가 질문

이 문서는 검색 동작이 변경될 때 구현과 함께 갱신한다.
