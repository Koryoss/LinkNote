import chromadb
import json
import os
import re
from providers.hybrid_provider import embed_text, generate_answer

chroma_client = chromadb.PersistentClient(path=os.getenv("CHROMA_PATH", "./chroma_db"))
DATA_DIR = os.getenv("DATA_DIR", "./data")
collection = chroma_client.get_or_create_collection(name="study_notes")

def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start += chunk_size - overlap
    return chunks

def add_pdf_pages_to_db(pages, filename: str, semester: str, course: str, title: str, user_id: str, unit: str = ""):
    ids, documents, metadatas, embeddings = [], [], [], []
    for page in pages:
        page_number = page["page"]
        chunks = chunk_text(page["text"])
        for chunk_index, chunk in enumerate(chunks):
            chunk_id = f"{user_id}-{semester}-{course}-{title}-{filename}-p{page_number}-c{chunk_index}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({
                "user_id": user_id, "semester": semester, "course": course, "title": title,
                "filename": filename, "page": page_number, "chunk_index": chunk_index, "unit": unit
            })
            embeddings.append(embed_text(chunk))
    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

def _normalize_existing_user_ids():
    results = collection.get(include=["metadatas"])
    update_ids, update_metadatas = [], []
    for chunk_id, metadata in zip(results.get("ids", []), results.get("metadatas", [])):
        metadata = metadata or {}
        if not metadata.get("user_id"):
            normalized_metadata = dict(metadata)
            normalized_metadata["user_id"] = "default"
            update_ids.append(chunk_id)
            update_metadatas.append(normalized_metadata)
    if update_ids:
        collection.update(ids=update_ids, metadatas=update_metadatas)

def _build_where_filter(search_filter=None, user_id=None):
    conditions = []
    if user_id:
        conditions.append({"user_id": user_id})
    for key in ["semester", "course", "filename"]:
        if value := (search_filter.get(key) if search_filter else None):
            conditions.append({key: value})
    if not conditions: return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}

def _extract_json_array(text: str):
    if not isinstance(text, str): return text
    start, end = text.find("["), text.rfind("]")
    return text[start:end + 1] if start != -1 and end > start else text

def _salvage_objects(text):
    objs = []
    for m in re.finditer(r"\{[^{}]*\}", text or ""):
        try: objs.append(json.loads(m.group(0)))
        except Exception: pass
    return objs

def rename_unit(user_id, semester, course, old_unit, new_unit):
    """ChromaDB 청크의 unit 메타데이터를 일괄 변경. 변경된 청크 수 반환."""
    where = _build_where_filter({"semester": semester, "course": course}, user_id=user_id)
    res = collection.get(where=where, include=["metadatas"])
    ids, metas = [], []
    for cid, m in zip(res.get("ids", []), res.get("metadatas", [])):
        m = m or {}
        if (m.get("unit") or "").strip() == old_unit:
            nm = dict(m); nm["unit"] = new_unit
            ids.append(cid); metas.append(nm)
    if ids:
        collection.update(ids=ids, metadatas=metas)
    return len(ids)


def _parse_concept_items(raw_text, chunks):
    parsed_raw = _extract_json_array((raw_text or "").strip())
    try:
        parsed = json.loads(parsed_raw)
        if not isinstance(parsed, list): parsed = _salvage_objects(raw_text)
    except Exception:
        parsed = _salvage_objects(raw_text)  # 토큰 잘림 등 → 객체 단위로 건짐
    out = []
    for item in parsed:
        if not (isinstance(item, dict) and (name := (item.get("name") or "").strip()) and (keyword := (item.get("keyword") or "").strip())): continue
        try:
            weight = max(1, min(5, int(item.get("importance") or item.get("weight"))))
        except (ValueError, TypeError):
            weight = 3
        match = next((c for c in chunks if keyword in c["text"]), None)
        page = match.get("page") if match else None
        fname = match.get("filename") if match else None
        links = [r.strip() for r in item.get("related", []) if isinstance(r, str) and r.strip()]
        out.append({"name": name, "keyword": keyword, "weight": weight, "page": page, "filename": fname, "links": links})
    return out


