# LinkNote 개발 문서

작성 기준: 2026-07-02, `main` 최신 구현 기준.

이 문서는 LinkNote 개발자가 저장소를 다시 열었을 때 현재 구조와 판단 기준을 빠르게 복원할 수 있도록 만든 단일 개발 스냅샷이다. `README.md`, `docs/api.md`, `docs/architecture.md`, `docs/recall-learning-memory.md`, `docs/deployment.md`, `desktop/README.md`, 그리고 현재 `api_server.py` 구현을 합쳐 정리한다.

## 1. 제품 정의

LinkNote는 PDF 기반 학습 자료를 로컬 우선으로 수집하고, 검색하고, 설명해보고, 복습하는 학습 시스템이다.

현재 핵심 흐름은 다음과 같다.

```text
PDF 업로드
-> 텍스트 추출
-> chunk 생성
-> ChromaDB 인덱싱
-> 질문/검색
-> 개념 추출
-> 개념 연결 그래프
-> 설명해보기
-> AI 피드백
-> Learning Memory
-> Learning Dashboard
-> Start Review
-> Review Map
-> Knowledge Exploration
```

중요한 제품 원칙:

- LinkNote는 성적을 매기는 시스템이 아니라 학습 상태를 정리하는 시스템이다.
- `weak` 계열 표현은 사용자 노출 개념이 아니다. 기존 필드명은 호환용 내부 메타데이터로만 남긴다.
- `NEW` 개념은 약한 개념이 아니라 아직 설명해보지 않은 미평가 개념이다.
- 그래프 보기와 Learning Memory 페이지 로드는 GPT/OpenAI를 호출하지 않는다.
- GPT/OpenAI 호출은 사용자가 명시적으로 AI 답변, AI 피드백, AI Summary, 개념 추출/그래프 재생성을 실행할 때만 일어난다.
- 현재는 단일 사용자/로컬 평가 흐름 안정화가 우선이다. 완전한 프로덕션 멀티유저 격리는 아직 목표 상태가 아니다.

## 2. 기술 스택

### Backend

- Python 3.11 권장. 현재 로컬 venv는 Python 3.11.15, Render 문서는 Python 3.11.9 기준이다.
- FastAPI: `api_server.py`
- Local auth helper: `auth.py`
- RAG/indexing core: `rag.py`
- PDF extraction: `pdf_loader.py`
- Vector store: ChromaDB persistent collection `study_notes`
- Local JSON state: `data/*.json`
- Model provider abstraction: `providers/`

### Frontend

- 정적 HTML/JS/CSS 파일을 FastAPI가 직접 서빙한다.
- 메인 웹 UI는 `web/gallery.html`이다.
- 별도 빌드 단계가 없는 static web surface다.

### Desktop

- Tauri v2 shell: `desktop/`
- 데스크톱 창은 별도 UI를 새로 구현하지 않고 `http://127.0.0.1:8000`을 연다.
- 따라서 데스크톱 앱은 FastAPI가 서빙하는 `web/gallery.html` 경험을 그대로 보여준다.

### Deployment

- Render 배포용 `render.yaml`이 있다.
- Render에서는 `DATA_DIR=/var/data`, `CHROMA_PATH=/var/data/chroma_db`, `EMBED_PROVIDER=openai` 형태를 쓴다.

## 3. 저장소 구조

```text
study-rag-api/
├── api_server.py                 # FastAPI 서버, API 라우트, learning/review 로직
├── auth.py                       # 로컬 계정, 토큰, data_user_id 처리
├── rag.py                        # ChromaDB, chunking, retrieval, concept extraction/linking
├── retrieval.py                  # rich retrieval 보조
├── pdf_loader.py                 # PDF 텍스트 추출
├── reset_db.py                   # 로컬 개발 데이터 초기화 유틸
├── check_all.sh                  # .env/서버/API/OpenAI 스모크 체크
├── run_api.sh                    # 로컬 FastAPI 실행
├── LinkNote 시작.command         # macOS 더블클릭 실행 스크립트
├── 응용프로그램에 설치.command   # 빌드된 Tauri 앱을 /Applications로 복사
├── requirements.txt
├── render.yaml
│
├── web/
│   ├── gallery.html              # 메인 My Library
│   ├── mypage.html               # My Page / Learning Dashboard
│   ├── learning-memory.html      # Learning Memory hub
│   ├── concept-graph.html        # Full Knowledge Map advanced view
│   ├── clinical-reflection.html  # Nursing Clinical Reflection
│   └── app.js                    # legacy experimental frontend
│
├── desktop/
│   ├── src-tauri/                # Tauri 설정/러스트 shell
│   ├── src/                      # fallback desktop web assets
│   └── package.json
│
├── providers/
│   ├── hybrid_provider.py        # embedding provider 선택 + answer provider 연결
│   ├── openai_provider.py        # OpenAI chat/embedding provider
│   └── ollama_provider.py        # local Ollama embedding provider
│
├── prompts/
│   └── recall_feedback.md        # 설명해보기 AI 피드백 프롬프트
│
├── docs/
│   ├── api.md
│   ├── architecture.md
│   ├── deployment.md
│   ├── recall-learning-memory.md
│   ├── nursing-clinical-reflection.md
│   └── development-guide.md      # 이 문서
│
├── tests/
│   └── test_learning_session_sm2.py
│
├── data/                         # gitignore 대상, 로컬 사용자 데이터
└── chroma_db/                    # gitignore 대상, 로컬 vector index
```

## 4. 실행 방법

### Python API

