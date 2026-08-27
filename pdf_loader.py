import logging
import os
import re
import shutil
import subprocess
import hashlib
from concurrent.futures import ProcessPoolExecutor
from statistics import median

import fitz

from analysis_cache import get_json, set_json


logger = logging.getLogger(__name__)
_KOREAN_RE = re.compile(r"[가-힣]")
_ALNUM_RE = re.compile(r"[0-9A-Za-z가-힣]")
_PAREN_ENGLISH_RE = re.compile(r"\([A-Za-z][A-Za-z -]{1,62}\)")
_NORMAL_PUNCTUATION = set(".,:;!?()[]{}%+-*/=<>·—–_'")
_PROTECTED_NATIVE_PATTERNS = (
    re.compile(r"\([A-Za-z][A-Za-z0-9 +/.,'’-]{1,62}\)"),
    re.compile(r"\b(?=[A-Za-z0-9-]*[A-Z])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b"),
    re.compile(r"\b[A-Z][A-Z0-9]{1,11}\b"),
    re.compile(r"\b\d+(?:\.\d+)?(?:\s*[-~]\s*\d+(?:\.\d+)?)?\s*(?:%|bpm|mg/dL|g/dL|mL|µL|μL)\b", re.IGNORECASE),
)
_SPARSE_HEADING_SUFFIXES = (
    "백혈병", "림프종", "증후군", "빈혈", "검사", "기전", "항체", "인자",
    "장애", "질환", "치료", "치유", "물질", "사정", "과정", "반응", "영향",
    "요인", "분류", "증", "병", "염", "암", "계",
)
_SPARSE_HEADING_SUFFIX_RE = re.compile(
    rf"(?:{'|'.join(map(re.escape, _SPARSE_HEADING_SUFFIXES))})$"
)
_HEADING_REGION_RATIOS = (0.27, 0.55)
_PDF_CACHE_VERSION = "pdf-text-v4-fallback-layout"
_TESSDATA_CANDIDATES = (
    "/opt/homebrew/share/tessdata",
    "/usr/local/share/tessdata",
    "/usr/share/tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
)
_TESSERACT_CANDIDATES = (
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/usr/bin/tesseract",
)


def _normalized_token(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").lower())


def merge_ocr_with_native_terms(ocr_text: str, native_text: str) -> dict:
    """Keep OCR body text while retaining high-signal English terms and measurements."""
    ocr = str(ocr_text or "").strip()
    native = str(native_text or "").strip()
    if not ocr or not native:
        return {"text": ocr, "restored_terms": []}

    ocr_normalized = _normalized_token(ocr)
    restored = []
    seen = set()
    for pattern in _PROTECTED_NATIVE_PATTERNS:
        for match in pattern.finditer(native):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            key = _normalized_token(value)
            if len(key) < 2 or key in seen or key in ocr_normalized:
                continue
            seen.add(key)
            restored.append(value)

    if not restored:
        return {"text": ocr, "restored_terms": []}
    return {
        "text": f"{ocr}\n\n[원문 영문·수치] {' · '.join(restored)}",
        "restored_terms": restored,
    }


def _plausible_heading_term(raw_line: str) -> str:
    value = re.sub(r"\s+", " ", str(raw_line or "")).strip(" -:;·")
    before_parenthesis = re.split(r"[（(]", value, maxsplit=1)[0]
    compact = "".join(_KOREAN_RE.findall(before_parenthesis))
    if (
        3 <= len(compact) <= 24
        and len(re.findall(r"[A-Za-z]", before_parenthesis)) <= 1
        and _SPARSE_HEADING_SUFFIX_RE.search(compact)
    ):
        return compact
    return ""


def _heading_suffix_score(value: str) -> int:
    return max(
        (len(suffix) for suffix in _SPARSE_HEADING_SUFFIXES if value.endswith(suffix)),
        default=0,
    )


def _within_one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    short_index = long_index = differences = 0
    while short_index < len(short) and long_index < len(long):
        if short[short_index] == long[long_index]:
            short_index += 1
            long_index += 1
        else:
            differences += 1
            long_index += 1
            if differences > 1:
                return False
    return True


def _needs_sparse_heading_ocr(ocr_text: str) -> bool:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(ocr_text or "").splitlines() if line.strip()]
    if sum(len(_KOREAN_RE.findall(line)) for line in lines) < 20:
        return False
    heading_lines = lines[:4]
    return bool(heading_lines) and not any(_plausible_heading_term(line) for line in heading_lines)


