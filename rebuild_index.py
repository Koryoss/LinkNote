"""ChromaDB 재인덱싱 복구 스크립트.
- recovery_mapping.csv (filename,semester,course,unit,title) 를 읽어
- data/uploads 의 원본 PDF를 다시 추출·임베딩하여 ChromaDB에 채운다.
- 임베딩 제공자는 서버와 동일하게 .env 의 EMBED_PROVIDER 를 따른다.
사용법(서버 끈 상태에서):
  ./venv/bin/python rebuild_index.py            # 실제 실행
  ./venv/bin/python rebuild_index.py --dry-run  # 매핑만 점검(임베딩 X)
"""
import csv
import glob
import os
import re
import sys
import unicodedata

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DATA_USER_ID = "정유진"  # users.json 의 data_user_id
UPLOADS = os.path.join(os.getenv("DATA_DIR", "./data"), "uploads")
MAP_CSV = "material_registry.csv" if os.path.exists("material_registry.csv") else "recovery_mapping.csv"
DRY = "--dry-run" in sys.argv


def nfc(s):
    return unicodedata.normalize("NFC", s)


def norm_semester(s):
    """엑셀이 '2026-1'을 날짜(2026.1.1, 2026-01-01 등)로 바꿔 저장해도 되돌린다."""
    s = (s or "").strip()
    if "여름" in s:
        return "2026-여름"
    if s.startswith("2026"):
        return "2026-1"
    return s


def load_map():
    m = {}
    with open(MAP_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            m[nfc(row["filename"].strip())] = row
    return m


def find_path(filename):
    """매핑의 (uuid 제거) 파일명으로 실제 업로드 파일 경로 찾기."""
    target = nfc(filename)
    for p in glob.glob(os.path.join(UPLOADS, "*.pdf")):
        base = nfc(re.sub(r"^[0-9a-f]{32}_", "", os.path.basename(p)))
        if base == target:
            return p
    return None


def main():
    mapping = load_map()
    print(f"매핑 {len(mapping)}개, EMBED_PROVIDER={os.getenv('EMBED_PROVIDER','ollama')}, dry_run={DRY}")

    # 과목명 미확인(?)만 게이트 — 단원의 ?는 검색에 영향 없어 통과시킴
    unsure = [fn for fn, r in mapping.items() if "?" in r["course"]]
    if unsure:
        print(f"\n[주의] 과목명 확인 필요(? 포함) {len(unsure)}개 — CSV에서 먼저 채우는 걸 권장:")
        for fn in unsure:
            print("   -", fn, "=>", mapping[fn]["course"], "/", mapping[fn].get("unit"))
        if not DRY:
            ans = input("\n그래도 진행할까요? (y/N) ").strip().lower()
            if ans != "y":
                print("중단."); return

    if DRY:
        miss = [fn for fn in mapping if not find_path(fn)]
        print(f"\n[dry-run] 원본 못 찾은 파일 {len(miss)}개:", miss[:10])
        print("dry-run 종료 (임베딩/저장 안 함).")
        return

    from pdf_loader import extract_pdf_text
    from rag import add_pdf_pages_to_db, collection

    # 기존(비어있는) 컬렉션 정리: 이 사용자 청크만 삭제 후 재적재
    try:
        collection.delete(where={"user_id": DATA_USER_ID})
    except Exception as e:
        print("기존 삭제 건너뜀:", e)

    ok = fail = 0
    for i, (filename, row) in enumerate(mapping.items(), 1):
        path = find_path(filename)
        if not path:
            print(f"  [{i}/{len(mapping)}] 원본 없음: {filename}"); fail += 1; continue
        pages = extract_pdf_text(path)
        if not pages:
            print(f"  [{i}/{len(mapping)}] 텍스트 추출 실패: {filename}"); fail += 1; continue
        add_pdf_pages_to_db(
            pages=pages,
            filename=filename,
            semester=norm_semester(row["semester"]),
            course=row["course"].strip(),
            title=row["title"].strip(),
            user_id=DATA_USER_ID,
            unit=row.get("unit", "").strip(),
        )
        ok += 1
        print(f"  [{i}/{len(mapping)}] OK  {row['course']} | {filename} ({len(pages)}p)")

    print(f"\n완료: 성공 {ok}, 실패 {fail}")
    print("총 청크:", collection.count())


if __name__ == "__main__":
    main()
