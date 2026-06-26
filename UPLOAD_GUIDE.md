# 내 PDF 올리고 갤러리에서 보기 — 단계 안내

목표: 로컬에서 API 서버를 켜고, 내 PDF를 **단원명과 함께** 업로드해서
`gallery.html`(지식 갤러리)에서 과목 → 단원 → 개념지도 흐름을 확인한다.
모두 로컬·무료(Ollama)로 동작하며 OpenAI 키는 필요 없다.

---

## 0. 준비물 (처음 한 번만)

1) **Ollama 실행** + 임베딩 모델 받기 (터미널):
```
ollama pull nomic-embed-text
```
Ollama 앱이 켜져 있거나 `ollama serve` 가 돌고 있어야 한다. (업로드 시 임베딩에 사용)

2) **백엔드 패키지 설치** (venv 콘솔 스크립트가 깨져 있으니 `python -m pip` 사용):
```
cd ~/Desktop/LINKNOTE/study-rag-api
./venv/bin/python -m pip install fastapi "uvicorn[standard]" python-multipart
```

---

## 1. API 서버 켜기
```
cd ~/Desktop/LINKNOTE/study-rag-api
./venv/bin/python -m uvicorn api_server:app --reload --port 8000
```
(또는 `sh run_api.sh` — 같은 명령이다. venv 파이썬으로 직접 실행하는 게 가장 확실하다.)

- **정상 신호**: 터미널에 `Uvicorn running on http://127.0.0.1:8000` 가 떠야 한다.
  이 줄이 보여야 비로소 /docs 가 열린다.
- 확인: 브라우저에서 http://127.0.0.1:8000/docs (반드시 **http**, https 아님).
- 이 터미널 창은 **켜둔 채로** 둔다. 닫으면 서버도 꺼진다.

### /docs 가 안 열릴 때 — 순서대로 확인
1. **터미널에 `Uvicorn running...` 가 떴나?**
   - 안 떴고 빨간 `Traceback` 이 떴다면 서버가 시작에 실패한 것. 그 메시지가 원인이다.
2. **`ModuleNotFoundError: No module named '...'`** → 그 패키지가 venv에 없다. 설치:
   ```
   ./venv/bin/python -m pip install -r requirements.txt
   ```
3. **`Address already in use` (포트 8000 사용 중)** → 다른 포트로:
   ```
   ./venv/bin/python -m uvicorn api_server:app --reload --port 8001
   ```
   이때 `web/index.html`, `web/gallery.html` 안의 `127.0.0.1:8000` 도 `8001` 로 바꿔야 한다.
4. **그냥 멈춰 보이고 아무 줄도 안 뜬다** → 시작 중일 수 있다. 10초 기다렸다 새로고침.
5. **빠른 점검(서버 켠 채 다른 터미널에서)**:
   ```
   curl http://127.0.0.1:8000/timetable?user_id=yoojin
   ```
   JSON 이 돌아오면 서버는 정상이고, 문제는 브라우저 쪽(주소·https 등)이다.

> 참고: OpenAI 키가 없어도 서버는 이제 정상 기동된다(답변 생성만 키가 필요).
> 업로드·갤러리 조회는 Ollama만 있으면 된다.

## 2. 업로드 화면 열기
- 파인더에서 `study-rag-api/web/index.html` 더블클릭 (브라우저로 열림).
- 입력값 (예시 — 내 데이터에 맞게):
  - 사용자명: `yoojin`   ← 갤러리와 **반드시 동일**하게
  - 학기: `2026-1`
  - 과목: `병태생리학1`   (또는 `인체구조와기능2`)
  - 자료명: `비뇨기계 질환 기말범위`
  - **단원: `신부전`**   ← ★ 꼭 입력. 이게 갤러리의 "단원 카드"가 된다.
  - PDF 파일: `기말범위-비뇨기계 질환 .pdf` 선택
- **[PDF 업로드 & 학습]** 클릭 → "학습 완료 (페이지 N)" 가 뜨면 성공.
  (임베딩 때문에 수십 초 걸릴 수 있다. Ollama가 꺼져 있으면 실패한다.)

## 3. 갤러리에서 확인
- `study-rag-api/web/gallery.html` 더블클릭.
- 사용자명 `yoojin` 확인 → **불러오기** 클릭.
- `2026-1` 선택 → 과목 카드 `병태생리학1` 클릭 → 단원 카드 `신부전` 클릭 → 개념 지도.
  - 우상단 점이 초록(API 연결됨)이면 내 실제 데이터다.
  - 개념 지도는 아직 "개념 추출 전 · 예시" 뱃지가 붙는다(개념 추출은 다음 단계 C). 단원까지는 진짜 내 데이터.

---

## 참고

- **단원을 여러 개 보고 싶으면**: 지금은 "한 파일 = 한 단원" 구조다. 같은 PDF를
  단원명만 바꿔(예: `신부전`, `투석`, `만성신질환`) 여러 번 업로드하면 단원 카드가 여러 개 생긴다.
  (테스트용. 나중에 개념 추출이 들어가면 한 파일에서 자동 분리하는 방향으로 발전.)
- **단원을 안 적고 올리면**: 갤러리에서 "아직 단원이 없습니다" 빈 화면이 나온다. 다시 단원명 넣어 올리면 된다.
- **서버를 안 켜도**: gallery.html은 데모 데이터로 흐름만 보여준다(우상단 점이 빨강).
- 사용자명(`user_id`)이 업로드와 갤러리에서 다르면 자료가 안 보인다. 똑같이 맞출 것.