def merge_sparse_heading_terms(ocr_text: str, sparse_text: str) -> dict:
    """Append only plausible Korean heading terms recovered by sparse OCR."""
    primary = str(ocr_text or "").strip()
    primary_key = re.sub(r"[^0-9A-Za-z가-힣]+", "", primary).lower()
    candidates = []
    for index, raw_line in enumerate(str(sparse_text or "").splitlines()[:24]):
        compact = _plausible_heading_term(raw_line)
        key = re.sub(r"[^0-9A-Za-z가-힣]+", "", compact).lower()
        if compact and key not in primary_key:
            candidates.append((_heading_suffix_score(compact), len(compact), -index, compact))

    restored = []
    for _, _, _, compact in sorted(candidates, reverse=True):
        if compact in restored:
            continue
        if any(
            _within_one_edit(compact, existing)
            or compact in existing
            or existing in compact
            for existing in restored
        ):
            continue
        restored.append(compact)
    if not restored:
        return {"text": primary, "restored_terms": []}
    return {
        "text": f"{primary}\n\n[희소 영역 OCR] {' · '.join(restored)}",
        "restored_terms": restored,
    }


def text_quality_metrics(text: str, prefer_korean: bool = False) -> dict:
    """Return explainable text-quality signals used to compare native text and OCR."""
    value = str(text or "")
    compact = re.sub(r"\s+", "", value)
    nonempty_lines = [line.strip() for line in value.splitlines() if line.strip()]
    alnum_count = len(_ALNUM_RE.findall(compact))
    korean_count = len(_KOREAN_RE.findall(compact))
    suspicious_count = sum(
        1 for char in compact
        if not _ALNUM_RE.fullmatch(char) and char not in _NORMAL_PUNCTUATION
    )
    meaningful_lines = sum(1 for line in nonempty_lines if len(_ALNUM_RE.findall(line)) >= 3)
    isolated_lines = sum(1 for line in nonempty_lines if len(_ALNUM_RE.findall(line)) <= 1)
    latin_only_lines = sum(
        1 for line in nonempty_lines
        if len(re.findall(r"[A-Za-z]", line)) >= 3
        and not _KOREAN_RE.search(line)
        and not _PAREN_ENGLISH_RE.search(line)
    )
    readable_ratio = alnum_count / max(1, len(compact))
    korean_ratio = korean_count / max(1, alnum_count)
    noise_ratio = suspicious_count / max(1, len(compact))
    meaningful_line_ratio = meaningful_lines / max(1, len(nonempty_lines))
    isolated_line_ratio = isolated_lines / max(1, len(nonempty_lines))
    latin_only_line_ratio = latin_only_lines / max(1, len(nonempty_lines))
    language_penalty = 35.0 if prefer_korean and korean_count < 4 else 0.0
    score = (
        readable_ratio * 55.0
        + meaningful_line_ratio * 20.0
        + (min(1.0, korean_count / 20.0) * 25.0 if prefer_korean else 10.0)
        - noise_ratio * 25.0
        - isolated_line_ratio * 15.0
        - (latin_only_line_ratio * 25.0 if prefer_korean else 0.0)
        - language_penalty
    )
    return {
        "characters": len(compact),
        "alnum_count": alnum_count,
        "korean_count": korean_count,
        "korean_ratio": round(korean_ratio, 4),
        "noise_ratio": round(noise_ratio, 4),
        "meaningful_line_ratio": round(meaningful_line_ratio, 4),
        "isolated_line_ratio": round(isolated_line_ratio, 4),
        "latin_only_line_ratio": round(latin_only_line_ratio, 4),
        "score": round(max(0.0, min(100.0, score)), 1),
    }


