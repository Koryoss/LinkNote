# LinkNote 개념 추출 품질 계획

작성 기준: 2026-08-15

이 문서는 LinkNote의 개념 추출 현재 동작, 알려진 실패 유형, 목표 파이프라인, 품질 지표와 구현 순서를 한곳에 정리한 기준 문서다. 과거 설계 배경은 루트의 `COPILOT_PROMPT_concepts.md`와 `COPILOT_PROMPT_concept_graph.md`에 남아 있지만, 앞으로 개념 추출 품질을 변경할 때는 이 문서를 먼저 갱신한다.

## 1. 목표

개념 추출의 목표는 단원마다 일정 개수의 단어를 만드는 것이 아니다. 다음 조건을 만족하는 근거 기반 개념 인덱스를 만드는 것이다.

- 자료의 핵심 개념을 페이지 전체에서 빠뜨리지 않는다.
- 한국어 표준명, 본문 표기, 영문명, 약어와 동의어를 하나의 개념으로 묶는다.
- 모든 개념을 실제 파일과 페이지 근거로 되돌아갈 수 있게 한다.
- OCR 오류나 모델 추론을 원문 사실처럼 저장하지 않는다.
- 전체 단원을 반복 추출하지 않고 누락된 페이지만 보완할 수 있게 한다.
- 검색, 개념 지도와 Learning Memory가 같은 canonical concept를 사용하게 한다.

개념 개수 자체는 품질 지표가 아니다. 짧은 자료는 적게, 긴 자료는 많이 나올 수 있어야 하며 `10개`, `16개` 같은 상한을 목표 개수로 취급하지 않는다.

## 2. 현재 구현

### 2.1 업로드와 텍스트 추출

현재 `/ingest`는 다음 순서로 동작한다.

1. PDF를 저장한다.
2. PyMuPDF native text extraction을 우선 사용한다.
3. 한국어 자료인데 한글 비율이 지나치게 낮은 페이지는 `kor+eng`, 300 DPI OCR을 재시도한다.
4. 페이지 텍스트를 chunk로 나눠 ChromaDB에 저장한다.
5. 검색 캐시를 무효화한다.
6. 해당 단원의 개념 추출을 자동 실행한다.

개념 추출이 실패해도 PDF 저장과 검색 인덱싱은 성공으로 유지된다. 응답의 `concept_extraction_status`와 `concept_count`로 개념 추출 결과를 별도로 확인한다.

### 2.2 개념 추출

`rag.build_concepts_for_unit`의 현재 흐름은 다음과 같다.

1. 사용자·학기·과목·단원에 해당하는 모든 chunk를 불러온다.
2. 긴 단원을 약 9,000자 segment로 나눈다.
3. segment마다 최대 16개 개념을 LLM으로 추출한다.
4. 잘린 JSON 응답은 salvage parsing으로 복원한다.
5. `keyword` 기준으로 중복을 병합한다.
6. 개념을 3~6개 상위 `group`으로 분류한다.
7. 중요도 `weight` 순으로 저장한다.

위 흐름은 현재 저장 API가 사용하는 legacy 경로다. 검증 중인 candidate-aware 경로는 고정 16개 상한을 제거하고, segment를 페이지 경계에서만 나누며, 모든 페이지를 순서대로 점검하도록 요구한다. 페이지 수와 누락 후보 수에 따라 출력 한도를 2,200~6,000 token 범위에서 조정하되 실제 개념 수를 목표값으로 강제하지 않는다.

현재 저장 필드는 다음과 같다.

- `name`: 한국어 표준 개념명
- `keyword`: 본문에서 실제로 찾을 수 있는 표기
- `aliases`: 영문명, 약어, 한국어 동의어
- `weight`: 모델이 정한 중요도 1~5
- `page`, `filename`: 첫 근거 위치
- `pages`, `occurrences`: 전체 근거 위치
- `links`: 같은 단원에서 연결된 개념
- `group`: 상위 분류

### 2.3 재추출과 비용

- 업로드 시 현재 개념 추출이 자동 실행된다.
- `POST /reindex-concepts`로 단원 또는 과목을 다시 추출할 수 있다.
- 두 경로의 개념 추출은 `generate_answer`를 사용하므로 설정된 OpenAI/API provider 비용이 발생할 수 있다.
- OCR, 결정적 후보 추출, 원문 검증과 검색의 문서 기반 fallback은 LLM 생성 호출을 추가하지 않는다.
- 재추출은 기존 `concepts.json`의 대상 단원 값을 교체하므로 실행 전 범위와 비용을 화면에 알려야 한다.

## 3. 확인된 실패 유형