```bash
cd /Users/jeong-yujin/Desktop/LINKNOTE/study-rag-api
./venv/bin/python -m uvicorn api_server:app --reload --host 127.0.0.1 --port 8000
```

또는:

```bash
sh run_api.sh
```

접속:

```text
http://127.0.0.1:8000
```

FastAPI docs:

```text
http://127.0.0.1:8000/docs
```

### Desktop app

백엔드를 먼저 띄운 뒤:

```bash
cd desktop
npm run tauri dev
```

빌드:

```bash
cd desktop
npm run tauri build
```

빌드된 앱 설치:

```bash
sh "응용프로그램에 설치.command"
```

더블클릭 실행 스크립트:

```bash
sh "LinkNote 시작.command"
```

이 스크립트는 `127.0.0.1:8000` 백엔드가 없으면 `uvicorn api_server:app --port 8000`을 백그라운드로 띄우고, 설치된 `LinkNote.app` 또는 빌드 산출물을 연다.

## 5. 환경 변수

| 변수 | 용도 | 기본값 / 현재 의미 |
| --- | --- | --- |
| `DATA_DIR` | JSON 상태와 업로드 파일 저장 루트 | `./data` |
| `CHROMA_PATH` | ChromaDB persistent directory | `./chroma_db` |
| `OPENAI_API_KEY` | OpenAI 답변, 피드백, AI Summary, OpenAI embedding에 필요 | 없으면 서버는 뜨지만 해당 액션은 실패 |
| `EMBED_PROVIDER` | embedding provider 선택 | 기본 `ollama`, Render는 `openai` |
| `AUTH_SECRET` | auth token HMAC signing secret | 없으면 `data/.auth_secret` 생성 |
| `GOOGLE_CLIENT_ID` | Google 로그인 클라이언트 ID | 없으면 Google 로그인 비활성/미구성 |
| `MAINTAINER_EMAILS` | legacy namespace 연결 허용 관리자 이메일 목록 | 기본 `kory124@snu.ac.kr` |
| `PROMPTS_DIR` | 프롬프트 파일 루트 | `./prompts` |

주의:

- `.env`, `data/`, `chroma_db/`, `venv/`, desktop build artifacts는 커밋 대상이 아니다.
- 로컬 Ollama embedding과 OpenAI embedding을 같은 ChromaDB collection에 섞지 않는다.
- Render의 `/var/data`와 로컬 `./data`는 완전히 별도 상태다.

## 6. 데이터 저장소

LinkNote는 아직 PostgreSQL 같은 서버 DB가 아니라 로컬 JSON + ChromaDB 조합을 쓴다.

### JSON files

| 파일 | 생성/사용 주체 | 내용 |
| --- | --- | --- |
| `data/users.json` | `auth.py` | 사용자 계정, password hash, Google sub, `data_user_id`, `student_track` |
| `data/.auth_secret` | `auth.py` | 로컬 token signing secret |
| `data/timetable.json` | timetable endpoints | 시간표 항목 |
| `data/concepts.json` | `/reindex-concepts`, `/concepts` | 사용자/학기/과목/단원별 추출 개념 |
| `data/concept_index.json` | `/reindex-graph` | concept embedding node index |
| `data/concept_links.json` | `/reindex-graph` | cross-course concept edges |
| `data/recall_traces.json` | recall/Learning Memory | 설명해보기 trace와 저장된 feedback |
| `data/learning_sessions.json` | learning session endpoints | 세션 큐, cursor, item status |
| `data/review_schedule.json` | review endpoints | SM-2 schedule state |
| `data/learning_memory_summaries.json` | AI Summary endpoints | 명시적으로 생성한 AI Summary |
| `data/clinical_reflections.json` | Clinical Reflection | 간호 실습 회고 기록 |
| `data/search_cache.json` | `/ask/search` | 사용자/질문/필터별 search-only cache |

### Uploaded files

업로드 PDF는 `data/uploads/` 아래에 저장된다. 실제 저장 파일명은 UUID prefix가 붙을 수 있다. `/file` preview는 요청 filename을 바로 신뢰하지 않고, 먼저 token에서 `data_user_id`를 복원하고 library/chunk metadata로 소유 여부를 확인한 뒤 물리 파일을 찾는다.

### ChromaDB

`rag.py`는 ChromaDB collection `study_notes`를 사용한다.

각 chunk metadata 주요 필드:

```json
{
  "user_id": "data_user_id",
  "semester": "2026-1",
  "course": "병태생리학 1",
  "title": "신부전",
  "filename": "renal_failure.pdf",
  "page": 1,
  "chunk_index": 0,
  "unit": "신장"
}
```

Chunk ID 형식:

```text
{user_id}-{semester}-{course}-{title}-{filename}-p{page}-c{chunk_index}
```

Chunking 기본값:

- `chunk_size=700`
- `overlap=120`

## 7. 인증과 소유권 모델

현재 인증은 `auth.py`의 표준 라이브러리 기반 lightweight auth다.

- 비밀번호 저장: PBKDF2-HMAC-SHA256 + salt
- 토큰: HMAC-SHA256 서명 payload, JWT 유사 구조
- 토큰 TTL: 30일
- 사용자 저장: `data/users.json`
- 공개 사용자 객체에는 `account_id`, `email`, `display_name`, `data_user_id`, `student_track` 등이 포함된다.

보호 API는 다음 흐름으로 소유권을 계산한다.

```text
Authorization: Bearer <token>
-> current_user()
-> current_uid()
-> user.data_user_id
```

개발 규칙:

