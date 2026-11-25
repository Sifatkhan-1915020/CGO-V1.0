"""
CareGuide Real-Time API Call, Token Usage & Estimated Cost Tracker.
Persists actual measured Gemini token counts (from SDK usage_metadata / REST usageMetadata)
and Baidu Unlimited-OCR calls in SQLite (`careguide_system.db`).
"""

import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "careguide_system.db",
)

# USD per 1,000,000 tokens: (input_rate_usd, output_rate_usd)
MODEL_PRICING_PER_1M: Dict[str, Tuple[float, float]] = {
    "gemini-3-flash-preview": (0.10, 0.40),
    "gemini-3.5-flash": (0.10, 0.40),
    "gemini-flash-latest": (0.10, 0.40),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-3.1-flash-lite-preview": (0.075, 0.30),
    "baidu-unlimited-ocr": (0.0, 0.0),
    "rapidocr-local": (0.0, 0.0),
}

FEATURE_LABELS: Dict[str, str] = {
    "doc_analyze": "AI Summary & Q&A",
    "invoice_extract": "Invoice Extraction",
    "ocr_fallback": "Gemini OCR Fallback",
    "manual_reocr": "Manual Gemini Re-OCR",
    "baidu_ocr": "Baidu Unlimited-OCR",
}

GEMINI_FEATURES = ("doc_analyze", "invoice_extract", "ocr_fallback", "manual_reocr")


def get_model_rates(model: str) -> Tuple[float, float]:
    """Return (input_usd_per_1m, output_usd_per_1m) for a given model name."""
    norm = (model or "").strip().lower()
    if norm in MODEL_PRICING_PER_1M:
        return MODEL_PRICING_PER_1M[norm]
    if "lite" in norm:
        return (0.075, 0.30)
    if "pro" in norm:
        return (1.25, 5.00)
    if "baidu" in norm or "rapid" in norm or "ocr" in norm:
        return (0.0, 0.0)
    return (0.10, 0.40)


def calculate_cost_usd(model: str, prompt_tokens: int, candidates_tokens: int) -> float:
    """Compute exact estimated USD cost from real measured prompt + output tokens."""
    in_rate, out_rate = get_model_rates(model)
    cost = (max(0, prompt_tokens) / 1_000_000.0) * in_rate + (max(0, candidates_tokens) / 1_000_000.0) * out_rate
    return round(cost, 8)


def extract_gemini_usage(response_obj: Any) -> Dict[str, int]:
    """
    Extract real token counts from either a google-genai SDK response object
    (response.usage_metadata) or a REST API JSON dict (data["usageMetadata"]).
    Returns {prompt_tokens, candidates_tokens, total_tokens}.
    """
    prompt_t = 0
    cand_t = 0
    total_t = 0

    if response_obj is None:
        return {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}

    if isinstance(response_obj, dict):
        meta = response_obj.get("usageMetadata") or response_obj.get("usage_metadata") or {}
        if isinstance(meta, dict):
            prompt_t = int(meta.get("promptTokenCount") or meta.get("prompt_token_count") or 0)
            cand_t = int(meta.get("candidatesTokenCount") or meta.get("candidates_token_count") or 0)
            total_t = int(meta.get("totalTokenCount") or meta.get("total_token_count") or (prompt_t + cand_t))
    else:
        meta = getattr(response_obj, "usage_metadata", None) or getattr(response_obj, "usageMetadata", None)
        if meta is not None:
            prompt_t = int(getattr(meta, "prompt_token_count", None) or getattr(meta, "promptTokenCount", 0) or 0)
            cand_t = int(getattr(meta, "candidates_token_count", None) or getattr(meta, "candidatesTokenCount", 0) or 0)
            total_t = int(getattr(meta, "total_token_count", None) or getattr(meta, "totalTokenCount", 0) or (prompt_t + cand_t))

    if total_t == 0 and (prompt_t or cand_t):
        total_t = prompt_t + cand_t

    return {
        "prompt_tokens": max(0, prompt_t),
        "candidates_tokens": max(0, cand_t),
        "total_tokens": max(0, total_t),
    }


