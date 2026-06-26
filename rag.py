import chromadb
import json
import os
from providers.hybrid_provider import embed_text, generate_answer


chroma_client = chromadb.PersistentClient(path=os.getenv("CHROMA_PATH", "./chroma_db"))
DATA_DIR = os.getenv("DATA_DIR", "./data")

collection = chroma_client.get_or_create_collection(
    name="study_notes"
)


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
    unit: str = ""
):
    ids = []
    documents = []
    metadatas = []
    embeddings = []

    for page in pages:
        page_number = page["page"]
        chunks = chunk_text(page["text"])

        for chunk_index, chunk in enumerate(chunks):
            chunk_id = (
                f"{user_id}-{semester}-{course}-{title}-{filename}"
                f"-p{page_number}-c{chunk_index}"
            )

            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({
                "user_id": user_id,
                "semester": semester,
                "course": course,
                "title": title,
                "filename": filename,
                "page": page_number,
                "chunk_index": chunk_index,
                "unit": unit
            })
            embeddings.append(embed_text(chunk))

    if ids:
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings
        )


def _normalize_existing_user_ids():
    results = collection.get(include=["metadatas"])
    update_ids = []
    update_metadatas = []

    for chunk_id, metadata in zip(results.get("ids", []), results.get("metadatas", [])):
        metadata = metadata or {}

        if metadata.get("user_id"):
            continue

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
        value = search_filter.get(key) if search_filter else None

        if value:
            conditions.append({key: value})

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


def _extract_json_array(text: str):
    if not isinstance(text, str):
        return text

    start = text.find("[")
    end = text.rfind("]")

    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return text


def build_concepts_for_unit(user_id, semester, course, unit):
    _normalize_existing_user_ids()
    where_filter = _build_where_filter({"semester": semester, "course": course}, user_id=user_id)

    if not where_filter:
        return []

    results = collection.get(where=where_filter, include=["metadatas", "documents"])
    metadatas = results.get("metadatas", []) or []
    documents = results.get("documents", []) or []

    chunks = []
    for metadata, document in zip(metadatas, documents):
        if (metadata.get("unit") or "").strip() == unit:
            chunks.append({
                "text": document or "",
                "page": metadata.get("page"),
                "filename": metadata.get("filename"),
                "chunk_index": metadata.get("chunk_index"),
            })

    if not chunks:
        return []

    merged_text = "".join([chunk["text"] for chunk in chunks])[:6000]
    prompt = (
        "다음은 한 단원의 강의자료다. 이 단원의 핵심 개념을 8개 이내로 뽑아라.\n"
        "- 반드시 한국어. 설명 금지. JSON 배열만 출력.\n"
        "- 각 항목 형식: {\"name\":\"개념 전체 이름\",\"keyword\":\"가장 짧은 핵심 용어\",\"importance\":1~5,\"related\":[\"같은 단원에서 직접 관련된 다른 개념 keyword\", ...]}\n"
        "- keyword는 본문에서 실제로 찾을 수 있는 짧은 단어(예: \"신부전\", \"AKI\")로.\n"
        "- 반드시 JSON 배열 형식만 출력: [{\"name\":..., \"keyword\":..., \"importance\":..., \"related\":[...]}, ...]\n"
        "자료:" + merged_text
    )

    response = generate_answer(prompt).strip()
    response = _extract_json_array(response)

    try:
        parsed = json.loads(response)
    except Exception:
        return []

    if not isinstance(parsed, list):
        return []

    unique_names = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if name and name not in unique_names:
            unique_names.append(name)

    concepts = []
    for item in parsed:
        if not isinstance(item, dict):
            continue

        name = (item.get("name") or "").strip()
        if not name:
            continue

        importance = item.get("importance")
        if importance is None:
            importance = item.get("weight")

        try:
            weight = int(importance)
        except Exception:
            continue

        weight = max(1, min(5, weight))

        page = None
        for chunk in chunks:
            if name in chunk["text"]:
                page = chunk.get("page")
                break

    concepts = []
    for item in parsed:
        if not isinstance(item, dict):
            continue

        name = (item.get("name") or "").strip()
        keyword = (item.get("keyword") or "").strip()
        if not name or not keyword:
            continue

        importance = item.get("importance")
        if importance is None:
            importance = item.get("weight")

        try:
            weight = int(importance)
        except Exception:
            continue

        weight = max(1, min(5, weight))

        # keyword 기반 page 매칭
        page = None
        for chunk in chunks:
            if keyword in chunk["text"]:
                page = chunk.get("page")
                break

        # related 필드를 사용하여 links 구성
        related = item.get("related", [])
        if not isinstance(related, list):
            related = []
        links = [r.strip() for r in related if isinstance(r, str) and r.strip()]

        concepts.append({
            "name": name,
            "keyword": keyword,
            "weight": weight,
            "page": page,
            "links": links,
        })

    return concepts