- 보호 API는 frontend가 보낸 `user_id`를 권한 근거로 쓰면 안 된다.
- Pydantic model에 남아 있는 `user_id`는 compatibility field다.
- upload, ask, recall, library, timetable, concept, chunk 요청은 `Authorization` header를 기준으로 동작해야 한다.
- `link_user_id`는 관리자 migration 용도다. 일반 사용자가 legacy namespace를 claim할 수 없다.

### Student track

`student_track` 허용값:

- `general`
- `nursing`

기본값은 `general`이다. Nursing Clinical Reflection은 `student_track = nursing` 사용자에게 더 의미 있는 additive feature지만, 기존 PDF/RAG/graph/storage 구조를 바꾸지 않는다.

## 8. RAG와 검색

### Ingestion

`POST /ingest` 흐름:

1. PDF upload
2. `pdf_loader.extract_pdf_text`
3. page text -> chunks
4. `providers.hybrid_provider.embed_text`
5. ChromaDB upsert
6. 필요 시 단원 개념 추출

기본 embedding provider:

- local: Ollama
- Render/openai mode: `EMBED_PROVIDER=openai`

### Question modes

My Library에는 두 모드가 있다.

| UI 모드 | API | GPT 호출 | 용도 |
| --- | --- | --- | --- |
| 빠른 검색 | `POST /ask/search` | 없음 | 관련 chunks, concepts, Learning Memory/Recall 찾기 |
| AI 답변 | `POST /ask` | 있음 | 자료 기반 답변 생성 |

`/ask/search`는 search-only endpoint다. 저장된 자료, concept index, Learning Memory를 local keyword/metadata 방식으로 찾고 GPT 답변은 생성하지 않는다. 민감/임상처럼 보이는 query는 cache하지 않는다.

`/ask`는 `mode`에 따라 다음 함수를 사용한다.

- normal/single: `answer_question`
- connection-focused: `answer_with_connections`

답변 프롬프트 규칙:

- 업로드된 강의자료만 근거로 답한다.
- 자료에 없으면 지어내지 않는다.
- 출처는 과목, 단원, 파일명, page 형식으로 표시한다.

## 9. 개념 추출과 그래프

### Concept extraction

`POST /reindex-concepts`는 특정 semester/course/unit의 chunk에서 개념을 추출해 `data/concepts.json`에 저장한다.

`rag.build_concepts_for_unit` 내부 흐름:

1. 해당 user/semester/course/unit chunks 로드
2. 긴 단원은 segment로 나누어 map extraction
3. JSON array salvage로 모델 응답 복원
4. keyword 기준 중복 merge
5. 3~6개 상위 group 할당
6. 중요도 weight 기준 정렬

개념 item 주요 필드:

- `name`
- `keyword`
- `weight`
- `page`
- `filename`
- `links`
- `group`

### Graph reindex

`POST /reindex-graph`는 다음을 생성한다.

- `data/concept_index.json`
- `data/concept_links.json`

`build_concept_embeddings`는 concept node별 embedding을 만든다.

`build_cross_links`는 서로 다른 course의 concept embedding 유사도를 비교한다.

Cross-link 기본 원칙:

- 같은 course concept끼리는 cross link 후보에서 제외한다.
- 너무 높은 유사도 또는 normalized name 동일 케이스는 중복/동일 개념 가능성이 있어 제외한다.
- 중간 유사도 구간은 LLM verification으로 `YES | 이유` 형식을 받아 검증한다.

## 10. Learning State와 Review Priority

개념 그래프는 이제 "약한 점수" 중심이 아니라 학습자 중심 상태 모델을 쓴다.

### Learning State

| 상태 | 의미 |
| --- | --- |
| `NEW` | 아직 설명해본 trace가 없다. 약한 개념이 아니라 미평가 상태다. |
| `LEARNING` | 설명 기록이 있고 학습이 진행 중이다. |
| `REVIEW` | 이미 학습했지만 missing links, 오래된 recall, 반복 피드백 때문에 다시 볼 시점이다. |
| `MASTERED` | 최근 설명했고 missing links가 거의 없다. |

현재 판정 로직:

```text
recall_count <= 0
-> NEW

missing_links_count >= 3 또는 마지막 설명이 21일 이상 전
-> REVIEW

마지막 설명이 14일 이내이고 missing_links_count == 0
-> MASTERED

그 외
-> LEARNING
```

### Review Priority

`review_priority`는 0~100 추천 우선순위다. 성적, 정오답 점수, 능력 평가가 아니다.

현재 기본 범위:

- `NEW`: 약 70
- `LEARNING`: 45부터 시작, missing/age로 증가
- `REVIEW`: 72부터 시작, missing/age로 증가
- `MASTERED`: 약 20, 최근 설명이면 더 낮아짐

중심 개념과 bridge concept은 작은 보정값을 받는다.

```text
centrality_score 보정: 최대 +6
bridge_score 보정: 최대 +4
```

### Review Reason

`review_reason`은 UI가 "왜 이 개념이 보이는지" 설명할 수 있도록 만든 문자열 배열이다.

예:

- `아직 설명해본 기록이 없습니다.`
- `처음으로 설명해보기를 권장합니다.`
- `이미 학습한 개념이며 다시 복습할 시점입니다.`
- `Missing Links 3개`
- `다른 개념과 많이 연결되는 핵심 개념입니다.`

### Compatibility fields

`weak_score`, `weak_concept_count`, `weak_concepts`, `weak_only` 같은 이름은 일부 wire compatibility를 위해 남아 있다.

개발 규칙:

