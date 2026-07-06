"""회상(recall) 저장 — SCiyl 로드맵 Phase 1 (AI 채점 없음, 로컬 JSON)."""
import json
import os
import time

DATA_DIR = os.getenv("DATA_DIR", "./data")
RECALL_PATH = os.path.join(DATA_DIR, "recall.json")


def _load():
    if not os.path.exists(RECALL_PATH):
        return []
    try:
        with open(RECALL_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RECALL_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_recall(user_id, semester, course, unit, concept, answer_text):
    data = _load()
    rec = {
        "user_id": user_id, "semester": semester, "course": course,
        "unit": unit, "concept": concept, "answer_text": answer_text,
        "created_at": int(time.time()),
    }
    data.append(rec)
    _save(data)
    return rec


def get_recalls(user_id, semester=None, course=None, unit=None):
    return [
        r for r in _load()
        if r.get("user_id") == user_id
        and (semester is None or r.get("semester") == semester)
        and (course is None or r.get("course") == course)
        and (unit is None or r.get("unit") == unit)
    ]


def stats(user_id, semester, course, unit):
    """단원 내 개념별 {count, last} — 노드 배지·정렬용."""
    s = {}
    for r in get_recalls(user_id, semester, course, unit):
        c = r.get("concept")
        e = s.setdefault(c, {"count": 0, "last": 0})
        e["count"] += 1
        e["last"] = max(e["last"], r.get("created_at", 0))
    return s
