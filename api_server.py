from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import unicodedata
from pydantic import BaseModel

from providers.openai_provider import generate_answer as generate_openai_answer

from pdf_loader import extract_pdf_text
logger = logging.getLogger(__name__)

from rag import (
    add_pdf_pages_to_db,
    answer_question,
    answer_with_connections,
    build_concepts_for_unit,
    build_concept_embeddings,
    build_cross_links,
    rename_unit,
    delete_chunks_by_filter,
    get_chunks,
    get_filter_label,
    get_library_overview,
    get_units,
)

DATA_DIR = os.getenv("DATA_DIR", "./data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
TIMETABLE_PATH = os.path.join(DATA_DIR, "timetable.json")
CONCEPTS_PATH = os.path.join(DATA_DIR, "concepts.json")
RECALL_TRACES_PATH = os.path.join(DATA_DIR, "recall_traces.json")
CONCEPT_INDEX_PATH = os.path.join(DATA_DIR, "concept_index.json")
CONCEPT_LINKS_PATH = os.path.join(DATA_DIR, "concept_links.json")
MAINTAINER_EMAIL = "kory124@snu.ac.kr"
PROMPTS_DIR = os.getenv("PROMPTS_DIR", "./prompts")
RECALL_FEEDBACK_PROMPT_PATH = os.path.join(PROMPTS_DIR, "recall_feedback.md")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(CONCEPTS_PATH), exist_ok=True)