def select_page_text(native_text: str, ocr_text: str = "", prefer_korean: bool = False) -> dict:
    """Select native or OCR text and retain the decision evidence."""
    native = str(native_text or "").strip()
    ocr = str(ocr_text or "").strip()
    native_quality = text_quality_metrics(native, prefer_korean)
    ocr_quality = text_quality_metrics(ocr, prefer_korean) if ocr else None
    native_missing = native_quality["alnum_count"] < 3
    ocr_recovered_text = bool(
        ocr and ocr_quality
        and native_missing
        and ocr_quality["alnum_count"] >= 12
        and ocr_quality["score"] >= 10
    )
    ocr_recovered_korean = bool(
        ocr and ocr_quality and prefer_korean
        and native_quality["korean_count"] < 4
        and ocr_quality["korean_count"] >= 4
        and ocr_quality["score"] >= 25
    )
    ocr_higher_quality = bool(
        ocr and ocr_quality
        and ocr_quality["score"] > native_quality["score"]
        and (
            not prefer_korean
            or ocr_quality["korean_count"] >= max(4, native_quality["korean_count"] + 3)
        )
    )
    use_ocr = ocr_recovered_text or ocr_recovered_korean or ocr_higher_quality
    if use_ocr:
        if ocr_recovered_text:
            reason = "ocr_recovered_missing_text"
        elif ocr_recovered_korean:
            reason = "ocr_recovered_korean"
        else:
            reason = "ocr_improved_korean" if prefer_korean else "ocr_higher_quality"
        merged = merge_ocr_with_native_terms(ocr, native)
        selected_text, selected_quality, method = merged["text"], ocr_quality, "ocr_kor_eng_300dpi"
        restored_terms = merged["restored_terms"]
    else:
        reason = "native_sufficient" if not ocr else "native_preferred_ocr_no_gain"
        selected_text, selected_quality, method = native, native_quality, "native"
        restored_terms = []
    return {
        "text": selected_text,
        "method": method,
        "reason": reason,
        "quality": selected_quality,
        "native_quality": native_quality,
        "ocr_quality": ocr_quality,
        "restored_terms": restored_terms,
    }


def _needs_korean_ocr(text: str, prefer_korean: bool) -> bool:
    if not prefer_korean:
        return False
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    korean_count = len(_KOREAN_RE.findall(compact))
    return korean_count < 4 or korean_count / len(compact) < 0.02


def _valid_tessdata(path: str) -> bool:
    return bool(
        path
        and os.path.isfile(os.path.join(path, "kor.traineddata"))
        and os.path.isfile(os.path.join(path, "eng.traineddata"))
    )


def _resolve_tessdata() -> str:
    candidates = [os.getenv("TESSDATA_PREFIX", "")]
    try:
        candidates.append(fitz.get_tessdata())
    except RuntimeError as exc:
        # Apps launched from Finder do not inherit Homebrew's PATH. PyMuPDF then
        # raises here even when tessdata is installed in a standard Homebrew path.
        logger.info("PyMuPDF could not discover tessdata from PATH: %s", exc)
    candidates.extend(_TESSDATA_CANDIDATES)
    for candidate in candidates:
        if _valid_tessdata(candidate):
            os.environ["TESSDATA_PREFIX"] = candidate
            return candidate
    return ""


def _korean_ocr_available() -> bool:
    return bool(_resolve_tessdata())


def _find_tesseract_executable() -> str:
    executable = shutil.which("tesseract")
    if executable:
        return executable
    return next((path for path in _TESSERACT_CANDIDATES if os.access(path, os.X_OK)), "")


