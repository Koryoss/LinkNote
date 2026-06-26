#!/bin/sh

cd "$(dirname "$0")" || exit 1

# venv가 다른 위치에서 옮겨와 venv/bin/uvicorn 의 shebang이 깨졌을 수 있으므로,
# 깨진 콘솔 스크립트 대신 venv 파이썬으로 직접 uvicorn 모듈을 실행한다.
./venv/bin/python -m uvicorn api_server:app --reload --host 127.0.0.1 --port 8000
