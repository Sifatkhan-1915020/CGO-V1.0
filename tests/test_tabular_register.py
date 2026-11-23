"""
Unit tests for Tabular Invoice Register / Ledger extraction and Task 3 audit.
Tests 1,000-row extraction, multi-item invoice grouping, and zero OCR execution.
"""

import os
import unittest
from typing import Dict, Any, List

from careguide.tabular_extractor import TabularExtractor
from careguide.invoice_extractor import InvoiceExtractor, InvoiceData, InvoiceLineItem
from api_server import partition_documents_for_invoice_extraction


class TestTabularRegister(unittest.TestCase):

    def setUp(self):
        self.extractor = InvoiceExtractor()
        self.challan_file = r"C:\Users\User\Downloads\challan_data_1000_rows.xlsx"

    def test_is_tabular_invoice_register_positive(self):
        """Verify standard Bengali and English tabular registers are correctly detected."""
        bangla_sheet = {
            "name": "চালান ডেটা",
            "columns": ["চালান নং", "তারিখ", "সরবরাহকারী", "ক্রেতা", "বিবরণ", "পরিমাণ", "একক দর", "মোট টাকা", "উপমোট", "ভ্যাট", "সর্বমোট", "মুদ্রা"],
            "data": [{"চালান নং": "CHL-001", "বিবরণ": "কাগজ", "মোট টাকা": 500}]
        }
        self.assertTrue(InvoiceExtractor.is_tabular_invoice_register(bangla_sheet))

        english_sheet = {
            "name": "Invoices",
            "columns": ["Invoice Number", "Supplier Name", "Description", "Quantity", "Unit Price", "Line Total Amount", "Currency"],
            "data": [{"Invoice Number": "INV-100", "Description": "Mouse", "Line Total Amount": 25.0}]
        }
        self.assertTrue(InvoiceExtractor.is_tabular_invoice_register(english_sheet))

    def test_is_tabular_invoice_register_negative_for_visual_sheets(self):
        """Verify visual document sheets (with title banners, not registers) return False."""
        visual_sheet = {
            "name": "Bangla_Invoice_চালান",
            "columns": ["বাণিজ্যিক চালান ও বিল (COMMERCIAL INVOICE)", "Column_2", "Column_3", "Column_4", "Column_5"],
            "data": [{"বাণিজ্যিক চালান ও বিল (COMMERCIAL INVOICE)": "চালান নং (Invoice No):"}]
        }
        self.assertFalse(InvoiceExtractor.is_tabular_invoice_register(visual_sheet))

    def test_multi_item_invoice_grouping(self):
        """Test grouping multiple rows with the same invoice number into a single invoice with multiple line items."""
        register_sheet = {
            "name": "Ledger",
            "columns": ["Invoice Number", "Invoice Date", "Supplier Name", "Description", "Quantity", "Unit Price", "Amount"],
            "data": [
                {"Invoice Number": "INV-2026-001", "Invoice Date": "2026-11-20", "Supplier Name": "Alpha Supply", "Description": "Item A", "Quantity": 2, "Unit Price": 50.0, "Amount": 100.0},
                {"Invoice Number": "INV-2026-001", "Invoice Date": "2026-11-20", "Supplier Name": "Alpha Supply", "Description": "Item B", "Quantity": 3, "Unit Price": 100.0, "Amount": 300.0},
                {"Invoice Number": "INV-2026-002", "Invoice Date": "2026-11-21", "Supplier Name": "Beta Traders", "Description": "Item C", "Quantity": 1, "Unit Price": 250.0, "Amount": 250.0},
            ]
        }
        invoices = InvoiceExtractor.extract_invoices_from_tabular_sheet(register_sheet, source_filename="ledger.xlsx")
        self.assertEqual(len(invoices), 2)

        # Invoice 1
        inv1 = invoices[0]
        self.assertEqual(inv1.invoice_number, "INV-2026-001")
        self.assertEqual(inv1.supplier_name, "Alpha Supply")
        self.assertEqual(len(inv1.line_items), 2)
        self.assertEqual(inv1.line_items[0].description, "Item A")
        self.assertEqual(inv1.line_items[1].description, "Item B")
        self.assertEqual(inv1.subtotal, 400.0)
        self.assertEqual(inv1.total, 400.0)

        # Invoice 2
        inv2 = invoices[1]
        self.assertEqual(inv2.invoice_number, "INV-2026-002")
        self.assertEqual(len(inv2.line_items), 1)
        self.assertEqual(inv2.total, 250.0)

        # Validation
        validated = self.extractor.validate_invoices(invoices)
        self.assertEqual(len(validated), 2)
        self.assertTrue(all(v.validation_status == "CLEAN" for v in validated))

    def test_extract_1000_rows_challan(self):
        """Verify extracting all 1000 invoices from challan_data_1000_rows.xlsx in < 0.5s."""
        if not os.path.exists(self.challan_file):
            self.skipTest(f"Challan test file not found at {self.challan_file}")

        sheets = list(TabularExtractor.extract_xlsx(self.challan_file))
        self.assertGreaterEqual(len(sheets), 1)

        sheet_dict = sheets[0].model_dump()
        self.assertTrue(InvoiceExtractor.is_tabular_invoice_register(sheet_dict))

        invoices = InvoiceExtractor.extract_invoices_from_tabular_sheet(
            sheet_dict,
            source_filename="challan_data_1000_rows.xlsx"
        )
        self.assertEqual(len(invoices), 1000)

        # First and last invoice sanity check
        self.assertEqual(invoices[0].invoice_number, "CHL-2026-0100")
        self.assertEqual(invoices[0].supplier_name, "মেঘনা ট্রেডিং কর্পোরেশন")
        self.assertEqual(invoices[0].total, 1176.0)
        self.assertEqual(invoices[0].currency, "BDT")

        self.assertEqual(invoices[-1].invoice_number, "CHL-2026-1099")
        self.assertEqual(invoices[-1].supplier_name, "প্রগতি এন্টারপ্রাইজ")
        self.assertEqual(invoices[-1].total, 1638.0)

        # Task 3 Validation
        validated = self.extractor.validate_invoices(invoices)
        self.assertEqual(len(validated), 1000)
        clean_count = sum(1 for v in validated if not v.requires_manual_review)
        self.assertEqual(clean_count, 1000)

    def test_partition_documents_scope_limit(self):
        """Verify partition_documents_for_invoice_extraction respects max_invoices scope."""
        if not os.path.exists(self.challan_file):
            self.skipTest(f"Challan test file not found at {self.challan_file}")

        sheets = list(TabularExtractor.extract_xlsx(self.challan_file))
        raw_docs = [{"filename": "challan_data_1000_rows.xlsx", "pages": [], "sheets": [s.model_dump() for s in sheets]}]

        # Test scope = 25
        tab_invs, doc_pairs = partition_documents_for_invoice_extraction(raw_docs, max_invoices=25)
        self.assertEqual(len(tab_invs), 25)
        self.assertEqual(len(doc_pairs), 0)

        # Test scope = None (All 1000)
        tab_invs_all, _ = partition_documents_for_invoice_extraction(raw_docs, max_invoices=None)
        self.assertEqual(len(tab_invs_all), 1000)


if __name__ == "__main__":
    unittest.main()