- 새 사용자 문구에는 `Weak Score`, `Weak Concepts`, `약한 개념`을 쓰지 않는다.
- 새 UI는 `Learning State`, `Review Priority`, `Review Concepts`, `Review needed`를 쓴다.
- backend compatibility field는 제거하지 말고 의미를 문서화해서 유지한다.

## 11. Concept Graph와 Concept Connections

### API

`GET /concept-graph`:

- 기존 gallery/graph용 nodes/edges를 반환한다.
- node에 recall metadata, learning_state, review_priority, review_reason을 붙인다.

`GET /concept-graph/overview`:

- Full Knowledge Map과 Learning Memory compact map의 기본 데이터 소스다.
- GPT/OpenAI 호출 없음.
- ChromaDB reindex 없음.
- concept graph rebuild 없음.
- 기존 `concept_index.json`, `concept_links.json`, recall traces, review schedule만 읽는다.

Overview node 주요 필드:

- `id`
- `label` / `name`
- `semester`
- `course`
- `unit`
- `weight`
- `recall_count`
- `last_recalled_at`
- `missing_links_count`
- `learning_state`
- `review_priority`
- `review_reason`
- `weak_score` compatibility
- `degree`
- `weighted_degree`
- `connected_count`
- `centrality_score`
- `bridge_score`
- `memory_score`
- `review_score`
- `priority_score`
- `node_types`
- `why_shown`
- `recommended_action`
- `is_due`
- `due_at`

Overview stats:

- `node_count`
- `edge_count`
- `review_concept_count`
- `learning_concept_count`
- `mastered_concept_count`
- `recalled_concept_count`
- `bridge_concept_count`
- `core_concept_count`
- `new_concept_count`
- `weak_concept_count` compatibility

Ranking info:

```json
{
  "algorithm_version": "concept_graph_learning_state_v3_sm2",
  "score_components": [
    "learning_state",
    "review_priority",
    "centrality_score",
    "bridge_score",
    "memory_score",
    "due_at"
  ]
}
```

### Full Knowledge Map UI

파일: `web/concept-graph.html`

역할:

- Learning Memory에서 더 깊게 들어가는 advanced graph view
- 첫 화면은 전체 그래프를 전부 펼치지 않고 오늘의 학습 큐와 제한된 지도부터 보여준다.
- `Explore Full Knowledge Map`은 고급 액션이다.

모드:

- `Review Map`
- `Core Map`
- `Connection Map`
- `Learning Memory Map`
- `New Concepts`
- `Full Knowledge Map`

Full mode는 복잡도 때문에 visible node를 제한한다. 과목/단원 필터, concept focus, selected node 중심 보기로 읽기성을 확보한다.

### Learning Dashboard and Review Map

파일: `web/learning-memory.html`

역할:

- default experience는 Learning Dashboard다.
- 대시보드는 "지금 무엇을 해야 하는가"에 3초 안에 답하는 화면이다. summary card는 `Today's Focus`와 `Learning Progress` 두 개만 둔다.
- `Today's Focus`는 학습 상태에 따라 단 하나의 primary action만 보여준다. 우선순위:
  1. due 복습 개념이 있으면(`GET /review/due`) → `복습할 개념 N개` + `Start Review`
  2. 미완료 learning session이 있으면(`GET /learning-session/current`) → `Continue learning` + 다음 개념 + `Continue`
  3. 그 외 → `Explore new concepts` + `Browse Knowledge Map`
- 여러 경쟁 action을 동시에 보여주지 않는다. `Continue Learning`, `Central Concept`, `Recently Explained`, `Unexplained Concepts` 같은 개별 card는 두지 않는다.
- `Learning Progress`는 learned/total/percent만 보여준다. graph 통계나 구현 세부는 노출하지 않는다.
- node count, edge count, central concept, missing links, learning state, review priority 설명, unexplained concepts 같은 내부 graph/debug 정보는 dashboard에 노출하지 않는다.
- Review Map은 dashboard 아래의 보조 의사결정 도구다. 설명 문구는 한 문장으로 유지한다("학습 기록을 바탕으로 복습 우선순위가 높은 개념부터 보여줍니다").
- Review Map에는 Course filter, Unit filter, Review Needed Only를 둔다.
- `Explore Full Knowledge Map` 링크는 Review Map 아래에 secondary navigation으로 둔다. dashboard의 primary action이 아니다.
- Full graph, node connections, edge relationships, graph filters는 `web/concept-graph.html`의 Knowledge Exploration으로 보낸다.
- node click 시 action panel을 열어 Why now, Learning Memory, 빠른 검색, My Library context 이동을 제공한다.
- 원칙: dashboard는 학습용, Knowledge Map은 탐색용이다.

## 12. Recall Trace와 AI Feedback

### Recall Trace

`POST /recall-traces`는 학습자가 개념을 자기 말로 설명한 기록을 저장한다.

기본 필드:

- `id`
- `user_id`
- `semester`
- `course`
- `unit`
- `concept`
- `answer_text`
- `created_at`

`GET /recall-traces`는 현재 user의 traces를 semester/course/unit/concept/limit으로 필터링한다.

### AI Feedback

`POST /recall-feedback`는 설명에 대한 SCiyl-style directional feedback을 생성한다.

필드:

- `good_points`
- `missing_links`
- `followup_question`
- `source_hint`

`trace_id`가 있으면 해당 trace에 feedback을 직접 저장한다. 없으면 concept/scope/answer_text 기준으로 최신 matching trace를 찾아 붙이고, 필요 시 별도 feedback record를 append한다.

중요:

