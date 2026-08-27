"""Read-only, no-LLM audit for concept extraction quality."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Sequence

from concept_quality import build_concept_coverage_report, normalize_concept_term


GENERIC_CONCEPT_NAMES = {
    "가지", "결절", "세동", "질환", "장애", "증상", "원인", "치료", "진단",
    "검사", "간호", "약물", "방법", "과정", "기능", "구조", "변화", "상태",
    "종류", "개념", "특징", "정의",
}


def _term_fragments(value: Any):
    values = value if isinstance(value, list) else [value]
    out = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        for part in [text, *re.split(r"[,;/]", text)]:
            clean = part.strip()
            if clean and clean not in out:
                out.append(clean)
    return out


def _concept_terms(concept: Mapping[str, Any]):
    values = []
    for key in ("name", "concept", "keyword", "aliases", "synonyms", "alias"):
        values.extend(_term_fragments(concept.get(key)))
    return [value for value in values if len(normalize_concept_term(value)) >= 2]


def _source_normalize(value: Any):
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def _source_pages(chunks):
    pages = defaultdict(list)
    for chunk in chunks:
        pages[(str(chunk.get("filename") or ""), int(chunk.get("page") or 0))].append(
            str(chunk.get("text") or "")
        )
    return {key: _source_normalize("\n".join(parts)) for key, parts in pages.items()}


def _concept_source_grounding(concept, page_sources):
    terms = [_source_normalize(value) for value in _concept_terms(concept)]
    terms = [term for term in terms if len(term) >= 2]
    source_values = list(page_sources.values())
    source_grounded = any(term in source for term in terms for source in source_values)

    evidence = concept.get("occurrences") if isinstance(concept.get("occurrences"), list) else []
    if not evidence and (concept.get("page") is not None or concept.get("filename")):
        evidence = [{"filename": concept.get("filename"), "page": concept.get("page")}]
    evidence_grounded = False
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        try:
            key = (str(item.get("filename") or ""), int(item.get("page") or 0))
        except (TypeError, ValueError):
            continue
        source = page_sources.get(key, "")
        surface_terms = [_source_normalize(value) for value in _term_fragments(item.get("surface"))]
        check_terms = [term for term in surface_terms if term] or terms
        if source and any(term in source for term in check_terms):
            evidence_grounded = True
            break
    return {
        "source_grounded": source_grounded,
        "has_evidence_metadata": bool(evidence),
        "evidence_grounded": evidence_grounded,
    }


def _identity_conflicts(concepts):
    owners = defaultdict(set)
    for concept in concepts:
        name = str(concept.get("name") or concept.get("keyword") or "").strip()
        for term in _concept_terms(concept):
            owners[normalize_concept_term(term)].add(name)
    return [
        {"term": term, "concepts": sorted(names)}
        for term, names in owners.items()
        if term and len(names) > 1
    ]


def _risk_level(score):
    if score >= 70:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def audit_unit(chunks, concepts, saved_aliases=None):
    chunks = list(chunks)
    concepts = [item for item in concepts if isinstance(item, Mapping)]
    pages = _source_pages(chunks)
    coverage = build_concept_coverage_report(chunks, concepts, saved_aliases or {})

    grounding = [_concept_source_grounding(concept, pages) for concept in concepts]
    source_ungrounded = [
        str(concept.get("name") or concept.get("keyword") or "")
        for concept, result in zip(concepts, grounding)
        if not result["source_grounded"]
    ]
    missing_evidence = [
        str(concept.get("name") or concept.get("keyword") or "")
        for concept, result in zip(concepts, grounding)
        if not result["has_evidence_metadata"]
    ]
    invalid_evidence = [
        str(concept.get("name") or concept.get("keyword") or "")
        for concept, result in zip(concepts, grounding)
        if result["has_evidence_metadata"] and not result["evidence_grounded"]
    ]
    generic = sorted({
        str(concept.get("name") or "").strip()
        for concept in concepts
        if normalize_concept_term(concept.get("name")) in {
            normalize_concept_term(name) for name in GENERIC_CONCEPT_NAMES
        }
    })
    normalized_names = Counter(
        normalize_concept_term(concept.get("name") or concept.get("keyword"))
        for concept in concepts
    )
    duplicate_names = sorted(name for name, count in normalized_names.items() if name and count > 1)
    conflicts = _identity_conflicts(concepts)

    known_quality = [chunk for chunk in chunks if chunk.get("text_quality_score") is not None]
    poor_quality = [
        chunk for chunk in known_quality
        if float(chunk.get("text_quality_score") or 0) < 40
    ]
    empty_chunks = sum(not str(chunk.get("text") or "").strip() for chunk in chunks)
    combined_source = "".join(str(chunk.get("text") or "") for chunk in chunks)
    korean_chars = len(re.findall(r"[가-힣]", combined_source))
    latin_chars = len(re.findall(r"[A-Za-z]", combined_source))
    language_chars = korean_chars + latin_chars

    candidate_count = coverage["candidate_count"]
    missing_count = coverage["missing_count"]
    missing_ratio = missing_count / candidate_count if candidate_count else 0.0
    ungrounded_ratio = len(source_ungrounded) / len(concepts) if concepts else 0.0
    page_count = len(pages)
    density = len(concepts) / page_count if page_count else 0.0

    score = 0
    if chunks and not concepts:
        score += 80
    score += round(missing_ratio * 40) if candidate_count >= 3 else 0
    score += round(ungrounded_ratio * 30)
    if page_count >= 10 and density < 0.35:
        score += 15
    score += min(10, len(generic) * 3)
    score += min(10, (len(duplicate_names) + len(conflicts)) * 2)
    if known_quality and len(poor_quality) / len(known_quality) >= 0.2:
        score += 10
    score = min(100, score)

    missing_candidates = []
    for candidate in coverage["missing"]:
        occurrence = next(iter(candidate.get("occurrences") or []), {})
        missing_candidates.append({
            "name": candidate.get("name"),
            "aliases": candidate.get("aliases") or [],
            "filename": occurrence.get("filename"),
            "page": occurrence.get("page"),
            "surface": occurrence.get("surface"),
            "candidate_rule": occurrence.get("candidate_rule"),
        })

    return {
        "risk_score": score,
        "risk_level": _risk_level(score),
        "chunk_count": len(chunks),
        "page_count": page_count,
        "file_count": len({str(chunk.get("filename") or "") for chunk in chunks}),
        "concept_count": len(concepts),
        "concepts_per_page": round(density, 3),
        "candidate_count": candidate_count,
        "covered_candidate_count": coverage["covered_count"],
        "missing_candidate_count": missing_count,
        "missing_candidate_ratio": round(missing_ratio, 4),
        "missing_candidates": missing_candidates,
        "source_ungrounded_concepts": source_ungrounded,
        "missing_evidence_metadata_concepts": missing_evidence,
        "invalid_evidence_concepts": invalid_evidence,
        "generic_concepts": generic,
        "duplicate_normalized_names": duplicate_names,
        "identity_conflicts": conflicts,
        "empty_chunk_count": empty_chunks,
        "text_quality_metadata_chunks": len(known_quality),
        "poor_text_quality_chunks": len(poor_quality),
        "source_korean_chars": korean_chars,
        "source_latin_chars": latin_chars,
        "source_korean_ratio": round(korean_chars / language_chars, 4) if language_chars else 0.0,
    }


def audit_workspace(
    chunks_by_scope,
    concepts_by_scope,
    saved_aliases=None,
    registry_scopes=(),
    expected_languages=None,
):
    chunk_scopes = set(chunks_by_scope)
    concept_scopes = set(concepts_by_scope)
    scopes = sorted(chunk_scopes | concept_scopes)
    units = []
    for semester, course, unit in scopes:
        result = audit_unit(
            chunks_by_scope.get((semester, course, unit), []),
            concepts_by_scope.get((semester, course, unit), []),
            saved_aliases=saved_aliases,
        )
        result.update({"semester": semester, "course": course, "unit": unit})
        expected_language = (expected_languages or {}).get((semester, course, unit))
        result["expected_language"] = expected_language
        result["suspected_language_loss"] = bool(
            expected_language == "ko"
            and result["page_count"] >= 3
            and result["source_latin_chars"] > 0
            and result["source_korean_ratio"] < 0.25
        )
        if result["suspected_language_loss"]:
            result["risk_score"] = min(100, result["risk_score"] + 15)
            result["risk_level"] = _risk_level(result["risk_score"])
        if (semester, course, unit) in chunk_scopes - concept_scopes:
            result["scope_issue"] = "chunks_without_concept_unit"
            result["risk_score"] = max(result["risk_score"], 80)
            result["risk_level"] = _risk_level(result["risk_score"])
        elif (semester, course, unit) in concept_scopes - chunk_scopes:
            result["scope_issue"] = "concept_unit_without_chunks"
            result["risk_score"] = max(result["risk_score"], 70)
            result["risk_level"] = _risk_level(result["risk_score"])
        units.append(result)
    units.sort(key=lambda item: (-item["risk_score"], item["semester"], item["course"], item["unit"]))

    severity = Counter(unit["risk_level"] for unit in units)
    courses = defaultdict(lambda: {"unit_count": 0, "risk_total": 0, "high_or_critical": 0})
    for unit in units:
        key = f'{unit["semester"]} · {unit["course"]}'
        courses[key]["unit_count"] += 1
        courses[key]["risk_total"] += unit["risk_score"]
        courses[key]["high_or_critical"] += unit["risk_level"] in {"high", "critical"}
    course_summary = []
    for key, item in courses.items():
        semester, course = key.split(" · ", 1)
        course_summary.append({
            "semester": semester,
            "course": course,
            "unit_count": item["unit_count"],
            "average_risk": round(item["risk_total"] / item["unit_count"], 1),
            "high_or_critical_units": item["high_or_critical"],
        })
    course_summary.sort(key=lambda item: (-item["average_risk"], item["course"]))

    registry_scopes = set(registry_scopes)
    return {
        "summary": {
            "unit_count": len(units),
            "chunk_count": sum(unit["chunk_count"] for unit in units),
            "page_count": sum(unit["page_count"] for unit in units),
            "concept_count": sum(unit["concept_count"] for unit in units),
            "candidate_count": sum(unit["candidate_count"] for unit in units),
            "missing_candidate_count": sum(unit["missing_candidate_count"] for unit in units),
            "source_ungrounded_concept_count": sum(len(unit["source_ungrounded_concepts"]) for unit in units),
            "missing_evidence_metadata_count": sum(len(unit["missing_evidence_metadata_concepts"]) for unit in units),
            "generic_concept_count": sum(len(unit["generic_concepts"]) for unit in units),
            "identity_conflict_count": sum(len(unit["identity_conflicts"]) for unit in units),
            "text_quality_metadata_chunks": sum(unit["text_quality_metadata_chunks"] for unit in units),
            "poor_text_quality_chunks": sum(unit["poor_text_quality_chunks"] for unit in units),
            "suspected_language_loss_unit_count": sum(unit["suspected_language_loss"] for unit in units),
            "severity": dict(severity),
            "chunk_only_scope_count": len(chunk_scopes - concept_scopes),
            "concept_only_scope_count": len(concept_scopes - chunk_scopes),
            "registry_missing_scope_count": len(chunk_scopes - registry_scopes) if registry_scopes else None,
            "external_api_calls": 0,
        },
        "course_summary": course_summary,
        "chunk_only_scopes": [list(scope) for scope in sorted(chunk_scopes - concept_scopes)],
        "concept_only_scopes": [list(scope) for scope in sorted(concept_scopes - chunk_scopes)],
        "registry_missing_scopes": [list(scope) for scope in sorted(chunk_scopes - registry_scopes)] if registry_scopes else [],
        "units": units,
    }


def render_markdown(report, user_id):
    summary = report["summary"]
    lines = [
        "# LinkNote 전체 개념 품질 무료 진단",
        "",
        f"- 사용자 범위: `{user_id}`",
        f"- 생성 시각(UTC): `{report['generated_at']}`",
        f"- 외부 API 호출: `{summary['external_api_calls']}`",
        f"- 단원 / chunk / 페이지 / 개념: `{summary['unit_count']} / {summary['chunk_count']} / {summary['page_count']} / {summary['concept_count']}`",
        "",
        "## 해석 주의",
        "",
        "이 보고서는 결정적 규칙으로 위험 단원을 선별한다. 누락 후보는 재검토 대상이지 확정 개념이 아니며, 원문에 없는 기존 개념도 OCR·표기 차이 때문에 오탐될 수 있다.",
        "",
        "## 요약",
        "",
        f"- 위험도: `{json.dumps(summary['severity'], ensure_ascii=False)}`",
        f"- 후보 `{summary['candidate_count']}`개 중 미포함 후보: `{summary['missing_candidate_count']}`개",
        f"- 원문 표기를 찾지 못한 기존 개념: `{summary['source_ungrounded_concept_count']}`개",
        f"- 파일·페이지 근거 metadata가 없는 기존 개념: `{summary['missing_evidence_metadata_count']}`개",
        f"- 일반어 검토 대상: `{summary['generic_concept_count']}`개",
        f"- 동일 term 소유 충돌: `{summary['identity_conflict_count']}`개",
        f"- 텍스트 품질 metadata 보유 chunk: `{summary['text_quality_metadata_chunks']}/{summary['chunk_count']}`",
        f"- 한국어 자료의 OCR/언어 손실 의심 단원: `{summary['suspected_language_loss_unit_count']}`개",
        f"- chunk/개념 단원 키 불일치: `{summary['chunk_only_scope_count']} / {summary['concept_only_scope_count']}`",
        "",
        "## 과목별 위험도",
        "",
        "| 학기 | 과목 | 단원 | 평균 위험도 | high/critical |",
        "|---|---|---:|---:|---:|",
    ]
    for item in report["course_summary"]:
        lines.append(f"| {item['semester']} | {item['course']} | {item['unit_count']} | {item['average_risk']} | {item['high_or_critical_units']} |")
    lines.extend([
        "",
        "## 단원별 순위",
        "",
        "| 위험도 | 점수 | 학기 | 과목 | 단원 | 페이지 | 개념 | 누락 후보 | 원문 미확인 |",
        "|---|---:|---|---|---|---:|---:|---:|---:|",
    ])
    for item in report["units"]:
        lines.append(
            f"| {item['risk_level']} | {item['risk_score']} | {item['semester']} | {item['course']} | "
            f"{item['unit']} | {item['page_count']} | {item['concept_count']} | "
            f"{item['missing_candidate_count']} | {len(item['source_ungrounded_concepts'])} |"
        )
    lines.extend(["", "## 우선 검토 단원 상세", ""])
    for item in report["units"][:20]:
        missing = ", ".join(candidate["name"] for candidate in item["missing_candidates"][:15]) or "없음"
        ungrounded = ", ".join(item["source_ungrounded_concepts"][:15]) or "없음"
        generic = ", ".join(item["generic_concepts"]) or "없음"
        lines.extend([
            f"### {item['course']} · {item['unit']} ({item['risk_level']} {item['risk_score']})",
            "",
            f"- 누락 후보 예시: {missing}",
            f"- 원문 미확인 기존 개념: {ungrounded}",
            f"- 일반어 검토 대상: {generic}",
            f"- alias/keyword 소유 충돌: {len(item['identity_conflicts'])}개",
            f"- OCR/언어 손실 의심: {'예' if item['suspected_language_loss'] else '아니요'} (한글 비율 {item['source_korean_ratio']})",
            "",
        ])
    return "\n".join(lines) + "\n"


def _load_registry_metadata(path):
    if not os.path.exists(path):
        return set(), {}
    with open(path, encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    scopes = {
        (row.get("semester", ""), row.get("course", ""), (row.get("unit") or "").strip())
        for row in rows
    }
    languages = {}
    for row in rows:
        scope = (row.get("semester", ""), row.get("course", ""), (row.get("unit") or "").strip())
        language = (row.get("language") or "").strip().lower()
        if language:
            languages[scope] = language
    return scopes, languages


def main():
    parser = argparse.ArgumentParser(description="Run a read-only concept quality audit")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", "./data"))
    parser.add_argument("--registry", default=os.getenv("REGISTRY_CSV", "material_registry.csv"))
    parser.add_argument("--output-prefix", required=True)
    args = parser.parse_args()

    import rag

    concepts_path = os.path.join(args.data_dir, "concepts.json")
    profiles_path = os.path.join(args.data_dir, "search_profiles.json")
    before = hashlib.sha256(open(concepts_path, "rb").read()).hexdigest()
    with open(concepts_path, encoding="utf-8") as handle:
        raw_concepts = json.load(handle).get(args.user_id, {})
    profiles = {}
    if os.path.exists(profiles_path):
        with open(profiles_path, encoding="utf-8") as handle:
            profiles = json.load(handle)
    saved_aliases = profiles.get(args.user_id, {}).get("aliases", {})

    concepts_by_scope = {}
    for semester, courses in raw_concepts.items():
        for course, units in courses.items():
            for unit, concepts in units.items():
                concepts_by_scope[(semester, course, unit)] = concepts if isinstance(concepts, list) else []

    result = rag.collection.get(where={"user_id": args.user_id}, include=["metadatas", "documents"])
    chunks_by_scope = defaultdict(list)
    for metadata, document in zip(result.get("metadatas", []), result.get("documents", [])):
        scope = (metadata.get("semester", ""), metadata.get("course", ""), (metadata.get("unit") or "").strip())
        chunks_by_scope[scope].append({**metadata, "text": document or ""})

    registry_scopes, expected_languages = _load_registry_metadata(args.registry)
    report = audit_workspace(
        chunks_by_scope,
        concepts_by_scope,
        saved_aliases=saved_aliases,
        registry_scopes=registry_scopes,
        expected_languages=expected_languages,
    )
    report["user_id"] = args.user_id
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    after = hashlib.sha256(open(concepts_path, "rb").read()).hexdigest()
    report["integrity"] = {
        "concepts_sha256_before": before,
        "concepts_sha256_after": after,
        "concepts_unchanged": before == after,
    }

    output_dir = os.path.dirname(args.output_prefix)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output_prefix + ".json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    with open(args.output_prefix + ".md", "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report, args.user_id))
    print(json.dumps({"summary": report["summary"], "integrity": report["integrity"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
