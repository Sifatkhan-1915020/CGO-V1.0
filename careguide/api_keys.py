"""
CareGuide API Key Authentication & Management Module.

Provides cryptographically secure API key generation, SHA-256 storage,
rate limiting (sliding window RPM), quota tracking, scope authorization,
and audit usage logging for external developer access.
"""

import os
import time
import secrets
import hashlib
import sqlite3
import threading
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta

# Default Database path (shares system db with api_server)
DB_PATH = os.environ.get(
    "CAREGUIDE_SYSTEM_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "careguide_system.db")
)

# In-memory sliding window for rate limiting: key_id -> list of float timestamps
_RPM_LOCK = threading.Lock()
_RPM_TRACKER: Dict[str, List[float]] = {}


def get_db_connection() -> sqlite3.Connection:
    """Get SQLite database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_api_keys_db():
    """Initialize api_keys and api_key_usage tables if they do not exist."""
    with get_db_connection() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL,
            masked_key TEXT NOT NULL,
            scopes TEXT NOT NULL,
            rate_limit_rpm INTEGER DEFAULT 60,
            total_limit INTEGER DEFAULT -1,
            total_requests INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            last_used_at TEXT,
            created_by TEXT DEFAULT 'SYSTEM'
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS api_key_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            status_code INTEGER,
            client_ip TEXT,
            timestamp TEXT,
            latency_ms REAL
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_key_usage_key ON api_key_usage(key_id)")
        conn.commit()


# Auto-initialize on import
init_api_keys_db()


def hash_key(raw_key: str) -> str:
    """Generate SHA-256 hash of raw API key for secure storage."""
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def mask_key(raw_key: str) -> str:
    """Mask key for safe display (e.g. cg_live_...9f2a)."""
    clean = raw_key.strip()
    if len(clean) <= 12:
        return clean[:4] + "..." + clean[-2:]
    prefix = clean.split("_", 2)[0] + "_" + clean.split("_", 2)[1] + "_" if clean.count("_") >= 2 else clean[:8]
    suffix = clean[-4:]
    return f"{prefix}...{suffix}"


def generate_api_key(
    name: str,
    scopes: Optional[List[str]] = None,
    rate_limit_rpm: int = 60,
    total_limit: int = -1,
    expires_in_days: Optional[int] = 90,
    is_test: bool = False,
    created_by: str = "SYSTEM"
) -> Dict[str, Any]:
    """
    Generate a cryptographically secure API key and persist its SHA-256 hash.
    The raw API key is returned ONLY once during creation.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Customer or application name is required.")

    prefix = "cg_test_" if is_test else "cg_live_"
    random_secret = secrets.token_urlsafe(32)
    raw_key = f"{prefix}{random_secret}"
    key_id = f"key_{secrets.token_hex(8)}"
    key_digest = hash_key(raw_key)
    masked = mask_key(raw_key)

    valid_scopes = scopes if scopes is not None else ["extract", "invoices", "health"]
    scopes_str = ",".join(s.strip() for s in valid_scopes if s.strip())

    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(days=expires_in_days)).isoformat() if (expires_in_days and expires_in_days > 0) else None

    with get_db_connection() as conn:
        conn.execute("""
        INSERT INTO api_keys (
            key_id, name, key_hash, key_prefix, masked_key,
            scopes, rate_limit_rpm, total_limit, total_requests,
            is_active, created_at, expires_at, last_used_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, NULL, ?)
        """, (
            key_id, name, key_digest, prefix, masked,
            scopes_str, rate_limit_rpm, total_limit,
            created_at, expires_at, created_by
        ))
        conn.commit()

    return {
        "key_id": key_id,
        "name": name,
        "api_key": raw_key,  # Raw secret shown only on generation
        "masked_key": masked,
        "prefix": prefix,
        "scopes": [s for s in scopes_str.split(",") if s],
        "rate_limit_rpm": rate_limit_rpm,
        "total_limit": total_limit,
        "created_at": created_at,
        "expires_at": expires_at,
        "created_by": created_by,
        "is_active": True
    }


