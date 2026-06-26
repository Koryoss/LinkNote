# Study RAG Local 프로젝트 방향

## 1. 목표

이 프로젝트는 개인 공부용 PDF RAG 앱을 단계적으로 발전시키는 것을 목표로 한다.

초기에는 로컬에서 혼자 쓰는 Streamlit 앱으로 시작하고, 이후 OpenAI API 전환, 사용자별 자료 분리, 웹 서비스 배포까지 확장할 수 있게 구조를 정리한다.

이 앱의 차별점은 단순히 PDF 하나에 질문하는 것이 아니라, 여러 학기와 여러 과목의 PDF를 누적해두고, 현재 질문과 관련된 이전 학기/다른 과목의 내용을 함께 찾아 공부할 개념 간 연결을 보여주는 것이다.

## 2. A. 제품 발전 단계

### A-1. 터미널에서 실행하는 로컬 웹앱

- Streamlit으로 로컬 웹앱 실행
- PDF 업로드
- 로컬 AI 모델로 embedding 생성 및 답변 생성
- ChromaDB에 학습 데이터 저장

### A-2. 실행 스크립트로 간단 실행

- 매번 명령어를 직접 입력하지 않아도 실행되도록 스크립트 추가
- 예: 앱 실행, DB 확인, DB 초기화 등을 간단한 명령으로 처리

### A-3. 데스크톱 앱처럼 패키징

- 사용자가 터미널을 거의 보지 않아도 실행할 수 있게 구성
- 로컬 앱을 데스크톱 앱처럼 사용할 수 있는 형태로 패키징

### A-4. 웹 서비스로 배포

- 로컬 전용 앱을 웹 서비스 형태로 확장
- 사용자가 브라우저에서 접속해 PDF 학습 및 질문 답변을 사용할 수 있게 배포
- 이 단계에서는 Streamlit보다 FastAPI + Next.js 구조가 더 적합해질 수 있다.

## 3. 현재 수준

현재 프로젝트는 사용자의 컴퓨터 안에서 실행되는 개인 공부 앱이다.

현재 가능한 일:

- PDF를 올리면 자동으로 텍스트를 읽는다.
- 학기, 과목명, 자료명을 함께 저장한다.
- PDF 텍스트를 chunk로 나눈다.
- 로컬 embedding 모델로 벡터를 만든다.
- ChromaDB에 텍스트 chunk, embedding, metadata를 저장한다.
- 질문하면 관련 chunk를 검색한다.
- 자료 기반 답변을 생성한다.
- 답변에 학기, 과목, 자료명, 파일명, 페이지 출처를 표시한다.

현재 구조:

```text
자료 등록:
학기 + 과목명 + 자료명 + PDF

저장:
텍스트 chunk + embedding + metadata

질문:
관련 chunk 검색

답변:
자료 기반 설명 + 학기/과목/페이지 출처
```

현재 앱은 `Ollama + ChromaDB + Streamlit` 기반이다.

## UI Vision

현재:

- 관리도구 스타일 UI
- 자료 등록 / 삭제 / 검색 범위 선택 중심
- 정보를 "보관하고 관리하는" 화면처럼 보인다.

미래:

- 지식 갤러리 스타일 UI
- 개념과 자료가 독립된 조각으로 보이고, 그 조각들이 연결되어 하나의 구조를 이룬다.
- 정보를 "연결하고 이해하는" 화면으로 보인다.

철학:

- PDF는 독립된 조각이다.
- 과목은 독립된 조각이다.
- 개념은 독립된 조각이다.
- LinkNote는 그 조각들을 연결해 하나의 학습 구조를 보여준다.

참고 키워드:

- Knowledge Gallery
- Concept Canvas
- Connected Learning

진행 메모:

- 1차로 Hero 문구, 지식 갤러리 개념 카드, 연결된 자료 카드를 추가했다(전체 리디자인 아님).
- 실제 개념 그래프는 아직 만들지 않는다. 지금은 "조각이 모여 구조가 된다"는 느낌 전달이 목표다.
- 팀 역할 구분(코덱스=코어 엔진, 클로드=UI·문서)은 `TEAM_ROLES.md` 참고.
- 이 UI는 로컬 버전(`study-rag-local`)과 공통으로 유지한다. 한쪽을 바꾸면 양쪽 다 맞춘다.

## 4. 핵심 기능 로드맵

### 4.1 자료 등록