- recall trace 저장/list는 OpenAI 없이 동작한다.
- `/recall-feedback`는 `OPENAI_API_KEY`가 필요하다.
- 피드백은 정오답 채점이 아니라 연결 보완 안내다.

## 13. Learning Memory

파일: `web/learning-memory.html`

Learning Memory는 저장된 설명과 AI 피드백을 다시 쓰는 학습 허브다.

### API

| Method | Path | GPT 호출 | 설명 |
| --- | --- | --- | --- |
| `GET` | `/learning-memory` | 없음 | normalized memory list |
| `DELETE` | `/learning-memory/{memory_id}` | 없음 | memory와 linked feedback 삭제 |
| `GET` | `/learning-memory/summary` | 없음 | rule-based summary |
| `POST` | `/learning-memory/ai-summary` | 있음 | 명시 클릭 시 AI Summary 생성 |
| `GET` | `/learning-memory/ai-summaries` | 없음 | 저장된 AI Summary list |

### Memory card

각 card는 다음을 보여준다.

- 내 설명
- AI 피드백
- 좋았던 점
- 더 연결해볼 점
- 다시 생각해볼 질문
- 개선 요약
- 복습 힌트
- 다시 볼 자료/source hint

AI 피드백이 없으면 명시적인 `AI 피드백 생성` 버튼을 보여준다.

### Rule-based summary

`GET /learning-memory/summary`는 GPT를 호출하지 않고 다음을 계산한다.

- total memories
- concepts explained
- review concepts from repeated missing links
- frequent missing links
- weekly summary from recent memories
- exam review focus from missing links and REVIEW-state concepts

### Optional AI Summary

GPT 호출 버튼:

- 이번 주 요약 생성
- 과목 요약 생성
- 시험 대비 요약 생성
- 복습 개념 요약 생성
- AI 피드백 생성

AI Summary 생성 전에는 브라우저 confirm으로 API 비용 안내를 한다.

`summary_type` 허용/사용 값:

- `weekly`
- `course`
- `exam`
- `weak_concepts` legacy wire value for review concepts

## 14. Learning Session과 SM-2 Review

최신 `main`에는 개념 그래프를 학습 전략 도구로 확장하는 Learning Session과 Review Schedule이 들어와 있다.

### 데이터 파일

| 파일 | 내용 |
| --- | --- |
| `data/learning_sessions.json` | 세션 목록, item queue, cursor, completed_at |
| `data/review_schedule.json` | concept별 SM-2 schedule |

### Learning Session API

`POST /learning-session/start`

Request:

```json
{
  "scope": {
    "course": null,
    "unit": null
  },
  "size": 7
}
```

동작:

- 현재 user의 concept overview nodes를 만든다.
- course/unit scope가 있으면 필터링한다.
- `review_priority` desc, `weak_score` desc, label asc 기준으로 정렬한다.
- size는 1~50으로 clamp한다.
- session item은 `pending` 상태로 시작한다.

Response 주요 필드:

```json
{
  "id": "sess_xxx",
  "user_id": "data_user_id",
  "created_at": "2026-07-02T12:00:00+00:00",
  "scope": {"course": null, "unit": null},
  "items": [
    {
      "concept_id": "c1",
      "concept": "신부전",
      "course": "병태생리학 1",
      "unit": "신장",
      "state_at_start": "REVIEW",
      "status": "pending"
    }
  ],
  "cursor": 0,
  "completed_at": null
}
```

`POST /learning-session/{session_id}/advance`

Request:

```json
{
  "concept_id": "c1",
  "result": "explained"
}
```

동작:

- session ownership 확인
- item 찾기
- `result === "explained"`이면 item status를 `explained`로 바꾸고 synthetic recall trace를 저장한다.
- 그 외 result는 `skipped`로 처리한다.
- cursor를 하나 전진한다.
- 마지막 item 이후 `completed_at`을 설정한다.
- 다음 item이 있으면 `next_item`을 응답에 포함한다.

Synthetic recall trace의 `answer_text`는 현재 다음 형식이다.

```text
[sess_xxx] learning-session explained
```

이 값은 실제 learner text가 아니라 세션 완료 표시용 trace다. 나중에 세션 UI가 실제 답변을 받게 되면 이 부분은 교체 대상이다.

`GET /learning-session/current`

- 현재 user의 미완료 세션 중 가장 최근 것을 반환한다.
- 없으면 `{"session": null}`.

`GET /learning-session/{session_id}`

- 단일 세션 조회.
- user_id가 다르면 403.

### Review Schedule API

`POST /review/grade`

Request:

```json
{
  "concept_id": "c1",
  "quality": 5
}
```

`quality`는 0~5 정수다. 범위를 벗어나면 400.

응답:

```json
{
  "concept_id": "c1",
  "user_id": "data_user_id",
  "ease": 2.6,
  "interval_days": 6,
  "repetitions": 2,
  "last_reviewed_at": "2026-07-02T12:00:00+00:00",
  "due_at": "2026-07-08T12:00:00+00:00"
}
```

`GET /review/due`

Query:

- `limit`: default 20, 1~100으로 clamp
- `course`: optional
- `unit`: optional

동작:

- concept overview nodes를 만든다.
- `is_due === true`인 node가 있으면 `due_at` asc, `review_priority` desc로 정렬한다.
- due node가 없으면 `review_priority` desc, label asc fallback.

응답 item:

```json
{
  "concept_id": "c1",
  "concept": "신부전",
  "course": "병태생리학 1",
  "unit": "신장",
  "learning_state": "REVIEW",
  "review_reason": ["이미 학습한 개념이며 다시 복습할 시점입니다."],
  "review_priority": 86,
  "is_due": true,
  "due_at": "2026-07-02T12:00:00+00:00"
}
```