_MAP_PROMPT = (
    "다음은 한 단원 강의자료의 일부다. 이 조각의 핵심 개념을 최대 10개 뽑아라.\n"
    "- 반드시 한국어. 설명 금지. JSON 배열만 출력.\n"
    "- 각 항목: {\"name\":\"개념 전체 이름\",\"keyword\":\"가장 짧은 핵심 용어\",\"importance\":1~5,\"related\":[\"관련 keyword\", ...]}\n"
    "- keyword는 본문에서 찾을 수 있는 짧은 단어(예: \"신부전\",\"AKI\").\n"
    "자료:"
)


def _assign_groups(concepts, unit):
    """개념들을 3~6개 상위 분류로 묶어 group 필드를 부여 (다층 구조)."""
    if len(concepts) <= 3:
        for c in concepts: c["group"] = unit
        return concepts
    listing = ", ".join(c["keyword"] for c in concepts)
    prompt = (
        f"'{unit}' 단원의 아래 개념들을 3~6개의 상위 분류로 묶어라.\n"
        "- 각 개념에 가장 알맞은 상위 분류 이름을 붙인다(상위 분류는 짧은 한국어).\n"
        'JSON 배열만 출력: [{"keyword":"개념keyword","group":"상위분류"}, ...]\n'
        f"개념들: {listing}"
    )
    try:
        parsed = json.loads(_extract_json_array(generate_answer(prompt).strip()))
        gmap = {_norm_name(d.get("keyword","")): (d.get("group") or "").strip()
                for d in parsed if isinstance(d, dict)}
        for c in concepts:
            c["group"] = gmap.get(_norm_name(c["keyword"])) or unit
    except Exception:
        for c in concepts: c["group"] = unit
    return concepts


def build_concepts_for_unit(user_id, semester, course, unit, seg_size: int = 9000):
    where_filter = _build_where_filter({"semester": semester, "course": course}, user_id=user_id)
    if not where_filter: return []
    results = collection.get(where=where_filter, include=["metadatas", "documents"])
    chunks = [
        {"text": doc or "", "page": meta.get("page"), "filename": meta.get("filename")}
        for meta, doc in zip(results.get("metadatas", []), results.get("documents", []))
        if (meta.get("unit") or "").strip() == unit
    ]
    if not chunks: return []
    chunks.sort(key=lambda c: (c.get("page") or 0))

    # MAP: 단원 전체를 세그먼트로 나눠 빠짐없이 개념 추출
    segments, cur, cur_len = [], [], 0
    for c in chunks:
        cur.append(c); cur_len += len(c["text"])
        if cur_len >= seg_size:
            segments.append(cur); cur, cur_len = [], 0
    if cur: segments.append(cur)

    raw = []
    for seg in segments:
        text = "".join(c["text"] for c in seg)[:seg_size + 2000]
        try:
            raw += _parse_concept_items(generate_answer(_MAP_PROMPT + text, max_tokens=1600), seg)
        except Exception:
            continue
    if not raw:
        sampled = "".join(c["text"] for c in chunks)[:11000]
        raw = _parse_concept_items(generate_answer(_MAP_PROMPT + sampled, max_tokens=1600), chunks)
    if not raw: return []

    # REDUCE: keyword 기준 중복 병합
    by_kw = {}
    for c in raw:
        k = _norm_name(c["keyword"]) or _norm_name(c["name"])
        if not k: continue
        e = by_kw.get(k)
        if e:
            e["weight"] = max(e["weight"], c["weight"])
            e["links"] = list({*e["links"], *c["links"]})
            if e.get("page") is None: e["page"] = c.get("page")
            if e.get("filename") is None: e["filename"] = c.get("filename")
        else:
            by_kw[k] = dict(c)
    merged = list(by_kw.values())

    # 상위 그룹(다층) 부여 + 중요도 정렬
    merged = _assign_groups(merged, unit)
    merged.sort(key=lambda c: c["weight"], reverse=True)
    return merged

