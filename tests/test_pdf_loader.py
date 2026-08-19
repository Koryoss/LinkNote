import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pdf_loader import (
    _find_tesseract_executable,
    _korean_ocr_available,
    _needs_sparse_heading_ocr,
    _needs_korean_ocr,
    _ocr_worker_count,
    _ocr_page_with_fallback,
    _structured_ocr_sections,
    merge_ocr_with_native_terms,
    merge_sparse_heading_terms,
    select_page_text,
    text_quality_metrics,
)


class PdfLoaderTests(unittest.TestCase):
    def test_finder_environment_discovers_homebrew_ocr(self):
        def installed_file(path):
            return path in {
                "/opt/homebrew/share/tessdata/kor.traineddata",
                "/opt/homebrew/share/tessdata/eng.traineddata",
            }

        with patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=True), \
                patch("pdf_loader.fitz.get_tessdata", side_effect=RuntimeError("not installed")), \
                patch("pdf_loader.os.path.isfile", side_effect=installed_file), \
                patch("pdf_loader.os.access", return_value=True):
            self.assertTrue(_korean_ocr_available())
            self.assertEqual(
                __import__("os").environ["TESSDATA_PREFIX"],
                "/opt/homebrew/share/tessdata",
            )
            self.assertEqual(_find_tesseract_executable(), "/opt/homebrew/bin/tesseract")

    def test_missing_tessdata_does_not_raise(self):
        with patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=True), \
                patch("pdf_loader.fitz.get_tessdata", side_effect=RuntimeError("not installed")), \
                patch("pdf_loader.os.path.isfile", return_value=False):
            self.assertFalse(_korean_ocr_available())

    def test_ocr_worker_count_is_capped_at_two(self):
        with patch.dict("os.environ", {"OCR_MAX_WORKERS": "20"}):
            self.assertEqual(_ocr_worker_count(), 2)

    def test_primary_ocr_failure_uses_direct_tesseract_fallback(self):
        page = object()
        with patch("pdf_loader._primary_ocr_page", side_effect=RuntimeError("OCR failed")), \
                patch("pdf_loader._run_tesseract", return_value="만성 염증과 육아종") as fallback:
            result = _ocr_page_with_fallback(page)

        fallback.assert_called_once_with(page, dpi=300, psm=6)
        self.assertTrue(result["ocr_fallback_used"])
        self.assertEqual(result["ocr_engine"], "tesseract_cli_psm6")
        self.assertIn("만성 염증", result["ocr_text"])

    def test_layout_structure_preserves_heading_table_rows_and_bilingual_pairs(self):
        words = [
            (10, 10, 80, 25, "급성", 0, 0, 0),
            (88, 10, 160, 25, "염증의", 0, 0, 1),
            (168, 10, 250, 25, "진단검사", 0, 0, 2),
            (10, 80, 150, 94, "백혈구(Leukocytes)", 1, 0, 0),
            (310, 80, 470, 94, "중성구 수의 증가", 1, 0, 1),
            (10, 110, 180, 124, "C-반응 단백질(C-reactive protein)", 1, 1, 0),
            (310, 110, 470, 124, "급성 염증에서 증가", 1, 1, 1),
        ]
        result = _structured_ocr_sections(words, SimpleNamespace(width=600, height=400))

        self.assertEqual(result["heading"], "급성염증의진단검사")
        self.assertEqual(len(result["table_rows"]), 2)
        self.assertIn("백혈구 (Leukocytes)", result["bilingual_pairs"])
        self.assertIn("[표 행]", result["text"])
        self.assertIn("[한영 병기]", result["text"])

    def test_layout_structure_drops_broken_english_alias_fragment(self):
        words = [
            (10, 10, 120, 24, "세포막인지질(ol", 0, 0, 0),
            (126, 10, 240, 24, "MEMBRANE", 0, 0, 1),
            (246, 10, 340, 24, "LIPIDS)", 0, 0, 2),
        ]
        result = _structured_ocr_sections(words, SimpleNamespace(width=600, height=400))
        self.assertEqual(result["bilingual_pairs"], [])
        self.assertEqual(result["rejected_bilingual_pairs"], ["세포막인지질 (ol MEMBRANE LIPIDS)"])

    def test_korean_course_page_with_only_english_terms_requests_ocr(self):
        self.assertTrue(_needs_korean_ocr("(Bradycardia) 60 bpm", prefer_korean=True))

    def test_page_with_korean_text_keeps_native_extraction(self):
        self.assertFalse(_needs_korean_ocr("서맥은 심박수가 느린 상태입니다.", prefer_korean=True))

    def test_english_course_does_not_force_korean_ocr(self):
        self.assertFalse(_needs_korean_ocr("(Bradycardia) 60 bpm", prefer_korean=False))

    def test_korean_ocr_wins_when_native_text_loses_the_korean_definition(self):
        result = select_page_text(
            "(Bradycardia) 60 bpm",
            "서맥(Bradycardia)은 분당 60회 미만의 느린 심박동이다.",
            prefer_korean=True,
        )
        self.assertEqual(result["method"], "ocr_kor_eng_300dpi")
        self.assertEqual(result["reason"], "ocr_recovered_korean")
        self.assertIn("서맥", result["text"])

    def test_symbol_heavy_ocr_does_not_replace_readable_native_text(self):
        result = select_page_text(
            "서맥은 분당 60회 미만의 느린 심박동이다.",
            "| © | e | 4194 | A|24",
            prefer_korean=True,
        )
        self.assertEqual(result["method"], "native")
        self.assertEqual(result["text"], "서맥은 분당 60회 미만의 느린 심박동이다.")

    def test_ocr_replaces_empty_native_text_even_on_english_only_page(self):
        result = select_page_text(
            "",
            "Vitamin B12 deficiency after total gastrectomy: systematic review and meta-analysis",
            prefer_korean=True,
        )
        self.assertEqual(result["method"], "ocr_kor_eng_300dpi")
        self.assertEqual(result["reason"], "ocr_recovered_missing_text")

    def test_ocr_replaces_native_page_when_korean_was_lost(self):
        result = select_page_text(
            "(Hemophilia A) VIII IX",
            "A형 혈우병(Hemophilia A)은 응고인자 VIII 결핍으로 발생한다.",
            prefer_korean=True,
        )
        self.assertEqual(result["method"], "ocr_kor_eng_300dpi")
        self.assertEqual(result["reason"], "ocr_recovered_korean")
        self.assertIn("혈우병", result["text"])

    def test_hybrid_ocr_restores_missing_native_medical_terms_and_measurements(self):
        merged = merge_ocr_with_native_terms(
            "총철결합능과 평균 적혈구 용적을 측정한다.",
            "TIBC (Total iron binding capacity), MCV, Hb 10.5 g/dL, BCR-ABL",
        )
        self.assertIn("TIBC", merged["restored_terms"])
        self.assertIn("MCV", merged["restored_terms"])
        self.assertIn("10.5 g/dL", merged["restored_terms"])
        self.assertIn("BCR-ABL", merged["restored_terms"])
        self.assertIn("[원문 영문·수치]", merged["text"])

    def test_hybrid_does_not_duplicate_terms_already_recognized_by_ocr(self):
        merged = merge_ocr_with_native_terms(
            "서맥(Bradycardia)은 분당 60회 미만이다.",
            "(Bradycardia) 60 bpm",
        )
        self.assertNotIn("(Bradycardia)", merged["restored_terms"])

    def test_quality_metrics_expose_language_and_noise_signals(self):
        clean = text_quality_metrics("서맥은 느린 심박동이다.", prefer_korean=True)
        noisy = text_quality_metrics("| © | e | 4194 |", prefer_korean=True)
        self.assertGreater(clean["korean_count"], noisy["korean_count"])
        self.assertGreater(clean["score"], noisy["score"])

    def test_korean_page_reports_latin_only_ocr_lines(self):
        quality = text_quality_metrics(
            "서맥은 느린 심박동이다.\nates\nee ee ee\n(Bradycardia)",
            prefer_korean=True,
        )
        self.assertGreater(quality["latin_only_line_ratio"], 0)

    def test_broken_heading_with_readable_body_requests_sparse_ocr(self):
        text = (
            "aS kes|구증7 | 증\noni—\nS&\n<\n"
            "적혈구수가비정상적으로증가되어있는상태\n원발성 적혈구 생산 증가"
        )
        self.assertTrue(_needs_sparse_heading_ocr(text))

    def test_clean_korean_heading_does_not_request_sparse_ocr(self):
        text = "적혈구증가증\n적혈구 수가 비정상적으로 증가되어 있는 상태\n원인과 치료"
        self.assertFalse(_needs_sparse_heading_ocr(text))

    def test_corrupted_leukemia_heading_requests_sparse_ocr(self):
        text = (
            "마성공스선배형벼(CML)\n"
            "골수성줄기세포 악성 변화로 말초혈액과 조직에 중성구가 증식하는 질환"
        )
        self.assertTrue(_needs_sparse_heading_ocr(text))

    def test_clean_bilingual_leukemia_heading_does_not_request_sparse_ocr(self):
        text = "만성 골수성 백혈병(CML)\n골수성 줄기세포의 악성 변화로 발생하는 질환"
        self.assertFalse(_needs_sparse_heading_ocr(text))

    def test_sparse_ocr_restores_only_plausible_missing_heading_terms(self):
        primary = "aS kes|구증7 | 증\n적혈구수가비정상적으로증가되어있는상태"
        sparse = "적혈구증가증\n전상저\n원발성\n적혈구수가 비정상적으로 증가되어 있는 상태"
        merged = merge_sparse_heading_terms(primary, sparse)
        self.assertEqual(merged["restored_terms"], ["적혈구증가증"])
        self.assertIn("[희소 영역 OCR] 적혈구증가증", merged["text"])

    def test_sparse_ocr_does_not_duplicate_existing_heading(self):
        merged = merge_sparse_heading_terms("적혈구증가증", "적혈구증가증")
        self.assertEqual(merged["restored_terms"], [])

    def test_sparse_ocr_accepts_bilingual_heading_and_drops_near_ocr_mutation(self):
        primary = "마성공스선배형벼(CML)\n골수성 줄기세포의 악성 변화"
        sparse = "만성 골수성 백열병(CML)\n만성 골수성 백혈병(CML)"
        merged = merge_sparse_heading_terms(primary, sparse)
        self.assertEqual(merged["restored_terms"], ["만성골수성백혈병"])


if __name__ == "__main__":
    unittest.main()