def _ocr_rows_from_words(words, page_width: float) -> list[dict]:
    """Rebuild visual rows and retain large horizontal gaps as table columns."""
    prepared = []
    for word in words or []:
        if len(word) < 5 or not str(word[4] or "").strip():
            continue
        x0, y0, x1, y1 = (float(word[index]) for index in range(4))
        prepared.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "text": str(word[4]).strip(), "center": (y0 + y1) / 2,
            "height": max(1.0, y1 - y0),
        })
    if not prepared:
        return []
    typical_height = median(item["height"] for item in prepared)
    tolerance = max(3.0, typical_height * 0.52)
    rows = []
    for item in sorted(prepared, key=lambda value: (value["center"], value["x0"])):
        target = next((row for row in reversed(rows[-4:]) if abs(row["center"] - item["center"]) <= tolerance), None)
        if target is None:
            target = {"center": item["center"], "words": []}
            rows.append(target)
        target["words"].append(item)
        target["center"] = sum(word["center"] for word in target["words"]) / len(target["words"])

    output = []
    for row in rows:
        row_words = sorted(row["words"], key=lambda value: value["x0"])
        char_widths = [
            (word["x1"] - word["x0"]) / max(1, len(word["text"]))
            for word in row_words
        ]
        typical_char_width = median(char_widths) if char_widths else 4.0
        large_gap = max(18.0, page_width * 0.025, typical_char_width * 3.2)
        parts = []
        previous_x1 = None
        for word in row_words:
            if previous_x1 is not None:
                parts.append(" | " if word["x0"] - previous_x1 >= large_gap else " ")
            parts.append(word["text"])
            previous_x1 = max(previous_x1 or word["x1"], word["x1"])
        output.append({
            "text": "".join(parts).strip(),
            "y0": min(word["y0"] for word in row_words),
            "height": median(word["height"] for word in row_words),
        })
    return output


def _structured_ocr_sections(words, page_rect) -> dict:
    """Preserve slide title, table rows, and explicit Korean-English pairs."""
    rows = _ocr_rows_from_words(words, float(page_rect.width))
    if not rows:
        return {"text": "", "heading": "", "table_rows": [], "bilingual_pairs": [], "rejected_bilingual_pairs": []}

    heading_candidates = [
        row for row in rows
        if row["y0"] <= float(page_rect.height) * 0.28
        and 3 <= len(_ALNUM_RE.findall(row["text"])) <= 70
        and len(_KOREAN_RE.findall(row["text"])) >= 2
    ]
    heading = ""
    if heading_candidates:
        raw_heading = max(heading_candidates, key=lambda row: (row["height"], -row["y0"]))["text"]
        compact_heading = "".join(_KOREAN_RE.findall(raw_heading))
        if 3 <= len(compact_heading) <= 32 and _SPARSE_HEADING_SUFFIX_RE.search(compact_heading):
            heading = compact_heading

    table_rows = []
    for row in rows:
        cells = [cell.strip() for cell in row["text"].split("|") if cell.strip()]
        if len(cells) >= 2 and sum(len(_ALNUM_RE.findall(cell)) >= 2 for cell in cells) >= 2:
            table_rows.append(" | ".join(cells))
    if len(table_rows) < 2:
        table_rows = []

    bilingual_pairs = []
    rejected_bilingual_pairs = []
    pair_re = re.compile(
        r"([가-힣][가-힣0-9· /-]{1,28})\s*[（(]\s*([A-Za-z][A-Za-z0-9 +/.,'’-]{1,62})\s*[)）]"
    )
    for row in rows:
        for match in pair_re.finditer(row["text"]):
            korean = re.sub(r"\s+", " ", match.group(1)).strip(" -/·|")
            english = re.sub(r"\s+", " ", match.group(2)).strip()
            pair = f"{korean} ({english})"
            english_words = re.findall(r"[A-Za-z]+", english)
            suspicious_leading_fragment = bool(
                len(english_words) >= 3
                and len(english_words[0]) <= 2
                and english_words[0].islower()
            )
            if suspicious_leading_fragment:
                if pair not in rejected_bilingual_pairs:
                    rejected_bilingual_pairs.append(pair)
            elif pair not in bilingual_pairs:
                bilingual_pairs.append(pair)

    lines = []
    if heading:
        lines.append(f"[제목] {heading}")
    lines.extend(f"[표 행] {row}" for row in table_rows[:40])
    lines.extend(f"[한영 병기] {pair}" for pair in bilingual_pairs[:40])
    return {
        "text": "\n".join(lines),
        "heading": heading,
        "table_rows": table_rows,
        "bilingual_pairs": bilingual_pairs,
        "rejected_bilingual_pairs": rejected_bilingual_pairs,
    }


