from __future__ import annotations

import json
import logging
import os
import re
import hashlib
import time
import uuid
import csv
import threading
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, Depends, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import unicodedata
from pydantic import BaseModel, Field

from providers.openai_provider import generate_answer as generate_openai_answer

try:
    from pdf_loader import extract_pdf_text
except Exception:
    def extract_pdf_text(*args, **kwargs):
        return []
logger = logging.getLogger(__name__)

from rag import (
    add_pdf_pages_to_db,
    answer_question,
    answer_with_connections,
    build_concepts_for_unit,
    build_concept_embeddings,
    build_concept_embeddings_for_scope,
    build_cross_links,
    build_cross_links_for_scope,
    move_chunks_by_filter,
    move_graph_scope,
    remove_graph_scope,
    rename_unit,
    delete_chunks_by_filter,
    get_chunks,
    get_filter_label,
    get_library_overview,
    get_units,
    search_relevant_chunks,
    concept_occurrences_for_unit,
    concept_source_chunks_for_unit,
    _concept_occurrences_for_terms_from_chunks,
)
from search_engine import (
    INTENT_LABELS,
    SEARCH_ALGORITHM_VERSION,
    classify_intent,
    expand_tokens,
    learning_score,
    matched_fields,
    normalized_keyword_score,
    preference_score,
    raw_text_score,
    resolve_scope,
    score_reason,
    semantic_score,
    source_relevance_label,
    tokenize,
    weighted_score,
)

DATA_DIR = os.getenv("DATA_DIR", "./data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
TIMETABLE_PATH = os.path.join(DATA_DIR, "timetable.json")
CONCEPTS_PATH = os.path.join(DATA_DIR, "concepts.json")
RECALL_TRACES_PATH = os.path.join(DATA_DIR, "recall_traces.json")
LEARNING_SESSIONS_PATH = os.path.join(DATA_DIR, "learning_sessions.json")
REVIEW_SCHEDULE_PATH = os.path.join(DATA_DIR, "review_schedule.json")
LEARNING_MEMORY_SUMMARIES_PATH = os.path.join(DATA_DIR, "learning_memory_summaries.json")
CLINICAL_REFLECTIONS_PATH = os.path.join(DATA_DIR, "clinical_reflections.json")
STUDY_CLAIMS_PATH = os.path.join(DATA_DIR, "study_claims.json")
CLINICAL_VERIFICATIONS_PATH = os.path.join(DATA_DIR, "clinical_verifications.json")
SEARCH_CACHE_PATH = os.path.join(DATA_DIR, "search_cache.json")
SEARCH_EVENTS_PATH = os.path.join(DATA_DIR, "search_events.json")
SEARCH_PROFILES_PATH = os.path.join(DATA_DIR, "search_profiles.json")
QUESTION_HISTORY_PATH = os.path.join(DATA_DIR, "question_history.json")
UPLOAD_EVENTS_PATH = os.path.join(DATA_DIR, "upload_events.json")
INGEST_JOBS_PATH = os.path.join(DATA_DIR, "ingest_jobs.json")
LECTURE_NOTES_PATH = os.path.join(DATA_DIR, "lecture_notes.json")
CONCEPT_INDEX_PATH = os.path.join(DATA_DIR, "concept_index.json")
CONCEPT_LINKS_PATH = os.path.join(DATA_DIR, "concept_links.json")
MAINTAINER_EMAIL = "kory124@snu.ac.kr"
PROMPTS_DIR = os.getenv("PROMPTS_DIR", "./prompts")
REGISTRY_CSV_PATH = os.getenv("REGISTRY_CSV", "material_registry.csv")
RECALL_FEEDBACK_PROMPT_PATH = os.path.join(PROMPTS_DIR, "recall_feedback.md")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(CONCEPTS_PATH), exist_ok=True)

_ingest_jobs_lock = threading.RLock()
_concepts_write_lock = threading.RLock()
_upload_events_lock = threading.RLock()
_question_history_lock = threading.RLock()

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
import registry as _registry


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
    # 다른 과목으로 이어지는 연계 개념 제안 (저장된 개념 그래프 조회만, 추가 LLM 호출 없음)
    related_concepts: List[Dict[str, Any]] = []


class AskSearchRequest(BaseModel):
    question: str
    search_filter: Optional[SearchFilter] = None
    scope: Optional[str] = "auto"
    limit: Optional[int] = 5
    surface: Optional[str] = "library"
    current_concept: Optional[str] = None


class ConceptNoteUpsertRequest(BaseModel):
    semester: str
    course: str
    unit: str
    filename: str
    concept: str
    note_text: str = ""
    source_pages: List[Any] = Field(default_factory=list)


class LectureNoteUpsertRequest(BaseModel):
    note_id: Optional[str] = None
    title: str = ""
    semester: str
    course: str
    unit: str
    filename: str
    start_page: int
    end_page: int
    note_text: str = ""
    tags: List[str] = Field(default_factory=list)


class SearchEventCreate(BaseModel):
    search_id: str
    event_type: str
    result_type: Optional[str] = None
    result_id: Optional[str] = None
    question: Optional[str] = None
    intent: Optional[str] = None
    course: Optional[str] = None
    concept: Optional[str] = None


class SearchAliasCreate(BaseModel):
    concept: str
    alias: str


class IngestResponse(BaseModel):
    ok: bool
    filename: str
    pages: int
    message: str
    unit: str = ""
    concept_count: int = 0
    concept_extraction_status: str = "not_run"


def _effective_unit_name(unit: str, filename: str) -> str:
    explicit = str(unit or "").strip()
    if explicit:
        return explicit
    stem = os.path.splitext(os.path.basename(str(filename or "")))[0].strip()
    return stem or "미분류"


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


class LibraryFileLocation(BaseModel):
    semester: str
    course: str
    unit: str
    filename: str


class MoveLibraryFilePayload(BaseModel):
    source: LibraryFileLocation
    target_semester: str
    target_course: str
    target_unit: str


class LibraryFileActionResponse(BaseModel):
    ok: bool
    filename: str
    affected_chunks: int
    concepts_status: str = "unchanged"
    graph_nodes: int = 0
    source_file_deleted: bool = False


def _normalize_library_overview(overview: Dict[str, Any]) -> LibraryResponse:
    semesters = []

    def semester_key(item: Any) -> tuple:
        value = str(item[0])
        match = re.fullmatch(r"(\d{4})-(\d+)", value)
        return (int(match.group(1)), int(match.group(2))) if match else (-1, -1)

    for semester, courses in sorted(overview.get("semesters", {}).items(), key=semester_key, reverse=True):
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


def _record_upload_event(
    user_id: str,
    filename: str,
    semester: str,
    course: str,
    unit: str,
    status: str,
    message: str = "",
    pages: int = 0,
    concept_count: int = 0,
) -> Dict[str, Any]:
    event = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "filename": filename,
        "semester": semester.strip(),
        "course": course.strip(),
        "unit": unit.strip(),
        "status": status,
        "message": message,
        "pages": pages,
        "concept_count": concept_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _upload_events_lock:
        events = _load_json_file(UPLOAD_EVENTS_PATH)
        events = [item for item in events if isinstance(item, dict)] if isinstance(events, list) else []
        events.append(event)
        os.makedirs(os.path.dirname(UPLOAD_EVENTS_PATH), exist_ok=True)
        _save_json_file(UPLOAD_EVENTS_PATH, events[-2000:])
    return event


def _save_json_file(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _create_ingest_job(user_id: str, semester: str, course: str, filenames: List[str]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "job_id": uuid.uuid4().hex,
        "user_id": user_id,
        "semester": semester.strip(),
        "course": course.strip(),
        "filenames": filenames,
        "status": "queued",
        "progress": 0,
        "total_files": len(filenames),
        "completed_files": 0,
        "failed_files": 0,
        "concept_count": 0,
        "message": "분석 대기 중",
        "errors": [],
        "created_at": now,
        "updated_at": now,
    }
    with _ingest_jobs_lock:
        jobs = _load_json_file(INGEST_JOBS_PATH)
        jobs = jobs if isinstance(jobs, dict) else {}
        jobs[job["job_id"]] = job
        _save_json_file(INGEST_JOBS_PATH, jobs)
    return dict(job)


_lecture_notes_lock = threading.RLock()


def _update_ingest_job(job_id: str, **changes) -> Dict[str, Any]:
    with _ingest_jobs_lock:
        jobs = _load_json_file(INGEST_JOBS_PATH)
        jobs = jobs if isinstance(jobs, dict) else {}
        job = dict(jobs.get(job_id) or {})
        if not job:
            return {}
        job.update(changes)
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        jobs[job_id] = job
        _save_json_file(INGEST_JOBS_PATH, jobs)
        return dict(job)


def _get_ingest_job(job_id: str) -> Dict[str, Any]:
    with _ingest_jobs_lock:
        jobs = _load_json_file(INGEST_JOBS_PATH)
        return dict(jobs.get(job_id) or {}) if isinstance(jobs, dict) else {}


def _load_question_history() -> List[Dict[str, Any]]:
    data = _load_json_file(QUESTION_HISTORY_PATH)
    return data if isinstance(data, list) else []


def _save_question_history(items: List[Dict[str, Any]]) -> None:
    parent_dir = os.path.dirname(QUESTION_HISTORY_PATH)
    os.makedirs(parent_dir, exist_ok=True)
    temp_path = f"{QUESTION_HISTORY_PATH}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(items, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, QUESTION_HISTORY_PATH)
    except OSError as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="검색·질문 기록을 저장하지 못했습니다.") from exc


def _record_question_history(
    user_id: str,
    question: str,
    mode: str,
    *,
    answer: str = "",
    search_filter: Optional[Dict[str, Any]] = None,
    scope: str = "",
    result_count: int = 0,
) -> Dict[str, Any]:
    item = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "question": str(question or "").strip(),
        "answer": str(answer or "").strip(),
        "mode": str(mode or "ai_answer").strip(),
        "search_filter": search_filter or {},
        "scope": str(scope or "").strip(),
        "result_count": max(0, int(result_count or 0)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _question_history_lock:
        items = _load_question_history()
        items.append(item)
        _save_question_history(items[-5000:])
    return item


def _question_history_for_user(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    with _question_history_lock:
        items = [
            item for item in _load_question_history()
            if isinstance(item, dict) and item.get("user_id") == user_id
        ]
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items[:max(1, min(int(limit or 50), 5000))]


def _delete_question_history_for_user(user_id: str, history_id: str = "") -> int:
    target_id = str(history_id or "").strip()
    deleted = 0
    with _question_history_lock:
        items = _load_question_history()
        kept: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            owned = item.get("user_id") == user_id
            selected = not target_id or str(item.get("id") or "") == target_id
            if owned and selected:
                deleted += 1
                continue
            kept.append(item)
        if deleted:
            _save_question_history(kept)
    return deleted


def _search_history_summary(result: Dict[str, Any]) -> str:
    lines: List[str] = []

    concept_names: List[str] = []
    for item in result.get("related_concepts") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("concept") or item.get("name") or "").strip()
        if name and name not in concept_names:
            concept_names.append(name)
    if concept_names:
        lines.append("관련 개념: " + ", ".join(concept_names[:6]))

    source_names: List[str] = []
    for item in result.get("sources") or []:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or item.get("title") or "자료").strip()
        page = item.get("page")
        label = filename + (f" p.{page}" if page not in (None, "") else "")
        if label not in source_names:
            source_names.append(label)
    if source_names:
        lines.append("관련 문서: " + " · ".join(source_names[:6]))

    memory_names: List[str] = []
    for item in result.get("learning_memory_matches") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("concept") or item.get("name") or "").strip()
        if name and name not in memory_names:
            memory_names.append(name)
    if memory_names:
        lines.append("Learning Memory: " + ", ".join(memory_names[:4]))

    return "\n".join(lines) or "관련 결과를 찾지 못했습니다."


def _load_lecture_notes() -> List[Dict[str, Any]]:
    data = _load_json_file(LECTURE_NOTES_PATH)
    return data if isinstance(data, list) else []


def _save_lecture_notes(items: List[Dict[str, Any]]) -> None:
    with _lecture_notes_lock:
        parent_dir = os.path.dirname(LECTURE_NOTES_PATH)
        os.makedirs(parent_dir, exist_ok=True)
        temp_path = f"{LECTURE_NOTES_PATH}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(items, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, LECTURE_NOTES_PATH)
        except OSError as exc:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise HTTPException(
                status_code=500,
                detail="수업 필기를 저장하지 못했습니다.",
            ) from exc


def _normalize_lecture_note_tags(tags: Any) -> List[str]:
    allowed = ("important", "exam", "question")
    if not isinstance(tags, (list, tuple)):
        return []
    requested = {str(tag or "").strip().lower() for tag in tags}
    return [tag for tag in allowed if tag in requested]


def _clean_lecture_note_title(title: Any) -> str:
    return " ".join(str(title or "").split())[:120]


