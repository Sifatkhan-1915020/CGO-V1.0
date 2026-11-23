"""
Unit tests for CareGuide Supabase Database Client & Schema Sync.
"""

import unittest
from unittest.mock import MagicMock, patch
from careguide.supabase_client import SupabaseClient


class TestSupabaseClient(unittest.TestCase):

    def setUp(self):
        self.mock_url = "https://mockproject.supabase.co"
        self.mock_key = "sb_secret_mock_test_key_12345678"
        self.client = SupabaseClient(url=self.mock_url, key=self.mock_key)

    def test_configuration_detection(self):
        """Test URL and key configuration detection."""
        self.assertTrue(self.client.is_configured)
        self.assertEqual(self.client.url, self.mock_url)
        self.assertEqual(self.client.key, self.mock_key)

        empty_client = SupabaseClient(url="", key="")
        self.assertFalse(empty_client.is_configured)

    @patch("httpx.get")
    def test_check_connection_success(self, mock_get):
        """Test health check with available schema."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "definitions": {
                "invoices": {},
                "invoice_line_items": {},
                "validation_issues": {},
                "documents": {}
            }
        }
        mock_get.return_value = mock_resp

        status = self.client.check_connection()
        self.assertTrue(status["connected"])
        self.assertTrue(status["schema_ready"])
        self.assertEqual(status["status"], "connected")
        self.assertIn("invoices", status["tables_found"])

    @patch("httpx.get")
    def test_check_connection_pending_schema(self, mock_get):
        """Test health check when database is connected but tables aren't created yet."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"definitions": {}}
        mock_get.return_value = mock_resp

        status = self.client.check_connection()
        self.assertTrue(status["connected"])
        self.assertFalse(status["schema_ready"])
        self.assertEqual(status["status"], "connected_pending_schema")

    def test_save_invoices_data_mapping(self):
        """Test mapping and insertion of invoices, items, and issues."""
        mock_table = MagicMock()
        mock_insert = MagicMock()
        mock_table.insert.return_value = mock_insert
        mock_insert.execute.return_value = MagicMock()

        self.client._client = MagicMock()
        self.client._client.table.return_value = mock_table

        sample_invoices = [
            {
                "source_filename": "inv_01.pdf",
                "invoice_number": "INV-100",
                "invoice_date": "2024-11-20",
                "supplier_name": "Pharma Med",
                "customer_name": "Clinic ABC",
                "currency": "USD",
                "subtotal": 100.0,
                "tax": 10.0,
                "total": 110.0,
                "requires_manual_review": False,
                "validation_status": "CLEAN",
                "line_items": [
                    {
                        "item_number": 1,
                        "description": "Bandages",
                        "quantity": 2.0,
                        "unit_price": 50.0,
                        "amount": 100.0
                    }
                ],
                "validation_issues": []
            }
        ]

        result = self.client.save_invoices(sample_invoices, job_id="test_job_1")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["saved_invoices"], 1)
        self.assertEqual(result["saved_items"], 1)
        self.assertEqual(result["saved_issues"], 0)


if __name__ == "__main__":
    unittest.main()