def _count_matching_chunks(where_filter=None):
    if not where_filter:
        return collection.count()

    results = collection.get(
        where=where_filter,
        include=["metadatas"]
    )

    return len(results.get("ids", []))


def delete_chunks_by_filter(search_filter, user_id: str):
    _normalize_existing_user_ids()
    where_filter = _build_where_filter(search_filter, user_id=user_id)

    if not where_filter:
        return 0

    deleted_count = _count_matching_chunks(where_filter)

    if deleted_count == 0:
        return 0

    collection.delete(where=where_filter)
    return deleted_count


def reset_collection(user_id: str):
    _normalize_existing_user_ids()
    where_filter = _build_where_filter(user_id=user_id)
    deleted_count = _count_matching_chunks(where_filter)

    if deleted_count > 0:
        collection.delete(where=where_filter)

    return deleted_count


def search_relevant_chunks(question: str, n_results: int = 5, search_filter=None, user_id=None):
    _normalize_existing_user_ids()
    where_filter = _build_where_filter(search_filter, user_id=user_id)
    document_count = _count_matching_chunks(where_filter)

    if document_count == 0:
        return []

    n_results = min(n_results, document_count)
    question_embedding = embed_text(question)

    query_args = {
        "query_embeddings": [question_embedding],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"]
    }

    if where_filter:
        query_args["where"] = where_filter

    results = collection.query(**query_args)

    chunks = []

    if not results["documents"] or not results["documents"][0]:
        return chunks

    ids = results.get("ids", [[]])[0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0]

    for index, (doc, metadata) in enumerate(zip(documents, metadatas)):
        chunks.append({
            "id": ids[index] if index < len(ids) else "",
            "text": doc,
            "semester": metadata.get("semester", ""),
            "course": metadata.get("course", ""),
            "title": metadata.get("title", ""),
            "filename": metadata.get("filename", ""),
            "user_id": metadata.get("user_id", "default"),
            "page": metadata.get("page", ""),
            "chunk_index": metadata.get("chunk_index", ""),
            "distance": distances[index] if index < len(distances) else None
        })

    return chunks


def get_filter_label(search_filter=None):
    if not search_filter:
        return "전체 자료"

    parts = []

    if search_filter.get("semester"):
        parts.append(search_filter["semester"])

    if search_filter.get("course"):
        parts.append(search_filter["course"])

    if search_filter.get("filename"):
        parts.append(search_filter["filename"])

    if not parts:
        return "전체 자료"

    return " / ".join(parts)


def get_library_overview(user_id: str):
    _normalize_existing_user_ids()
    where_filter = _build_where_filter(user_id=user_id)
    total_chunks = _count_matching_chunks(where_filter)
    overview = {
        "total_chunks": total_chunks,
        "semesters": {}
    }

    if total_chunks == 0:
        return overview

    results = collection.get(where=where_filter, include=["metadatas"])

    for metadata in results.get("metadatas", []):
        semester = metadata.get("semester") or "학기 미지정"
        course = metadata.get("course") or "과목 미지정"
        title = metadata.get("title") or ""
        filename = metadata.get("filename") or "파일명 미지정"

        semester_data = overview["semesters"].setdefault(semester, {})
        course_data = semester_data.setdefault(course, {})

        if filename not in course_data:
            course_data[filename] = {
                "title": title,
                "filename": filename
            }
        elif title and not course_data[filename].get("title"):
            course_data[filename]["title"] = title

    return overview


def get_units(user_id: str, semester: str, course: str):
    _normalize_existing_user_ids()
    where_filter = _build_where_filter(
        {"semester": semester, "course": course},
        user_id=user_id,
    )

    if not where_filter:
        return []

    results = collection.get(where=where_filter, include=["metadatas"])
    units = {}

    for metadata in results.get("metadatas", []):
        unit_name = (metadata.get("unit") or "").strip()
        if not unit_name:
            continue

        filename = metadata.get("filename") or ""
        page = metadata.get("page")

        if unit_name not in units:
            units[unit_name] = {
                "files": set(),
                "pages": set()
            }

        if filename:
            units[unit_name]["files"].add(filename)
        if page is not None:
            units[unit_name]["pages"].add(page)

    return [
        {
            "unit": unit_name,
            "file_count": len(unit_info["files"]),
            "page_count": len(unit_info["pages"]),
        }
        for unit_name, unit_info in sorted(units.items())
    ]


