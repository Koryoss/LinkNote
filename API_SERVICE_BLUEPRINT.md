# Study RAG API 서비스화 청사진

## 1단계: API 버전

초기 목표는 `study-rag-api`를 개인 실험용 API 기반 구조로 정리하는 것이다.

이 단계에서는 진짜 계정 기능 없이 혼자 쓰는 실험용으로 진행한다.

구성:

- `study-rag-api`
- Ollama embedding
- OpenAI 답변 생성

역할:

- `app.py`: Streamlit 전용 화면이다. 나중에는 버리거나 참고용으로만 사용한다.
- `Next.js`: 새 프론트엔드가 된다.
- `FastAPI routes`: 새 API 엔드포인트가 된다.

## 2단계: 로컬 사용자 개념 추가

아직 진짜 회원가입은 만들지 않는다.

대신 앱 시작 시 사용자명을 입력하게 한다.

예:

```text
사용자명: yujin
```

입력한 사용자명은 `user_id`로 사용하고, PDF chunk metadata에 함께 저장한다.

예:

```json
{
  "user_id": "yujin",
  "semester": "2026-1",
  "course": "병리생리학1"
}
```

이렇게 하면 실제 로그인 기능을 만들기 전에 사용자별 자료 분리 구조를 미리 연습할 수 있다.

## 3단계: 진짜 계정 기능

웹 서비스로 전환할 때 추가한다.

필요한 기능:

- 회원가입
- 로그인
- 비밀번호 암호화
- 세션 관리
- 사용자별 PDF 저장
- 사용자별 API 사용량 추적

이 단계에서는 Streamlit보다 FastAPI + Next.js 구조가 더 적합하다.

목표 구조:

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

## 재사용할 것

다음 파일과 구조는 최대한 재사용한다.

- `pdf_loader.py`: PDF 텍스트 추출 담당. 거의 그대로 재사용 가능하다.
- `rag.py`: 질문 답변 로직 담당. 함수 구조만 잘 정리하면 재사용 가능하다.
- `providers/ollama_provider.py`: Ollama 모델 호출 담당. 그대로 재사용 가능하다.
- `providers/openai_provider.py`: OpenAI 모델 호출 담당. 그대로 재사용 가능하다.
- `providers/hybrid_provider.py`: Ollama embedding + OpenAI 답변 생성 같은 혼합 구성을 담당. 그대로 재사용 가능하다.
- ChromaDB 저장 방식: 초기에는 재사용 가능하다.
- 학기/과목/자료명 metadata 구조: 그대로 가져간다.

## 바뀌는 것

주로 화면과 API 입구가 바뀐다.

- `app.py`: Streamlit 전용이라 나중에는 버리거나 참고용으로만 사용한다.
- `Next.js`: 새 프론트엔드가 된다.
- `FastAPI routes`: 새 API 엔드포인트가 된다.

## 앞으로의 코드 분리 원칙

나중에 전면 수정을 피하려면 Streamlit 안에 복잡한 로직을 많이 넣지 않는다.

`app.py`는 화면만 담당하고, 실제 기능은 다른 파일의 함수를 호출하게 만든다.

권장 구조:

```text
app.py
= Streamlit 화면만 담당

rag.py
= 질문 답변 로직만 담당

pdf_loader.py
= PDF 텍스트 추출만 담당

library.py
= 저장된 자료 현황 조회 담당

providers/
= 모델 호출 담당
```

이 구조를 유지하면 나중에 Streamlit을 Next.js + FastAPI로 바꿀 때 핵심 로직을 덜 고치고 옮길 수 있다.
