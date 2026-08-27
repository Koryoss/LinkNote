"""Commit an already-reviewed re-extraction report without another LLM call."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import rag
from concept_quality import _apply_standard_term_override, normalize_concept_term


def _read(path: Path):
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _merge_reviewed_concepts(concepts: list[dict], exclusions: set[str]) -> list[dict]:
    merged: dict[str, dict] = {}
    for raw in concepts:
        item = dict(raw)
        if str(item.get("name") or "").strip() in exclusions:
            continue
        original_name = str(item.get("name") or "").strip()
        original_keyword = str(item.get("keyword") or "").strip()
        _apply_standard_term_override(item)
        key = normalize_concept_term(item.get("name"))
        if not key:
            continue
        aliases = [
            *item.get("aliases", []),
            original_name,
            original_keyword,
        ]
        item["aliases"] = list(dict.fromkeys(
            value for value in aliases
            if value and normalize_concept_term(value) not in {
                normalize_concept_term(item.get("name")),
                normalize_concept_term(item.get("keyword")),
            }
        ))
        if key not in merged:
            merged[key] = item
            continue
        target = merged[key]
        target["weight"] = max(int(target.get("weight") or 3), int(item.get("weight") or 3))
        target["aliases"] = list(dict.fromkeys([
            *target.get("aliases", []),
            str(item.get("keyword") or "").strip(),
            *item.get("aliases", []),
        ]))
        target["links"] = list(dict.fromkeys([*target.get("links", []), *item.get("links", [])]))
        seen = {
            (entry.get("filename"), entry.get("page"), normalize_concept_term(entry.get("surface")))
            for entry in target.get("occurrences", [])
        }
        for occurrence in item.get("occurrences", []):
            occurrence_key = (
                occurrence.get("filename"),
                occurrence.get("page"),
                normalize_concept_term(occurrence.get("surface")),
            )
            if occurrence_key not in seen:
                target.setdefault("occurrences", []).append(occurrence)
                seen.add(occurrence_key)
        target["occurrences"].sort(key=lambda value: (
            str(value.get("filename") or ""), int(value.get("page") or 0)
        ))
        target["pages"] = sorted({
            int(entry.get("page")) for entry in target["occurrences"] if entry.get("page")
        })
        if target["pages"]:
            target["page"] = target["pages"][0]
    return sorted(merged.values(), key=lambda item: (
        -int(item.get("weight") or 3), str(item.get("name") or "")
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()

    report = _read(args.report)
    scope = report.get("scope", {})
    extraction = report.get("extraction", {})
    coverage = float(extraction.get("candidate_coverage") or 0)
    if coverage < 0.45:
        raise RuntimeError(f"후보 coverage가 저장 기준보다 낮습니다: {coverage:.1%}")
    concepts = _merge_reviewed_concepts(
        extraction.get("concepts", []), set(args.exclude)
    )
    if not concepts:
        raise RuntimeError("저장할 검증 개념이 없습니다.")

    data_dir = Path(rag.DATA_DIR)
    concepts_path = data_dir / "concepts.json"
    index_path = data_dir / "concept_index.json"
    links_path = data_dir / "concept_links.json"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = data_dir / "backups" / f"commit-reextract-{scope['unit']}-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    copies = {}
    for path in (concepts_path, index_path, links_path):
        if path.exists():
            backup = backup_dir / path.name
            shutil.copy2(path, backup)
            copies[path] = backup

    try:
        concepts_data = _read(concepts_path)
        concepts_data.setdefault(scope["user"], {}).setdefault(
            scope["semester"], {}
        ).setdefault(scope["course"], {})[scope["unit"]] = concepts
        rag._write_json_atomic(str(concepts_path), concepts_data)
        embedding = rag.build_concept_embeddings_for_scope(
            scope["user"], scope["semester"], scope["course"], scope["unit"]
        )
        links = rag.build_cross_links_for_scope(
            scope["user"], scope["semester"], scope["course"], scope["unit"],
            threshold=0.45, top_k=5,
        )
    except Exception:
        for destination, backup in copies.items():
            shutil.copy2(backup, destination)
        raise

    report["committed"] = True
    report["committed_at"] = datetime.now().astimezone().isoformat()
    report["backup_dir"] = str(backup_dir)
    report["manual_exclusions"] = args.exclude
    report["extraction"]["committed_count"] = len(concepts)
    report["extraction"]["concepts"] = concepts
    report["graph"] = {
        "replaced_concepts": embedding["replaced_count"],
        "new_concepts": len(embedding["target_nodes"]),
        "total_concepts": embedding["total_count"],
        "removed_cross_edges": links["removed_edge_count"],
        "new_cross_edges": len(links["new_edges"]),
        "total_cross_edges": links["total_edge_count"],
        "llm_calls": links["llm_calls"],
    }
    rag._write_json_atomic(str(args.report), report)
    print(json.dumps({
        "ok": True,
        "report": str(args.report),
        "backup_dir": str(backup_dir),
        "extracted_count": extraction.get("new_count"),
        "committed_count": len(concepts),
        "candidate_coverage": coverage,
        "graph": report["graph"],
        "concept_names": [item.get("name") for item in concepts],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
