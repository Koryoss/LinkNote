"""material_registry.csv 읽기 헬퍼 (자료 정본)."""
import csv
import os

REG_CSV = os.getenv("REGISTRY_CSV", "material_registry.csv")


def _rows():
    if not os.path.exists(REG_CSV):
        return []
    with open(REG_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def unit_registered_at(semester, course):
    """(semester, course)의 단원별 최초 등록시각(added_at) 매핑."""
    out = {}
    for r in _rows():
        if r.get("semester") == semester and r.get("course") == course:
            u = (r.get("unit") or "").strip()
            a = (r.get("added_at") or "").strip()
            if not u:
                continue
            if u not in out or (a and a < out[u]):
                out[u] = a
    return out
