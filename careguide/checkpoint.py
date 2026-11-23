"""
SQLite and JSONL checkpoint ledger for interrupt-resilient processing.
Enables instant resumption of 1,000-page PDFs and 1,000-image batches from page N.
"""

import os
import sqlite3
import json
import time
from typing import Optional, List, Dict, Any
from .schema import PageResult, SheetResult


class CheckpointLedger:
    """Manages transactional state and incremental persistence for document batches."""

    def __init__(self, db_path: str = "careguide_checkpoint.db", jsonl_path: Optional[str] = None):
        self.db_path = db_path
        self.jsonl_path = jsonl_path or "careguide_stream.jsonl"
        self._init_db()

    def _init_db(self):
        """Initialize SQLite checkpoint tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS page_checkpoints (
                    batch_id TEXT,
                    doc_id TEXT,
                    page_index INTEGER,
                    status TEXT,
                    result_json TEXT,
                    timestamp REAL,
                    PRIMARY KEY (batch_id, doc_id, page_index)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sheet_checkpoints (
                    batch_id TEXT,
                    doc_id TEXT,
                    sheet_name TEXT,
                    sheet_index INTEGER,
                    status TEXT,
                    result_json TEXT,
                    timestamp REAL,
                    PRIMARY KEY (batch_id, doc_id, sheet_name)
                )
            """)
            conn.commit()

    def is_page_completed(self, batch_id: str, doc_id: str, page_index: int) -> bool:
        """Check if a specific page in a document has already been processed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM page_checkpoints WHERE batch_id=? AND doc_id=? AND page_index=?",
                (batch_id, doc_id, page_index)
            )
            row = cursor.fetchone()
            return row is not None and row[0] == "SUCCESS"

    def record_page(self, batch_id: str, doc_id: str, page: PageResult):
        """Record page result to SQLite and append to the real-time JSONL stream."""
        page_dict = page.model_dump()
        page_json = json.dumps(page_dict)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO page_checkpoints 
                (batch_id, doc_id, page_index, status, result_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (batch_id, doc_id, page.index, page.status.value, page_json, time.time()))
            conn.commit()

        # Append to JSONL stream
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "batch_id": batch_id,
                "doc_id": doc_id,
                "type": "PAGE",
                "index": page.index,
                "status": page.status.value,
                "data": page_dict
            }) + "\n")

    def record_sheet(self, batch_id: str, doc_id: str, sheet: SheetResult):
        """Record sheet result to SQLite and append to the real-time JSONL stream."""
        sheet_dict = sheet.model_dump()
        sheet_json = json.dumps(sheet_dict)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sheet_checkpoints 
                (batch_id, doc_id, sheet_name, sheet_index, status, result_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (batch_id, doc_id, sheet.name, sheet.index, sheet.status.value, sheet_json, time.time()))
            conn.commit()

        # Append to JSONL stream
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "batch_id": batch_id,
                "doc_id": doc_id,
                "type": "SHEET",
                "name": sheet.name,
                "status": sheet.status.value,
                "data": sheet_dict
            }) + "\n")

    def get_cached_pages(self, batch_id: str, doc_id: str) -> List[PageResult]:
        """Retrieve all completed pages for a document from SQLite."""
        pages = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT result_json FROM page_checkpoints WHERE batch_id=? AND doc_id=? ORDER BY page_index ASC",
                (batch_id, doc_id)
            )
            for row in cursor.fetchall():
                pages.append(PageResult(**json.loads(row[0])))
        return pages
