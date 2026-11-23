"""
Unit tests for the Gemini Fallback Packet architecture:
- Trigger condition evaluation (Bangla, confidence < 85%, corrupted/failed status)
- Packet formation (max 20 elements per packet, <=20 combined into 1 packet)
- Element differentiation and prompt construction
- Multi-element demultiplexing and reconciliation
"""

import unittest
from unittest.mock import MagicMock, patch
from careguide.schema import (
    PageResult,
    DocumentResult,
    DocumentType,
    ExtractionType,
    ProcessingStatus,
    is_bangla_text,
    needs_gemini_fallback,
)
from careguide.gemini_fallback import (
    build_packet_prompt,
    demultiplex_packet_response,
    GeminiOCRClient,
)
from careguide.orchestrator import DocumentOrchestrator


class TestGeminiPacketFallback(unittest.TestCase):

    def test_bangla_detection(self):
        """Test detection of Bengali / Bangla Unicode characters."""
        self.assertTrue(is_bangla_text("ঔষধ: নাপা ৫০০ মিলিগ্রাম"))
        self.assertTrue(is_bangla_text("Patient Name: রহিম উদ্দিন (Age: 45)"))
        self.assertFalse(is_bangla_text("Rx: Paracetamol 500mg TDS"))
        self.assertFalse(is_bangla_text("1234567890"))
        self.assertFalse(is_bangla_text(""))
        self.assertFalse(is_bangla_text(None))

    def test_needs_gemini_fallback_criteria(self):
        """Test trigger conditions for routing to the fallback queue."""
        # 1. Bangla detected -> True
        p1 = PageResult(
            index=1,
            type=ExtractionType.UNLIMITED_OCR,
            status=ProcessingStatus.SUCCESS,
            text="রোগীর নাম: করিম",
            confidence=0.95
        )
        needs, reason = needs_gemini_fallback(p1)
        self.assertTrue(needs)
        self.assertEqual(reason, "bangla_detected")

        # 2. Low confidence (< 85%) -> True
        p2 = PageResult(
            index=2,
            type=ExtractionType.UNLIMITED_OCR,
            status=ProcessingStatus.SUCCESS,
            text="Unclear blurred text",
            confidence=0.72
        )
        needs, reason = needs_gemini_fallback(p2)
        self.assertTrue(needs)
        self.assertIn("low_confidence", reason)

        # 3. Corrupted / Failed -> True
        p3 = PageResult(
            index=3,
            type=ExtractionType.FAILED,
            status=ProcessingStatus.CORRUPTED,
            text="Corrupted frame",
            confidence=0.0
        )
        needs, reason = needs_gemini_fallback(p3)
        self.assertTrue(needs)
        self.assertIn("corrupted", reason)

        # 4. High confidence clean English -> False
        p4 = PageResult(
            index=4,
            type=ExtractionType.UNLIMITED_OCR,
            status=ProcessingStatus.SUCCESS,
            text="Clean medical report with high clarity.",
            confidence=0.96
        )
        needs, reason = needs_gemini_fallback(p4)
        self.assertFalse(needs)

    def test_build_packet_prompt_structure(self):
        """Test packet prompt formatting and delimiter instructions."""
        elements = [
            {"element_id": "fileA.png#p1", "filename": "fileA.png", "page_index": 1, "trigger_reason": "bangla"},
            {"element_id": "fileB.jpg#p1", "filename": "fileB.jpg", "page_index": 1, "trigger_reason": "low_confidence_0.65"}
        ]
        prompt = build_packet_prompt(elements)
        self.assertIn("packet of 2 document page/image element(s)", prompt)
        self.assertIn("=== START ELEMENT [i/n]: {element_id} ===", prompt)
        self.assertIn("fileA.png#p1", prompt)
        self.assertIn("fileB.jpg#p1", prompt)

    def test_demultiplex_multi_element_response(self):
        """Test demultiplexing of multi-element Gemini response into separate results."""
        elements = [
            {"element_id": "doc1.pdf#p1", "filename": "doc1.pdf", "page_index": 1, "original_text": "", "original_confidence": 0.5},
            {"element_id": "doc1.pdf#p2", "filename": "doc1.pdf", "page_index": 2, "original_text": "", "original_confidence": 0.6},
            {"element_id": "image_03.jpg#p1", "filename": "image_03.jpg", "page_index": 1, "original_text": "", "original_confidence": 0.0}
        ]

        gemini_raw_output = """
=== START ELEMENT [1/3]: doc1.pdf#p1 ===
# Prescription
ঔষধ: সেকলো ২০ মি.গ্রা.
১+০+১ (খাবার পূর্বে)
=== END ELEMENT [1/3]: doc1.pdf#p1 ===

=== START ELEMENT [2/3]: doc1.pdf#p2 ===
## Diagnostic Lab Test
Blood Glucose: 110 mg/dL
HbA1c: 5.7%
=== END ELEMENT [2/3]: doc1.pdf#p2 ===

=== START ELEMENT [3/3]: image_03.jpg#p1 ===
### Ultrasound Scan Report
Liver size: Normal
=== END ELEMENT [3/3]: image_03.jpg#p1 ===
"""
        results = demultiplex_packet_response(gemini_raw_output, elements)
        self.assertEqual(len(results), 3)

        self.assertEqual(results[0]["element_id"], "doc1.pdf#p1")
        self.assertEqual(results[0]["status"], "SUCCESS")
        self.assertEqual(results[0]["confidence"], 0.98)
        self.assertIn("সেকলো", results[0]["text"])

        self.assertEqual(results[1]["element_id"], "doc1.pdf#p2")
        self.assertEqual(results[1]["status"], "SUCCESS")
        self.assertIn("Blood Glucose", results[1]["text"])

        self.assertEqual(results[2]["element_id"], "image_03.jpg#p1")
        self.assertEqual(results[2]["status"], "SUCCESS")
        self.assertIn("Ultrasound Scan Report", results[2]["text"])

    def test_demultiplex_resilient_missing_end_tags(self):
        """Test parser resilience when END tags are omitted by the LLM."""
        elements = [
            {"element_id": "item1#p1", "filename": "item1", "page_index": 1},
            {"element_id": "item2#p2", "filename": "item2", "page_index": 2}
        ]
        gemini_raw_output = """
=== START ELEMENT [1/2]: item1#p1 ===
Content of item 1
=== START ELEMENT [2/2]: item2#p2 ===
Content of item 2
"""
        results = demultiplex_packet_response(gemini_raw_output, elements)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["text"], "Content of item 1")
        self.assertEqual(results[1]["text"], "Content of item 2")

    def test_orchestrator_packet_execution(self):
        """Test end-to-end packet grouping and execution within DocumentOrchestrator."""
        orch = DocumentOrchestrator(gemini_api_key="mock_key_123456789")

        # Mock transcribe_packet on Gemini client
        def mock_transcribe_packet(packet, progress_callback=None):
            return [
                {
                    "element_id": elem["element_id"],
                    "filename": elem["filename"],
                    "page_index": elem["page_index"],
                    "text": f"Rescued text for {elem['element_id']}",
                    "confidence": 0.98,
                    "status": "SUCCESS",
                    "engine": "Gemini Vision AI (Packet Fallback)",
                    "trigger_reason": elem.get("trigger_reason")
                }
                for elem in packet
            ]

        orch.ocr_client.gemini_client.transcribe_packet = MagicMock(side_effect=mock_transcribe_packet)

        # Create mock document with 3 pages needing fallback
        pages = [
            PageResult(
                index=1,
                type=ExtractionType.UNLIMITED_OCR,
                status=ProcessingStatus.SUCCESS,
                text="ঔষধ: এসিমেপ ২০",
                confidence=0.90,
                image_bytes=b"fake_image_bytes_1"
            ),
            PageResult(
                index=2,
                type=ExtractionType.UNLIMITED_OCR,
                status=ProcessingStatus.SUCCESS,
                text="Low conf page",
                confidence=0.60,
                image_bytes=b"fake_image_bytes_2"
            ),
            PageResult(
                index=3,
                type=ExtractionType.FAILED,
                status=ProcessingStatus.CORRUPTED,
                text="Corrupted scan",
                confidence=0.0,
                image_bytes=b"fake_image_bytes_3"
            )
        ]

        doc = DocumentResult(
            doc_id="test_doc_01",
            filename="prescription_pack.pdf",
            file_path="prescription_pack.pdf",
            file_type=DocumentType.PDF,
            mime_type="application/pdf",
            file_size_bytes=1024,
            status=ProcessingStatus.PARTIAL_SUCCESS,
            processing_time_ms=100.0,
            total_pages_or_sheets=3,
            pages=pages,
            sheets=[]
        )

        orch._execute_fallback_packets([doc])

        # Verify all 3 were passed together in 1 packet
        self.assertEqual(orch.ocr_client.gemini_client.transcribe_packet.call_count, 1)
        passed_packet = orch.ocr_client.gemini_client.transcribe_packet.call_args[0][0]
        self.assertEqual(len(passed_packet), 3)

        # Verify pages updated to SUCCESS, 0.98 confidence, and Gemini engine
        for p in doc.pages:
            self.assertEqual(p.status, ProcessingStatus.SUCCESS)
            self.assertEqual(p.confidence, 0.98)
            self.assertEqual(p.engine, "Gemini Vision AI (Packet Fallback)")
            self.assertIn("Rescued text", p.text)
            self.assertIsNone(p.image_bytes)  # Memory freed

        # Verify document overall status upgraded to SUCCESS
        self.assertEqual(doc.status, ProcessingStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()
