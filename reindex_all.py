"""전체 개념 지도 + 과목 간 연결 그래프 재생성 (서버/토큰 불필요).
ChromaDB의 모든 (학기·과목·단원)에 대해:
  1) 개념 추출 → concepts.json
  2) 개념 임베딩 → concept_index.json
  3) 과목 간 교차연결 → concept_links.json
사용법(서버 켜둔 채로 가능):
  ./venv/bin/python reindex_all.py
"""
import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from rag import (
    collection,
    build_concepts_for_unit,
    build_concept_embeddings,
    build_cross_links,
    DATA_DIR,
)

USER = "정유진"  # users.json 의 data_user_id


def main():
    res = collection.get(include=["metadatas"])
    units = {}
    for m in res.get("metadatas", []):
        m = m or {}
        if m.get("user_id") != USER:
            continue
        sem, course, unit = m.get("semester", ""), m.get("course", ""), (m.get("unit", "") or "").strip()
        if unit:
            units[(sem, course, unit)] = True

    print(f"단원 {len(units)}개 → 개념 추출 시작 (OpenAI 사용)")
    path = os.path.join(DATA_DIR, "concepts.json")
    data = {USER: {}}
    total = 0
    for sem, course, unit in sorted(units):
        cs = build_concepts_for_unit(USER, sem, course, unit)
        data[USER].setdefault(sem, {}).setdefault(course, {})[unit] = cs
        total += len(cs)
        print(f"  {sem}/{course}/{unit}: {len(cs)}개")
        # 단원마다 저장 → 중간에 끊겨도 진행분 보존
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nconcepts.json 저장 완료 (개념 {total}개). 임베딩·그래프 생성 중…")

    build_concept_embeddings(USER)
    links = build_cross_links(USER)
    n = len(links.get("edges", [])) if isinstance(links, dict) else (links or 0)
    print(f"완료 ✅  개념 {total}개 · 교차연결 {n}개")


if __name__ == "__main__":
    main()
