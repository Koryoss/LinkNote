# study-rag-api FastAPI 서버 실행

새로 분리된 FastAPI 서버를 실행하려면:

```bash
cd study-rag-api
uvicorn api_server:app --reload --port 8000
```

이제 웹/데스크톱 앱은 이 API 서버를 통해 `rag.py`와 ChromaDB를 공유할 수 있습니다.