### 3.1 원문에는 있지만 추출 목록에 없는 개념

대표 사례는 병태생리학 자료의 `서맥 (Bradycardia)`이다. PDF p.4 원문과 빠른 검색에는 있었지만 기존 `concepts.json`에는 없어 관련 개념이 비었다.

현재 빠른 검색은 이 경우 원문에서 직접 확인된 질문 용어를 `origin=document`인 임시 관련 개념으로 보여준다. 이는 검색 누락을 가리는 안전망이며 `concepts.json`이나 개념 그래프를 자동 수정하지 않는다.

### 3.2 OCR 손상

슬라이드의 도형, 특수 글꼴과 이미지 텍스트는 native extraction에서 한국어가 사라지거나 `A|24`, `4194` 같은 문자열로 변할 수 있다. OCR을 적용해도 제목과 본문 순서가 섞일 수 있다.

### 3.3 segment별 추출 편차

각 segment를 독립적으로 처리하면 다음 문제가 생긴다.

- 한 segment에서 중요도가 낮다고 판단된 용어가 전체 결과에서 사라진다.
- 같은 개념이 한국어명, 영문명, 약어로 중복 생성된다.
- 정의는 다음 segment에 있는데 용어만 앞 segment에 있어 관계가 끊긴다.
- 모델 출력 길이가 잘리면 뒤쪽 개념이 구조적으로 누락된다.

### 3.4 고정 개수 편향

상한이 사실상 목표 개수처럼 작동하면 긴 단원도 비슷한 개수만 남는다. `material_registry.csv`의 `concept_count`가 여러 자료에서 반복되는 현상은 coverage 검사가 필요하다는 신호로 취급한다.

## 4. 목표 파이프라인

목표 파이프라인은 `원문 품질 검사 → 결정적 후보 생성 → LLM 구조화 → 원문 검증 → 누락 보완 → 저장` 순서다.

### 4.1 단계 A: 페이지별 원문 품질 검사

페이지마다 다음 값을 계산해 extraction metadata로 남긴다.

- 전체 문자 수
- 한글·영문·숫자 비율
- native extraction과 OCR 중 선택된 방식
- OCR 전후 한글 문자 증가량
- 반복 특수문자와 깨진 토큰 비율

한국어 자료에서 한글 비율이 낮거나 텍스트가 거의 없을 때만 OCR을 실행한다. native와 OCR 중 단순히 긴 텍스트가 아니라 읽을 수 있는 단어 비율과 한국어 보존량이 높은 쪽을 선택한다.

### 4.2 단계 B: 결정적 개념 후보 생성

LLM 호출 전에 페이지 원문에서 후보를 만든다. 후보는 개념 정답이 아니라 누락 검사용 체크리스트다.

우선 후보:

- `한국어 (English)` 또는 `English (한국어)` 형태
- 영문 전체 이름과 함께 나온 2~10자 약어
- 제목·소제목에 있는 명사구
- 정의 표현과 붙어 있는 용어: `~란`, `~은`, `정의`, `미만`, `이상`, `증상`, `원인`
- 여러 페이지 또는 한 페이지에서 반복되는 전문용어
- 사용자 alias와 기존 canonical concept 사전에 들어 있는 용어

후보마다 `surface`, `page`, `filename`, `evidence_span`, `candidate_rule`을 보존한다. OCR 잡음을 개념으로 만들지 않도록 문자 종류가 비정상적이거나 근거 문맥이 없는 후보는 제외한다.

### 4.3 단계 C: candidate-aware MAP 추출

현재 MAP 프롬프트에 해당 segment의 후보 목록을 함께 제공한다. 모델은 후보를 무조건 채택하는 것이 아니라 본문 근거가 있는 개념을 구조화한다.

요구 schema:

```json
{
  "name": "서맥",
  "keyword": "Bradycardia",
  "aliases": ["서맥"],
  "importance": 4,
  "related": ["Tachycardia"],
  "evidence": {
    "filename": "[Week 10] 심혈관계 질환 II.pdf",
    "page": 4,
    "surface": "Bradycardia"
  }
}
```

프롬프트 원칙:

- `name`은 한국어 표준 용어를 우선한다.
- `keyword` 또는 alias 중 하나는 반드시 원문에 실제로 존재해야 한다.
- 괄호 안 영문명과 약어를 버리지 않는다.
- 개념 수를 채우기 위해 일반 단어나 문서 밖 지식을 추가하지 않는다.
- 각 개념에 최소 하나의 page-level evidence를 반환한다.

### 4.4 단계 D: 결정적 REDUCE와 canonicalization