def _count_matching_chunks(where_filter=None):
    return collection.count() if not where_filter else len(collection.get(where=where_filter, include=[]).get("ids", []))

def delete_chunks_by_filter(search_filter, user_id: str):
    where_filter = _build_where_filter(search_filter, user_id=user_id)
    if not where_filter: return 0
    ids_to_delete = collection.get(where=where_filter, include=[]).get("ids", [])
    if not ids_to_delete: return 0
    collection.delete(ids=ids_to_delete)
    return len(ids_to_delete)

def reset_collection(user_id: str):
    where_filter = _build_where_filter(None, user_id=user_id)
    ids_to_delete = collection.get(where=where_filter, include=[]).get("ids", [])
    if not ids_to_delete: return 0
    collection.delete(ids=ids_to_delete)
    return len(ids_to_delete)

def search_relevant_chunks(question: str, n_results: int = 5, search_filter=None, user_id=None, rich: bool = False):
    where_filter = _build_where_filter(search_filter, user_id=user_id)
    if (doc_count := _count_matching_chunks(where_filter)) == 0: return []
    if rich:
        from retrieval import retrieve
        return retrieve(collection, question, where_filter, n=n_results)
    query_args = {"query_embeddings": [embed_text(question)], "n_results": min(n_results, doc_count), "include": ["documents", "metadatas", "distances"]}
    if where_filter: query_args["where"] = where_filter
    results = collection.query(**query_args)
    if not results.get("documents"): return []
    chunks = []
    for i, (id_val, doc, meta, dist) in enumerate(zip(results["ids"][0], results["documents"][0], results["metadatas"][0], results["distances"][0])):
        chunks.append({**meta, "id": id_val, "text": doc, "distance": dist})
    return chunks

def get_filter_label(search_filter=None):
    if not search_filter: return "전체 자료"
    parts = [v for k in ["semester", "course", "filename"] if (v := search_filter.get(k))]
    return " / ".join(parts) if parts else "전체 자료"

def get_library_overview(user_id: str):
    where_filter = _build_where_filter(None, user_id=user_id)
    total_chunks = _count_matching_chunks(where_filter)
    overview = {"total_chunks": total_chunks, "semesters": {}}
    if total_chunks == 0: return overview
    for meta in collection.get(where=where_filter, include=["metadatas"]).get("metadatas", []):
        semester, course = meta.get("semester", "학기 미지정"), meta.get("course", "과목 미지정")
        title, filename = meta.get("title", ""), meta.get("filename", "파일명 미지정")
        course_data = overview["semesters"].setdefault(semester, {}).setdefault(course, {})
        if filename not in course_data:
            course_data[filename] = {"title": title, "filename": filename}
        elif title and not course_data[filename].get("title"):
            course_data[filename]["title"] = title
    return overview

def get_units(user_id: str, semester: str, course: str):
    where_filter = _build_where_filter({"semester": semester, "course": course}, user_id=user_id)
    if not where_filter: return []
    units = {}
    for meta in collection.get(where=where_filter, include=["metadatas"]).get("metadatas", []):
        if not (unit_name := (meta.get("unit") or "").strip()): continue
        if unit_name not in units: units[unit_name] = {"files": set(), "pages": set()}
        if filename := meta.get("filename"): units[unit_name]["files"].add(filename)
        if page := meta.get("page"): units[unit_name]["pages"].add(page)
    return [{"unit": name, "file_count": len(info["files"]), "page_count": len(info["pages"])} for name, info in sorted(units.items())]

