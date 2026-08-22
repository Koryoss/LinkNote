"""Deterministic, read-only quality checks for concept extraction.

This module does not call an LLM and does not mutate ChromaDB or concepts.json.
It produces candidate and coverage reports that can be reviewed before a
re-extraction is approved.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Sequence


_ENGLISH_TERM_RE = re.compile(r"\(([A-Za-z][A-Za-z -]{1,62})\)")
_KOREAN_LABEL_RE = re.compile(r"^([가-힣][가-힣· /-]{1,30})\s*:")
_ENGLISH_ONLY_RE = re.compile(r"[A-Za-z][A-Za-z -]+")
_KOREAN_ONLY_RE = re.compile(r"[가-힣]+")
_GENERIC_LABELS = {
    "개념", "결과", "단계", "분류", "발생", "발생 원인", "원인", "정의",
    "종류", "주의점", "증상", "진단", "치료", "특징", "특징적 변화", "효능",
}
_GENERIC_CONCEPT_NAMES = {
    "개념", "개요", "결과", "단계", "목차", "발생 원인", "주의점", "학습목표",
    "효능", "특징적 변화", "주요질환", "주요혈액질환", "대표질환",
}
_SHORT_MEDICAL_LABELS = {
    "괴사", "궤양", "구축", "미란", "발열", "부종", "쇼크", "염증", "유착",
    "천공", "통증",
}
_NON_CONCEPT_ENGLISH_TERMS = {
    "age in years", "cell enzymes", "compression", "elevation", "ice", "kg", "rest",
}
_FRAGMENT_ENDINGS = (
    "으로진행", "에서발생", "에의한", "을통한", "를통한", "할수있음", "할수있다",
    "사용", "활용", "적용",
)
_TERM_OVERRIDES_PATH = os.path.join(
    os.path.dirname(__file__), "config", "concept_term_overrides.json"
)
_ENGLISH_PHRASE_START_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "because", "both", "called", "due",
    "each", "exactly", "for", "from", "have", "in", "is", "made", "not", "on",
    "once", "or", "represented", "same", "some", "such", "that", "the", "these",
    "this", "those", "to", "was", "were", "which", "with",
}
_ENGLISH_GRAMMAR_WORDS = {
    "a", "an", "and", "are", "as", "at", "because", "both", "by", "each", "for",
    "from", "has", "have", "in", "is", "not", "of", "on", "or", "that", "the",
    "these", "this", "those", "to", "was", "were", "which", "with",
}


def normalize_concept_term(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


@lru_cache(maxsize=1)
def _load_term_overrides() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_TERM_OVERRIDES_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    overrides: Dict[str, Dict[str, Any]] = {}
    for entry in payload.get("terms", []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict) or not str(entry.get("canonical_name") or "").strip():
            continue
        for alias in [entry.get("canonical_name"), *entry.get("aliases", [])]:
            key = normalize_concept_term(alias)
            if key:
                overrides[key] = dict(entry)
    return overrides


def _apply_standard_term_override(item: Dict[str, Any]) -> None:
    original_name = str(item.get("name") or "").strip()
    override = _load_term_overrides().get(normalize_concept_term(original_name))
    if not override:
        return
    canonical_name = str(override.get("canonical_name") or "").strip()
    if not canonical_name:
        return
    aliases = _list_values(item.get("aliases"))
    if original_name and normalize_concept_term(original_name) != normalize_concept_term(canonical_name):
        aliases.insert(0, original_name)
    item["name"] = canonical_name
    item["aliases"] = list(dict.fromkeys(aliases))
    item["verified_preferred_terms"] = [
        str(value).strip() for value in override.get("preferred_keywords", [])
        if str(value).strip()
    ]
    item["term_override_rule"] = "verified_dictionary"
    sources = override.get("sources") if isinstance(override.get("sources"), list) else []
    item["term_override_sources"] = [
        str(value).strip() for value in [override.get("source"), *sources]
        if str(value or "").strip()
    ]


def _list_values(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,;/]", value) if part.strip()]
    return []


def _concept_name(item: Mapping[str, Any]) -> str:
    return str(item.get("name") or item.get("concept") or item.get("keyword") or "").strip()


def build_quality_alias_groups(
    concepts: Sequence[Mapping[str, Any]],
    saved_aliases: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Build explicit aliases plus conservative review-only compound suffix aliases."""
    groups: List[Dict[str, Any]] = []
    seen = set()

    def add(name: str, aliases: Iterable[str], rule: str) -> None:
        clean_name = str(name or "").strip()
        clean_aliases = []
        for alias in aliases:
            value = str(alias or "").strip()
            if value and normalize_concept_term(value) != normalize_concept_term(clean_name):
                clean_aliases.append(value)
        key = (normalize_concept_term(clean_name), tuple(sorted(normalize_concept_term(x) for x in clean_aliases)), rule)
        if not clean_name or not clean_aliases or key in seen:
            return
        seen.add(key)
        groups.append({"name": clean_name, "aliases": list(dict.fromkeys(clean_aliases)), "rule": rule})

    for item in concepts:
        name = _concept_name(item)
        keyword = str(item.get("keyword") or "").strip()
        aliases = [keyword]
        for field in ("aliases", "synonyms", "alias"):
            aliases.extend(_list_values(item.get(field)))
        add(name, aliases, "extracted_alias")

        # A compound pair such as 심실빈맥 / ventricular tachycardia can provide
        # a review-only candidate 빈맥 / tachycardia. It is never persisted as a
        # canonical concept without later source validation.
        english_words = re.findall(r"[A-Za-z]+", keyword)
        if _KOREAN_ONLY_RE.fullmatch(name) and len(name) >= 4 and len(english_words) >= 2:
            korean_suffix = name[-2:]
            english_suffix = english_words[-1]
            add(korean_suffix, [english_suffix], "compound_suffix_alias")

    for name, aliases in (saved_aliases or {}).items():
        add(str(name), _list_values(aliases), "user_alias")

    groups.sort(key=lambda item: (item["name"], item["rule"], item["aliases"]))
    return groups