def get_chunks(user_id, limit=50, offset=0, search_filter=None, full=False):
    """저장된 chunk의 본문과 메타데이터를 페이지 단위로 돌려준다(열람용)."""
    _normalize_existing_user_ids()
    where_filter = _build_where_filter(search_filter, user_id=user_id)
    results = collection.get(where=where_filter, include=["metadatas", "documents"])

    ids = results.get("ids", []) or []
    metas = results.get("metadatas", []) or []
    docs = results.get("documents", []) or []

    items = []
    for i in range(len(ids)):
        m = metas[i] if i < len(metas) else {}
        text = docs[i] if i < len(docs) else ""
        items.append({
            "id": ids[i],
            "semester": m.get("semester"),
            "course": m.get("course"),
            "title": m.get("title"),
            "filename": m.get("filename"),
            "unit": m.get("unit", ""),
            "page": m.get("page"),
            "chunk_index": m.get("chunk_index"),
            "text": text if full else (text[:200]),
        })

    items.sort(key=lambda x: (x["filename"] or "", x["page"] or 0, x["chunk_index"] or 0))
    total = len(items)
    return {"total": total, "limit": limit, "offset": offset, "items": items[offset:offset + limit]}


def _chunk_key(chunk):
    return (
        chunk.get("filename", ""),
        chunk.get("page", ""),
        chunk.get("chunk_index", "")
    )


def _has_different_source(candidate, direct_chunks):
    for direct_chunk in direct_chunks:
        if (
            candidate.get("filename") != direct_chunk.get("filename")
            or candidate.get("course") != direct_chunk.get("course")
            or candidate.get("semester") != direct_chunk.get("semester")
        ):
            return True

    return False


def _format_context(chunks):
    return "\n\n".join(
        [
            (
                f"[출처: {chunk['semester']} / {chunk['course']} / "
                f"{chunk['title']} / {chunk['filename']} p.{chunk['page']}]\n"
                f"{chunk['text']}"
            )
            for chunk in chunks
        ]
    )


def _extract_connection_concepts(question, direct_chunks):
    concept_context = _format_context(direct_chunks[:2])

    prompt = f"""
너는 강의자료에서 연결 검색에 쓸 핵심 개념만 뽑는 도우미다.

규칙:
- 반드시 한국어로만 답한다.
- 중국어, 일본어, 베트남어, 한자, 외국어식 표현을 섞지 않는다.
- 아래 자료와 질문에서 연결 검색에 쓸 핵심 개념을 3~6개만 뽑는다.
- 설명문을 쓰지 않는다.
- 쉼표로 구분한 키워드만 출력한다.
- 자료에 영어 전문용어가 있으면 그 용어는 유지할 수 있다.

자료:
{concept_context}

질문:
{question}
"""

    return generate_answer(prompt).strip()


def _extract_sources(chunks):
    return [
        {
            "semester": chunk["semester"],
            "course": chunk["course"],
            "title": chunk["title"],
            "filename": chunk["filename"],
            "page": chunk["page"]
        }
        for chunk in chunks
    ]


def answer_question(question: str, search_filter=None, search_scope_label=None, user_id=None):
    chunks = search_relevant_chunks(question, search_filter=search_filter, user_id=user_id)

    if not chunks:
        return "선택한 검색 범위에서 관련 자료를 찾지 못했습니다. 먼저 PDF를 학습시키거나 검색 범위를 넓혀주세요.", []

    context = _format_context(chunks)
    search_scope_label = search_scope_label or get_filter_label(search_filter)

    prompt = f"""
너는 사용자의 강의자료를 기반으로 설명하는 공부 도우미다.

규칙:
- 반드시 아래 제공된 자료를 우선 근거로 답변한다.
- 자료에 없는 내용은 추측하지 말고 "업로드된 자료만으로는 확인하기 어렵다"고 말한다.
- 반드시 자연스러운 한국어로만 답변한다.
- 중국어, 일본어, 베트남어, 한자, 외국어식 표현을 절대 섞지 않는다.
- 답변 문장은 한국어 문장으로만 작성한다.
- 자료에 영어 전문용어가 있으면 용어는 유지하되, 설명은 오로지 한국어로 한다.
- 답변은 공부하기 좋게 정리한다.
- 가능하면 현재 개념과 관련된 이전 지식도 연결해서 설명한다.
- 마지막에 참고한 출처를 적는다.
- "자료 속 근거" 항목에는 문장 인용을 쓰지 말고 페이지 번호만 적는다.
- "연결해서 복습하면 좋은 개념" 항목에는 설명문을 쓰지 말고 키워드와 페이지 번호만 적는다.
- 현재 검색 범위 안에서 찾은 자료만 근거로 답변한다.

답변 형식:
1. 핵심 설명
- 질문에 대한 핵심 답을 1~3문장으로 설명한다.

2. 쉽게 풀어쓴 설명
- 공부하는 사람이 이해하기 쉽게 설명한다.
- 필요한 경우 bullet point를 사용한다.

3. 자료 속 근거
- 자료 내용을 인용하지 않는다.
- 설명 문장을 쓰지 않는다.
- 근거가 되는 페이지 번호만 적는다.
- 형식:
  - p.1, p.3

4. 연결해서 복습하면 좋은 개념
- 긴 설명을 쓰지 않는다.
- 키워드와 페이지 번호만 적는다.
- 형식:
  - 키워드: p.쪽수
  - 키워드: p.쪽수

5. 참고한 출처
- 학기 / 과목명 / 자료명 / 파일명 / 페이지 형식으로 적는다.

자료:
{context}

현재 검색 범위:
{search_scope_label}

질문:
{question}
"""

    answer = generate_answer(prompt)

    sources = _extract_sources(chunks)

    return answer, sources