def get_chunks(user_id, limit=50, offset=0, search_filter=None, full=False):
    where_filter = _build_where_filter(search_filter, user_id=user_id)
    results = collection.get(where=where_filter, include=["metadatas", "documents"])
    items = [{**meta, "id": id_val, "text": doc if full else doc[:200]} for id_val, meta, doc in zip(results["ids"], results["metadatas"], results["documents"])]
    items.sort(key=lambda x: (x.get("filename", ""), x.get("page", 0), x.get("chunk_index", 0)))
    return {"total": len(items), "limit": limit, "offset": offset, "items": items[offset:offset + limit]}

def _chunk_key(chunk):
    return (chunk.get("filename", ""), chunk.get("page", ""), chunk.get("chunk_index", ""))

def _has_different_source(candidate, direct_chunks):
    return any(candidate.get("course") != dc.get("course") for dc in direct_chunks)

def _format_context(chunks):
    return "\n\n".join(
        f"[과목:{c.get('course','')} · 단원:{c.get('unit','')} · 파일:{c.get('filename','')} p.{c.get('page','')}]\n{c.get('text','')}"
        for c in chunks
    )

def _extract_connection_concepts(question, direct_chunks):
    prompt = f"""너는 강의자료에서 연결 검색에 쓸 핵심 개념만 뽑는 도우미다. 규칙: 반드시 한국어로만, 쉼표로 구분한 3~6개 키워드만 출력한다.
자료:
{_format_context(direct_chunks[:2])}
질문: {question}"""
    return generate_answer(prompt).strip()

def _extract_sources(chunks):
    return list({json.dumps(c, sort_keys=True): c for c in [{"semester": c.get("semester"), "course": c.get("course"), "title": c.get("title"), "filename": c.get("filename"), "page": c.get("page")} for c in chunks]}.values())

def answer_question(question: str, search_filter=None, search_scope_label=None, user_id=None):
    chunks = search_relevant_chunks(question, n_results=8, search_filter=search_filter, user_id=user_id, rich=True)
    if not chunks: return "선택한 검색 범위에서 관련 자료를 찾지 못했습니다.", []
    prompt = f"""너는 업로드된 강의자료만 근거로 답하는 학습 도우미다. [규칙] 자료 내용만으로, 한국어로, 지어내지 말고, 인용은 (과목명 - 단원명 - 파일명 p.쪽수) 형식으로 표기하며 답한다.
[답변 형식] 핵심 답변: 2~4문장 요약. 자세히: 풀어 설명. 근거: (과목명 - 단원명 - 파일명 p.쪽수)
[자료]\n{_format_context(chunks)}
[검색 범위] {search_scope_label or get_filter_label(search_filter)}
[질문] {question}"""
    return generate_answer(prompt), _extract_sources(chunks)

def _cosine(a, b):
    s = na = nb = 0.0
    for x, y in zip(a, b): s += x * y; na += x * x; nb += y * y
    return -1.0 if na == 0 or nb == 0 else s / ((na ** 0.5) * (nb ** 0.5))

def _graph_connected_chunks(question, user_id, n_seed=3, max_targets=4):
    idx_path, lnk_path = os.path.join(DATA_DIR, "concept_index.json"), os.path.join(DATA_DIR, "concept_links.json")
    if not (os.path.exists(idx_path) and os.path.exists(lnk_path)): return []
    try: index, links = json.load(open(idx_path, encoding="utf-8")), json.load(open(lnk_path, encoding="utf-8"))
    except Exception: return []
    nodes = [n for n in index if n.get("user_id") == user_id and n.get("embedding")]
    if not nodes: return []
    try: qv = embed_text(question)
    except Exception: return []
    seeds = sorted(nodes, key=lambda n: _cosine(qv, n["embedding"]), reverse=True)[:n_seed]
    seed_ids, by_id = {n["id"] for n in seeds}, {n["id"]: n for n in nodes}
    edges = [e for e in (links.get("edges", []) if isinstance(links, dict) else []) if e.get("a") and e.get("b")]
    targets, seen_ids = [], set()
    for e in edges:
        for src, dst in ((e["a"], e["b"]), (e["b"], e["a"])):
            if src in seed_ids and dst in by_id and dst not in seed_ids and dst not in seen_ids:
                if (src_node := by_id.get(src)) and (tgt := by_id.get(dst)) and tgt.get("course") != src_node.get("course"):
                    seen_ids.add(dst); targets.append((e.get("score", 0), tgt))
    targets.sort(key=lambda t: t[0], reverse=True)
    return [res[0] for _, tgt in targets[:max_targets] if (res := search_relevant_chunks(tgt.get("keyword") or tgt.get("name"), 1, {"course": tgt.get("course")}, user_id))]

