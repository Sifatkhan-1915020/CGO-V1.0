"""
Unit tests for CareGuide Invoice Extraction, Task 3 Validation Rules,
duplicate detection, and multi-sheet Excel workbook export.
"""

import os
import tempfile
import unittest
import openpyxl

from careguide.invoice_extractor import (
    InvoiceExtractor,
    InvoiceData,
    InvoiceLineItem,
    ValidatedInvoice,
    ValidationIssue,
)


class TestInvoiceExtractionAndValidation(unittest.TestCase):

    def setUp(self):
        self.extractor = InvoiceExtractor(api_key="mock_api_key_123456789")

    def test_clean_invoice_validation(self):
        """Test an invoice with complete data and 100% accurate arithmetic."""
        inv = InvoiceData(
            source_file="invoice_clean.pdf",
            invoice_number="INV-2024-001",
            invoice_date="2024-11-15",
            supplier_name="Global Health Supplies",
            customer_name="Dhaka Medical College",
            currency="USD",
            subtotal=1000.00,
            tax=150.00,
            total=1150.00,
            line_items=[
                InvoiceLineItem(item_number=1, description="Sterile Gauze 100pk", quantity=10.0, unit_price=40.00, amount=400.00),
                InvoiceLineItem(item_number=2, description="Infusion Sets", quantity=20.0, unit_price=30.00, amount=600.00),
            ]
        )

        results = self.extractor.validate_invoices([inv])
        self.assertEqual(len(results), 1)
        rec = results[0]

        self.assertFalse(rec.requires_manual_review)
        self.assertEqual(rec.validation_status, "CLEAN")
        self.assertEqual(len(rec.issues), 0)

    def test_missing_fields_validation(self):
        """Test identification of missing mandatory fields."""
        inv = InvoiceData(
            source_file="invoice_missing.pdf",
            invoice_number=None,  # Missing
            invoice_date=None,    # Missing
            supplier_name=None,   # Missing
            customer_name="Dhaka Hospital",
            currency="USD",
            subtotal=500.00,
            tax=None,
            total=None            # Missing
        )

        results = self.extractor.validate_invoices([inv])
        rec = results[0]

        self.assertTrue(rec.requires_manual_review)
        self.assertEqual(rec.validation_status, "CRITICAL_ERROR")

        rules_flagged = [i.rule for i in rec.issues]
        self.assertIn("MISSING_INVOICE_NUMBER", rules_flagged)
        self.assertIn("MISSING_INVOICE_DATE", rules_flagged)
        self.assertIn("MISSING_SUPPLIER_NAME", rules_flagged)
        self.assertIn("MISSING_TOTAL_AMOUNT", rules_flagged)

    def test_arithmetic_total_inconsistency(self):
        """Test flag when Subtotal + Tax does not equal Total."""
        inv = InvoiceData(
            source_file="invoice_bad_math.pdf",
            invoice_number="INV-999",
            invoice_date="2024-11-10",
            supplier_name="Apex Pharma",
            customer_name="Care Clinic",
            currency="BDT",
            subtotal=1000.00,
            tax=100.00,
            total=1250.00  # Should be 1100.00!
        )

        results = self.extractor.validate_invoices([inv])
        rec = results[0]

        self.assertTrue(rec.requires_manual_review)
        rules_flagged = [i.rule for i in rec.issues]
        self.assertIn("ARITHMETIC_MISMATCH_TOTAL", rules_flagged)

    def test_line_items_sum_and_unit_price_inconsistency(self):
        """Test line items summing errors and unit price calculation mismatches."""
        inv = InvoiceData(
            source_file="invoice_items_err.pdf",
            invoice_number="INV-555",
            invoice_date="2024-11-01",
            supplier_name="MediTech",
            customer_name="City Lab",
            currency="USD",
            subtotal=500.00,
            tax=0.00,
            total=500.00,
            line_items=[
                # 5 * 50 = 250, but stated amount is 200 (Unit price math error)
                InvoiceLineItem(item_number=1, description="Blood Tubes", quantity=5.0, unit_price=50.00, amount=200.00),
                # Stated amount is 100, so sum is 200 + 100 = 300 != subtotal 500 (Line items sum mismatch)
                InvoiceLineItem(item_number=2, description="Syringes", quantity=10.0, unit_price=10.00, amount=100.00),
            ]
        )

        results = self.extractor.validate_invoices([inv])
        rec = results[0]

        self.assertTrue(rec.requires_manual_review)
        rules_flagged = [i.rule for i in rec.issues]
        self.assertIn("LINE_ITEM_UNIT_MATH", rules_flagged)
        self.assertIn("LINE_ITEMS_SUM_MISMATCH", rules_flagged)

    def test_duplicate_invoice_detection(self):
        """Test detection of duplicate invoice numbers across records."""
        inv1 = InvoiceData(
            source_file="batch_1_fileA.pdf",
            invoice_number="INV-DUPE-100",
            invoice_date="2024-10-01",
            supplier_name="Square Pharmaceuticals",
            customer_name="General Hospital",
            subtotal=2000.00,
            tax=100.00,
            total=2100.00
        )
        inv2 = InvoiceData(
            source_file="batch_2_fileB.pdf",
            invoice_number="INV-DUPE-100",  # Exact duplicate number and supplier
            invoice_date="2024-10-05",
            supplier_name="Square Pharmaceuticals",
            customer_name="City Branch",
            subtotal=2000.00,
            tax=100.00,
            total=2100.00
        )

        results = self.extractor.validate_invoices([inv1, inv2])
        self.assertEqual(len(results), 2)

        # Both records must be flagged as duplicates
        self.assertTrue(results[0].requires_manual_review)
        self.assertTrue(results[1].requires_manual_review)

        self.assertIn("DUPLICATE_INVOICE", [i.rule for i in results[0].issues])
        self.assertIn("DUPLICATE_INVOICE", [i.rule for i in results[1].issues])

    def test_multi_sheet_excel_generation(self):
        """Test generation of professional multi-sheet Excel workbook with audit styling."""
        inv1 = InvoiceData(
            source_file="clean.pdf",
            invoice_number="INV-001",
            invoice_date="2024-11-20",
            supplier_name="Supplier A",
            customer_name="Customer A",
            currency="USD",
            subtotal=500.00,
            tax=50.00,
            total=550.00,
            line_items=[
                InvoiceLineItem(item_number=1, description="Widget X", quantity=5.0, unit_price=100.00, amount=500.00)
            ]
        )
        inv2 = InvoiceData(
            source_file="flagged.pdf",
            invoice_number="INV-002",
            invoice_date="2024-11-20",
            supplier_name="Supplier B",
            customer_name="Customer B",
            currency="USD",
            subtotal=1000.00,
            tax=50.00,
            total=1200.00  # Discrepancy: 1000 + 50 = 1050 != 1200
        )

        validated = self.extractor.validate_invoices([inv1, inv2])

        with tempfile.TemporaryDirectory() as tmpdir:
            out_excel = os.path.join(tmpdir, "invoices_report.xlsx")
            res_path = self.extractor.generate_excel_workbook(validated, out_excel)

            self.assertTrue(os.path.exists(res_path))

            # Verify sheets and content with openpyxl
            wb = openpyxl.load_workbook(res_path)
            self.assertIn("Invoices_Summary", wb.sheetnames)
            self.assertIn("Line_Items", wb.sheetnames)
            self.assertIn("Validation_Audit", wb.sheetnames)

            # Invoices_Summary check
            ws_summary = wb["Invoices_Summary"]
            self.assertEqual(ws_summary.max_row, 3)  # Header + 2 invoices
            # Row 2 (Clean)
            self.assertIn("CLEAN", ws_summary.cell(row=2, column=1).value)
            # Row 3 (Flagged)
            self.assertIn("FLAGGED", ws_summary.cell(row=3, column=1).value)

            # Line_Items check
            ws_items = wb["Line_Items"]
            self.assertEqual(ws_items.max_row, 2)  # Header + 1 line item
            self.assertEqual(ws_items.cell(row=2, column=5).value, "Widget X")

            # Validation_Audit check
            ws_audit = wb["Validation_Audit"]
            self.assertTrue(ws_audit.max_row >= 2)
            self.assertIn("ARITHMETIC_MISMATCH_TOTAL", ws_audit.cell(row=2, column=2).value)


if __name__ == "__main__":
    unittest.main()
