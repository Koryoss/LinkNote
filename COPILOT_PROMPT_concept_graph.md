# Copilot 설계 — 과목을 잇는 개념 그래프 (Cross-Subject Concept Graph)

목표: LinkNote를 "단원별 별 지도"에서 **여러 과목의 개념이 연결된 하나의 지식 그래프**로 발전시킨다.
핵심 아이디어: 개념을 **임베딩**해서, 표현이 달라도 의미가 가까운 개념을 **과목 경계를 넘어** 연결한다.
(예: 병태생리학의 "급성 신손상(AKI)" ↔ 인체구조와기능의 "신장" ↔ "체액조절")

대상: `study-rag-api`. 두 단계로 나눠 진행한다. **Phase 1 먼저, 그다음 Phase 2.**

## 절대 제약
- `rag.py`의 검색/임베딩/답변 핵심 함수 시그니처는 깨지 않는다(호출·필드 추가만).
- 임베딩은 이미 쓰는 **Ollama `embed_text`(로컬·무료)** 를 그대로 사용한다. (개념 임베딩에 OpenAI 쓰지 말 것)
- ChromaDB 데이터 삭제/초기화 금지. 한국어 출력 규칙 유지.
- 비용이 드는 작업(LLM 개념추출)은 기존처럼 `/reindex-concepts` 수동 트리거로만.

---

## Phase 1 — 개념 추출 품질 강화 (저렴, LLM 프롬프트)

`rag.py`의 `build_concepts_for_unit`를 개선한다.

1. LLM 프롬프트를 바꿔 각 개념마다 **대표 키워드(keyword)** 와 **같은 단원 내 관계(relations)** 도 받는다:
   ```
   다음은 한 단원의 강의자료다. 핵심 개념을 8개 이내로 뽑아라.
   - 반드시 한국어. 설명 금지. JSON 배열만 출력.
   - 각 항목 형식:
     {"name":"개념 전체 이름","keyword":"가장 짧은 핵심 용어","importance":1~5,
      "related":["같은 단원에서 직접 관련된 다른 개념 keyword", ...]}
   - keyword 는 본문에서 실제로 찾을 수 있는 짧은 단어(예: "신부전", "AKI")로.
   자료: <merged_text 앞 6000자>
   ```
2. 후처리(파이썬, 결정적):
   - `page` 매칭을 **name 대신 keyword** 로 한다(긴 이름은 본문에 안 나와서 지금 page=None 문제 발생).
   - `links` = `related`에 나온 keyword를 같은 단원의 다른 개념과 매칭해 채운다.
   - 저장 구조에 `keyword` 필드를 추가한다:
     ```
     {"name":..., "keyword":"신부전", "weight":5, "page":3, "links":[...]}
     ```
3. 기존 `/reindex-concepts`로 다시 돌리면 `data/concepts.json`이 keyword·links 포함해 갱신된다.

이걸로 단원 내 지도는 실제 관계로 연결되고 page도 살아난다.

---

## Phase 2 — 개념 임베딩 + 과목 간 연결 (비전의 핵심)

### 2-1. 개념 임베딩 인덱스 만들기
- 새 함수 `build_concept_embeddings(user_id)` (rag.py 또는 별도 모듈):
  - `data/concepts.json`의 모든 (semester, course, unit, 개념)을 순회.
  - 각 개념을 `embed_text(keyword + " " + name)` 로 임베딩(Ollama, 로컬).
  - 결과를 `data/concept_index.json`에 저장. 각 노드:
    ```
    { "id":"정유진::2026-1::병태생리학 1::급성 신부전, 만성 신부전::신부전",
      "user_id":..., "semester":..., "course":..., "unit":..., "name":..., "keyword":"신부전",
      "weight":5, "embedding":[ ... ] }
    ```

### 2-2. 과목 간 유사도 연결(cross-edges) 계산
- 새 함수 `build_cross_links(user_id, threshold=0.78, top_k=3)`:
  - 모든 개념 쌍 중 **서로 다른 course**인 것만 대상으로 코사인 유사도 계산.
  - 각 개념마다 다른 과목에서 가장 유사한 top_k 개념을 cross-link로 채택(유사도 ≥ threshold).
  - 결과를 `data/concept_links.json`에 저장:
    ```
    { "edges":[ {"a":"<id>","b":"<id>","score":0.83,"type":"cross"} ... ] }
    ```
  - 같은 단원 내 links(Phase 1)는 `type":"intra"` 로 함께 둘 수 있다.

### 2-3. 트리거 엔드포인트
- `POST /reindex-graph` (user_id):
  - `build_concept_embeddings` → `build_cross_links` 순으로 실행, 저장.
  - 응답: `{ "ok":true, "concepts":N, "cross_edges":M }`
  - (concepts.json이 먼저 있어야 함 — 안내 메시지 포함)

### 2-4. 그래프 조회 엔드포인트
- `GET /concept-graph?user_id=&semester=`(semester 선택적):
  - `concept_index.json` + `concept_links.json`을 합쳐 프론트가 그릴 그래프를 반환:
    ```
    { "nodes":[ {"id","name","keyword","course","unit","weight"} ... ],
      "edges":[ {"a","b","type","score"} ... ] }
    ```
  - 노드의 `course`로 색을 다르게 칠하면 "과목을 잇는" 그래프가 한눈에 보인다.

### 2-5. (선택) 답변 품질 연계
- `answer_with_connections`에서 질문 개념과 가까운 cross-link 개념의 과목 자료를 우선 검색에 포함하면,
  답이 다른 과목 지식까지 끌어와 더 좋아진다. (지금은 안 해도 됨, 다음 단계)

---

## 확인 시나리오
1. `/reindex-concepts` (Phase 1 반영본)로 한 과목 재추출 → concepts.json에 keyword·links·page 채워짐 확인.
2. `/reindex-graph` 실행 → concept_index.json, concept_links.json 생성, cross_edges > 0 확인.
3. `/concept-graph?user_id=정유진` 호출 → 서로 다른 course의 노드가 edge(type:"cross")로 연결돼 있는지 확인.
   - 기대 예시: "신부전"(병태생리학) ↔ "신장"/"체액조절"(인체구조와기능) 연결.

프론트(개념 지도 화면)는 이 `/concept-graph`를 받아 과목별 색 + 교차 연결선으로 그리면 된다. (프론트 작업은 분리)