def validate_api_key(
    raw_key: str,
    required_scope: Optional[str] = None,
    client_ip: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Validate an incoming raw API key against database.
    Checks:
      1. Key exists
      2. Key is active (not revoked)
      3. Key is not expired
      4. Key has required scope (if specified)
      5. Rate limit (RPM sliding window)
      6. Total quota limit (if configured)

    Returns:
      (is_valid: bool, error_detail: Optional[str], key_record: Optional[Dict])
    """
    if not raw_key or not isinstance(raw_key, str):
        return False, "Missing or invalid API key.", None

    clean_key = raw_key.strip()
    if clean_key.lower().startswith("bearer "):
        clean_key = clean_key[7:].strip()

    digest = hash_key(clean_key)

    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE key_hash = ?", (digest,)).fetchone()
        if not row:
            return False, "Invalid API key.", None

        record = dict(row)

    # 1. Active status check
    if not record.get("is_active"):
        return False, "API key has been revoked.", record

    # 2. Expiration check
    expires_at_str = record.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.now(timezone.utc) >= expires_at:
                return False, "API key has expired.", record
        except Exception:
            pass

    # 3. Scope authorization check
    key_scopes = [s.strip() for s in (record.get("scopes") or "").split(",") if s.strip()]
    if required_scope:
        if "*" not in key_scopes and "admin" not in key_scopes and required_scope not in key_scopes:
            return False, f"API key lacks required scope '{required_scope}'. Granted: {key_scopes}", record

    # 4. Total Quota Check
    total_limit = record.get("total_limit", -1)
    total_requests = record.get("total_requests", 0)
    if total_limit > 0 and total_requests >= total_limit:
        return False, f"API key total usage quota exceeded ({total_requests}/{total_limit}).", record

    # 5. Sliding-Window Rate Limit (RPM)
    rpm_limit = record.get("rate_limit_rpm", 60)
    if rpm_limit > 0:
        now_ts = time.time()
        key_id = record["key_id"]
        with _RPM_LOCK:
            window = _RPM_TRACKER.setdefault(key_id, [])
            # Purge timestamps older than 60 seconds
            window = [ts for ts in window if now_ts - ts < 60.0]
            if len(window) >= rpm_limit:
                _RPM_TRACKER[key_id] = window
                return False, f"Rate limit exceeded: maximum {rpm_limit} requests per minute.", record
            window.append(now_ts)
            _RPM_TRACKER[key_id] = window

    # Update request count & last used timestamp asynchronously/inline
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE api_keys SET total_requests = total_requests + 1, last_used_at = ? WHERE key_id = ?",
                (now_iso, record["key_id"])
            )
            conn.commit()
    except Exception:
        pass

    record["total_requests"] += 1
    record["last_used_at"] = now_iso
    return True, None, record


def record_api_usage(
    key_id: str,
    endpoint: str,
    method: str,
    status_code: int,
    client_ip: Optional[str] = None,
    latency_ms: float = 0.0
):
    """Log an API request event for audit and billing analysis."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with get_db_connection() as conn:
            conn.execute("""
            INSERT INTO api_key_usage (key_id, endpoint, method, status_code, client_ip, timestamp, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (key_id, endpoint, method, status_code, client_ip or "unknown", ts, round(latency_ms, 2)))
            conn.commit()
    except Exception:
        pass


def revoke_api_key(key_id_or_hash: str) -> bool:
    """Revoke an API key by key_id or hash."""
    target = key_id_or_hash.strip()
    with get_db_connection() as conn:
        res = conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE key_id = ? OR key_hash = ?",
            (target, target)
        )
        conn.commit()
        return res.rowcount > 0


def reactivate_api_key(key_id: str) -> bool:
    """Reactivate a previously revoked API key."""
    with get_db_connection() as conn:
        res = conn.execute("UPDATE api_keys SET is_active = 1 WHERE key_id = ?", (key_id.strip(),))
        conn.commit()
        return res.rowcount > 0


def delete_api_key(key_id: str) -> bool:
    """Permanently delete an API key and its usage records."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM api_key_usage WHERE key_id = ?", (key_id.strip(),))
        res = conn.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id.strip(),))
        conn.commit()
        return res.rowcount > 0


def list_api_keys(active_only: bool = False) -> List[Dict[str, Any]]:
    """List stored API keys with masked representation (raw secret never exposed)."""
    with get_db_connection() as conn:
        query = "SELECT * FROM api_keys"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d.pop("key_hash", None)  # Never leak hash to caller
            d["scopes"] = [s.strip() for s in (d.get("scopes") or "").split(",") if s.strip()]
            d["is_active"] = bool(d.get("is_active"))
            result.append(d)
        return result


def get_api_key_by_id(key_id: str) -> Optional[Dict[str, Any]]:
    """Get metadata and usage summary for a single API key."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE key_id = ?", (key_id.strip(),)).fetchone()
        if not row:
            return None
        d = dict(row)
        d.pop("key_hash", None)
        d["scopes"] = [s.strip() for s in (d.get("scopes") or "").split(",") if s.strip()]
        d["is_active"] = bool(d.get("is_active"))
        return d


def get_api_key_usage_stats(key_id: str, limit: int = 50) -> Dict[str, Any]:
    """Retrieve recent usage requests and aggregated stats for a key."""
    with get_db_connection() as conn:
        key_row = conn.execute("SELECT * FROM api_keys WHERE key_id = ?", (key_id.strip(),)).fetchone()
        if not key_row:
            return {"error": "API key not found"}

        recent = conn.execute(
            "SELECT * FROM api_key_usage WHERE key_id = ? ORDER BY id DESC LIMIT ?",
            (key_id.strip(), limit)
        ).fetchall()

        stats = conn.execute("""
        SELECT
            COUNT(*) as total_logged,
            AVG(latency_ms) as avg_latency_ms,
            SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as error_count
        FROM api_key_usage WHERE key_id = ?
        """, (key_id.strip(),)).fetchone()

        return {
            "key_id": key_id,
            "name": key_row["name"],
            "masked_key": key_row["masked_key"],
            "total_requests": key_row["total_requests"],
            "avg_latency_ms": round(stats["avg_latency_ms"] or 0.0, 2),
            "error_count": stats["error_count"] or 0,
            "recent_requests": [dict(r) for r in recent]
        }