def _primary_ocr_page(page) -> dict:
    text_page = page.get_textpage_ocr(language="kor+eng", dpi=300, full=True)
    ocr_text = page.get_text("text", textpage=text_page).strip()
    words = page.get_text("words", textpage=text_page, sort=True)
    layout = _structured_ocr_sections(words, page.rect)
    if layout["text"]:
        ocr_text = f"{ocr_text}\n\n[구조 OCR]\n{layout['text']}".strip()
    return {
        "ocr_text": ocr_text,
        "ocr_engine": "pymupdf_tesseract",
        "ocr_fallback_used": False,
        "ocr_layout": layout,
    }


def _ocr_page_text(page) -> str:
    return _primary_ocr_page(page)["ocr_text"]


def _ocr_page_with_fallback(page) -> dict:
    try:
        return _primary_ocr_page(page)
    except Exception as exc:
        logger.warning("Primary PyMuPDF OCR failed; using direct Tesseract fallback: %s", exc)
        fallback_text = _run_tesseract(page, dpi=300, psm=6)
        return {
            "ocr_text": fallback_text,
            "ocr_engine": "tesseract_cli_psm6",
            "ocr_fallback_used": True,
            "ocr_fallback_reason": type(exc).__name__,
            "ocr_layout": {"text": "", "heading": "", "table_rows": [], "bilingual_pairs": [], "rejected_bilingual_pairs": []},
        }


def _run_tesseract(page, *, dpi: int, psm: int, clip=None) -> str:
    executable = _find_tesseract_executable()
    if not executable:
        return ""
    pixmap = page.get_pixmap(dpi=dpi, alpha=False, clip=clip)
    completed = subprocess.run(
        [executable, "stdin", "stdout", "-l", "kor+eng", "--psm", str(psm)],
        input=pixmap.tobytes("png"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=45,
    )
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _ocr_sparse_page_text(page) -> str:
    return _run_tesseract(page, dpi=400, psm=11)


def _ocr_heading_region_text(page) -> str:
    outputs = []
    for ratio in _HEADING_REGION_RATIOS:
        clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * ratio)
        try:
            text = _run_tesseract(page, dpi=500, psm=6, clip=clip)
        except (subprocess.SubprocessError, TimeoutError) as exc:
            logger.warning("Heading OCR failed at ratio %.2f: %s", ratio, exc)
            continue
        if text:
            outputs.append(text)
    return "\n".join(outputs)


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ocr_worker_count() -> int:
    try:
        return max(1, min(int(os.getenv("OCR_MAX_WORKERS", "2")), 2))
    except ValueError:
        return 2


def _ocr_page_from_path(pdf_path: str, page_index: int) -> dict:
    """Open an isolated document per worker because PyMuPDF pages are not thread-safe."""
    worker_doc = fitz.open(pdf_path)
    try:
        page = worker_doc[page_index]
        ocr_data = _ocr_page_with_fallback(page)
        ocr_text = ocr_data["ocr_text"]
        sparse_restored_terms = []
        if _needs_sparse_heading_ocr(ocr_text):
            sparse_sources = [_ocr_heading_region_text(page), _ocr_sparse_page_text(page)]
            sparse = merge_sparse_heading_terms(
                ocr_text, "\n".join(text for text in sparse_sources if text)
            )
            ocr_text = sparse["text"]
            sparse_restored_terms = sparse["restored_terms"]
        return {
            **ocr_data,
            "ocr_text": ocr_text,
            "sparse_restored_terms": sparse_restored_terms,
        }
    finally:
        worker_doc.close()


