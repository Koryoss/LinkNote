#!/bin/sh
# 더블클릭 한 번으로: 백엔드(필요시) 시작 → LinkNote 앱 열기
cd "$(dirname "$0")" || exit 1
PORT=8000
BASE="http://127.0.0.1:$PORT"

# 1) 백엔드가 안 떠 있으면 시작
if ! curl -s "$BASE/" >/dev/null 2>&1; then
  echo "백엔드 시작 중..."
  ./venv/bin/python -m uvicorn api_server:app --port $PORT > server.log 2>&1 &
  for i in $(seq 1 30); do
    curl -s "$BASE/" >/dev/null 2>&1 && break
    sleep 1
  done
fi

if curl -s "$BASE/" >/dev/null 2>&1; then
  echo "백엔드 OK"
else
  echo "⚠ 백엔드가 안 떴습니다. server.log 를 확인하세요."
fi

# 2) 앱 열기 (응용프로그램에 설치돼 있으면 그걸, 아니면 빌드 결과를)
open -a LinkNote 2>/dev/null || open "./desktop/src-tauri/target/release/bundle/macos/LinkNote.app"