- 학기 / 과목명 / 자료명 / PDF 저장
- metadata에 `semester`, `course`, `title`, `filename`, `page`, `chunk_index` 저장

### 4.2 자료 현황 확인

- 좌측 sidebar에서 저장된 자료 현황 표시
- 총 chunk 수 표시
- 학기별 교과목명 표시
- 교과목별 파일 수 표시
- 파일명 목록 표시

### 4.3 검색 범위 필터

- 전체 자료 검색
- 특정 학기 검색
- 특정 과목 검색
- 특정 파일 검색
- 특정 학기 + 과목 조합 검색

### 4.4 여러 PDF 연결 검색

- 질문과 직접 관련된 자료 찾기
- 다른 PDF / 다른 과목 / 다른 학기에서 연결되는 자료 찾기
- 직접 관련 자료와 연결 후보 자료를 분리해서 보여주기
- 복습 키워드 + 쪽수 표시
- 출처 표시

여러 PDF 연결 검색은 이 프로젝트의 핵심 기능이다. PDF를 하나씩 따로 보는 것이 아니라, 여러 학기와 과목의 자료를 누적해두고 현재 질문과 연결되는 과거 지식, 다른 과목 개념, 관련 페이지를 함께 찾아 복습 흐름을 만들어준다.

### 4.5 API 전환

- 답변 생성만 OpenAI API로 전환
- 필요하면 embedding까지 OpenAI API로 전환

## 5. 사용자 분리 / 계정 확장 계획

초기에는 진짜 회원가입 기능을 만들지 않는다.

대신 `user_id`를 metadata에 저장하는 방식으로 사용자별 자료 분리 구조를 먼저 연습할 수 있다.

예:

- `user_id = "default"`
- 또는 사용자가 입력한 이름

모든 chunk metadata에 `user_id`를 저장하면 사용자별 자료 분리가 가능하다. 나중에 웹 서비스로 확장할 때 이 `user_id`를 실제 로그인 계정과 연결한다.

API 비용 관리에도 `user_id`가 필요하다.

추적 가능한 항목:

- 사용자별 질문 횟수
- 사용자별 토큰 사용량
- 사용자별 업로드 파일 수
- 사용자별 저장 chunk 수

metadata 예시:

```json
{
  "user_id": "default",
  "semester": "2026-1",
  "course": "병리생리학1",
  "title": "신부전",
  "filename": "renal_failure.pdf",
  "page": 1,
  "chunk_index": 0
}
```

## 6. API 전환 청사진

나중에 로컬 Ollama 모델 대신 OpenAI API를 사용할 수 있도록 provider 구조를 유지한다.

### 6.1 현재 구조

```text
app.py
↓
rag.py
↓
providers/ollama_provider.py
↓
Ollama 로컬 모델
```

### 6.2 목표 구조

```text
app.py
↓
rag.py
↓
providers/openai_provider.py
↓
OpenAI API
```

웹 서비스로 확장하면 다음 구조를 목표로 한다.

```text
사용자 브라우저
↓
Next.js 화면
↓
FastAPI API 서버
↓
rag.py
↓
ChromaDB / PostgreSQL / OpenAI API
```

## 7. B. API 전환 단계

### B-1. 답변 생성만 OpenAI API로 전환

```text
embedding: Ollama 로컬
답변 생성: OpenAI API
```

장점:

- PDF 학습 비용 없음
- 기존 ChromaDB 유지 가능
- PDF 재학습 필요 없음
- 답변 속도와 한국어 품질을 먼저 개선할 수 있음

단점:

- embedding 검색 품질은 로컬 모델 수준

하이브리드 provider:

```text
providers/hybrid_provider.py
```

예상 구조:

```python
from providers.ollama_provider import embed_text
from providers.openai_provider import generate_answer
```

의미:

- embedding은 기존 Ollama / `nomic-embed-text` 사용
- 답변 생성은 OpenAI API 사용
- 기존 ChromaDB 유지 가능
- PDF 재학습 필요 없음
- 답변 속도와 한국어 품질을 먼저 개선할 수 있음

### B-2. embedding + 답변 모두 OpenAI API로 전환

```text
embedding: OpenAI API
답변 생성: OpenAI API
```

장점:

- 검색 품질 개선
- 답변 품질 개선
- 구조가 더 안정적

단점:

- PDF 학습할 때도 비용 발생
- 기존 ChromaDB 재생성 또는 collection 분리 필요