def _line_occurrence(chunk: Mapping[str, Any], line: str, surface: str, rule: str) -> Dict[str, Any]:
    return {
        "filename": chunk.get("filename", ""),
        "page": chunk.get("page"),
        "surface": surface,
        "evidence_span": re.sub(r"\s+", " ", line).strip()[:240],
        "candidate_rule": rule,
    }


def _plausible_parenthetical_english_term(surface: str) -> bool:
    words = re.findall(r"[A-Za-z]+", surface)
    if not words or len(words) > 6:
        return False
    lowered = [word.lower() for word in words]
    if lowered[0] in _ENGLISH_PHRASE_START_STOPWORDS:
        return False
    grammar_count = sum(word in _ENGLISH_GRAMMAR_WORDS for word in lowered)
    if len(words) >= 4 and grammar_count >= 2:
        return False
    if normalize_concept_term(surface) in {
        normalize_concept_term(value) for value in _NON_CONCEPT_ENGLISH_TERMS
    }:
        return False
    return True


def _plausible_bilingual_english_term(surface: str) -> bool:
    words = re.findall(r"[A-Za-z]+", str(surface or ""))
    return bool(
        _plausible_parenthetical_english_term(surface)
        and not (
            len(words) >= 3
            and len(words[0]) <= 2
            and words[0].islower()
        )
    )


def _candidate_label_rejection_reason(value: Any, rule: str) -> str:
    label = re.sub(r"\s+", " ", str(value or "")).strip(" -:;·|[]")
    normalized = normalize_concept_term(label)
    if not normalized:
        return "empty_candidate"
    if rule == "parenthetical_english":
        return "generic_english_item" if not _plausible_parenthetical_english_term(label) else ""
    if normalized in {
        normalize_concept_term(item) for item in (*_GENERIC_LABELS, *_GENERIC_CONCEPT_NAMES)
    }:
        return "generic_heading_or_item"
    if any(normalized.endswith(normalize_concept_term(ending)) for ending in _FRAGMENT_ENDINGS):
        return "sentence_fragment"
    if len(normalized) > 24 or "/" in label:
        return "sentence_fragment"
    if "과" in label and rule == "korean_definition_label":
        return "compound_section_label"
    if rule == "korean_definition_label" and normalized.endswith("계"):
        return "body_system_label"
    if rule == "korean_definition_label" and re.fullmatch(r"[가-힣]{2,3}", normalized) and normalized not in {
        normalize_concept_term(item) for item in _SHORT_MEDICAL_LABELS
    }:
        return "short_unverified_label"
    return ""