### SM-2 구현

Schedule key:

```text
{user_id}::{concept_id}
```

기본 entry:

```json
{
  "concept_id": "c1",
  "user_id": "data_user_id",
  "ease": 2.5,
  "interval_days": 0,
  "repetitions": 0,
  "last_reviewed_at": null,
  "due_at": "now"
}
```

현재 알고리즘:

```text
quality < 3:
  repetitions = 0
  interval_days = 1

quality >= 3:
  repetitions += 1
  repetitions == 1 -> interval_days = 1
  repetitions == 2 -> interval_days = 6
  repetitions >= 3 -> interval_days = round(prev_interval * ease)

ease = max(
  1.3,
  ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
)

due_at = now + interval_days
```

테스트 파일:

```text
tests/test_learning_session_sm2.py
```

검증하는 것:

- quality 2, 3, 5에서 interval/repetition 변화
- learning session advance가 recall trace를 남기는지
- review grade가 1일, 6일 schedule로 이어지는지

## 15. Nursing Clinical Reflection

파일:

- `web/clinical-reflection.html`
- `docs/nursing-clinical-reflection.md`
- 관련 backend: `/clinical-reflection`, `/clinical-reflections`

목표:

- 간호학과 사용자가 실습 상황을 de-identified text로 입력한다.
- 업로드된 학습 자료와 concept graph를 연결해 교육적 회고를 돕는다.
- 진단, 치료, 처방, clinical decision directive를 생성하지 않는다.

Safety:

- 환자 이름, 등록번호, 주민등록번호, 전화번호, 정확한 병실/식별자 등 patient-identifying information을 입력하면 안 된다.
- backend는 identifier-like pattern을 검사하고, 걸리면 retrieval/GPT 전에 중단한다.
- 결과는 학습/회고용이며 임상 판단, 병원 정책, 지도자 지시를 대체하지 않는다.

Feedback fields:

- `knowledge_connections`
- `nursing_process_links`
- `missed_assessment_cues`
- `safe_next_questions`
- `review_focus`
- `source_hints`
- `educational_summary`

## 16. Web UI entry points

### `web/gallery.html`

현재 `/`에서 서빙되는 메인 UI.

주요 기능:

- 로그인/회원가입/Google login overlay
- localStorage `ln_token` 기반 Authorization header
- 학기/과목/단원 gallery navigation
- PDF upload
- timetable sidebar
- unit list sorting/drag order
- concept graph mini view
- 설명해보기 panel
- recent Learning Memory
- AI feedback request
- 빠른 검색/AI 답변 inline question
- PDF preview
- unit rename

### `web/mypage.html`

Learner-oriented summary page.

주요 기능:

- Learning Dashboard
- recent Learning Memory
- Learning Snapshot
- collapsed Developer Information
- nursing user일 때 Clinical Reflection entry point

### `web/learning-memory.html`

Learning Dashboard and Learning Memory hub.

주요 기능:

- default Learning Dashboard
- Today's Focus (상태 기반 단일 primary action: Start Review / Continue / Browse Knowledge Map)
- Learning Progress (learned/total/percent)
- Review Map with course/unit/review-needed filters
- Explore Full Knowledge Map secondary link (Review Map 아래)
- Memory cards
- AI feedback generation per memory
- memory deletion
- AI Summary generation/list
- Quick Search from selected concept node

### `web/concept-graph.html`

Full Knowledge Map advanced view.

주요 기능:

- 오늘의 학습 큐
- 제한된 graph render
- node detail/action panel
- concept focus
- course/unit filters
- quick search
- full map exploration

### `web/clinical-reflection.html`

Nursing practice reflection page.

주요 기능:

- safety notice
- de-identified practice situation input
- feedback render
- recent reflections list

### `web/app.js`

Legacy experimental frontend. 현재 authenticated production flow의 기준 파일로 쓰지 않는다.

## 17. API surface snapshot

이 목록은 현재 `api_server.py` 기준이다.

### Search / RAG / Upload

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/ask/search` | GPT 없는 빠른 검색 |
| `POST` | `/ask` | 자료 기반 AI 답변 |
| `POST` | `/ingest` | PDF 업로드, chunk/index |
| `GET` | `/library` | 현재 user library overview |
| `DELETE` | `/library` | 필터에 맞는 indexed chunks 삭제 |
| `GET` | `/chunks` | chunk inspect |
| `GET` | `/file` | token 기반 PDF preview |
| `POST` | `/rename-unit` | ChromaDB metadata와 concept JSON unit rename |

### Timetable

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/timetable` | semester별 course list |
| `GET` | `/timetable/entries` | raw timetable entries |
| `POST` | `/timetable/entries` | entry 추가 |
| `PUT` | `/timetable/entries` | entry 수정 |
| `DELETE` | `/timetable/entries` | entry 삭제/all clear |

### Concepts / Graph

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/units` | semester/course별 units |
| `POST` | `/reindex-concepts` | concept extraction rebuild |
| `GET` | `/concepts` | extracted concepts 조회 |
| `POST` | `/reindex-graph` | concept_index/concept_links rebuild |
| `GET` | `/concept-graph` | graph nodes/edges |
| `GET` | `/concept-graph/overview` | read-only ranked overview |

### Recall / Learning Memory

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/recall-traces` | 설명해보기 저장 |
| `GET` | `/recall-traces` | 설명 기록 list |
| `POST` | `/recall-feedback` | AI feedback 생성/저장 |
| `GET` | `/learning-memory` | normalized memory list |
| `DELETE` | `/learning-memory/{memory_id}` | memory 삭제 |
| `GET` | `/learning-memory/summary` | rule-based summary |
| `POST` | `/learning-memory/ai-summary` | 명시적 AI Summary 생성 |
| `GET` | `/learning-memory/ai-summaries` | 저장된 AI Summary list |

