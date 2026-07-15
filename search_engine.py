"""Deterministic search ranking helpers for LinkNote's local-first search."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


SEARCH_ALGORITHM_VERSION = "hybrid_personalized_v1"

_STOP_WORDS = {
    "그리고", "그러나", "관련", "자료", "찾아줘", "보여줘", "설명", "정리", "대해", "대한",
    "무엇", "어떤", "어떻게", "인가", "인지", "the", "and", "for", "with", "what", "how",
}

_INTENT_PATTERNS: Sequence[Tuple[str, Sequence[str]]] = (
    ("personal_memory", ("내 설명", "내가 설명", "기억", "learning memory", "recall", "피드백")),
    ("source_location", ("어디", "몇 페이지", "페이지", "pdf", "자료 찾아", "나온 자료", "출처")),
    ("connection", ("연결", "연계", "관련 개념", "다른 과목", "개념 관계")),
    ("comparison", ("차이", "비교", "공통점", "대조", "vs")),
    ("mechanism", ("기전", "과정", "원리", "왜", "어떻게")),
    ("review", ("복습", "시험", "외워", "핵심", "다시 볼")),
    ("definition", ("뭐야", "무엇", "정의", "뜻", "개념")),
)

INTENT_LABELS = {
    "personal_memory": "내 학습 기록",
    "source_location": "자료 위치",
    "connection": "개념 연결",
    "comparison": "개념 비교",
    "mechanism": "기전과 과정",
    "review": "복습",
    "definition": "개념 설명",
    "general": "일반 검색",
}

SOURCE_WEIGHTS = {
    "semantic": 0.40,
    "keyword": 0.25,
    "concept": 0.15,
    "learning": 0.14,
    "preference": 0.06,
}


def tokenize(text: str, limit: int = 16) -> List[str]:
    seen, tokens = set(), []
    for raw in re.findall(r"[0-9A-Za-z가-힣]+", text or ""):
        token = raw.lower().strip()
        if re.fullmatch(r"[가-힣]+", token):
            for suffix in ("으로", "에서", "까지", "부터", "처럼", "에게", "한테", "을", "를", "이", "가", "은", "는", "과", "와", "의", "에", "로"):
                if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                    token = token[:-len(suffix)]
                    break
        if len(token) < 2 or token in _STOP_WORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def classify_intent(question: str) -> str:
    lowered = (question or "").lower()
    if ("내가" in lowered or "내 설명" in lowered) and any(term in lowered for term in ("설명", "기억", "피드백", "어떻게")):
        return "personal_memory"
    for intent, patterns in _INTENT_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return intent
    return "general"


def resolve_scope(requested_scope: str, search_filter: Mapping[str, str], intent: str) -> Tuple[str, str]:
    requested = (requested_scope or "auto").strip().lower()
    if requested in {"single", "multi"}:
        return requested, "사용자가 검색 범위를 직접 선택했습니다."
    if search_filter:
        return "single", "현재 선택한 자료 범위를 우선 검색합니다."
    if intent in {"connection", "comparison"}:
        return "multi", "개념 연결 또는 비교 질문이라 여러 자료를 검색합니다."
    return "multi", "선택된 자료가 없어 내 전체 자료를 검색합니다."


def expand_tokens(tokens: Sequence[str], alias_map: Mapping[str, Sequence[str]], limit: int = 24) -> List[str]:
    expanded, seen = [], set()
    for token in tokens:
        values = [token]
        values.extend(alias_map.get(token, []))
        for value in values:
            normalized = str(value or "").lower().strip()
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            expanded.append(normalized)
            if len(expanded) >= limit:
                return expanded
    return expanded


def raw_text_score(text: str, tokens: Sequence[str]) -> float:
    if not tokens:
        return 0.0
    haystack = str(text or "").lower()
    score = 0.0
    for token in tokens:
        if token in haystack:
            score += 1.0
        score += min(haystack.count(token), 3) * 0.2
    return round(score, 4)


def normalized_keyword_score(text: str, tokens: Sequence[str]) -> float:
    if not tokens:
        return 0.0
    return round(min(1.0, raw_text_score(text, tokens) / max(1.0, len(tokens) * 1.2)), 4)


def semantic_score(distance: Any) -> float:
    try:
        value = max(0.0, float(distance))
    except (TypeError, ValueError):
        return 0.0
    return round(1.0 / (1.0 + value), 4)


def preference_score(profile: Mapping[str, Any], course: str = "", concept: str = "") -> float:
    course_counts = profile.get("course_counts") if isinstance(profile.get("course_counts"), dict) else {}
    concept_counts = profile.get("concept_counts") if isinstance(profile.get("concept_counts"), dict) else {}
    values = []
    if course:
        values.append(float(course_counts.get(course, 0) or 0))
    if concept:
        values.append(float(concept_counts.get(concept, 0) or 0))
    maximum = max([1.0, *[float(v or 0) for v in course_counts.values()], *[float(v or 0) for v in concept_counts.values()]])
    return round(min(1.0, max(values, default=0.0) / maximum), 4)


def learning_score(metadata: Mapping[str, Any]) -> float:
    if not metadata:
        return 0.0
    priority = max(0.0, min(100.0, float(metadata.get("review_priority", 0) or 0))) / 100.0
    state = str(metadata.get("learning_state") or "NEW").upper()
    state_bonus = {"REVIEW": 1.0, "LEARNING": 0.65, "NEW": 0.45, "MASTERED": 0.15}.get(state, 0.0)
    return round(min(1.0, priority * 0.7 + state_bonus * 0.3), 4)


def weighted_score(components: Mapping[str, float], weights: Mapping[str, float] = SOURCE_WEIGHTS) -> float:
    total = sum(float(components.get(key, 0.0) or 0.0) * weight for key, weight in weights.items())
    return round(max(0.0, min(1.0, total)), 4)


def score_reason(components: Mapping[str, float], matched_fields: Iterable[str] = ()) -> str:
    labels = {
        "semantic": "질문과 의미가 가깝습니다",
        "keyword": "질문 키워드가 포함되어 있습니다",
        "concept": "관련 개념과 일치합니다",
        "learning": "현재 복습 흐름과 관련이 있습니다",
        "preference": "자주 학습한 범위와 관련이 있습니다",
    }
    ranked = sorted(components.items(), key=lambda item: float(item[1] or 0), reverse=True)
    reasons = [labels[key] for key, value in ranked if value > 0 and key in labels][:2]
    fields = [str(field) for field in matched_fields if str(field)]
    if fields:
        reasons.append(f"일치 영역: {', '.join(fields[:3])}")
    return ". ".join(reasons) + ("." if reasons else "")


def matched_fields(fields: Mapping[str, str], tokens: Sequence[str]) -> List[str]:
    return [name for name, value in fields.items() if raw_text_score(value, tokens) > 0]