def answer_with_connections(question: str, search_filter=None, search_scope_label=None, user_id=None):
    direct_chunks = search_relevant_chunks(
        question,
        n_results=3,
        search_filter=search_filter,
        user_id=user_id
    )

    if not direct_chunks:
        return "선택한 검색 범위에서 직접 관련 자료를 찾지 못했습니다. 먼저 PDF를 학습시키거나 검색 범위를 넓혀주세요.", []

    connection_concepts = _extract_connection_concepts(question, direct_chunks)
    connection_query = "\n\n".join(
        [question, connection_concepts] + [chunk["text"][:350] for chunk in direct_chunks[:2]]
    )
    candidate_pool = search_relevant_chunks(connection_query, n_results=12, user_id=user_id)

    direct_keys = {_chunk_key(chunk) for chunk in direct_chunks}
    selected_keys = set()
    preferred_candidates = []
    fallback_candidates = []

    for candidate in candidate_pool:
        candidate_key = _chunk_key(candidate)

        if candidate_key in direct_keys or candidate_key in selected_keys:
            continue

        selected_keys.add(candidate_key)

        if _has_different_source(candidate, direct_chunks):
            preferred_candidates.append(candidate)
        else:
            fallback_candidates.append(candidate)

    connection_chunks = (preferred_candidates + fallback_candidates)[:4]

    direct_context = _format_context(direct_chunks)
    connection_context = _format_context(connection_chunks)

    if not connection_context:
        connection_context = "다른 PDF, 과목, 학기에서 뚜렷하게 연결되는 후보 자료를 찾지 못했습니다."

    search_scope_label = search_scope_label or get_filter_label(search_filter)

    prompt = f"""
너는 사용자의 강의자료를 서로 연결해 설명하는 공부 도우미다.

가장 중요한 규칙:
- 반드시 한국어로만 답변한다.
- 중국어, 일본어, 베트남어, 한자, 외국어식 표현을 절대 섞지 않는다.
- 답변 문장은 한국어 문장으로만 작성한다.
- 자료에 영어 전문용어가 있으면 용어는 유지하되, 설명 문장은 오로지 한국어로 쓴다.
- 아래 제공된 직접 관련 자료와 연결 후보 자료만 근거로 답변한다.
- 자료에 없는 내용은 추측하지 말고 "업로드된 자료만으로는 확인하기 어렵다"고 말한다.
- 직접 관련 자료와 연결 후보 자료를 구분해서 설명한다.
- 연결 후보가 약하면 억지로 연결하지 말고 약하다고 말한다.
- 연결 후보 자료가 직접 관련 자료와 같은 파일/과목/학기뿐이라면 연결성이 약하다고 말한다.
- 답변은 간결하지만 복습에 바로 쓸 수 있게 정리한다.
- "함께 복습하면 좋은 개념" 항목에는 설명문을 쓰지 말고 키워드와 페이지 번호만 적는다.
- "참고한 출처" 항목은 학기 / 과목명 / 자료명 / 파일명 / 페이지 형식으로 적는다.

답변 형식:
1. 질문과 직접 관련된 자료
2. 다른 PDF에서 연결되는 자료
3. 두 자료가 어떻게 연결되는지
4. 함께 복습하면 좋은 순서
- 키워드와 페이지 번호만 적는다.
- 형식:
  - 키워드: p.쪽수
  - 키워드: p.쪽수
5. 참고한 출처
- 학기 / 과목명 / 자료명 / 파일명 / 페이지 형식으로 적는다.

[직접 관련 자료]
{direct_context}

[1차 검색에서 뽑은 핵심 개념]
{connection_concepts}

[연결 후보 자료]
{connection_context}

직접 자료 검색 범위:
{search_scope_label}

질문:
{question}
"""

    answer = generate_answer(prompt)
    sources = _extract_sources(direct_chunks + connection_chunks)

    return answer, sources


