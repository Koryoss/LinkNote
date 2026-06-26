# LinkNote 웹 배포 (Render) — C4 단계

준비된 것(C1~C3): 임베딩 OpenAI 전환, 토큰 기반 보안, 영구경로 env화, `render.yaml`.
아래는 **당신이 하는 단계**입니다. (비용: Render starter ≈ $7/월 + OpenAI 임베딩 소액)

---

## 1. GitHub에 코드 올리기
`study-rag-api` 폴더를 git 저장소로 올립니다. (이 폴더가 저장소 루트가 되게)
```
cd ~/Desktop/LINKNOTE/study-rag-api
git init
git add .
git commit -m "LinkNote web deploy"
```
→ GitHub에서 새 repo 만들고(예: `linknote`), 안내대로 push.
> `.gitignore`가 `.env`, `data/`, `chroma_db/`, `venv/`, 데스크톱 빌드물을 제외하므로 비밀키·로컬데이터는 안 올라갑니다. (안전)

## 2. Render에서 서비스 생성
1. https://render.com 가입(또는 로그인) → GitHub 연결.
2. **New + → Blueprint** 선택 → 방금 올린 repo 선택 → `render.yaml` 자동 인식 → Apply.
   - (Blueprint가 안 보이면 **New + → Web Service** 수동 생성:
     Build `pip install -r requirements.txt`, Start `uvicorn api_server:app --host 0.0.0.0 --port $PORT`,
     Plan **Starter**, Disk 추가: mount `/var/data`, 1GB,
     환경변수 아래 3번대로.)

## 3. 환경변수 입력 (Render 대시보드 → Environment)
- `OPENAI_API_KEY` = 본인 OpenAI 키
- `GOOGLE_CLIENT_ID` = 본인 구글 클라이언트 ID (`...apps.googleusercontent.com`)
- (render.yaml로 만들면 `EMBED_PROVIDER`, `DATA_DIR`, `CHROMA_PATH`, `AUTH_SECRET`는 자동 설정됨)

## 4. 배포 → URL 확인
- 빌드가 끝나면 주소가 생겨요: 예 `https://linknote.onrender.com`
- 그 주소를 열면 로그인 화면이 떠야 합니다.

## 5. Google 로그인 origin 추가
- Google Cloud → 사용자 인증 정보 → OAuth 클라이언트 → **승인된 JavaScript 원본**에
  `https://linknote.onrender.com` (본인 실제 주소) 추가 → 저장.

## 6. 첫 사용 (데이터는 웹에서 새로)
⚠️ 웹 서버의 데이터는 **빈 상태로 시작**해요. 로컬 자료(Ollama 임베딩)는 임베딩 방식이 달라 그대로 못 옮겨요.
1. 웹 화면에서 **회원가입**(같은 학교 이메일 권장).
2. **＋ 자료 추가**로 PDF 업로드 → OpenAI 임베딩으로 다시 학습됨.
3. 필요하면 개념 추출: 로컬에서 했던 것처럼 웹에서도 `/reindex-concepts` 호출(이제 토큰 필요 → 로그인 상태로 브라우저에서 단원 열면 자동 안내).

---

### 참고/주의
- **첫 빌드는 몇 분** 걸려요(chromadb 설치).
- Starter 플랜이어야 **디스크(영구저장)** 가 돼요. 무료 플랜은 디스크 없어서 재시작 시 데이터 사라짐.
- 웹 계정과 로컬 데스크톱 계정/데이터는 **분리**돼요(서버가 다름). 둘 다 쓰려면 각각 자료를 넣어야 함.
- Google 로그인은 웹에서 작동(데스크톱 앱 웹뷰에선 막힘 — 정상).
