"""과목명 일괄 변경 (재임베딩 없이 ChromaDB 메타데이터만 수정).
사용:  ./venv/bin/python fix_course.py "간호학개론" "기본간호학"
"""
import sys
from rag import collection

USER = "정유진"


def main():
    if len(sys.argv) < 3:
        print('사용법: fix_course.py "기존과목" "새과목"'); return
    old, new = sys.argv[1], sys.argv[2]
    res = collection.get(where={"user_id": USER}, include=["metadatas"])
    ids, metas = [], []
    for cid, m in zip(res.get("ids", []), res.get("metadatas", [])):
        m = m or {}
        if (m.get("course") or "").strip() == old:
            nm = dict(m); nm["course"] = new
            if (nm.get("title") or "").strip() == old:
                nm["title"] = new
            ids.append(cid); metas.append(nm)
    if not ids:
        print(f"'{old}' 청크 없음."); return
    collection.update(ids=ids, metadatas=metas)
    print(f"'{old}' → '{new}': 청크 {len(ids)}개 변경 완료")
    print("이어서 ./venv/bin/python reindex_all.py 로 개념·그래프를 갱신하세요.")


if __name__ == "__main__":
    main()
