# LinkNote 개발 역할·승인 운영 지침

기준일: 2026-08-22
문서 소유자 및 최종 감독자: 정유진

## 현재 개발 현황

| 영역 | 상태 | 근거 | 다음 책임자 |
| --- | --- | --- | --- |
| `concept_notes` 저장 구조와 API | GitHub 통합 완료 | PR #13, merge `00af84e` | Codex 회귀 검수 |
| `/study-workspace`와 안전한 `/file` 연결 | GitHub 통합 완료 | PR #14, merge `2bd349e` | Codex 회귀 검수 |
| 3열 학습 작업대 UI | 구현 완료, 병합 대기 | `web/study-workspace.html`/`.js`/`-logic.js`, PR #17에 포함. 기존 `GET /study-workspace`·`GET/PUT /concept-notes`·`GET /file` 응답만 사용, 백엔드 무변경 | 감독자 최종 승인 → merge |
| 기존 로컬 저장소(`study-rag-api`) dirty 상태 | 정리 완료 | `backup/local-work-20260822` 브랜치로 4,400여 줄 백업·커밋·push, `origin/main`과 병합 충돌(3개 파일, 23곳) 해결, 인증 fallback 스텁 제거, 문서 버전 표기 수정 | — |
| PR #17 (`backup/local-work-20260822` → `main`) | `MERGEABLE`, 병합 대기 | 테스트 172개 통과(원래 22개에서 증가), 실사용 서버로 학습 작업대·개념 노트 저장 재검증 완료 | 감독자 최종 승인 후 Codex가 merge |
| Manus UX 프로토타입 | 참고안·조건부 승인 | `Koryoss/linknote-concept-ux` | 학습 작업대 UI에 부분 반영 완료 |
| CareFlow `<스터디>` 연계 | 부분 구현 | `/study/import`, `/study/ask`, `/study/claim`, claims CRUD 존재 | 계약 검수 후 양쪽 제품별 구현 |
| `/study/audit` | 미구현·계획 경계 | `AGENTS.md`에 접점만 존재하고 실제 route 없음 | 감독자 우선순위 승인 후 설계 |

상태 값은 `제안 → 진행 중 → 조건부 승인 → 승인 → 통합 → 실제 앱 검증` 순서로 사용한다. 에이전트의 완료 보고만으로 상태를 `승인` 이상으로 바꾸지 않는다.

## 감독자: 정유진

감독자는 제품과 사용자 데이터의 최종 의사결정권자다.

담당:

- 개발 우선순위와 학습 UX 방향 결정
- 자료 이동·삭제·재추출·재색인 등 실제 데이터 변경 승인
- OpenAI 등 유료 API 사용과 비용 범위 승인
- Claude Cowork·Copilot·Codex에 넘길 범위 선택
- 조건부 승인과 최종 승인 구분
- GitHub 병합, 배포, 데스크탑 앱 교체의 최종 승인
- CareFlow와 LinkNote 사이의 제품 경계·스키마 변경 승인

감독자는 코드 세부 구현을 직접 해결할 필요는 없지만, 다음 질문에는 명시적으로 답한다.

- 사용자가 실제로 원하는 결과가 무엇인가?
- 기존 데이터에 영향을 주어도 되는가?
- 유료 API를 사용해도 되는가?
- 두 제품 중 어느 쪽이 기능과 데이터를 소유하는가?
- 조건부 결과를 실제 앱에 반영해도 되는가?

## Codex

Codex는 오케스트레이터·검수자·통합 책임자다.

담당:

- 요청을 진단, 구현, 검증, 데이터 변경, 통합으로 분류
- `AGENTS.md`, 이 문서, SPEC 존재 여부, 레지스트리 확인
- Claude Cowork와 Copilot에 줄 범위를 작게 정의
- 도구 보고가 아니라 실제 코드, diff, 호출 경계, 테스트를 검수
- 사용자 데이터와 dirty 작업 트리를 보존
- 승인된 변경만 최신 `origin/main`의 clean clone/worktree에 재현
- 대상 테스트, 전체 테스트, 문법·diff 검사를 수행하고 PR을 검토
- 감독자 승인 범위 안에서 GitHub 통합
- 실제 데스크탑 앱 스모크 테스트 결과와 남은 위험 보고

금지:

- 감독자 승인 없이 실제 자료 삭제·이동·재추출·재색인
- 감독자 승인 없이 유료 API 호출
- dirty 저장소에서 `reset`, 광범위 restore, 무조건 pull, `git add -A`
- 다른 도구의 완료 보고만으로 최종 승인

## Claude Cowork

