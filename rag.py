import chromadb
import json
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from providers.hybrid_provider import embed_text, embed_texts, generate_answer

chroma_client = chromadb.PersistentClient(path=os.getenv("CHROMA_PATH", "./chroma_db"))
DATA_DIR = os.getenv("DATA_DIR", "./data")
collection = chroma_client.get_or_create_collection(name="study_notes")


def _concept_worker_count() -> int:
    try:
        return max(1, min(int(os.getenv("CONCEPT_MAX_WORKERS", "3")), 3))
    except ValueError:
        return 3


def _parallel_ordered(items, worker):
    values = list(items)
    if len(values) <= 1 or _concept_worker_count() == 1:
        return [worker(item) for item in values]
    with ThreadPoolExecutor(max_workers=min(_concept_worker_count(), len(values))) as executor:
        return list(executor.map(worker, values))

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

def add_pdf_pages_to_db(
    pages,
    filename: str,
    semester: str,
    course: str,
    title: str,
    user_id: str,
    unit: str = "",
    stored_filename: str = "",
):
    ids, documents, metadatas = [], [], []
    for page in pages:
        page_number = page["page"]
        chunks = chunk_text(page["text"])
        for chunk_index, chunk in enumerate(chunks):
            chunk_id = f"{user_id}-{semester}-{course}-{title}-{filename}-p{page_number}-c{chunk_index}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadata = {
                "user_id": user_id, "semester": semester, "course": course, "title": title,
                "filename": filename, "page": page_number, "chunk_index": chunk_index, "unit": unit
            }
            if stored_filename:
                metadata["stored_filename"] = stored_filename
            for key in (
                "text_extraction_method", "text_quality_reason", "text_quality_score",
                "text_korean_ratio", "text_noise_ratio",
            ):
                if page.get(key) is not None:
                    metadata[key] = page[key]
            metadatas.append(metadata)
    if ids:
        embeddings = embed_texts(documents)
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
    for key in ["semester", "course", "unit", "filename"]:
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


def _concept_occurrences_from_chunks(keyword, chunks):
    seen = set()
    out = []
    needle = str(keyword or "").strip()
    if not needle:
        return out
    normalized_needle = unicodedata.normalize("NFKC", needle).casefold()
    for c in chunks:
        source_text = unicodedata.normalize("NFKC", str(c.get("text") or "")).casefold()
        if normalized_needle not in source_text:
            continue
        page = c.get("page")
        filename = c.get("filename")
        key = (filename or "", page)
        if key in seen:
            continue
        seen.add(key)
        out.append({"filename": filename, "page": page})
    out.sort(key=lambda x: (int(x.get("page") or 0), str(x.get("filename") or "")))
    return out


def _concept_occurrences_for_terms_from_chunks(terms, chunks):
    """Return every unique source page that contains any explicit concept term."""
    merged = {}
    for term in terms:
        normalized = _norm_name(term)
        if len(normalized) < 2:
            continue
        for occurrence in _concept_occurrences_from_chunks(term, chunks):
            key = (occurrence.get("filename") or "", occurrence.get("page"))
            merged.setdefault(key, occurrence)
    return sorted(
        merged.values(),
        key=lambda item: (str(item.get("filename") or ""), int(item.get("page") or 0)),
    )


def concept_source_chunks_for_unit(user_id, semester, course, unit):
    """Load a unit's source text once for deterministic occurrence backfilling."""
    where_filter = _build_where_filter({"semester": semester, "course": course}, user_id=user_id)
    if not where_filter:
        return []
    results = collection.get(where=where_filter, include=["metadatas", "documents"])
    return [
        {"text": document or "", "page": metadata.get("page"), "filename": metadata.get("filename")}
        for metadata, document in zip(results.get("metadatas", []), results.get("documents", []))
        if (metadata.get("unit") or "").strip() == unit
    ]