app = FastAPI(
    title="study-rag-api",
    description="공용 FastAPI 백엔드 서버",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


import auth as _auth


def current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Authorization 토큰에서 로그인 사용자 레코드를 반환한다."""
    token = (authorization or "").replace("Bearer ", "").strip()
    uid = _auth.verify_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user = _auth.get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return user


def current_uid(authorization: Optional[str] = Header(None)) -> str:
    """Authorization: Bearer 토큰에서 로그인 사용자의 data_user_id 를 도출.
    토큰 없거나 잘못되면 401. (클라이언트가 보낸 user_id 는 신뢰하지 않음.)"""
    user = current_user(authorization)
    return user.get("data_user_id") or user["email"]


class SearchFilter(BaseModel):
    semester: Optional[str] = None
    course: Optional[str] = None
    filename: Optional[str] = None


class AskRequest(BaseModel):
    user_id: Optional[str] = None
    question: str
    mode: Optional[str] = "single"
    search_filter: Optional[SearchFilter] = None


class AskResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]
    scope_label: str


class IngestResponse(BaseModel):
    ok: bool
    filename: str
    pages: int
    message: str


class LibraryFile(BaseModel):
    filename: str
    title: str


class LibraryCourse(BaseModel):
    course: str
    files: List[LibraryFile]


class LibrarySemester(BaseModel):
    semester: str
    courses: List[LibraryCourse]


class LibraryResponse(BaseModel):
    total_chunks: int
    semesters: List[LibrarySemester]


class TimetableSemester(BaseModel):
    semester: str
    courses: List[str]


class TimetableResponse(BaseModel):
    semesters: List[TimetableSemester]


class DeleteLibraryPayload(BaseModel):
    user_id: Optional[str] = None
    search_filter: Optional[SearchFilter] = None


class DeleteLibraryResponse(BaseModel):
    ok: bool
    deleted_count: int


def _normalize_library_overview(overview: Dict[str, Any]) -> LibraryResponse:
    semesters = []

    for semester, courses in sorted(overview.get("semesters", {}).items()):
        course_items = []

        for course, files in sorted(courses.items()):
            file_items = [
                LibraryFile(filename=filename, title=file_info.get("title", ""))
                for filename, file_info in sorted(files.items())
            ]
            course_items.append(LibraryCourse(course=course, files=file_items))

        semesters.append(LibrarySemester(semester=semester, courses=course_items))

    return LibraryResponse(total_chunks=overview.get("total_chunks", 0), semesters=semesters)


def _load_json_file(path: str) -> Any:
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_json_file(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _load_timetable() -> List[Dict[str, Any]]:
    if not os.path.exists(TIMETABLE_PATH):
        return []

    try:
        with open(TIMETABLE_PATH, "r", encoding="utf-8") as f:
            return [item for item in json.load(f) if isinstance(item, dict)]
    except (OSError, ValueError):
        return []


def _save_timetable(items: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(TIMETABLE_PATH), exist_ok=True)
    with open(TIMETABLE_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _load_recall_traces() -> List[Dict[str, Any]]:
    data = _load_json_file(RECALL_TRACES_PATH)
    return data if isinstance(data, list) else []


def _save_recall_traces(items: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(RECALL_TRACES_PATH), exist_ok=True)
    with open(RECALL_TRACES_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _load_recall_feedback_prompt() -> str:
    try:
        with open(RECALL_FEEDBACK_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return (
            "You are a SCiyl-inspired active learning feedback layer. "
            "Return JSON only with good_points, missing_links, followup_question, source_hint."
        )


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("response is not text")
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start:end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("response is not a JSON object")
    return parsed


def _normalize_feedback_payload(payload: Dict[str, Any]) -> RecallFeedbackResponse:
    def list_of_strings(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()][:5]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    return RecallFeedbackResponse(
        good_points=list_of_strings(payload.get("good_points")),
        missing_links=list_of_strings(payload.get("missing_links")),
        followup_question=str(payload.get("followup_question") or "").strip(),
        source_hint=str(payload.get("source_hint") or "").strip(),
    )


def _format_feedback_sources(chunks: List[Dict[str, Any]]) -> str:
    blocks = []
    for item in chunks[:8]:
        label = (
            f"과목:{item.get('course', '')} · 단원:{item.get('unit', '')} · "
            f"파일:{item.get('filename', '')} p.{item.get('page', '')}"
        )
        blocks.append(f"[{label}]\n{str(item.get('text', ''))[:1200]}")
    return "\n\n".join(blocks)


def _feedback_source_chunks(user_id: str, semester: str, course: str, unit: str, concept: str) -> List[Dict[str, Any]]:
    data = get_chunks(
        user_id=user_id,
        limit=500,
        offset=0,
        full=True,
        search_filter={"semester": semester, "course": course},
    )
    items = [item for item in data.get("items", []) if (item.get("unit") or "").strip() == unit]
    concept_norm = concept.replace(" ", "").lower()
    direct = [
        item for item in items
        if concept_norm and concept_norm in str(item.get("text", "")).replace(" ", "").lower()
    ]
    pool = direct or items
    pool.sort(key=lambda item: (item.get("filename", ""), item.get("page", 0), item.get("chunk_index", 0)))
    return pool[:8]


class TimetableEntry(BaseModel):
    semester: str
    day: str = ""
    time: str = ""
    course: str
    memo: str = ""


class RecallTraceCreate(BaseModel):
    user_id: Optional[str] = None
    semester: str
    course: str
    unit: str
    concept: str
    answer_text: str


class RecallTrace(BaseModel):
    id: str
    user_id: str
    semester: str
    course: str
    unit: str
    concept: str
    answer_text: str
    created_at: str
    feedback: Optional[Dict[str, Any]] = None
    feedback_created_at: Optional[str] = None


class RecallTraceResponse(BaseModel):
    ok: bool
    trace: RecallTrace


class RecallTraceListResponse(BaseModel):
    traces: List[RecallTrace]


class RecallFeedbackRequest(BaseModel):
    user_id: Optional[str] = None
    semester: str
    course: str
    unit: str
    concept: str
    answer_text: str
    trace_id: Optional[str] = None


class RecallFeedbackResponse(BaseModel):
    good_points: List[str]
    missing_links: List[str]
    followup_question: str
    source_hint: str



def _model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _concept_key(value: Any) -> str:
    return str(value or "").replace(" ", "").strip().lower()


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trace_matches(item: Dict[str, Any], user_id: str, semester: str, course: str, unit: str, concept: str) -> bool:
    return (
        str(item.get("user_id", "")).strip() == user_id
        and str(item.get("semester", "")).strip() == semester
        and str(item.get("course", "")).strip() == course
        and str(item.get("unit", "")).strip() == unit
        and _concept_key(item.get("concept")) == _concept_key(concept)
    )


def _score_recall_weakness(recall_count: int, last_recalled_at: Optional[str], missing_links_count: int) -> int:
    if recall_count <= 0:
        return 90

    score = 15
    last_dt = _parse_iso_datetime(last_recalled_at)
    if last_dt is None:
        score += 35
    else:
        age_days = (datetime.now(timezone.utc) - last_dt).days
        if age_days >= 21:
            score += 45
        elif age_days >= 14:
            score += 35
        elif age_days >= 7:
            score += 20

    score += min(40, max(0, missing_links_count) * 15)
    return max(0, min(100, score))


def _recall_metadata_for_scope(user_id: str, semester: str, course: str, unit: str) -> Dict[str, Dict[str, Any]]:
    metadata: Dict[str, Dict[str, Any]] = {}
    for item in _load_recall_traces():
        if not isinstance(item, dict):
            continue
        if not _trace_matches(item, user_id, semester, course, unit, item.get("concept", "")):
            continue

        key = _concept_key(item.get("concept"))
        if not key:
            continue

        entry = metadata.setdefault(key, {
            "recall_count": 0,
            "last_recalled_at": None,
            "missing_links_count": 0,
        })
        entry["recall_count"] += 1

        created_at = str(item.get("created_at") or "")
        if created_at and (not entry["last_recalled_at"] or created_at > entry["last_recalled_at"]):
            entry["last_recalled_at"] = created_at

        feedback = item.get("feedback")
        missing_links = feedback.get("missing_links") if isinstance(feedback, dict) else []
        if isinstance(missing_links, list):
            entry["missing_links_count"] += len([x for x in missing_links if str(x).strip()])

    return metadata


def _augment_concepts_with_recall(
    concepts: List[Dict[str, Any]],
    user_id: str,
    semester: str,
    course: str,
    unit: str,
) -> List[Dict[str, Any]]:
    metadata = _recall_metadata_for_scope(user_id, semester, course, unit)
    augmented: List[Dict[str, Any]] = []

    for concept in concepts:
        item = dict(concept)
        key = _concept_key(item.get("name") or item.get("keyword"))
        meta = metadata.get(key, {})
        recall_count = int(meta.get("recall_count") or 0)
        last_recalled_at = meta.get("last_recalled_at")
        missing_links_count = int(meta.get("missing_links_count") or 0)
        item["recall_count"] = recall_count
        item["last_recalled_at"] = last_recalled_at
        item["missing_links_count"] = missing_links_count
        item["weak_score"] = _score_recall_weakness(recall_count, last_recalled_at, missing_links_count)
        augmented.append(item)

    return augmented


def _persist_recall_feedback(payload: RecallFeedbackRequest, feedback: RecallFeedbackResponse) -> None:
    traces = _load_recall_traces()
    feedback_data = _model_to_dict(feedback)
    matched_index: Optional[int] = None
    trace_id = (payload.trace_id or "").strip()

    if trace_id:
        for index, item in enumerate(traces):
            if isinstance(item, dict) and str(item.get("feedback_type") or ""):
                continue
            if isinstance(item, dict) and str(item.get("id", "")).strip() == trace_id:
                matched_index = index
                break

    if matched_index is None:
        for index, item in enumerate(traces):
            if not isinstance(item, dict) or str(item.get("feedback_type") or ""):
                continue
            if not _trace_matches(
                item,
                payload.user_id.strip(),
                payload.semester.strip(),
                payload.course.strip(),
                payload.unit.strip(),
                payload.concept.strip(),
            ):
                continue
            if str(item.get("answer_text", "")).strip() != payload.answer_text.strip():
                continue
            if matched_index is None or str(item.get("created_at", "")) > str(traces[matched_index].get("created_at", "")):
                matched_index = index

    created_at = datetime.now(timezone.utc).isoformat()
    feedback_record = {
        "id": uuid.uuid4().hex,
        "user_id": payload.user_id.strip(),
        "semester": payload.semester.strip(),
        "course": payload.course.strip(),
        "unit": payload.unit.strip(),
        "concept": payload.concept.strip(),
        "answer_text": payload.answer_text.strip(),
        "feedback": feedback_data,
        "feedback_text": json.dumps(feedback_data, ensure_ascii=False),
        "feedback_type": "explain_concept",
        "source_trace_id": trace_id or (traces[matched_index].get("id") if matched_index is not None else ""),
        "created_at": created_at,
    }

    if matched_index is not None:
        traces[matched_index]["feedback"] = feedback_data
        traces[matched_index]["feedback_created_at"] = created_at

    traces.append(feedback_record)
    _save_recall_traces(traces)


def _is_uuid_like(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        uuid.UUID(raw)
        return True
    except ValueError:
        pass
    try:
        uuid.UUID(raw.replace("-", ""))
        return len(raw.replace("-", "")) == 32
    except ValueError:
        return False


def _safe_list_json(path: str) -> List[Dict[str, Any]]:
    data = _load_json_file(path)
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _iter_user_concepts(data: Any, user_id: str) -> List[Dict[str, Any]]:
    concepts: List[Dict[str, Any]] = []
    user_data = data.get(user_id, {}) if isinstance(data, dict) else {}
    if not isinstance(user_data, dict):
        return concepts
    for semester_data in user_data.values():
        if not isinstance(semester_data, dict):
            continue
        for course_data in semester_data.values():
            if not isinstance(course_data, dict):
                continue
            for unit_concepts in course_data.values():
                if isinstance(unit_concepts, list):
                    concepts.extend([item for item in unit_concepts if isinstance(item, dict)])
    return concepts


def _library_summary_for_user(user_id: str) -> Dict[str, Any]:
    overview = get_library_overview(user_id=user_id)
    semesters = overview.get("semesters", {}) if isinstance(overview, dict) else {}
    filenames = set()
    courses = set()
    recent_uploads_by_file: Dict[str, Dict[str, Any]] = {}

    if isinstance(semesters, dict):
        for semester, semester_courses in semesters.items():
            if not isinstance(semester_courses, dict):
                continue
            for course, files in semester_courses.items():
                courses.add((str(semester), str(course)))
                if not isinstance(files, dict):
                    continue
                for filename, info in files.items():
                    filenames.add(str(filename))
                    recent_uploads_by_file[str(filename)] = {
                        "semester": semester,
                        "course": course,
                        "filename": filename,
                        "title": info.get("title", "") if isinstance(info, dict) else "",
                    }

    chunk_data = get_chunks(user_id=user_id, limit=5000, offset=0, full=False)
    units = {
        (str(item.get("semester") or ""), str(item.get("course") or ""), str(item.get("unit") or ""))
        for item in chunk_data.get("items", [])
        if str(item.get("unit") or "").strip()
    }

    recent_uploads = list(recent_uploads_by_file.values())[-5:]
    recent_uploads.reverse()
    return {
        "pdf_count": len(filenames),
        "course_count": len(courses),
        "unit_count": len(units),
        "recent_uploads": recent_uploads,
    }


def _learning_summary_for_user(user_id: str) -> Dict[str, Any]:
    concepts = _iter_user_concepts(_load_json_file(CONCEPTS_PATH), user_id)
    graph_nodes = [item for item in _safe_list_json(CONCEPT_INDEX_PATH) if item.get("user_id") == user_id]
    node_ids = {str(item.get("id")) for item in graph_nodes if item.get("id")}
    links_data = _load_json_file(CONCEPT_LINKS_PATH)
    graph_edges = []
    if isinstance(links_data, dict):
        graph_edges = [
            edge for edge in links_data.get("edges", [])
            if isinstance(edge, dict) and str(edge.get("a")) in node_ids and str(edge.get("b")) in node_ids
        ]

    traces = [item for item in _load_recall_traces() if item.get("user_id") == user_id]
    explanation_traces = [item for item in traces if not str(item.get("feedback_type") or "")]
    feedback_records = [item for item in traces if item.get("feedback_type") == "explain_concept"]
    concepts_explained = {
        _concept_key(item.get("concept")) for item in feedback_records if _concept_key(item.get("concept"))
    }
    last_explained_at = max([str(item.get("created_at") or "") for item in feedback_records] or ["",]) or None
    last_studied_at = max(
        [str(item.get("created_at") or "") for item in (feedback_records or explanation_traces)] or ["",]
    ) or None

    recent = sorted(feedback_records, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:5]
    recent_explanations = [
        {
            "id": item.get("id", ""),
            "semester": item.get("semester", ""),
            "course": item.get("course", ""),
            "unit": item.get("unit", ""),
            "concept": item.get("concept", ""),
            "answer_text": item.get("answer_text", ""),
            "feedback_text": item.get("feedback_text", ""),
            "feedback_type": item.get("feedback_type", "explain_concept"),
            "created_at": item.get("created_at"),
        }
        for item in recent
    ]

    return {
        "concept_count": len(concepts),
        "graph_node_count": len(graph_nodes),
        "graph_edge_count": len(graph_edges),
        "explanation_feedback_count": len(feedback_records),
        "concepts_explained_count": len(concepts_explained),
        "last_explained_at": last_explained_at,
        "last_studied_at": last_studied_at,
        "recent_explanations": recent_explanations,
    }

def _build_timetable_response(uid: str) -> TimetableResponse:
    timetable = [e for e in _load_timetable() if e.get("user_id") == uid]
    grouped: Dict[str, set] = {}

    for item in timetable:
        semester = item.get("semester") or "학기 미정"
        course = item.get("course")

        if not course:
            continue

        grouped.setdefault(semester, set()).add(course)

    semesters = [
        TimetableSemester(semester=semester, courses=sorted(courses))
        for semester, courses in sorted(grouped.items())
    ]

    return TimetableResponse(semesters=semesters)


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest, data_user_id: str = Depends(current_uid)) -> AskResponse:
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question이 필요합니다.")

    if request.mode == "connections":
        answer, sources = answer_with_connections(
            request.question,
            search_filter=request.search_filter.dict() if request.search_filter else None,
            search_scope_label=get_filter_label(request.search_filter.dict() if request.search_filter else None),
            user_id=data_user_id,
        )
    else:
        answer, sources = answer_question(
            request.question,
            search_filter=request.search_filter.dict() if request.search_filter else None,
            search_scope_label=get_filter_label(request.search_filter.dict() if request.search_filter else None),
            user_id=data_user_id,
        )

    scope_label = get_filter_label(request.search_filter.dict() if request.search_filter else None)

    return AskResponse(answer=answer, sources=sources, scope_label=scope_label)


@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    semester: str = Form(...),
    course: str = Form(...),
    title: str = Form(...),
    unit: str = Form(""),
    file: UploadFile = File(...),
    data_user_id: str = Depends(current_uid),
) -> IngestResponse:
    filename = os.path.basename(file.filename or "")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있습니다.")

    unique_name = f"{uuid.uuid4().hex}_{filename}"
    destination = os.path.join(UPLOAD_DIR, unique_name)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="빈 PDF 파일입니다.")

    with open(destination, "wb") as f:
        f.write(content)

    pages = extract_pdf_text(destination)
    if not pages:
        return IngestResponse(ok=False, filename=filename, pages=0, message="PDF에서 텍스트를 추출하지 못했습니다.")

    add_pdf_pages_to_db(
        pages=pages,
        filename=filename,
        semester=semester.strip(),
        course=course.strip(),
        title=title.strip(),
        user_id=data_user_id,
        unit=unit.strip(),
    )

    # 업로드 시 해당 단원 개념 자동 추출(개념 지도용). 실패해도 업로드는 성공 처리.
    if unit.strip():
        try:
            cs = build_concepts_for_unit(data_user_id, semester.strip(), course.strip(), unit.strip())
            if cs:
                cdata = _load_json_file(CONCEPTS_PATH)
                cdata.setdefault(data_user_id, {}).setdefault(semester.strip(), {}).setdefault(course.strip(), {})[unit.strip()] = cs
                _save_json_file(CONCEPTS_PATH, cdata)
        except Exception:
            pass

    return IngestResponse(ok=True, filename=filename, pages=len(pages), message="학습 완료")


@app.get("/library", response_model=LibraryResponse)
async def library(data_user_id: str = Depends(current_uid)) -> LibraryResponse:
    overview = get_library_overview(user_id=data_user_id)
    return _normalize_library_overview(overview)


@app.delete("/library", response_model=DeleteLibraryResponse)
async def delete_library(payload: DeleteLibraryPayload, data_user_id: str = Depends(current_uid)) -> DeleteLibraryResponse:
    deleted_count = delete_chunks_by_filter(
        search_filter=payload.search_filter.dict() if payload.search_filter else None,
        user_id=data_user_id,
    )
    return DeleteLibraryResponse(ok=True, deleted_count=deleted_count)


@app.get("/timetable", response_model=TimetableResponse)
async def timetable(data_user_id: str = Depends(current_uid)) -> TimetableResponse:
    return _build_timetable_response(data_user_id)


@app.get("/timetable/entries")
async def timetable_entries(data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    items = _load_timetable()
    return {"entries": [e for e in items if e.get("user_id") == data_user_id]}


@app.post("/timetable/entries")
async def add_timetable_entry(entry: TimetableEntry, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    if not entry.semester.strip() or not entry.course.strip():
        raise HTTPException(status_code=400, detail="학기와 과목명은 필수입니다.")
    items = _load_timetable()
    items.append({
        "user_id": data_user_id,
        "semester": entry.semester.strip(),
        "day": entry.day.strip(),
        "time": entry.time.strip(),
        "course": entry.course.strip(),
        "memo": entry.memo.strip(),
    })
    _save_timetable(items)
    return {"ok": True}


@app.delete("/timetable/entries")
async def delete_timetable_entry(index: Optional[int] = None, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    items = _load_timetable()
    mine = [i for i, e in enumerate(items) if e.get("user_id") == data_user_id]
    if index is None:
        items = [e for e in items if e.get("user_id") != data_user_id]
        _save_timetable(items)
        return {"ok": True, "count": 0}
    if index < 0 or index >= len(mine):
        raise HTTPException(status_code=400, detail="잘못된 index입니다.")
    items.pop(mine[index])
    _save_timetable(items)
    return {"ok": True}


@app.put("/timetable/entries")
async def update_timetable_entry(index: int, entry: TimetableEntry, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    if not entry.semester.strip() or not entry.course.strip():
        raise HTTPException(status_code=400, detail="학기와 과목명은 필수입니다.")
    items = _load_timetable()
    mine = [i for i, e in enumerate(items) if e.get("user_id") == data_user_id]
    if index < 0 or index >= len(mine):
        raise HTTPException(status_code=400, detail="잘못된 index입니다.")
    items[mine[index]] = {
        "user_id": data_user_id,
        "semester": entry.semester.strip(),
        "day": entry.day.strip(),
        "time": entry.time.strip(),
        "course": entry.course.strip(),
        "memo": entry.memo.strip(),
    }
    _save_timetable(items)
    return {"ok": True}


@app.get("/units")
async def units(semester: str, course: str, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    if not semester.strip() or not course.strip():
        raise HTTPException(status_code=400, detail="semester, course가 필요합니다.")

    units = get_units(user_id=data_user_id, semester=semester, course=course)

    if not units:
        return {"status": "empty", "units": []}

    return {"status": "ready", "units": units}


@app.post("/reindex-concepts")
async def reindex_concepts(payload: dict, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    user_id = data_user_id
    semester = (payload.get("semester") or "").strip()
    course = (payload.get("course") or "").strip()
    unit = payload.get("unit")

    if not semester or not course:
        raise HTTPException(status_code=400, detail="semester, course가 필요합니다.")

    concepts_data = _load_json_file(CONCEPTS_PATH)
    user_data = concepts_data.setdefault(user_id, {})
    semester_data = user_data.setdefault(semester, {})
    course_data = semester_data.setdefault(course, {})

    units = get_units(user_id=user_id, semester=semester, course=course)
    target_units = []

    if unit and unit.strip():
        target_units = [unit.strip()]
    else:
        target_units = [item["unit"] for item in units if item.get("unit")]

    indexed = 0
    total_concepts = 0

    for target_unit in target_units:
        try:
            concepts = build_concepts_for_unit(
                user_id=user_id,
                semester=semester,
                course=course,
                unit=target_unit,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "개념 추출 중 오류가 발생했습니다. "
                    "OPENAI_API_KEY가 설정되어 있는지 확인하고 다시 시도해주세요."
                ),
            ) from exc

        course_data[target_unit] = concepts
        total_concepts += len(concepts)
        indexed += 1

    _save_json_file(CONCEPTS_PATH, concepts_data)

    return {
        "ok": True,
        "units_indexed": indexed,
        "concepts_count": total_concepts,
    }


@app.get("/concepts")
async def concepts(semester: str, course: str, unit: str, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    if not semester.strip() or not course.strip() or not unit.strip():
        raise HTTPException(status_code=400, detail="semester, course, unit이 필요합니다.")

    concepts_data = _load_json_file(CONCEPTS_PATH)
    concepts = (
        concepts_data
        .get(data_user_id, {})
        .get(semester, {})
        .get(course, {})
        .get(unit, [])
    )

    if not concepts:
        return {"status": "empty", "concepts": []}

    return {"status": "ready", "concepts": _augment_concepts_with_recall(concepts, data_user_id, semester, course, unit)}


@app.post("/recall-traces", response_model=RecallTraceResponse)
async def create_recall_trace(
    payload: RecallTraceCreate,
    data_user_id: str = Depends(current_uid),
) -> RecallTraceResponse:
    user_id = data_user_id
    semester = payload.semester.strip()
    course = payload.course.strip()
    unit = payload.unit.strip()
    concept = payload.concept.strip()
    answer_text = payload.answer_text.strip()

    if not all([user_id, semester, course, unit, concept, answer_text]):
        raise HTTPException(
            status_code=400,
            detail="user_id, semester, course, unit, concept, answer_text가 필요합니다.",
        )

    trace = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "semester": semester,
        "course": course,
        "unit": unit,
        "concept": concept,
        "answer_text": answer_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    traces = _load_recall_traces()
    traces.append(trace)
    _save_recall_traces(traces)
    return RecallTraceResponse(ok=True, trace=RecallTrace(**trace))


@app.get("/recall-traces", response_model=RecallTraceListResponse)
async def recall_traces(
    semester: str,
    course: str,
    unit: str,
    concept: Optional[str] = None,
    limit: int = 20,
    data_user_id: str = Depends(current_uid),
) -> RecallTraceListResponse:
    filters = {
        "user_id": data_user_id,
        "semester": semester.strip(),
        "course": course.strip(),
        "unit": unit.strip(),
    }
    if not all(filters.values()):
        raise HTTPException(status_code=400, detail="user_id, semester, course, unit이 필요합니다.")

    concept_filter = (concept or "").strip()
    traces = []
    for item in _load_recall_traces():
        if not isinstance(item, dict) or str(item.get("feedback_type") or ""):
            continue
        if any(str(item.get(key, "")) != value for key, value in filters.items()):
            continue
        if concept_filter and str(item.get("concept", "")) != concept_filter:
            continue
        traces.append(item)

    traces.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    safe_limit = max(1, min(limit, 100))
    return RecallTraceListResponse(traces=[RecallTrace(**item) for item in traces[:safe_limit]])


@app.post("/recall-feedback", response_model=RecallFeedbackResponse)
async def recall_feedback(
    payload: RecallFeedbackRequest,
    data_user_id: str = Depends(current_uid),
) -> RecallFeedbackResponse:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY가 설정되지 않아 recall feedback을 생성할 수 없습니다.",
        )

    user_id = data_user_id
    semester = payload.semester.strip()
    course = payload.course.strip()
    unit = payload.unit.strip()
    concept = payload.concept.strip()
    answer_text = payload.answer_text.strip()

    if not all([user_id, semester, course, unit, concept, answer_text]):
        raise HTTPException(
            status_code=400,
            detail="user_id, semester, course, unit, concept, answer_text가 필요합니다.",
        )

    source_chunks = _feedback_source_chunks(user_id, semester, course, unit, concept)
    if not source_chunks:
        raise HTTPException(status_code=400, detail="피드백에 사용할 관련 자료 chunk를 찾지 못했습니다.")

    prompt = f"""{_load_recall_feedback_prompt()}

[학습 컨텍스트]
- user_id: {user_id}
- semester: {semester}
- course: {course}
- unit: {unit}
- concept: {concept}

[사용자 설명]
{answer_text}

[자료 근거]
{_format_feedback_sources(source_chunks)}
"""

    try:
        raw_feedback = generate_openai_answer(prompt, max_tokens=700)
        feedback = _normalize_feedback_payload(_extract_json_object(raw_feedback))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"recall feedback 생성에 실패했습니다: {exc}") from exc

    if not feedback.followup_question:
        feedback.followup_question = "이 개념이 단원 전체 흐름에서 어떤 역할을 하는지 한 문장으로 다시 설명해볼까요?"
    if not feedback.source_hint:
        first = source_chunks[0]
        feedback.source_hint = f"{first.get('course', course)} · {first.get('unit', unit)} · {first.get('filename', '')} p.{first.get('page', '')} 근처를 다시 보세요."

    payload_for_persist = payload.copy(update={"user_id": data_user_id})
    try:
        _persist_recall_feedback(payload_for_persist, feedback)
    except Exception:
        logger.exception("Failed to persist explanation feedback for user_id=%s", data_user_id)
    return feedback


@app.get("/chunks")
async def chunks(
    limit: int = 50,
    offset: int = 0,
    full: bool = False,
    semester: Optional[str] = None,
    course: Optional[str] = None,
    filename: Optional[str] = None,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    search_filter = None
    if semester or course or filename:
        search_filter = {"semester": semester, "course": course, "filename": filename}

    return get_chunks(
        user_id=data_user_id,
        limit=limit,
        offset=offset,
        full=full,
        search_filter=search_filter,
    )


def _uid_from_token(token: str) -> str:
    uid = _auth.verify_token((token or "").replace("Bearer ", "").strip())
    if not uid:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user = _auth.get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return user.get("data_user_id") or user["email"]


@app.get("/file")
async def serve_file(filename: str, token: str = ""):
    """업로드된 원본 PDF 자체를 반환 (iframe 미리보기용). 토큰은 쿼리로 받음."""
    _uid_from_token(token)
    want = unicodedata.normalize("NFC", os.path.basename(filename))
    for fn in os.listdir(UPLOAD_DIR):
        base = unicodedata.normalize("NFC", fn)
        if base == want or base.endswith("_" + want):
            return FileResponse(os.path.join(UPLOAD_DIR, fn), media_type="application/pdf")
    raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")


@app.post("/rename-unit")
async def rename_unit_ep(payload: dict, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    sem = (payload.get("semester") or "").strip()
    course = (payload.get("course") or "").strip()
    old = (payload.get("old_unit") or "").strip()
    new = (payload.get("new_unit") or "").strip()
    if not (sem and course and old and new):
        raise HTTPException(status_code=400, detail="semester, course, old_unit, new_unit 가 필요합니다.")
    n = rename_unit(data_user_id, sem, course, old, new)
    cdata = _load_json_file(CONCEPTS_PATH)
    try:
        units = cdata[data_user_id][sem][course]
        if old in units:
            units[new] = units.pop(old)
            _save_json_file(CONCEPTS_PATH, cdata)
    except Exception:
        pass
    return {"ok": True, "updated_chunks": n}


@app.post("/reindex-graph")
async def reindex_graph(payload: dict, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """개념 임베딩 및 과목 간 연결 생성"""
    user_id = data_user_id
    
    # concepts.json이 있는지 확인
    concepts_path = CONCEPTS_PATH
    if not os.path.exists(concepts_path):
        raise HTTPException(
            status_code=400,
            detail="먼저 /reindex-concepts로 개념을 추출해주세요."
        )
    
    with open(concepts_path, "r", encoding="utf-8") as f:
        concepts_data = json.load(f)
    
    if user_id not in concepts_data:
        raise HTTPException(
            status_code=400,
            detail=f"user_id '{user_id}'에 대한 개념 데이터가 없습니다."
        )
    
    try:
        # 1단계: 개념 임베딩 생성
        embeddings_index = build_concept_embeddings(user_id)
        if not embeddings_index:
            raise HTTPException(
                status_code=500,
                detail="개념 임베딩 생성 실패"
            )
        
        # 2단계: cross-link 생성
        cross_edges = build_cross_links(
            user_id=user_id,
            threshold=0.45,
            top_k=5
        )
        
        return {
            "ok": True,
            "concepts": len(embeddings_index),
            "cross_edges": len(cross_edges),
        }
    
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"그래프 생성 중 오류 발생: {str(exc)}"
        ) from exc


@app.get("/concept-graph")
async def concept_graph(semester: Optional[str] = None, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """개념 그래프 조회 (과목별 색상, 교차 연결선 포함)"""
    user_id = data_user_id
    
    index_path = os.path.join(DATA_DIR, "concept_index.json")
    links_path = os.path.join(DATA_DIR, "concept_links.json")
    
    if not os.path.exists(index_path) or not os.path.exists(links_path):
        raise HTTPException(
            status_code=400,
            detail="먼저 /reindex-graph로 그래프를 생성해주세요."
        )
    
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            embeddings_index = json.load(f)
        
        with open(links_path, "r", encoding="utf-8") as f:
            links_data = json.load(f)
        
        # user_id 필터링
        user_nodes = [
            {
                "id": e["id"],
                "name": e["name"],
                "keyword": e["keyword"],
                "course": e["course"],
                "unit": e["unit"],
                "semester": e.get("semester", ""),
                "weight": e["weight"],
            }
            for e in embeddings_index if e.get("user_id") == user_id
        ]
        
        # semester 필터링 (선택적)
        if semester and semester.strip():
            user_nodes = [n for n in user_nodes if n.get("semester") == semester.strip()]
        
        graph_meta: Dict[str, Dict[str, Any]] = {}
        for node in user_nodes:
            node_semester = str(node.get("semester") or semester or "")
            meta = _recall_metadata_for_scope(user_id, node_semester, str(node.get("course") or ""), str(node.get("unit") or ""))
            graph_meta.update({
                (node_semester, str(node.get("course") or ""), str(node.get("unit") or ""), key): value
                for key, value in meta.items()
            })

        for node in user_nodes:
            node_semester = str(node.get("semester") or semester or "")
            meta = graph_meta.get((node_semester, str(node.get("course") or ""), str(node.get("unit") or ""), _concept_key(node.get("name") or node.get("keyword"))), {})
            recall_count = int(meta.get("recall_count") or 0)
            last_recalled_at = meta.get("last_recalled_at")
            missing_links_count = int(meta.get("missing_links_count") or 0)
            node["recall_count"] = recall_count
            node["last_recalled_at"] = last_recalled_at
            node["missing_links_count"] = missing_links_count
            node["weak_score"] = _score_recall_weakness(recall_count, last_recalled_at, missing_links_count)

        # cross-link 노드 ID 검증
        node_ids = set(n["id"] for n in user_nodes)
        edges = [
            e for e in links_data.get("edges", [])
            if e["a"] in node_ids and e["b"] in node_ids
        ]
        
        return {
            "nodes": user_nodes,
            "edges": edges,
        }
    
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"그래프 조회 중 오류 발생: {str(exc)}"
        ) from exc



@app.get("/me/summary")
async def me_summary(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    data_user_id = user.get("data_user_id") or user["email"]
    email = str(user.get("email") or "").lower()
    is_maintainer = email == MAINTAINER_EMAIL
    is_legacy_namespace = not _is_uuid_like(str(data_user_id))
    public = _auth.public_user(user)

    account = {
        **public,
        "is_maintainer": is_maintainer,
        "is_legacy_namespace": is_legacy_namespace,
        "namespace_label": "Legacy user_id" if is_legacy_namespace else "UUID data_user_id",
        "migration_status": (
            "Existing data namespace linked"
            if is_maintainer and is_legacy_namespace
            else "Isolated data namespace"
        ),
    }

    return {
        "account": account,
        "library": _library_summary_for_user(data_user_id),
        "learning": _learning_summary_for_user(data_user_id),
    }


# ============== 계정 / 인증 (Stage C-1) ==============
from fastapi import Header
import auth as _auth


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = ""
    link_user_id: Optional[str] = ""


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/register")
async def auth_register(req: RegisterRequest) -> Dict[str, Any]:
    user, err = _auth.register_user(req.email, req.password, req.display_name or "", req.link_user_id or "")
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"token": _auth.create_token(user["id"]), "user": _auth.public_user(user)}


@app.post("/auth/login")
async def auth_login(req: LoginRequest) -> Dict[str, Any]:
    user, err = _auth.authenticate(req.email, req.password)
    if err:
        raise HTTPException(status_code=401, detail=err)
    return {"token": _auth.create_token(user["id"]), "user": _auth.public_user(user)}


@app.get("/auth/me")
async def auth_me(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    token = (authorization or "").replace("Bearer ", "").strip()
    uid = _auth.verify_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user = _auth.get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return {"user": _auth.public_user(user)}


# ============== Google 로그인 (Stage C-1) ==============
import urllib.request as _urlreq
import urllib.parse as _urlparse

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")


class GoogleRequest(BaseModel):
    credential: str
    link_user_id: Optional[str] = ""


@app.get("/auth/config")
async def auth_config() -> Dict[str, Any]:
    # 프론트가 GIS 초기화에 쓸 공개 Client ID (비밀 아님)
    return {"google_client_id": GOOGLE_CLIENT_ID}


def _verify_google_idtoken(credential: str) -> Dict[str, Any]:
    url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + _urlparse.quote(credential)
    with _urlreq.urlopen(url, timeout=10) as resp:
        return json.load(resp)


@app.post("/auth/google")
async def auth_google(req: GoogleRequest) -> Dict[str, Any]:
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="서버에 GOOGLE_CLIENT_ID가 설정되지 않았습니다(.env).")
    try:
        info = _verify_google_idtoken(req.credential)
    except Exception:
        raise HTTPException(status_code=401, detail="Google 토큰 검증에 실패했습니다.")
    if info.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Google 클라이언트 ID가 일치하지 않습니다.")
    email = info.get("email")
    if not email or str(info.get("email_verified", "")).lower() not in ("true", "1"):
        raise HTTPException(status_code=401, detail="이메일을 확인할 수 없습니다.")
    try:
        user = _auth.upsert_google_user(email, info.get("sub", ""), info.get("name", ""), req.link_user_id or "")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"token": _auth.create_token(user["id"]), "user": _auth.public_user(user)}


# ============== 정적 프론트엔드 서빙 (Stage A) ==============
# FastAPI가 web/ 폴더를 직접 서빙한다.
#   http://127.0.0.1:8000/            -> gallery.html (메인 앱)
#   http://127.0.0.1:8000/index.html  -> 업로드/질문 화면
# API 라우트(/ask, /library 등)는 위에서 먼저 등록되어 우선 매칭된다.
import os as _os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_WEB_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "web")


@app.get("/")
async def _serve_gallery():
    return FileResponse(_os.path.join(_WEB_DIR, "gallery.html"))


# 정적 파일 폴백(맨 마지막에 등록 → API 라우트보다 후순위)
app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
