# Copilot 작업 2: 학습 작업대 읽기 API

작업 1인 concept-notes API가 승인된 뒤에만 사용한다.
이번에는 학습 작업대용 읽기 API만 구현하고 UI는 만들지 않는다.

## 요청

`GET /study-workspace`는 필수 query인
`semester`, `course`, `unit`, `filename`을 받는다.
소유자는 요청 body가 아니라 `current_uid`로 확인한다.

응답 구조:

- `scope`: semester, course, unit, filename
- `source_available`: 원본 PDF 미리보기 가능 여부
- `pages`: page, title, text_preview, concepts
- `concepts`: name, definition, first_page, pages, occurrences, note

## 구조 규칙

1. 같은 page의 여러 chunk는 하나로 합치고 페이지 숫자순으로 정렬한다.
2. text_preview는 chunk의 원래 순서를 유지해 합치되 최대 700자로 제한한다.
3. 확실한 제목이 없으면 제목을 추정하지 않고 `p.{page}`로 표시한다.
4. pages[].concepts는 저장된 개념의 occurrences/pages만 사용한다.
5. 새로운 키워드 추출이나 의미 추론을 하지 않는다.
6. concepts는 first_page 순서로 정렬하고, 같은 페이지에서는 저장 순서를 유지한다.
7. concepts[].note는 동일 사용자와 동일 범위·파일·개념의 노트 한 건 또는 null이다.
8. 별도의 단일 note 필드는 만들지 않는다.
9. 원본 파일이 없어도 소유 chunk가 있으면 작업대 데이터는 반환한다.

기존 `get_chunks`, `/concepts`,
`_augment_concepts_with_page_locations`, `/file` 소유권 방식을 재사용한다.
별도 SPEC 파일은 없으며 AGENTS.md와 기존 API 패턴을 따른다.

## 금지 범위

이 endpoint는 읽기 전용이다. 다음 작업을 호출하거나 수행하지 않는다.

- LLM 또는 GPT API
- 임베딩 생성
- OCR 또는 재추출
- 개념 저장 또는 재색인
- data JSON, uploads, Chroma 데이터 수정

## 필수 테스트

- 여러 chunk가 한 page로 합쳐지고 페이지가 숫자순으로 정렬된다.
- 개념이 최초 등장 페이지 순서로 정렬된다.
- concept note가 대응하는 concepts[].note에만 포함된다.
- 다른 사용자의 chunk, 개념, 노트가 노출되지 않는다.
- 다른 사용자의 filename으로 조회하면 404를 반환한다.
- OpenAI provider와 저장 헬퍼가 호출되지 않는다.

완료 후 변경 파일, 실행한 테스트, 제목 추출 한계를 보고한다.
