"""
Unit tests for Bangla utilities, Plain Text (TXT) extraction (no OCR),
and CSV/TSV/Excel bilingual extraction with zero OCR.
"""

import os
import tempfile
import unittest
import pandas as pd

from careguide.bangla_utils import (
    bangla_to_arabic_numerals,
    arabic_to_bangla_numerals,
    parse_bangla_numeric_string,
    is_bangla_text,
    normalize_bangla_unicode,
    is_bijoy_ascii,
    convert_bijoy_to_unicode,
    BANGLA_FINANCIAL_TERMS
)
from careguide.tabular_extractor import TabularExtractor
from careguide.validator import FileValidator, FileFormat
from careguide.schema import DocumentType, ExtractionType
from careguide.invoice_extractor import InvoiceExtractor


class TestBanglaAndTabular(unittest.TestCase):

    def test_bangla_numeral_conversion(self):
        # 1. Full digit set conversion
        bn_digits = "০১২৩৪৫৬৭৮৯"
        ar_digits = bangla_to_arabic_numerals(bn_digits)
        self.assertEqual(ar_digits, "0123456789")

        # 2. Reverse conversion
        self.assertEqual(arabic_to_bangla_numerals(12345), "১২৩৪৫")

    def test_bangla_numeric_parsing(self):
        # Currency symbol and Bangla digits
        self.assertEqual(parse_bangla_numeric_string("৳ ৪,৫০০.৫০"), 4500.5)
        self.assertEqual(parse_bangla_numeric_string("টাকা ১২০"), 120.0)
        self.assertEqual(parse_bangla_numeric_string("৮১৫.০৬"), 815.06)
        self.assertEqual(parse_bangla_numeric_string(""), None)

    def test_is_bangla_text(self):
        self.assertTrue(is_bangla_text("চালান নং RCT-2026-0001"))
        self.assertTrue(is_bangla_text("সরবরাহকারী: মেসার্স মেঘনা সাপ্লাই"))
        self.assertFalse(is_bangla_text("Commercial Invoice INV-001"))

    def test_invoice_extractor_safe_float_bangla(self):
        self.assertEqual(InvoiceExtractor._safe_float("৳ ৪,৫০০.০০"), 4500.0)
        self.assertEqual(InvoiceExtractor._safe_float("১২,৩৪৫.৬৭"), 12345.67)
        self.assertEqual(InvoiceExtractor._safe_float("500.00"), 500.0)

    def test_txt_extraction_no_ocr(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("চালান নং: INV-BN-001\nতারিখ: ১২/০৭/২০২৬\nসরবরাহকারী: পদ্মা এন্টারপ্রাইজ\nসর্বমোট: ৫,০০০ টাকা\n")
            txt_path = f.name

        try:
            # 1. Validator checks
            val = FileValidator.validate(txt_path)
            self.assertTrue(val.is_valid)
            self.assertEqual(val.format, FileFormat.TXT)

            # 2. Extract without OCR
            pages = list(TabularExtractor.extract_txt(txt_path))
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0].type, ExtractionType.DIGITAL_TEXT)
            self.assertIn("চালান নং", pages[0].text)
            self.assertIn("পদ্মা এন্টারপ্রাইজ", pages[0].text)
            self.assertEqual(pages[0].confidence, 1.0)
            self.assertEqual(pages[0].engine, "Direct Text Reader (No OCR)")
        finally:
            os.remove(txt_path)

    def test_csv_tsv_bangla_extraction(self):
        # Create a TSV file with Bangla headers and numbers
        tsv_content = (
            "চালান নং\tতারিখ\tবিবরণ\tপরিমাণ\tদর\tমোট টাকা\n"
            "INV-01\t2026-01-01\tএ৪ কাগজ\t১০\t৮১৫.০৬\t৮১৫০.৬০\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8") as f:
            f.write(tsv_content)
            tsv_path = f.name

        try:
            val = FileValidator.validate(tsv_path)
            self.assertTrue(val.is_valid)
            self.assertEqual(val.format, FileFormat.TSV)

            sheets = list(TabularExtractor.extract_csv(tsv_path))
            self.assertEqual(len(sheets), 1)
            self.assertEqual(sheets[0].name, "TSV")
            self.assertEqual(sheets[0].row_count, 1)
            self.assertIn("চালান নং", sheets[0].columns)
            self.assertEqual(sheets[0].data[0]["বিবরণ"], "এ৪ কাগজ")
        finally:
            os.remove(tsv_path)


if __name__ == "__main__":
    unittest.main()
