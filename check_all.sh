#!/bin/sh
# LinkNote API: .env 점검 → 서버 재시작 → 기동 대기 → 엔드포인트 확인 → OpenAI 개념추출 테스트
# 사용법:  cd ~/Desktop/LINKNOTE/study-rag-api && sh check_all.sh

cd "$(dirname "$0")" || exit 1
PORT=8000
BASE="http://127.0.0.1:$PORT"
PY=./venv/bin/python
USER_ID="정유진"
SEM="2026-1"
COURSE="병태생리학 1"

echo "================ 1) .env / OPENAI_API_KEY 점검 ================"
$PY - <<'PYEOF'
from pathlib import Path
key=None
p=Path('.env')
if p.exists():
    for line in p.read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if line.startswith('OPENAI_API_KEY='):
            key=line.split('=',1)[1].strip().strip('"').strip("'")
if not key:
    print("  [X] OPENAI_API_KEY 없음 -> .env 에 추가 필요")
elif not key.startswith('sk-'):
    print(f"  [!] 진짜 키가 아님(placeholder/형식이상, 시작='{key[:8]}'). sk-... 키로 교체 필요")
else:
    print(f"  [OK] 키 있음 (sk-...{key[-4:]}, 길이 {len(key)})")
PYEOF

echo
echo "================ 2) 기존 서버 종료 후 재시작 ================"
PIDS=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$PIDS" ]; then kill $PIDS 2>/dev/null; echo "  기존 서버 종료: $PIDS"; sleep 1; fi
nohup $PY -m uvicorn api_server:app --port $PORT > server.log 2>&1 &
echo "  서버 백그라운드 시작 (로그: server.log)"

echo
echo "================ 3) 서버 기동 대기 ================"
OK=0
for i in $(seq 1 30); do
  if curl -s "$BASE/docs" >/dev/null 2>&1; then OK=1; break; fi
  sleep 1
done
if [ "$OK" != "1" ]; then
  echo "  [X] 서버가 안 떴습니다. server.log 마지막 줄:"
  tail -n 20 server.log
  exit 1
fi
echo "  [OK] 서버 응답함: $BASE  (문서: $BASE/docs)"

echo
echo "================ 4) 엔드포인트 확인 ================"
echo "  /timetable:"
curl -s "$BASE/timetable?user_id=$USER_ID" | head -c 300; echo
echo "  /library:"
curl -s "$BASE/library?user_id=$USER_ID" | $PY -c "import sys,json
try:
    d=json.load(sys.stdin)
    print('   total_chunks =', d.get('total_chunks'), '| 학기수 =', len(d.get('semesters',[])))
    for s in d.get('semesters',[]):
        print('   -', s.get('semester'), ':', ', '.join(c.get('course','') for c in s.get('courses',[])))
except Exception as e:
    print('   (파싱 실패)', e)"

echo
echo "================ 5) 개념 추출 테스트 (OpenAI 사용) ================"
echo "  대상: $USER_ID / $SEM / $COURSE"
RESP=$(curl -s -X POST "$BASE/reindex-concepts" -H "Content-Type: application/json" \
  -d "{\"user_id\":\"$USER_ID\",\"semester\":\"$SEM\",\"course\":\"$COURSE\"}")
echo "  응답: $RESP"
echo "$RESP" | grep -q '"ok": *true' \
  && echo "  [OK] OpenAI 개념 추출 성공! 갤러리에서 해당 단원 개념지도 확인하세요." \
  || echo "  [!] 실패 -> 위 응답 메시지 확인 (보통 키 없음/결제 미설정/quota)."

echo
echo "================ 완료 ================"
echo "서버는 계속 켜져 있습니다. 끄려면:  lsof -ti tcp:$PORT | xargs kill"