class UsageTracker:
    """Thread-safe SQLite-backed tracker for Gemini/OCR calls, tokens, and USD costs."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_PATH
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS api_usage_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        feature TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        candidates_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        cost_usd REAL NOT NULL DEFAULT 0.0,
                        latency_ms REAL NOT NULL DEFAULT 0.0,
                        status TEXT NOT NULL DEFAULT 'success',
                        items_count INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
                conn.commit()

    def record_call(
        self,
        feature: str,
        model: str,
        prompt_tokens: int = 0,
        candidates_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "success",
        provider: Optional[str] = None,
        items_count: int = 1,
    ) -> Dict[str, Any]:
        """Record a real LLM or OCR call with measured token usage and calculated cost."""
        norm_feature = feature if feature in FEATURE_LABELS else "ocr_fallback"
        norm_provider = provider or ("gemini" if norm_feature in GEMINI_FEATURES else "ocr")
        p_tok = max(0, int(prompt_tokens or 0))
        c_tok = max(0, int(candidates_tokens or 0))
        t_tok = max(p_tok + c_tok, int(total_tokens or 0))
        cost = calculate_cost_usd(model, p_tok, c_tok)
        now = time.time()
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO api_usage_logs
                        (ts, created_at, feature, provider, model, prompt_tokens, candidates_tokens, total_tokens, cost_usd, latency_ms, status, items_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        ts_str,
                        norm_feature,
                        norm_provider,
                        model or "unknown",
                        p_tok,
                        c_tok,
                        t_tok,
                        cost,
                        round(float(latency_ms or 0.0), 2),
                        status,
                        max(1, int(items_count or 1)),
                    ),
                )
                conn.commit()

        return {
            "feature": norm_feature,
            "provider": norm_provider,
            "model": model,
            "prompt_tokens": p_tok,
            "candidates_tokens": c_tok,
            "total_tokens": t_tok,
            "cost_usd": cost,
            "latency_ms": round(float(latency_ms or 0.0), 2),
            "status": status,
        }

    def get_summary(self, recent_limit: int = 12) -> Dict[str, Any]:
        """Return aggregated real token counts, USD cost, call breakdown, and recent calls."""
        with self._lock:
            with closing(self._connect()) as conn:
                agg = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_calls,
                        SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_calls,
                        SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS failed_calls,
                        SUM(CASE WHEN provider = 'gemini' THEN 1 ELSE 0 END) AS gemini_calls,
                        SUM(CASE WHEN provider != 'gemini' THEN 1 ELSE 0 END) AS ocr_calls,
                        COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                        COALESCE(SUM(candidates_tokens), 0) AS candidates_tokens,
                        COALESCE(SUM(total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd
                    FROM api_usage_logs
                    """
                ).fetchone()

                feat_rows = conn.execute(
                    """
                    SELECT
                        feature,
                        COUNT(*) AS calls,
                        SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS errors,
                        COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                        COALESCE(SUM(candidates_tokens), 0) AS candidates_tokens,
                        COALESCE(SUM(total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                        MAX(created_at) AS last_call_at
                    FROM api_usage_logs
                    GROUP BY feature
                    """
                ).fetchall()

                last_models = {}
                for row in conn.execute(
                    """
                    SELECT a.feature, a.model
                    FROM api_usage_logs a
                    INNER JOIN (
                        SELECT feature, MAX(id) AS max_id
                        FROM api_usage_logs
                        GROUP BY feature
                    ) b ON a.id = b.max_id
                    """
                ).fetchall():
                    last_models[row["feature"]] = row["model"]

                recent_rows = conn.execute(
                    """
                    SELECT created_at, feature, provider, model, prompt_tokens, candidates_tokens, total_tokens, cost_usd, latency_ms, status, items_count
                    FROM api_usage_logs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, int(recent_limit)),),
                ).fetchall()

        by_feature: Dict[str, Dict[str, Any]] = {
            key: {
                "feature": key,
                "label": label,
                "calls": 0,
                "errors": 0,
                "prompt_tokens": 0,
                "candidates_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "last_model": None,
                "last_call_at": None,
            }
            for key, label in FEATURE_LABELS.items()
        }

        for r in feat_rows:
            fkey = r["feature"]
            if fkey in by_feature:
                by_feature[fkey].update(
                    {
                        "calls": int(r["calls"] or 0),
                        "errors": int(r["errors"] or 0),
                        "prompt_tokens": int(r["prompt_tokens"] or 0),
                        "candidates_tokens": int(r["candidates_tokens"] or 0),
                        "total_tokens": int(r["total_tokens"] or 0),
                        "cost_usd": round(float(r["cost_usd"] or 0.0), 6),
                        "last_model": last_models.get(fkey),
                        "last_call_at": r["last_call_at"],
                    }
                )

        recent_calls: List[Dict[str, Any]] = [
            {
                "timestamp": r["created_at"],
                "feature": r["feature"],
                "label": FEATURE_LABELS.get(r["feature"], r["feature"]),
                "provider": r["provider"],
                "model": r["model"],
                "prompt_tokens": int(r["prompt_tokens"] or 0),
                "candidates_tokens": int(r["candidates_tokens"] or 0),
                "total_tokens": int(r["total_tokens"] or 0),
                "cost_usd": round(float(r["cost_usd"] or 0.0), 6),
                "latency_ms": float(r["latency_ms"] or 0.0),
                "status": r["status"],
                "items_count": int(r["items_count"] or 1),
            }
            for r in recent_rows
        ]

        return {
            "total_calls": int(agg["total_calls"] or 0),
            "success_calls": int(agg["success_calls"] or 0),
            "failed_calls": int(agg["failed_calls"] or 0),
            "gemini_calls": int(agg["gemini_calls"] or 0),
            "ocr_calls": int(agg["ocr_calls"] or 0),
            "prompt_tokens": int(agg["prompt_tokens"] or 0),
            "candidates_tokens": int(agg["candidates_tokens"] or 0),
            "total_tokens": int(agg["total_tokens"] or 0),
            "estimated_cost_usd": round(float(agg["total_cost_usd"] or 0.0), 6),
            "pricing_basis": "Estimated USD from measured Gemini usageMetadata (Flash ~$0.10/1M in, $0.40/1M out; Baidu OCR $0.00)",
            "by_feature": list(by_feature.values()),
            "recent_calls": recent_calls,
        }

    def reset(self) -> None:
        """Clear all recorded usage logs."""
        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute("DELETE FROM api_usage_logs")
                conn.commit()


usage_tracker = UsageTracker()