def concept_occurrences_for_unit(user_id, semester, course, unit, keyword):
    chunks = concept_source_chunks_for_unit(user_id, semester, course, unit)
    return _concept_occurrences_from_chunks(keyword, chunks)


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
        occurrences = _concept_occurrences_from_chunks(keyword, chunks)
        match = occurrences[0] if occurrences else None
        page = match.get("page") if match else None
        fname = match.get("filename") if match else None
        links = [r.strip() for r in item.get("related", []) if isinstance(r, str) and r.strip()]
        aliases = []
        for key in ("aliases", "synonyms", "alias"):
            value = item.get(key)
            values = value if isinstance(value, list) else re.split(r"[,;/]", value) if isinstance(value, str) else []
            for alias in values:
                normalized = str(alias or "").strip()
                if normalized and normalized not in aliases and normalized not in {name, keyword}:
                    aliases.append(normalized)
        raw_evidence = item.get("evidence")
        evidence_items = raw_evidence if isinstance(raw_evidence, list) else [raw_evidence] if isinstance(raw_evidence, dict) else []
        model_evidence = []
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                continue
            try:
                evidence_page = int(evidence.get("page")) if evidence.get("page") is not None else None
            except (TypeError, ValueError):
                evidence_page = None
            surface = str(evidence.get("surface") or "").strip()
            if not surface:
                continue
            model_evidence.append({
                "filename": str(evidence.get("filename") or "").strip(),
                "page": evidence_page,
                "surface": surface,
                "evidence_span": str(evidence.get("evidence_span") or "").strip()[:240],
            })
        out.append({"name": name, "keyword": keyword, "aliases": aliases[:12], "weight": weight, "page": page, "filename": fname, "pages": [x["page"] for x in occurrences if x.get("page") is not None], "occurrences": occurrences, "links": links, "model_evidence": model_evidence})
    return out


_MAP_PROMPT = (
    "다음은 한 단원 강의자료의 일부다. 이 조각의 핵심 개념을 최대 16개 뽑아라.\n"
    "- name은 반드시 한국어 표준 용어로 쓴다. 설명 금지. JSON 배열만 출력.\n"
    "- 각 항목: {\"name\":\"한국어 개념명\",\"keyword\":\"본문에 실제 등장하는 핵심 용어\",\"aliases\":[\"영문명\",\"약어\",\"한국어 동의어\"],\"importance\":1~5,\"related\":[\"관련 keyword\", ...]}\n"
    "- 본문에 괄호로 표시된 영문 의학용어와 약어를 빠뜨리지 말고 aliases에 보존한다. 한글이 깨지고 영문만 남은 용어도 한국어 name을 추론한다.\n"
    "- keyword는 본문에서 실제 찾을 수 있는 짧은 문자열(예: \"AKI\", \"Bradycardia\")이어야 한다.\n"
    "자료:"
)


_CANDIDATE_AWARE_MAP_RULES = (
    "다음은 페이지 번호가 표시된 한 단원 강의자료의 일부다. 페이지 전체의 핵심 개념을 뽑아라.\n"
    "- name은 반드시 한국어 표준 용어로 쓴다. 설명 금지. JSON 배열만 출력한다.\n"
    "- 각 항목 형식: "
    '{"name":"한국어 개념명","keyword":"본문 표기","aliases":["영문명","약어","동의어"],'
    '"importance":1~5,"related":["관련 keyword"],'
    '"evidence":[{"filename":"파일명","page":페이지번호,"surface":"본문의 실제 표기",'
    '"evidence_span":"개념을 확인할 수 있는 짧은 원문"}]}\n'
    "- keyword 또는 aliases 중 적어도 하나는 자료 원문에 실제로 있어야 한다.\n"
    "- evidence의 surface와 evidence_span은 자료에서 그대로 찾을 수 있는 문자열이어야 한다.\n"
    "- 괄호 안 영문명과 약어를 aliases에 보존한다.\n"
    "- aliases에는 같은 개념의 정확한 동의어·영문명·약어만 넣는다. 예: 림프종의 alias에 호지킨림프종을, 백혈병의 alias에 급성백혈병을 넣지 않는다. 관련 개념, 하위 유형, 반대 개념은 related에 넣는다.\n"
    "- '조혈작용의 감소', '비정상적으로 증가되어 있는 상태' 같은 설명·분류 문구를 name으로 만들지 말고 표준 질환명·검사명·기전명만 개념으로 만든다.\n"
    "- '단검사'처럼 잘린 검사명은 개념으로 만들지 않는다. Fe처럼 1~2자인 표기는 같은 페이지에 긴 표준명이나 한국어 이름이 확인될 때만 keyword로 쓴다.\n"
    "- keyword에는 정의 문장이나 '적혈구의 파괴의 증가' 같은 설명을 넣지 말고, 원문에 있는 짧은 표준명·영문명·약어를 사용한다.\n"
    "- 후보 목록은 누락 방지용 체크리스트일 뿐이다. 근거가 없는 후보를 억지로 채택하지 않는다.\n"
    "- 모든 [FILE | PAGE] 구간을 페이지 순서대로 빠짐없이 검토한다. 페이지마다 핵심 개념이 있으면 1개 이상 반환한다.\n"
    "- 같은 개념이 여러 페이지에 나오면 항목을 중복 생성하지 말고 evidence 배열에 근거를 합친다.\n"
    "- 전체 개수 상한은 없다. 앞쪽 페이지의 개념만 뽑고 뒤쪽 페이지를 생략하지 않는다.\n"
    "- 사진 출처나 그림 캡션에만 등장하고 본문 정의·기전·설명이 없는 질환명, 인명, 기관명은 개념에서 제외한다.\n"
    "- 개수 상한을 채우기 위해 일반 단어나 자료 밖 지식을 추가하지 않는다.\n"
)