Claude Cowork는 범위가 큰 하나의 구현 단위를 맡는 빌드 작업자다. 현재 우선 책임은 3열 학습 작업대 UI다.

작업 조건:

- 최신 GitHub main을 새로 복제한 `linknote-cowork` 폴더 사용
- 별도 feature branch 사용
- Manus 결과는 UX 참고 자료로만 사용
- 기존 FastAPI와 `web/*.html` 구조를 유지
- 구현 전 실제 API route와 merge commit 확인
- 완료 시 변경 파일, 테스트, 수동 검증, 미확인 항목 보고

금지:

- 이미 구현된 `/study-workspace` 또는 `concept_notes` 중복 구현
- 원본 dirty 저장소 수정
- `data`, `uploads`, `chroma_db` 수정
- 승인 전 commit·push·PR·merge
- React 프로토타입을 제품 구조 확인 없이 그대로 이식

## Copilot

Copilot은 짧고 수술적인 코드 작업과 테스트 보강에 사용한다.

적합한 작업:

- 특정 함수 한두 곳의 예외 처리
- 명시된 회귀 테스트 추가
- 작은 접근성·문구·스타일 수정
- 기존 패턴을 따르는 단일 API 또는 헬퍼 보완

부적합한 작업:

- 제품 아키텍처와 데이터 소유권 결정
- 여러 단계 기능을 한 번에 구현
- 실제 데이터 마이그레이션
- 전체 파일 문자열 치환·재포맷
- GitHub 통합과 최종 승인

Copilot 결과는 항상 Codex가 실제 diff와 테스트로 재검증한다.

## CareFlow `<스터디>`와 LinkNote의 경계

### CareFlow가 소유하는 것

- `<스터디>` 화면과 학습 자료 작성 흐름
- CareFlow 안에서 생성된 원본 문서·페이지 구조
- LinkNote로 내보내기 전 사용자 편집 상태
- 내보내기 UX와 사용자 동의

### LinkNote가 소유하는 것

- `linknote-pages-v1` 가져오기와 사용자 범위 인덱싱
- 가져온 자료에 대한 검색과 근거 기반 응답
- 학습 주장 생성·저장과 출처 강도 표시
- LinkNote 내부 인증, 자료 소유권, 검색 청크, 개념·노트 데이터

### 현재 계약

| 접점 | 상태 | 책임 |
| --- | --- | --- |
| `POST /study/import` | 구현됨 | CareFlow export를 LinkNote 자료로 가져오기 |
| `POST /study/ask` | 구현됨 | 가져온 자료 범위에서 질문·출처 반환 |
| `POST /study/claim` | 구현됨 | 주장 초안과 근거 강도 생성 |
| `/study/claims` CRUD | 구현됨 | 인증 사용자 주장 저장·조회·삭제 |
| `/study/audit` | 미구현 | 문구·경계 검수 후보; 구현 전 별도 명세 필요 |

경계 규칙:

- CareFlow 데이터와 LinkNote My Library, 개념 노트, Learning Memory를 자동으로 합치지 않는다.
- import는 idempotency, 사용자 소유권, 중복 자료 정책을 명시한 뒤 확장한다.
- 임상 판단처럼 보일 수 있는 문구는 교육 목적, 자료 근거, 한계가 드러나야 한다.
- payload 또는 스키마 변경은 양쪽 소비자를 함께 검증한다.
- cross-product 쓰기, 데이터 이전, 삭제, 재색인은 감독자 승인을 받는다.

## 표준 작업 순서

1. 감독자가 목표·우선순위·데이터 및 비용 허용 범위를 정한다.
2. Codex가 저장소 상태와 계약을 진단하고 작업을 분리한다.
3. Copilot은 작은 수정, Claude Cowork는 큰 일관 구현을 맡는다.
4. Codex가 실제 diff, 보안·소유권 경계, 테스트를 검수한다.
5. 감독자가 기능 결과와 UX를 조건부 또는 최종 승인한다.
6. Codex가 승인된 변경만 clean branch로 GitHub에 통합한다.
7. Codex와 감독자가 실제 데스크탑 앱에서 검증해 상태를 `실제 앱 검증`으로 바꾼다.

## 인수인계 보고 형식

모든 도구는 다음을 보고한다.

1. 맡은 범위와 제외한 범위
2. 읽은 기준 문서와 현재 commit
3. 변경 파일과 사용자 영향
4. 실행한 테스트와 실패 포함 전체 결과
5. 실제 데이터·유료 API·외부 시스템 변경 여부
6. 확인하지 못한 항목과 위험
7. 현재 승인 상태
8. 다음 책임자와 정확한 다음 작업

“완료”, “문제없음”, “통과”만으로 보고를 끝내지 않는다.