## 8. OpenAI API 전환 준비 작업

### 8.1 OpenAI 패키지 추가

`requirements.txt`에 추가:

```text
openai
python-dotenv
```

설치:

```bash
pip install -r requirements.txt
```

### 8.2 API 키 저장

프로젝트 폴더에 `.env` 파일 생성:

```text
OPENAI_API_KEY=너의_API_KEY
```

그리고 `.gitignore`도 만든다.

```text
.env
chroma_db/
data/uploads/
venv/
```

### 8.3 OpenAI provider 만들기

새 파일:

```text
providers/openai_provider.py
```

역할은 기존 `providers/ollama_provider.py`와 똑같이 두 함수만 제공하면 된다.

- `embed_text(text)`
- `generate_answer(prompt)`

예상 구조:

```python
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4.1-mini"


def embed_text(text: str):
    response = client.embeddings.create(
        model=EMBED_MODEL,
        input=text
    )
    return response.data[0].embedding


def generate_answer(prompt: str):
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": "너는 강의자료 기반 학습 도우미다. 반드시 한국어로만 답변한다."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2
    )

    return response.choices[0].message.content
```

### 8.4 `rag.py`에서 provider 교체

현재:

```python
from providers.ollama_provider import embed_text, generate_answer
```

OpenAI만 사용할 때:

```python
from providers.openai_provider import embed_text, generate_answer
```

하이브리드로 사용할 때:

```python
from providers.hybrid_provider import embed_text, generate_answer
```

## 9. embedding provider 변경 시 주의사항

Ollama의 `nomic-embed-text`로 만든 embedding과 OpenAI의 `text-embedding-3-small` embedding은 벡터 차원과 의미 공간이 다르다.

따라서 둘을 같은 ChromaDB 컬렉션에 섞어 쓰면 안 된다.

embedding 모델을 바꿀 때는 다음 중 하나를 선택한다.

### 방법 A: 기존 `chroma_db` 삭제 후 재학습

OpenAI embedding으로 바꾸면 기존 DB를 지우고 PDF를 다시 학습시켜야 한다.

```bash
rm -rf chroma_db
```

그다음 앱을 다시 실행하고 PDF를 다시 업로드/학습한다.

### 방법 B: collection 이름 분리

서로 다른 embedding 공간이 섞이는 실수를 줄이기 위해 collection 이름을 분리할 수 있다.

예:

- `study_notes_ollama`
- `study_notes_openai`

이 방식은 Ollama embedding 실험과 OpenAI embedding 실험을 나란히 유지할 때 유용하다.

## 10. 추천 전환 순서

추천은 단계적으로 바꾸는 것이다.

1. `providers/openai_provider.py` 추가
2. `generate_answer`만 OpenAI로 먼저 연결
3. `embedding`은 기존 Ollama 유지
4. `providers/hybrid_provider.py` 방식으로 실행
5. 속도와 답변 품질 개선 확인
6. 만족하면 `text-embedding-3-small`로 embedding도 교체
7. `chroma_db` 삭제 후 PDF 재학습 또는 collection 이름 분리

## 11. 현재 유지해야 할 원칙

- 전체 앱 구조를 갑자기 새로 만들지 않는다.
- `app.py`, `rag.py`, `pdf_loader.py`, `providers/` 구조를 유지한다.
- `app.py`에는 Streamlit 화면 로직만 두고, 복잡한 기능 로직은 별도 함수로 분리한다.
- `rag.py`는 질문 답변 로직을 담당한다.
- `pdf_loader.py`는 PDF 텍스트 추출만 담당한다.
- 나중에 `library.py`를 만들어 저장된 자료 현황 조회를 분리할 수 있다.
- OpenAI API 전환 전까지는 Ollama 기반 로컬 실행을 기본으로 둔다.
- embedding provider를 바꾸면 기존 ChromaDB와 섞어 쓰지 않는다.
- PDF 원본, ChromaDB, `.env`, `venv`는 Git에 올리지 않는다.

## 12. 자주 쓰는 실행 명령어

### 로컬 버전 실행

```bash
cd ~/Desktop/study-rag-local
source venv/bin/activate
streamlit run app.py
```

### API 버전 실행

```bash
cd ~/Desktop/study-rag-api
source venv/bin/activate
streamlit run app.py
```

### 앱 종료

```text
Control + C
```

### DB 확인

```bash
python inspect_db.py
```

### Ollama 모델 확인

```bash
ollama list
```
