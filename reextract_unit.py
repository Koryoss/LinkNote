"""Re-extract one unit from its source PDF and atomically refresh its concept graph.

This command deliberately does not replace document chunks in ChromaDB. It reads
the original PDF with the current native/OCR selection pipeline, validates every
concept against that text, replaces only the requested unit in concepts.json,
and incrementally rebuilds graph nodes and links for the same scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

import rag
from concept_quality import build_concept_coverage_report
from pdf_loader import extract_pdf_text


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_digest(chunks: list[dict]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(str(chunk.get("filename") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(chunk.get("page") or "").encode("ascii"))
        digest.update(b"\0")
        digest.update(str(chunk.get("text") or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _backup(paths: list[Path], backup_dir: Path) -> dict[str, str]:
    backup_dir.mkdir(parents=True, exist_ok=False)
    copies = {}
    for path in paths:
        if not path.exists():
            continue
        destination = backup_dir / path.name
        shutil.copy2(path, destination)
        copies[str(path)] = str(destination)
    return copies


def _restore(copies: dict[str, str]) -> None:
    for destination, backup in copies.items():
        shutil.copy2(backup, destination)


def _fresh_chunks(pdf_path: Path, filename: str) -> tuple[list[dict], list[dict]]:
    pages = extract_pdf_text(str(pdf_path), prefer_korean=True)
    chunks = []
    for page in pages:
        for chunk_index, text in enumerate(rag.chunk_text(page.get("text") or "")):
            chunks.append({
                "filename": filename,
                "page": page.get("page"),
                "chunk_index": chunk_index,
                "text": text,
            })
    return pages, chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--semester", required=True)
    parser.add_argument("--course", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--segment-size", type=int, default=4500)
    parser.add_argument("--min-candidate-coverage", type=float, default=0.45)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    data_dir = Path(rag.DATA_DIR)
    concepts_path = data_dir / "concepts.json"
    index_path = data_dir / "concept_index.json"
    links_path = data_dir / "concept_links.json"
    profiles_path = data_dir / "search_profiles.json"
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    concepts_data = _read_json(concepts_path, {})
    existing = (
        concepts_data.get(args.user, {})
        .get(args.semester, {})
        .get(args.course, {})
        .get(args.unit, [])
    )
    if not isinstance(existing, list):
        raise RuntimeError("기존 단원 개념 데이터가 목록 형식이 아닙니다.")
    profiles = _read_json(profiles_path, {})
    profile = profiles.get(args.user, {}) if isinstance(profiles, dict) else {}
    saved_aliases = profile.get("aliases", {}) if isinstance(profile, dict) else {}

    pages, chunks = _fresh_chunks(args.pdf, args.filename)
    if not chunks:
        raise RuntimeError("원본 PDF에서 재추출할 텍스트를 찾지 못했습니다.")
    pre_coverage = build_concept_coverage_report(chunks, existing, saved_aliases)
    result = rag.shadow_extract_concepts_from_chunks(
        chunks,
        pre_coverage.get("missing", []),
        args.unit,
        seg_size=max(2000, args.segment_size),
        existing_concepts=existing,
    )
    if result.get("errors"):
        raise RuntimeError(f"MAP 추출 실패: {result['errors']}")
    extracted = result.get("concepts", [])
    if not extracted:
        raise RuntimeError("검증을 통과한 개념이 0개라 기존 데이터를 유지합니다.")
    if existing and len(extracted) < max(3, len(existing) // 2):
        raise RuntimeError(
            f"새 개념 수가 비정상적으로 적습니다: 기존 {len(existing)}개, 신규 {len(extracted)}개"
        )

    extracted_at = datetime.now(timezone.utc).isoformat()
    source_digest = _source_digest(chunks)
    for concept in extracted:
        concept["extraction_version"] = "concept_quality_v1"
        concept["source_digest"] = source_digest
        concept["extracted_at"] = extracted_at
    post_coverage = build_concept_coverage_report(chunks, extracted, saved_aliases)
    candidate_count = int(post_coverage.get("candidate_count") or 0)
    covered_count = int(post_coverage.get("covered_count") or 0)
    candidate_coverage = covered_count / max(1, candidate_count)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = reports_dir / f"reextract-{args.semester}-{args.course}-{args.unit}-{timestamp}.json"
    report = {
        "scope": {
            "user": args.user,
            "semester": args.semester,
            "course": args.course,
            "unit": args.unit,
            "filename": args.filename,
        },
        "source": {
            "pdf": str(args.pdf),
            "pdf_sha256": _sha256(args.pdf),
            "source_digest": source_digest,
            "pages": len(pages),
            "chunks": len(chunks),
            "ocr_pages": sum(1 for page in pages if str(page.get("text_extraction_method") or "").startswith("ocr_")),
            "fallback_pages": sum(1 for page in pages if page.get("text_ocr_fallback_used")),
        },
        "extraction": {
            "existing_count": len(existing),
            "new_count": len(extracted),
            "map_calls": result.get("map_calls", 0),
            "group_calls": result.get("group_calls", 0),
            "rejected": result.get("rejected", []),
            "pre_coverage": pre_coverage,
            "post_coverage": post_coverage,
            "candidate_coverage": round(candidate_coverage, 4),
            "concepts": extracted,
        },
        "committed": False,
        "graph": None,
    }
    rag._write_json_atomic(str(report_path), report)

    if not args.commit:
        print(json.dumps({"report": str(report_path), **report["source"], **report["extraction"]}, ensure_ascii=False))
        return 0

    if candidate_coverage < args.min_candidate_coverage:
        raise RuntimeError(
            "후보 coverage가 저장 기준보다 낮아 기존 데이터를 유지합니다: "
            f"{candidate_coverage:.1%} < {args.min_candidate_coverage:.1%}"
        )

    backup_dir = data_dir / "backups" / f"reextract-{args.unit}-{timestamp}"
    copies = _backup([concepts_path, index_path, links_path], backup_dir)
    try:
        unit_data = (
            concepts_data.setdefault(args.user, {})
            .setdefault(args.semester, {})
            .setdefault(args.course, {})
        )
        unit_data[args.unit] = extracted
        rag._write_json_atomic(str(concepts_path), concepts_data)
        embedding = rag.build_concept_embeddings_for_scope(
            args.user, args.semester, args.course, args.unit
        )
        links = rag.build_cross_links_for_scope(
            args.user, args.semester, args.course, args.unit,
            threshold=0.45, top_k=5,
        )
    except Exception:
        _restore(copies)
        raise

    report["committed"] = True
    report["backup_dir"] = str(backup_dir)
    report["graph"] = {
        "replaced_concepts": embedding["replaced_count"],
        "new_concepts": len(embedding["target_nodes"]),
        "total_concepts": embedding["total_count"],
        "removed_cross_edges": links["removed_edge_count"],
        "new_cross_edges": len(links["new_edges"]),
        "total_cross_edges": links["total_edge_count"],
        "llm_calls": links["llm_calls"],
    }
    rag._write_json_atomic(str(report_path), report)
    print(json.dumps({
        "ok": True,
        "report": str(report_path),
        "backup_dir": str(backup_dir),
        **report["source"],
        "existing_count": len(existing),
        "new_count": len(extracted),
        "post_candidate_count": post_coverage.get("candidate_count", 0),
        "post_covered_count": post_coverage.get("covered_count", 0),
        "post_missing_count": post_coverage.get("missing_count", 0),
        "graph": report["graph"],
        "concept_names": [concept.get("name") for concept in extracted],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