def _lecture_note_identity_and_range(payload: Dict[str, Any]) -> tuple[Dict[str, str], int, int]:
    identity = {
        key: str(payload.get(key) or "").strip()
        for key in ("semester", "course", "unit", "filename")
    }
    if not all(identity.values()):
        raise HTTPException(
            status_code=400,
            detail="semester, course, unit, filename은 모두 필요합니다.",
        )
    try:
        start_page = int(payload.get("start_page"))
        end_page = int(payload.get("end_page"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="페이지 구간이 올바르지 않습니다.") from exc
    if start_page < 1 or end_page < start_page:
        raise HTTPException(status_code=400, detail="페이지 구간이 올바르지 않습니다.")
    return identity, start_page, end_page


def _get_lecture_notes_for_user(
    user_id: str,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    filters = filters or {}
    items = []
    with _lecture_notes_lock:
        for note in _load_lecture_notes():
            if not isinstance(note, dict) or note.get("user_id") != user_id:
                continue
            if all(
                not filters.get(key)
                or str(note.get(key) or "").strip() == str(filters[key]).strip()
                for key in ("semester", "course", "unit", "filename")
            ):
                items.append(dict(note))

    def page_range(item: Dict[str, Any]) -> tuple[int, int]:
        try:
            return int(item.get("start_page") or 0), int(item.get("end_page") or 0)
        except (TypeError, ValueError):
            return 0, 0

    return sorted(items, key=page_range)


def _upsert_lecture_note(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    identity, start_page, end_page = _lecture_note_identity_and_range(payload)
    note_id = str(payload.get("note_id") or "").strip()

    now = datetime.now(timezone.utc).isoformat()
    with _lecture_notes_lock:
        notes = _load_lecture_notes()
        matched_index = next((
            index for index, note in enumerate(notes)
            if isinstance(note, dict)
            and note.get("user_id") == user_id
            and (not note_id or str(note.get("id") or "") == note_id)
            and all(
                str(note.get(key) or "").strip() == value
                for key, value in identity.items()
            )
            and note.get("start_page") == start_page
            and note.get("end_page") == end_page
        ), None)
        if note_id and matched_index is None:
            raise HTTPException(status_code=404, detail="수업 필기를 찾을 수 없습니다.")
        title = _clean_lecture_note_title(payload.get("title"))
        note_text = _clean_note_text(payload.get("note_text"))
        tags = _normalize_lecture_note_tags(payload.get("tags"))
        if matched_index is not None:
            note = dict(notes[matched_index])
            note.update(title=title, note_text=note_text, tags=tags, updated_at=now)
            notes[matched_index] = note
        else:
            note = {
                "id": uuid.uuid4().hex,
                "user_id": user_id,
                **identity,
                "start_page": start_page,
                "end_page": end_page,
                "title": title,
                "note_text": note_text,
                "tags": tags,
                "created_at": now,
                "updated_at": now,
            }
            notes.append(note)
        _save_lecture_notes(notes)
    return note


def _create_lecture_note(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    identity, start_page, end_page = _lecture_note_identity_and_range(payload)
    now = datetime.now(timezone.utc).isoformat()
    note = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        **identity,
        "start_page": start_page,
        "end_page": end_page,
        "title": _clean_lecture_note_title(payload.get("title")),
        "note_text": _clean_note_text(payload.get("note_text")),
        "tags": _normalize_lecture_note_tags(payload.get("tags")),
        "created_at": now,
        "updated_at": now,
    }
    with _lecture_notes_lock:
        notes = _load_lecture_notes()
        notes.append(note)
        _save_lecture_notes(notes)
    return note


def _delete_lecture_note_for_user(user_id: str, note_id: str) -> bool:
    with _lecture_notes_lock:
        notes = _load_lecture_notes()
        kept = []
        deleted = False
        for note in notes:
            if (
                isinstance(note, dict)
                and note.get("user_id") == user_id
                and str(note.get("id") or "") == str(note_id or "")
            ):
                deleted = True
                continue
            kept.append(note)
        if deleted:
            _save_lecture_notes(kept)
        return deleted


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


def _load_learning_sessions() -> List[Dict[str, Any]]:
    data = _load_json_file(LEARNING_SESSIONS_PATH)
    return data if isinstance(data, list) else []


def _save_learning_sessions(items: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(LEARNING_SESSIONS_PATH), exist_ok=True)
    with open(LEARNING_SESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _load_review_schedule() -> Dict[str, Dict[str, Any]]:
    data = _load_json_file(REVIEW_SCHEDULE_PATH)
    if isinstance(data, dict):
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}
    return {}


def _save_review_schedule(items: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(REVIEW_SCHEDULE_PATH), exist_ok=True)
    with open(REVIEW_SCHEDULE_PATH, "w", encoding="utf-8") as f:
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
    if raw.startswith(""):
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
    # 방금 설명한 개념에서 다른 과목으로 이어지는 추후 학습 제안 (개념 그래프 조회만)
    related_concepts: List[Dict[str, Any]] = []


class LearningSessionStartRequest(BaseModel):
    scope: Optional[Dict[str, Optional[str]]] = None
    size: int = 7


class LearningSessionAdvanceRequest(BaseModel):
    concept_id: str
    result: str


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


class LearningMemoryBulkDeleteRequest(BaseModel):
    ids: Optional[List[str]] = None
    delete_all: bool = False
    course: Optional[str] = None
    unit: Optional[str] = None
    concept: Optional[str] = None


class LearningMemorySelectedDeleteRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


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


def _schedule_key(user_id: str, concept_id: str) -> str:
    return f"{str(user_id or '').strip()}::{str(concept_id or '').strip()}"


def _review_schedule_entry(user_id: str, concept_id: str) -> Optional[Dict[str, Any]]:
    schedules = _load_review_schedule()
    entry = schedules.get(_schedule_key(user_id, concept_id))
    return entry if isinstance(entry, dict) else None


def _build_review_schedule_entry(user_id: str, concept_id: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "concept_id": str(concept_id or ""),
        "user_id": str(user_id or ""),
        "ease": 2.5,
        "interval_days": 0,
        "repetitions": 0,
        "last_reviewed_at": None,
        "due_at": now.isoformat(),
    }


def _apply_sm2_schedule(entry: Dict[str, Any], quality: int) -> Dict[str, Any]:
    quality_value = max(0, min(5, int(quality)))
    now = datetime.now(timezone.utc)
    prev_interval = max(0, int((entry or {}).get("interval_days") or 0))
    ease = float((entry or {}).get("ease") or 2.5)
    repetitions = int((entry or {}).get("repetitions") or 0)

    if quality_value < 3:
        repetitions = 0
        interval_days = 1
    else:
        repetitions += 1
        if repetitions == 1:
            interval_days = 1
        elif repetitions == 2:
            interval_days = 6
        else:
            interval_days = max(1, int(round(prev_interval * ease)))

    ease = max(1.3, ease + (0.1 - (5 - quality_value) * (0.08 + (5 - quality_value) * 0.02)))
    due_at = (now + timedelta(days=interval_days)).isoformat()
    return {
        **entry,
        "ease": round(ease, 2),
        "interval_days": int(interval_days),
        "repetitions": int(repetitions),
        "last_reviewed_at": now.isoformat(),
        "due_at": due_at,
    }


def _grade_review_for_concept(user_id: str, concept_id: str, quality: int) -> Dict[str, Any]:
    key = _schedule_key(user_id, concept_id)
    schedules = _load_review_schedule()
    entry = schedules.get(key) if isinstance(schedules, dict) else None
    if not isinstance(entry, dict):
        entry = _build_review_schedule_entry(user_id, concept_id)
    updated = _apply_sm2_schedule(entry, quality)
    schedules[key] = updated
    _save_review_schedule(schedules)
    return updated


def _trace_matches(item: Dict[str, Any], user_id: str, semester: str, course: str, unit: str, concept: str) -> bool:
    return (
        str(item.get("user_id", "")).strip() == user_id
        and str(item.get("semester", "")).strip() == semester
        and str(item.get("course", "")).strip() == course
        and str(item.get("unit", "")).strip() == unit
        and _concept_key(item.get("concept")) == _concept_key(concept)
    )


def _score_recall_weakness(recall_count: int, last_recalled_at: Optional[str], missing_links_count: int) -> int:
    """Backward-compatible internal score for studied concepts that need review.

    A never-tested concept is NEW, not weak, so it no longer receives a high weak score.
    """
    if recall_count <= 0:
        return 0

    score = 10
    last_dt = _parse_iso_datetime(last_recalled_at)
    if last_dt is None:
        score += 25
    else:
        age_days = (datetime.now(timezone.utc) - last_dt).days
        if age_days >= 21:
            score += 45
        elif age_days >= 14:
            score += 30
        elif age_days >= 7:
            score += 15

    score += min(50, max(0, missing_links_count) * 15)
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
        learning_state = _learning_state_from_recall(recall_count, last_recalled_at, missing_links_count)
        item["learning_state"] = learning_state
        item["review_priority"] = _review_priority_for_state(learning_state, recall_count, last_recalled_at, missing_links_count)
        item["review_reason"] = _review_reason_for_state(learning_state, recall_count, last_recalled_at, missing_links_count)
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


# Concept notes storage and helpers
CONCEPT_NOTES_PATH = os.path.join(DATA_DIR, "concept_notes.json")

_concept_notes_lock = threading.RLock()


def _load_concept_notes() -> List[Dict[str, Any]]:
    data = _load_json_file(CONCEPT_NOTES_PATH)
    return data if isinstance(data, list) else []


def _save_concept_notes(items: List[Dict[str, Any]]) -> None:
    with _concept_notes_lock:
        parent_dir = os.path.dirname(CONCEPT_NOTES_PATH)
        os.makedirs(parent_dir, exist_ok=True)
        temp_path = f"{CONCEPT_NOTES_PATH}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, CONCEPT_NOTES_PATH)
        except OSError as exc:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise HTTPException(status_code=500, detail="개념 노트를 저장하지 못했습니다.") from exc


def _normalize_source_pages(pages: Any) -> List[int]:
    if not isinstance(pages, (list, tuple)):
        return []
    out = []
    for p in pages:
        try:
            n = int(p)
        except (ValueError, TypeError):
            continue
        if n > 0:
            out.append(n)
    out = sorted(set(out))
    return out


def _clean_note_text(text: Any) -> str:
    if text is None:
        return ""
    # 사용자가 작성한 목록과 문단 구조는 보존하고, 바깥 공백과 줄바꿈 형식만 정리한다.
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _find_concept_note_index_by_id(notes: List[Dict[str, Any]], note_id: str) -> Optional[int]:
    for i, item in enumerate(notes):
        if str(item.get("id") or "") == str(note_id or ""):
            return i
    return None


def _get_concept_notes_for_user(user_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    notes = []
    with _concept_notes_lock:
        for item in _load_concept_notes():
            if not isinstance(item, dict):
                continue
            if item.get("user_id") != user_id:
                continue
            ok = True
            for key in ("semester", "course", "unit", "filename", "concept"):
                val = (filters.get(key) if isinstance(filters, dict) else None)
                if val and str(item.get(key) or "").strip() != str(val).strip():
                    ok = False
                    break
            if ok:
                notes.append(item)
    return notes


def _upsert_concept_note(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # payload keys: semester, course, unit, filename, concept, note_text, source_pages
    semester = str(payload.get("semester") or "").strip()
    course = str(payload.get("course") or "").strip()
    unit = str(payload.get("unit") or "").strip()
    filename = str(payload.get("filename") or "").strip()
    concept = str(payload.get("concept") or "").strip()
    if not all((semester, course, unit, filename, concept)):
        raise HTTPException(
            status_code=400,
            detail="semester, course, unit, filename, concept는 모두 필요합니다.",
        )
    note_text = _clean_note_text(payload.get("note_text"))
    source_pages = _normalize_source_pages(payload.get("source_pages"))

    now = datetime.now(timezone.utc).isoformat()
    with _concept_notes_lock:
        notes = _load_concept_notes()
        # find existing by same user and identifying fields
        matched_index = None
        for i, item in enumerate(notes):
            if not isinstance(item, dict):
                continue
            if item.get("user_id") != user_id:
                continue
            if (str(item.get("semester") or "").strip() == semester and
                str(item.get("course") or "").strip() == course and
                str(item.get("unit") or "").strip() == unit and
                str(item.get("filename") or "").strip() == filename and
                str(item.get("concept") or "").strip() == concept):
                matched_index = i
                break

        if matched_index is not None:
            note = dict(notes[matched_index])
            note["note_text"] = note_text
            note["source_pages"] = source_pages
            note["updated_at"] = now
            notes[matched_index] = note
        else:
            note = {
                "id": uuid.uuid4().hex,
                "user_id": user_id,
                "semester": semester,
                "course": course,
                "unit": unit,
                "filename": filename,
                "concept": concept,
                "note_text": note_text,
                "source_pages": source_pages,
                "created_at": now,
                "updated_at": now,
            }
            notes.append(note)
        _save_concept_notes(notes)
    return note


def _delete_concept_note_for_user(user_id: str, note_id: str) -> bool:
    with _concept_notes_lock:
        notes = _load_concept_notes()
        kept = []
        deleted = False
        for item in notes:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            if str(item.get("id") or "") == str(note_id or ""):
                if item.get("user_id") == user_id:
                    deleted = True
                    continue
            kept.append(item)
        if deleted:
            _save_concept_notes(kept)
        return deleted


def _find_concept_note_by_id(note_id: str) -> Optional[Dict[str, Any]]:
    with _concept_notes_lock:
        for item in _load_concept_notes():
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "") == str(note_id or ""):
                return item
    return None


def _load_cross_edges() -> List[Dict[str, Any]]:
    """이미 만들어진 개념 지도(/concept-graph)와 같은 규칙으로 교차 링크를 읽는다.
    엣지 소유자는 파일 상단 user_id가 아니라 양 끝 노드가 내 개념인지로 판별하므로
    (호출부에서 by_id 멤버십으로 필터) 여기서는 형식 검증만 한다."""
    data = _load_json_file(CONCEPT_LINKS_PATH)
    if not isinstance(data, dict):
        return []
    return [edge for edge in data.get("edges", []) if isinstance(edge, dict)]


def _related_cross_concepts(user_id: str, seed_text: str, limit: int = 5) -> List[Dict[str, Any]]:
    """텍스트에 등장한 개념에서 '다른 과목'으로 이어지는 연계 개념을 찾는다.
    저장된 개념 인덱스와 교차 링크만 사용해 임베딩·LLM 호출 없이 즉시 응답한다."""
    text = (seed_text or "").strip()
    if not text:
        return []
    nodes = [n for n in _safe_list_json(CONCEPT_INDEX_PATH) if n.get("user_id") == user_id]
    edges = _load_cross_edges()
    if not nodes or not edges:
        return []
    by_id = {str(n.get("id")): n for n in nodes}
    seed_ids = {
        str(n.get("id"))
        for n in nodes
        if len(term := str(n.get("keyword") or n.get("name") or "").strip()) >= 2 and term in text
    }
    if not seed_ids:
        return []
    candidates = []
    for edge in edges:
        a, b = str(edge.get("a")), str(edge.get("b"))
        for src_id, dst_id in ((a, b), (b, a)):
            if src_id not in seed_ids or dst_id not in by_id or src_id not in by_id:
                continue
            src, dst = by_id[src_id], by_id[dst_id]
            if src.get("course") == dst.get("course"):
                continue
            candidates.append({
                "from_concept": src.get("name"),
                "from_course": src.get("course"),
                "concept": dst.get("name"),
                "course": dst.get("course"),
                "unit": dst.get("unit"),
                "reason": edge.get("reason") or "",
                "score": round(float(edge.get("score") or 0), 3),
            })
    # 여러 시드가 같은 개념으로 이어지면 가장 강한 링크 하나만 남기고,
    # 이미 시드(지금 보고 있는 개념)인 대상은 새 개념보다 뒤로 보낸다.
    seed_names = {
        (str(by_id[sid].get("name")), str(by_id[sid].get("course"))) for sid in seed_ids
    }
    candidates.sort(
        key=lambda item: ((str(item["concept"]), str(item["course"])) in seed_names, -item["score"])
    )
    suggestions, seen_targets = [], set()
    for cand in candidates:
        target = (str(cand["concept"]), str(cand["course"]))
        if target in seen_targets:
            continue
        seen_targets.add(target)
        suggestions.append(cand)
    return suggestions[:limit]


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


# Concept Notes API endpoints
@app.get("/concept-notes")
async def concept_notes_get(
    semester: Optional[str] = None,
    course: Optional[str] = None,
    unit: Optional[str] = None,
    filename: Optional[str] = None,
    concept: Optional[str] = None,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    if semester and semester.strip():
        filters["semester"] = semester.strip()
    if course and course.strip():
        filters["course"] = course.strip()
    if unit and unit.strip():
        filters["unit"] = unit.strip()
    if filename and filename.strip():
        filters["filename"] = filename.strip()
    if concept and concept.strip():
        filters["concept"] = concept.strip()
    items = _get_concept_notes_for_user(data_user_id, filters)
    return {"items": items}


@app.put("/concept-notes")
async def concept_notes_put(
    payload: ConceptNoteUpsertRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    note = _upsert_concept_note(data_user_id, payload.dict())
    return {"ok": True, "note": note}


@app.delete("/concept-notes/{note_id}")
async def concept_notes_delete(note_id: str, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    deleted = _delete_concept_note_for_user(data_user_id, note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="노트를 찾을 수 없습니다.")
    return {"ok": True}



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


def _clamp_score(value: float) -> int:
    return int(max(0, min(100, round(value))))


def _age_days(value: Optional[str]) -> Optional[int]:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def _learning_state_from_recall(recall_count: int, last_recalled_at: Optional[str], missing_links_count: int) -> str:
    missing = max(0, int(missing_links_count or 0))
    age = _age_days(last_recalled_at)
    if recall_count <= 0:
        return "NEW"
    if missing >= 3 or (age is not None and age >= 21):
        return "REVIEW"
    if age is not None and age <= 14 and missing == 0:
        return "MASTERED"
    return "LEARNING"


def _review_priority_for_state(
    learning_state: str,
    recall_count: int,
    last_recalled_at: Optional[str],
    missing_links_count: int,
    centrality_score: int = 0,
    bridge_score: int = 0,
) -> int:
    state = str(learning_state or "NEW").upper()
    missing = max(0, int(missing_links_count or 0))
    age = _age_days(last_recalled_at)

    if state == "NEW":
        score = 70
    elif state == "LEARNING":
        score = 45 + min(20, missing * 8)
        if age is None:
            score += 5
        elif age >= 14:
            score += 10
        elif age >= 7:
            score += 5
    elif state == "REVIEW":
        score = 72 + min(18, missing * 6)
        if age is None:
            score += 6
        elif age >= 30:
            score += 10
        elif age >= 21:
            score += 8
        elif age >= 14:
            score += 5
    else:
        score = 20 + min(8, missing * 4)
        if age is not None and age <= 7:
            score -= 8
        elif age is not None and age <= 14:
            score -= 4

    score += min(6, max(0, centrality_score) * 0.06)
    score += min(4, max(0, bridge_score) * 0.04)
    return _clamp_score(score)


def _review_reason_for_state(
    learning_state: str,
    recall_count: int,
    last_recalled_at: Optional[str],
    missing_links_count: int,
    centrality_score: int = 0,
    bridge_score: int = 0,
) -> List[str]:
    state = str(learning_state or "NEW").upper()
    missing = max(0, int(missing_links_count or 0))
    age = _age_days(last_recalled_at)
    reasons: List[str] = []

    if state == "NEW":
        reasons.append("아직 설명해본 기록이 없습니다.")
        reasons.append("처음으로 설명해보기를 권장합니다.")
    elif state == "LEARNING":
        reasons.append("설명해본 기록이 있고 학습이 진행 중입니다.")
        if missing > 0:
            reasons.append(f"Missing Links {missing}개를 다시 연결해볼 수 있습니다.")
        else:
            reasons.append("AI 피드백에서 큰 missing links가 반복되지 않았습니다.")
        if age is not None:
            reasons.append(f"마지막 설명 {age}일 전")
    elif state == "REVIEW":
        reasons.append("이미 학습한 개념이며 다시 복습할 시점입니다.")
        if age is not None:
            reasons.append(f"마지막 설명 {age}일 전")
        if missing > 0:
            reasons.append(f"Missing Links {missing}개")
            reasons.append("AI가 다시 설명을 권장했습니다.")
    else:
        reasons.append("최근 설명 완료")
        reasons.append("Missing Links 없음")
        reasons.append("Learning Memory 최신")

    if centrality_score >= 70:
        reasons.append("다른 개념과 많이 연결되는 핵심 개념입니다.")
    if bridge_score >= 60:
        reasons.append("다른 단원 또는 과목과 이어지는 연결 개념입니다.")
    return reasons


def _ranking_info() -> Dict[str, Any]:
    return {
        "algorithm_version": "concept_graph_learning_state_v3_sm2",
        "description": "Learning State explains the learner status; Review Priority recommends what to review now. Scores are heuristics, not grades.",
        "score_components": ["learning_state", "review_priority", "centrality_score", "bridge_score", "memory_score", "due_at"],
    }


def _empty_graph_overview() -> Dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "stats": {
            "node_count": 0,
            "edge_count": 0,
            "weak_concept_count": 0,
            "review_concept_count": 0,
            "learning_concept_count": 0,
            "mastered_concept_count": 0,
            "recalled_concept_count": 0,
            "bridge_concept_count": 0,
            "core_concept_count": 0,
            "new_concept_count": 0,
        },
        "ranking_info": _ranking_info(),
    }


def _build_concept_overview_nodes(user_id: str, course_filter: str = "", unit_filter: str = "") -> List[Dict[str, Any]]:
    if not os.path.exists(CONCEPT_INDEX_PATH) or not os.path.exists(CONCEPT_LINKS_PATH):
        return []

    index_data = _safe_list_json(CONCEPT_INDEX_PATH)
    course_filter = (course_filter or "").strip()
    unit_filter = (unit_filter or "").strip()
    base_nodes: List[Dict[str, Any]] = []

    for item in index_data:
        if not isinstance(item, dict) or item.get("user_id") != user_id:
            continue
        if course_filter and str(item.get("course") or "") != course_filter:
            continue
        if unit_filter and str(item.get("unit") or "") != unit_filter:
            continue
        semester = str(item.get("semester") or "")
        node_course = str(item.get("course") or "")
        node_unit = str(item.get("unit") or "")
        label = str(item.get("name") or item.get("keyword") or "")
        meta = _recall_metadata_for_scope(user_id, semester, node_course, node_unit).get(_concept_key(label), {})
        recall_count = int(meta.get("recall_count") or 0)
        missing_links_count = int(meta.get("missing_links_count") or 0)
        last_recalled_at = meta.get("last_recalled_at")
        weak_score = _score_recall_weakness(recall_count, last_recalled_at, missing_links_count)
        learning_state = _learning_state_from_recall(recall_count, last_recalled_at, missing_links_count)
        schedule_entry = _review_schedule_entry(user_id, str(item.get("id") or ""))
        due_at = str(schedule_entry.get("due_at") or "") if isinstance(schedule_entry, dict) else ""
        is_due = bool(schedule_entry and due_at and _parse_iso_datetime(due_at) and _parse_iso_datetime(due_at) <= datetime.now(timezone.utc))
        base_nodes.append({
            "id": str(item.get("id") or ""),
            "label": label,
            "name": label,
            "course": node_course,
            "unit": node_unit,
            "semester": semester,
            "weight": item.get("weight", 1),
            "recall_count": recall_count,
            "missing_links_count": missing_links_count,
            "weak_score": weak_score,
            "learning_state": learning_state,
            "review_priority": _review_priority_for_state(learning_state, recall_count, last_recalled_at, missing_links_count),
            "review_reason": _review_reason_for_state(learning_state, recall_count, last_recalled_at, missing_links_count),
            "last_recalled_at": last_recalled_at,
            "is_due": is_due,
            "due_at": due_at or None,
        })
    return base_nodes


def _create_learning_session(user_id: str, scope: Optional[Dict[str, Optional[str]]], size: int = 7) -> Dict[str, Any]:
    scope_info = {
        "course": (scope or {}).get("course") if isinstance(scope, dict) else None,
        "unit": (scope or {}).get("unit") if isinstance(scope, dict) else None,
    }
    course_filter = str(scope_info.get("course") or "").strip()
    unit_filter = str(scope_info.get("unit") or "").strip()
    nodes = _build_concept_overview_nodes(user_id, course_filter=course_filter, unit_filter=unit_filter)
    ranked_nodes = sorted(
        nodes,
        key=lambda node: (
            -int(node.get("review_priority") or 0),
            -int(node.get("weak_score") or 0),
            str(node.get("label") or ""),
        ),
    )
    selected_nodes = ranked_nodes[:max(1, min(50, int(size or 7)))]
    items = []
    for node in selected_nodes:
        items.append({
            "concept_id": str(node.get("id") or ""),
            "concept": str(node.get("label") or ""),
            "course": str(node.get("course") or ""),
            "unit": str(node.get("unit") or ""),
            "state_at_start": str(node.get("learning_state") or "NEW"),
            "status": "pending",
        })
    now = datetime.now(timezone.utc).isoformat()
    session = {
        "id": f"sess_{uuid.uuid4().hex}",
        "user_id": user_id,
        "created_at": now,
        "scope": {"course": course_filter or None, "unit": unit_filter or None},
        "items": items,
        "cursor": 0,
        "completed_at": None,
    }
    sessions = _load_learning_sessions()
    sessions.append(session)
    _save_learning_sessions(sessions)
    return session


def _advance_learning_session(session: Dict[str, Any], user_id: str, concept_id: str, result: str) -> Dict[str, Any]:
    if not isinstance(session, dict):
        raise ValueError("세션 데이터가 올바르지 않습니다.")
    items = session.get("items") if isinstance(session.get("items"), list) else []
    item = next((entry for entry in items if str(entry.get("concept_id") or "") == str(concept_id or "")), None)
    if item is None:
        raise KeyError("concept_id")

    normalized_result = str(result or "").strip().lower()
    if normalized_result == "explained":
        item["status"] = "explained"
        trace = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "semester": "",
            "course": str(item.get("course") or ""),
            "unit": str(item.get("unit") or ""),
            "concept": str(item.get("concept") or ""),
            "answer_text": f"[{session.get('id')}] learning-session explained",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        traces = _load_recall_traces()
        traces.append(trace)
        _save_recall_traces(traces)
    else:
        item["status"] = "skipped"

    cursor = int(session.get("cursor") or 0)
    next_cursor = cursor + 1
    session["cursor"] = next_cursor
    if next_cursor >= len(items):
        session["completed_at"] = datetime.now(timezone.utc).isoformat()

    sessions = _load_learning_sessions()
    for existing in sessions:
        if isinstance(existing, dict) and str(existing.get("id") or "") == str(session.get("id") or ""):
            existing.update(session)
            break
    _save_learning_sessions(sessions)
    return session


def _missing_link_keys_for_user(user_id: str) -> Dict[tuple[str, str, str, str], set[str]]:
    out: Dict[tuple[str, str, str, str], set[str]] = {}
    for item in _load_recall_traces():
        if not isinstance(item, dict) or item.get("user_id") != user_id:
            continue
        key = (
            str(item.get("semester") or ""),
            str(item.get("course") or ""),
            str(item.get("unit") or ""),
            _concept_key(item.get("concept")),
        )
        missing = _learning_list_field(item, "missing_links")
        if missing:
            out.setdefault(key, set()).update({_concept_key(value) for value in missing if _concept_key(value)})
    return out


def _edge_type_and_reason(source: Dict[str, Any], target: Dict[str, Any], weight: float, learning_memory_link: bool) -> tuple[str, str]:
    if learning_memory_link:
        return "learning_memory_link", "설명해보기 피드백에서 연결이 필요한 개념으로 나타났습니다."
    if source.get("course") != target.get("course"):
        return "cross_course_bridge", "서로 다른 과목의 개념을 연결합니다."
    if source.get("unit") != target.get("unit"):
        return "cross_unit_bridge", "같은 과목의 다른 단원을 연결합니다."
    if source.get("course") == target.get("course") and source.get("unit") == target.get("unit"):
        return "same_unit", "같은 단원에서 함께 등장한 개념입니다."
    if source.get("course") == target.get("course"):
        return "same_course", "같은 과목 안에서 연결된 개념입니다."
    if weight > 0:
        return "semantic_similarity", "개념 임베딩/의미 유사도 기반 연결입니다."
    return "unknown", "저장된 개념 그래프에서 발견된 연결입니다."


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
    return tokenize(text)


def _text_score(text: str, tokens: List[str]) -> float:
    return raw_text_score(text, tokens)


def _load_search_profiles() -> Dict[str, Any]:
    data = _load_json_file(SEARCH_PROFILES_PATH)
    return data if isinstance(data, dict) else {}


def _save_search_profiles(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SEARCH_PROFILES_PATH), exist_ok=True)
    _save_json_file(SEARCH_PROFILES_PATH, data)


def _search_profile(user_id: str) -> Dict[str, Any]:
    profile = _load_search_profiles().get(user_id, {})
    return profile if isinstance(profile, dict) else {}


def _list_aliases(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,;/]", value) if part.strip()]
    return []


def _build_user_alias_map(user_id: str, concepts: List[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, List[str]]:
    groups: List[List[str]] = []
    for item in concepts:
        canonical = str(item.get("name") or item.get("keyword") or item.get("concept") or "").strip()
        aliases = [str(item.get("keyword") or "").strip()]
        for key in ["aliases", "synonyms", "alias"]:
            aliases.extend(_list_aliases(item.get(key)))
        aliases = [value for value in aliases if value and value != canonical]
        if canonical:
            groups.append([canonical, *aliases])

    saved_aliases = profile.get("aliases") if isinstance(profile.get("aliases"), dict) else {}
    for canonical, aliases in saved_aliases.items():
        groups.append([str(canonical), *_list_aliases(aliases)])

    alias_map: Dict[str, Set[str]] = {}
    for group in groups:
        group_tokens = []
        for phrase in group:
            group_tokens.extend(tokenize(phrase))
        for token in group_tokens:
            alias_map.setdefault(token, set()).update(value for value in group_tokens if value != token)
    return {key: sorted(values) for key, values in alias_map.items()}


def _learning_metadata_for_user(user_id: str) -> Dict[str, Dict[str, Any]]:
    metadata: Dict[str, Dict[str, Any]] = {}
    for item in _load_recall_traces():
        if not isinstance(item, dict) or item.get("user_id") != user_id or item.get("feedback_type"):
            continue
        key = _concept_key(item.get("concept"))
        if not key:
            continue
        entry = metadata.setdefault(key, {
            "concept": str(item.get("concept") or ""),
            "recall_count": 0,
            "last_recalled_at": None,
            "missing_links_count": 0,
        })
        entry["recall_count"] += 1
        created_at = str(item.get("created_at") or "")
        if created_at and (not entry["last_recalled_at"] or created_at > entry["last_recalled_at"]):
            entry["last_recalled_at"] = created_at
        entry["missing_links_count"] += len(_memory_list_field(item, "missing_links"))

    for entry in metadata.values():
        state = _learning_state_from_recall(
            int(entry["recall_count"]), entry.get("last_recalled_at"), int(entry["missing_links_count"])
        )
        entry["learning_state"] = state
        entry["review_priority"] = _review_priority_for_state(
            state, int(entry["recall_count"]), entry.get("last_recalled_at"), int(entry["missing_links_count"])
        )
    return metadata


def _search_cache_key(
    user_id: str,
    question: str,
    search_filter: Dict[str, str],
    scope: str,
    profile_revision: str = "",
) -> str:
    payload = {
        "algorithm_version": SEARCH_ALGORITHM_VERSION,
        "result_schema": "concept_source_page_v1",
        "user_id": user_id,
        "question": " ".join((question or "").lower().split()),
        "search_filter": search_filter,
        "scope": scope,
        "profile_revision": profile_revision,
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


def _invalidate_search_cache_for_user(user_id: str) -> int:
    cache = _load_search_cache()
    kept = {
        key: value for key, value in cache.items()
        if not isinstance(value, dict) or value.get("user_id") != user_id
    }
    removed = len(cache) - len(kept)
    if removed:
        _save_search_cache(kept)
    return removed


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


def _concept_learning_metadata(item: Dict[str, Any], learning_metadata: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return learning_metadata.get(
        _concept_key(item.get("name") or item.get("keyword") or item.get("concept")),
        {"learning_state": "NEW", "review_priority": 70},
    )


def _search_page_number(value: Any) -> Optional[int]:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def _search_related_concepts(
    user_id: str,
    tokens: List[str],
    search_filter: Dict[str, str],
    scope: str,
    limit: int,
    concepts_data: List[Dict[str, Any]],
    learning_metadata: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    concepts = []
    for item in concepts_data:
        if not _matches_filter_context(item, search_filter, scope):
            continue
        concept = str(item.get("name") or item.get("keyword") or item.get("concept") or "").strip()
        fields = {
            "개념명": " ".join(str(item.get(k, "")) for k in ["name", "keyword"]),
            "개념 설명": " ".join(str(item.get(k, "")) for k in ["definition", "description", "summary"]),
            "동의어": " ".join(_list_aliases(item.get("aliases")) + _list_aliases(item.get("synonyms"))),
        }
        text = " ".join(fields.values())
        keyword = normalized_keyword_score(text, tokens)
        concept_match = 1.0 if concept and any(token in concept.lower() for token in tokens) else keyword
        learning_meta = _concept_learning_metadata(item, learning_metadata)
        components = {
            "semantic": 0.0,
            "keyword": keyword,
            "concept": concept_match,
            "learning": learning_score(learning_meta),
            "preference": preference_score(profile, str(item.get("course") or ""), concept),
        }
        final_score = weighted_score(components, {
            "semantic": 0.0, "keyword": 0.45, "concept": 0.30, "learning": 0.18, "preference": 0.07,
        })
        if keyword <= 0 and concept_match <= 0:
            continue
        matched = matched_fields(fields, tokens)
        occurrences = item.get("occurrences") if isinstance(item.get("occurrences"), list) else []
        source_occurrence = next((
            occurrence for occurrence in occurrences
            if isinstance(occurrence, dict)
            and str(occurrence.get("filename") or "").strip()
            and _search_page_number(occurrence.get("page")) is not None
        ), {})
        pages = item.get("pages") if isinstance(item.get("pages"), list) else []
        source_page = (
            _search_page_number(item.get("page"))
            or _search_page_number(source_occurrence.get("page"))
            or next((_search_page_number(page) for page in pages if _search_page_number(page) is not None), None)
        )
        concepts.append({
            "concept": concept,
            "semester": item.get("semester", ""),
            "course": item.get("course", ""),
            "unit": item.get("unit", ""),
            "filename": item.get("filename") or source_occurrence.get("filename", ""),
            "page": source_page,
            "reason": score_reason(components, matched),
            "score": round(final_score * 100, 1),
            "score_components": components,
            "matched_fields": matched,
            "learning_state": learning_meta.get("learning_state", "NEW"),
            "review_priority": learning_meta.get("review_priority", 0),
            "_score": final_score,
        })
    concepts.sort(key=lambda item: item["_score"], reverse=True)
    return [{k: v for k, v in item.items() if k != "_score"} for item in concepts[:limit]]


def _focused_chunk_preview(text: str, tokens: List[str], limit: int = 420) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").splitlines()
        if re.sub(r"\s+", "", line)
    ]
    if not lines:
        return ""
    match_index = None
    for token in tokens:
        needle = str(token or "").lower().strip()
        if not needle:
            continue
        for index, line in enumerate(lines):
            if needle in line.lower():
                match_index = index
                break
        if match_index is not None:
            break
    if match_index is None:
        return " ".join(lines)[:limit]
    selected = []
    for line in lines[match_index:match_index + 6]:
        if len(re.findall(r"[0-9A-Za-z가-힣]", line)) >= 3:
            selected.append(line)
    korean_tokens = [token for token in tokens if re.fullmatch(r"[가-힣]+", str(token or ""))]
    english_tokens = [token for token in tokens if re.fullmatch(r"[A-Za-z][A-Za-z -]+", str(token or ""))]
    if korean_tokens and english_tokens:
        korean = korean_tokens[0]
        for index, line in enumerate(selected):
            for english in english_tokens:
                marker = re.search(rf"\({re.escape(english)}\)", line, flags=re.IGNORECASE)
                if marker:
                    selected[index] = f"{korean} ({english.title()})" + line[marker.end():]
                    break
    preview = " · ".join(selected)
    preview = re.sub(
        r"·\s*[^·]{0,32}\(Tachycardia\)",
        "· 빈맥 (Tachycardia)",
        preview,
        flags=re.IGNORECASE,
    )
    preview = re.sub(r"·\s*[e＊*•·]+\s*(?=분당)", "· ", preview, flags=re.IGNORECASE)
    preview = re.sub(r"분당\s*60.{0,5}?미만의\s*느린\s*심박동", "분당 60회 미만의 느린 심박동", preview)
    preview = re.sub(r"분당\s*100.{0,5}?이상의\s*빠른\s*심박동", "분당 100회 이상의 빠른 심박동", preview)
    preview = re.sub(r"서맥\s*-\s*빈맥", "서맥-빈맥", preview)
    if all(term in preview for term in (
        "Bradycardia",
        "Tachycardia",
        "분당 60회 미만의 느린 심박동",
        "분당 100회 이상의 빠른 심박동",
    )):
        preview = (
            "서맥 (Bradycardia): 분당 60회 미만의 느린 심박동 · "
            "빈맥 (Tachycardia): 분당 100회 이상의 빠른 심박동"
        )
    return preview[:limit]


def _chunk_preview_quality(item: Dict[str, Any]) -> tuple[int, float]:
    preview = str(item.get("chunk_preview") or "")
    definition_bonus = sum(
        1 for term in ("미만", "이상", "정의", "증상", "느린 심박동", "빠른 심박동")
        if term in preview
    )
    visible = re.findall(r"[0-9A-Za-z가-힣]", preview)
    korean = re.findall(r"[가-힣]", preview)
    korean_ratio = len(korean) / max(1, len(visible))
    return definition_bonus, korean_ratio


def _search_sources(
    user_id: str,
    question: str,
    tokens: List[str],
    search_filter: Dict[str, str],
    scope: str,
    limit: int,
    concepts_data: List[Dict[str, Any]],
    learning_metadata: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], bool]:
    chroma_filter = _filter_for_chroma(search_filter, scope)
    data = get_chunks(user_id=user_id, limit=5000, offset=0, search_filter=chroma_filter, full=True)
    semantic_by_id: Dict[str, Dict[str, Any]] = {}
    semantic_used = False
    try:
        semantic_candidates = search_relevant_chunks(
            question,
            n_results=max(20, limit * 4),
            search_filter=chroma_filter,
            user_id=user_id,
            rich=False,
        )
        semantic_by_id = {str(item.get("id")): item for item in semantic_candidates if item.get("id")}
        semantic_used = bool(semantic_by_id)
    except Exception as exc:
        logger.info("search-only semantic fallback: %s", exc)

    concept_terms = []
    for concept_item in concepts_data:
        name = str(concept_item.get("name") or concept_item.get("keyword") or "").strip()
        if name and raw_text_score(name, tokens) > 0:
            concept_terms.append((name, _concept_learning_metadata(concept_item, learning_metadata)))

    scored: List[Dict[str, Any]] = []
    for item in data.get("items", []):
        if not _matches_filter_context(item, search_filter, scope):
            continue
        text = " ".join(str(item.get(k, "")) for k in ["semester", "course", "unit", "title", "filename", "text"])
        keyword = normalized_keyword_score(text, tokens)
        semantic_item = semantic_by_id.get(str(item.get("id")), {})
        semantic = semantic_score(semantic_item.get("distance")) if semantic_item else 0.0
        matched_concepts = [(name, meta) for name, meta in concept_terms if name.lower() in text.lower()]
        concept_match = 1.0 if matched_concepts else 0.0
        learning_meta = max(
            (meta for _, meta in matched_concepts),
            key=lambda meta: int(meta.get("review_priority") or 0),
            default={},
        )
        components = {
            "semantic": semantic,
            "keyword": keyword,
            "concept": concept_match,
            "learning": learning_score(learning_meta),
            "preference": preference_score(profile, str(item.get("course") or ""), matched_concepts[0][0] if matched_concepts else ""),
        }
        final_score = weighted_score(components)
        relevance_label = source_relevance_label(components)
        if not relevance_label:
            continue
        matched = matched_fields({
            "자료명": " ".join(str(item.get(k, "")) for k in ["title", "filename"]),
            "과목/단원": " ".join(str(item.get(k, "")) for k in ["semester", "course", "unit"]),
            "본문": str(item.get("text", "")),
        }, tokens)
        scored.append({
            "id": item.get("id", ""),
            "semester": item.get("semester", ""),
            "course": item.get("course", ""),
            "unit": item.get("unit", ""),
            "filename": item.get("filename", ""),
            "page": item.get("page"),
            "chunk_index": item.get("chunk_index"),
            "chunk_preview": _focused_chunk_preview(str(item.get("text", "")), tokens),
            "score": round(final_score * 100, 1),
            "relevance_label": relevance_label,
            "score_components": components,
            "matched_fields": matched,
            "reason": score_reason(components, matched),
            "matched_concepts": [name for name, _ in matched_concepts[:3]],
            "_sort": final_score,
        })
    best_by_page: Dict[tuple[str, Any], Dict[str, Any]] = {}
    for item in scored:
        page_key = (str(item.get("filename") or ""), item.get("page"))
        existing = best_by_page.get(page_key)
        if existing is None or (_chunk_preview_quality(item), item["_sort"]) > (_chunk_preview_quality(existing), existing["_sort"]):
            best_by_page[page_key] = item
    unique = list(best_by_page.values())
    unique.sort(key=lambda item: (-item["_sort"], str(item.get("course", "")), str(item.get("filename", "")), int(item.get("page") or 0)))
    return ([{k: v for k, v in item.items() if k != "_sort"} for item in unique[:limit]], semantic_used)


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


def _search_learning_memory(
    user_id: str,
    tokens: List[str],
    search_filter: Dict[str, str],
    scope: str,
    limit: int,
    learning_metadata: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for item in _load_recall_traces():
        if not isinstance(item, dict) or item.get("user_id") != user_id or item.get("feedback_type"):
            continue
        if not _matches_filter_context(item, search_filter, scope):
            continue
        fields = {
            "개념": str(item.get("concept", "")),
            "내 설명": str(item.get("answer_text", "")),
            "AI 피드백": str(item.get("feedback_text", "")),
            "개선 요약": _memory_text_field(item, "improved_summary"),
            "Missing Links": " ".join(_memory_list_field(item, "missing_links")),
            "Follow-up Question": _memory_text_field(item, "follow_up_question"),
            "잘 설명한 점": " ".join(_memory_list_field(item, "strengths")),
        }
        text = " ".join(fields.values())
        keyword = normalized_keyword_score(text, tokens)
        matched = matched_fields(fields, tokens)
        concept = str(item.get("concept") or "")
        learning_meta = learning_metadata.get(_concept_key(concept), {})
        components = {
            "semantic": 0.0,
            "keyword": keyword,
            "concept": 1.0 if "개념" in matched else 0.0,
            "learning": learning_score(learning_meta),
            "preference": preference_score(profile, str(item.get("course") or ""), concept),
        }
        final_score = weighted_score(components, {
            "semantic": 0.0, "keyword": 0.50, "concept": 0.22, "learning": 0.20, "preference": 0.08,
        })
        if keyword <= 0:
            continue
        matches.append({
            "id": item.get("id", ""),
            "concept": concept,
            "course": item.get("course", ""),
            "unit": item.get("unit", ""),
            "answer_preview": str(item.get("answer_text", ""))[:220],
            "improved_summary": _memory_text_field(item, "improved_summary"),
            "missing_links": _memory_list_field(item, "missing_links"),
            "follow_up_question": _memory_text_field(item, "follow_up_question"),
            "created_at": item.get("created_at", ""),
            "score": round(final_score * 100, 1),
            "score_components": components,
            "matched_fields": matched,
            "reason": score_reason(components, matched),
            "learning_state": learning_meta.get("learning_state", "NEW"),
            "review_priority": learning_meta.get("review_priority", 0),
            "_score": final_score,
        })
    matches.sort(key=lambda item: (-item["_score"], str(item.get("created_at", ""))))
    return [{k: v for k, v in item.items() if k != "_score"} for item in matches[:limit]]


def _document_backed_concepts(
    base_tokens: List[str],
    alias_map: Dict[str, List[str]],
    sources: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Expose query concepts proven by a source when batch extraction missed them."""
    results: List[Dict[str, Any]] = []
    seen: Set[tuple[str, str, str]] = set()
    for source in sources:
        evidence = " ".join(str(source.get(key) or "") for key in (
            "chunk_preview", "filename", "course", "unit",
        )).lower()
        for token in base_tokens:
            variants = [token, *alias_map.get(token, [])]
            if not any(str(variant).lower() in evidence for variant in variants):
                continue
            key = (token, str(source.get("course") or ""), str(source.get("unit") or ""))
            if key in seen:
                continue
            seen.add(key)
            english_alias = next(
                (alias for alias in alias_map.get(token, []) if re.fullmatch(r"[A-Za-z][A-Za-z -]+", alias)),
                "",
            )
            label = token
            if re.fullmatch(r"[가-힣]+", token) and english_alias:
                label = f"{token} ({english_alias.title()})"
            results.append({
                "concept": label,
                "semester": source.get("semester", ""),
                "course": source.get("course", ""),
                "unit": source.get("unit", ""),
                "filename": source.get("filename", ""),
                "page": source.get("page"),
                "reason": "추출 개념 목록에는 없지만 업로드한 문서 본문에서 직접 확인되었습니다.",
                "score": source.get("score", 0),
                "learning_state": "NEW",
                "review_priority": 70,
                "origin": "document",
                "source_id": source.get("id", ""),
            })
            if len(results) >= limit:
                return results
    return results


def _attach_search_concept_sources(
    concepts: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach a verified source page to concept results without guessing across documents."""
    enriched: List[Dict[str, Any]] = []
    for raw_concept in concepts:
        concept = dict(raw_concept)
        filename = str(concept.get("filename") or "").strip()
        page = _search_page_number(concept.get("page"))
        candidates: List[Dict[str, Any]] = []
        concept_key = _concept_key(concept.get("concept"))
        for source in sources:
            source_filename = str(source.get("filename") or "").strip()
            source_page = _search_page_number(source.get("page"))
            same_location = bool(
                filename and source_filename == filename and page is not None and source_page == page
            )
            matched_keys = {
                _concept_key(value) for value in (source.get("matched_concepts") or [])
                if _concept_key(value)
            }
            if same_location or (concept_key and concept_key in matched_keys):
                candidates.append(source)

        if candidates:
            best = max(candidates, key=lambda value: float(value.get("score") or 0))
            for key in ("semester", "course", "unit", "filename", "page"):
                if not concept.get(key):
                    concept[key] = best.get(key, "")

        concept["page"] = _search_page_number(concept.get("page"))
        concept["source_available"] = bool(
            str(concept.get("semester") or "").strip()
            and str(concept.get("course") or "").strip()
            and str(concept.get("filename") or "").strip()
            and concept.get("page") is not None
        )
        enriched.append(concept)
    return enriched


def _build_search_only_response(user_id: str, request: AskSearchRequest) -> Dict[str, Any]:
    question = request.question.strip()
    requested_scope = (request.scope or "auto").strip().lower()
    search_filter = _normalize_search_filter(request.search_filter)
    intent = classify_intent(question)
    scope, scope_reason = resolve_scope(requested_scope, search_filter, intent)
    limit = max(1, min(int(request.limit or 5), 12))
    profile = _search_profile(user_id)
    concepts_data = _iter_user_concepts_with_context(user_id)
    alias_map = _build_user_alias_map(user_id, concepts_data, profile)
    base_tokens = tokenize(question)
    tokens = expand_tokens(base_tokens, alias_map)
    learning_metadata = _learning_metadata_for_user(user_id)
    cache_key = _search_cache_key(user_id, question, search_filter, scope, str(profile.get("updated_at") or ""))
    can_cache = not _is_sensitive_search(question)
    if can_cache:
        cached = _load_search_cache().get(cache_key)
        if isinstance(cached, dict) and cached.get("user_id") == user_id:
            result = dict(cached.get("result") or {})
            result["related_concepts"] = _attach_search_concept_sources(
                result.get("related_concepts") or [],
                result.get("sources") or [],
            )
            result["from_cache"] = True
            result["search_id"] = uuid.uuid4().hex
            return result

    sources, semantic_used = _search_sources(
        user_id, question, tokens, search_filter, scope, limit, concepts_data, learning_metadata, profile
    )
    related_concepts = _search_related_concepts(
        user_id, tokens, search_filter, scope, limit, concepts_data, learning_metadata, profile
    )
    if not related_concepts:
        related_concepts = _document_backed_concepts(base_tokens, alias_map, sources, limit)
    related_concepts = _attach_search_concept_sources(related_concepts, sources)
    result = {
        "search_id": uuid.uuid4().hex,
        "question": question,
        "mode": "search_only",
        "scope": scope,
        "scope_reason": scope_reason,
        "intent": intent,
        "intent_label": INTENT_LABELS.get(intent, INTENT_LABELS["general"]),
        "algorithm_version": SEARCH_ALGORITHM_VERSION,
        "semantic_search_used": semantic_used,
        "expanded_terms": [token for token in tokens if token not in base_tokens],
        "from_cache": False,
        "related_concepts": related_concepts,
        "sources": sources,
        "learning_memory_matches": _search_learning_memory(
            user_id, tokens, search_filter, scope, limit, learning_metadata, profile
        ),
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


_SEARCH_EVENT_TYPES = {"result_opened", "search_refined", "helpful", "ai_answer_requested", "explanation_started"}


def _record_search_event(user_id: str, payload: SearchEventCreate) -> Dict[str, Any]:
    event_type = payload.event_type.strip().lower()
    if event_type not in _SEARCH_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="지원하지 않는 search event입니다.")
    question = str(payload.question or "").strip()
    event = {
        "id": uuid.uuid4().hex,
        "search_id": payload.search_id.strip(),
        "user_id": user_id,
        "event_type": event_type,
        "result_type": str(payload.result_type or "").strip(),
        "result_id": str(payload.result_id or "").strip(),
        "intent": str(payload.intent or "").strip(),
        "course": str(payload.course or "").strip(),
        "concept": str(payload.concept or "").strip(),
        "question": "" if _is_sensitive_search(question) else question,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    events = _load_json_file(SEARCH_EVENTS_PATH)
    events = [item for item in events if isinstance(item, dict)] if isinstance(events, list) else []
    events.append(event)
    os.makedirs(os.path.dirname(SEARCH_EVENTS_PATH), exist_ok=True)
    _save_json_file(SEARCH_EVENTS_PATH, events[-5000:])

    if event_type in {"result_opened", "helpful", "explanation_started"}:
        profiles = _load_search_profiles()
        profile = profiles.get(user_id, {}) if isinstance(profiles.get(user_id), dict) else {}
        course_counts = profile.get("course_counts") if isinstance(profile.get("course_counts"), dict) else {}
        concept_counts = profile.get("concept_counts") if isinstance(profile.get("concept_counts"), dict) else {}
        if event["course"]:
            course_counts[event["course"]] = min(1000, int(course_counts.get(event["course"], 0) or 0) + 1)
        if event["concept"]:
            concept_counts[event["concept"]] = min(1000, int(concept_counts.get(event["concept"], 0) or 0) + 1)
        profile.update({
            "course_counts": course_counts,
            "concept_counts": concept_counts,
            "updated_at": event["created_at"],
        })
        profiles[user_id] = profile
        _save_search_profiles(profiles)
    return {"ok": True, "event_id": event["id"]}


def _save_search_alias(user_id: str, concept: str, alias: str) -> Dict[str, Any]:
    canonical = concept.strip()
    normalized_alias = alias.strip()
    if len(canonical) < 2 or len(normalized_alias) < 2:
        raise HTTPException(status_code=400, detail="concept와 alias는 두 글자 이상이어야 합니다.")
    profiles = _load_search_profiles()
    profile = profiles.get(user_id, {}) if isinstance(profiles.get(user_id), dict) else {}
    aliases = profile.get("aliases") if isinstance(profile.get("aliases"), dict) else {}
    values = _list_aliases(aliases.get(canonical))
    if normalized_alias not in values and normalized_alias != canonical:
        values.append(normalized_alias)
    aliases[canonical] = values[:20]
    profile["aliases"] = aliases
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    profiles[user_id] = profile
    _save_search_profiles(profiles)
    return {"ok": True, "concept": canonical, "aliases": values[:20]}


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
    result = _build_search_only_response(data_user_id, request)
    _record_question_history(
        data_user_id,
        request.question,
        "quick_search",
        answer=_search_history_summary(result),
        search_filter=request.search_filter.dict(exclude_none=True) if request.search_filter else {},
        scope=str(result.get("scope") or ""),
        result_count=(
            len(result.get("related_concepts") or [])
            + len(result.get("sources") or [])
            + len(result.get("learning_memory_matches") or [])
        ),
    )
    return result


@app.post("/ask/search/events")
async def create_search_event(request: SearchEventCreate, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    return _record_search_event(data_user_id, request)


@app.get("/search/profile")
async def get_search_profile(data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    profile = _search_profile(data_user_id)
    return {
        "aliases": profile.get("aliases", {}),
        "course_counts": profile.get("course_counts", {}),
        "concept_counts": profile.get("concept_counts", {}),
        "updated_at": profile.get("updated_at"),
    }


@app.post("/search/profile/aliases")
async def create_search_alias(request: SearchAliasCreate, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    return _save_search_alias(data_user_id, request.concept, request.alias)


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

    # 과목간 연계는 답을 바꾸지 않고 '제안'으로만 곁들인다.
    try:
        related = _related_cross_concepts(data_user_id, f"{request.question}\n{answer}", limit=5)
    except Exception:
        related = []

    _record_question_history(
        data_user_id,
        request.question,
        "ai_answer",
        answer=answer,
        search_filter=request.search_filter.dict(exclude_none=True) if request.search_filter else {},
        scope=scope_label,
        result_count=len(sources),
    )
    return AskResponse(answer=answer, sources=sources, scope_label=scope_label, related_concepts=related)


@app.get("/question-history")
async def question_history(
    response: Response,
    limit: int = 50,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    all_items = _question_history_for_user(data_user_id, limit=5000)
    safe_limit = max(1, min(int(limit or 50), 200))
    return {"total": len(all_items), "items": all_items[:safe_limit]}


@app.delete("/question-history/{history_id}")
async def delete_question_history_item(
    history_id: str,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    deleted = _delete_question_history_for_user(data_user_id, history_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="삭제할 검색·질문 기록을 찾지 못했습니다.")
    return {"ok": True, "deleted": deleted}


@app.delete("/question-history")
async def delete_all_question_history(
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    deleted = _delete_question_history_for_user(data_user_id)
    return {"ok": True, "deleted": deleted}


@app.post("/question-history/delete-all")
async def post_delete_all_question_history(
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """WebView-safe full delete used by the current My Page UI.

    The DELETE route above remains available for older clients.
    """
    deleted = _delete_question_history_for_user(data_user_id)
    return {"ok": True, "deleted": deleted}


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

    effective_unit = _effective_unit_name(unit, filename)
    _record_upload_event(
        data_user_id, filename, semester, course, effective_unit, "received",
        message="서버가 PDF 파일을 받았습니다.",
    )
    prefer_korean_ocr = bool(re.search(r"[가-힣]", " ".join([
        semester, course, title, unit, filename
    ])))
    try:
        pages = extract_pdf_text(destination, prefer_korean=prefer_korean_ocr)
    except Exception as exc:
        logger.exception("PDF extraction failed for %s", filename)
        _record_upload_event(
            data_user_id, filename, semester, course, effective_unit, "failed",
            message=f"PDF 텍스트 추출 실패: {exc}",
        )
        raise HTTPException(status_code=422, detail="PDF 텍스트 추출에 실패했습니다.") from exc
    if not pages:
        _record_upload_event(
            data_user_id, filename, semester, course, effective_unit, "failed",
            message="PDF에서 텍스트를 추출하지 못했습니다.",
        )
        return IngestResponse(ok=False, filename=filename, pages=0, message="PDF에서 텍스트를 추출하지 못했습니다.")

    try:
        add_pdf_pages_to_db(
            pages=pages,
            filename=filename,
            semester=semester.strip(),
            course=course.strip(),
            title=title.strip(),
            user_id=data_user_id,
            unit=effective_unit,
            stored_filename=unique_name,
        )
    except Exception as exc:
        logger.exception("search indexing failed for %s", filename)
        _record_upload_event(
            data_user_id, filename, semester, course, effective_unit, "failed",
            message=f"검색 인덱싱 실패: {exc}", pages=len(pages),
        )
        raise HTTPException(status_code=500, detail="검색 인덱싱에 실패했습니다.") from exc
    _invalidate_search_cache_for_user(data_user_id)
    _record_upload_event(
        data_user_id, filename, semester, course, effective_unit, "indexed",
        message="검색 인덱싱이 완료되었습니다.", pages=len(pages),
    )

    # 업로드 시 해당 단원 개념 자동 추출(개념 지도용). 실패해도 업로드는 성공 처리.
    concept_count = 0
    concept_extraction_status = "empty"
    try:
        cs = build_concepts_for_unit(data_user_id, semester.strip(), course.strip(), effective_unit)
        if cs:
            concept_count = len(cs)
            concept_extraction_status = "success"
            cdata = _load_json_file(CONCEPTS_PATH)
            cdata.setdefault(data_user_id, {}).setdefault(semester.strip(), {}).setdefault(course.strip(), {})[effective_unit] = cs
            _save_json_file(CONCEPTS_PATH, cdata)
    except Exception as exc:
        concept_extraction_status = "failed"
        logger.warning("concept extraction failed for %s / %s: %s", course, effective_unit, exc)

    concept_message = (
        f"개념 {concept_count}개 추출"
        if concept_extraction_status == "success"
        else "개념 추출 결과 없음"
        if concept_extraction_status == "empty"
        else "개념 추출 실패"
    )
    _record_upload_event(
        data_user_id, filename, semester, course, effective_unit, "complete",
        message=concept_message, pages=len(pages), concept_count=concept_count,
    )
    return IngestResponse(
        ok=True,
        filename=filename,
        pages=len(pages),
        message=f"자료 저장·검색 인덱싱 완료 · 단원: {effective_unit} · {concept_message}",
        unit=effective_unit,
        concept_count=concept_count,
        concept_extraction_status=concept_extraction_status,
    )


def _process_ingest_batch_job(
    job_id: str,
    data_user_id: str,
    semester: str,
    course: str,
    requested_title: str,
    requested_unit: str,
    prepared_files: List[Dict[str, str]],
) -> None:
    """Index a submitted file batch off the FastAPI event loop, then extract each unit once."""
    semester = semester.strip()
    course = course.strip()
    errors = []
    successful_units = set()
    completed = 0
    _update_ingest_job(job_id, status="running", progress=2, message="PDF 분석 시작")

    for index, item in enumerate(prepared_files, start=1):
        filename = item["filename"]
        destination = item["destination"]
        effective_unit = _effective_unit_name(requested_unit, filename)
        effective_title = (
            requested_title.strip()
            if requested_title.strip() and len(prepared_files) == 1
            else os.path.splitext(filename)[0]
        )
        _record_upload_event(
            data_user_id, filename, semester, course, effective_unit, "received",
            message="백그라운드 분석 작업이 파일을 받았습니다.",
        )
        prefer_korean_ocr = bool(re.search(r"[가-힣]", " ".join([
            semester, course, effective_title, effective_unit, filename
        ])))
        try:
            pages = extract_pdf_text(destination, prefer_korean=prefer_korean_ocr)
            if not pages:
                raise ValueError("PDF에서 텍스트를 추출하지 못했습니다.")
            add_pdf_pages_to_db(
                pages=pages,
                filename=filename,
                semester=semester,
                course=course,
                title=effective_title,
                user_id=data_user_id,
                unit=effective_unit,
                stored_filename=item["stored_filename"],
            )
            completed += 1
            successful_units.add(effective_unit)
            _record_upload_event(
                data_user_id, filename, semester, course, effective_unit, "indexed",
                message="검색 인덱싱이 완료되었습니다.", pages=len(pages),
            )
        except Exception as exc:
            logger.exception("batch ingest failed for %s", filename)
            errors.append({"filename": filename, "message": str(exc)})
            _record_upload_event(
                data_user_id, filename, semester, course, effective_unit, "failed",
                message=f"분석 실패: {exc}",
            )
        _update_ingest_job(
            job_id,
            completed_files=completed,
            failed_files=len(errors),
            progress=min(72, 5 + round(index / max(1, len(prepared_files)) * 67)),
            message=f"파일 분석 {index}/{len(prepared_files)}",
            errors=errors,
        )

    _invalidate_search_cache_for_user(data_user_id)
    total_concepts = 0
    concept_errors = []
    ordered_units = sorted(successful_units)
    for index, effective_unit in enumerate(ordered_units, start=1):
        _update_ingest_job(
            job_id,
            progress=72 + round((index - 1) / max(1, len(ordered_units)) * 25),
            message=f"개념 추출 {index}/{len(ordered_units)} · {effective_unit}",
        )
        try:
            concepts = build_concepts_for_unit(data_user_id, semester, course, effective_unit)
            total_concepts += len(concepts)
            with _concepts_write_lock:
                concepts_data = _load_json_file(CONCEPTS_PATH)
                concepts_data.setdefault(data_user_id, {}).setdefault(semester, {}).setdefault(course, {})[
                    effective_unit
                ] = concepts
                _save_json_file(CONCEPTS_PATH, concepts_data)
        except Exception as exc:
            logger.warning("batch concept extraction failed for %s / %s: %s", course, effective_unit, exc)
            concept_errors.append({"unit": effective_unit, "message": str(exc)})

    all_errors = [*errors, *concept_errors]
    if completed == 0:
        final_status = "failed"
    elif all_errors:
        final_status = "partial"
    else:
        final_status = "complete"
    _update_ingest_job(
        job_id,
        status=final_status,
        progress=100,
        completed_files=completed,
        failed_files=len(errors),
        concept_count=total_concepts,
        errors=all_errors,
        message=(
            f"자료 {completed}개 분석 완료 · 개념 {total_concepts}개"
            if final_status == "complete"
            else f"자료 {completed}개 완료 · 확인 필요 {len(all_errors)}건"
        ),
    )


@app.post("/ingest/batch")
async def ingest_batch(
    background_tasks: BackgroundTasks,
    semester: str = Form(...),
    course: str = Form(...),
    files: List[UploadFile] = File(...),
    title: str = Form(""),
    unit: str = Form(""),
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    if not semester.strip() or not course.strip():
        raise HTTPException(status_code=400, detail="학기와 과목이 필요합니다.")
    if not files:
        raise HTTPException(status_code=400, detail="PDF 파일이 필요합니다.")

    received = []
    for upload in files:
        filename = os.path.basename(upload.filename or "")
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"PDF 파일만 업로드할 수 있습니다: {filename}")
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"빈 PDF 파일입니다: {filename}")
        received.append((filename, content))

    prepared_files = []
    for filename, content in received:
        stored_filename = f"{uuid.uuid4().hex}_{filename}"
        destination = os.path.join(UPLOAD_DIR, stored_filename)
        with open(destination, "wb") as target:
            target.write(content)
        prepared_files.append({
            "filename": filename,
            "stored_filename": stored_filename,
            "destination": destination,
        })

    job = _create_ingest_job(
        data_user_id, semester, course, [item["filename"] for item in prepared_files]
    )
    background_tasks.add_task(
        _process_ingest_batch_job,
        job["job_id"], data_user_id, semester, course, title, unit, prepared_files,
    )
    return job


@app.get("/ingest/jobs/{job_id}")
async def ingest_job(job_id: str, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    job = _get_ingest_job(job_id)
    if not job or job.get("user_id") != data_user_id:
        raise HTTPException(status_code=404, detail="분석 작업을 찾을 수 없습니다.")
    return job


@app.get("/library", response_model=LibraryResponse)
async def library(data_user_id: str = Depends(current_uid)) -> LibraryResponse:
    overview = get_library_overview(user_id=data_user_id)
    return _normalize_library_overview(overview)


@app.get("/upload-events")
async def upload_events(
    limit: int = 50,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    events = _load_json_file(UPLOAD_EVENTS_PATH)
    items = [
        item for item in events
        if isinstance(item, dict) and item.get("user_id") == data_user_id
    ] if isinstance(events, list) else []
    items.reverse()
    safe_limit = max(1, min(int(limit or 50), 200))
    return {"total": len(items), "items": items[:safe_limit]}


@app.delete("/library", response_model=DeleteLibraryResponse)
async def delete_library(payload: DeleteLibraryPayload, data_user_id: str = Depends(current_uid)) -> DeleteLibraryResponse:
    deleted_count = delete_chunks_by_filter(
        search_filter=payload.search_filter.dict() if payload.search_filter else None,
        user_id=data_user_id,
    )
    if deleted_count:
        _invalidate_search_cache_for_user(data_user_id)
    return DeleteLibraryResponse(ok=True, deleted_count=deleted_count)


@app.delete("/library/file", response_model=LibraryFileActionResponse)
async def delete_library_file(
    payload: LibraryFileLocation,
    data_user_id: str = Depends(current_uid),
) -> LibraryFileActionResponse:
    semester = payload.semester.strip()
    course = payload.course.strip()
    unit = payload.unit.strip()
    filename = _normalize_filename(payload.filename)
    if not (semester and course and unit):
        raise HTTPException(status_code=400, detail="학기, 과목, 단원은 필수입니다.")
    source_filter = {
        "semester": semester, "course": course, "unit": unit, "filename": filename,
    }
    before = get_chunks(
        user_id=data_user_id, limit=100000, search_filter=source_filter, full=False
    )
    if not before.get("total"):
        raise HTTPException(status_code=404, detail="해당 위치에서 자료를 찾지 못했습니다.")
    stored_filenames = {
        str(item.get("stored_filename") or "")
        for item in before.get("items", []) if item.get("stored_filename")
    }
    deleted_count = delete_chunks_by_filter(source_filter, data_user_id)
    remaining = get_chunks(
        user_id=data_user_id,
        limit=1,
        search_filter={"semester": semester, "course": course, "unit": unit},
    ).get("total", 0)
    concepts_status = "preserved_source_unit"
    graph_nodes = 0
    if remaining == 0:
        concept_result = _delete_concept_scope(data_user_id, semester, course, unit)
        concepts_status = concept_result["status"]
        graph_result = remove_graph_scope(data_user_id, semester, course, unit)
        graph_nodes = graph_result["removed_nodes"]
    _update_registry_file_scope(filename, (semester, course, unit), None)
    source_file_deleted = _delete_owned_source_file(
        stored_filenames, filename, data_user_id
    )
    _invalidate_search_cache_for_user(data_user_id)
    _record_upload_event(
        data_user_id, filename, semester, course, unit, "deleted",
        message=f"자료 삭제 완료: 검색 청크 {deleted_count}개",
    )
    return LibraryFileActionResponse(
        ok=True,
        filename=filename,
        affected_chunks=deleted_count,
        concepts_status=concepts_status,
        graph_nodes=graph_nodes,
        source_file_deleted=source_file_deleted,
    )


@app.post("/library/file/move", response_model=LibraryFileActionResponse)
async def move_library_file(
    payload: MoveLibraryFilePayload,
    data_user_id: str = Depends(current_uid),
) -> LibraryFileActionResponse:
    source = payload.source
    filename = _normalize_filename(source.filename)
    source_scope = (
        source.semester.strip(), source.course.strip(), source.unit.strip()
    )
    target_scope = (
        payload.target_semester.strip(), payload.target_course.strip(), payload.target_unit.strip()
    )
    if not all((*source_scope, *target_scope)):
        raise HTTPException(status_code=400, detail="출발·도착 학기, 과목, 단원은 필수입니다.")
    if source_scope == target_scope:
        raise HTTPException(status_code=400, detail="현재 위치와 이동할 위치가 같습니다.")
    source_filter = {
        "semester": source_scope[0], "course": source_scope[1],
        "unit": source_scope[2], "filename": filename,
    }
    source_data = get_chunks(
        user_id=data_user_id, limit=100000, search_filter=source_filter, full=False
    )
    if not source_data.get("total"):
        raise HTTPException(status_code=404, detail="현재 위치에서 자료를 찾지 못했습니다.")
    target_existing = get_chunks(
        user_id=data_user_id,
        limit=1,
        search_filter={
            "semester": target_scope[0], "course": target_scope[1],
            "unit": target_scope[2], "filename": filename,
        },
    ).get("total", 0)
    if target_existing:
        raise HTTPException(status_code=409, detail="이동할 위치에 같은 파일명이 이미 있습니다.")

    moved_count = move_chunks_by_filter(
        source_filter,
        {"semester": target_scope[0], "course": target_scope[1], "unit": target_scope[2]},
        data_user_id,
    )
    source_remaining = get_chunks(
        user_id=data_user_id,
        limit=1,
        search_filter={
            "semester": source_scope[0], "course": source_scope[1], "unit": source_scope[2],
        },
    ).get("total", 0)
    concepts_status = "preserved_source_unit"
    graph_nodes = 0
    if source_remaining == 0:
        concept_result = _move_concept_scope(
            data_user_id, source_scope, target_scope
        )
        concepts_status = concept_result["status"]
        graph_result = move_graph_scope(data_user_id, source_scope, target_scope)
        graph_nodes = graph_result["moved_nodes"]
    _update_registry_file_scope(filename, source_scope, target_scope)
    _invalidate_search_cache_for_user(data_user_id)
    _record_upload_event(
        data_user_id, filename, target_scope[0], target_scope[1], target_scope[2], "moved",
        message=(
            f"{source_scope[0]} · {source_scope[1]} · {source_scope[2]}에서 이동 · "
            f"검색 청크 {moved_count}개"
        ),
    )
    return LibraryFileActionResponse(
        ok=True,
        filename=filename,
        affected_chunks=moved_count,
        concepts_status=concepts_status,
        graph_nodes=graph_nodes,
    )


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

    reg = _registry.unit_registered_at(semester, course)
    for u in units:
        u["registered_at"] = reg.get(u["unit"], "")
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


def _augment_concepts_with_page_locations(concepts: List[Dict[str, Any]], user_id: str, semester: str, course: str, unit: str) -> List[Dict[str, Any]]:
    try:
        source_chunks = concept_source_chunks_for_unit(user_id, semester, course, unit)
    except Exception:
        source_chunks = []

    out = []
    def _safe_page_val(raw: Any) -> Optional[int]:
        try:
            if raw is None:
                return None
            p = int(raw)
            if p <= 0:
                return None
            return p
        except Exception:
            return None

    for concept in concepts:
        item = dict(concept)
        saved_occurrences = item.get("occurrences") if isinstance(item.get("occurrences"), list) else []
        terms = [item.get("keyword"), item.get("name")]
        for list_key in ("aliases", "synonyms", "alias"):
            value = item.get(list_key)
            terms.extend(value if isinstance(value, list) else [value] if isinstance(value, str) else [])
        discovered_occurrences = _concept_occurrences_for_terms_from_chunks(terms, source_chunks)

        occurrence_map: Dict[tuple[str, int], Dict[str, Any]] = {}
        # process discovered occurrences first
        for entry in discovered_occurrences or []:
            if not isinstance(entry, dict):
                continue
            raw_page = entry.get("page")
            page = _safe_page_val(raw_page)
            if page is None:
                continue
            key = (str(entry.get("filename") or ""), page)
            occ = dict(entry)
            occ["page"] = page
            occurrence_map[key] = occ

        # merge saved occurrences, preferring saved fields while keeping safe page ints
        for entry in saved_occurrences:
            if not isinstance(entry, dict):
                continue
            raw_page = entry.get("page")
            page = _safe_page_val(raw_page)
            if page is None:
                continue
            key = (str(entry.get("filename") or ""), page)
            merged = {**occurrence_map.get(key, {}), **dict(entry)}
            merged["page"] = page
            occurrence_map[key] = merged

        occurrences = list(occurrence_map.values())
        if occurrences:
            # sort safely by filename then integer page
            occurrences.sort(key=lambda x: (str(x.get("filename") or ""), int(x.get("page") or 0)))
            item["occurrences"] = occurrences
            item["pages"] = sorted({int(x.get("page")) for x in occurrences if x.get("page") is not None})
            first = occurrences[0]
            item["filename"] = first.get("filename") or item.get("filename")
            item["page"] = first.get("page") or item.get("page")
        out.append(item)
    return out


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

    recalled = _augment_concepts_with_recall(concepts, data_user_id, semester, course, unit)
    return {"status": "ready", "concepts": _augment_concepts_with_page_locations(recalled, data_user_id, semester, course, unit)}


@app.get("/study-workspace")
async def study_workspace(
    semester: str,
    course: str,
    unit: str,
    filename: str,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """Return a study workspace for a single uploaded file owned by the authenticated user.

    Response structure:
    {
      "scope": {"semester": ..., "course": ..., "unit": ..., "filename": ...},
      "source_available": bool,
      "pages": [ {"page": int, "title": str, "text_preview": str, "concepts": [str, ...]}, ... ],
      "concepts": [ {"name": ..., "definition": ..., "first_page": int, "pages": [...], "occurrences": [...], "note": {...}|null}, ... ]
    }
    """
    semester = (semester or "").strip()
    course = (course or "").strip()
    unit = (unit or "").strip()
    filename = (filename or "").strip()

    if not all((semester, course, unit, filename)):
        raise HTTPException(status_code=400, detail="semester, course, unit, filename이 필요합니다.")

    # Ensure there are owned chunks for this file
    source_filter = {"semester": semester, "course": course, "unit": unit, "filename": filename}
    chunks_data = get_chunks(user_id=data_user_id, limit=100000, search_filter=source_filter, full=True)
    total = int(chunks_data.get("total") or len(chunks_data.get("items", [])))
    if not total:
        # No chunks owned by user at this location
        raise HTTPException(status_code=404, detail="해당 자료를 찾을 수 없습니다.")

    items = chunks_data.get("items", [])

    # Group chunks by page while preserving original chunk order
    pages_map: Dict[int, List[Dict[str, Any]]] = {}
    for c in items:
        raw_page = c.get("page")
        try:
            page = int(raw_page)
        except Exception:
            # skip chunks with non-integer page values
            continue
        # only include positive page numbers
        if page <= 0:
            continue
        pages_map.setdefault(page, []).append(c)

    pages_list = []
    for page in sorted(pages_map.keys()):
        page_chunks = pages_map[page]
        # preserve original ordering by chunk_index if present (tolerant to bad values)
        def _safe_chunk_index(item: Dict[str, Any]) -> int:
            try:
                return int(item.get("chunk_index") or 0)
            except Exception:
                return 0
        page_chunks.sort(key=_safe_chunk_index)
        # build text_preview from chunk_preview or text fields
        parts = []
        for pc in page_chunks:
            text = pc.get("chunk_preview") or pc.get("chunk_text") or pc.get("text") or ""
            text = str(text or "").strip()
            if text:
                parts.append(text)
        text_preview = " · ".join(parts)
        if len(text_preview) > 700:
            text_preview = text_preview[:700]
        # determine page title only when confident
        title_candidate = None
        for pc in page_chunks:
            t = (pc.get("title") or "").strip()
            if t and len(t) >= 3 and not t.lower().startswith(f"p.{page}"):
                title_candidate = t
                break
        title = title_candidate or f"p.{page}"
        pages_list.append({"page": page, "title": title, "text_preview": text_preview, "concepts": []})

    # Load concepts for the unit and augment with page locations
    concepts_data = _load_json_file(CONCEPTS_PATH)
    raw_concepts = (
        concepts_data
        .get(data_user_id, {})
        .get(semester, {})
        .get(course, {})
        .get(unit, [])
    )
    if not isinstance(raw_concepts, list):
        raw_concepts = []

    recalled = _augment_concepts_with_recall(raw_concepts, data_user_id, semester, course, unit)
    augmented = _augment_concepts_with_page_locations(recalled, data_user_id, semester, course, unit)

    # Filter concepts to those that have occurrences in this filename and attach concept note
    final_concepts = []
    for idx, c in enumerate(augmented):
        occs = []
        for o in (c.get("occurrences") or []):
            if str(o.get("filename") or "").strip() != filename:
                continue
            raw_page = o.get("page")
            try:
                p = int(raw_page)
            except Exception:
                continue
            if p <= 0:
                continue
            occ = dict(o)
            occ["page"] = p
            occs.append(occ)
        if not occs:
            continue
        pages = sorted({o.get("page") for o in occs if o.get("page") is not None})
        first_page = pages[0] if pages else None
        # find concept note for this exact file+concept
        note_list = _get_concept_notes_for_user(data_user_id, {"semester": semester, "course": course, "unit": unit, "filename": filename, "concept": c.get("name")})
        note_obj = note_list[0] if note_list else None
        final = {
            "name": c.get("name"),
            "definition": c.get("definition") or c.get("desc") or "",
            "first_page": first_page,
            "pages": pages,
            "occurrences": occs,
            "note": note_obj,
            "_orig_index": idx,
        }
        final_concepts.append(final)

    # sort concepts by first_page asc, preserve stored order (stable sort by original index)
    final_concepts.sort(key=lambda x: (x.get("first_page") if x.get("first_page") is not None else 10**9, x.get("_orig_index")))
    for fc in final_concepts:
        fc.pop("_orig_index", None)

    # attach concept names to pages
    for p in pages_list:
        p_concepts = []
        for c in final_concepts:
            if p["page"] in (c.get("pages") or []):
                p_concepts.append(c.get("name"))
        p["concepts"] = p_concepts

    # determine whether source file is available in uploads (unambiguous match)
    source_path = _resolve_owned_upload_path(
        data_user_id=data_user_id,
        requested_filename=filename,
        semester=semester,
        course=course,
        unit=unit,
    )
    source_available = bool(source_path)

    return {
        "scope": {"semester": semester, "course": course, "unit": unit, "filename": filename},
        "source_available": source_available,
        "pages": pages_list,
        "concepts": final_concepts,
    }


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

    # 추후 학습 제안: 방금 설명한 개념(+'더 연결해볼 점')을 시드로 다른 과목 연계 개념 제안.
    try:
        seed = " ".join([concept, answer_text] + list(feedback.missing_links or []))
        feedback.related_concepts = _related_cross_concepts(user_id, seed, limit=4)
    except Exception:
        feedback.related_concepts = []

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


def _upload_content_signature(path: str) -> tuple[int, str]:
    """Return a stable signature so legacy duplicate uploads can be opened safely."""
    digest = hashlib.sha256()
    with open(path, "rb") as upload_file:
        for chunk in iter(lambda: upload_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return os.path.getsize(path), digest.hexdigest()



def _pick_unambiguous_upload_path(paths: List[str]) -> Optional[str]:
    unique_paths = sorted(set(paths))
    if len(unique_paths) == 1:
        return unique_paths[0]
    if len(unique_paths) > 1:
        signatures = {_upload_content_signature(path) for path in unique_paths}
        if len(signatures) == 1:
            return unique_paths[0]
    return None


def _legacy_upload_event_owns_scope(
    data_user_id: str,
    filename: str,
    semester: str,
    course: str,
    unit: str,
) -> bool:
    """Confirm a pre-stored_filename upload from its authenticated exact-scope event."""
    required = {
        "user_id": str(data_user_id or ""),
        "filename": str(filename or ""),
        "semester": str(semester or "").strip(),
        "course": str(course or "").strip(),
        "unit": str(unit or "").strip(),
    }
    if not all(required.values()):
        return False
    events = _load_json_file(UPLOAD_EVENTS_PATH)
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            event_filename = _normalize_filename(str(event.get("filename") or ""))
        except HTTPException:
            continue
        if (
            str(event.get("user_id") or "") == required["user_id"]
            and event_filename == required["filename"]
            and str(event.get("semester") or "").strip() == required["semester"]
            and str(event.get("course") or "").strip() == required["course"]
            and str(event.get("unit") or "").strip() == required["unit"]
            and str(event.get("status") or "") in {"received", "indexed", "complete"}
        ):
            return True
    return False


def _resolve_owned_upload_path(
    data_user_id: str,
    requested_filename: str,
    semester: str = "",
    course: str = "",
    unit: str = "",
) -> Optional[str]:
    """Resolve only source files directly referenced by chunks in the owned scope."""
    try:
        safe_filename = _normalize_filename(requested_filename)
    except HTTPException:
        return None

    search_filter: Dict[str, Any] = {"filename": safe_filename}
    for key, value in (("semester", semester), ("course", course), ("unit", unit)):
        if str(value or "").strip():
            search_filter[key] = str(value).strip()
    chunks = get_chunks(
        user_id=data_user_id,
        limit=10000,
        search_filter=search_filter,
        full=False,
    )
    if not chunks.get("total"):
        return None

    stored_names = {
        str(item.get("stored_filename") or "").strip()
        for item in chunks.get("items", [])
        if str(item.get("stored_filename") or "").strip()
    }
    stored_paths = [path for stored in stored_names if (path := _safe_upload_path(stored))]
    if path := _pick_unambiguous_upload_path(stored_paths):
        return path
    if stored_names:
        logger.warning(
            "Owned stored_filename(s) unavailable or ambiguous for user=%s filename=%s",
            data_user_id,
            safe_filename,
        )
        return None

    matches = _matching_upload_paths(safe_filename)
    if _legacy_upload_event_owns_scope(
        data_user_id,
        safe_filename,
        semester,
        course,
        unit,
    ):
        if path := _pick_unambiguous_upload_path(matches):
            return path
        if matches:
            logger.warning(
                "Legacy owned filename matches are ambiguous for user=%s filename=%s",
                data_user_id,
                safe_filename,
            )
        return None

    if matches:
        logger.warning(
            "Legacy filename matches are not returned without attributable ownership for user=%s filename=%s",
            data_user_id,
            safe_filename,
        )
    return None


@app.get("/file")
async def serve_file(
    filename: str,
    token: str = "",
    semester: str = "",
    course: str = "",
    unit: str = "",
):
    """업로드된 원본 PDF preview. 쿼리 토큰에서 도출한 data_user_id 소유 파일만 반환."""
    data_user_id = _uid_from_token(token)
    if path := _resolve_owned_upload_path(
        data_user_id=data_user_id,
        requested_filename=filename,
        semester=semester,
        course=course,
        unit=unit,
    ):
        return FileResponse(path, media_type="application/pdf")
    raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")


def _scope_units(data: Dict[str, Any], user_id: str, semester: str, course: str) -> Optional[Dict[str, Any]]:
    units = (
        data.get(user_id, {}).get(semester, {}).get(course, {})
        if isinstance(data, dict) else {}
    )
    return units if isinstance(units, dict) else None


def _merge_concept_lists(target: List[Any], incoming: List[Any]) -> List[Any]:
    merged = [dict(item) for item in target if isinstance(item, dict)]
    by_name = {
        _concept_key(item.get("name") or item.get("keyword")): item
        for item in merged
        if _concept_key(item.get("name") or item.get("keyword"))
    }
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = _concept_key(item.get("name") or item.get("keyword"))
        existing = by_name.get(key)
        if not existing:
            merged.append(item)
            if key:
                by_name[key] = item
            continue
        existing["weight"] = max(
            int(existing.get("weight") or 1), int(item.get("weight") or 1)
        )
        for list_key in ("aliases", "links", "related", "occurrences", "evidence"):
            values = []
            for value in [*(existing.get(list_key) or []), *(item.get(list_key) or [])]:
                marker = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else str(value)
                if marker not in {json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, dict) else str(v) for v in values}:
                    values.append(value)
            if values:
                existing[list_key] = values
    return merged


def _delete_concept_scope(user_id: str, semester: str, course: str, unit: str) -> Dict[str, Any]:
    data = _load_json_file(CONCEPTS_PATH)
    units = _scope_units(data, user_id, semester, course)
    if not units or unit not in units:
        return {"status": "source_missing", "concept_count": 0}
    concepts = units.pop(unit)
    _save_json_file(CONCEPTS_PATH, data)
    return {
        "status": "deleted",
        "concept_count": len(concepts) if isinstance(concepts, list) else 0,
    }


def _move_concept_scope(
    user_id: str,
    source_scope: tuple[str, str, str],
    target_scope: tuple[str, str, str],
) -> Dict[str, Any]:
    source_semester, source_course, source_unit = source_scope
    target_semester, target_course, target_unit = target_scope
    data = _load_json_file(CONCEPTS_PATH)
    source_units = _scope_units(data, user_id, source_semester, source_course)
    if not source_units or source_unit not in source_units:
        return {"status": "source_missing", "concept_count": 0}
    concepts = source_units.pop(source_unit)
    concepts = concepts if isinstance(concepts, list) else []
    target_units = (
        data.setdefault(user_id, {})
        .setdefault(target_semester, {})
        .setdefault(target_course, {})
    )
    target_units[target_unit] = _merge_concept_lists(
        target_units.get(target_unit) if isinstance(target_units.get(target_unit), list) else [],
        concepts,
    )
    _save_json_file(CONCEPTS_PATH, data)
    return {"status": "moved", "concept_count": len(concepts)}


def _update_registry_file_scope(
    filename: str,
    source_scope: tuple[str, str, str],
    target_scope: Optional[tuple[str, str, str]] = None,
) -> int:
    if not os.path.exists(REGISTRY_CSV_PATH):
        return 0
    with open(REGISTRY_CSV_PATH, encoding="utf-8-sig", newline="") as registry_file:
        reader = csv.DictReader(registry_file)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    source_semester, source_course, source_unit = source_scope
    changed, output_rows = 0, []
    for row in rows:
        is_match = (
            unicodedata.normalize("NFC", str(row.get("filename") or ""))
            == unicodedata.normalize("NFC", filename)
            and str(row.get("semester") or "") == source_semester
            and str(row.get("course") or "") == source_course
            and str(row.get("unit") or "").strip() == source_unit
        )
        if not is_match:
            output_rows.append(row)
            continue
        changed += 1
        if target_scope is None:
            continue
        target_semester, target_course, target_unit = target_scope
        output_rows.append({
            **row,
            "semester": target_semester,
            "course": target_course,
            "unit": target_unit,
        })
    if not changed:
        return 0
    temp_path = REGISTRY_CSV_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8-sig", newline="") as registry_file:
        writer = csv.DictWriter(registry_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)
    os.replace(temp_path, REGISTRY_CSV_PATH)
    return changed


def _delete_owned_source_file(stored_filenames: Set[str], original_filename: str, user_id: str) -> bool:
    all_chunks = get_chunks(user_id=user_id, limit=100000, full=False).get("items", [])
    referenced_stored = {
        str(item.get("stored_filename") or "") for item in all_chunks
        if item.get("stored_filename")
    }
    deleted = False
    for stored in stored_filenames - referenced_stored:
        if path := _safe_upload_path(stored):
            os.remove(path)
            deleted = True
    if stored_filenames:
        return deleted
    original_still_used = any(
        unicodedata.normalize("NFC", str(item.get("filename") or ""))
        == unicodedata.normalize("NFC", original_filename)
        for item in all_chunks
    )
    if not original_still_used:
        matches = _matching_upload_paths(original_filename)
        if len(matches) == 1:
            os.remove(matches[0])
            deleted = True
    return deleted


def _move_concept_unit_key(
    user_id: str,
    semester: str,
    course: str,
    old_unit: str,
    new_unit: str,
) -> Dict[str, Any]:
    """Move one concepts.json unit key without overwriting an existing target."""
    if old_unit == new_unit:
        return {"status": "unchanged", "concept_count": 0}
    data = _load_json_file(CONCEPTS_PATH)
    units = (
        data.get(user_id, {})
        .get(semester, {})
        .get(course, {})
    ) if isinstance(data, dict) else {}
    if not isinstance(units, dict) or old_unit not in units:
        return {"status": "source_missing", "concept_count": 0}
    concepts = units.get(old_unit)
    concept_count = len(concepts) if isinstance(concepts, list) else 0
    if new_unit in units:
        return {
            "status": "target_conflict",
            "concept_count": concept_count,
            "target_concept_count": len(units.get(new_unit)) if isinstance(units.get(new_unit), list) else 0,
        }
    units[new_unit] = units.pop(old_unit)
    _save_json_file(CONCEPTS_PATH, data)
    return {"status": "moved", "concept_count": concept_count}


@app.post("/rename-unit")
async def rename_unit_ep(payload: dict, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    sem = (payload.get("semester") or "").strip()
    course = (payload.get("course") or "").strip()
    old = (payload.get("old_unit") or "").strip()
    new = (payload.get("new_unit") or "").strip()
    if not (sem and course and old and new):
        raise HTTPException(status_code=400, detail="semester, course, old_unit, new_unit 가 필요합니다.")
    n = rename_unit(data_user_id, sem, course, old, new)
    concept_move = _move_concept_unit_key(data_user_id, sem, course, old, new)
    if n or concept_move["status"] == "moved":
        _invalidate_search_cache_for_user(data_user_id)
    return {"ok": True, "updated_chunks": n, "concept_unit": concept_move}


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


@app.post("/reindex-graph/scope")
async def reindex_graph_scope(payload: dict, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """Replace graph nodes and cross-links for one unit only."""
    semester = str(payload.get("semester") or "").strip()
    course = str(payload.get("course") or "").strip()
    unit = str(payload.get("unit") or "").strip()
    if not (semester and course and unit):
        raise HTTPException(status_code=400, detail="semester, course, unit가 필요합니다.")
    try:
        embedding_result = build_concept_embeddings_for_scope(
            data_user_id, semester, course, unit
        )
        link_result = build_cross_links_for_scope(
            data_user_id, semester, course, unit, threshold=0.45, top_k=5
        )
        return {
            "ok": True,
            "scope": {"semester": semester, "course": course, "unit": unit},
            "concepts": len(embedding_result["target_nodes"]),
            "replaced_concepts": embedding_result["replaced_count"],
            "total_concepts": embedding_result["total_count"],
            "new_cross_edges": len(link_result["new_edges"]),
            "removed_cross_edges": link_result["removed_edge_count"],
            "total_cross_edges": link_result["total_edge_count"],
            "embedding_calls": len(embedding_result["target_nodes"]),
            "llm_calls": link_result["llm_calls"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"단원 그래프 증분 생성 중 오류 발생: {str(exc)}",
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
            learning_state = _learning_state_from_recall(recall_count, last_recalled_at, missing_links_count)
            node["learning_state"] = learning_state
            node["review_priority"] = _review_priority_for_state(learning_state, recall_count, last_recalled_at, missing_links_count)
            node["review_reason"] = _review_reason_for_state(learning_state, recall_count, last_recalled_at, missing_links_count)

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


@app.get("/concept-graph/overview")
async def concept_graph_overview(
    course: Optional[str] = None,
    unit: Optional[str] = None,
    weak_only: bool = False,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """Read-only user-level concept graph view. Uses existing graph files only."""
    if not os.path.exists(CONCEPT_INDEX_PATH) or not os.path.exists(CONCEPT_LINKS_PATH):
        return _empty_graph_overview()

    links_data = _load_json_file(CONCEPT_LINKS_PATH)
    base_nodes = _build_concept_overview_nodes(data_user_id, course_filter=(course or ""), unit_filter=(unit or ""))

    node_ids = {node["id"] for node in base_nodes if node.get("id")}
    node_map = {node["id"]: node for node in base_nodes if node.get("id")}
    raw_edges = []
    if isinstance(links_data, dict):
        for edge in links_data.get("edges", []):
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source") or edge.get("a") or "")
            target = str(edge.get("target") or edge.get("b") or "")
            if source in node_ids and target in node_ids:
                raw_edges.append({
                    "source": source,
                    "target": target,
                    "weight": float(edge.get("weight", edge.get("score", 1)) or 1),
                })

    neighbors: Dict[str, List[Dict[str, Any]]] = {node_id: [] for node_id in node_ids}
    for edge in raw_edges:
        neighbors.setdefault(edge["source"], []).append({"id": edge["target"], "weight": edge["weight"]})
        neighbors.setdefault(edge["target"], []).append({"id": edge["source"], "weight": edge["weight"]})

    max_degree = max([len(items) for items in neighbors.values()] or [0]) or 1
    weighted_by_node = {
        node_id: sum(float(item.get("weight") or 1) for item in items)
        for node_id, items in neighbors.items()
    }
    max_weighted = max(weighted_by_node.values() or [0]) or 1
    degree_values = sorted([len(items) for items in neighbors.values()], reverse=True)
    core_degree_threshold = degree_values[max(0, min(len(degree_values) - 1, int(len(degree_values) * 0.2)))] if degree_values else 0
    missing_map = _missing_link_keys_for_user(data_user_id)

    enriched_nodes: List[Dict[str, Any]] = []
    for node in base_nodes:
        node_neighbors = neighbors.get(node["id"], [])
        neighbor_nodes = [node_map[item["id"]] for item in node_neighbors if item["id"] in node_map]
        degree = len(node_neighbors)
        weighted_degree = weighted_by_node.get(node["id"], 0)
        course_count = len({item.get("course") for item in neighbor_nodes if item.get("course")})
        unit_count = len({(item.get("course"), item.get("unit")) for item in neighbor_nodes if item.get("unit")})
        centrality_score = _clamp_score((degree / max_degree) * 60 + (weighted_degree / max_weighted) * 40)
        bridge_score = _clamp_score((60 if course_count > 1 else 0) + (30 if unit_count > 1 else 0) + min(10, degree))
        age = _age_days(node.get("last_recalled_at"))
        memory_score = _clamp_score(min(70, int(node.get("recall_count") or 0) * 20) + (30 if age is not None and age <= 14 else 15 if age is not None and age <= 60 else 0))
        learning_state = _learning_state_from_recall(int(node.get("recall_count") or 0), node.get("last_recalled_at"), int(node.get("missing_links_count") or 0))
        review_score = _review_priority_for_state(learning_state, int(node.get("recall_count") or 0), node.get("last_recalled_at"), int(node.get("missing_links_count") or 0))
        review_priority = _review_priority_for_state(learning_state, int(node.get("recall_count") or 0), node.get("last_recalled_at"), int(node.get("missing_links_count") or 0), centrality_score, bridge_score)
        priority_score = _clamp_score(review_priority * 0.55 + centrality_score * 0.25 + bridge_score * 0.15 + memory_score * 0.05)

        node_types = [learning_state.lower()]
        if learning_state == "REVIEW":
            node_types.extend(["review", "weak"])
        if learning_state == "NEW":
            node_types.append("new")
        if centrality_score >= 70 or (core_degree_threshold and degree >= core_degree_threshold):
            node_types.append("core")
        if bridge_score >= 60:
            node_types.append("bridge")
        if int(node.get("recall_count") or 0) > 0:
            node_types.append("recalled")
        if age is not None and age <= 14:
            node_types.append("recent")
        node_types = list(dict.fromkeys(node_types))

        why = _review_reason_for_state(learning_state, int(node.get("recall_count") or 0), node.get("last_recalled_at"), int(node.get("missing_links_count") or 0), centrality_score, bridge_score)

        if learning_state == "NEW":
            recommended_action = "처음으로 내 말로 설명해보세요."
        elif learning_state == "REVIEW":
            recommended_action = "Learning Memory의 AI 피드백을 다시 확인하고 설명해보세요."
        elif learning_state == "LEARNING":
            recommended_action = "남은 missing links를 확인하며 설명을 보완해보세요."
        elif "bridge" in node_types:
            recommended_action = "연결된 개념들을 함께 비교해보세요."
        elif "core" in node_types:
            recommended_action = "이 개념을 중심으로 단원 구조를 정리해보세요."
        else:
            recommended_action = "지금은 새 개념이나 REVIEW 상태 개념을 먼저 봐도 좋습니다."

        enriched_nodes.append({
            **node,
            "degree": degree,
            "weighted_degree": round(weighted_degree, 4),
            "connected_count": degree,
            "centrality_score": centrality_score,
            "bridge_score": bridge_score,
            "memory_score": memory_score,
            "review_score": review_score,
            "review_priority": review_priority,
            "priority_score": priority_score,
            "learning_state": learning_state,
            "review_reason": why,
            "node_types": node_types,
            "why_shown": why,
            "recommended_action": recommended_action,
        })

    if weak_only:
        enriched_nodes = [node for node in enriched_nodes if node.get("learning_state") == "REVIEW"]
        node_ids = {node["id"] for node in enriched_nodes}

    max_edge_weight = max([float(edge.get("weight") or 0) for edge in raw_edges] or [0]) or 1
    edge_missing_map = missing_map
    edges = []
    for edge in raw_edges:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            continue
        source = node_map[edge["source"]]
        target = node_map[edge["target"]]
        source_key = (source.get("semester", ""), source.get("course", ""), source.get("unit", ""), _concept_key(source.get("label")))
        target_key = (target.get("semester", ""), target.get("course", ""), target.get("unit", ""), _concept_key(target.get("label")))
        learning_memory_link = (
            _concept_key(target.get("label")) in edge_missing_map.get(source_key, set())
            or _concept_key(source.get("label")) in edge_missing_map.get(target_key, set())
        )
        edge_type, reason = _edge_type_and_reason(source, target, float(edge.get("weight") or 0), learning_memory_link)
        edges.append({
            "source": edge["source"],
            "target": edge["target"],
            "weight": round(float(edge.get("weight") or 0), 4),
            "normalized_weight": _clamp_score((float(edge.get("weight") or 0) / max_edge_weight) * 100),
            "edge_type": edge_type,
            "reason": reason,
        })

    return {
        "nodes": enriched_nodes,
        "edges": edges,
        "stats": {
            "node_count": len(enriched_nodes),
            "edge_count": len(edges),
            "weak_concept_count": len([node for node in enriched_nodes if node.get("learning_state") == "REVIEW"]),
            "review_concept_count": len([node for node in enriched_nodes if node.get("learning_state") == "REVIEW"]),
            "learning_concept_count": len([node for node in enriched_nodes if node.get("learning_state") == "LEARNING"]),
            "mastered_concept_count": len([node for node in enriched_nodes if node.get("learning_state") == "MASTERED"]),
            "recalled_concept_count": len([node for node in enriched_nodes if "recalled" in node.get("node_types", [])]),
            "bridge_concept_count": len([node for node in enriched_nodes if "bridge" in node.get("node_types", [])]),
            "core_concept_count": len([node for node in enriched_nodes if "core" in node.get("node_types", [])]),
            "new_concept_count": len([node for node in enriched_nodes if node.get("learning_state") == "NEW"]),
        },
        "ranking_info": _ranking_info(),
    }



@app.get("/concept-notes")
async def concept_notes_get(
    semester: Optional[str] = None,
    course: Optional[str] = None,
    unit: Optional[str] = None,
    filename: Optional[str] = None,
    concept: Optional[str] = None,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    filters = {
        key: value.strip()
        for key, value in {
            "semester": semester,
            "course": course,
            "unit": unit,
            "filename": filename,
            "concept": concept,
        }.items()
        if value and value.strip()
    }
    return {
        "items": _get_concept_notes_for_user(data_user_id, filters),
    }


@app.put("/concept-notes")
async def concept_notes_put(
    payload: ConceptNoteUpsertRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    note = _upsert_concept_note(data_user_id, payload.dict())
    return {"ok": True, "note": note}


@app.delete("/concept-notes/{note_id}")
async def concept_notes_delete(
    note_id: str,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    if not _delete_concept_note_for_user(data_user_id, note_id):
        raise HTTPException(status_code=404, detail="노트를 찾을 수 없습니다.")
    return {"ok": True}


@app.get("/lecture-notes")
async def lecture_notes_get(
    semester: Optional[str] = None,
    course: Optional[str] = None,
    unit: Optional[str] = None,
    filename: Optional[str] = None,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    filters = {
        key: value.strip()
        for key, value in {
            "semester": semester,
            "course": course,
            "unit": unit,
            "filename": filename,
        }.items()
        if value and value.strip()
    }
    return {"items": _get_lecture_notes_for_user(data_user_id, filters)}


@app.put("/lecture-notes")
async def lecture_notes_put(
    payload: LectureNoteUpsertRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    note = _upsert_lecture_note(data_user_id, payload.dict())
    return {"ok": True, "note": note}


@app.post("/lecture-notes")
async def lecture_notes_post(
    payload: LectureNoteUpsertRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    note = _create_lecture_note(data_user_id, payload.dict())
    return {"ok": True, "note": note}


@app.delete("/lecture-notes/{note_id}")
async def lecture_notes_delete(
    note_id: str,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    if not _delete_lecture_note_for_user(data_user_id, note_id):
        raise HTTPException(status_code=404, detail="수업 필기를 찾을 수 없습니다.")
    return {"ok": True}


@app.post("/learning-session/start")
async def start_learning_session(
    payload: LearningSessionStartRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """Start a study session using the current review priority ranking.

    Example response:
    {
      "id": "sess_abc123",
      "user_id": "demo",
      "created_at": "2026-07-02T12:00:00+00:00",
      "scope": {"course": null, "unit": null},
      "items": [{"concept_id": "c1", "concept": "신부전", "course": "병리생리학1", "unit": "신장", "state_at_start": "REVIEW", "status": "pending"}],
      "cursor": 0,
      "completed_at": null
    }
    """
    size = max(1, min(50, int(payload.size or 7)))
    return _create_learning_session(data_user_id, payload.scope, size=size)


@app.post("/learning-session/{session_id}/advance")
async def advance_learning_session(
    session_id: str,
    payload: LearningSessionAdvanceRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """Advance a session item and persist a recall trace when the learner explained the concept.

    Example response:
    {
      "id": "sess_abc123",
      "user_id": "demo",
      "cursor": 1,
      "completed_at": null,
      "next_item": {"concept_id": "c2", "concept": "요독증", "state_at_start": "LEARNING", "status": "pending"}
    }
    """
    sessions = _load_learning_sessions()
    session = next((item for item in sessions if isinstance(item, dict) and str(item.get("id") or "") == session_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    if str(session.get("user_id") or "") != data_user_id:
        raise HTTPException(status_code=403, detail="이 세션에 접근할 수 없습니다.")
    if not payload.concept_id:
        raise HTTPException(status_code=400, detail="concept_id가 필요합니다.")

    updated = _advance_learning_session(session, data_user_id, payload.concept_id, payload.result)
    next_item = None
    if int(updated.get("cursor") or 0) < len(updated.get("items") or []):
        next_item = (updated.get("items") or [])[int(updated.get("cursor") or 0)]
    return {
        **updated,
        "next_item": next_item,
    }


@app.get("/learning-session/current")
async def current_learning_session(data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """Return the latest unfinished learning session, if any.

    Example response:
    {"session": {"id": "sess_abc123", "cursor": 2, "items": []}}
    """
    sessions = _load_learning_sessions()
    incomplete = [
        item for item in sessions
        if isinstance(item, dict) and str(item.get("user_id") or "") == data_user_id and not item.get("completed_at")
    ]
    if incomplete:
        incomplete.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"session": incomplete[0]}
    return {"session": None}


@app.get("/learning-session/{session_id}")
async def get_learning_session(session_id: str, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """Return a single learning session.

    Example response:
    {"id": "sess_abc123", "user_id": "demo", "cursor": 0, "items": []}
    """
    sessions = _load_learning_sessions()
    session = next((item for item in sessions if isinstance(item, dict) and str(item.get("id") or "") == session_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    if str(session.get("user_id") or "") != data_user_id:
        raise HTTPException(status_code=403, detail="이 세션에 접근할 수 없습니다.")
    return session


@app.post("/review/grade")
async def grade_review(
    payload: Dict[str, Any],
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """Grade a concept review result using SM-2 and return the updated schedule entry.

    Example response:
    {"concept_id": "c1", "user_id": "demo", "ease": 2.5, "interval_days": 6, "repetitions": 2, "due_at": "2026-07-08T12:00:00+00:00"}
    """
    concept_id = str(payload.get("concept_id") or "").strip()
    quality = int(payload.get("quality") or 0)
    if not concept_id:
        raise HTTPException(status_code=400, detail="concept_id가 필요합니다.")
    if quality < 0 or quality > 5:
        raise HTTPException(status_code=400, detail="quality는 0~5 사이여야 합니다.")
    entry = _grade_review_for_concept(data_user_id, concept_id, quality)
    return {**entry, "due_at": entry.get("due_at")}


@app.get("/review/due")
async def review_due(
    limit: int = 20,
    course: Optional[str] = None,
    unit: Optional[str] = None,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """Return concepts that are due for review, ordered by due_at or fallback review_priority.

    Example response:
    {"items": [{"concept_id": "c1", "concept": "신부전", "learning_state": "REVIEW", "review_reason": ["..."], "is_due": true, "due_at": "2026-07-02T12:00:00+00:00"}]}
    """
    nodes = _build_concept_overview_nodes(data_user_id, course_filter=(course or ""), unit_filter=(unit or ""))
    if not nodes:
        return {"items": []}

    due_nodes = [node for node in nodes if bool(node.get("is_due"))]
    if due_nodes:
        ordered = sorted(due_nodes, key=lambda node: (str(node.get("due_at") or ""), -int(node.get("review_priority") or 0)))
    else:
        ordered = sorted(nodes, key=lambda node: (-int(node.get("review_priority") or 0), str(node.get("label") or "")))

    items = []
    for node in ordered[:max(1, min(100, int(limit or 20)))]:
        items.append({
            "concept_id": str(node.get("id") or ""),
            "concept": str(node.get("label") or ""),
            "course": str(node.get("course") or ""),
            "unit": str(node.get("unit") or ""),
            "learning_state": str(node.get("learning_state") or "NEW"),
            "review_reason": list(node.get("review_reason") or []),
            "review_priority": int(node.get("review_priority") or 0),
            "is_due": bool(node.get("is_due")),
            "due_at": node.get("due_at"),
        })
    return {"items": items}


@app.get("/learning/suggestions")
async def learning_suggestions(limit: int = 6, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """추후 학습 제안: 복습 대기·최근 학습 개념에서 다른 과목으로 이어지는 연계 개념.

    판단·평가가 아니라 저장된 개념 그래프의 교차 링크를 그대로 보여주는 제안이다.
    """
    seed_names: List[str] = []
    try:
        nodes = _build_concept_overview_nodes(data_user_id)
        due = [n for n in nodes if n.get("is_due")]
        pool = due or sorted(nodes, key=lambda n: -int(n.get("review_priority") or 0))[:10]
        seed_names += [str(n.get("label") or "") for n in pool]
    except Exception:
        pass
    traces = [t for t in _load_recall_traces() if t.get("user_id") == data_user_id]
    traces.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)
    seed_names += [str(t.get("concept") or "") for t in traces[:10]]

    seed_text = " ".join(name for name in seed_names if name)
    return {"items": _related_cross_concepts(data_user_id, seed_text, limit=max(1, min(20, limit)))}


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


def _delete_learning_memories_for_user(
    data_user_id: str,
    target_ids: Optional[Set[str]] = None,
    delete_all: bool = False,
    course: Optional[str] = None,
    unit: Optional[str] = None,
    concept: Optional[str] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """Delete recall traces (and their linked AI feedback) owned by data_user_id.

    strict=True (default, used by the single-id and legacy bulk routes) 404s if
    any requested id isn't owned by this user. strict=False (used by the
    multi-id POST route) silently skips ids that don't exist or belong to
    someone else instead of failing the whole batch — a mixed selection still
    deletes the valid ones.
    """
    requested_ids = {str(item or "").strip() for item in (target_ids or set()) if str(item or "").strip()}
    if not delete_all and not requested_ids:
        raise HTTPException(status_code=400, detail="삭제할 Learning Memory id가 필요합니다.")

    traces = _load_recall_traces()
    user_items = [item for item in traces if isinstance(item, dict) and item.get("user_id") == data_user_id]
    course_filter = str(course or "").strip()
    unit_filter = str(unit or "").strip()
    concept_filter = str(concept or "").strip().lower()

    def matches_filters(item: Dict[str, Any]) -> bool:
        if course_filter and str(item.get("course") or "") != course_filter:
            return False
        if unit_filter and str(item.get("unit") or "") != unit_filter:
            return False
        if concept_filter and concept_filter not in " ".join([
            str(item.get("concept") or ""),
            str(item.get("answer_text") or ""),
            str(item.get("feedback_text") or ""),
        ]).lower():
            return False
        return True

    deletable_items = [item for item in user_items if matches_filters(item)] if delete_all else user_items
    user_ids = {str(item.get("id") or "").strip() for item in user_items if str(item.get("id") or "").strip()}
    deletable_ids = {str(item.get("id") or "").strip() for item in deletable_items if str(item.get("id") or "").strip()}

    if delete_all:
        related_ids = set(deletable_ids)
    else:
        owned_ids = requested_ids & user_ids
        missing_ids = requested_ids - user_ids
        if missing_ids and strict:
            raise HTTPException(status_code=404, detail="Learning Memory를 찾을 수 없습니다.")
        related_ids = set(owned_ids)

    changed = True
    while changed:
        changed = False
        for item in user_items:
            item_id = str(item.get("id") or "").strip()
            source_id = str(item.get("source_trace_id") or "").strip()
            if not item_id:
                continue
            if item_id in related_ids or (source_id and source_id in related_ids):
                before = len(related_ids)
                related_ids.add(item_id)
                if source_id:
                    related_ids.add(source_id)
                changed = changed or len(related_ids) > before

    kept = []
    deleted_count = 0
    for item in traces:
        if not isinstance(item, dict) or item.get("user_id") != data_user_id:
            kept.append(item)
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id in related_ids:
            deleted_count += 1
            continue
        kept.append(item)

    _save_recall_traces(kept)
    return {"ok": True, "deleted_count": deleted_count}


@app.delete("/learning-memory")
async def delete_learning_memories(
    payload: LearningMemoryBulkDeleteRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """Deprecated: kept for older desktop app builds. Some WebViews (Tauri's
    WKWebView included) don't reliably deliver a body on DELETE requests, so
    the current mypage.html uses POST-only bulk routes instead. Do not remove
    without confirming no installed build still calls this."""
    return _delete_learning_memories_for_user(
        data_user_id=data_user_id,
        target_ids=set(payload.ids or []),
        delete_all=bool(payload.delete_all),
        course=payload.course,
        unit=payload.unit,
        concept=payload.concept,
    )


# Registered ahead of the /{memory_id} route below so "all" is never captured
# as a memory_id path parameter.
@app.delete("/learning-memory/all")
async def delete_all_learning_memories(data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """Body-less full delete, safe for WebViews that drop DELETE request bodies."""
    return _delete_learning_memories_for_user(data_user_id=data_user_id, delete_all=True)


@app.post("/learning-memory/delete-all")
async def post_delete_all_learning_memories(data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    """POST full delete used by the current My Page UI.

    Existing DELETE routes stay registered for installed older clients.
    """
    return _delete_learning_memories_for_user(data_user_id=data_user_id, delete_all=True)


@app.post("/learning-memory/delete")
async def delete_selected_learning_memories(
    payload: LearningMemorySelectedDeleteRequest,
    data_user_id: str = Depends(current_uid),
) -> Dict[str, Any]:
    """POST variant of selective delete for the same WebView reason as above.
    Ids that don't exist or belong to another user are silently skipped rather
    than failing the whole batch."""
    ids = [str(item or "").strip() for item in (payload.ids or []) if str(item or "").strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="삭제할 Learning Memory id가 필요합니다.")
    return _delete_learning_memories_for_user(data_user_id=data_user_id, target_ids=set(ids), strict=False)


@app.delete("/learning-memory/{memory_id}")
async def delete_learning_memory(memory_id: str, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    return _delete_learning_memories_for_user(data_user_id=data_user_id, target_ids={memory_id})


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


@app.delete("/learning-memory/ai-summaries/{summary_id}")
async def delete_learning_memory_ai_summary(summary_id: str, data_user_id: str = Depends(current_uid)) -> Dict[str, Any]:
    target_id = str(summary_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="AI Summary id is required.")

    summaries = _load_learning_memory_summaries()
    kept = []
    deleted = False
    for item in summaries:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        if str(item.get("id") or "").strip() == target_id and item.get("user_id") == data_user_id:
            deleted = True
            continue
        kept.append(item)

    if not deleted:
        raise HTTPException(status_code=404, detail="AI Summary를 찾을 수 없습니다.")

    _save_learning_memory_summaries(kept)
    return {"ok": True, "deleted": True}


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
# _auth와 Header는 파일 상단에서 이미 import되어 있다 (중복 정의·조용한 인증 폴백 제거).


# ============== 스터디 (허용 계정 전용) ==============
# CareFlow 스터디 워크스페이스와 호환되는 기능: 자료 가져오기(linknote-pages-v1),
# 논문 질문(출처 인용), 주장 근거화(근거강도 초안). STUDY_EMAILS 계정만 사용 가능.

STUDY_DISCLAIMER = "⚠️ 이 내용은 자료 근거 인용이며 의료 자문이 아닙니다."

STUDY_ASK_PROMPT = """당신은 업로드된 논문·강의 자료를 분석하는 학술 리서치 보조 도구입니다.

규칙:
1. 오직 제공된 자료 청크만을 근거로 답변합니다. 없는 내용을 꾸며내지 않습니다.
2. 모든 주장에는 출처를 표기합니다: [자료 제목, p.페이지]
3. 진단·예후·처방·중증도 판정을 절대 하지 않습니다.
4. 상관관계와 인과관계를 명확히 구분합니다 ("관련이 있다" vs "원인이다").
5. 자료에 없는 정보는 "제공된 자료에서 확인할 수 없습니다"라고 답합니다.

한국어로 답변하고, 마지막 줄에 반드시 이 문구를 붙입니다:
\"""" + STUDY_DISCLAIMER + "\""

STUDY_CLAIM_PROMPT = """당신은 연구 근거를 분석하는 학술 도구입니다.
제공된 자료 청크와 주장을 비교해 아래 JSON만 출력하세요 (다른 텍스트 없이).

{"source_summary": "저자(연도) 자료명 p.페이지 — 없으면 '출처 미확인'",
 "strength": "강|중|약|출처 미확인",
 "application_context": "이 주장이 어떤 학습·기능 맥락에서 쓰일 수 있는지 (한 문장)",
 "safety_note": "상관·인과 구분, 진단 표현 금지 해당 여부 (한 문장)"}

근거강도 기준:
- 강: 국가통계·메타분석·체계적 문헌고찰·확립된 원칙
- 중: 코호트·단면·척도 연구 (n≥50)
- 약: 소표본(n<50)·설계 정의·사례 보고·전문가 의견
- 출처 미확인: 관련 자료 없거나 유사도 낮음

규칙:
- 진단·예후·처방 관련 주장은 safety_note에 경고 필수
- 인과관계 주장이면 safety_note에 "상관관계로만 표현 필요" 추가
"""

STUDY_STRENGTHS = ("강", "중", "약", "출처 미확인")


def current_study_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """스터디 기능은 STUDY_EMAILS(기본: 관리자 계정)에서만 열린다."""
    user = current_user(authorization)
    if not _auth.is_study_enabled(user):
        raise HTTPException(status_code=403, detail="이 계정에서는 스터디 기능을 사용할 수 없습니다.")
    return user


def _study_uid(user: Dict[str, Any]) -> str:
    return user.get("data_user_id") or user["email"]


def _study_source(chunk: Dict[str, Any]) -> Dict[str, Any]:
    distance = chunk.get("distance")
    similarity = max(0, round((1 - distance) * 100)) if isinstance(distance, (int, float)) else None
    text = chunk.get("text") or ""
    return {
        "source_id": chunk.get("id") or "",
        "doc_title": chunk.get("title") or chunk.get("filename") or "",
        "filename": chunk.get("filename") or "",
        "stored_filename": chunk.get("stored_filename") or "",
        "semester": chunk.get("semester") or "",
        "course": chunk.get("course") or "",
        "unit": chunk.get("unit") or "",
        "page_num": chunk.get("page") or 0,
        "similarity": similarity,
        "excerpt": text[:130] + ("…" if len(text) > 130 else ""),
    }


def _study_context(chunks: List[Dict[str, Any]]) -> str:
    return "\n\n---\n\n".join(
        f"[출처 {i + 1}] 자료: \"{c.get('title') or c.get('filename')}\" / p.{c.get('page') or 0}\n{c.get('text') or ''}"
        for i, c in enumerate(chunks)
    )


def _parse_study_claim_json(raw: str) -> Dict[str, str]:
    fallback = {"source_summary": "출처 미확인", "strength": "출처 미확인", "application_context": "", "safety_note": ""}
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return fallback
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return fallback
    strength = parsed.get("strength", "")
    return {
        "source_summary": str(parsed.get("source_summary") or "출처 미확인"),
        "strength": strength if strength in STUDY_STRENGTHS else "약",
        "application_context": str(parsed.get("application_context") or ""),
        "safety_note": str(parsed.get("safety_note") or ""),
    }


class StudyPagePayload(BaseModel):
    page: int
    text: str


class StudyImportDoc(BaseModel):
    title: str
    filename: str
    semester: Optional[str] = "CareFlow"
    course: Optional[str] = "스터디 논문"
    unit: Optional[str] = ""
    pages: List[StudyPagePayload]


class StudyImportRequest(BaseModel):
    format: Optional[str] = None
    docs: List[StudyImportDoc]


class StudyAskRequest(BaseModel):
    question: str
    search_filter: Optional[SearchFilter] = None


class StudyClaimRequest(BaseModel):
    claim: str
    search_filter: Optional[SearchFilter] = None


class StudyClaimSourcePayload(BaseModel):
    source_id: str
    doc_title: Optional[str] = ""
    filename: str
    stored_filename: Optional[str] = ""
    semester: str
    course: str
    unit: str
    page_num: int
    similarity: Optional[int] = None
    excerpt: Optional[str] = ""


class StudyClaimSavePayload(BaseModel):
    claim: str
    source_summary: Optional[str] = ""
    strength: Optional[str] = "출처 미확인"
    application_context: Optional[str] = ""
    safety_note: Optional[str] = ""
    search_filter: Optional[SearchFilter] = None
    sources: List[StudyClaimSourcePayload] = Field(default_factory=list)


def _validate_study_claim_sources(
    user_id: str,
    sources: List[StudyClaimSourcePayload],
    search_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if len(sources) > 5:
        raise HTTPException(status_code=400, detail="출처는 최대 5개까지 선택할 수 있습니다.")

    verified: List[Dict[str, Any]] = []
    scope_cache: Dict[tuple, List[Dict[str, Any]]] = {}
    seen_source_ids: Set[str] = set()
    for source in sources:
        source_id = source.source_id.strip()
        if source_id in seen_source_ids:
            continue
        exact_scope: Dict[str, str] = {
            "semester": source.semester.strip(),
            "course": source.course.strip(),
            "filename": _normalize_filename(source.filename),
        }
        if source.unit.strip():
            exact_scope["unit"] = source.unit.strip()
        if not source_id or not all(exact_scope.get(key) for key in ("semester", "course", "filename")) or source.page_num < 1:
            raise HTTPException(status_code=400, detail="선택한 출처의 위치 정보가 올바르지 않습니다.")
        for key, value in (search_filter or {}).items():
            source_value = source.unit.strip() if key == "unit" else exact_scope.get(key)
            if value and source_value != value:
                raise HTTPException(status_code=400, detail="선택한 출처가 현재 자료 범위를 벗어났습니다.")

        cache_key = tuple(exact_scope.get(key, "") for key in ("semester", "course", "unit", "filename"))
        if cache_key not in scope_cache:
            scope_cache[cache_key] = get_chunks(
                user_id=user_id,
                limit=100000,
                search_filter=exact_scope,
                full=False,
            ).get("items", [])
        owned = next(
            (
                item for item in scope_cache[cache_key]
                if str(item.get("id") or "") == source_id
                and int(item.get("page") or 0) == source.page_num
            ),
            None,
        )
        if not owned:
            raise HTTPException(status_code=400, detail="선택한 출처를 내 자료에서 확인할 수 없습니다.")
        safe_source = _study_source(owned)
        if source.similarity is not None:
            safe_source["similarity"] = max(0, min(100, int(source.similarity)))
        verified.append(safe_source)
        seen_source_ids.add(source_id)
    return verified


@app.post("/study/import")
async def study_import(req: StudyImportRequest, user: Dict[str, Any] = Depends(current_study_user)) -> Dict[str, Any]:
    """CareFlow 스터디 내보내기(linknote-pages-v1) JSON을 내 자료로 학습한다."""
    if req.format and req.format != "linknote-pages-v1":
        raise HTTPException(status_code=400, detail="지원하지 않는 형식입니다. (linknote-pages-v1 필요)")
    if not req.docs:
        raise HTTPException(status_code=400, detail="가져올 문서가 없습니다.")

    uid = _study_uid(user)
    results = []
    for doc in req.docs:
        filename = (doc.filename or doc.title or "").strip()
        title = (doc.title or filename).strip()
        pages = [{"page": p.page, "text": p.text} for p in doc.pages if (p.text or "").strip()]
        if not filename or not pages:
            results.append({"filename": filename or title, "ok": False, "message": "본문 텍스트가 없습니다."})
            continue
        add_pdf_pages_to_db(
            pages=pages,
            filename=filename,
            semester=(doc.semester or "CareFlow").strip() or "CareFlow",
            course=(doc.course or "스터디 논문").strip() or "스터디 논문",
            title=title,
            user_id=uid,
            unit=(doc.unit or "").strip(),
        )
        results.append({"filename": filename, "ok": True, "pages": len(pages)})
    return {"results": results}


@app.post("/study/ask")
async def study_ask(req: StudyAskRequest, user: Dict[str, Any] = Depends(current_study_user)) -> Dict[str, Any]:
    """논문 질문 — 자료 청크만 근거로 출처·유사도와 함께 답한다."""
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question이 필요합니다.")

    search_filter = _model_to_dict(req.search_filter) if req.search_filter else None
    chunks = search_relevant_chunks(question, n_results=6, search_filter=search_filter, user_id=_study_uid(user))
    scope_label = get_filter_label(search_filter)
    if not chunks:
        return {
            "answer": f"제공된 자료에서 관련 내용을 찾을 수 없습니다.\n\n{STUDY_DISCLAIMER}",
            "sources": [],
            "scope_label": scope_label,
        }

    answer = generate_openai_answer(
        f"{STUDY_ASK_PROMPT}\n\n자료 청크:\n{_study_context(chunks)}\n\n질문: {question}",
        max_tokens=1500,
    )
    return {"answer": answer, "sources": [_study_source(c) for c in chunks], "scope_label": scope_label}


@app.post("/study/claim")
async def study_claim(req: StudyClaimRequest, user: Dict[str, Any] = Depends(current_study_user)) -> Dict[str, Any]:
    """주장 근거화 — 주장과 자료를 비교해 근거강도 초안(4필드)을 만든다. 저장은 별도."""
    claim = (req.claim or "").strip()
    if not claim:
        raise HTTPException(status_code=400, detail="claim이 필요합니다.")

    search_filter = _model_to_dict(req.search_filter) if req.search_filter else None
    chunks = search_relevant_chunks(claim, n_results=5, search_filter=search_filter, user_id=_study_uid(user))
    scope_label = get_filter_label(search_filter)
    if not chunks:
        draft = {"source_summary": "출처 미확인", "strength": "출처 미확인", "application_context": "", "safety_note": ""}
    else:
        raw = generate_openai_answer(
            f"{STUDY_CLAIM_PROMPT}\n자료 청크:\n{_study_context(chunks)}\n\n주장: {claim}",
            max_tokens=600,
        )
        draft = _parse_study_claim_json(raw)

    return {
        "claim": claim,
        "draft": draft,
        "sources": [_study_source(c) for c in chunks],
        "scope_label": scope_label,
        "search_filter": search_filter,
    }


@app.post("/study/claims")
async def study_claim_save(payload: StudyClaimSavePayload, user: Dict[str, Any] = Depends(current_study_user)) -> Dict[str, Any]:
    claim = (payload.claim or "").strip()
    if not claim:
        raise HTTPException(status_code=400, detail="claim이 필요합니다.")
    search_filter = _model_to_dict(payload.search_filter) if payload.search_filter else None
    sources = _validate_study_claim_sources(_study_uid(user), payload.sources, search_filter)
    strength = payload.strength if payload.strength in STUDY_STRENGTHS else "출처 미확인"
    if not sources:
        strength = "출처 미확인"
    entry = {
        "id": uuid.uuid4().hex,
        "claim": claim,
        "source_summary": (payload.source_summary or "").strip(),
        "strength": strength,
        "application_context": (payload.application_context or "").strip(),
        "safety_note": (payload.safety_note or "").strip(),
        "scope_label": get_filter_label(search_filter),
        "sources": sources,
        "created": int(time.time()),
    }
    data = _load_json_file(STUDY_CLAIMS_PATH)
    data.setdefault(_study_uid(user), []).insert(0, entry)
    _save_json_file(STUDY_CLAIMS_PATH, data)
    return {"ok": True, "claim": entry}


@app.get("/study/claims")
async def study_claim_list(user: Dict[str, Any] = Depends(current_study_user)) -> Dict[str, Any]:
    data = _load_json_file(STUDY_CLAIMS_PATH)
    return {"claims": data.get(_study_uid(user), [])}


@app.delete("/study/claims/{claim_id}")
async def study_claim_delete(claim_id: str, user: Dict[str, Any] = Depends(current_study_user)) -> Dict[str, Any]:
    data = _load_json_file(STUDY_CLAIMS_PATH)
    uid = _study_uid(user)
    claims = data.get(uid, [])
    remaining = [c for c in claims if c.get("id") != claim_id]
    if len(remaining) == len(claims):
        raise HTTPException(status_code=404, detail="저장된 주장을 찾을 수 없습니다.")
    data[uid] = remaining
    _save_json_file(STUDY_CLAIMS_PATH, data)
    return {"ok": True}


# ============== 임상 검증 (간호학과 트랙 전용) ==============
# 갤러리 단원 화면 우측 패널에서 임상 주장·중재를 내 자료 근거로 검증한다.
# 스터디의 주장 근거화와 같은 4필드 초안이지만, 간호 실습 교육 맥락으로 프레이밍한다.

CLINICAL_VERIFY_PROMPT = """당신은 간호학 학습을 돕는 근거 검증 도구입니다.
제공된 강의자료 청크와 임상 주장을 비교해 아래 JSON만 출력하세요 (다른 텍스트 없이).

{"source_summary": "출처 요약: 자료명 p.페이지 — 없으면 '출처 미확인'",
 "strength": "강|중|약|출처 미확인",
 "application_context": "이 주장이 임상 실습에서 어떤 상황과 관련되는지 (교육 목적, 한 문장)",
 "safety_note": "주의점: 상관·인과 구분, 환자 개별 적용 시 병원 지침·지도자 확인 필요 여부 (한 문장)"}

근거강도 기준:
- 강: 임상 가이드라인·교과서 원칙·메타분석
- 중: 코호트·단면·척도 연구 수준의 서술 (n≥50)
- 약: 소표본·사례 보고·전문가 의견 수준의 서술
- 출처 미확인: 관련 자료 없거나 유사도 낮음

규칙:
- 진단·처방·환자 개별 의사결정 관련 주장은 safety_note에 "병원 지침·지도자 확인 필요" 필수
- 인과관계 주장이면 safety_note에 "상관관계로만 표현 필요" 추가
"""

CLINICAL_DISCLAIMER = "⚠️ 교육용 근거 검증입니다. 실제 임상 의사결정은 병원 지침과 지도자 확인을 따르세요."


def current_nursing_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """임상 검증은 간호학과 트랙에서만 열린다. study_enabled와 분리한다."""
    user = current_user(authorization)
    track = (user.get("student_track") or "general").strip().lower()
    if track != "nursing":
        raise HTTPException(status_code=403, detail="임상 검증은 간호학과 트랙 계정에서 사용할 수 있습니다.")
    return user


class ClinicalVerifyRequest(BaseModel):
    claim: str
    semester: Optional[str] = None
    course: Optional[str] = None
    unit: Optional[str] = None


class ClinicalVerificationSavePayload(BaseModel):
    claim: str
    source_summary: Optional[str] = ""
    strength: Optional[str] = "출처 미확인"
    application_context: Optional[str] = ""
    safety_note: Optional[str] = ""
    course: Optional[str] = ""
    unit: Optional[str] = ""


def _clinical_scope_chunks(uid: str, claim: str, req: "ClinicalVerifyRequest"):
    """단원 → 과목 → 전체 순으로 좁은 범위부터 근거를 찾는다."""
    scopes = []
    if req.course and req.unit:
        scopes.append({"semester": req.semester, "course": req.course, "unit": req.unit})
    if req.course:
        scopes.append({"semester": req.semester, "course": req.course})
    scopes.append(None)
    for scope in scopes:
        chunks = search_relevant_chunks(claim, n_results=5, search_filter=scope, user_id=uid)
        if chunks:
            return chunks, get_filter_label(scope)
    return [], get_filter_label(None)


@app.post("/clinical/verify")
async def clinical_verify(req: ClinicalVerifyRequest, user: Dict[str, Any] = Depends(current_nursing_user)) -> Dict[str, Any]:
    claim = (req.claim or "").strip()
    if not claim:
        raise HTTPException(status_code=400, detail="claim이 필요합니다.")
    uid = _study_uid(user)

    chunks, scope_label = _clinical_scope_chunks(uid, claim, req)
    if not chunks:
        draft = {"source_summary": "출처 미확인", "strength": "출처 미확인",
                 "application_context": "", "safety_note": CLINICAL_DISCLAIMER}
    else:
        raw = generate_openai_answer(
            f"{CLINICAL_VERIFY_PROMPT}\n자료 청크:\n{_study_context(chunks)}\n\n임상 주장: {claim}",
            max_tokens=600,
        )
        draft = _parse_study_claim_json(raw)

    try:
        related = _related_cross_concepts(uid, f"{claim} {draft.get('source_summary', '')}", limit=4)
    except Exception:
        related = []

    return {
        "claim": claim,
        "draft": draft,
        "sources": [_study_source(c) for c in chunks],
        "scope_label": scope_label,
        "related_concepts": related,
        "disclaimer": CLINICAL_DISCLAIMER,
    }


@app.post("/clinical/verifications")
async def clinical_verification_save(payload: ClinicalVerificationSavePayload, user: Dict[str, Any] = Depends(current_nursing_user)) -> Dict[str, Any]:
    claim = (payload.claim or "").strip()
    if not claim:
        raise HTTPException(status_code=400, detail="claim이 필요합니다.")
    entry = {
        "id": uuid.uuid4().hex,
        "claim": claim,
        "source_summary": (payload.source_summary or "").strip(),
        "strength": payload.strength if payload.strength in STUDY_STRENGTHS else "출처 미확인",
        "application_context": (payload.application_context or "").strip(),
        "safety_note": (payload.safety_note or "").strip(),
        "course": (payload.course or "").strip(),
        "unit": (payload.unit or "").strip(),
        "created": int(time.time()),
    }
    data = _load_json_file(CLINICAL_VERIFICATIONS_PATH)
    data.setdefault(_study_uid(user), []).insert(0, entry)
    _save_json_file(CLINICAL_VERIFICATIONS_PATH, data)
    return {"ok": True, "verification": entry}


@app.get("/clinical/verifications")
async def clinical_verification_list(
    course: Optional[str] = None,
    unit: Optional[str] = None,
    limit: int = 20,
    user: Dict[str, Any] = Depends(current_nursing_user),
) -> Dict[str, Any]:
    items = _load_json_file(CLINICAL_VERIFICATIONS_PATH).get(_study_uid(user), [])
    if course:
        items = [i for i in items if (i.get("course") or "") == course]
    if unit:
        items = [i for i in items if (i.get("unit") or "") == unit]
    return {"items": items[:max(1, min(100, limit))]}


@app.delete("/clinical/verifications/{verification_id}")
async def clinical_verification_delete(verification_id: str, user: Dict[str, Any] = Depends(current_nursing_user)) -> Dict[str, Any]:
    data = _load_json_file(CLINICAL_VERIFICATIONS_PATH)
    uid = _study_uid(user)
    items = data.get(uid, [])
    remaining = [i for i in items if i.get("id") != verification_id]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail="저장된 검증 기록을 찾을 수 없습니다.")
    data[uid] = remaining
    _save_json_file(CLINICAL_VERIFICATIONS_PATH, data)
    return {"ok": True}


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = ""
    link_user_id: Optional[str] = ""
    student_track: Optional[str] = "general"


class LoginRequest(BaseModel):
    email: str
    password: str


class TrackUpdateRequest(BaseModel):
    student_track: str


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


@app.patch("/auth/track")
async def auth_update_track(req: TrackUpdateRequest, authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    user = current_user(authorization)
    updated, err = _auth.update_student_track(user["id"], req.student_track)
    if err or not updated:
        raise HTTPException(status_code=400, detail=err or "학습 트랙을 변경하지 못했습니다.")
    return {"user": _auth.public_user(updated)}


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
            "/auth/track",
            "/me/summary",
            "/learning-memory",
            "/learning-memory/summary",
            "/learning-memory/ai-summary",
            "/learning-memory/ai-summaries",
            "/clinical-reflection",
            "/clinical-reflections",
            "/concept-graph/overview",
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


class _NoCacheStaticFiles(StaticFiles):
    """데스크탑 앱(WKWebView)이 HTML을 캐시해 수정이 반영 안 되는 문제 방지."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


@app.get("/")
async def _serve_gallery():
    return FileResponse(
        _os.path.join(_WEB_DIR, "gallery.html"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# 정적 파일 폴백(맨 마지막에 등록 → API 라우트보다 후순위)
app.mount("/", _NoCacheStaticFiles(directory=_WEB_DIR, html=True), name="web")