def _page_result(page_number: int, native_text: str, ocr_data: dict, prefer_korean: bool) -> dict | None:
    sparse_restored_terms = ocr_data.get("sparse_restored_terms", [])
    selection = select_page_text(native_text, ocr_data.get("ocr_text", ""), prefer_korean)
    if sparse_restored_terms and selection["method"].startswith("ocr_"):
        selection["method"] += "+heading_multicrop_500dpi+psm11_400dpi"
    if ocr_data.get("ocr_fallback_used") and selection["method"].startswith("ocr_"):
        selection["method"] += "+tesseract_cli_fallback"
    if not selection["text"]:
        return None
    return {
        "page": page_number,
        "text": selection["text"],
        "text_extraction_method": selection["method"],
        "text_quality_reason": selection["reason"],
        "text_quality_score": selection["quality"]["score"],
        "text_korean_ratio": selection["quality"]["korean_ratio"],
        "text_noise_ratio": selection["quality"]["noise_ratio"],
        "text_native_terms_restored": selection["restored_terms"],
        "text_sparse_terms_restored": sparse_restored_terms,
        "text_ocr_engine": ocr_data.get("ocr_engine"),
        "text_ocr_fallback_used": bool(ocr_data.get("ocr_fallback_used")),
        "text_ocr_fallback_reason": ocr_data.get("ocr_fallback_reason"),
        "text_layout_heading": (ocr_data.get("ocr_layout") or {}).get("heading"),
        "text_layout_table_rows": len((ocr_data.get("ocr_layout") or {}).get("table_rows") or []),
        "text_layout_bilingual_pairs": len((ocr_data.get("ocr_layout") or {}).get("bilingual_pairs") or []),
    }


def extract_pdf_text(pdf_path: str, prefer_korean: bool = False):
    pdf_hash = _file_sha256(pdf_path)
    pdf_cache_key = hashlib.sha256(
        f"{_PDF_CACHE_VERSION}\0{pdf_hash}\0{int(prefer_korean)}".encode("utf-8")
    ).hexdigest()
    if cached_pdf := get_json("pdf", pdf_cache_key):
        return cached_pdf

    doc = fitz.open(pdf_path)
    try:
        native_pages = [page.get_text("text").strip() for page in doc]
    finally:
        doc.close()

    can_ocr_korean = _korean_ocr_available()
    if prefer_korean and not can_ocr_korean and any(
        _needs_korean_ocr(text, prefer_korean) for text in native_pages
    ):
        logger.warning("Korean OCR recommended but kor.traineddata is not installed.")

    page_results = {}
    ocr_targets = []
    for page_index, native_text in enumerate(native_pages):
        page_cache_key = hashlib.sha256(
            f"{_PDF_CACHE_VERSION}\0{pdf_hash}\0{page_index}\0{int(prefer_korean)}\0{int(can_ocr_korean)}".encode("utf-8")
        ).hexdigest()
        cached_page = get_json("pdf_page", page_cache_key)
        if cached_page is not None:
            page_results[page_index] = cached_page
        elif can_ocr_korean and _needs_korean_ocr(native_text, prefer_korean):
            ocr_targets.append((page_index, page_cache_key))
        else:
            result = _page_result(page_index + 1, native_text, {}, prefer_korean)
            page_results[page_index] = result
            set_json("pdf_page", page_cache_key, result)

    if ocr_targets:
        # PyMuPDF's OCR bridge shares Leptonica state across threads. Separate
        # processes preserve two-way parallelism without intermittent code=4 failures.
        with ProcessPoolExecutor(max_workers=min(_ocr_worker_count(), len(ocr_targets))) as executor:
            futures = {
                page_index: executor.submit(_ocr_page_from_path, pdf_path, page_index)
                for page_index, _ in ocr_targets
            }
            for page_index, page_cache_key in ocr_targets:
                try:
                    ocr_data = futures[page_index].result()
                except Exception as exc:
                    logger.warning("Korean OCR failed for page %s: %s", page_index + 1, exc)
                    ocr_data = {}
                result = _page_result(page_index + 1, native_pages[page_index], ocr_data, prefer_korean)
                page_results[page_index] = result
                set_json("pdf_page", page_cache_key, result)

    pages = [page_results[index] for index in sorted(page_results) if page_results[index]]
    set_json("pdf", pdf_cache_key, pages)
    return pages