모델 결과를 그대로 저장하지 않고 다음 규칙으로 병합한다.

- Unicode NFKC, 대소문자, 공백, 하이픈을 정규화한다.
- `name`, `keyword`, `aliases` 중 하나가 겹치면 동일 개념 후보로 묶는다.
- 모델이 낸 `aliases`만 겹치는 경우에는 병합하지 않는다. alias는 다른 개념을 잘못 연결할 수 있으므로 `name` 또는 `keyword`의 직접 일치를 병합 기준으로 사용한다.
- 기존 단원에 같은 `name` 또는 `keyword`가 있으면 기존 canonical 이름을 재사용한다.
- alias나 evidence가 다른 기존 canonical concept에 속하면 제거하고, 근거가 전부 제거된 새 항목은 거부한다.
- 한국어명과 영문명이 같은 괄호 표현에서 나왔으면 하나의 bilingual concept로 묶는다.
- 약어는 같은 근거 문맥에서 expansion이 확인될 때만 병합한다.
- `occurrences`는 파일·페이지 기준으로 중복 제거한다.
- 원문에서 확인되지 않는 keyword는 저장하지 않는다.
- 서로 다른 뜻의 동형어는 과목·단원·근거 문맥이 다르면 강제로 병합하지 않는다.

### 4.5 단계 E: coverage validator

REDUCE 결과와 단계 B의 후보를 비교한다. 다음 후보가 결과의 `name`, `keyword`, `aliases` 어디에도 없으면 누락 후보로 기록한다.

- 한영 병기 용어
- 제목 또는 소제목 용어
- 정의 문장에 붙은 용어
- 두 번 이상 반복된 전문용어
- 사용자 alias로 등록된 용어

validator는 자동으로 새 개념을 확정하지 않는다. `covered`, `missing`, `rejected_as_noise`로 분류하고 근거 페이지를 남긴다.

### 4.6 단계 F: 증분 보완 추출

누락 후보가 있을 때 전체 단원을 다시 추출하지 않고 해당 페이지와 앞뒤 한 페이지 범위만 작은 프롬프트로 재처리한다. 보완 결과도 단계 D 원문 검증을 통과해야 저장된다.

증분 처리의 장점:

- 전체 재추출보다 비용이 작다.
- 이미 안정된 concept ID와 graph link의 변화를 줄인다.
- 어떤 후보 때문에 추가 호출됐는지 추적할 수 있다.

자동 증분 호출은 비용 정책을 확정한 뒤 활성화한다. 그 전에는 누락 보고서만 만들고 사용자가 `누락 개념 보완`을 실행하도록 한다.

### 4.7 단계 G: 검색과 그래프 반영

- 검증된 개념만 `data/concepts.json`에 저장한다.
- graph reindex는 변경된 concept만 다시 embedding할 수 있게 source digest를 사용한다.
- 빠른 검색의 `origin=document` 결과는 계속 안전망으로 유지한다.
- 문서 기반 임시 개념이 여러 번 검색되면 재추출 대상 후보로 올리되 자동으로 정본에 승격하지 않는다.

## 5. 저장 구조 확장안

기존 필드를 유지하면서 다음 metadata를 추가한다.

```json
{
  "name": "서맥",
  "keyword": "Bradycardia",
  "aliases": ["서맥"],
  "weight": 4,
  "page": 4,
  "filename": "[Week 10] 심혈관계 질환 II.pdf",
  "pages": [4],
  "occurrences": [
    {
      "filename": "[Week 10] 심혈관계 질환 II.pdf",
      "page": 4,
      "surface": "Bradycardia",
      "evidence_span": "분당 60회 미만의 느린 심박동"
    }
  ],
  "origin": "llm_verified",
  "extraction_version": "concept_quality_v1",
  "source_digest": "...",
  "extracted_at": "..."
}
```

숫자 confidence는 사용자 화면에 표시하지 않는다. 내부 모델 점수보다 `원문 직접 확인`, `alias로 확인`, `검토 필요` 같은 evidence 상태가 이해하기 쉽고 검증 가능하다.

## 6. 품질 지표

단순 concept count 대신 다음 지표를 단원별로 기록한다.

| 지표 | 정의 | 초기 목표 |
| --- | --- | ---: |
| source grounding rate | 저장 개념 중 원문 occurrence가 하나 이상인 비율 | 100% |
| page attachment rate | 저장 개념 중 파일과 page가 있는 비율 | 95% 이상 |
| bilingual preservation | 한영 병기 후보가 하나의 concept/alias로 보존된 비율 | 95% 이상 |
| candidate coverage | 고우선 후보가 concept 또는 alias에 포함된 비율 | 90% 이상 |
| duplicate rate | 동일 의미 개념이 중복 노드로 남은 비율 | 5% 이하 |
| OCR review rate | 저품질 판정 페이지 중 OCR 또는 검토 결과가 있는 비율 | 100% |