def detect_concept_candidates(
    chunks: Sequence[Mapping[str, Any]],
    alias_groups: Sequence[Mapping[str, Any]] = (),
    rejected_candidates: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Return high-precision candidates with page evidence, without writing data."""
    candidates: Dict[str, Dict[str, Any]] = {}
    claimed_surface: Dict[str, str] = {}

    def add(name: str, aliases: Iterable[str], occurrence: Dict[str, Any]) -> None:
        label = str(name or "").strip()
        key = normalize_concept_term(label)
        if len(key) < 2:
            return
        rule = str(occurrence.get("candidate_rule") or "")
        if rule in {"parenthetical_english", "korean_definition_label", "bilingual_pair"}:
            validation_label = occurrence.get("surface") if rule == "parenthetical_english" else label
            reason = _candidate_label_rejection_reason(validation_label, rule)
            if reason:
                if rejected_candidates is not None:
                    rejected_candidates.append({
                        "name": label,
                        "rule": rule,
                        "reason": reason,
                        "filename": occurrence.get("filename"),
                        "page": occurrence.get("page"),
                        "evidence_span": occurrence.get("evidence_span"),
                    })
                return
        item = candidates.setdefault(key, {
            "name": label,
            "aliases": [],
            "rules": [],
            "occurrences": [],
        })
        for alias in aliases:
            value = str(alias or "").strip()
            existing_aliases = {normalize_concept_term(x) for x in item["aliases"]}
            if value and normalize_concept_term(value) not in {key, *existing_aliases}:
                item["aliases"].append(value)
        rule = occurrence["candidate_rule"]
        if rule not in item["rules"]:
            item["rules"].append(rule)
        occurrence_key = (
            occurrence.get("filename"), occurrence.get("page"),
            normalize_concept_term(occurrence.get("surface")), rule,
        )
        if occurrence_key not in {
            (x.get("filename"), x.get("page"), normalize_concept_term(x.get("surface")), x.get("candidate_rule"))
            for x in item["occurrences"]
        }:
            item["occurrences"].append(occurrence)

    for group in alias_groups:
        name = str(group.get("name") or "").strip()
        aliases = _list_values(group.get("aliases"))
        terms = aliases if group.get("rule") == "compound_suffix_alias" else [name, *aliases]
        for term in terms:
            normalized = normalize_concept_term(term)
            if normalized:
                claimed_surface[normalized] = name

    for chunk in chunks:
        for raw_line in str(chunk.get("text") or "").splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue

            lowered = line.lower()
            for group in alias_groups:
                name = str(group.get("name") or "").strip()
                aliases = _list_values(group.get("aliases"))
                terms = aliases if group.get("rule") == "compound_suffix_alias" else [name, *aliases]
                matched = next((term for term in terms if str(term).lower() in lowered), None)
                if matched:
                    add(name, aliases, _line_occurrence(chunk, line, str(matched), str(group.get("rule") or "alias")))

            bilingual_pairs = []
            if line.startswith("[한영 병기]"):
                pair_source = line[len("[한영 병기]"):].strip()
                pair_match = re.fullmatch(
                    r"([가-힣][가-힣0-9· /-]{1,28})\s*[（(]\s*([A-Za-z][A-Za-z0-9 +/.,'’-]{1,62})\s*[)）]",
                    pair_source,
                )
                if pair_match:
                    bilingual_pairs.append((pair_match.group(1).strip(), pair_match.group(2).strip()))
            for korean, english in bilingual_pairs:
                if not _plausible_bilingual_english_term(english):
                    if rejected_candidates is not None:
                        rejected_candidates.append({
                            "name": korean,
                            "rule": "bilingual_pair",
                            "reason": "broken_bilingual_alias",
                            "filename": chunk.get("filename"),
                            "page": chunk.get("page"),
                            "evidence_span": line[:240],
                        })
                    continue
                claimed_name = claimed_surface.get(normalize_concept_term(korean)) or claimed_surface.get(normalize_concept_term(english))
                canonical = claimed_name or korean
                claimed_surface[normalize_concept_term(english)] = canonical
                add(
                    canonical,
                    [english],
                    _line_occurrence(chunk, line, korean, "bilingual_pair"),
                )

            for match in _ENGLISH_TERM_RE.finditer(line):
                surface = re.sub(r"\s+", " ", match.group(1)).strip()
                if not _ENGLISH_ONLY_RE.fullmatch(surface):
                    continue
                if not _plausible_parenthetical_english_term(surface):
                    if rejected_candidates is not None:
                        rejected_candidates.append({
                            "name": surface,
                            "rule": "parenthetical_english",
                            "reason": "generic_english_item_or_sentence",
                            "filename": chunk.get("filename"),
                            "page": chunk.get("page"),
                            "evidence_span": line[:240],
                        })
                    continue
                claimed_name = claimed_surface.get(normalize_concept_term(surface))
                add(
                    claimed_name or surface,
                    [surface] if claimed_name else [],
                    _line_occurrence(chunk, line, surface, "parenthetical_english"),
                )

            label_match = _KOREAN_LABEL_RE.match(line)
            if label_match:
                surface = label_match.group(1).strip(" -/·")
                if surface:
                    add(surface, [], _line_occurrence(chunk, line, surface, "korean_definition_label"))

    bilingual_owners = {}
    for key, item in candidates.items():
        if "bilingual_pair" not in item.get("rules", []):
            continue
        for alias in item.get("aliases", []):
            alias_key = normalize_concept_term(alias)
            if alias_key:
                bilingual_owners[alias_key] = key
    for english_key, owner_key in list(bilingual_owners.items()):
        if english_key == owner_key or english_key not in candidates or owner_key not in candidates:
            continue
        english_item = candidates[english_key]
        if not _ENGLISH_ONLY_RE.fullmatch(str(english_item.get("name") or "")):
            continue
        owner = candidates[owner_key]
        for rule in english_item.get("rules", []):
            if rule not in owner["rules"]:
                owner["rules"].append(rule)
        for occurrence in english_item.get("occurrences", []):
            occurrence_key = (
                occurrence.get("filename"), occurrence.get("page"),
                normalize_concept_term(occurrence.get("surface")), occurrence.get("candidate_rule"),
            )
            if occurrence_key not in {
                (x.get("filename"), x.get("page"), normalize_concept_term(x.get("surface")), x.get("candidate_rule"))
                for x in owner["occurrences"]
            }:
                owner["occurrences"].append(occurrence)
        del candidates[english_key]

    out = list(candidates.values())
    for item in out:
        item["aliases"].sort(key=str.lower)
        item["rules"].sort()
        item["occurrences"].sort(key=lambda x: (str(x.get("filename") or ""), int(x.get("page") or 0), x["candidate_rule"]))
    out.sort(key=lambda item: (int(item["occurrences"][0].get("page") or 0), item["name"].lower()))
    return out


def build_concept_coverage_report(
    chunks: Sequence[Mapping[str, Any]],
    concepts: Sequence[Mapping[str, Any]],
    saved_aliases: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    groups = build_quality_alias_groups(concepts, saved_aliases)
    candidates = detect_concept_candidates(chunks, groups)
    extracted_terms = set()
    for concept in concepts:
        for value in (
            _concept_name(concept),
            str(concept.get("keyword") or ""),
            *_list_values(concept.get("aliases")),
            *_list_values(concept.get("synonyms")),
        ):
            normalized = normalize_concept_term(value)
            if normalized:
                extracted_terms.add(normalized)

    covered, missing = [], []
    for candidate in candidates:
        terms = {normalize_concept_term(candidate["name"])}
        terms.update(normalize_concept_term(alias) for alias in candidate["aliases"])
        target = covered if any(term in extracted_terms for term in terms if term) else missing
        target.append(candidate)

    return {
        "candidate_count": len(candidates),
        "covered_count": len(covered),
        "missing_count": len(missing),
        "covered": covered,
        "missing": missing,
    }


def _normalized_source_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", "", text)


def _verified_acronym_bridge(
    concept: Mapping[str, Any],
    filename: str,
    surface: str,
    chunks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any] | None:
    """Ground a verified acronym through its exact long form in the same file."""
    acronym = unicodedata.normalize("NFKC", str(surface or "")).strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{2,7}", acronym):
        return None
    preferred = _list_values(concept.get("verified_preferred_terms"))
    preferred_keys = {normalize_concept_term(value) for value in preferred}
    if normalize_concept_term(acronym) not in preferred_keys:
        return None
    expansions = [
        value for value in preferred
        if len(re.findall(r"[A-Za-z]+", value)) >= 2
        and normalize_concept_term(value) != normalize_concept_term(acronym)
    ]
    for expansion in expansions:
        expansion_key = _normalized_source_text(expansion)
        for chunk in chunks:
            if (
                unicodedata.normalize("NFKC", str(chunk.get("filename") or ""))
                != unicodedata.normalize("NFKC", filename)
            ):
                continue
            if expansion_key and expansion_key in _normalized_source_text(chunk.get("text")):
                return {
                    "filename": filename,
                    "page": chunk.get("page"),
                    "surface": expansion,
                    "evidence_span": expansion,
                    "status": "direct_span",
                    "grounding_rule": "verified_acronym_long_form",
                    "acronym_surface": acronym,
                }
    return None


def _verified_direct_surface(
    concept: Mapping[str, Any], surface: str,
) -> str:
    """Return an exact verified term that is sufficient as its own evidence span."""
    surface_key = normalize_concept_term(surface)
    if not surface_key:
        return ""
    canonical = str(concept.get("name") or "").strip()
    if surface_key == normalize_concept_term(canonical) and len(surface_key) >= 3:
        return canonical
    for preferred in _list_values(concept.get("verified_preferred_terms")):
        if (
            surface_key == normalize_concept_term(preferred)
            and len(re.findall(r"[A-Za-z]+", preferred)) >= 2
        ):
            return preferred
    return ""


def _surface_matches_concept_term(concept: Mapping[str, Any], surface: str) -> bool:
    surface_key = normalize_concept_term(surface)
    terms = [
        concept.get("name"), concept.get("keyword"),
        *_list_values(concept.get("aliases")),
        *_list_values(concept.get("verified_preferred_terms")),
    ]
    return bool(surface_key) and surface_key in {
        normalize_concept_term(value) for value in terms if normalize_concept_term(value)
    }


def _canonical_name_in_file_bridge(
    concept: Mapping[str, Any], filename: str, surface: str,
    chunks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any] | None:
    canonical = str(concept.get("name") or "").strip()
    canonical_key = _normalized_source_text(canonical)
    if (
        len(normalize_concept_term(canonical)) < 3
        or not _surface_matches_concept_term(concept, surface)
    ):
        return None
    for chunk in chunks:
        if (
            unicodedata.normalize("NFKC", str(chunk.get("filename") or ""))
            != unicodedata.normalize("NFKC", filename)
        ):
            continue
        if canonical_key in _normalized_source_text(chunk.get("text")):
            return {
                "filename": filename,
                "page": chunk.get("page"),
                "surface": canonical,
                "evidence_span": canonical,
                "status": "direct_span",
                "grounding_rule": "canonical_name_in_same_file",
                "source_surface": surface,
            }
    return None


def validate_model_evidence(
    concept: Mapping[str, Any],
    chunks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Validate model evidence against the exact file/page source text."""
    raw_items = concept.get("model_evidence")
    evidence_items = raw_items if isinstance(raw_items, list) else []
    grounded, rejected = [], []
    for evidence in evidence_items:
        if not isinstance(evidence, Mapping):
            continue
        filename = str(evidence.get("filename") or "").strip()
        surface = str(evidence.get("surface") or "").strip()
        span = str(evidence.get("evidence_span") or "").strip()
        try:
            page = int(evidence.get("page")) if evidence.get("page") is not None else None
        except (TypeError, ValueError):
            page = None
        source = next((
            chunk for chunk in chunks
            if unicodedata.normalize("NFKC", str(chunk.get("filename") or ""))
            == unicodedata.normalize("NFKC", filename)
            and int(chunk.get("page") or 0) == int(page or 0)
        ), None)
        if source is None:
            rejected.append({"evidence": dict(evidence), "reason": "source_page_not_found"})
            continue
        source_text = _normalized_source_text(source.get("text"))
        if not surface or _normalized_source_text(surface) not in source_text:
            rejected.append({"evidence": dict(evidence), "reason": "surface_not_found"})
            continue
        span_matched = bool(span and _normalized_source_text(span) in source_text)
        if not span_matched:
            direct_surface = _verified_direct_surface(concept, surface)
            if direct_surface:
                grounded.append({
                    "filename": filename,
                    "page": page,
                    "surface": surface,
                    "evidence_span": direct_surface,
                    "status": "direct_span",
                    "grounding_rule": "verified_direct_surface",
                })
                continue
            canonical = str(concept.get("name") or "").strip()
            if (
                len(normalize_concept_term(canonical)) >= 3
                and _surface_matches_concept_term(concept, surface)
                and _normalized_source_text(canonical) in source_text
            ):
                grounded.append({
                    "filename": filename,
                    "page": page,
                    "surface": surface,
                    "evidence_span": canonical,
                    "status": "direct_span",
                    "grounding_rule": "canonical_name_on_source_page",
                })
                continue
            canonical_bridge = _canonical_name_in_file_bridge(
                concept, filename, surface, chunks
            )
            if canonical_bridge:
                grounded.append(canonical_bridge)
                continue
            bridged = _verified_acronym_bridge(concept, filename, surface, chunks)
            if bridged:
                grounded.append(bridged)
                continue
        grounded.append({
            "filename": filename,
            "page": page,
            "surface": surface,
            "evidence_span": span if span_matched else "",
            "status": "direct_span" if span_matched else "surface_only",
        })
    return {"grounded": grounded, "rejected": rejected}


def _term_fragments(value: Any) -> List[str]:
    values = value if isinstance(value, list) else [value]
    fragments: List[str] = []
    for raw_value in values:
        text = str(raw_value or "").strip()
        if not text:
            continue
        for fragment in [text, *re.split(r"[,;/]", text)]:
            clean = fragment.strip()
            if clean and clean not in fragments:
                fragments.append(clean)
    return fragments


def _concept_term_set(item: Mapping[str, Any]) -> set[str]:
    """Trusted merge keys: aliases from a model must not bridge concepts."""
    values = [str(item.get("name") or "").strip(), *_term_fragments(item.get("keyword"))]
    return {normalize_concept_term(value) for value in values if normalize_concept_term(value)}


def _existing_concept_terms(item: Mapping[str, Any]) -> set[str]:
    values = [
        *_term_fragments(item.get("name")),
        *_term_fragments(item.get("keyword")),
        *_term_fragments(item.get("aliases")),
        *_term_fragments(item.get("synonyms")),
    ]
    return {normalize_concept_term(value) for value in values if normalize_concept_term(value)}


def _canonical_name_keys(value: Any) -> set[str]:
    """Return conservative name-only variants safe for canonical reuse.

    These variants intentionally apply only to concept names, not keywords or
    model-proposed aliases.  This lets a proposal such as ``혈우병 A형`` reuse
    ``혈우병`` and ``파종성 혈관내 응고`` reuse ``...응고증`` without allowing
    broad substring matches such as CML -> 백혈병.
    """
    normalized = normalize_concept_term(value)
    if not normalized:
        return set()
    keys = {normalized}
    if len(normalized) >= 4 and normalized.endswith("증"):
        keys.add(normalized[:-1])
    subtype_base = re.sub(r"(?:a|b|c)형$", "", normalized)
    if len(subtype_base) >= 3:
        keys.add(subtype_base)
    bare_hemophilia_subtype = re.fullmatch(r"(혈우병)(?:a|b|c)", normalized)
    if bare_hemophilia_subtype:
        keys.add(bare_hemophilia_subtype.group(1))
    return keys


def _bounded_edit_distance(left: str, right: str, limit: int = 2) -> int:
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row_index, left_char in enumerate(left, start=1):
        current = [row_index]
        for column_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column_index] + 1,
                previous[column_index - 1] + (left_char != right_char),
            ))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _keyword_quality_issue(name: Any, keyword: Any) -> str:
    """Classify only high-confidence OCR damage patterns."""
    raw_name = unicodedata.normalize("NFKC", str(name or "")).strip()
    raw_keyword = unicodedata.normalize("NFKC", str(keyword or "")).strip()
    name_key = normalize_concept_term(raw_name)
    keyword_key = normalize_concept_term(raw_keyword)
    if not keyword_key:
        return "missing_keyword"
    has_korean = bool(re.search(r"[가-힣]", raw_keyword))
    has_latin = bool(re.search(r"[A-Za-z]", raw_keyword))
    if has_korean and has_latin:
        return "mixed_script_ocr"
    if re.fullmatch(r"[A-Za-z]{1,2}", raw_keyword):
        return "ambiguous_short_keyword"
    if (
        re.fullmatch(r".{3,}의.{2,}의(?:감소|증가|저하|파괴|변화)", keyword_key)
        or re.fullmatch(r".{3,}(?:있는|없는|하는|되는)상태", keyword_key)
        or re.fullmatch(r".{3,}(?:하는|되는)과정", keyword_key)
    ):
        return "explanatory_keyword"
    if (
        name_key and keyword_key != name_key
        and re.fullmatch(r"[가-힣]+", name_key)
        and re.fullmatch(r"[가-힣]+", keyword_key)
    ):
        if name_key.endswith(keyword_key) and 1 <= len(name_key) - len(keyword_key) <= 2:
            return "truncated_name_surface"
        if (
            keyword_key not in name_key and name_key not in keyword_key
            and min(len(name_key), len(keyword_key)) >= 4
            and _bounded_edit_distance(name_key, keyword_key) <= 2
        ):
            return "near_name_ocr_mutation"
    return ""