def _cosine_similarity(vec_a, vec_b):
    """두 벡터의 코사인 유사도 계산"""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a ** 2 for a in vec_a) ** 0.5
    norm_b = sum(b ** 2 for b in vec_b) ** 0.5
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)


def build_concept_embeddings(user_id: str):
    """모든 개념을 임베딩하여 concept_index.json에 저장"""
    concepts_path = os.path.join(DATA_DIR, "concepts.json")
    if not os.path.exists(concepts_path):
        return []
    
    with open(concepts_path, "r", encoding="utf-8") as f:
        concepts_data = json.load(f)
    
    if user_id not in concepts_data:
        return []
    
    user_concepts = concepts_data[user_id]
    embeddings_index = []
    
    for semester, semester_data in user_concepts.items():
        for course, course_data in semester_data.items():
            for unit, concepts in course_data.items():
                if not isinstance(concepts, list):
                    continue
                
                for concept in concepts:
                    if not isinstance(concept, dict):
                        continue
                    
                    name = concept.get("name", "")
                    keyword = concept.get("keyword", "")
                    
                    if not name or not keyword:
                        continue
                    
                    # 임베딩 텍스트: keyword + " " + name
                    embed_text_input = f"{keyword} {name}"
                    
                    try:
                        embedding = embed_text(embed_text_input)
                        if not embedding:
                            continue
                    except Exception:
                        continue
                    
                    concept_id = f"{user_id}::{semester}::{course}::{unit}::{name}"
                    
                    embeddings_index.append({
                        "id": concept_id,
                        "user_id": user_id,
                        "semester": semester,
                        "course": course,
                        "unit": unit,
                        "name": name,
                        "keyword": keyword,
                        "weight": concept.get("weight", 1),
                        "embedding": embedding,
                    })
    
    # concept_index.json에 저장
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(embeddings_index, f, ensure_ascii=False, indent=2)
    
    return embeddings_index


def build_cross_links(user_id: str, threshold: float = 0.78, top_k: int = 3):
    """서로 다른 과목 개념 간 유사도 연결 계산 및 저장"""
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    if not os.path.exists(index_path):
        return []
    
    with open(index_path, "r", encoding="utf-8") as f:
        embeddings_index = json.load(f)
    
    # user_id에 해당하는 개념만 필터링
    user_embeddings = [e for e in embeddings_index if e.get("user_id") == user_id]
    
    cross_edges = []
    seen_pairs = set()
    
    # 모든 개념 쌍 중 서로 다른 course인 것만 대상
    for i, concept_a in enumerate(user_embeddings):
        course_a = concept_a.get("course")
        embedding_a = concept_a.get("embedding")
        
        if not embedding_a:
            continue
        
        # 이 개념과 유사한 다른 course의 개념 찾기
        similarities = []
        
        for concept_b in user_embeddings:
            if concept_a.get("id") == concept_b.get("id"):
                continue
            
            course_b = concept_b.get("course")
            if course_a == course_b:  # 같은 과목은 제외
                continue
            
            embedding_b = concept_b.get("embedding")
            if not embedding_b:
                continue
            
            score = _cosine_similarity(embedding_a, embedding_b)
            if score >= threshold:
                similarities.append({
                    "concept_b": concept_b,
                    "score": score,
                })
        
        # top_k 개만 선택
        similarities.sort(key=lambda x: x["score"], reverse=True)
        for sim in similarities[:top_k]:
            concept_b = sim["concept_b"]
            score = sim["score"]
            
            pair_key = tuple(sorted([concept_a.get("id"), concept_b.get("id")]))
            if pair_key not in seen_pairs:
                seen_pairs.add(pair_key)
                cross_edges.append({
                    "a": concept_a.get("id"),
                    "b": concept_b.get("id"),
                    "score": score,
                    "type": "cross",
                })
    
    # concept_links.json에 저장
    links_path = os.path.join(DATA_DIR, "concept_links.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    
    links_data = {
        "user_id": user_id,
        "edges": cross_edges,
    }
    
    with open(links_path, "w", encoding="utf-8") as f:
        json.dump(links_data, f, ensure_ascii=False, indent=2)
    
    return cross_edges
