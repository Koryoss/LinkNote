#!/bin/sh
# 모든 과목에 대해 개념 추출(/reindex-concepts)을 실행한다. (OpenAI 사용 — 비용 발생)
# 사용법:  cd ~/Desktop/LINKNOTE/study-rag-api && sh reindex_all.sh
# 서버가 켜져 있어야 함(먼저 sh check_all.sh 또는 sh run_api.sh).

cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python
BASE="http://127.0.0.1:8000"
USER_ID="정유진"

echo "라이브러리에서 과목 목록 읽는 중..."
$PY - "$BASE" "$USER_ID" <<'PYEOF'
import sys, json, urllib.parse, urllib.request
base, user = sys.argv[1], sys.argv[2]

def get(path):
    return json.load(urllib.request.urlopen(base + path))

def post(path, payload):
    req = urllib.request.Request(base + path,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    return json.load(urllib.request.urlopen(req))

lib = get('/library?user_id=' + urllib.parse.quote(user))
total_units = 0
total_concepts = 0
for sem in lib.get('semesters', []):
    s = sem['semester']
    for c in sem.get('courses', []):
        course = c['course']
        print(f"  - {s} / {course} ... ", end='', flush=True)
        try:
            r = post('/reindex-concepts', {'user_id': user, 'semester': s, 'course': course})
            print(f"단원 {r.get('units_indexed',0)} · 개념 {r.get('concepts_count',0)}")
            total_units += r.get('units_indexed', 0)
            total_concepts += r.get('concepts_count', 0)
        except Exception as e:
            print("실패:", e)
print(f"\n완료: 단원 {total_units}개 · 개념 {total_concepts}개 추출")
PYEOF