초기 목표는 회귀 fixture가 쌓이면 과목 유형별로 조정한다. 목표 미달 때문에 근거 없는 개념을 추가해서는 안 된다.

## 7. 회귀 테스트 자료

최소 fixture에는 다음 사례를 포함한다.

- `서맥 (Bradycardia)`와 `빈맥 (Tachycardia)`가 같은 슬라이드에 있는 경우
- 한글은 OCR로만 읽히고 영문은 native extraction에 남는 경우
- 약어가 여러 뜻을 가질 수 있는 경우
- 같은 개념이 여러 페이지에 반복되는 경우
- 긴 단원에서 segment 경계에 정의가 나뉘는 경우
- 표·도형의 숫자와 특수문자가 용어처럼 보이는 경우
- 영어 자료에서 한국어 표준명을 추론하되 원문 alias는 보존해야 하는 경우

테스트 층위:

1. OCR 선택과 텍스트 품질 단위 테스트
2. 결정적 후보 추출과 canonicalization 단위 테스트
3. 고정 LLM 응답 fixture를 사용한 MAP/REDUCE 테스트
4. 단원 재추출 전후 coverage 비교 테스트
5. 검색에서 concept와 근거 문서가 함께 반환되는 API 테스트

실제 API를 호출하는 테스트는 비용과 비결정성 때문에 기본 회귀 테스트에서 분리한다.

## 8. 구현 순서

### P0 — 완료 또는 현재 안전망

- 한국어 저품질 페이지의 `kor+eng` OCR fallback
- MAP segment 추출과 keyword 중복 병합
- 영문명·약어·동의어 `aliases` 보존
- 빠른 검색의 양방향 alias 확장
- 추출 누락 시 `origin=document` 관련 개념 표시

### P1 — 비용 없는 품질 계측

- 페이지 텍스트 품질 metadata
- 결정적 후보 생성기
- coverage validator와 단원별 품질 보고서
- `서맥/Bradycardia` 회귀 fixture

### P2 — 추출 정교화

- candidate-aware MAP schema
- evidence 기반 REDUCE/canonicalization
- 추출 버전과 source digest 저장
- 대상 페이지만 처리하는 증분 보완 API

### P3 — 운영 UI와 그래프 갱신

- 업로드 결과에 품질 보고서 요약 표시
- `누락 개념 보완` 사용자 트리거와 예상 API 사용 안내
- 변경 concept만 graph reindex
- 검토 필요 후보 승인·제외 UI

## 9. 운영 원칙

- PDF 저장·검색 인덱싱과 개념 추출 성공 여부를 분리한다.
- 비용이 발생하는 재추출은 범위와 실행 여부를 사용자에게 보여준다.
- 실패한 단원을 빈 성공으로 표시하지 않는다.
- 기존 단원 데이터를 교체하기 전에 새 결과가 source grounding 검사를 통과해야 한다.
- 사용자별 alias와 학습 기록은 다른 사용자 데이터와 섞지 않는다.
- 의료·간호 용어는 업로드 자료의 학습용 표현으로 다루며 진단이나 임상 판단으로 확장하지 않는다.

## 10. 관련 파일

- 현재 추출 구현: `rag.py`
- 업로드·재추출 API: `api_server.py`
- OCR: `pdf_loader.py`
- 추출 결과: `data/concepts.json`
- 사용자 alias: `data/search_profiles.json`
- 자료 정본 metadata: `material_registry.csv`
- 현재 개발 흐름: `docs/development-guide.md`
- 검색 연결: `docs/search-algorithm.md`
- 과거 설계 기록: `COPILOT_PROMPT_concepts.md`, `COPILOT_PROMPT_concept_graph.md`

## 11. 완료 기준

개념 추출 품질 개선은 다음 조건을 모두 만족할 때 완료로 본다.

- `서맥/Bradycardia` fixture가 추출 개념과 alias에 포함된다.
- 모든 저장 개념에 실제 파일·페이지 근거가 있다.
- 누락 후보와 OCR 저품질 페이지를 단원별로 확인할 수 있다.
- 한 단원만 증분 보완해도 다른 단원의 concept ID와 graph가 변하지 않는다.
- 기본 회귀 테스트는 외부 API 없이 재현 가능하다.
- 실제 API 추출 테스트는 별도 opt-in으로 실행되고 비용 발생 여부가 표시된다.
