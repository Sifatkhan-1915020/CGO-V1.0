"""
Supabase Database Client & Data Sync Service for CareGuide.
Provides persistent storage, relational invoice indexing, Task 3 audit records,
and document extraction history in PostgreSQL via Supabase.
"""

import os
import uuid
import time
from typing import Dict, Any, List, Optional, Tuple


class SupabaseClient:
    """Client for managing CareGuide persistence with Supabase."""

    def __init__(
        self,
        url: Optional[str] = None,
        key: Optional[str] = None
    ):
        # Auto-load .env if needed
        if not os.environ.get("SUPABASE_URL"):
            env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
            if os.path.exists(env_file):
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ[k.strip()] = v.strip()

        if url is not None:
            self.url = url.strip()
        else:
            self.url = os.environ.get("SUPABASE_URL", "").strip()

        if key is not None:
            self.key = key.strip()
        else:
            self.key = (
                os.environ.get("SUPABASE_SECRET_KEY", "")
                or os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
            ).strip()
        self._client = None

        if self.is_configured:
            try:
                from supabase import create_client, Client
                self._client: Optional[Client] = create_client(self.url, self.key)
            except Exception as e:
                print(f"[SupabaseClient] Warning: Failed to initialize Supabase client: {e}")
                self._client = None

    @property
    def is_configured(self) -> bool:
        """Check if Supabase URL and Key are provided."""
        return bool(self.url and self.key)

    def check_connection(self) -> Dict[str, Any]:
        """
        Verify Supabase credentials and check which database tables exist.
        Returns health status, latency, and detected tables.
        """
        if not self.is_configured:
            return {
                "status": "unconfigured",
                "connected": False,
                "message": "Supabase URL and Key are not set in environment or .env.",
                "tables_found": []
            }

        start = time.time()
        try:
            import httpx
            headers = {
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}"
            }
            res = httpx.get(f"{self.url}/rest/v1/", headers=headers, timeout=8.0)
            elapsed = round(time.time() - start, 3)

            if res.status_code == 200:
                spec = res.json()
                definitions = spec.get("definitions", {})
                tables = list(definitions.keys())

                # Check core CareGuide tables
                required = ["invoices", "invoice_line_items", "validation_issues", "documents"]
                ready = all(t in tables for t in required)

                return {
                    "status": "connected" if ready else "connected_pending_schema",
                    "connected": True,
                    "url": self.url,
                    "latency_sec": elapsed,
                    "tables_found": tables,
                    "schema_ready": ready,
                    "message": "Connected to Supabase PostgreSQL database." if ready else "Connected, but tables need to be created using supabase_schema.sql."
                }
            else:
                return {
                    "status": "auth_error",
                    "connected": False,
                    "status_code": res.status_code,
                    "message": f"Supabase responded with HTTP {res.status_code}: {res.text[:120]}"
                }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "message": f"Connection error: {str(e)}"
            }

    def save_invoices(self, invoices: List[Dict[str, Any]], job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Save validated invoices, itemized line items, and Task 3 audit findings into Supabase.
        """
        if not self._client:
            raise RuntimeError("Supabase client is not configured. Provide SUPABASE_URL and SUPABASE_SECRET_KEY.")

        saved_invoices = 0
        saved_items = 0
        saved_issues = 0
        errors = []

        # Prepare bulk records
        all_inv_rows = []
        all_item_rows = []
        all_issue_rows = []

        for inv in invoices:
            inv_uuid = str(uuid.uuid4())
            inv_row = {
                "id": inv_uuid,
                "job_id": job_id,
                "source_filename": inv.get("source_filename") or inv.get("source_file", "unknown"),
                "invoice_number": inv.get("invoice_number"),
                "invoice_date": inv.get("invoice_date"),
                "supplier_name": inv.get("supplier_name"),
                "customer_name": inv.get("customer_name"),
                "currency": inv.get("currency") or "USD",
                "subtotal": inv.get("subtotal"),
                "tax": inv.get("tax"),
                "total": inv.get("total"),
                "requires_manual_review": bool(inv.get("requires_manual_review", False)),
                "validation_status": inv.get("validation_status", "CLEAN"),
                "raw_notes": inv.get("raw_notes") or inv.get("raw_text_snippet")
            }
            all_inv_rows.append(inv_row)

            line_items = inv.get("line_items", [])
            for li in line_items:
                qty = li.get("quantity")
                price = li.get("unit_price")
                amt = li.get("amount")
                expected = (qty * price) if (qty is not None and price is not None) else None
                math_match = True
                if expected is not None and amt is not None:
                    math_match = abs(expected - amt) <= 0.05

                all_item_rows.append({
                    "invoice_id": inv_uuid,
                    "item_number": li.get("item_number"),
                    "description": li.get("description"),
                    "quantity": qty,
                    "unit_price": price,
                    "amount": amt,
                    "math_match": math_match
                })

            issues = inv.get("validation_issues") or inv.get("issues", [])
            for iss in issues:
                all_issue_rows.append({
                    "invoice_id": inv_uuid,
                    "rule": iss.get("rule") or iss.get("issue_type", "AUDIT_FLAG"),
                    "issue_type": iss.get("issue_type") or iss.get("rule", "AUDIT_FLAG"),
                    "severity": iss.get("severity", "ERROR"),
                    "message": iss.get("message", "Validation flag")
                })

        # Fast Bulk Insert Pass
        try:
            if all_inv_rows:
                self._client.table("invoices").insert(all_inv_rows).execute()
                saved_invoices = len(all_inv_rows)
            if all_item_rows:
                # Insert line items in chunks of 500 to avoid payload caps
                for c_idx in range(0, len(all_item_rows), 500):
                    self._client.table("invoice_line_items").insert(all_item_rows[c_idx:c_idx + 500]).execute()
                saved_items = len(all_item_rows)
            if all_issue_rows:
                for c_idx in range(0, len(all_issue_rows), 500):
                    self._client.table("validation_issues").insert(all_issue_rows[c_idx:c_idx + 500]).execute()
                saved_issues = len(all_issue_rows)
        except Exception as bulk_err:
            print(f"[SupabaseClient] Bulk insert fallback to itemized mode: {bulk_err}")
            # Fallback to individual insertion
            saved_invoices = 0
            saved_items = 0
            saved_issues = 0
            for inv_r in all_inv_rows:
                try:
                    self._client.table("invoices").insert(inv_r).execute()
                    saved_invoices += 1
                except Exception as e:
                    errors.append(f"Invoice {inv_r.get('invoice_number')}: {e}")

            for itm_r in all_item_rows:
                try:
                    self._client.table("invoice_line_items").insert(itm_r).execute()
                    saved_items += 1
                except Exception:
                    pass

            for iss_r in all_issue_rows:
                try:
                    self._client.table("validation_issues").insert(iss_r).execute()
                    saved_issues += 1
                except Exception:
                    pass

        return {
            "status": "success" if not errors else "partial_success",
            "saved_invoices": saved_invoices,
            "saved_items": saved_items,
            "saved_issues": saved_issues,
            "errors": errors
        }

    def save_extraction_job(self, summary: Dict[str, Any], documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Record a document batch extraction job with all files and pages in Supabase.
        """
        if not self._client:
            raise RuntimeError("Supabase client is not configured.")

        job_id = f"job_{int(time.time() * 1000)}"
        job_row = {
            "id": job_id,
            "total_files": summary.get("total_files", len(documents)),
            "total_pages": summary.get("total_pages", 0),
            "total_sheets": summary.get("total_sheets", 0),
            "duration_sec": summary.get("duration_sec", 0.0),
            "throughput_pages_per_sec": summary.get("throughput_pages_per_sec", 0.0),
            "summary": summary
        }

        try:
            self._client.table("extraction_jobs").insert(job_row).execute()

            # Insert document records
            for doc in documents:
                doc_uuid = str(uuid.uuid4())
                pages = doc.get("pages", [])
                doc_row = {
                    "id": doc_uuid,
                    "job_id": job_id,
                    "filename": doc.get("filename", "unknown"),
                    "status": doc.get("status", "SUCCESS"),
                    "engine_routing": doc.get("engine_routing", "OCR & Triage"),
                    "processing_time_sec": doc.get("processing_time_sec", 0.0),
                    "page_count": len(pages),
                    "metadata": {"sheets_count": len(doc.get("sheets", []))}
                }
                self._client.table("documents").insert(doc_row).execute()

                # Insert pages
                page_rows = []
                for p in pages:
                    page_rows.append({
                        "document_id": doc_uuid,
                        "page_number": p.get("page_number", 1),
                        "type": p.get("type", "EXTRACTED"),
                        "confidence": p.get("confidence", 1.0),
                        "engine": p.get("engine", "OCR"),
                        "text": p.get("text", "")
                    })

                if page_rows:
                    self._client.table("document_pages").insert(page_rows).execute()

            return {"status": "success", "job_id": job_id}
        except Exception as e:
            print(f"[SupabaseClient] Error saving extraction job: {e}")
            return {"status": "error", "error": str(e)}

    def fetch_invoices(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch invoices with their line items and validation issues from Supabase."""
        if not self._client:
            return []

        try:
            # Query invoices
            res = (
                self._client.table("invoices")
                .select("*, invoice_line_items(*), validation_issues(*)")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception as e:
            print(f"[SupabaseClient] Error fetching invoices: {e}")
            return []