### Learning Session / Review

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/learning-session/start` | review priority 기반 세션 생성 |
| `POST` | `/learning-session/{session_id}/advance` | item 진행, explained면 trace 저장 |
| `GET` | `/learning-session/current` | 최신 미완료 세션 |
| `GET` | `/learning-session/{session_id}` | 단일 세션 |
| `POST` | `/review/grade` | SM-2 schedule 갱신 |
| `GET` | `/review/due` | due concepts 또는 priority fallback |

### Nursing

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/clinical-reflection` | 간호 실습 회고 feedback |
| `GET` | `/clinical-reflections` | 저장된 회고 list |

### Auth / Static

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/auth/register` | local user register |
| `POST` | `/auth/login` | local login |
| `GET` | `/auth/me` | token -> public user |
| `GET` | `/auth/config` | public Google config |
| `POST` | `/auth/google` | Google ID token login/register |
| `GET` | `/health` | server health |
| `GET` | `/` | `web/gallery.html` |
| static | `/*` | files under `web/` |

## 18. GPT/OpenAI 호출 경계

OpenAI가 필요할 수 있는 동작:

- `/ask`
- `/recall-feedback`
- `/learning-memory/ai-summary`
- `/clinical-reflection`
- `/reindex-concepts`
- `/reindex-graph` 중 embedding/provider 설정에 따라 일부
- `EMBED_PROVIDER=openai` 상태의 ingest/indexing/search embedding

OpenAI 없이 동작해야 하는 동작:

- server startup
- static page load
- auth
- `/library`
- `/timetable`
- `/chunks`
- `/file`
- `/ask/search`
- `/recall-traces`
- `/learning-memory`
- `/learning-memory/summary`
- `/learning-memory/ai-summaries`
- `/concept-graph/overview`
- `/learning-session/*`
- `/review/*`

개발 규칙:

- import 시점에 OpenAI client를 만들지 않는다.
- OpenAI key가 없어도 서버가 기동해야 한다.
- OpenAI가 필요한 endpoint는 key 없음 상태에서 명확한 error를 반환한다.

## 19. Desktop update model

데스크톱 앱은 별도 데이터를 갖지 않는다.

```text
LinkNote.app
-> Tauri window
-> http://127.0.0.1:8000
-> FastAPI
-> web/gallery.html
-> same local data/chroma_db
```

따라서 backend/static web 수정은 local server가 최신 코드를 서빙하면 데스크톱 앱에도 반영된다.

데스크톱 앱 자체를 다시 빌드해야 하는 경우:

- Tauri 설정 변경
- 앱 아이콘 변경
- native shell 코드 변경
- `/Applications/LinkNote.app` bundle 자체를 새로 배포해야 하는 경우

단순 `web/*.html` 또는 `api_server.py` 변경은 backend restart로 반영된다. 최근에는 WKWebView cache 때문에 반영이 늦는 문제가 있어 static no-cache header가 적용된 상태다.

## 20. 배포 모델

Render Blueprint:

- `render.yaml`
- start: `uvicorn api_server:app --host 0.0.0.0 --port $PORT`
- persistent disk: `/var/data`
- `DATA_DIR=/var/data`
- `CHROMA_PATH=/var/data/chroma_db`
- `EMBED_PROVIDER=openai`

Render에서 필요한 manual secrets:

- `OPENAI_API_KEY`
- `GOOGLE_CLIENT_ID`

주의:

- Render data는 로컬 data와 분리된다.
- local Ollama embeddings를 Render OpenAI embedding collection으로 그대로 복사하지 않는다.
- Starter plan disk가 없으면 재시작 때 데이터가 사라질 수 있다.

## 21. 테스트와 검증

### Python syntax

```bash
PYTHONPYCACHEPREFIX=/tmp/linknote-pyc ./venv/bin/python -m py_compile api_server.py auth.py rag.py
```

### Learning Session / SM-2 unit test

```bash
PYTHONPYCACHEPREFIX=/tmp/linknote-pyc ./venv/bin/python -m unittest discover -s tests -p test_learning_session_sm2.py
```

### Static JS syntax check

정적 HTML script block을 추출해 `node --check`로 확인할 수 있다. 최근 검증 대상:

- `web/gallery.html`
- `web/learning-memory.html`
- `web/concept-graph.html`

### Boundary smoke checks

서버 실행 후:

```bash
curl -s http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/learning-memory.html
curl -i http://127.0.0.1:8000/concept-graph/overview
```

주의: 대부분의 protected API는 auth token이 없으면 401이 정상이다.

### `check_all.sh`

`check_all.sh`는 다음을 수행한다.

1. `.env` / `OPENAI_API_KEY` 확인
2. 기존 8000 서버 종료 후 재시작
3. 서버 기동 대기
4. `/timetable`, `/library` 확인
5. `/reindex-concepts` OpenAI 개념 추출 테스트

이 스크립트는 서버를 죽이고 다시 띄우며 OpenAI를 사용할 수 있으므로, 단순 문법 검증용으로 무심코 실행하지 않는다.

## 22. 개발 규칙과 금지사항

### 데이터 보존

- 기존 `data/`, `chroma_db/`, `data/uploads/`를 임의로 삭제/이동/정리하지 않는다.
- concept graph 보기만으로 reindex, rebuild, GPT 호출이 일어나면 안 된다.
- ownership hardening 작업은 기존 업로드와 analysis data를 파괴하지 않아야 한다.

### 소유권

- frontend-provided `user_id`를 protected data access 근거로 쓰지 않는다.
- 새 endpoint는 `Depends(current_uid)` 또는 `Depends(current_user)`를 우선 사용한다.
- PDF iframe preview처럼 header를 붙일 수 없는 경우만 query token을 쓰고, 서버에서 token -> user -> ownership 검증을 끝까지 한다.

### 학습 모델 언어

- 사용자 문구에서 `Weak Score`, `Weak Concepts`, `약한 개념`을 피한다.
- `Learning State`, `Review Priority`, `Review Concepts`, `오늘의 복습`, `설명해보기`를 사용한다.
- `review_priority`는 추천이지 grade가 아니다.
- `NEW`은 약함이 아니라 아직 설명/회상 trace가 없는 상태다.

### Clinical Reflection 안전

- 환자 식별 정보 입력을 허용하지 않는다.
- 진단, 예후, 처방, 치료 지시, clinical decision directive를 만들지 않는다.
- 결과는 교육적 회고와 학습 자료 연결로 한정한다.

### OpenAI 비용

- 페이지 로드에서 GPT를 부르지 않는다.
- AI Summary/feedback처럼 비용이 드는 action은 명시 버튼과 안내를 둔다.
- key가 없을 때는 서버 전체를 깨뜨리지 않고 해당 action만 명확히 실패시킨다.

## 23. 다음 개발자가 먼저 볼 파일

1. `api_server.py`
2. `auth.py`
3. `rag.py`
4. `web/gallery.html`
5. `web/learning-memory.html`
6. `web/concept-graph.html`
7. `docs/api.md`
8. `docs/architecture.md`
9. `docs/recall-learning-memory.md`
10. `tests/test_learning_session_sm2.py`

## 24. 현재 남은 큰 과제

- Learning Session UI를 실제 learner answer 입력 기반으로 확장한다.
- Synthetic trace 대신 실제 세션 답변을 recall trace에 저장한다.
- Learning Dashboard의 Start Review 이후 화면을 실제 learner answer 입력 기반 세션 UI로 확장한다.
- `docs/api.md`와 이 문서를 계속 동기화한다.
- local JSON storage가 커질 때 migration/locking 전략을 정한다.
- production multi-user storage boundary를 강화할 시점을 별도 PR로 잡는다.
- Google login desktop webview 제약을 사용자 안내에 더 명확히 반영한다.
- Render 운영 데이터와 로컬 데이터의 export/import 전략을 별도 설계한다.


## 25. Production 전환 전 아키텍처 기준

이 항목들은 즉시 구현할 작업이 아니라, 공개 서비스 또는 대규모 리팩터링 PR을 시작하기 전 반드시 합의해야 하는 기준선이다. 기존 데스크탑 앱, local-first 학습 흐름, 임시 user_id 기반 구조와 충돌하지 않도록 별도 PR 단위로 진행한다.

### API 계층 분리

- 현재 `api_server.py`는 단일 FastAPI 진입점 역할을 한다. production 전환 전에는 도메인별 `router / service / repository` 계층으로 분리한다.
- 우선 분리 대상 도메인은 `auth`, `library`, `ingest`, `ask`, `recall`, `learning_memory`, `clinical_reflection`이다.
- 분리 PR은 endpoint behavior, auth dependency, response shape를 바꾸지 않는 migration-first 방식으로 진행한다.
- 대규모 이동 전에는 백업 또는 브랜치 전략을 사용자에게 먼저 확인한다.

### Auth 경계

- 현재 `auth.py`는 로컬/소수 테스트에 적합한 lightweight auth다.
- 공개 서비스 전환 시 검증된 Auth provider와 durable user DB로 전환한다.
- 전환 전까지 실제 다중 사용자 DB를 임의로 도입하지 않는다. 기존 local `user_id`/token-derived ownership 흐름을 유지한다.

### Provider와 모델 평가

- `providers/*`는 local-first 철학과 배포 전환을 모두 지원해야 한다.
- 모델/provider 변경은 기능 코드와 분리하고, 요청 목적, 모델명, 비용 추정, 성공/실패, latency, 사용자 action 단위의 평가 로그를 남길 설계를 둔다.
- 페이지 로드에서 GPT 호출이 일어나지 않는 원칙은 유지한다.

### 데이터 분리와 스토리지 전환

- `data/` 및 `chroma_db/`에는 사용자 데이터와 fixture/test data를 절대 섞지 않는다.
- JSON 파일 저장은 SQLite 또는 Postgres로 옮길 계획을 세우고, migration, backup, rollback 절차를 포함한다.
- Chroma metadata schema는 version field와 migration note를 두고 관리한다.
- 저장소 구조 변경은 기존 업로드, recall trace, Learning Memory, concept graph 데이터를 파괴하지 않아야 한다.

### 운영 보안과 비용 제한

- CORS는 공개 전 `allow_origins=["*"]`에서 허용 도메인 목록으로 좁힌다.
- 업로드 크기 제한, 파일 타입 검증, rate limit, AI cost limit, audit log를 추가한다.
- 임상/간호 관련 기능은 audit log와 safety boundary를 별도 설계한다.
- 이 항목들은 기능 구현 PR과 섞지 않고 production-hardening PR로 분리한다.