def _is_generic_concept_name(value: Any) -> bool:
    return normalize_concept_term(value) in _GENERIC_CONCEPT_NAMES


def _is_explanatory_concept_name(value: Any) -> bool:
    normalized = normalize_concept_term(value)
    return bool(
        re.fullmatch(r".{3,}의(?:감소|증가|저하|파괴|변화)", normalized)
        or re.fullmatch(r".{3,}(?:있는|없는|하는|되는)상태", normalized)
        or re.fullmatch(r".{3,}(?:하는|되는)과정", normalized)
    )


def _is_short_ocr_fragment_name(value: Any) -> bool:
    normalized = normalize_concept_term(value)
    return bool(re.fullmatch(r"[가-힣]검사", normalized))


def _is_narrower_variant(base_values: Iterable[Any], alias: Any) -> bool:
    alias_key = normalize_concept_term(alias)
    if not alias_key:
        return False
    for base_value in base_values:
        base_key = normalize_concept_term(base_value)
        if len(base_key) < 3 or alias_key == base_key:
            continue
        extra = ""
        if alias_key.endswith(base_key):
            extra = alias_key[:-len(base_key)]
        elif alias_key.startswith(base_key):
            extra = alias_key[len(base_key):]
        if not extra or extra in {"증"}:
            continue
        if len(extra) >= 2 or re.fullmatch(r"[abc](?:형)?", extra):
            return True
    return False


