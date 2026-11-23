"""
Unit tests for invoice document segmentation in multi-page PDFs and continuation pages.
"""

import unittest
from api_server import segment_document_into_invoices


class TestInvoiceSegmentation(unittest.TestCase):

    def test_single_page_document(self):
        doc = {
            "filename": "invoice_single.pdf",
            "pages": [{"index": 1, "text": "INVOICE #INV-001\nTotal: 100.00"}]
        }
        res = segment_document_into_invoices([doc])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0][0], "invoice_single.pdf")
        self.assertIn("INVOICE #INV-001", res[0][1])

    def test_multi_page_separate_invoices(self):
        doc = {
            "filename": "batch_100_invoices.pdf",
            "pages": [
                {"index": 1, "text": "COMMERCIAL INVOICE\nINVOICE NO: INV-001\nTotal: 50.00"},
                {"index": 2, "text": "COMMERCIAL INVOICE\nINVOICE NO: INV-002\nTotal: 75.00"},
                {"index": 3, "text": "COMMERCIAL INVOICE\nINVOICE NO: INV-003\nTotal: 120.00"},
            ]
        }
        res = segment_document_into_invoices([doc])
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0][0], "batch_100_invoices.pdf (Page 1)")
        self.assertEqual(res[1][0], "batch_100_invoices.pdf (Page 2)")
        self.assertEqual(res[2][0], "batch_100_invoices.pdf (Page 3)")

    def test_continuation_pages_grouping(self):
        doc = {
            "filename": "multi_page_inv.pdf",
            "pages": [
                {
                    "index": 1,
                    "text": "INVOICE NO: INV-001\nItems 1-5\nContinued on the next page. Invoice totals appear on the final page."
                },
                {
                    "index": 2,
                    "text": "COMMERCIAL INVOICE\nLINE ITEMS CONTINUED\nINVOICE NO: INV-001\nItems 6-10\nSubtotal: 100\nTotal: 110"
                },
                {
                    "index": 3,
                    "text": "INVOICE NO: INV-002\nTotal: 25.00"
                }
            ]
        }
        res = segment_document_into_invoices([doc])
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0][0], "multi_page_inv.pdf (Pages 1-2)")
        self.assertIn("Items 1-5", res[0][1])
        self.assertIn("LINE ITEMS CONTINUED", res[0][1])
        self.assertEqual(res[1][0], "multi_page_inv.pdf (Page 3)")

    def test_max_invoices_limit(self):
        doc = {
            "filename": "large_batch.pdf",
            "pages": [{"index": i, "text": f"INVOICE {i}"} for i in range(1, 51)]
        }
        res = segment_document_into_invoices([doc], max_invoices=10)
        self.assertEqual(len(res), 10)


if __name__ == "__main__":
    unittest.main()
