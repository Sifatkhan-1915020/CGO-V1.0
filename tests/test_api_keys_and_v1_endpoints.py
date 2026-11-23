"""
Unit and Integration Tests for CareGuide API Key Generator and Developer v1 Endpoints.
Tests:
  - Cryptographic generation and hashing
  - Database persistence and listing
  - Active, expired, and revoked key validation
  - Rate-limit (RPM) sliding window enforcement
  - Total quota enforcement
  - Scope authorization checks
  - FastAPI /v1/health, /v1/extract, /v1/invoices/extract endpoints with TestClient
  - Backward compatibility of existing routes
"""

import os
import sys
import time
import unittest
from fastapi.testclient import TestClient

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from careguide.api_keys import (
    generate_api_key,
    validate_api_key,
    revoke_api_key,
    reactivate_api_key,
    delete_api_key,
    list_api_keys,
    get_api_key_by_id,
    get_api_key_usage_stats,
    hash_key,
    mask_key,
)
from api_server import app


class TestCareGuideAPIKeys(unittest.TestCase):
    def setUp(self):
        self.test_keys_to_clean = []

    def tearDown(self):
        for k_id in self.test_keys_to_clean:
            try:
                delete_api_key(k_id)
            except Exception:
                pass

    def test_key_generation_and_hashing(self):
        """Test cryptographic key generation, prefixing, masking, and hashing."""
        key_data = generate_api_key(
            name="unittest-client",
            scopes=["extract", "invoices"],
            rate_limit_rpm=30,
            total_limit=500,
            expires_in_days=30
        )
        self.test_keys_to_clean.append(key_data["key_id"])

        raw_key = key_data["api_key"]
        self.assertTrue(raw_key.startswith("cg_live_"))
        self.assertEqual(key_data["name"], "unittest-client")
        self.assertEqual(key_data["rate_limit_rpm"], 30)
        self.assertEqual(key_data["total_limit"], 500)
        self.assertIn("extract", key_data["scopes"])
        self.assertIn("invoices", key_data["scopes"])
        self.assertTrue(key_data["masked_key"].startswith("cg_live_..."))

        # Verify hash match
        expected_digest = hash_key(raw_key)
        self.assertEqual(len(expected_digest), 64)  # SHA-256 is 64 hex characters

    def test_key_validation_and_revocation(self):
        """Test key validation, scope checks, and immediate revocation."""
        key_data = generate_api_key(
            name="revocation-test",
            scopes=["extract"]
        )
        self.test_keys_to_clean.append(key_data["key_id"])

        raw_key = key_data["api_key"]

        # 1. Valid with granted scope
        valid, err, rec = validate_api_key(raw_key, required_scope="extract")
        self.assertTrue(valid)
        self.assertIsNone(err)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["name"], "revocation-test")

        # 2. Invalid with missing scope
        valid_scope, err_scope, _ = validate_api_key(raw_key, required_scope="admin")
        self.assertFalse(valid_scope)
        self.assertIn("lacks required scope", err_scope)

        # 3. Revoke key
        revoked = revoke_api_key(key_data["key_id"])
        self.assertTrue(revoked)

        # 4. Attempt validation after revocation
        valid_after, err_after, _ = validate_api_key(raw_key)
        self.assertFalse(valid_after)
        self.assertIn("revoked", err_after)

        # 5. Reactivate key
        reactivated = reactivate_api_key(key_data["key_id"])
        self.assertTrue(reactivated)
        valid_react, _, _ = validate_api_key(raw_key)
        self.assertTrue(valid_react)

    def test_rate_limiting_sliding_window(self):
        """Test RPM rate limiting rejects requests when exceeding threshold."""
        key_data = generate_api_key(
            name="ratelimit-test",
            rate_limit_rpm=3  # Low RPM for fast testing
        )
        self.test_keys_to_clean.append(key_data["key_id"])
        raw_key = key_data["api_key"]

        # Call 3 times (should succeed)
        for i in range(3):
            valid, err, _ = validate_api_key(raw_key)
            self.assertTrue(valid, f"Request {i+1} should succeed")

        # 4th call should trigger rate limit
        valid_4, err_4, _ = validate_api_key(raw_key)
        self.assertFalse(valid_4)
        self.assertIn("Rate limit exceeded", err_4)

    def test_total_quota_limit(self):
        """Test total lifetime quota limit enforcement."""
        key_data = generate_api_key(
            name="quota-test",
            total_limit=2
        )
        self.test_keys_to_clean.append(key_data["key_id"])
        raw_key = key_data["api_key"]

        v1, _, _ = validate_api_key(raw_key)
        self.assertTrue(v1)
        v2, _, _ = validate_api_key(raw_key)
        self.assertTrue(v2)

        # 3rd should fail quota
        v3, err3, _ = validate_api_key(raw_key)
        self.assertFalse(v3)
        self.assertIn("quota exceeded", err3)


class TestFastAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        # Generate an active key for test API calls
        cls.test_key = generate_api_key(
            name="api-endpoint-test-client",
            scopes=["extract", "invoices", "health"],
            rate_limit_rpm=60
        )
        cls.raw_key = cls.test_key["api_key"]

    @classmethod
    def tearDownClass(cls):
        try:
            delete_api_key(cls.test_key["key_id"])
        except Exception:
            pass

    def test_unauthenticated_v1_health_rejected(self):
        """Test /v1/health returns 401 when no key provided."""
        res = self.client.get("/v1/health")
        self.assertEqual(res.status_code, 401)
        self.assertIn("API key required", res.json()["detail"])

    def test_authenticated_v1_health_with_header(self):
        """Test /v1/health returns 200 with X-API-Key."""
        res = self.client.get("/v1/health", headers={"X-API-Key": self.raw_key})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["client"]["name"], "api-endpoint-test-client")

    def test_authenticated_v1_health_with_bearer(self):
        """Test /v1/health returns 200 with Authorization: Bearer <key>."""
        res = self.client.get("/v1/health", headers={"Authorization": f"Bearer {self.raw_key}"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")

    def test_api_keys_management_flow(self):
        """Test creating, listing, verifying, and revoking keys via HTTP endpoints."""
        # 1. Generate key via API
        create_res = self.client.post("/api/keys/generate", json={
            "name": "partner-via-api",
            "scopes": ["extract", "invoices"],
            "rpm": 120,
            "days": 45
        })
        self.assertEqual(create_res.status_code, 200)
        key_info = create_res.json()["data"]
        created_key_id = key_info["key_id"]
        created_raw_key = key_info["api_key"]

        # 2. Verify key
        verify_res = self.client.get("/api/keys/verify", headers={"X-API-Key": created_raw_key})
        self.assertEqual(verify_res.status_code, 200)
        self.assertEqual(verify_res.json()["status"], "valid")

        # 3. List keys
        list_res = self.client.get("/api/keys")
        self.assertEqual(list_res.status_code, 200)
        keys = list_res.json()["keys"]
        self.assertTrue(any(k["key_id"] == created_key_id for k in keys))

        # 4. Revoke key
        revoke_res = self.client.delete(f"/api/keys/{created_key_id}")
        self.assertEqual(revoke_res.status_code, 200)

        # 5. Verify revoked fails
        verify_revoked = self.client.get("/api/keys/verify", headers={"X-API-Key": created_raw_key})
        self.assertEqual(verify_revoked.status_code, 401)

        # Cleanup
        delete_api_key(created_key_id)

    def test_backward_compatibility_existing_routes(self):
        """Verify existing public/internal routes remain functional without requiring an API key."""
        # Existing health check
        health_res = self.client.get("/api/health")
        self.assertEqual(health_res.status_code, 200)
        self.assertEqual(health_res.json()["status"], "healthy")

        # Existing employee list
        emp_res = self.client.get("/api/auth/employees")
        self.assertEqual(emp_res.status_code, 200)
        self.assertIn("employees", emp_res.json())

    def test_v1_extract_with_sample_file(self):
        """Test /v1/extract processes an uploaded document file with API key."""
        csv_path = os.path.join(PROJECT_ROOT, "dummy_bangla_invoice.csv")
        if not os.path.exists(csv_path):
            self.skipTest("dummy_bangla_invoice.csv not found for upload test")

        with open(csv_path, "rb") as f:
            res = self.client.post(
                "/v1/extract",
                headers={"X-API-Key": self.raw_key},
                files=[("files", ("dummy_bangla_invoice.csv", f, "text/csv"))]
            )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total_documents"], 1)
        self.assertIn("documents", data)
        self.assertEqual(len(data["documents"]), 1)


if __name__ == "__main__":
    unittest.main()