def answer_with_connections(question: str, search_filter=None, search_scope_label=None, user_id=None):
    direct_chunks = search_relevant_chunks(question, 5, search_filter, user_id, rich=True)
    if not direct_chunks: return "선택한 검색 범위에서 직접 관련 자료를 찾지 못했습니다.", []
    connection_concepts = _extract_connection_concepts(question, direct_chunks)
    connection_query = "\n\n".join([question, connection_concepts] + [c["text"][:350] for c in direct_chunks[:2]])
    candidate_pool = search_relevant_chunks(connection_query, 12, None, user_id)
    direct_keys, seen_keys = {_chunk_key(c) for c in direct_chunks}, set()
    preferred, fallback = [], []
    for cand in candidate_pool:
        key = _chunk_key(cand)
        if key in direct_keys or key in seen_keys: continue
        seen_keys.add(key)
        (preferred if _has_different_source(cand, direct_chunks) else fallback).append(cand)
    connection_chunks = (preferred + fallback)[:4]
    graph_chunks = _graph_connected_chunks(question, user_id)
    seen_keys.update(_chunk_key(c) for c in direct_chunks)
    seen_keys.update(_chunk_key(c) for c in connection_chunks)
    merged_connections = connection_chunks + [c for c in graph_chunks if _chunk_key(c) not in seen_keys]
    final_connections = merged_connections[:6]
    connection_context = _format_context(final_connections) or "다른 PDF, 과목, 학기에서 뚜렷하게 연결되는 후보 자료를 찾지 못했습니다."
    prompt = f"""너는 여러 강의자료를 "연결"해 설명하는 학습 도우미다. [목표] 질문에 직접 답하고, 다른 과목/파일에서 연결되는 내용을 찾아 설명한다. [규칙] 제공된 [자료]만 근거. 없으면 "자료에서 확인되지 않음". 연결 약하면 "뚜렷한 교차 연결 약함". 한국어로, 인용은 (과목명 - 단원명 - 파일명 p.쪽수) 형식으로 표기.
[답변 형식] 1) 직접 답: 핵심 답. 2) 연결: 다른 자료와 어떻게 이어지는지. 3) 함께 볼 개념: 키워드 목록.
[직접 자료]\n{_format_context(direct_chunks)}
[연결 후보 자료]\n{connection_context}
[질문] {question}"""
    return generate_answer(prompt), _extract_sources(direct_chunks + final_connections)

def _cosine_similarity(vec_a, vec_b):
    if not vec_a or not vec_b or len(vec_a) != len(vec_b): return 0.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a, norm_b = sum(a**2 for a in vec_a)**0.5, sum(b**2 for b in vec_b)**0.5
    return 0.0 if norm_a == 0 or norm_b == 0 else dot_product / (norm_a * norm_b)