def _is_related_descriptor(name: Any, keyword: Any, alias: Any) -> bool:
    """Return true for a mechanism/deficiency phrase, not an exact synonym."""
    alias_key = normalize_concept_term(alias)
    if not alias_key or not re.search(r"(?:결핍|부족|deficien)", alias_key):
        return False
    base_keys = {
        normalize_concept_term(value) for value in (name, keyword)
        if normalize_concept_term(value)
    }
    return not any(re.search(r"(?:결핍|부족|deficien)", key) for key in base_keys)


def _source_backed_terms(
    item: Mapping[str, Any], chunks: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Find proposed names/aliases literally present on the grounded pages."""
    source_keys = {
        (unicodedata.normalize("NFKC", str(entry.get("filename") or "")), int(entry.get("page") or 0))
        for entry in item.get("occurrences", [])
    }
    values = [
        item.get("name"), item.get("keyword"), *_list_values(item.get("aliases")),
        *_list_values(item.get("verified_preferred_terms")),
    ]
    found, seen = [], set()
    for value in values:
        clean = str(value or "").strip()
        term_key = normalize_concept_term(clean)
        if not clean or not term_key or term_key in seen:
            continue
        for chunk in chunks:
            source_key = (
                unicodedata.normalize("NFKC", str(chunk.get("filename") or "")),
                int(chunk.get("page") or 0),
            )
            if source_key not in source_keys:
                continue
            if _normalized_source_text(clean) in _normalized_source_text(chunk.get("text")):
                seen.add(term_key)
                found.append({
                    "value": clean,
                    "filename": chunk.get("filename"),
                    "page": chunk.get("page"),
                })
                break
    return found


def _best_clean_grounded_term(item: Mapping[str, Any]) -> Dict[str, Any] | None:
    current_key = normalize_concept_term(item.get("keyword"))
    name_key = normalize_concept_term(item.get("name"))
    verified_keys = {
        normalize_concept_term(value) for value in _list_values(item.get("verified_preferred_terms"))
        if normalize_concept_term(value)
    }
    candidates = []
    for entry in item.get("source_backed_terms", []):
        value = str(entry.get("value") or "").strip()
        value_key = normalize_concept_term(value)
        if not value_key or value_key == current_key or _keyword_quality_issue(item.get("name"), value):
            continue
        latin_words = re.findall(r"[A-Za-z]+", value)
        if value_key == name_key:
            rank = 0
        elif value_key in verified_keys and latin_words:
            rank = 1
        elif len(latin_words) >= 2 and _plausible_parenthetical_english_term(value):
            rank = 2
        else:
            continue
        candidates.append({"rank": rank, "length": len(value_key), "entry": dict(entry)})
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: (candidate["rank"], -candidate["length"]))
    return candidates[0]["entry"]


def reconcile_with_existing_concepts(
    concepts: Sequence[Mapping[str, Any]],
    existing_concepts: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Reuse stable names and remove aliases/evidence owned by another concept."""
    owners: Dict[str, set[int]] = {}
    name_variant_owners: Dict[str, set[int]] = {}
    for index, existing in enumerate(existing_concepts):
        for term in _existing_concept_terms(existing):
            owners.setdefault(term, set()).add(index)
        for term in _canonical_name_keys(existing.get("name")):
            name_variant_owners.setdefault(term, set()).add(index)

    accepted, rejected = [], []
    for concept in concepts:
        item = dict(concept)
        _apply_standard_term_override(item)
        anchor_terms = _concept_term_set(item)
        anchor_owners = set().union(*(owners.get(term, set()) for term in anchor_terms)) if anchor_terms else set()
        matched_index = next(iter(anchor_owners)) if len(anchor_owners) == 1 else None
        canonical_match_rule = "exact_term" if matched_index is not None else ""
        if matched_index is None:
            proposed_name_keys = _canonical_name_keys(item.get("name"))
            variant_owners = (
                set().union(*(name_variant_owners.get(term, set()) for term in proposed_name_keys))
                if proposed_name_keys else set()
            )
            if len(variant_owners) == 1:
                matched_index = next(iter(variant_owners))
                canonical_match_rule = "name_variant"

        original_name = str(item.get("name") or "").strip()
        if matched_index is None and _is_generic_concept_name(original_name):
            rejected.append({
                "concept": original_name,
                "reasons": ["generic_heading_not_concept"],
            })
            continue
        if matched_index is None and _is_explanatory_concept_name(original_name):
            rejected.append({
                "concept": original_name,
                "reasons": ["explanatory_phrase_requires_review"],
            })
            continue
        if matched_index is None and _is_short_ocr_fragment_name(original_name):
            rejected.append({
                "concept": original_name,
                "reasons": ["ocr_fragment_name_requires_review"],
            })
            continue

        keyword_issue = _keyword_quality_issue(original_name, item.get("keyword"))
        if matched_index is None and keyword_issue == "truncated_name_surface":
            rejected.append({
                "concept": original_name,
                "reasons": ["truncated_keyword_requires_review"],
            })
            continue
        if keyword_issue:
            replacement = _best_clean_grounded_term(item)
            if replacement:
                old_keyword = str(item.get("keyword") or "").strip()
                item["keyword"] = replacement["value"]
                item["ocr_keyword_replaced"] = old_keyword
                item.setdefault("occurrences", []).append({
                    "filename": replacement.get("filename"),
                    "page": replacement.get("page"),
                    "surface": replacement["value"],
                    "evidence_span": replacement["value"],
                    "status": "direct_span",
                })
            elif matched_index is not None:
                existing_keyword = str(existing_concepts[matched_index].get("keyword") or "").strip()
                if existing_keyword:
                    item["ocr_keyword_replaced"] = str(item.get("keyword") or "").strip()
                    item["keyword"] = existing_keyword
                    item["keyword_restored_from_existing"] = True
            elif matched_index is None:
                rejected.append({
                    "concept": original_name,
                    "reasons": [
                        "ambiguous_short_keyword_requires_review"
                        if keyword_issue == "ambiguous_short_keyword"
                        else (
                            "explanatory_keyword_requires_review"
                            if keyword_issue == "explanatory_keyword"
                            else "ocr_keyword_without_clean_grounding"
                        )
                    ],
                })
                continue

        removed_evidence, conflict_surface_terms = [], set()
        kept_evidence = []
        for evidence in item.get("occurrences", []):
            surface_terms = {
                normalize_concept_term(value)
                for value in _term_fragments(evidence.get("surface"))
                if normalize_concept_term(value)
            }
            evidence_owners = set().union(*(owners.get(term, set()) for term in surface_terms)) if surface_terms else set()
            conflicting_owners = evidence_owners - ({matched_index} if matched_index is not None else set())
            if conflicting_owners:
                removed_evidence.append(dict(evidence))
                conflict_surface_terms.update(surface_terms)
            else:
                kept_evidence.append(dict(evidence))

        removed_aliases, kept_aliases = [], []
        for alias in _list_values(item.get("aliases")):
            alias_terms = {
                normalize_concept_term(value)
                for value in _term_fragments(alias)
                if normalize_concept_term(value)
            }
            alias_owners = set().union(*(owners.get(term, set()) for term in alias_terms)) if alias_terms else set()
            conflicting_owners = alias_owners - ({matched_index} if matched_index is not None else set())
            if conflicting_owners or alias_terms & conflict_surface_terms:
                removed_aliases.append(alias)
            else:
                kept_aliases.append(alias)

        if item.get("occurrences") and not kept_evidence:
            rejected.append({
                "concept": str(item.get("name") or item.get("keyword") or ""),
                "reasons": ["canonical_conflict_removed_all_evidence"],
            })
            continue

        if matched_index is not None:
            existing = existing_concepts[matched_index]
            item["name"] = str(existing.get("name") or existing.get("keyword") or original_name).strip()
            item["weight"] = max(int(item.get("weight") or 3), int(existing.get("weight") or 3))
            existing_aliases = _list_values(existing.get("aliases"))
            if original_name and normalize_concept_term(original_name) != normalize_concept_term(item["name"]):
                kept_aliases.insert(0, original_name)
            kept_aliases = [*existing_aliases, *kept_aliases]
            item["canonical_status"] = "reused_existing"
            item["canonical_match_rule"] = canonical_match_rule
        else:
            item["canonical_status"] = "new"

        exact_aliases, moved_variants, moved_descriptors = [], [], []
        canonical_keys = {
            normalize_concept_term(item.get("name")),
            normalize_concept_term(item.get("keyword")),
        }
        unique_aliases = list(dict.fromkeys(kept_aliases))
        base_values = [item.get("name"), item.get("keyword"), *unique_aliases]
        for alias in unique_aliases:
            if normalize_concept_term(alias) in canonical_keys:
                continue
            if _is_narrower_variant(base_values, alias):
                moved_variants.append(alias)
            elif _is_related_descriptor(item.get("name"), item.get("keyword"), alias):
                moved_descriptors.append(alias)
            else:
                exact_aliases.append(alias)
        existing_variants = _list_values(item.get("related_variants"))
        existing_descriptors = _list_values(item.get("related_descriptors"))
        item["aliases"] = exact_aliases[:20]
        item["related_variants"] = list(dict.fromkeys([*existing_variants, *moved_variants]))[:20]
        item["related_descriptors"] = list(dict.fromkeys(
            [*existing_descriptors, *moved_descriptors]
        ))[:20]
        if moved_variants:
            item["alias_relations_reclassified"] = moved_variants
        if moved_descriptors:
            item["alias_descriptors_reclassified"] = moved_descriptors
        item["occurrences"] = kept_evidence
        item["pages"] = sorted({entry.get("page") for entry in kept_evidence if entry.get("page") is not None})
        item["page"] = item["pages"][0] if item["pages"] else None
        item["filename"] = next((entry.get("filename") for entry in kept_evidence if entry.get("filename")), None)
        item["evidence_status"] = (
            "direct_span" if any(entry.get("status") == "direct_span" for entry in kept_evidence)
            else "surface_only"
        )
        item.pop("source_backed_terms", None)
        item.pop("verified_preferred_terms", None)
        if matched_index is None and item["evidence_status"] != "direct_span":
            rejected.append({
                "concept": str(item.get("name") or item.get("keyword") or ""),
                "reasons": ["new_surface_only_requires_review"],
            })
            continue
        if removed_aliases or removed_evidence:
            item["canonical_conflicts_removed"] = {
                "aliases": removed_aliases,
                "evidence": removed_evidence,
            }
        accepted.append(item)

    accepted.sort(key=lambda item: (-int(item.get("weight") or 3), str(item.get("name") or "")))
    return {"accepted": accepted, "rejected": rejected}


def _merge_grounded_group(items: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    korean_names = [
        str(item.get("name") or "").strip() for item in items
        if _KOREAN_ONLY_RE.fullmatch(str(item.get("name") or "").strip())
    ]
    name = korean_names[0] if korean_names else str(items[0].get("name") or "").strip()
    evidence = []
    for item in items:
        for entry in item.get("grounded_evidence", []):
            key = (entry.get("filename"), entry.get("page"), normalize_concept_term(entry.get("surface")))
            if key not in {
                (x.get("filename"), x.get("page"), normalize_concept_term(x.get("surface")))
                for x in evidence
            }:
                evidence.append(dict(entry))
    surfaces = {normalize_concept_term(item.get("surface")): item.get("surface") for item in evidence}
    keyword = next((
        str(item.get("keyword") or "").strip() for item in items
        if normalize_concept_term(item.get("keyword")) in surfaces
    ), str(items[0].get("keyword") or "").strip())
    alias_values = []
    for item in items:
        alias_values.extend([
            str(item.get("name") or "").strip(),
            str(item.get("keyword") or "").strip(),
            *_list_values(item.get("aliases")),
            *_list_values(item.get("synonyms")),
        ])
    excluded = {normalize_concept_term(name), normalize_concept_term(keyword)}
    aliases, alias_keys = [], set()
    for value in alias_values:
        key = normalize_concept_term(value)
        if value and key and key not in excluded and key not in alias_keys:
            alias_keys.add(key)
            aliases.append(value)
    links = []
    for item in items:
        for link in item.get("links", []):
            value = str(link or "").strip()
            if value and value not in links:
                links.append(value)
    pages = sorted({entry.get("page") for entry in evidence if entry.get("page") is not None})
    filenames = list(dict.fromkeys(str(entry.get("filename") or "") for entry in evidence if entry.get("filename")))
    verified_preferred_terms = list(dict.fromkeys(
        value
        for item in items
        for value in _list_values(item.get("verified_preferred_terms"))
    ))
    term_override_sources = list(dict.fromkeys(
        value
        for item in items
        for value in _list_values(item.get("term_override_sources"))
    ))
    merged = {
        "name": name,
        "keyword": keyword,
        "aliases": aliases[:20],
        "weight": max(int(item.get("weight") or 3) for item in items),
        "page": pages[0] if pages else None,
        "filename": filenames[0] if filenames else None,
        "pages": pages,
        "occurrences": evidence,
        "links": links,
        "origin": "llm_grounded",
        "evidence_status": "direct_span" if any(x.get("status") == "direct_span" for x in evidence) else "surface_only",
    }
    if verified_preferred_terms:
        merged["verified_preferred_terms"] = verified_preferred_terms
        merged["term_override_rule"] = "verified_dictionary"
        merged["term_override_sources"] = term_override_sources
    return merged


def canonicalize_grounded_concepts(
    concepts: Sequence[Mapping[str, Any]],
    chunks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Reject ungrounded concepts and merge bilingual/alias-overlapping items."""
    grounded_items, rejected = [], []
    for concept in concepts:
        prepared = dict(concept)
        _apply_standard_term_override(prepared)
        validation = validate_model_evidence(prepared, chunks)
        if not validation["grounded"]:
            reasons = sorted({item["reason"] for item in validation["rejected"]}) or ["missing_evidence"]
            rejected.append({
                "concept": str(prepared.get("name") or prepared.get("keyword") or ""),
                "reasons": reasons,
            })
            continue
        grounded_items.append({**prepared, "grounded_evidence": validation["grounded"]})

    groups: List[List[Dict[str, Any]]] = []
    for item in grounded_items:
        terms = _concept_term_set(item)
        matching = [index for index, group in enumerate(groups) if terms & set().union(*(_concept_term_set(x) for x in group))]
        if not matching:
            groups.append([dict(item)])
            continue
        base = matching[0]
        groups[base].append(dict(item))
        for index in reversed(matching[1:]):
            groups[base].extend(groups.pop(index))

    accepted = [_merge_grounded_group(group) for group in groups]
    for item in accepted:
        item["source_backed_terms"] = _source_backed_terms(item, chunks)
    accepted.sort(key=lambda item: (-item["weight"], item["name"]))
    return {"accepted": accepted, "rejected": rejected}
