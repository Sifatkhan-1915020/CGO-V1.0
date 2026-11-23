"""
Comprehensive unit and integration test suite for CareGuide Document Extraction Engine.
Covers digital PDFs, scanned PDFs, multi-frame TIFFs, multi-sheet XLSX, CSVs, poor-quality images,
corrupted file recovery, and checkpoint ledger resumption.
"""

import os
import sys
import unittest
import tempfile
import shutil
import numpy as np
import cv2
from PIL import Image
import fitz  # PyMuPDF
import openpyxl

from careguide.schema import ProcessingStatus, ExtractionType, DocumentType
from careguide.validator import FileValidator, FileFormat
from careguide.preprocessor import ImageEnhancer
from careguide.ocr_client import OCRClient
from careguide.pdf_extractor import PDFExtractor
from careguide.image_extractor import ImageExtractor
from careguide.tabular_extractor import TabularExtractor
from careguide.checkpoint import CheckpointLedger
from careguide.orchestrator import DocumentOrchestrator


class TestDocumentPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="careguide_test_")
        cls.ocr_client = OCRClient()  # Uses local standby mock for deterministic unit testing

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    # -------------------------------------------------------------
    # 1. Validation & Magic Byte Sniffing
    # -------------------------------------------------------------
    def test_file_validation_and_corruption_shield(self):
        # Empty file
        empty_path = os.path.join(self.test_dir, "empty.pdf")
        with open(empty_path, "wb") as f:
            pass
        v_empty = FileValidator.validate(empty_path)
        self.assertFalse(v_empty.is_valid)
        self.assertIn("empty", v_empty.error_message.lower())

        # Corrupted PDF (missing header)
        corrupt_pdf = os.path.join(self.test_dir, "corrupt.pdf")
        with open(corrupt_pdf, "wb") as f:
            f.write(b"NOT_A_PDF_STREAM_RANDOM_BYTES_123456789")
        v_corrupt = FileValidator.validate(corrupt_pdf)
        self.assertFalse(v_corrupt.is_valid)
        self.assertIn("Corrupted PDF", v_corrupt.error_message)

    # -------------------------------------------------------------
    # 2. Digital PDF Fast-Path
    # -------------------------------------------------------------
    def test_digital_pdf_fast_path(self):
        pdf_path = os.path.join(self.test_dir, "sample_digital.pdf")
        doc = fitz.open()

        # Page 1: Digital text
        page1 = doc.new_page()
        page1.insert_text((50, 72), "CareGuide Patient Report Header", fontsize=14)
        page1.insert_text((50, 100), "This is a digital PDF page containing clean text blocks that should bypass OCR.", fontsize=11)

        # Page 2: Digital text
        page2 = doc.new_page()
        page2.insert_text((50, 72), "Page 2 Clinical Observations and Diagnostic Data", fontsize=12)
        doc.save(pdf_path)
        doc.close()

        extractor = PDFExtractor(self.ocr_client)
        results = list(extractor.extract_document(pdf_path, "sample_digital.pdf"))

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].type, ExtractionType.DIGITAL_TEXT)
        self.assertEqual(results[0].status, ProcessingStatus.SUCCESS)
        self.assertIn("CareGuide Patient Report Header", results[0].text)
        self.assertGreaterEqual(len(results[0].blocks), 1)

    # -------------------------------------------------------------
    # 3. Scanned PDF & Hybrid Triage
    # -------------------------------------------------------------
    def test_scanned_pdf_triage(self):
        pdf_path = os.path.join(self.test_dir, "sample_scanned.pdf")
        doc = fitz.open()

        # Create an image to embed as a scanned page
        img_np = np.ones((400, 600, 3), dtype=np.uint8) * 255
        cv2.putText(img_np, "SCANNED INVOICE #1024", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
        _, img_png = cv2.imencode(".png", img_np)

        page = doc.new_page()
        page.insert_image(fitz.Rect(0, 0, 595, 842), stream=img_png.tobytes())
        doc.save(pdf_path)
        doc.close()

        extractor = PDFExtractor(self.ocr_client)
        results = list(extractor.extract_document(pdf_path, "sample_scanned.pdf"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].type, ExtractionType.UNLIMITED_OCR)
        self.assertEqual(results[0].status, ProcessingStatus.SUCCESS)

    # -------------------------------------------------------------
    # 4. Poor Image Quality Enhancement (Deskew, Denoise)
    # -------------------------------------------------------------
    def test_image_enhancement_and_deskew(self):
        # Create a skewed image
        img = np.ones((500, 500, 3), dtype=np.uint8) * 255
        cv2.putText(img, "DIAGNOSTIC LAB RESULT", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
        cv2.putText(img, "HEMOGLOBIN: 14.2 g/dL", (50, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

        # Rotate by 5 degrees to simulate skew
        M = cv2.getRotationMatrix2D((250, 250), 5.0, 1.0)
        skewed = cv2.warpAffine(img, M, (500, 500), borderValue=(255, 255, 255))
        _, skewed_bytes = cv2.imencode(".png", skewed)

        enhanced_bytes, applied = ImageEnhancer.enhance(skewed_bytes.tobytes())
        self.assertIsInstance(enhanced_bytes, bytes)
        self.assertGreater(len(enhanced_bytes), 0)
        self.assertTrue(any("deskew" in op or "adaptive" in op for op in applied))

    # -------------------------------------------------------------
    # 5. Multi-Frame TIFF Extraction
    # -------------------------------------------------------------
    def test_multi_frame_tiff(self):
        tiff_path = os.path.join(self.test_dir, "multipage.tiff")
        frame1 = Image.new("RGB", (300, 300), color=(255, 255, 255))
        frame2 = Image.new("RGB", (300, 300), color=(240, 240, 240))
        frame1.save(tiff_path, save_all=True, append_images=[frame2])

        extractor = ImageExtractor(self.ocr_client)
        results = list(extractor.extract_document(tiff_path, "multipage.tiff"))

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].index, 1)
        self.assertEqual(results[1].index, 2)
        self.assertEqual(results[0].status, ProcessingStatus.SUCCESS)

    # -------------------------------------------------------------
    # 6. Tabular Extraction: Multi-Sheet XLSX
    # -------------------------------------------------------------
    def test_multi_sheet_xlsx(self):
        xlsx_path = os.path.join(self.test_dir, "records.xlsx")
        wb = openpyxl.Workbook()

        # Sheet 1: Patients
        ws1 = wb.active
        ws1.title = "Patients"
        ws1.append(["ID", "Name", "Age", "Condition"])
        ws1.append([101, "Alice Smith", 45, "Hypertension"])
        ws1.append([102, "Bob Jones", 62, "Diabetes"])

        # Sheet 2: Vitals
        ws2 = wb.create_sheet(title="Vitals")
        ws2.append(["ID", "HeartRate", "BP", "Date"])
        ws2.append([101, 72, "120/80", "2026-11-15"])

        wb.save(xlsx_path)
        wb.close()

        sheets = list(TabularExtractor.extract_xlsx(xlsx_path))
        self.assertEqual(len(sheets), 2)
        self.assertEqual(sheets[0].name, "Patients")
        self.assertEqual(sheets[0].row_count, 2)
        self.assertEqual(sheets[0].columns, ["ID", "Name", "Age", "Condition"])
        self.assertEqual(sheets[0].data[0]["Name"], "Alice Smith")

        self.assertEqual(sheets[1].name, "Vitals")
        self.assertEqual(sheets[1].row_count, 1)

    # -------------------------------------------------------------
    # 7. Tabular Extraction: CSV with Sniffer
    # -------------------------------------------------------------
    def test_csv_extraction_with_delimiter_sniffing(self):
        # CSV with semicolon delimiter
        csv_path = os.path.join(self.test_dir, "semicolon.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("PatientID;Dosage;Unit;Status\n")
            f.write("P01;50;mg;Active\n")
            f.write("P02;100;mg;Pending\n")

        sheets = list(TabularExtractor.extract_csv(csv_path))
        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0].row_count, 2)
        self.assertIn("Dosage", sheets[0].columns)
        self.assertEqual(sheets[0].data[0]["Dosage"], "50")

    # -------------------------------------------------------------
    # 8. Checkpoint Ledger Resumption
    # -------------------------------------------------------------
    def test_checkpoint_ledger(self):
        db_path = os.path.join(self.test_dir, "test_checkpoint.db")
        jsonl_path = os.path.join(self.test_dir, "test_stream.jsonl")
        ledger = CheckpointLedger(db_path=db_path, jsonl_path=jsonl_path)

        from careguide.schema import PageResult
        sample_page = PageResult(
            index=1,
            type=ExtractionType.DIGITAL_TEXT,
            status=ProcessingStatus.SUCCESS,
            text="Checkpoint test content"
        )

        batch_id = "test_batch_123"
        doc_id = "doc_abc"

        self.assertFalse(ledger.is_page_completed(batch_id, doc_id, 1))
        ledger.record_page(batch_id, doc_id, sample_page)
        self.assertTrue(ledger.is_page_completed(batch_id, doc_id, 1))

        # Check JSONL stream was appended
        self.assertTrue(os.path.exists(jsonl_path))
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("Checkpoint test content", lines[0])

    # -------------------------------------------------------------
    # 9. End-to-End Batch Orchestration
    # -------------------------------------------------------------
    def test_batch_orchestration(self):
        batch_folder = os.path.join(self.test_dir, "batch_test_folder")
        os.makedirs(batch_folder, exist_ok=True)

        # 1. Digital PDF
        doc = fitz.open()
        p = doc.new_page()
        p.insert_text((50, 72), "Batch Test Digital Content Line 1 Line 2 Line 3 Line 4", fontsize=12)
        doc.save(os.path.join(batch_folder, "test_doc.pdf"))
        doc.close()

        # 2. CSV
        with open(os.path.join(batch_folder, "data.csv"), "w", encoding="utf-8") as f:
            f.write("A,B,C\n1,2,3\n4,5,6\n")

        # 3. Image
        img = Image.new("RGB", (200, 200), color=(255, 255, 255))
        img.save(os.path.join(batch_folder, "scan.png"))

        # 4. Corrupt file
        with open(os.path.join(batch_folder, "broken.pdf"), "wb") as f:
            f.write(b"CORRUPTED_DATA_TRUNCATED")

        orchestrator = DocumentOrchestrator(
            checkpoint_db=os.path.join(self.test_dir, "batch_check.db"),
            jsonl_output=os.path.join(self.test_dir, "batch_out.jsonl")
        )

        summary = orchestrator.process_batch([batch_folder], show_progress=False)
        self.assertEqual(summary.total_files, 4)
        self.assertEqual(summary.successful_files, 3)
        self.assertEqual(summary.corrupted_or_failed_files, 1)  # Broken PDF caught!


if __name__ == "__main__":
    unittest.main()