def build_concept_embeddings(user_id: str):
    concepts_path = os.path.join(DATA_DIR, "concepts.json")
    if not os.path.exists(concepts_path): return []
    with open(concepts_path, "r", encoding="utf-8") as f: concepts_data = json.load(f)
    if user_id not in concepts_data: return []
    embeddings_index = []
    for s_name, s_data in concepts_data[user_id].items():
        for c_name, c_data in s_data.items():
            for u_name, concepts in c_data.items():
                if not isinstance(concepts, list): continue
                for concept in concepts:
                    if not (isinstance(concept, dict) and (name := concept.get("name", ""))): continue
                    keyword = concept.get("keyword", "") or name
                    try:
                        if not (embedding := embed_text(f"{keyword} {name}".strip())): continue
                    except Exception: continue
                    embeddings_index.append({
                        "id": f"{user_id}::{s_name}::{c_name}::{u_name}::{name}", "user_id": user_id,
                        "semester": s_name, "course": c_name, "unit": u_name, "name": name, "keyword": keyword,
                        "weight": concept.get("weight", 1), "embedding": embedding
                    })
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f: json.dump(embeddings_index, f, ensure_ascii=False, indent=2)
    return embeddings_index

def _norm_name(x):
    return "".join(ch for ch in str(x).lower() if ch.isalnum())

def _verify_and_get_reason_with_llm(concept_a, concept_b):
    prompt = f"""아래 두 개념이 서로 다른 학문적 관점에서 깊은 연관성이 있는지 판별하고, 만약 있다면 그 이유를 한 문장으로 설명해줘.
- 개념 A ({concept_a['course']}): {concept_a['name']} (키워드: {concept_a['keyword']})
- 개념 B ({concept_b['course']}): {concept_b['name']} (키워드: {concept_b['keyword']})
[규칙] 1. 관련 있다면 'YES | 이유' 형식으로 답변. 2. 관련 없다면 'NO'만 답변."""
    try:
        response = generate_answer(prompt).strip()
        if response.upper().startswith("YES"):
            return True, response.split("|", 1)[-1].strip()
        return False, None
    except Exception:
        return False, None

def build_cross_links(user_id: str, threshold: float = 0.40, top_k: int = 8, llm_verify_range: tuple = (0.45, 0.85)):
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    if not os.path.exists(index_path): return []
    with open(index_path, "r", encoding="utf-8") as f: embeddings_index = json.load(f)
    user_embeddings = [e for e in embeddings_index if e.get("user_id") == user_id and e.get("embedding")]
    cross_edges, seen_pairs = [], set()
    for concept_a in user_embeddings:
        similarities = []
        for concept_b in user_embeddings:
            if concept_a["id"] == concept_b["id"] or concept_a["course"] == concept_b["course"]: continue
            score = _cosine_similarity(concept_a["embedding"], concept_b["embedding"])
            if score >= threshold and not (score >= 0.92 or _norm_name(concept_a["name"]) == _norm_name(concept_b["name"])):
                adjusted_score = score * (1 + 0.1 * ((concept_a.get("weight", 1) + concept_b.get("weight", 1)) / 10))
                similarities.append({"concept_b": concept_b, "score": score, "adjusted_score": adjusted_score})
        similarities.sort(key=lambda x: x["adjusted_score"], reverse=True)
        for sim in similarities[:top_k]:
            concept_b, score, adjusted_score = sim["concept_b"], sim["score"], sim["adjusted_score"]
            pair_key = tuple(sorted([concept_a["id"], concept_b["id"]]))
            if pair_key in seen_pairs: continue
            is_related, reason = False, None
            verify_min, verify_max = llm_verify_range
            if verify_min <= score < verify_max:
                is_related, reason = _verify_and_get_reason_with_llm(concept_a, concept_b)
            elif score >= verify_max:
                is_related = True
            if is_related:
                seen_pairs.add(pair_key)
                cross_edges.append({
                    "a": concept_a["id"], "b": concept_b["id"], "score": adjusted_score,
                    "type": "cross", "reason": reason or "High similarity score"
                })
    links_path = os.path.join(DATA_DIR, "concept_links.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(links_path, "w", encoding="utf-8") as f:
        json.dump({"user_id": user_id, "edges": cross_edges}, f, ensure_ascii=False, indent=2)
    return cross_edges