def build_candidate_aware_map_prompt(chunks, candidates, max_chars: int = 11000) -> str:
    """Build the future MAP prompt without invoking an external model."""
    candidate_payload = []
    for candidate in candidates:
        occurrences = candidate.get("occurrences") if isinstance(candidate.get("occurrences"), list) else []
        candidate_payload.append({
            "name": str(candidate.get("name") or ""),
            "aliases": [str(x) for x in candidate.get("aliases", []) if str(x).strip()][:12],
            "evidence": [
                {
                    "filename": str(item.get("filename") or ""),
                    "page": item.get("page"),
                    "surface": str(item.get("surface") or ""),
                    "candidate_rule": str(item.get("candidate_rule") or ""),
                }
                for item in occurrences[:5]
                if isinstance(item, dict)
            ],
        })
    source_parts, used = [], 0
    for chunk in chunks:
        marker = f"[FILE: {chunk.get('filename') or ''} | PAGE: {chunk.get('page') or ''}]\n"
        body = str(chunk.get("text") or "")
        available = max_chars - used - len(marker)
        if available <= 0:
            break
        part = marker + body[:available]
        source_parts.append(part)
        used += len(part)
    return (
        _CANDIDATE_AWARE_MAP_RULES
        + "\n[누락 검사 후보]\n"
        + json.dumps(candidate_payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\n[페이지 원문]\n"
        + "\n\n".join(source_parts)
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


def _segment_concept_chunks(chunks, seg_size):
    page_groups = []
    for chunk in chunks:
        page_key = (str(chunk.get("filename") or ""), int(chunk.get("page") or 0))
        if not page_groups or page_groups[-1][0] != page_key:
            page_groups.append((page_key, []))
        page_groups[-1][1].append(chunk)

    segments, current, current_length = [], [], 0
    for _, page_chunks in page_groups:
        page_length = sum(len(chunk.get("text") or "") for chunk in page_chunks)
        if current and current_length + page_length > seg_size:
            segments.append(current)
            current, current_length = [], 0
        current.extend(page_chunks)
        current_length += page_length
    if current:
        segments.append(current)
    return segments


def _candidate_aware_max_tokens(segment, candidates):
    page_count = len({
        (str(chunk.get("filename") or ""), int(chunk.get("page") or 0))
        for chunk in segment
    })
    return min(6000, max(2200, 900 + page_count * 120 + len(candidates) * 35))


def _segment_source_limit(segment, seg_size):
    source_length = sum(len(chunk.get("text") or "") + 80 for chunk in segment)
    return max(seg_size + 2000, source_length)


def _candidates_for_segment(candidates, segment):
    source_keys = {
        (str(chunk.get("filename") or ""), int(chunk.get("page") or 0))
        for chunk in segment
    }
    return [
        candidate for candidate in candidates
        if any(
            (str(item.get("filename") or ""), int(item.get("page") or 0)) in source_keys
            for item in candidate.get("occurrences", [])
            if isinstance(item, dict)
        )
    ]


def shadow_extract_concepts_from_chunks(
    chunks, candidates, unit, seg_size: int = 9000, existing_concepts=None
):
    """Run candidate-aware extraction without reading or writing concepts.json.

    There is exactly one MAP call per segment and at most one grouping call. A
    failed MAP call is reported instead of falling back to another model call.
    """
    from concept_quality import canonicalize_grounded_concepts, reconcile_with_existing_concepts

    ordered_chunks = sorted(
        [dict(chunk) for chunk in chunks],
        key=lambda chunk: (
            str(chunk.get("filename") or ""),
            int(chunk.get("page") or 0),
            int(chunk.get("chunk_index") or 0),
        ),
    )
    segments = _segment_concept_chunks(ordered_chunks, seg_size)
    parsed_items, errors = [], []

    def extract_segment(index_segment):
        index, segment = index_segment
        prompt = build_candidate_aware_map_prompt(
            segment,
            segment_candidates := _candidates_for_segment(candidates, segment),
            max_chars=_segment_source_limit(segment, seg_size),
        )
        try:
            response = generate_answer(
                prompt,
                max_tokens=_candidate_aware_max_tokens(segment, segment_candidates),
            )
            return {"items": _parse_concept_items(response, segment), "error": None}
        except Exception as exc:
            return {"items": [], "error": {"segment": index, "error": f"{type(exc).__name__}: {exc}"}}

    for result in _parallel_ordered(enumerate(segments, start=1), extract_segment):
        parsed_items.extend(result["items"])
        if result["error"]:
            errors.append(result["error"])

    grounded = canonicalize_grounded_concepts(parsed_items, ordered_chunks)
    reconciled = reconcile_with_existing_concepts(
        grounded["accepted"], existing_concepts or []
    )
    accepted = reconciled["accepted"]
    group_calls = 1 if len(accepted) > 3 else 0
    if accepted:
        accepted = _assign_groups(accepted, unit)
        accepted.sort(key=lambda concept: (-concept["weight"], concept["name"]))
    return {
        "concepts": accepted,
        "rejected": [*grounded["rejected"], *reconciled["rejected"]],
        "errors": errors,
        "map_calls": len(segments),
        "group_calls": group_calls,
    }


def build_concepts_for_unit_shadow(
    user_id,
    semester,
    course,
    unit,
    existing_concepts=None,
    saved_aliases=None,
    seg_size: int = 9000,
):
    """Load one exact unit and return an unpersisted extraction comparison."""
    from concept_quality import build_concept_coverage_report

    where_filter = _build_where_filter({"semester": semester, "course": course}, user_id=user_id)
    if not where_filter:
        return {"concepts": [], "rejected": [], "errors": [], "map_calls": 0, "group_calls": 0,
                "coverage": {"candidate_count": 0, "covered_count": 0, "missing_count": 0,
                             "covered": [], "missing": []}}
    results = collection.get(where=where_filter, include=["metadatas", "documents"])
    chunks = [
        {
            "text": document or "",
            "page": metadata.get("page"),
            "filename": metadata.get("filename"),
            "chunk_index": metadata.get("chunk_index"),
        }
        for metadata, document in zip(results.get("metadatas", []), results.get("documents", []))
        if (metadata.get("unit") or "").strip() == unit
    ]
    coverage = build_concept_coverage_report(chunks, existing_concepts or [], saved_aliases or {})
    result = shadow_extract_concepts_from_chunks(
        chunks,
        coverage["missing"],
        unit,
        seg_size=seg_size,
        existing_concepts=existing_concepts or [],
    )
    result["coverage"] = coverage
    result["source_chunk_count"] = len(chunks)
    return result


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

    def extract_segment(seg):
        text = "".join(c["text"] for c in seg)[:seg_size + 2000]
        try:
            return _parse_concept_items(generate_answer(_MAP_PROMPT + text, max_tokens=2200), seg)
        except Exception:
            return []

    raw = []
    for extracted in _parallel_ordered(segments, extract_segment):
        raw.extend(extracted)
    if not raw:
        sampled = "".join(c["text"] for c in chunks)[:11000]
        raw = _parse_concept_items(generate_answer(_MAP_PROMPT + sampled, max_tokens=2200), chunks)
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
            e["aliases"] = list(dict.fromkeys([*(e.get("aliases") or []), *(c.get("aliases") or [])]))[:12]
            if e.get("page") is None: e["page"] = c.get("page")
            if e.get("filename") is None: e["filename"] = c.get("filename")
            merged_occurrences = {
                (item.get("filename") or "", item.get("page")): item
                for item in [*(e.get("occurrences") or []), *(c.get("occurrences") or [])]
            }
            e["occurrences"] = sorted(merged_occurrences.values(), key=lambda x: (int(x.get("page") or 0), str(x.get("filename") or "")))
            e["pages"] = sorted({item.get("page") for item in e["occurrences"] if item.get("page") is not None})
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


def move_chunks_by_filter(search_filter, updates, user_id: str):
    """Move matching chunks by changing only their library-scope metadata."""
    where_filter = _build_where_filter(search_filter, user_id=user_id)
    if not where_filter:
        return 0
    result = collection.get(where=where_filter, include=["metadatas"])
    ids, metadatas = [], []
    allowed_updates = {
        key: str(value or "").strip()
        for key, value in (updates or {}).items()
        if key in {"semester", "course", "unit"} and str(value or "").strip()
    }
    if not allowed_updates:
        return 0
    for chunk_id, metadata in zip(result.get("ids", []), result.get("metadatas", [])):
        ids.append(chunk_id)
        metadatas.append({**(metadata or {}), **allowed_updates})
    if ids:
        collection.update(ids=ids, metadatas=metadatas)
    return len(ids)

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
    return [{
        "unit": name,
        "file_count": len(info["files"]),
        "page_count": len(info["pages"]),
        "files": sorted(info["files"]),
    } for name, info in sorted(units.items())]

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


def _write_json_atomic(path, payload):
    """Write JSON beside the destination and replace it in one filesystem step."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, delete=False
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _concept_scope_prefix(user_id, semester, course, unit):
    return f"{user_id}::{semester}::{course}::{unit}::"


def _node_in_scope(node, user_id, semester, course, unit):
    return (
        isinstance(node, dict)
        and node.get("user_id") == user_id
        and node.get("semester") == semester
        and node.get("course") == course
        and node.get("unit") == unit
    )


def build_concept_embeddings_for_scope(user_id, semester, course, unit):
    """Replace graph embeddings for one unit while preserving every other node."""
    concepts_path = os.path.join(DATA_DIR, "concepts.json")
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    if not os.path.exists(concepts_path):
        raise ValueError("concepts.json이 없습니다.")

    with open(concepts_path, "r", encoding="utf-8") as concepts_file:
        concepts_data = json.load(concepts_file)
    concepts = (
        concepts_data.get(user_id, {})
        .get(semester, {})
        .get(course, {})
        .get(unit)
    )
    if not isinstance(concepts, list):
        raise ValueError("지정한 범위의 개념 목록을 찾지 못했습니다.")

    existing_index = []
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as index_file:
            loaded_index = json.load(index_file)
        if isinstance(loaded_index, list):
            existing_index = loaded_index

    target_nodes = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        name = str(concept.get("name") or "").strip()
        if not name:
            continue
        keyword = str(concept.get("keyword") or name).strip()
        embedding = embed_text(f"{keyword} {name}".strip())
        if not embedding:
            raise RuntimeError(f"개념 임베딩 생성 실패: {name}")
        target_nodes.append({
            "id": f"{_concept_scope_prefix(user_id, semester, course, unit)}{name}",
            "user_id": user_id,
            "semester": semester,
            "course": course,
            "unit": unit,
            "name": name,
            "keyword": keyword,
            "weight": concept.get("weight", 1),
            "embedding": embedding,
        })

    if concepts and not target_nodes:
        raise RuntimeError("지정한 범위의 개념 임베딩을 생성하지 못했습니다.")

    preserved_nodes = [
        node for node in existing_index
        if not _node_in_scope(node, user_id, semester, course, unit)
    ]
    updated_index = preserved_nodes + target_nodes
    _write_json_atomic(index_path, updated_index)
    return {
        "target_nodes": target_nodes,
        "replaced_count": len(existing_index) - len(preserved_nodes),
        "preserved_count": len(preserved_nodes),
        "total_count": len(updated_index),
    }

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


def build_cross_links_for_scope(
    user_id,
    semester,
    course,
    unit,
    threshold=0.45,
    top_k=5,
    auto_accept_score=0.85,
    max_similarity_exclusive=0.92,
):
    """Rebuild only cross-course edges touching one unit, without LLM calls."""
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    links_path = os.path.join(DATA_DIR, "concept_links.json")
    if not os.path.exists(index_path):
        raise ValueError("concept_index.json이 없습니다.")

    with open(index_path, "r", encoding="utf-8") as index_file:
        embeddings_index = json.load(index_file)
    user_nodes = [
        node for node in embeddings_index
        if isinstance(node, dict)
        and node.get("user_id") == user_id
        and node.get("embedding")
    ]
    target_nodes = [
        node for node in user_nodes
        if _node_in_scope(node, user_id, semester, course, unit)
    ]
    if not target_nodes:
        raise ValueError("지정한 범위의 그래프 노드를 찾지 못했습니다.")

    links_payload = {"user_id": user_id, "edges": []}
    if os.path.exists(links_path):
        with open(links_path, "r", encoding="utf-8") as links_file:
            loaded_links = json.load(links_file)
        if isinstance(loaded_links, dict):
            links_payload = dict(loaded_links)
    existing_edges = links_payload.get("edges", [])
    if not isinstance(existing_edges, list):
        existing_edges = []

    scope_prefix = _concept_scope_prefix(user_id, semester, course, unit)
    preserved_edges = []
    for edge in existing_edges:
        if not isinstance(edge, dict):
            continue
        endpoint_a = str(edge.get("a") or edge.get("source") or "")
        endpoint_b = str(edge.get("b") or edge.get("target") or "")
        if endpoint_a.startswith(scope_prefix) or endpoint_b.startswith(scope_prefix):
            continue
        preserved_edges.append(edge)

    new_edges, seen_pairs = [], set()
    for concept_a in target_nodes:
        similarities = []
        for concept_b in user_nodes:
            if (
                concept_a["id"] == concept_b.get("id")
                or concept_a.get("course") == concept_b.get("course")
            ):
                continue
            score = _cosine_similarity(
                concept_a.get("embedding"), concept_b.get("embedding")
            )
            if not (threshold <= score < max_similarity_exclusive):
                continue
            if _norm_name(concept_a.get("name")) == _norm_name(concept_b.get("name")):
                continue
            adjusted_score = score * (
                1 + 0.1 * ((concept_a.get("weight", 1) + concept_b.get("weight", 1)) / 10)
            )
            similarities.append((adjusted_score, score, concept_b))

        similarities.sort(key=lambda item: item[0], reverse=True)
        for adjusted_score, score, concept_b in similarities[:top_k]:
            if score < auto_accept_score:
                continue
            pair_key = tuple(sorted((concept_a["id"], concept_b["id"])))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            new_edges.append({
                "a": concept_a["id"],
                "b": concept_b["id"],
                "score": adjusted_score,
                "type": "cross",
                "reason": "High similarity score (incremental, no LLM)",
            })

    links_payload["user_id"] = user_id
    links_payload["edges"] = preserved_edges + new_edges
    _write_json_atomic(links_path, links_payload)
    return {
        "target_node_count": len(target_nodes),
        "removed_edge_count": len(existing_edges) - len(preserved_edges),
        "new_edges": new_edges,
        "preserved_edge_count": len(preserved_edges),
        "total_edge_count": len(links_payload["edges"]),
        "llm_calls": 0,
    }


def remove_graph_scope(user_id, semester, course, unit):
    """Remove one empty library scope from the persisted concept graph."""
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    links_path = os.path.join(DATA_DIR, "concept_links.json")
    if not os.path.exists(index_path):
        return {"removed_nodes": 0, "removed_edges": 0}
    with open(index_path, encoding="utf-8") as index_file:
        index = json.load(index_file)
    index = index if isinstance(index, list) else []
    removed_ids = {
        str(node.get("id") or "")
        for node in index
        if _node_in_scope(node, user_id, semester, course, unit)
    }
    prefix = _concept_scope_prefix(user_id, semester, course, unit)
    kept_nodes = [node for node in index if str(node.get("id") or "") not in removed_ids]
    _write_json_atomic(index_path, kept_nodes)

    removed_edges = 0
    if os.path.exists(links_path):
        with open(links_path, encoding="utf-8") as links_file:
            links = json.load(links_file)
        links = links if isinstance(links, dict) else {"user_id": user_id, "edges": []}
        edges = links.get("edges") if isinstance(links.get("edges"), list) else []
        kept_edges = []
        for edge in edges:
            endpoint_a = str(edge.get("a") or edge.get("source") or "")
            endpoint_b = str(edge.get("b") or edge.get("target") or "")
            if (
                endpoint_a in removed_ids or endpoint_b in removed_ids
                or endpoint_a.startswith(prefix) or endpoint_b.startswith(prefix)
            ):
                removed_edges += 1
                continue
            kept_edges.append(edge)
        links["edges"] = kept_edges
        _write_json_atomic(links_path, links)
    return {"removed_nodes": len(removed_ids), "removed_edges": removed_edges}


def move_graph_scope(user_id, source_scope, target_scope):
    """Relocate graph node IDs and their edge endpoints without new embeddings."""
    source_semester, source_course, source_unit = source_scope
    target_semester, target_course, target_unit = target_scope
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    links_path = os.path.join(DATA_DIR, "concept_links.json")
    if not os.path.exists(index_path):
        return {"moved_nodes": 0, "deduplicated_nodes": 0, "updated_edges": 0}
    with open(index_path, encoding="utf-8") as index_file:
        index = json.load(index_file)
    index = index if isinstance(index, list) else []
    target_prefix = _concept_scope_prefix(
        user_id, target_semester, target_course, target_unit
    )
    existing_target_ids = {
        str(node.get("id") or "")
        for node in index
        if _node_in_scope(
            node, user_id, target_semester, target_course, target_unit
        )
    }
    remap, moved_nodes, deduplicated = {}, 0, 0
    updated_nodes = []
    for node in index:
        if not _node_in_scope(
            node, user_id, source_semester, source_course, source_unit
        ):
            updated_nodes.append(node)
            continue
        old_id = str(node.get("id") or "")
        new_id = target_prefix + str(node.get("name") or node.get("keyword") or "")
        remap[old_id] = new_id
        moved_nodes += 1
        if new_id in existing_target_ids:
            deduplicated += 1
            continue
        existing_target_ids.add(new_id)
        updated_nodes.append({
            **node,
            "id": new_id,
            "semester": target_semester,
            "course": target_course,
            "unit": target_unit,
        })
    _write_json_atomic(index_path, updated_nodes)

    updated_edges = 0
    if os.path.exists(links_path) and remap:
        with open(links_path, encoding="utf-8") as links_file:
            links = json.load(links_file)
        links = links if isinstance(links, dict) else {"user_id": user_id, "edges": []}
        edges = links.get("edges") if isinstance(links.get("edges"), list) else []
        deduped_edges, seen = [], set()
        for edge in edges:
            changed = False
            item = dict(edge)
            for key in ("a", "b", "source", "target"):
                endpoint = str(item.get(key) or "")
                if endpoint in remap:
                    item[key] = remap[endpoint]
                    changed = True
            endpoint_a = str(item.get("a") or item.get("source") or "")
            endpoint_b = str(item.get("b") or item.get("target") or "")
            if not endpoint_a or not endpoint_b or endpoint_a == endpoint_b:
                continue
            pair = tuple(sorted((endpoint_a, endpoint_b)))
            if pair in seen:
                continue
            seen.add(pair)
            deduped_edges.append(item)
            updated_edges += bool(changed)
        links["edges"] = deduped_edges
        _write_json_atomic(links_path, links)
    return {
        "moved_nodes": moved_nodes,
        "deduplicated_nodes": deduplicated,
        "updated_edges": updated_edges,
    }
