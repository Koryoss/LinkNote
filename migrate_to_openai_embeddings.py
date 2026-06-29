"""로컬 ChromaDB의 모든 청크를 OpenAI 임베딩으로 다시 임베딩한다.

왜 필요한가:
- 로컬 기본 임베딩(Ollama/nomic)은 한국어 분별력이 약해 검색이 자주 엉뚱해진다.
- OpenAI 임베딩(text-embedding-3-small)으로 바꾸면 정확도가 크게 오른다.
- 단, 임베딩 차원이 달라지므로(768 → 1536) 기존 컬렉션을 다시 만들어야 한다.
  이 스크립트는 PDF를 다시 올릴 필요 없이, 이미 저장된 청크 본문을 그대로 재임베딩한다.

주의:
- 청크 수만큼 OpenAI 임베딩을 호출하므로 시간·비용(소액)이 든다.
- 실행 전 .env 에 OPENAI_API_KEY 가 있어야 한다.
- 이 스크립트는 기존 컬렉션을 지우고 새로 만든다(임베딩만 교체, 본문/메타데이터는 그대로).

실행 방법:
    cd ~/Desktop/LINKNOTE/study-rag-api
    ./venv/bin/python migrate_to_openai_embeddings.py
실행 후:
    1) .env 에  EMBED_PROVIDER=openai  추가  (앞으로 검색도 OpenAI 임베딩 사용)
    2) 서버 재시작
    3) (선택) 개념 그래프도 OpenAI로 다시:
       ./venv/bin/python -c "import rag; rag.build_concept_embeddings('정유진'); rag.build_cross_links('정유진')"
"""
import os
import chromadb

from providers.openai_provider import embed_text as openai_embed

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION = "study_notes"


def main():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    col = client.get_or_create_collection(name=COLLECTION)

    data = col.get(include=["documents", "metadatas"])
    ids = data.get("ids", [])
    docs = data.get("documents", []) or []
    metas = data.get("metadatas", []) or []
    total = len(ids)

    if total == 0:
        print("청크가 없습니다. 마이그레이션할 데이터가 없어요.")
        return

    print(f"총 {total}개 청크를 OpenAI 임베딩으로 다시 임베딩합니다...")
    new_emb = []
    for i, doc in enumerate(docs):
        new_emb.append(openai_embed(doc or " "))
        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"  {i + 1}/{total}")

    # 차원이 바뀌므로 컬렉션을 새로 만든다(본문/메타는 그대로, 임베딩만 교체)
    print("컬렉션 재생성 중...")
    client.delete_collection(COLLECTION)
    col2 = client.create_collection(name=COLLECTION)

    B = 100
    for i in range(0, total, B):
        col2.add(
            ids=ids[i:i + B],
            documents=docs[i:i + B],
            metadatas=metas[i:i + B],
            embeddings=new_emb[i:i + B],
        )

    print("완료! 이제 .env 에 EMBED_PROVIDER=openai 를 추가하고 서버를 재시작하세요.")


if __name__ == "__main__":
    main()
