from __future__ import annotations

import json
import logging
import os
import re
import hashlib
import uuid
from collections import Counter
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
LEARNING_MEMORY_SUMMARIES_PATH = os.path.join(DATA_DIR, "learning_memory_summaries.json")
CLINICAL_REFLECTIONS_PATH = os.path.join(DATA_DIR, "clinical_reflections.json")
SEARCH_CACHE_PATH = os.path.join(DATA_DIR, "search_cache.json")
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
    unit: Optional[str] = None
    filename: Optional[str] = None


class AskRequest(BaseModel):
    # Deprecated compatibility field; ownership is derived from current_uid().
    user_id: Optional[str] = None
    question: str
    mode: Optional[str] = "single"
    search_filter: Optional[SearchFilter] = None


class AskResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]
    scope_label: str


class AskSearchRequest(BaseModel):
    question: str
    search_filter: Optional[SearchFilter] = None
    scope: Optional[str] = "auto"
    limit: Optional[int] = 5


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
    # Deprecated compatibility field; ownership is derived from current_uid().
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


def _load_learning_memory_summaries() -> List[Dict[str, Any]]:
    data = _load_json_file(LEARNING_MEMORY_SUMMARIES_PATH)
    return data if isinstance(data, list) else []


def _save_learning_memory_summaries(items: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(LEARNING_MEMORY_SUMMARIES_PATH), exist_ok=True)
    with open(LEARNING_MEMORY_SUMMARIES_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _load_clinical_reflections() -> List[Dict[str, Any]]:
    data = _load_json_file(CLINICAL_REFLECTIONS_PATH)
    return data if isinstance(data, list) else []


def _save_clinical_reflections(items: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(CLINICAL_REFLECTIONS_PATH), exist_ok=True)
    with open(CLINICAL_REFLECTIONS_PATH, "w", encoding="utf-8") as f:
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
    # Deprecated compatibility field; ownership is derived from current_uid().
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
    # Deprecated compatibility field; ownership is derived from current_uid().
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


class LearningMemoryAiSummaryRequest(BaseModel):
    summary_type: Optional[str] = "weekly"
    course: Optional[str] = None
    unit: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    concepts: Optional[List[str]] = None
    max_items: Optional[int] = 30


class LearningMemoryAiSummaryResponse(BaseModel):
    summary_type: str
    title: str
    summary: str
    review_focus: List[str]
    weak_concepts: List[str]
    suggested_questions: List[str]
    source_memory_ids: List[str]
    created_at: str


class LearningMemoryAiSummariesResponse(BaseModel):
    items: List[Dict[str, Any]]


class ClinicalReflectionRequest(BaseModel):
    situation_text: str
    learning_goal: str = ""
    selected_course: Optional[str] = None
    selected_unit: Optional[str] = None
    mode: Optional[str] = "reflection"


class ClinicalReflectionFeedback(BaseModel):
    knowledge_connections: List[str]
    nursing_process_links: List[str]
    missed_assessment_cues: List[str]
    safe_next_questions: List[str]
    review_focus: List[str]
    source_hints: List[str]
    educational_summary: str


class ClinicalReflectionResponse(BaseModel):
    id: str
    user_id: str
    student_track: str
    situation_text: str
    learning_goal: str
    selected_course: Optional[str] = None
    selected_unit: Optional[str] = None
    related_concepts: List[Dict[str, Any]]
    related_sources: List[Dict[str, Any]]
    feedback: ClinicalReflectionFeedback
    safety_flags: List[str]
    created_at: str


class ClinicalReflectionListResponse(BaseModel):
    items: List[Dict[str, Any]]



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


def _feedback_object(item: Dict[str, Any]) -> Dict[str, Any]:
    feedback = item.get("feedback")
    if isinstance(feedback, dict):
        return feedback
    raw = item.get("feedback_text")
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _learning_list_field(item: Dict[str, Any], key: str) -> List[str]:
    value = item.get(key)
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    feedback = _feedback_object(item)
    value = feedback.get(key)
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _learning_text_field(item: Dict[str, Any], key: str) -> str:
    value = str(item.get(key) or "").strip()
    if value and not (key == "feedback_text" and value.startswith("{")):
        return value
    feedback = _feedback_object(item)
    value = str(feedback.get(key) or "").strip()
    if value:
        return value
    if key == "feedback_text" and feedback:
        parts = []
        good = _learning_list_field(item, "good_points")
        missing = _learning_list_field(item, "missing_links")
        followup = _learning_text_field(item, "followup_question")
        if good:
            parts.append("좋았던 점: " + ", ".join(good))
        if missing:
            parts.append("더 연결해볼 점: " + ", ".join(missing))
        if followup:
            parts.append("질문: " + followup)
        return " / ".join(parts)
    return ""


def _learning_has_ai_feedback(item: Dict[str, Any]) -> bool:
    if str(item.get("feedback_type") or "") == "explain_concept":
        return True
    if _feedback_object(item):
        return True
    for key in ["feedback_text", "good_points", "missing_links", "followup_question", "improved_summary", "review_hint", "source_hint"]:
        if _learning_list_field(item, key) or _learning_text_field(item, key):
            return True
    return False


def _learning_memory_item(item: Dict[str, Any]) -> Dict[str, Any]:
    has_ai_feedback = _learning_has_ai_feedback(item)
    feedback_created_at = str(item.get("feedback_created_at") or "")
    if not feedback_created_at and str(item.get("feedback_type") or "") == "explain_concept":
        feedback_created_at = str(item.get("created_at") or "")
    return {
        "id": str(item.get("id") or ""),
        "semester": str(item.get("semester") or ""),
        "course": str(item.get("course") or ""),
        "unit": str(item.get("unit") or ""),
        "concept": str(item.get("concept") or ""),
        "answer_text": str(item.get("answer_text") or ""),
        "feedback_text": _learning_text_field(item, "feedback_text"),
        "good_points": _learning_list_field(item, "good_points"),
        "missing_links": _learning_list_field(item, "missing_links"),
        "followup_question": _learning_text_field(item, "followup_question"),
        "improved_summary": _learning_text_field(item, "improved_summary"),
        "review_hint": _learning_text_field(item, "review_hint"),
        "source_hint": _learning_text_field(item, "source_hint"),
        "has_ai_feedback": has_ai_feedback,
        "created_at": item.get("created_at") or "",
        "feedback_created_at": feedback_created_at,
    }


def _learning_memory_items_for_user(
    user_id: str,
    course: Optional[str] = None,
    unit: Optional[str] = None,
    concept: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    course_filter = (course or "").strip()
    unit_filter = (unit or "").strip()
    concept_filter = (concept or "").strip().lower()
    items = []
    for item in _load_recall_traces():
        if not isinstance(item, dict) or item.get("user_id") != user_id:
            continue
        memory = _learning_memory_item(item)
        if course_filter and memory["course"] != course_filter:
            continue
        if unit_filter and memory["unit"] != unit_filter:
            continue
        if concept_filter:
            haystack = " ".join([
                memory["concept"],
                memory["answer_text"],
                memory["feedback_text"],
                memory["improved_summary"],
                " ".join(memory["missing_links"]),
            ]).lower()
            if concept_filter not in haystack:
                continue
        items.append(memory)
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items[:max(1, min(limit, 200))]


def _learning_memory_summary_for_user(user_id: str) -> Dict[str, Any]:
    memories = _learning_memory_items_for_user(user_id, limit=5000)
    concept_keys = {_concept_key(item.get("concept")) for item in memories if _concept_key(item.get("concept"))}
    missing_counter: Counter[str] = Counter()
    weak_counter: Counter[str] = Counter()
    for item in memories:
        missing_links = [x for x in item.get("missing_links", []) if str(x).strip()]
        missing_counter.update(missing_links)
        if item.get("concept") and missing_links:
            weak_counter[str(item.get("concept"))] += len(missing_links)

    frequent_missing_links = [
        {"name": name, "count": count}
        for name, count in missing_counter.most_common(10)
    ]
    weak_concepts = [
        {"concept": name, "issue_count": count}
        for name, count in weak_counter.most_common(10)
    ]
    recent_memories = memories[:5]
    if recent_memories:
        courses = sorted({m.get("course") for m in recent_memories if m.get("course")})
        weekly_summary = (
            f"최근 {len(recent_memories)}개의 복습 메모리가 있습니다. "
            f"{', '.join(courses[:3]) or '여러 과목'} 자료를 다시 보며 missing links와 follow-up question을 확인하세요."
        )
    else:
        weekly_summary = "아직 저장된 Learning Memory가 없습니다. 개념 지도에서 설명해보기를 사용하면 복습 메모리가 쌓입니다."
    exam_review_focus = [x["name"] for x in frequent_missing_links[:5]] or [x["concept"] for x in weak_concepts[:5]]
    return {
        "total_memories": len(memories),
        "concepts_explained": len(concept_keys),
        "weak_concepts": weak_concepts,
        "frequent_missing_links": frequent_missing_links,
        "recent_memories": recent_memories,
        "weekly_summary": weekly_summary,
        "exam_review_focus": exam_review_focus,
        "last_memory_created_at": str(memories[0].get("created_at") or "") if memories else None,
    }


_SUMMARY_TYPES = {"weekly", "course", "exam", "weak_concepts"}


def _normalize_summary_type(value: Optional[str]) -> str:
    raw = (value or "weekly").strip()
    return raw if raw in _SUMMARY_TYPES else "weekly"


def _date_filter_bound(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raw = raw + ("T23:59:59+00:00" if end_of_day else "T00:00:00+00:00")
    parsed = _parse_iso_datetime(raw)
    return parsed


def _memory_matches_ai_summary_filters(memory: Dict[str, Any], payload: LearningMemoryAiSummaryRequest) -> bool:
    course = (payload.course or "").strip()
    unit = (payload.unit or "").strip()
    if course and str(memory.get("course") or "") != course:
        return False
    if unit and str(memory.get("unit") or "") != unit:
        return False

    concepts = {_concept_key(item) for item in (payload.concepts or []) if _concept_key(item)}
    if concepts and _concept_key(memory.get("concept")) not in concepts:
        return False

    created_at = _parse_iso_datetime(memory.get("created_at"))
    date_from = _date_filter_bound(payload.date_from)
    date_to = _date_filter_bound(payload.date_to, end_of_day=True)
    if date_from and (created_at is None or created_at < date_from):
        return False
    if date_to and (created_at is None or created_at > date_to):
        return False
    return True


def _ai_summary_memories_for_user(user_id: str, payload: LearningMemoryAiSummaryRequest) -> List[Dict[str, Any]]:
    max_items = max(1, min(int(payload.max_items or 30), 80))
    memories = _learning_memory_items_for_user(user_id, limit=5000)
    filtered = [item for item in memories if _memory_matches_ai_summary_filters(item, payload)]
    return filtered[:max_items]


def _ai_summary_filters(payload: LearningMemoryAiSummaryRequest) -> Dict[str, Any]:
    data = _model_to_dict(payload)
    return {
        key: value
        for key, value in data.items()
        if key != "summary_type" and value not in (None, "", [])
    }


def _format_ai_summary_memories(memories: List[Dict[str, Any]]) -> str:
    blocks = []
    for index, item in enumerate(memories, start=1):
        blocks.append(
            "\n".join([
                f"[{index}] id={item.get('id', '')}",
                f"course={item.get('course', '')} / unit={item.get('unit', '')} / concept={item.get('concept', '')}",
                f"created_at={item.get('created_at', '')}",
                f"answer_text={str(item.get('answer_text', ''))[:700]}",
                f"good_points={', '.join(item.get('good_points', []) or [])}",
                f"missing_links={', '.join(item.get('missing_links', []) or [])}",
                f"followup_question={item.get('followup_question', '')}",
                f"improved_summary={str(item.get('improved_summary', ''))[:500]}",
            ])
        )
    return "\n\n".join(blocks)


def _ai_summary_instruction(summary_type: str) -> str:
    if summary_type == "course":
        return "선택된 과목의 Learning Memory를 요약하고, 반복되는 missing links를 연결해 과목별 복습 계획을 제안하세요."
    if summary_type == "exam":
        return "시험 대비 관점에서 우선순위 개념, 흔한 혼동, 짧은 서술형 연습 질문을 정리하세요."
    if summary_type == "weak_concepts":
        return "반복되는 missing links와 낮은 회상 근거가 보이는 약한 개념을 중심으로 원자료와 다시 연결하는 방법을 제안하세요."
    return "이번 주 또는 최근 Learning Memory에서 설명한 내용, 강해지는 개념, 다음에 복습할 개념, 후속 질문 3개를 정리하세요."


def _build_learning_memory_ai_summary_prompt(
    payload: LearningMemoryAiSummaryRequest,
    memories: List[Dict[str, Any]],
) -> str:
    summary_type = _normalize_summary_type(payload.summary_type)
    return f"""You are LinkNote's Learning Memory study summarizer.
Use only the saved student learning memories below. Be educational, specific, and non-judgmental.
Do not invent medical diagnosis, prognosis, prescription, or patient-specific advice.
Return JSON only with keys: title, summary, review_focus, weak_concepts, suggested_questions.

[Summary type]
{summary_type}

[Instruction]
{_ai_summary_instruction(summary_type)}

[Filters]
{json.dumps(_ai_summary_filters(payload), ensure_ascii=False)}

[Learning Memory records]
{_format_ai_summary_memories(memories)}
"""


def _list_of_strings_from_payload(payload: Dict[str, Any], key: str, limit: int = 8) -> List[str]:
    value = payload.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:limit]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_ai_summary_payload(
    raw: Dict[str, Any],
    payload: LearningMemoryAiSummaryRequest,
    memories: List[Dict[str, Any]],
) -> LearningMemoryAiSummaryResponse:
    summary_type = _normalize_summary_type(payload.summary_type)
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        summary = "AI Summary가 생성되었지만 요약 본문이 비어 있습니다. 원 Learning Memory를 함께 확인해주세요."
    title = str(raw.get("title") or "").strip() or {
        "weekly": "이번 주 Learning Memory 요약",
        "course": "과목별 Learning Memory 요약",
        "exam": "시험 대비 Learning Memory 요약",
        "weak_concepts": "약한 개념 중심 Learning Memory 요약",
    }.get(summary_type, "Learning Memory AI Summary")
    return LearningMemoryAiSummaryResponse(
        summary_type=summary_type,
        title=title,
        summary=summary,
        review_focus=_list_of_strings_from_payload(raw, "review_focus", 10),
        weak_concepts=_list_of_strings_from_payload(raw, "weak_concepts", 10),
        suggested_questions=_list_of_strings_from_payload(raw, "suggested_questions", 10),
        source_memory_ids=[str(item.get("id") or "") for item in memories if str(item.get("id") or "")],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _public_ai_summary_record(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "summary_type": str(item.get("summary_type") or ""),
        "filters": item.get("filters") if isinstance(item.get("filters"), dict) else {},
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or ""),
        "review_focus": [str(x) for x in item.get("review_focus", []) if str(x).strip()] if isinstance(item.get("review_focus"), list) else [],
        "weak_concepts": [str(x) for x in item.get("weak_concepts", []) if str(x).strip()] if isinstance(item.get("weak_concepts"), list) else [],
        "suggested_questions": [str(x) for x in item.get("suggested_questions", []) if str(x).strip()] if isinstance(item.get("suggested_questions"), list) else [],
        "source_memory_ids": [str(x) for x in item.get("source_memory_ids", []) if str(x).strip()] if isinstance(item.get("source_memory_ids"), list) else [],
        "created_at": str(item.get("created_at") or ""),
    }


_CLINICAL_IDENTIFIER_PATTERNS = [
    ("resident_id", re.compile(r"\b\d{6}[- ]?[1-4]\d{6}\b")),
    ("phone_number", re.compile(r"\b(?:01[016789]|02|0[3-6][1-5])[- ]?\d{3,4}[- ]?\d{4}\b")),
    ("registration_number", re.compile(r"(?:등록번호|환자번호|병원번호|hospital\s*id|patient\s*id)\s*[:：]?\s*[A-Za-z0-9-]{4,}", re.I)),
    ("patient_name_label", re.compile(r"(?:환자명|이름|성명|patient\s*name)\s*[:：]\s*\S+", re.I)),
    ("room_number", re.compile(r"(?:병실|호실|room)\s*[:：]?\s*\d{2,4}\s*(?:호|번|room)?", re.I)),
    ("date_of_birth", re.compile(r"(?:생년월일|date\s*of\s*birth|dob)\s*[:：]?\s*\d{2,4}[-./년 ]\d{1,2}[-./월 ]\d{1,2}", re.I)),
    ("address", re.compile(r"(?:주소|address)\s*[:：]\s*\S+", re.I)),
]


def _clinical_safety_flags(text: str) -> List[str]:
    flags = []
    for name, pattern in _CLINICAL_IDENTIFIER_PATTERNS:
        if pattern.search(text or ""):
            flags.append(name)
    return flags


def _require_nursing_user(user: Dict[str, Any]) -> str:
    track = str(_auth.public_user(user).get("student_track") or "general")
    if track != "nursing":
        raise HTTPException(status_code=403, detail="Clinical Reflection is available for nursing students.")
    return track


def _clinical_search_filter(payload: ClinicalReflectionRequest) -> Dict[str, str]:
    search_filter: Dict[str, str] = {}
    if payload.selected_course and payload.selected_course.strip():
        search_filter["course"] = payload.selected_course.strip()
    if payload.selected_unit and payload.selected_unit.strip():
        search_filter["unit"] = payload.selected_unit.strip()
    return search_filter


def _clinical_related_context(
    user_id: str,
    payload: ClinicalReflectionRequest,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    text = " ".join([
        payload.situation_text,
        payload.learning_goal or "",
        payload.selected_course or "",
        payload.selected_unit or "",
    ])
    tokens = _tokens(text)
    search_filter = _clinical_search_filter(payload)
    scope = "single" if search_filter else "multi"
    related_concepts = _search_related_concepts(user_id, tokens, search_filter, scope, limit=8)
    related_sources = _search_sources(user_id, tokens, search_filter, scope, limit=6)
    memory_matches = _search_learning_memory(user_id, tokens, search_filter, scope, limit=5)
    return related_concepts, related_sources, memory_matches


def _format_clinical_context(
    related_concepts: List[Dict[str, Any]],
    related_sources: List[Dict[str, Any]],
    memory_matches: List[Dict[str, Any]],
) -> str:
    concepts = "\n".join(
        f"- {item.get('concept', '')} ({item.get('course', '')} · {item.get('unit', '')})"
        for item in related_concepts
    ) or "- 관련 개념 없음"
    sources = "\n".join(
        (
            f"- {item.get('course', '')} · {item.get('unit', '')} · "
            f"{item.get('filename', '')} p.{item.get('page', '')}: "
            f"{str(item.get('chunk_preview', ''))[:500]}"
        )
        for item in related_sources
    ) or "- 관련 자료 없음"
    memories = "\n".join(
        (
            f"- {item.get('concept', '')} ({item.get('course', '')} · {item.get('unit', '')}): "
            f"{str(item.get('improved_summary') or item.get('answer_preview') or '')[:350]}"
        )
        for item in memory_matches
    ) or "- 관련 Learning Memory 없음"
    return f"[Related concepts]\n{concepts}\n\n[Related uploaded sources]\n{sources}\n\n[Related Learning Memory]\n{memories}"


def _build_clinical_reflection_prompt(
    payload: ClinicalReflectionRequest,
    related_concepts: List[Dict[str, Any]],
    related_sources: List[Dict[str, Any]],
    memory_matches: List[Dict[str, Any]],
) -> str:
    return f"""You are LinkNote's Nursing Clinical Reflection learning assistant.
This is educational reflection only. Do not provide medical diagnosis, prognosis, treatment orders, medication instructions, clinical orders, or patient-specific decision-making advice.
Use cautious language. Encourage the learner to ask a clinical instructor, preceptor, or hospital policy source for patient-specific decisions.
Use only the de-identified reflection and retrieved study context below.
Return JSON only with keys: knowledge_connections, nursing_process_links, missed_assessment_cues, safe_next_questions, review_focus, source_hints, educational_summary.

[Student reflection]
situation_text: {payload.situation_text.strip()}
learning_goal: {(payload.learning_goal or '').strip()}
selected_course: {(payload.selected_course or '').strip()}
selected_unit: {(payload.selected_unit or '').strip()}

{_format_clinical_context(related_concepts, related_sources, memory_matches)}
"""


def _normalize_clinical_feedback(payload: Dict[str, Any]) -> ClinicalReflectionFeedback:
    return ClinicalReflectionFeedback(
        knowledge_connections=_list_of_strings_from_payload(payload, "knowledge_connections", 8),
        nursing_process_links=_list_of_strings_from_payload(payload, "nursing_process_links", 8),
        missed_assessment_cues=_list_of_strings_from_payload(payload, "missed_assessment_cues", 8),
        safe_next_questions=_list_of_strings_from_payload(payload, "safe_next_questions", 8),
        review_focus=_list_of_strings_from_payload(payload, "review_focus", 8),
        source_hints=_list_of_strings_from_payload(payload, "source_hints", 8),
        educational_summary=str(payload.get("educational_summary") or "").strip()
        or "실습 상황을 업로드 자료와 연결해 복습해보세요. 환자별 판단은 담당 지도자와 확인해야 합니다.",
    )


def _public_clinical_reflection(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "student_track": str(item.get("student_track") or "nursing"),
        "situation_text": str(item.get("situation_text") or ""),
        "learning_goal": str(item.get("learning_goal") or ""),
        "selected_course": item.get("selected_course"),
        "selected_unit": item.get("selected_unit"),
        "related_concepts": item.get("related_concepts") if isinstance(item.get("related_concepts"), list) else [],
        "related_sources": item.get("related_sources") if isinstance(item.get("related_sources"), list) else [],
        "feedback": item.get("feedback") if isinstance(item.get("feedback"), dict) else {},
        "safety_flags": item.get("safety_flags") if isinstance(item.get("safety_flags"), list) else [],
        "created_at": str(item.get("created_at") or ""),
    }


def _normalize_search_filter(search_filter: Optional[Any]) -> Dict[str, str]:
    if not search_filter:
        return {}
    raw = _model_to_dict(search_filter) if isinstance(search_filter, BaseModel) else dict(search_filter)
    return {
        key: str(value).strip()
        for key, value in raw.items()
        if key in {"semester", "course", "unit", "filename"} and str(value or "").strip()
    }


def _filter_for_chroma(search_filter: Dict[str, str], scope: str) -> Dict[str, str]:
    if scope == "multi":
        return {}
    return {
        key: value
        for key, value in search_filter.items()
        if key in {"semester", "course", "filename"}
    }


def _tokens(text: str) -> List[str]:
    return [
        token.lower()
        for token in re.findall(r"[0-9A-Za-z가-힣]+", text or "")
        if len(token.strip()) >= 2
    ]


def _text_score(text: str, tokens: List[str]) -> float:
    if not tokens:
        return 0.0
    haystack = str(text or "").lower()
    score = 0.0
    for token in tokens:
        if token in haystack:
            score += 1.0
        score += min(haystack.count(token), 3) * 0.2
    return round(score, 4)


def _search_cache_key(user_id: str, question: str, search_filter: Dict[str, str], scope: str) -> str:
    payload = {
        "user_id": user_id,
        "question": " ".join((question or "").lower().split()),
        "search_filter": search_filter,
        "scope": scope,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_search_cache() -> Dict[str, Any]:
    data = _load_json_file(SEARCH_CACHE_PATH)
    return data if isinstance(data, dict) else {}


def _save_search_cache(cache: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SEARCH_CACHE_PATH), exist_ok=True)
    with open(SEARCH_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _is_sensitive_search(question: str) -> bool:
    sensitive_terms = ["환자", "등록번호", "주민등록번호", "병실", "전화번호", "hospital id", "patient id"]
    lowered = (question or "").lower()
    return any(term in lowered for term in sensitive_terms)


def _iter_user_concepts_with_context(user_id: str) -> List[Dict[str, Any]]:
    data = _load_json_file(CONCEPTS_PATH)
    user_data = data.get(user_id, {}) if isinstance(data, dict) else {}
    out: List[Dict[str, Any]] = []
    if not isinstance(user_data, dict):
        return out
    for semester, semester_data in user_data.items():
        if not isinstance(semester_data, dict):
            continue
        for course, course_data in semester_data.items():
            if not isinstance(course_data, dict):
                continue
            for unit, unit_concepts in course_data.items():
                if not isinstance(unit_concepts, list):
                    continue
                for concept in unit_concepts:
                    if not isinstance(concept, dict):
                        continue
                    out.append({
                        **concept,
                        "semester": semester,
                        "course": course,
                        "unit": unit,
                    })
    return out


def _matches_filter_context(item: Dict[str, Any], search_filter: Dict[str, str], scope: str) -> bool:
    if scope == "multi":
        return True
    for key, value in search_filter.items():
        if key in {"semester", "course", "unit", "filename"} and value:
            if str(item.get(key) or "") != value:
                return False
    return True


def _search_related_concepts(user_id: str, tokens: List[str], search_filter: Dict[str, str], scope: str, limit: int) -> List[Dict[str, Any]]:
    concepts = []
    for item in _iter_user_concepts_with_context(user_id):
        if not _matches_filter_context(item, search_filter, scope):
            continue
        concept = str(item.get("name") or item.get("keyword") or item.get("concept") or "").strip()
        text = " ".join(str(item.get(k, "")) for k in ["name", "keyword", "definition", "description", "summary"])
        score = _text_score(text, tokens)
        if score <= 0 and concept and any(token in concept.lower() for token in tokens):
            score = 1.0
        if score <= 0:
            continue
        concepts.append({
            "concept": concept,
            "course": item.get("course", ""),
            "unit": item.get("unit", ""),
            "reason": "질문 키워드가 개념명 또는 설명과 일치합니다.",
            "_score": score,
        })
    concepts.sort(key=lambda item: item["_score"], reverse=True)
    return [{k: v for k, v in item.items() if k != "_score"} for item in concepts[:limit]]


def _search_sources(user_id: str, tokens: List[str], search_filter: Dict[str, str], scope: str, limit: int) -> List[Dict[str, Any]]:
    chroma_filter = _filter_for_chroma(search_filter, scope)
    data = get_chunks(user_id=user_id, limit=5000, offset=0, search_filter=chroma_filter, full=True)
    scored: List[Dict[str, Any]] = []
    for item in data.get("items", []):
        if not _matches_filter_context(item, search_filter, scope):
            continue
        text = " ".join(str(item.get(k, "")) for k in ["semester", "course", "unit", "title", "filename", "text"])
        score = _text_score(text, tokens)
        if score <= 0 and tokens:
            continue
        scored.append({
            "semester": item.get("semester", ""),
            "course": item.get("course", ""),
            "unit": item.get("unit", ""),
            "filename": item.get("filename", ""),
            "page": item.get("page"),
            "chunk_index": item.get("chunk_index"),
            "chunk_preview": str(item.get("text", ""))[:420],
            "score": score,
            "_sort": score,
        })
    scored.sort(key=lambda item: (-item["_sort"], str(item.get("course", "")), str(item.get("filename", "")), int(item.get("page") or 0)))
    return [{k: v for k, v in item.items() if k != "_sort"} for item in scored[:limit]]


def _memory_list_field(item: Dict[str, Any], key: str) -> List[str]:
    value = item.get(key)
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    feedback = item.get("feedback")
    if isinstance(feedback, dict):
        inner = feedback.get(key)
        if isinstance(inner, list):
            return [str(x).strip() for x in inner if str(x).strip()]
    return []


def _memory_text_field(item: Dict[str, Any], key: str) -> str:
    value = str(item.get(key) or "").strip()
    if value:
        return value
    feedback = item.get("feedback")
    if isinstance(feedback, dict):
        return str(feedback.get(key) or "").strip()
    return ""


def _search_learning_memory(user_id: str, tokens: List[str], search_filter: Dict[str, str], scope: str, limit: int) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for item in _load_recall_traces():
        if not isinstance(item, dict) or item.get("user_id") != user_id:
            continue
        if not _matches_filter_context(item, search_filter, scope):
            continue
        text = " ".join([
            str(item.get("concept", "")),
            str(item.get("answer_text", "")),
            str(item.get("feedback_text", "")),
            _memory_text_field(item, "improved_summary"),
            " ".join(_memory_list_field(item, "missing_links")),
        ])
        score = _text_score(text, tokens)
        if score <= 0 and tokens:
            continue
        matches.append({
            "concept": item.get("concept", ""),
            "course": item.get("course", ""),
            "unit": item.get("unit", ""),
            "answer_preview": str(item.get("answer_text", ""))[:220],
            "improved_summary": _memory_text_field(item, "improved_summary"),
            "created_at": item.get("created_at", ""),
            "_score": score,
        })
    matches.sort(key=lambda item: (-item["_score"], str(item.get("created_at", ""))))
    return [{k: v for k, v in item.items() if k != "_score"} for item in matches[:limit]]


def _build_search_only_response(user_id: str, request: AskSearchRequest) -> Dict[str, Any]:
    question = request.question.strip()
    requested_scope = (request.scope or "auto").strip().lower()
    search_filter = _normalize_search_filter(request.search_filter)
    scope = "single" if requested_scope == "single" or (requested_scope == "auto" and search_filter) else "multi"
    if requested_scope == "multi":
        scope = "multi"
    limit = max(1, min(int(request.limit or 5), 12))
    cache_key = _search_cache_key(user_id, question, search_filter, scope)
    can_cache = not _is_sensitive_search(question)
    if can_cache:
        cached = _load_search_cache().get(cache_key)
        if isinstance(cached, dict) and cached.get("user_id") == user_id:
            result = dict(cached.get("result") or {})
            result["from_cache"] = True
            return result

    tokens = _tokens(question)
    result = {
        "question": question,
        "mode": "search_only",
        "scope": scope,
        "from_cache": False,
        "related_concepts": _search_related_concepts(user_id, tokens, search_filter, scope, limit),
        "sources": _search_sources(user_id, tokens, search_filter, scope, limit),
        "learning_memory_matches": _search_learning_memory(user_id, tokens, search_filter, scope, limit),
        "can_generate_ai_answer": True,
    }
    if can_cache:
        cache = _load_search_cache()
        cache[cache_key] = {
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }
        _save_search_cache(cache)
    return result


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


@app.post("/ask/search")
async def ask_search(request: AskSearchRequest, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question이 필요합니다.")
    return _build_search_only_response(data_user_id, request)


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
            detail="semester, course, unit, concept, answer_text가 필요합니다.",
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
        raise HTTPException(status_code=400, detail="semester, course, unit이 필요합니다.")

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
            detail="semester, course, unit, concept, answer_text가 필요합니다.",
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


def _normalize_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFC", (name or "").strip())
    if (
        not normalized
        or normalized in {".", ".."}
        or ".." in normalized
        or "/" in normalized
        or "\\" in normalized
        or os.path.isabs(normalized)
        or os.path.basename(normalized) != normalized
    ):
        raise HTTPException(status_code=400, detail="잘못된 파일명입니다.")
    return normalized


def _safe_upload_path(upload_filename: str) -> Optional[str]:
    base = os.path.basename(upload_filename or "")
    if not base:
        return None
    upload_root = os.path.realpath(UPLOAD_DIR)
    candidate = os.path.realpath(os.path.join(UPLOAD_DIR, base))
    if not candidate.startswith(upload_root + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


def _filenames_owned_by_user(data_user_id: str) -> set[str]:
    overview = get_library_overview(user_id=data_user_id)
    owned: set[str] = set()
    for courses in overview.get("semesters", {}).values():
        for files in courses.values():
            for filename in files.keys():
                try:
                    owned.add(_normalize_filename(filename or ""))
                except HTTPException:
                    continue
    return owned


def _matching_upload_paths(filename: str) -> List[str]:
    matches = []
    want = unicodedata.normalize("NFC", filename)
    for stored in os.listdir(UPLOAD_DIR):
        base = unicodedata.normalize("NFC", stored)
        if base == want or base.endswith("_" + want):
            if path := _safe_upload_path(stored):
                matches.append(path)
    return sorted(matches)


def _resolve_owned_upload_path(data_user_id: str, requested_filename: str) -> Optional[str]:
    safe_filename = _normalize_filename(requested_filename)
    if safe_filename not in _filenames_owned_by_user(data_user_id):
        return None

    matches = _matching_upload_paths(safe_filename)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning("Ambiguous upload preview for data_user_id=%s filename=%s", data_user_id, safe_filename)
    return None


@app.get("/file")
async def serve_file(filename: str, token: str = ""):
    """업로드된 원본 PDF preview. 쿼리 토큰에서 도출한 data_user_id 소유 파일만 반환."""
    data_user_id = _uid_from_token(token)
    if path := _resolve_owned_upload_path(data_user_id, filename):
        return FileResponse(path, media_type="application/pdf")
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


@app.get("/learning-memory")
async def learning_memory(
    course: Optional[str] = None,
    unit: Optional[str] = None,
    concept: Optional[str] = None,
    limit: int = 50,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    all_items = _learning_memory_items_for_user(
        user_id=data_user_id,
        course=course,
        unit=unit,
        concept=concept,
        limit=5000,
    )
    safe_limit = max(1, min(limit, 200))
    items = all_items[:safe_limit]
    return {"total": len(all_items), "items": items}


@app.get("/learning-memory/summary")
async def learning_memory_summary(data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    return _learning_memory_summary_for_user(data_user_id)


@app.post("/learning-memory/ai-summary", response_model=LearningMemoryAiSummaryResponse)
async def learning_memory_ai_summary(
    payload: LearningMemoryAiSummaryRequest,
    data_user_id: str = Depends(current_uid),
) -> LearningMemoryAiSummaryResponse:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="AI summary generation is not configured.")

    payload.summary_type = _normalize_summary_type(payload.summary_type)
    memories = _ai_summary_memories_for_user(data_user_id, payload)
    if not memories:
        raise HTTPException(status_code=400, detail="AI Summary에 사용할 Learning Memory가 없습니다.")

    prompt = _build_learning_memory_ai_summary_prompt(payload, memories)
    try:
        raw_summary = generate_openai_answer(prompt, max_tokens=900)
        response = _normalize_ai_summary_payload(_extract_json_object(raw_summary), payload, memories)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI summary 생성에 실패했습니다: {exc}") from exc

    record = {
        "id": uuid.uuid4().hex,
        "user_id": data_user_id,
        "summary_type": response.summary_type,
        "filters": _ai_summary_filters(payload),
        "title": response.title,
        "summary": response.summary,
        "review_focus": response.review_focus,
        "weak_concepts": response.weak_concepts,
        "suggested_questions": response.suggested_questions,
        "source_memory_ids": response.source_memory_ids,
        "created_at": response.created_at,
    }
    summaries = _load_learning_memory_summaries()
    summaries.append(record)
    _save_learning_memory_summaries(summaries)
    return response


@app.get("/learning-memory/ai-summaries", response_model=LearningMemoryAiSummariesResponse)
async def learning_memory_ai_summaries(
    summary_type: Optional[str] = None,
    limit: int = 10,
    data_user_id: str = Depends(current_uid),
) -> LearningMemoryAiSummariesResponse:
    requested_type = (summary_type or "").strip()
    safe_limit = max(1, min(limit, 50))
    items = []
    for item in _load_learning_memory_summaries():
        if not isinstance(item, dict) or item.get("user_id") != data_user_id:
            continue
        if requested_type and item.get("summary_type") != _normalize_summary_type(requested_type):
            continue
        items.append(_public_ai_summary_record(item))
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return LearningMemoryAiSummariesResponse(items=items[:safe_limit])


@app.post("/clinical-reflection", response_model=ClinicalReflectionResponse)
async def clinical_reflection(
    payload: ClinicalReflectionRequest,
    user: Dict[str, Any] = Depends(current_user),
) -> ClinicalReflectionResponse:
    student_track = _require_nursing_user(user)
    data_user_id = user.get("data_user_id") or user["email"]
    situation_text = payload.situation_text.strip()
    if not situation_text:
        raise HTTPException(status_code=400, detail="실습 상황을 입력해주세요.")

    safety_flags = _clinical_safety_flags(situation_text)
    if safety_flags:
        raise HTTPException(
            status_code=400,
            detail="환자 식별 정보가 포함될 수 있습니다. 이름, 등록번호, 병실, 주민번호, 전화번호 등은 제거하고 다시 입력해 주세요.",
        )

    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="Clinical Reflection AI feedback is not configured.")

    related_concepts, related_sources, memory_matches = _clinical_related_context(data_user_id, payload)
    prompt = _build_clinical_reflection_prompt(payload, related_concepts, related_sources, memory_matches)
    try:
        raw_feedback = generate_openai_answer(prompt, max_tokens=900)
        feedback = _normalize_clinical_feedback(_extract_json_object(raw_feedback))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Clinical Reflection feedback 생성에 실패했습니다: {exc}") from exc

    created_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": uuid.uuid4().hex,
        "user_id": data_user_id,
        "student_track": student_track,
        "situation_text": situation_text,
        "learning_goal": (payload.learning_goal or "").strip(),
        "selected_course": (payload.selected_course or "").strip() or None,
        "selected_unit": (payload.selected_unit or "").strip() or None,
        "related_concepts": related_concepts,
        "related_sources": related_sources,
        "feedback": _model_to_dict(feedback),
        "safety_flags": [],
        "created_at": created_at,
    }
    reflections = _load_clinical_reflections()
    reflections.append(record)
    _save_clinical_reflections(reflections)
    return ClinicalReflectionResponse(**record)


@app.get("/clinical-reflections", response_model=ClinicalReflectionListResponse)
async def clinical_reflections(
    limit: int = 20,
    course: Optional[str] = None,
    unit: Optional[str] = None,
    user: Dict[str, Any] = Depends(current_user),
) -> ClinicalReflectionListResponse:
    _require_nursing_user(user)
    data_user_id = user.get("data_user_id") or user["email"]
    course_filter = (course or "").strip()
    unit_filter = (unit or "").strip()
    safe_limit = max(1, min(limit, 100))
    items = []
    for item in _load_clinical_reflections():
        if not isinstance(item, dict) or item.get("user_id") != data_user_id:
            continue
        if course_filter and str(item.get("selected_course") or "") != course_filter:
            continue
        if unit_filter and str(item.get("selected_unit") or "") != unit_filter:
            continue
        items.append(_public_clinical_reflection(item))
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return ClinicalReflectionListResponse(items=items[:safe_limit])


# ============== 계정 / 인증 (Stage C-1) ==============
from fastapi import Header
import auth as _auth


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = ""
    link_user_id: Optional[str] = ""
    student_track: Optional[str] = "general"


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/register")
async def auth_register(req: RegisterRequest) -> Dict[str, Any]:
    user, err = _auth.register_user(
        req.email,
        req.password,
        req.display_name or "",
        req.link_user_id or "",
        req.student_track or "general",
    )
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


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": app.version,
        "routes": [
            "/auth/me",
            "/me/summary",
            "/learning-memory",
            "/learning-memory/summary",
            "/learning-memory/ai-summary",
            "/learning-memory/ai-summaries",
            "/clinical-reflection",
            "/clinical-reflections",
            "/ask/search",
        ],
    }


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
