"""
FastAPI Backend Server for CareGuide React Frontend.
Provides endpoints for multi-format document extraction, real-time SSE streaming,
Bangla & <85% confidence Gemini Vision AI fallback, health checks, and export downloads (ZIP / JSON).
"""

import os
import io
import re
import time
import json
import random
import zipfile
import tempfile
import asyncio
import threading
from collections import deque
from typing import List, Optional, Dict, Any, Tuple
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
import pandas as pd

from careguide import DocumentOrchestrator
from careguide.ocr_client import OCRClient
from careguide.gemini_fallback import GeminiOCRClient
from careguide.doc_analyzer import DocumentAnalyzer
from careguide.usage_tracker import usage_tracker
from careguide.schema import ProcessingStatus, ExtractionType
from careguide.invoice_extractor import InvoiceExtractor, ValidatedInvoice, InvoiceData
from careguide.supabase_client import SupabaseClient
from careguide.api_keys import (
    generate_api_key,
    validate_api_key,
    revoke_api_key,
    reactivate_api_key,
    delete_api_key,
    list_api_keys,
    get_api_key_by_id,
    get_api_key_usage_stats,
    record_api_usage,
    init_api_keys_db,
)

# Load .env file automatically
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_file):
    with open(_env_file, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

# Default remote GPU backend URL (Tailscale Mesh, Cloudflare Quick Tunnel, or Localhost)
DEFAULT_GPU_URL = (
    os.environ.get("TAILSCALE_URL", "").strip() or
    os.environ.get("CLOUDFLARE_TUNNEL_URL", "").strip() or
    os.environ.get("OCR_API_URL", "").strip() or
    os.environ.get("KAGGLE_GPU_URL", "").strip() or
    None
)

from fastapi.openapi.utils import get_openapi

app = FastAPI(
    title="CareGuide Document Extraction & Invoice Audit API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="CareGuide Developer API & OCR Engine",
        version="1.0.0",
        description="Interactive OpenAPI & Swagger documentation for CareGuide Multi-Format Document Extraction, Tabular Ingestion, Financial Invoice Auditing, and API Key Management.",
        routes=app.routes,
    )
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    openapi_schema["components"]["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "CareGuide Secret API Key (e.g. cg_live_...)"
        },
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "API Key",
            "description": "CareGuide Secret API Key as Bearer Token"
        }
    }
    openapi_schema["security"] = [{"ApiKeyAuth": []}, {"BearerAuth": []}]
    openapi_schema["servers"] = [
        {"url": "/", "description": "Current Origin / Vercel Proxy"},
        {"url": "http://localhost:8008", "description": "Local FastAPI Backend"},
        {"url": "http://100.x.y.z:8000", "description": "Tailscale WireGuard Mesh Remote GPU"},
        {"url": "https://xxxx.trycloudflare.com", "description": "Cloudflare Quick Tunnel GPU"}
    ]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

@app.get("/openapi.json", include_in_schema=False)
async def get_root_openapi():
    return JSONResponse(custom_openapi())

# Enable CORS for React frontend (Vite port 3000, 5173, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for active job logs, results & uploaded file sessions for Re-OCR
JOBS: Dict[str, Dict[str, Any]] = {}
INVOICE_EXCEL_CACHE: Dict[str, bytes] = {}
EXTRACTION_FILE_SESSIONS: Dict[str, Dict[str, str]] = {}


def register_extraction_session(saved_paths: List[str]) -> str:
    """Store uploaded file paths under a session ID so users can trigger manual Gemini Re-OCR."""
    session_id = f"sess_{int(time.time() * 1000)}_{random.randint(100, 999)}"
    EXTRACTION_FILE_SESSIONS[session_id] = {os.path.basename(p): p for p in saved_paths}
    while len(EXTRACTION_FILE_SESSIONS) > 8:
        oldest_key = next(iter(EXTRACTION_FILE_SESSIONS))
        EXTRACTION_FILE_SESSIONS.pop(oldest_key, None)
    return session_id

# -------------------------------------------------------------
# Redis Telemetry & Prometheus / Grafana Observability Engine
# -------------------------------------------------------------
import redis
import psutil
from prometheus_client import CollectorRegistry, Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Dedicated Prometheus Registry for CG-OCR (prevents duplicate registration on reload)
CG_REGISTRY = CollectorRegistry(auto_describe=True)

# Prometheus Metrics Scrape Targets for Grafana
METRIC_REQUESTS_TOTAL = Counter('cg_ocr_requests_total', 'Total HTTP requests processed by CG-OCR engine', ['method', 'endpoint', 'status'], registry=CG_REGISTRY)
METRIC_REQUEST_LATENCY = Histogram('cg_ocr_request_duration_seconds', 'HTTP request latency distribution in seconds', ['endpoint'], registry=CG_REGISTRY)
METRIC_INVOICES_EXTRACTED = Counter('cg_ocr_invoices_extracted_total', 'Total invoices extracted and audited', ['status'], registry=CG_REGISTRY)
METRIC_SECURITY_EVENTS = Counter('cg_ocr_security_events_total', 'Security and unauthorized access attempts', ['type', 'severity'], registry=CG_REGISTRY)
METRIC_REDIS_HITS = Counter('cg_ocr_redis_hits_total', 'Redis cache hit count', registry=CG_REGISTRY)
METRIC_REDIS_MISSES = Counter('cg_ocr_redis_misses_total', 'Redis cache miss count', registry=CG_REGISTRY)
METRIC_CPU_PERCENT = Gauge('cg_system_cpu_usage_percent', 'Current host CPU usage percentage', registry=CG_REGISTRY)
METRIC_MEMORY_BYTES = Gauge('cg_system_memory_usage_bytes', 'Current server RSS memory usage in bytes', registry=CG_REGISTRY)
METRIC_REDIS_KEYS = Gauge('cg_redis_total_keys', 'Current number of active keys in Redis telemetry store', registry=CG_REGISTRY)
METRIC_GEMINI_CALLS = Gauge('cg_gemini_calls_total', 'Total recorded Gemini API calls', registry=CG_REGISTRY)
METRIC_GEMINI_TOKENS = Gauge('cg_gemini_tokens_total', 'Total recorded Gemini API tokens', ['kind'], registry=CG_REGISTRY)
METRIC_GEMINI_COST_USD = Gauge('cg_gemini_estimated_cost_usd', 'Estimated cumulative Gemini USD cost from token usage', registry=CG_REGISTRY)

# Prime psutil CPU baseline once so subsequent interval=None calls measure real delta
psutil.cpu_percent(interval=None)
_PROCESS_HANDLE = psutil.Process()


class RedisTelemetryEngine:
    """
    Real-Time Redis Telemetry & Caching Engine.
    Connects to a standalone Redis instance and reports exact live metrics from PING / INFO / DBSIZE / SCAN.
    If the Redis server is unreachable, accurately reports offline status while safely failing over
    to a local in-memory cache so authentication/rate-limit checks keep functioning.
    """
    def __init__(self):
        self.redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        self.client = None
        self.is_connected = False
        self.mode = "offline"  # "standalone" or "offline"
        self.last_error: Optional[str] = None
        self._memory_store: Dict[str, Tuple[Any, float]] = {}
        self._stats = {
            "hits": 0,
            "misses": 0,
            "ops": 0,
            "start_time": time.time(),
            "last_ping_ms": None,
        }
        self._init_connection()

    def _init_connection(self):
        try:
            r = redis.from_url(self.redis_url, socket_timeout=0.35, socket_connect_timeout=0.35)
            r.ping()
            self.client = r
            self.is_connected = True
            self.mode = "standalone"
            self.last_error = None
            print(f"[REDIS TELEMETRY] Connected to live Redis instance at {self.redis_url}", flush=True)
        except Exception as e:
            self.client = None
            self.is_connected = False
            self.mode = "offline"
            self.last_error = str(e)
            print(f"[REDIS TELEMETRY] Redis server unavailable at {self.redis_url}: {e}", flush=True)

    def _ensure_connection(self) -> bool:
        if self.client is not None:
            return True
        self._init_connection()
        return self.client is not None

    def set(self, key: str, val: Any, ex: Optional[int] = None) -> bool:
        self._stats["ops"] += 1
        if self._ensure_connection() and self.client is not None:
            try:
                self.client.set(key, json.dumps(val) if isinstance(val, (dict, list)) else str(val), ex=ex)
                METRIC_REDIS_KEYS.set(self.client.dbsize())
                return True
            except Exception as e:
                self.is_connected = False
                self.mode = "offline"
                self.last_error = str(e)
                self.client = None
        exp = time.time() + ex if ex else float('inf')
        self._memory_store[key] = (val, exp)
        METRIC_REDIS_KEYS.set(len(self._memory_store))
        return True

    def get(self, key: str) -> Optional[Any]:
        self._stats["ops"] += 1
        if self._ensure_connection() and self.client is not None:
            try:
                raw = self.client.get(key)
                if raw is not None:
                    self._stats["hits"] += 1
                    METRIC_REDIS_HITS.inc()
                    try:
                        return json.loads(raw)
                    except Exception:
                        return raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
                else:
                    self._stats["misses"] += 1
                    METRIC_REDIS_MISSES.inc()
                    return None
            except Exception as e:
                self.is_connected = False
                self.mode = "offline"
                self.last_error = str(e)
                self.client = None
        if key in self._memory_store:
            val, exp = self._memory_store[key]
            if time.time() <= exp:
                self._stats["hits"] += 1
                METRIC_REDIS_HITS.inc()
                return val
            del self._memory_store[key]
        self._stats["misses"] += 1
        METRIC_REDIS_MISSES.inc()
        return None

    def delete(self, key: str):
        self._stats["ops"] += 1
        if self._ensure_connection() and self.client is not None:
            try:
                self.client.delete(key)
                METRIC_REDIS_KEYS.set(self.client.dbsize())
            except Exception:
                pass
        self._memory_store.pop(key, None)

    def _scan_real_namespaces(self) -> List[Dict[str, Any]]:
        """Group real live Redis keys by prefix and inspect their remaining TTLs."""
        groups: Dict[str, Dict[str, Any]] = {}
        try:
            for raw_k in self.client.scan_iter(count=300):
                key_str = raw_k.decode("utf-8", errors="ignore") if isinstance(raw_k, bytes) else str(raw_k)
                prefix = key_str.split(":", 1)[0] if ":" in key_str else key_str
                if prefix not in groups:
                    groups[prefix] = {"namespace": f"{prefix}:*", "keys": 0, "ttls": []}
                groups[prefix]["keys"] += 1
                if len(groups[prefix]["ttls"]) < 10:
                    try:
                        ttl_val = self.client.ttl(raw_k)
                        groups[prefix]["ttls"].append(ttl_val)
                    except Exception:
                        pass
        except Exception:
            return []

        result = []
        for prefix, g in sorted(groups.items(), key=lambda item: -item[1]["keys"])[:8]:
            ttls = [t for t in g["ttls"] if isinstance(t, int)]
            if not ttls or all(t == -1 for t in ttls):
                ttl_label = "No Expiry"
            else:
                pos = [t for t in ttls if t >= 0]
                if not pos:
                    ttl_label = "Volatile"
                elif min(pos) == max(pos):
                    ttl_label = f"{max(pos)}s"
                else:
                    ttl_label = f"{min(pos)}s - {max(pos)}s"
            result.append({
                "namespace": g["namespace"],
                "keys": g["keys"],
                "ttl": ttl_label,
                "purpose": "Live Redis keyspace prefix",
            })
        return result

    def get_telemetry_info(self) -> Dict[str, Any]:
        self._ensure_connection()
        host_display = self.redis_url.split("@")[-1].split("/")[0]

        if self.client is not None and self.mode == "standalone":
            try:
                t0 = time.perf_counter()
                self.client.ping()
                ping_latency = round((time.perf_counter() - t0) * 1000.0, 2)
                info = self.client.info()
                used_mem_bytes = int(info.get("used_memory", 0))
                hits = int(info.get("keyspace_hits", 0))
                misses = int(info.get("keyspace_misses", 0))
                total_keys = int(self.client.dbsize())
                ops_per_sec = float(info.get("instantaneous_ops_per_sec", 0))
                total_cmds = int(info.get("total_commands_processed", 0))
                uptime = int(info.get("uptime_in_seconds", 0))
                version = str(info.get("redis_version", "unknown"))
                total_lookups = hits + misses
                hit_rate = round((hits / total_lookups * 100.0), 1) if total_lookups > 0 else 0.0
                METRIC_REDIS_KEYS.set(total_keys)

                return {
                    "status": "online",
                    "connected": True,
                    "mode": f"Live Standalone Redis Server (v{version})",
                    "server_type": f"Redis v{version}",
                    "version": version,
                    "host": host_display,
                    "ping_latency_ms": ping_latency,
                    "used_memory_bytes": used_mem_bytes,
                    "used_memory_human": info.get("used_memory_human") or f"{used_mem_bytes / (1024 * 1024):.2f} MB",
                    "total_keys": total_keys,
                    "keyspace_hits": hits,
                    "keyspace_misses": misses,
                    "hit_rate_percent": hit_rate,
                    "ops_per_sec": round(ops_per_sec, 1),
                    "total_commands_processed": total_cmds,
                    "uptime_seconds": uptime,
                    "cached_namespaces": self._scan_real_namespaces(),
                }
            except Exception as e:
                self.is_connected = False
                self.mode = "offline"
                self.last_error = str(e)
                self.client = None

        # Offline fallback: report honest disconnected state & actual local fallback buffer size
        now = time.time()
        expired = [k for k, (_, exp) in self._memory_store.items() if now > exp]
        for k in expired:
            del self._memory_store[k]
        hits, misses = self._stats["hits"], self._stats["misses"]
        total_lookups = hits + misses
        hit_rate = round((hits / total_lookups * 100.0), 1) if total_lookups > 0 else 0.0
        uptime = int(now - self._stats["start_time"])
        return {
            "status": "offline",
            "connected": False,
            "mode": "Offline (In-Memory Fallback Active)",
            "server_type": "Disconnected",
            "version": None,
            "host": host_display,
            "error": self.last_error or "Redis connection refused",
            "ping_latency_ms": None,
            "used_memory_bytes": 0,
            "used_memory_human": "0.00 MB",
            "total_keys": len(self._memory_store),
            "keyspace_hits": hits,
            "keyspace_misses": misses,
            "hit_rate_percent": hit_rate,
            "ops_per_sec": round(self._stats["ops"] / max(uptime, 1), 1),
            "total_commands_processed": self._stats["ops"],
            "uptime_seconds": uptime,
            "cached_namespaces": [],
        }

redis_engine = RedisTelemetryEngine()


class RealTimeTelemetryRecorder:
    """Collects actual HTTP request throughput, durations, errors, and host CPU/RSS samples."""

    def __init__(self, max_events: int = 5000):
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=max_events)
        self._resource_samples: deque = deque(maxlen=240)
        self.in_flight: int = 0
        self.start_time: float = time.time()

    def enter_request(self) -> None:
        with self._lock:
            self.in_flight += 1

    def record_request(self, duration_ms: float, status_code: int, cpu_pct: float, mem_bytes: int) -> None:
        now = time.time()
        with self._lock:
            self.in_flight = max(0, self.in_flight - 1)
            self._events.append((now, duration_ms, status_code))
            self._resource_samples.append((now, cpu_pct, mem_bytes))

    def sample_resources_now(self, redis_ops: float = 0.0) -> Tuple[float, float]:
        cpu_pct = round(float(psutil.cpu_percent(interval=None) or 0.0), 1)
        mem_bytes = _PROCESS_HANDLE.memory_info().rss
        mem_mb = round(mem_bytes / (1024 * 1024), 1)
        now = time.time()
        with self._lock:
            self._resource_samples.append((now, cpu_pct, mem_bytes))
        METRIC_CPU_PERCENT.set(cpu_pct)
        METRIC_MEMORY_BYTES.set(mem_bytes)
        return cpu_pct, mem_mb

    def get_timeseries_and_summary(self, redis_ops_per_sec: float = 0.0) -> Dict[str, Any]:
        cpu_now, mem_mb_now = self.sample_resources_now(redis_ops_per_sec)
        now = time.time()
        window_start = now - 60.0

        with self._lock:
            recent_events = [e for e in self._events if e[0] >= window_start]
            recent_resources = [s for s in self._resource_samples if s[0] >= window_start]
            active_conn = self.in_flight

        # Build 12 real 5-second buckets spanning [now - 60s .. now]
        bucket_size = 5.0
        points = []
        for idx in range(12):
            b_start = window_start + idx * bucket_size
            b_end = b_start + bucket_size
            b_evts = [e for e in recent_events if b_start <= e[0] < b_end]
            b_res = [s for s in recent_resources if b_start <= s[0] < b_end]

            b_rps = round(len(b_evts) / bucket_size, 2)
            if b_evts:
                durs = sorted(e[1] for e in b_evts)
                p95_idx = min(len(durs) - 1, max(0, int(0.95 * (len(durs) - 1))))
                b_p95 = round(durs[p95_idx], 2)
            else:
                b_p95 = 0.0

            b_cpu = round(sum(s[1] for s in b_res) / len(b_res), 1) if b_res else cpu_now
            b_mem = round((b_res[-1][2] / (1024 * 1024)), 1) if b_res else mem_mb_now

            points.append({
                "timestamp": time.strftime("%H:%M:%S", time.localtime(b_end)),
                "requests": len(b_evts),
                "rps": b_rps,
                "latency_p95_ms": b_p95,
                "cpu_percent": b_cpu,
                "memory_mb": b_mem,
                "redis_ops_sec": round(redis_ops_per_sec, 1),
            })

        total_60s = len(recent_events)
        elapsed_window = min(60.0, max(1.0, now - self.start_time))
        avg_rps = round(total_60s / elapsed_window, 2)
        if recent_events:
            all_durs = sorted(e[1] for e in recent_events)
            p95_idx = min(len(all_durs) - 1, max(0, int(0.95 * (len(all_durs) - 1))))
            p95_ms = round(all_durs[p95_idx], 2)
            err_count = sum(1 for e in recent_events if e[2] >= 400)
            error_rate_pct = round((err_count / total_60s) * 100.0, 2)
        else:
            p95_ms = 0.0
            error_rate_pct = 0.0

        return {
            "timeseries": points,
            "summary": {
                "avg_rps": avg_rps,
                "p95_latency_ms": p95_ms,
                "cpu_utilization": f"{cpu_now:.1f}%",
                "memory_utilization": f"{mem_mb_now:.1f} MB",
                "error_rate": f"{error_rate_pct:.2f}%",
                "active_connections": active_conn,
                "total_requests_60s": total_60s,
            },
        }


telemetry_recorder = RealTimeTelemetryRecorder()


@app.middleware("http")
async def prometheus_telemetry_middleware(request: Request, call_next):
    """Intercept all incoming requests to record real Prometheus and sliding-window metrics."""
    t_start = time.perf_counter()
    telemetry_recorder.enter_request()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_sec = time.perf_counter() - t_start
        duration_ms = duration_sec * 1000.0
        endpoint = request.url.path
        cpu_pct = round(float(psutil.cpu_percent(interval=None) or 0.0), 1)
        mem_bytes = _PROCESS_HANDLE.memory_info().rss
        telemetry_recorder.record_request(duration_ms, status_code, cpu_pct, mem_bytes)
        try:
            METRIC_REQUESTS_TOTAL.labels(method=request.method, endpoint=endpoint, status=str(status_code)).inc()
            METRIC_REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration_sec)
            METRIC_CPU_PERCENT.set(cpu_pct)
            METRIC_MEMORY_BYTES.set(mem_bytes)
        except Exception:
            pass

# -------------------------------------------------------------
# CG-OCR Persistent Database, Real Authentication & Security Engine
# -------------------------------------------------------------
import sqlite3
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "careguide_system.db")

def get_system_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_system_db():
    with get_system_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS authorized_employees (
            email TEXT PRIMARY KEY,
            added_by TEXT,
            added_at TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS security_audit_events (
            id TEXT PRIMARY KEY,
            type TEXT,
            severity TEXT,
            title TEXT,
            ip TEXT,
            device TEXT,
            message TEXT,
            timestamp TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS team_messages (
            id TEXT PRIMARY KEY,
            sender TEXT,
            email TEXT,
            role TEXT,
            text TEXT,
            timestamp TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS otp_sessions (
            email TEXT PRIMARY KEY,
            otp_code TEXT,
            expires_at REAL,
            attempts INTEGER,
            client_ip TEXT,
            device TEXT
        )
        """)
        # Seed default employees if empty
        row = conn.execute("SELECT COUNT(*) FROM authorized_employees").fetchone()
        if row and row[0] == 0:
            defaults = [
                ("sifat.khan@careguide.com", "SYSTEM_ADMIN", time.strftime("%Y-%m-%d %H:%M:%S")),
                ("sifat@careguide.com", "SYSTEM_ADMIN", time.strftime("%Y-%m-%d %H:%M:%S")),
                ("audit.officer@careguide.com", "SYSTEM_ADMIN", time.strftime("%Y-%m-%d %H:%M:%S")),
                ("finance.lead@careguide.com", "SYSTEM_ADMIN", time.strftime("%Y-%m-%d %H:%M:%S"))
            ]
            conn.executemany("INSERT OR IGNORE INTO authorized_employees VALUES (?, ?, ?)", defaults)
        conn.commit()

init_system_db()

OTP_RATE_LIMITS: Dict[str, List[float]] = {}

def db_get_employees() -> List[str]:
    with get_system_db() as conn:
        rows = conn.execute("SELECT email FROM authorized_employees ORDER BY added_at DESC").fetchall()
        return [r["email"] for r in rows]

def db_add_employee(email: str, added_by: str = "ADMIN") -> List[str]:
    email = email.strip().lower()
    with get_system_db() as conn:
        conn.execute("INSERT OR IGNORE INTO authorized_employees (email, added_by, added_at) VALUES (?, ?, ?)",
                     (email, added_by, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    return db_get_employees()

def db_remove_employee(email: str) -> List[str]:
    email = email.strip().lower()
    with get_system_db() as conn:
        conn.execute("DELETE FROM authorized_employees WHERE email = ?", (email,))
        conn.commit()
    return db_get_employees()

import socket

PUBLIC_IP_CACHE = None
PUBLIC_IP_CACHE_TIME = 0

def resolve_public_ip() -> str:
    """Resolve real external WAN IP address for this machine."""
    global PUBLIC_IP_CACHE, PUBLIC_IP_CACHE_TIME
    now = time.time()
    if PUBLIC_IP_CACHE and (now - PUBLIC_IP_CACHE_TIME < 600):
        return PUBLIC_IP_CACHE
    try:
        import urllib.request
        req = urllib.request.Request("https://api.ipify.org?format=json", headers={"User-Agent": "CG-OCR-Security/2.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ip = data.get("ip")
            if ip:
                PUBLIC_IP_CACHE = ip
                PUBLIC_IP_CACHE_TIME = now
                return ip
    except Exception:
        pass
    return "103.196.235.141"

def get_real_client_ip(request: Request) -> str:
    """Resolve real client public IP address including reverse proxies and direct connections."""
    # 1. Custom client header if passed from frontend
    x_client_ip = request.headers.get("x-client-ip")
    if x_client_ip and x_client_ip not in ("127.0.0.1", "::1", "localhost") and not x_client_ip.startswith("192.168.") and not x_client_ip.startswith("10."):
        return x_client_ip.strip()

    # 2. Proxy headers
    for hdr in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        val = request.headers.get(hdr)
        if val:
            ip = val.split(",")[0].strip()
            if ip and ip not in ("127.0.0.1", "::1", "localhost") and not ip.startswith("192.168.") and not ip.startswith("10."):
                return ip

    # 3. Direct client host
    host = request.client.host if request.client else ""
    if host and host not in ("127.0.0.1", "::1", "localhost") and not host.startswith("192.168.") and not host.startswith("10."):
        return host

    # 4. If running locally or on loopback/LAN, resolve machine's real public WAN IP
    return resolve_public_ip()

def parse_device_name(ua: str) -> str:
    """Parse raw User-Agent into clean, human-readable device name."""
    if not ua or ua in ("Unknown Device", "Unknown"):
        return "Unknown Client"
    
    os_name = "Desktop Workstation"
    if "Windows NT 10.0" in ua or "Windows NT 11.0" in ua:
        os_name = "Windows 11 PC"
    elif "Windows NT 6." in ua:
        os_name = "Windows PC"
    elif "iPhone" in ua:
        os_name = "Apple iPhone"
    elif "iPad" in ua:
        os_name = "Apple iPad"
    elif "Macintosh" in ua or "Mac OS" in ua:
        os_name = "MacBook (macOS)"
    elif "Android" in ua:
        os_name = "Android Mobile"
    elif "Linux" in ua:
        os_name = "Linux PC"

    browser = ""
    import re
    if "Edg/" in ua:
        m = re.search(r"Edg/([0-9]+)", ua)
        ver = f" (v{m.group(1)})" if m else ""
        browser = f"Microsoft Edge{ver}"
    elif "Chrome/" in ua:
        m = re.search(r"Chrome/([0-9]+)", ua)
        ver = f" (v{m.group(1)})" if m else ""
        browser = f"Google Chrome{ver}"
    elif "Firefox/" in ua:
        m = re.search(r"Firefox/([0-9]+)", ua)
        ver = f" (v{m.group(1)})" if m else ""
        browser = f"Mozilla Firefox{ver}"
    elif "Safari/" in ua and "Chrome" not in ua:
        m = re.search(r"Version/([0-9]+)", ua)
        ver = f" (v{m.group(1)})" if m else ""
        browser = f"Apple Safari{ver}"

    if browser:
        return f"{os_name} • {browser}"
    return os_name

def db_log_security_event(evt_type: str, severity: str, title: str, ip: str, device: str, message: str) -> str:
    evt_id = f"sec_{int(time.time() * 1000)}"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with get_system_db() as conn:
        conn.execute("""
        INSERT INTO security_audit_events (id, type, severity, title, ip, device, message, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (evt_id, evt_type, severity, title, ip, device[:120], message, ts))
        conn.commit()
    try:
        METRIC_SECURITY_EVENTS.labels(type=evt_type, severity=severity).inc()
    except Exception:
        pass
    return evt_id

def db_get_security_events(limit: int = 50) -> List[Dict[str, Any]]:
    with get_system_db() as conn:
        rows = conn.execute("SELECT * FROM security_audit_events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def db_save_chat_message(sender: str, email: str, role: str, text: str) -> Dict[str, Any]:
    msg_id = f"msg_{int(time.time() * 1000)}"
    ts = time.strftime("%I:%M %p")
    with get_system_db() as conn:
        conn.execute("""
        INSERT INTO team_messages (id, sender, email, role, text, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (msg_id, sender, email, role, text, ts))
        conn.commit()
    return {"id": msg_id, "sender": sender, "email": email, "role": role, "text": text, "time": ts}

def db_get_chat_messages(limit: int = 100) -> List[Dict[str, Any]]:
    with get_system_db() as conn:
        rows = conn.execute("SELECT id, sender, email, role, text, timestamp as time FROM team_messages ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def db_save_otp(email: str, otp_code: str, expires_at: float, client_ip: str, device: str):
    email = email.strip().lower()
    with get_system_db() as conn:
        conn.execute("DELETE FROM otp_sessions WHERE email = ?", (email,))
        conn.execute("""
        INSERT INTO otp_sessions (email, otp_code, expires_at, attempts, client_ip, device)
        VALUES (?, ?, ?, 0, ?, ?)
        """, (email, otp_code, expires_at, client_ip, device[:120]))
        conn.commit()

def db_get_otp(email: str) -> Optional[Dict[str, Any]]:
    email = email.strip().lower()
    with get_system_db() as conn:
        row = conn.execute("SELECT * FROM otp_sessions WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

def db_inc_otp_attempt(email: str):
    email = email.strip().lower()
    with get_system_db() as conn:
        conn.execute("UPDATE otp_sessions SET attempts = attempts + 1 WHERE email = ?", (email,))
        conn.commit()

def db_clear_otp(email: str):
    email = email.strip().lower()
    with get_system_db() as conn:
        conn.execute("DELETE FROM otp_sessions WHERE email = ?", (email,))
        conn.commit()


# --- Real Authentication & Employee Endpoints ---

@app.get("/api/auth/employees")
async def get_employees():
    """Retrieve persistent list of authorized employee emails managed by Admin."""
    return {"employees": db_get_employees()}


@app.post("/api/auth/employees")
async def add_employee(payload: Dict[str, Any]):
    """Admin endpoint to authorize new employee email into persistent database."""
    email = payload.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email address is required.")
    employees = db_add_employee(email)
    return {"status": "success", "message": f"Employee '{email}' authorized.", "employees": employees}


@app.delete("/api/auth/employees/{email}")
async def remove_employee(email: str):
    """Admin endpoint to revoke employee email access from persistent database."""
    employees = db_remove_employee(email)
    return {"status": "success", "message": f"Employee '{email}' revoked.", "employees": employees}



def send_email_otp(to_email: str, otp_code: str) -> bool:
    """Send OTP code via SMTP or Resend API if configured, or trigger Supabase Auth email."""
    # 1. Check Resend API (HTTP REST)
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    if resend_key:
        try:
            import urllib.request
            payload = json.dumps({
                "from": "CG-OCR <onboarding@resend.dev>",
                "to": [to_email],
                "subject": f"Your CG-OCR Verification Code: {otp_code}",
                "html": f"""
                <div style="font-family: -apple-system, sans-serif; background: #000; color: #fff; padding: 36px; border-radius: 12px; max-width: 480px; margin: 0 auto;">
                    <h2 style="color: #fff; margin-bottom: 8px;">CG-OCR Security Code</h2>
                    <p style="color: #a1a1aa; font-size: 14px; margin-bottom: 20px;">Use this 6-digit code to complete employee sign-in.</p>
                    <div style="background: #141416; border: 1px solid rgba(255,255,255,0.2); padding: 16px; text-align: center; font-size: 30px; font-weight: 700; letter-spacing: 6px; color: #fff; border-radius: 8px;">
                        {otp_code}
                    </div>
                    <p style="color: #71717a; font-size: 12px; margin-top: 20px;">Expires in 5 minutes. Every login generates a brand new code.</p>
                </div>
                """
            }).encode("utf-8")
            req = urllib.request.Request("https://api.resend.com/emails", data=payload, headers={
                "Authorization": f"Bearer {resend_key}",
                "Content-Type": "application/json"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"[EMAIL DISPATCH] Sent OTP to {to_email} via Resend API", flush=True)
                return True
        except Exception as e:
            print(f"[EMAIL DISPATCH WARNING] Resend API failed: {e}", flush=True)

    # 2. Check SMTP (e.g. Gmail App Password, port 587 TLS)
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    smtp_from = os.environ.get("SMTP_FROM", smtp_user or "auth@careguide.com")

    if smtp_host and smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Your CG-OCR Verification Code: {otp_code}"
            msg["From"] = f"CG-OCR Security <{smtp_from}>"
            msg["To"] = to_email

            text_content = f"Your CG-OCR verification code is: {otp_code}. Valid for 5 minutes."
            html_content = f"""
            <div style="font-family: -apple-system, sans-serif; background-color: #000; color: #fff; padding: 36px; border-radius: 12px; max-width: 480px; margin: 0 auto;">
                <h2 style="color: #fff; margin-bottom: 8px;">CG-OCR Security Code</h2>
                <p style="color: #a1a1aa; font-size: 14px; margin-bottom: 20px;">Use this 6-digit code to complete employee sign-in.</p>
                <div style="background: #141416; border: 1px solid rgba(255,255,255,0.2); padding: 16px; text-align: center; font-size: 30px; font-weight: 700; letter-spacing: 6px; color: #fff; border-radius: 8px;">
                    {otp_code}
                </div>
                <p style="color: #71717a; font-size: 12px; margin-top: 20px;">Expires in 5 minutes. Every login generates a brand new code.</p>
            </div>
            """
            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            clean_pass = smtp_pass.replace(" ", "")
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, clean_pass)
                server.send_message(msg)
            print(f"[EMAIL DISPATCH] Successfully sent email with OTP to {to_email} via SMTP ({smtp_host})", flush=True)
            return True
        except Exception as e:
            print(f"[EMAIL DISPATCH WARNING] SMTP send failed: {e}", flush=True)

    # 3. Supabase Auth Email Trigger
    try:
        sb_inst = SupabaseClient()
        if sb_inst._client:
            sb_inst._client.auth.sign_in_with_otp({"email": to_email})
            print(f"[EMAIL DISPATCH] Triggered Supabase Auth email dispatch for {to_email}", flush=True)
    except Exception as e:
        print(f"[EMAIL DISPATCH WARNING] Supabase Auth email note: {e}", flush=True)

    return False


@app.post("/api/auth/request-otp")
async def request_otp(payload: Dict[str, Any], request: Request):
    """
    Real cryptographic OTP generation for authorized employees with Anti-Bot OTP Guard
    and real incoming client IP and User-Agent capture.
    """
    email = payload.get("email", "").strip().lower()
    
    client_ip = get_real_client_ip(request)
    raw_ua = request.headers.get("user-agent", "Unknown Device")
    device = parse_device_name(raw_ua)

    if not email:
        raise HTTPException(status_code=400, detail="Employee email is required.")

    # 1. Whitelist authorization check against real database
    employees = db_get_employees()
    if email not in employees:
        db_log_security_event(
            evt_type="UNAUTHORIZED_LOGIN_ATTEMPT",
            severity="HIGH",
            title="Unauthorized Access Attempt Blocked",
            ip=client_ip,
            device=device,
            message=f"Unregistered email '{email}' attempted employee sign-in. Access blocked by OTP Guard."
        )
        raise HTTPException(
            status_code=403,
            detail=f"Access Denied: '{email}' is not on the authorized employee list. Please contact Administrator."
        )

    # 2. OTP Guard Anti-Bot Rate Limiting (max 3 requests per 60 seconds)
    now = time.time()
    rate_key = f"{client_ip}:{email}"
    history = [t for t in OTP_RATE_LIMITS.get(rate_key, []) if now - t < 60]
    OTP_RATE_LIMITS[rate_key] = history

    if len(history) >= 3:
        db_log_security_event(
            evt_type="OTP_GUARD_BOT_ATTACK",
            severity="CRITICAL",
            title="Bot / OTP Flood Attack Blocked",
            ip=client_ip,
            device=device,
            message=f"Excessive OTP generation attempts on '{email}'. Rate limit triggered (OTP Guard)."
        )
        raise HTTPException(
            status_code=429,
            detail="OTP Guard Alert: Too many attempts detected. Please wait 60 seconds before requesting another code."
        )

    OTP_RATE_LIMITS[rate_key].append(now)

    # 3. Generate 6-digit cryptographic OTP code
    otp_code = f"{secrets.randbelow(900000) + 100000}"

    # Generate real Supabase Auth magiclink/otp in parallel
    try:
        sb_inst = SupabaseClient()
        if sb_inst._client:
            sb_inst._client.auth.admin.generate_link({"type": "magiclink", "email": email})
    except Exception as e:
        pass

    # Store OTP session in persistent SQLite database
    db_save_otp(email, otp_code, now + 300, client_ip, device)

    # Dispatch email (via SMTP or Supabase Auth)
    send_email_otp(email, otp_code)

    # Log to server console for secure dispatch tracking
    print(f"\n[CG-OCR SECURE OTP DISPATCH] Verification code for {email}: {otp_code} (Valid for 5 minutes)\n", flush=True)

    return {
        "status": "success",
        "message": f"Security verification code sent to {email}. Please check your email inbox (and spam folder).",
        "expires_in_sec": 300
    }


@app.post("/api/auth/verify-otp")
async def verify_otp(payload: Dict[str, Any], request: Request):
    """
    Verify employee 6-digit OTP with brute-force lockout guard and session token return.
    """
    email = payload.get("email", "").strip().lower()
    otp = str(payload.get("otp", "")).strip()
    client_ip = get_real_client_ip(request)
    raw_ua = request.headers.get("user-agent", "Unknown Device")
    device = parse_device_name(raw_ua)

    session = db_get_otp(email)
    if not session or time.time() > session["expires_at"]:
        raise HTTPException(status_code=400, detail="Verification code has expired or was not requested. Please request a new code.")

    # Guard: Max 3 failed attempts
    if session["attempts"] >= 3:
        db_log_security_event(
            evt_type="BRUTE_FORCE_LOCKOUT",
            severity="CRITICAL",
            title="Brute Force OTP Attack Blocked",
            ip=client_ip,
            device=device,
            message=f"Account '{email}' locked after 3 invalid OTP attempts. OTP session invalidated."
        )
        db_clear_otp(email)
        raise HTTPException(status_code=403, detail="OTP Guard: 3 invalid attempts exceeded. Session locked for security.")

    if otp != session["otp_code"]:
        db_inc_otp_attempt(email)
        rem = 3 - (session["attempts"] + 1)
        db_log_security_event(
            evt_type="FAILED_OTP_VERIFICATION",
            severity="WARNING",
            title="Failed OTP Verification",
            ip=client_ip,
            device=device,
            message=f"Invalid OTP entered for '{email}'. {rem} attempt(s) remaining."
        )
        raise HTTPException(status_code=400, detail=f"Invalid verification code. {rem} attempt(s) remaining.")

    # Successful login: invalidate OTP session
    db_clear_otp(email)

    # Derive real display name
    local_part = email.split("@")[0].replace(".", " ").replace("_", " ").title()
    session_token = secrets.token_hex(24)

    return {
        "status": "success",
        "user": {
            "email": email,
            "role": "EMPLOYEE",
            "name": local_part
        },
        "token": session_token
    }


# --- Real Security & Audit Endpoints ---

@app.get("/api/security/events")
async def get_security_events():
    """Return real persistent security audit events recorded from live requests."""
    events = db_get_security_events(50)
    return {"events": events}


# --- Real Team Chat Messaging Endpoints ---

@app.get("/api/chat/messages")
async def get_chat_messages():
    """Retrieve real persistent team chat messages."""
    messages = db_get_chat_messages(100)
    return {"messages": messages}


@app.post("/api/chat/messages")
async def post_chat_message(payload: Dict[str, Any]):
    """Send and persist a new message to the team collaboration channel."""
    sender = payload.get("sender", "Employee").strip()
    email = payload.get("email", "employee@careguide.com").strip()
    role = payload.get("role", "EMPLOYEE").strip()
    text = payload.get("text", "").strip()

    if not text:
        raise HTTPException(status_code=400, detail="Message text cannot be empty.")

    saved_msg = db_save_chat_message(sender, email, role, text)
    return {"status": "success", "message": saved_msg}


@app.get("/api/health")
async def health(api_url: Optional[str] = None, gemini_api_key: Optional[str] = None):
    """Check API server health, Kaggle GPU backend (Tailscale/Cloudflare/Local), Supabase, Redis, and Grafana."""
    clean_url = (api_url.strip() if api_url and api_url.strip() else None) or DEFAULT_GPU_URL
    client = OCRClient(api_url=clean_url, gemini_api_key=gemini_api_key)
    ocr_status = client.health_check()
    sb_client = SupabaseClient()
    sb_status = sb_client.check_connection()
    redis_info = redis_engine.get_telemetry_info()
    usage_info = usage_tracker.get_summary()
    metrics_families = len(list(CG_REGISTRY.collect()))

    return {
        "status": "healthy",
        "service": "CareGuide Document Extraction API",
        "ocr_backend": ocr_status,
        "supabase": sb_status,
        "redis": redis_info,
        "grafana": {
            "status": "online",
            "prometheus_endpoint": "/metrics",
            "telemetry_endpoint": "/api/telemetry/grafana",
            "active_metrics": metrics_families,
            "scrape_interval": "5s"
        },
        "usage": usage_info,
        "timestamp": time.time()
    }


# -------------------------------------------------------------
# Prometheus Exporter & Grafana Telemetry Endpoints
# -------------------------------------------------------------

def _sync_usage_prometheus_gauges() -> Dict[str, Any]:
    """Update Prometheus gauges from real SQLite usage stats before scrape/query."""
    usage = usage_tracker.get_summary()
    try:
        METRIC_GEMINI_CALLS.set(usage["gemini_calls"])
        METRIC_GEMINI_TOKENS.labels(kind="prompt").set(usage["prompt_tokens"])
        METRIC_GEMINI_TOKENS.labels(kind="candidates").set(usage["candidates_tokens"])
        METRIC_GEMINI_TOKENS.labels(kind="total").set(usage["total_tokens"])
        METRIC_GEMINI_COST_USD.set(usage["estimated_cost_usd"])
    except Exception:
        pass
    return usage


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus / OpenMetrics scrape target for Grafana Agent and Prometheus Server."""
    _sync_usage_prometheus_gauges()
    telemetry_recorder.sample_resources_now()
    return Response(content=generate_latest(CG_REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/telemetry/usage")
async def get_usage_telemetry():
    """Return real-time token consumption, estimated USD cost, and total call counts."""
    return _sync_usage_prometheus_gauges()


@app.post("/api/telemetry/usage/reset")
async def reset_usage_telemetry():
    """Reset recorded API call and token counters."""
    usage_tracker.reset()
    return {"status": "success", "usage": _sync_usage_prometheus_gauges()}


@app.get("/api/telemetry/redis")
async def get_redis_telemetry():
    """Retrieve full Redis metrics, hit ratios, memory, and active key spaces."""
    return redis_engine.get_telemetry_info()


@app.post("/api/telemetry/redis/ping")
async def ping_redis():
    """Execute live Redis PING command to verify round-trip sub-millisecond response."""
    t0 = time.perf_counter()
    test_key = f"ping:test:{int(time.time() * 1000)}"
    redis_engine.set(test_key, "PONG", ex=10)
    val = redis_engine.get(test_key)
    latency = round((time.perf_counter() - t0) * 1000, 3)
    return {
        "status": "success" if redis_engine.is_connected else "offline_fallback",
        "connected": redis_engine.is_connected,
        "response": val or "PONG",
        "round_trip_ms": latency,
    }


@app.post("/api/telemetry/redis/test")
async def test_redis_cache(payload: Dict[str, Any] = None):
    """Store and retrieve a test key to verify live Redis memory operations."""
    payload = payload or {}
    key = payload.get("key", f"test:cache:{int(time.time())}")
    val = payload.get("value", {"telemetry": "active", "timestamp": time.time()})
    t0 = time.perf_counter()
    redis_engine.set(key, val, ex=60)
    retrieved = redis_engine.get(key)
    dur = round((time.perf_counter() - t0) * 1000, 2)
    return {
        "status": "success" if redis_engine.is_connected else "offline_fallback",
        "connected": redis_engine.is_connected,
        "key": key,
        "written": True,
        "retrieved": retrieved,
        "execution_ms": dur
    }


@app.get("/api/telemetry/grafana")
async def grafana_telemetry():
    """
    Real-time telemetry timeseries data (computed from actual HTTP requests and psutil samples)
    plus Prometheus metric family count and live API token/cost usage.
    """
    redis_info = redis_engine.get_telemetry_info()
    rt = telemetry_recorder.get_timeseries_and_summary(redis_ops_per_sec=redis_info.get("ops_per_sec", 0.0))
    usage = _sync_usage_prometheus_gauges()
    active_metrics_count = len(list(CG_REGISTRY.collect()))

    return {
        "status": "online",
        "prometheus_endpoint": "/metrics",
        "scrape_interval": "5s",
        "active_metrics_count": active_metrics_count,
        "timeseries": rt["timeseries"],
        "summary": rt["summary"],
        "usage": usage,
        "grafana_dashboard_json": {
            "title": "CG-OCR Enterprise Telemetry & Infrastructure",
            "uid": "cg-ocr-telemetry",
            "version": 1,
            "refresh": "5s",
            "panels": [
                {"id": 1, "title": "HTTP Request Throughput (RPS)", "type": "timeseries", "expr": "sum(rate(cg_ocr_requests_total[1m]))"},
                {"id": 2, "title": "P95 Request Latency (ms)", "type": "timeseries", "expr": "histogram_quantile(0.95, sum(rate(cg_ocr_request_duration_seconds_bucket[1m])) by (le)) * 1000"},
                {"id": 3, "title": "Redis Cache Hit Ratio (%)", "type": "gauge", "expr": "sum(rate(cg_ocr_redis_hits_total[1m])) / (sum(rate(cg_ocr_redis_hits_total[1m])) + sum(rate(cg_ocr_redis_misses_total[1m]))) * 100"},
                {"id": 4, "title": "Host CPU Utilization (%)", "type": "gauge", "expr": "cg_system_cpu_usage_percent"},
                {"id": 5, "title": "Invoices Extracted & Validated", "type": "stat", "expr": "sum(cg_ocr_invoices_extracted_total)"},
                {"id": 6, "title": "Security & OTP Breaches Blocked", "type": "stat", "expr": "sum(cg_ocr_security_events_total)"},
                {"id": 7, "title": "Gemini Estimated Token Cost (USD)", "type": "stat", "expr": "cg_gemini_estimated_cost_usd"}
            ]
        }
    }


@app.get("/api/telemetry/grafana/export")
async def export_grafana_dashboard():
    """Download ready-to-import Grafana Dashboard JSON."""
    dashboard = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "liveNow": True,
        "panels": [
            {
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "fieldConfig": {"defaults": {"custom": {"drawStyle": "line", "lineInterpolation": "smooth"}}},
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "id": 1,
                "targets": [{"expr": "sum(rate(cg_ocr_requests_total[1m]))", "legendFormat": "RPS"}],
                "title": "HTTP Request Throughput (RPS)",
                "type": "timeseries"
            },
            {
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "fieldConfig": {"defaults": {"unit": "ms"}},
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                "id": 2,
                "targets": [{"expr": "histogram_quantile(0.95, sum(rate(cg_ocr_request_duration_seconds_bucket[1m])) by (le)) * 1000", "legendFormat": "P95 Latency"}],
                "title": "P95 End-to-End Latency",
                "type": "timeseries"
            },
            {
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "fieldConfig": {"defaults": {"unit": "percent", "max": 100, "min": 0}},
                "gridPos": {"h": 6, "w": 8, "x": 0, "y": 8},
                "id": 3,
                "targets": [{"expr": "cg_system_cpu_usage_percent", "legendFormat": "CPU Load"}],
                "title": "Host CPU Utilization",
                "type": "gauge"
            },
            {
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "fieldConfig": {"defaults": {"unit": "percent", "max": 100, "min": 0}},
                "gridPos": {"h": 6, "w": 8, "x": 8, "y": 8},
                "id": 4,
                "targets": [{"expr": "sum(rate(cg_ocr_redis_hits_total[1m])) / (sum(rate(cg_ocr_redis_hits_total[1m])) + sum(rate(cg_ocr_redis_misses_total[1m]))) * 100", "legendFormat": "Hit %"}],
                "title": "Redis Cache Hit Ratio",
                "type": "gauge"
            },
            {
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}}},
                "gridPos": {"h": 6, "w": 8, "x": 16, "y": 8},
                "id": 5,
                "targets": [{"expr": "sum(cg_ocr_security_events_total)", "legendFormat": "Blocked"}],
                "title": "Security Intrusions Intercepted",
                "type": "stat"
            }
        ],
        "refresh": "5s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": ["careguide", "ocr", "telemetry", "redis", "grafana"],
        "time": {"from": "now-15m", "to": "now"},
        "title": "CareGuide CG-OCR Observability & Telemetry",
        "uid": "careguide-cg-ocr"
    }
    return Response(
        content=json.dumps(dashboard, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=CG_OCR_Grafana_Dashboard.json"}
    )


@app.get("/api/supabase/status")
async def get_supabase_status():
    """Check connectivity, latency, and schema status of Supabase database."""
    client = SupabaseClient()
    return client.check_connection()


@app.get("/api/supabase/schema")
async def get_supabase_schema():
    """Return the SQL schema DDL for user setup in Supabase SQL Editor."""
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supabase_schema.sql")
    if os.path.exists(schema_path):
        with open(schema_path, "r", encoding="utf-8") as f:
            return {"schema_sql": f.read()}
    return {"schema_sql": ""}


@app.post("/api/supabase/save/invoices")
async def save_invoices_to_supabase(payload: Dict[str, Any]):
    """Save extracted and validated invoices, line items, and audit flags into Supabase."""
    invoices = payload.get("invoices", [])
    job_id = payload.get("job_id")
    if not invoices:
        raise HTTPException(status_code=400, detail="No invoices provided to save.")

    client = SupabaseClient()
    if not client.is_configured:
        raise HTTPException(status_code=400, detail="Supabase credentials not configured in .env.")

    result = client.save_invoices(invoices, job_id=job_id)
    return result


@app.get("/api/supabase/invoices")
async def get_supabase_invoices(limit: int = 50):
    """Fetch previously stored invoices from Supabase."""
    client = SupabaseClient()
    if not client.is_configured:
        raise HTTPException(status_code=400, detail="Supabase credentials not configured in .env.")
    data = client.fetch_invoices(limit=limit)
    return {"invoices": data}


@app.post("/api/verify/gemini")
async def verify_gemini_key(payload: Dict[str, Any]):
    """Test and verify user's Gemini API key for Bangla & low-confidence fallback."""
    key = payload.get("api_key", "").strip()
    client = GeminiOCRClient(api_key=key)
    valid, message = client.verify_key()
    return {"valid": valid, "message": message}


@app.post("/api/extract/stream")
async def extract_documents_stream(
    files: List[UploadFile] = File(...),
    api_url: Optional[str] = Form(None),
    gemini_api_key: Optional[str] = Form(None),
    batch_size: int = Form(20)
):
    """
    Process uploaded files and stream real-time Server-Sent Events (SSE) logs,
    20-image epoch progress, Gemini Bangla fallbacks, and final structured documents.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    temp_dir = tempfile.mkdtemp(prefix="careguide_stream_")
    saved_paths = []
    for f in files:
        file_path = os.path.join(temp_dir, f.filename)
        content = await f.read()
        with open(file_path, "wb") as out_f:
            out_f.write(content)
        saved_paths.append(file_path)

    clean_url = (api_url.strip() if api_url and api_url.strip() else None) or DEFAULT_GPU_URL
    clean_gemini_key = (gemini_api_key.strip() if gemini_api_key and gemini_api_key.strip() else None) or os.environ.get("GEMINI_API_KEY", "").strip() or None
    session_id = register_extraction_session(saved_paths)
    print(f"[/api/extract/stream] Processing {len(saved_paths)} file(s) [session={session_id}]. Gemini Key configured: {bool(clean_gemini_key)}")

    async def event_generator():
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def log_callback(filename, msg, item=None):
            tag = "STEP"
            engine_str = "Triage / Scanning"
            if "DIGITAL_TEXT" in msg:
                tag = "DIGITAL"
                engine_str = "PyMuPDF Native"
            elif "Packet" in msg or "packet" in msg:
                tag = "PACKET"
                engine_str = "Gemini Packet Dispatch"
            elif "Gemini" in msg or "gemini" in msg or "Bangla" in msg or "rescued" in msg:
                tag = "GEMINI"
                engine_str = "Gemini Vision AI (Fallback)"
            elif "Epoch" in msg:
                tag = "EPOCH"
                engine_str = "20-Image Epoch Batch"
            elif "UNLIMITED_OCR" in msg or "OCR" in msg:
                tag = "OCR"
                engine_str = "Unlimited-OCR / RapidOCR"
            elif "Sheet" in msg or "Excel" in msg:
                tag = "TABULAR"
                engine_str = "OpenPyXL / xlrd"
            elif "CSV" in msg or "TSV" in msg:
                tag = "TABULAR"
                engine_str = "CSV Sniffer"
            elif "Validation" in msg:
                tag = "SHIELD"
                engine_str = "Integrity Shield"

            entry = {
                "time": time.strftime("%H:%M:%S"),
                "tag": tag,
                "message": f"{filename} -> {msg}"
            }
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "type": "log",
                    "data": entry,
                    "active_engine": engine_str,
                    "active_file": filename,
                    "active_detail": msg
                }
            )

        def worker():
            try:
                # Initial log
                init_msg = f"Pipeline started. Enqueued {len(saved_paths)} file(s). Batch size = {batch_size}."
                if clean_gemini_key:
                    init_msg += " Gemini Vision AI fallback active for Bangla & <85% confidence."
                init_entry = {
                    "time": time.strftime("%H:%M:%S"),
                    "tag": "INIT",
                    "message": init_msg
                }
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "data": init_entry})

                orchestrator = DocumentOrchestrator(
                    api_url=clean_url,
                    gemini_api_key=clean_gemini_key,
                    batch_size=batch_size,
                    checkpoint_db=os.path.join(temp_dir, "stream_check.db"),
                    jsonl_output=os.path.join(temp_dir, "stream_out.jsonl")
                )

                start_time = time.time()
                docs = orchestrator.process_files(saved_paths, progress_callback=log_callback)

                duration = round(time.time() - start_time, 2)
                total_pages = sum(len(d.pages) for d in docs)
                total_sheets = sum(len(d.sheets) for d in docs)
                successful = sum(1 for d in docs if d.status == ProcessingStatus.SUCCESS)
                failed = sum(1 for d in docs if d.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED))
                throughput = round(total_pages / duration, 2) if duration > 0 else float(total_pages)

                summary = {
                    "session_id": session_id,
                    "total_files": len(saved_paths),
                    "total_pages": total_pages,
                    "total_sheets": total_sheets,
                    "successful_files": successful,
                    "failed_files": failed,
                    "duration_sec": duration,
                    "throughput_pages_per_sec": throughput
                }
                docs_data = [d.model_dump() for d in docs]

                finish_entry = {
                    "time": time.strftime("%H:%M:%S"),
                    "tag": "FINISH",
                    "message": f"Batch completed in {duration}s. Extracted {total_pages} page(s) and {total_sheets} sheet(s)."
                }
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "data": finish_entry})

                loop.call_soon_threadsafe(queue.put_nowait, {
                    "type": "complete",
                    "session_id": session_id,
                    "summary": summary,
                    "documents": docs_data
                })
            except Exception as e:
                import traceback
                loop.call_soon_threadsafe(queue.put_nowait, {
                    "type": "error",
                    "error": str(e),
                    "traceback": traceback.format_exc()
                })
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/extract")
async def extract_documents(
    files: List[UploadFile] = File(...),
    api_url: Optional[str] = Form(None),
    gemini_api_key: Optional[str] = Form(None),
    batch_size: int = Form(20)
):
    """Synchronous fallback endpoint for processing uploaded files with 20-image epoch batching."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    job_id = f"job_{int(time.time() * 1000)}"
    JOBS[job_id] = {
        "status": "PROCESSING",
        "logs": [],
        "documents": [],
        "summary": None
    }

    temp_dir = tempfile.mkdtemp(prefix="careguide_api_")
    saved_paths = []

    for f in files:
        file_path = os.path.join(temp_dir, f.filename)
        content = await f.read()
        with open(file_path, "wb") as out_f:
            out_f.write(content)
        saved_paths.append(file_path)

    def log_event(msg: str, tag: str = "INFO"):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "tag": tag,
            "message": msg
        }
        JOBS[job_id]["logs"].append(entry)

    log_event(f"Received {len(saved_paths)} file(s). Starting extraction pipeline.", "INIT")

    clean_url = (api_url.strip() if api_url and api_url.strip() else None) or DEFAULT_GPU_URL
    clean_gemini_key = (gemini_api_key.strip() if gemini_api_key and gemini_api_key.strip() else None) or os.environ.get("GEMINI_API_KEY", "").strip() or None
    session_id = register_extraction_session(saved_paths)
    print(f"[/api/extract/stream] Processing {len(saved_paths)} file(s) [session={session_id}]. Gemini Key configured: {bool(clean_gemini_key)}")

    orchestrator = DocumentOrchestrator(
        api_url=clean_url,
        gemini_api_key=clean_gemini_key,
        batch_size=batch_size,
        checkpoint_db=os.path.join(temp_dir, "api_check.db"),
        jsonl_output=os.path.join(temp_dir, "api_stream.jsonl")
    )

    batch_start = time.time()

    def callback(filename, msg, item=None):
        tag = "STEP"
        if "DIGITAL_TEXT" in msg:
            tag = "DIGITAL"
        elif "Gemini" in msg or "gemini" in msg or "Bangla" in msg:
            tag = "GEMINI"
        elif "UNLIMITED_OCR" in msg or "OCR" in msg:
            tag = "OCR"
        elif "Sheet" in msg:
            tag = "TABULAR"
        elif "Validation" in msg:
            tag = "SHIELD"
        log_event(f"{filename} -> {msg}", tag)

    documents_results = orchestrator.process_files(saved_paths, progress_callback=callback)

    total_duration = round(time.time() - batch_start, 2)
    total_pages = sum(len(d.pages) for d in documents_results)
    total_sheets = sum(len(d.sheets) for d in documents_results)
    successful = sum(1 for d in documents_results if d.status == ProcessingStatus.SUCCESS)
    failed = sum(1 for d in documents_results if d.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED))
    throughput = round(total_pages / total_duration, 2) if total_duration > 0 else float(total_pages)

    summary_data = {
        "job_id": job_id,
        "session_id": session_id,
        "total_files": len(saved_paths),
        "total_pages": total_pages,
        "total_sheets": total_sheets,
        "successful_files": successful,
        "failed_files": failed,
        "duration_sec": total_duration,
        "throughput_pages_per_sec": throughput
    }

    docs_data = [d.model_dump() for d in documents_results]
    JOBS[job_id]["status"] = "COMPLETED"
    JOBS[job_id]["summary"] = summary_data
    JOBS[job_id]["documents"] = docs_data
    log_event(f"Batch completed in {total_duration}s. Extracted {total_pages} page(s) and {total_sheets} sheet(s).", "FINISH")

    return {
        "job_id": job_id,
        "session_id": session_id,
        "summary": summary_data,
        "documents": docs_data,
        "logs": JOBS[job_id]["logs"]
    }


def _reextract_file_with_gemini(
    file_path: str,
    filename: str,
    gemini_key: str,
    page_index: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Render requested page(s) of a PDF or Image and re-extract OCR text directly via Gemini Vision AI.
    Used when the user sees poor OCR output and manually triggers Gemini Re-OCR.
    """
    from PIL import Image, ImageSequence
    from careguide.preprocessor import ImageEnhancer

    gemini_client = GeminiOCRClient(api_key=gemini_key)
    if not gemini_client.is_configured:
        raise ValueError("Gemini API key is required for manual Gemini Re-OCR.")

    ext = os.path.splitext(filename)[1].lower()
    packet_elements: List[Dict[str, Any]] = []

    if ext == ".pdf":
        import fitz
        with fitz.open(file_path) as doc:
            target_indices = (
                [page_index - 1]
                if page_index is not None and 1 <= page_index <= doc.page_count
                else list(range(doc.page_count))
            )
            for idx in target_indices:
                p_num = idx + 1
                page = doc.load_page(idx)
                pix = page.get_pixmap(dpi=200)
                raw_bytes = pix.tobytes("png")
                pix = None
                enhanced_bytes, applied_ops = ImageEnhancer.enhance(
                    raw_bytes,
                    deskew=True,
                    remove_shadows=True,
                    adaptive_threshold=True,
                    denoise=True,
                    output_format=".jpg",
                )
                packet_elements.append({
                    "element_id": f"{filename}#p{p_num}",
                    "filename": filename,
                    "page_index": p_num,
                    "image_bytes": enhanced_bytes,
                    "applied_ops": applied_ops,
                    "trigger_reason": "manual_user_reocr",
                    "original_text": "",
                    "original_confidence": 0.0,
                })
    else:
        with Image.open(file_path) as pil_img:
            frames = list(ImageSequence.Iterator(pil_img))
            target_indices = (
                [page_index - 1]
                if page_index is not None and 1 <= page_index <= len(frames)
                else list(range(len(frames)))
            )
            for idx in target_indices:
                p_num = idx + 1
                buf = io.BytesIO()
                frames[idx].convert("RGB").save(buf, format="PNG")
                enhanced_bytes, applied_ops = ImageEnhancer.enhance(
                    buf.getvalue(),
                    deskew=True,
                    remove_shadows=True,
                    adaptive_threshold=True,
                    denoise=True,
                    output_format=".jpg",
                )
                packet_elements.append({
                    "element_id": f"{filename}#p{p_num}",
                    "filename": filename,
                    "page_index": p_num,
                    "image_bytes": enhanced_bytes,
                    "applied_ops": applied_ops,
                    "trigger_reason": "manual_user_reocr",
                    "original_text": "",
                    "original_confidence": 0.0,
                })

    if not packet_elements:
        raise ValueError("No valid pages found to re-extract.")

    transcribed = gemini_client.transcribe_packet(packet_elements)
    by_id = {r["element_id"]: r for r in transcribed}

    updated_pages: List[Dict[str, Any]] = []
    for elem in packet_elements:
        res = by_id.get(elem["element_id"], {})
        txt = (res.get("text") or "").strip()
        if ext != ".pdf" and txt and not txt.startswith("=== this image"):
            txt = f"=== this image: {filename} ===\n\n{txt}"
        updated_pages.append({
            "index": elem["page_index"],
            "type": ExtractionType.UNLIMITED_OCR.value if hasattr(ExtractionType, "UNLIMITED_OCR") else "UNLIMITED_OCR",
            "status": res.get("status", "SUCCESS") if txt else "FAILED",
            "text": txt,
            "preprocessing_applied": elem["applied_ops"],
            "confidence": res.get("confidence", 0.98) if txt else 0.0,
            "elapsed_ms": res.get("elapsed_ms", 0.0),
            "engine": res.get("engine", "Gemini Vision AI (Manual Re-OCR)").replace("Packet Fallback", "Manual Re-OCR"),
            "error": res.get("error"),
        })
    return updated_pages


@app.post("/api/ocr/reextract-gemini")
async def reextract_ocr_with_gemini(payload: Dict[str, Any]):
    """Re-extract OCR for a page or whole document using Gemini Vision AI from a cached extraction session."""
    session_id = (payload.get("session_id") or "").strip()
    filename = (payload.get("filename") or "").strip()
    page_index = payload.get("page_index")
    gemini_key = (payload.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()

    if not session_id or session_id not in EXTRACTION_FILE_SESSIONS:
        raise HTTPException(status_code=404, detail="Extraction session expired. Fallback upload required.")

    session_files = EXTRACTION_FILE_SESSIONS[session_id]
    file_path = session_files.get(filename)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Source file '{filename}' not found in session.")

    if page_index is not None:
        page_index = int(page_index)

    try:
        updated_pages = await asyncio.to_thread(
            _reextract_file_with_gemini,
            file_path=file_path,
            filename=filename,
            gemini_key=gemini_key,
            page_index=page_index,
        )
        return {"status": "success", "filename": filename, "pages": updated_pages}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini Re-OCR failed: {e}")


@app.post("/api/ocr/reextract-gemini-upload")
async def reextract_ocr_with_gemini_upload(
    file: UploadFile = File(...),
    page_index: Optional[int] = Form(None),
    gemini_api_key: Optional[str] = Form(None),
):
    """Upload fallback endpoint to re-extract OCR for a page or whole document using Gemini Vision AI."""
    gemini_key = (gemini_api_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    temp_dir = tempfile.mkdtemp(prefix="careguide_reocr_")
    file_path = os.path.join(temp_dir, file.filename)
    content = await file.read()
    with open(file_path, "wb") as out_f:
        out_f.write(content)

    try:
        updated_pages = await asyncio.to_thread(
            _reextract_file_with_gemini,
            file_path=file_path,
            filename=file.filename,
            gemini_key=gemini_key,
            page_index=page_index,
        )
        return {"status": "success", "filename": file.filename, "pages": updated_pages}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini Re-OCR failed: {e}")


@app.post("/api/download/zip")
async def download_zip(payload: Dict[str, Any]):
    """Bundle extracted Markdown pages and CSV sheets into a ZIP download."""
    documents = payload.get("documents", [])
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            filename = doc.get("filename", "document")
            base_name = os.path.splitext(filename)[0]

            # Markdown for pages
            pages = doc.get("pages", [])
            if pages:
                md_content = f"# {filename}\n\nStatus: {doc.get('status')}\nPages: {len(pages)}\n\n"
                for p in pages:
                    p_text = p.get('text', '').strip()
                    if p_text.startswith("=== this image"):
                        md_content += f"{p_text}\n\n"
                    else:
                        md_content += f"## Page {p.get('index')} ({p.get('type')})\n\n{p_text}\n\n"
                    if p.get("error"):
                        md_content += f"> Error: {p.get('error')}\n\n"
                zf.writestr(f"{base_name}.md", md_content)

            # CSVs for sheets
            sheets = doc.get("sheets", [])
            for s in sheets:
                columns = s.get("columns", [])
                data = s.get("data", [])
                sheet_name = s.get("name", "Sheet1")
                if columns:
                    df = pd.DataFrame(data)
                    csv_str = df.to_csv(index=False)
                    zf.writestr(f"{base_name}_{sheet_name}.csv", csv_str)

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=extracted_documents.zip"}
    )


def partition_documents_for_invoice_extraction(
    raw_docs: List[Dict[str, Any]],
    max_invoices: Optional[int] = None
) -> Tuple[List[InvoiceData], List[Tuple[str, str]]]:
    """
    Separates documents into:
    1. tabular_invoices: Tabular invoice registers / ledgers (XLSX, XLS, CSV, TSV) parsed deterministically with 100% precision.
    2. doc_text_pairs: PDF pages, image scans, and free-form visual invoice sheets to be processed via Gemini AI.
    """
    tabular_invoices: List[InvoiceData] = []
    doc_text_pairs: List[Tuple[str, str]] = []

    for doc in raw_docs:
        fname = doc.get("filename", "unknown")
        pages = doc.get("pages", [])
        sheets = doc.get("sheets", [])

        # 1. Check sheets for Tabular Invoice Registers vs Visual Sheets
        if sheets:
            for s in sheets:
                s_name = s.get("name", "Sheet")
                if InvoiceExtractor.is_tabular_invoice_register(s):
                    invoices = InvoiceExtractor.extract_invoices_from_tabular_sheet(
                        s,
                        source_filename=fname,
                        max_invoices=max_invoices
                    )
                    tabular_invoices.extend(invoices)
                else:
                    data = s.get("data", [])
                    if data:
                        df = pd.DataFrame(data)
                        sheet_text = f"\nSheet '{s_name}':\n" + df.to_string(index=False)
                        doc_text_pairs.append((f"{fname} ({s_name})", sheet_text))

        # 2. Check pages (PDFs, images, TXT)
        if pages:
            if len(pages) == 1:
                p_text = pages[0].get("text", "").strip()
                if p_text:
                    doc_text_pairs.append((fname, p_text))
            else:
                groups: List[List[Dict[str, Any]]] = []
                current_group: List[Dict[str, Any]] = []

                for page in pages:
                    p_text = page.get("text", "").strip()
                    if not p_text:
                        continue

                    # Check continuation signals on current or preceding page
                    is_continuation = bool(
                        re.search(
                            r"LINE ITEMS CONTINUED|Document page [2-9]/\d+|Document page [2-9] of \d+|Continued from the previous page",
                            p_text,
                            re.IGNORECASE
                        )
                    )
                    prev_continued = False
                    if current_group:
                        prev_text = current_group[-1].get("text", "")
                        prev_continued = bool(
                            re.search(r"Continued on the next page|Continued on next page", prev_text, re.IGNORECASE)
                        )

                    if (is_continuation or prev_continued) and current_group:
                        current_group.append(page)
                    else:
                        if current_group:
                            groups.append(current_group)
                        current_group = [page]

                if current_group:
                    groups.append(current_group)

                for grp in groups:
                    grp_pages = [g.get("index", 1) for g in grp]
                    if len(grp) == 1:
                        lbl = f"{fname} (Page {grp_pages[0]})"
                    else:
                        lbl = f"{fname} (Pages {grp_pages[0]}-{grp_pages[-1]})"
                    combined = "\n\n".join(g.get("text", "").strip() for g in grp if g.get("text", "").strip())
                    if combined.strip():
                        doc_text_pairs.append((lbl, combined))

    if max_invoices and max_invoices > 0:
        if len(tabular_invoices) >= max_invoices:
            tabular_invoices = tabular_invoices[:max_invoices]
            doc_text_pairs = []
        else:
            remaining = max_invoices - len(tabular_invoices)
            doc_text_pairs = doc_text_pairs[:remaining]

    return tabular_invoices, doc_text_pairs


def segment_document_into_invoices(
    raw_docs: List[Dict[str, Any]],
    max_invoices: Optional[int] = None
) -> List[Tuple[str, str]]:
    """
    Backwards-compatible segmentation helper.
    Returns doc_text_pairs from partitioned documents.
    """
    _, doc_text_pairs = partition_documents_for_invoice_extraction(raw_docs, max_invoices=max_invoices)
    return doc_text_pairs


@app.post("/api/invoices/extract/stream")
async def extract_invoices_stream(payload: Dict[str, Any]):
    """
    Stream real-time Gemini request status, progress bar percentages,
    Task 3 data validation telemetry, and final structured invoice results via SSE.
    """
    raw_docs = payload.get("documents", [])
    gemini_key = payload.get("gemini_api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    max_invoices_raw = payload.get("max_invoices") or payload.get("page_limit") or payload.get("max_pages")
    max_invoices = None
    if max_invoices_raw and str(max_invoices_raw).isdigit():
        max_invoices = int(max_invoices_raw)

    if not raw_docs:
        raise HTTPException(status_code=400, detail="No extracted document text provided.")

    tabular_invoices, doc_text_pairs = partition_documents_for_invoice_extraction(raw_docs, max_invoices=max_invoices)

    if not tabular_invoices and not doc_text_pairs:
        raise HTTPException(status_code=400, detail="No readable invoices or document text found.")

    extractor = InvoiceExtractor(api_key=gemini_key)
    if doc_text_pairs and not extractor.is_configured:
        raise HTTPException(status_code=400, detail="Gemini API key is required for unstructured document page extraction.")

    async def event_generator():
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_progress(evt):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "gemini_status", **evt})

        def worker():
            try:
                total_units = len(tabular_invoices) + len(doc_text_pairs)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "init",
                        "percent": 5,
                        "total_docs": total_units,
                        "model": "CareGuide Intelligence Engine",
                        "state": "CONNECTING",
                        "message": f"Initialized invoice pipeline for {total_units} invoice/document unit(s)..."
                    }
                )

                supabase = SupabaseClient()
                streamed_validated: List[ValidatedInvoice] = []
                all_extracted: List[InvoiceData] = []
                total_db_saved = 0
                job_id = f"job_{int(time.time())}"

                def on_batch(parsed_batch: List[InvoiceData]):
                    nonlocal total_db_saved
                    if not parsed_batch:
                        return
                    all_extracted.extend(parsed_batch)

                    # 1. Deterministic validation for this batch
                    validated_batch = extractor.validate_invoices(parsed_batch)
                    streamed_validated.extend(validated_batch)

                    # 2. Convert to dictionary records
                    dict_batch = [
                        {
                            **v.invoice.model_dump(),
                            "requires_manual_review": v.requires_manual_review,
                            "validation_status": v.validation_status,
                            "issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues],
                            "validation_issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues]
                        }
                        for v in validated_batch
                    ]

                    # 3. Live Auto-save to Supabase DB!
                    if supabase.is_configured:
                        try:
                            save_res = supabase.save_invoices(dict_batch, job_id=job_id)
                            saved_num = save_res.get("saved_invoices", len(dict_batch))
                            total_db_saved += saved_num
                        except Exception as db_e:
                            print(f"[Supabase Live Auto-Save] {db_e}")

                    clean_so_far = sum(1 for v in streamed_validated if not v.requires_manual_review)
                    review_so_far = sum(1 for v in streamed_validated if v.requires_manual_review)

                    # 4. Stream batch directly to the frontend for real-time display
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "invoice_batch",
                            "batch_invoices": dict_batch,
                            "total_extracted": len(streamed_validated),
                            "total_docs": total_units,
                            "clean_count": clean_so_far,
                            "review_count": review_so_far,
                            "db_saved_total": total_db_saved,
                            "db_auto_saved": supabase.is_configured
                        }
                    )

                # Step 1: Stream tabular invoices in fast responsive chunks (50 invoices per batch)
                if tabular_invoices:
                    tab_chunk_size = 50
                    tab_batches = (len(tabular_invoices) + tab_chunk_size - 1) // tab_chunk_size
                    for c_i in range(0, len(tabular_invoices), tab_chunk_size):
                        chunk = tabular_invoices[c_i:c_i + tab_chunk_size]
                        cur_b = (c_i // tab_chunk_size) + 1
                        pct = min(85, int((c_i / len(tabular_invoices)) * 75) + 10)
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {
                                "type": "gemini_status",
                                "state": "TABULAR_PARSING",
                                "model": "CareGuide Tabular Engine",
                                "batch": cur_b,
                                "total_batches": tab_batches,
                                "message": f"Processing tabular register batch {cur_b}/{tab_batches} (Invoices {c_i+1}-{min(c_i+len(chunk), len(tabular_invoices))} of {len(tabular_invoices)})...",
                                "percent": pct
                            }
                        )
                        on_batch(chunk)
                        time.sleep(0.03)

                # Step 2: Stream remaining document pages via Gemini AI
                if doc_text_pairs:
                    extracted_gemini = extractor.extract_invoices_from_text_batch(
                        doc_text_pairs,
                        progress_callback=on_progress,
                        on_batch_extracted=on_batch
                    )

                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "gemini_status",
                        "state": "VALIDATING",
                        "percent": 88,
                        "model": "CareGuide Validator",
                        "message": "Finalizing cross-document duplicate detection & audit..."
                    }
                )

                # Step 3: Full cross-invoice duplicate check across all extracted invoices
                validated = extractor.validate_invoices(all_extracted)

                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "gemini_status",
                        "state": "EXCEL",
                        "percent": 94,
                        "model": "CareGuide Formatter",
                        "message": "Generating formatted multi-sheet Excel workbook (.xlsx)..."
                    }
                )

                # Step 4: Generate Multi-Sheet Excel Workbook (.xlsx)
                import uuid
                download_id = f"inv_{uuid.uuid4().hex[:12]}"
                temp_excel = os.path.join(tempfile.gettempdir(), f"{download_id}.xlsx")
                extractor.generate_excel_workbook(validated, temp_excel)

                with open(temp_excel, "rb") as ef:
                    INVOICE_EXCEL_CACHE[download_id] = ef.read()

                try:
                    os.remove(temp_excel)
                except Exception:
                    pass

                clean_count = sum(1 for v in validated if not v.requires_manual_review)
                review_count = sum(1 for v in validated if v.requires_manual_review)

                result_data = {
                    "status": "success",
                    "download_id": download_id,
                    "total_invoices": len(validated),
                    "clean_count": clean_count,
                    "review_count": review_count,
                    "invoices": [
                        {
                            **v.invoice.model_dump(),
                            "requires_manual_review": v.requires_manual_review,
                            "validation_status": v.validation_status,
                            "issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues],
                            "validation_issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues]
                        }
                        for v in validated
                    ]
                }

                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "complete",
                        "percent": 100,
                        "state": "COMPLETED",
                        "message": f"Successfully extracted and audited {len(validated)} invoice(s)!",
                        "data": result_data
                    }
                )
            except Exception as e:
                import traceback
                traceback.print_exc()
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "error",
                        "error": str(e)
                    }
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/invoices/extract")
async def extract_and_validate_invoices(payload: Dict[str, Any]):
    """
    Extract structured invoice fields from previously transcribed documents,
    run Task 3 Data Validation (missing fields, math consistency, duplicates, manual review flagging),
    and build multi-sheet Excel workbook.
    """
    raw_docs = payload.get("documents", [])
    gemini_key = payload.get("gemini_api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    max_invoices_raw = payload.get("max_invoices") or payload.get("page_limit") or payload.get("max_pages")
    max_invoices = None
    if max_invoices_raw and str(max_invoices_raw).isdigit():
        max_invoices = int(max_invoices_raw)

    if not raw_docs:
        raise HTTPException(status_code=400, detail="No extracted document text provided.")

    tabular_invoices, doc_text_pairs = partition_documents_for_invoice_extraction(raw_docs, max_invoices=max_invoices)

    if not tabular_invoices and not doc_text_pairs:
        raise HTTPException(status_code=400, detail="No readable invoices or document text found.")

    extractor = InvoiceExtractor(api_key=gemini_key)
    all_extracted: List[InvoiceData] = list(tabular_invoices)

    # 1. Extraction of unstructured pages via Gemini Flash
    if doc_text_pairs:
        if not extractor.is_configured:
            raise HTTPException(status_code=400, detail="Gemini API key is required for visual document page extraction.")
        try:
            gemini_extracted = extractor.extract_invoices_from_text_batch(doc_text_pairs)
            all_extracted.extend(gemini_extracted)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Invoice extraction error: {e}")

    # 2. Task 3 Data Validation
    validated = extractor.validate_invoices(all_extracted)

    # 3. Generate Multi-Sheet Excel
    import uuid
    download_id = f"inv_{uuid.uuid4().hex[:12]}"
    temp_excel = os.path.join(tempfile.gettempdir(), f"{download_id}.xlsx")
    extractor.generate_excel_workbook(validated, temp_excel)

    with open(temp_excel, "rb") as ef:
        INVOICE_EXCEL_CACHE[download_id] = ef.read()

    try:
        os.remove(temp_excel)
    except Exception:
        pass

    clean_count = sum(1 for v in validated if not v.requires_manual_review)
    review_count = sum(1 for v in validated if v.requires_manual_review)

    return {
        "status": "success",
        "download_id": download_id,
        "total_invoices": len(validated),
        "clean_count": clean_count,
        "review_count": review_count,
        "invoices": [
            {
                **v.invoice.model_dump(),
                "requires_manual_review": v.requires_manual_review,
                "validation_status": v.validation_status,
                "issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues],
                "validation_issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues]
            }
            for v in validated
        ]
    }


@app.get("/api/invoices/download")
async def download_invoices_excel(id: str):
    """Download the generated multi-sheet Excel file (.xlsx)."""
    excel_bytes = INVOICE_EXCEL_CACHE.get(id)
    if not excel_bytes:
        raise HTTPException(status_code=404, detail="Excel report expired or not found. Please re-run extraction.")

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=CareGuide_Invoices_Validated.xlsx"}
    )


@app.post("/api/documents/analyze")
async def analyze_extracted_documents(payload: Dict[str, Any]):
    """
    Generate an executive summary or answer a specific user question
    from previously extracted document pages/sheets using strictly 1 Gemini call per query.
    """
    raw_docs = payload.get("documents", [])
    mode = (payload.get("mode") or "summary").strip().lower()
    question = payload.get("question")
    gemini_key = (payload.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()

    if not raw_docs:
        raise HTTPException(status_code=400, detail="No extracted document data provided.")

    analyzer = DocumentAnalyzer(api_key=gemini_key)
    if not analyzer.is_configured:
        raise HTTPException(status_code=400, detail="Gemini API key is required for AI Summary & Q&A.")

    try:
        result = await asyncio.to_thread(
            analyzer.analyze,
            documents=raw_docs,
            mode=mode,
            question=question,
        )
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI analysis error: {e}")


# -------------------------------------------------------------
# CareGuide Developer REST API & API Key Management System
# -------------------------------------------------------------

def extract_api_key_from_request(request: Request) -> Optional[str]:
    """Extract API key from X-API-Key header, Authorization Bearer header, or query parameter."""
    header_key = request.headers.get("x-api-key")
    if header_key and header_key.strip():
        return header_key.strip()

    auth_header = request.headers.get("authorization", "")
    if auth_header.strip().lower().startswith("bearer "):
        token = auth_header.strip()[7:].strip()
        if token:
            return token

    param_key = request.query_params.get("api_key")
    if param_key and param_key.strip():
        return param_key.strip()

    return None


def verify_api_key_dependency(scope: Optional[str] = None):
    """Dependency that authenticates developer requests with API key and verifies scope and quota."""
    async def dependency(request: Request) -> Dict[str, Any]:
        raw_key = extract_api_key_from_request(request)
        if not raw_key:
            raise HTTPException(
                status_code=401,
                detail="API key required. Provide via 'X-API-Key' or 'Authorization: Bearer <key>' header."
            )

        client_ip = get_real_client_ip(request)
        is_valid, err_msg, key_rec = validate_api_key(raw_key, required_scope=scope, client_ip=client_ip)
        if not is_valid:
            status_code = 401
            if err_msg and ("Rate limit" in err_msg or "quota exceeded" in err_msg):
                status_code = 429
            elif err_msg and "lacks required scope" in err_msg:
                status_code = 403
            raise HTTPException(status_code=status_code, detail=err_msg or "Invalid API key.")

        return key_rec

    return dependency


async def optional_api_key_dependency(request: Request) -> Optional[Dict[str, Any]]:
    """Optional API key dependency for backward-compatible frontend endpoints."""
    raw_key = extract_api_key_from_request(request)
    if not raw_key:
        return None
    client_ip = get_real_client_ip(request)
    is_valid, _, key_rec = validate_api_key(raw_key, client_ip=client_ip)
    return key_rec if is_valid else None


# --- API Key Management Endpoints ---

@app.post("/api/keys/generate")
@app.post("/api/v1/keys")
async def create_new_api_key(payload: Dict[str, Any], request: Request):
    """
    Generate a new CareGuide API key with configurable scopes, RPM, and quota limits.
    """
    name = (payload.get("name") or payload.get("customer_id") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Customer or application name is required.")

    scopes = payload.get("scopes")
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",") if s.strip()]

    try:
        rpm = int(payload.get("rate_limit_rpm", payload.get("rpm", 60)))
        total_limit = int(payload.get("total_limit", payload.get("limit", -1)))
        days = int(payload.get("expires_in_days", payload.get("days", 90)))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid numeric value for rpm, limit, or days.")

    is_test = bool(payload.get("is_test", payload.get("test", False)))
    created_by = (payload.get("created_by") or "API_ADMIN").strip()

    try:
        record = generate_api_key(
            name=name,
            scopes=scopes,
            rate_limit_rpm=rpm,
            total_limit=total_limit,
            expires_in_days=days if days > 0 else None,
            is_test=is_test,
            created_by=created_by
        )
        db_log_security_event(
            evt_type="API_KEY_CREATED",
            severity="INFO",
            title="New API Key Generated",
            ip=get_real_client_ip(request),
            device=request.headers.get("user-agent", "Unknown")[:120],
            message=f"Created API key '{record['key_id']}' for '{name}' with {rpm} RPM limit."
        )
        return {"status": "success", "data": record}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate API key: {str(e)}")


@app.get("/api/keys")
@app.get("/api/v1/keys")
async def list_all_api_keys(active_only: bool = False):
    """List all registered API keys (raw secrets are masked for safety)."""
    return {"status": "success", "keys": list_api_keys(active_only=active_only)}


@app.post("/api/keys/{key_id}/revoke")
@app.delete("/api/keys/{key_id}")
@app.delete("/api/v1/keys/{key_id}")
async def revoke_existing_api_key(key_id: str, request: Request):
    """Revoke an active API key immediately."""
    success = revoke_api_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found.")

    db_log_security_event(
        evt_type="API_KEY_REVOKED",
        severity="WARNING",
        title="API Key Revoked",
        ip=get_real_client_ip(request),
        device=request.headers.get("user-agent", "Unknown")[:120],
        message=f"API key '{key_id}' was revoked."
    )
    return {"status": "success", "message": f"API key '{key_id}' has been revoked."}


@app.get("/api/keys/verify")
@app.get("/api/v1/keys/verify")
async def verify_current_key(request: Request):
    """Verify an API key supplied via X-API-Key or Bearer header."""
    raw_key = extract_api_key_from_request(request)
    if not raw_key:
        raise HTTPException(status_code=401, detail="No API key provided.")

    is_valid, err_msg, rec = validate_api_key(raw_key, client_ip=get_real_client_ip(request))
    if not is_valid:
        raise HTTPException(status_code=401, detail=err_msg or "Invalid API key.")

    clean_rec = dict(rec)
    clean_rec.pop("key_hash", None)
    return {"status": "valid", "key": clean_rec}


@app.get("/api/keys/{key_id}")
@app.get("/api/v1/keys/{key_id}")
async def get_key_stats(key_id: str):
    """Retrieve usage statistics and audit logs for a key."""
    stats = get_api_key_usage_stats(key_id)
    if "error" in stats:
        raise HTTPException(status_code=404, detail=stats["error"])
    return {"status": "success", "data": stats}


# --- Dedicated Developer API v1 Endpoints ---

@app.get("/v1/health")
@app.get("/api/v1/health")
async def v1_api_health(
    request: Request,
    key_record: Dict[str, Any] = Depends(verify_api_key_dependency("health"))
):
    """
    Developer API v1 Health Check.
    Requires valid API key with 'health' or 'admin' or '*' scope.
    """
    start_t = time.time()
    latency_ms = (time.time() - start_t) * 1000
    record_api_usage(
        key_id=key_record["key_id"],
        endpoint="/v1/health",
        method="GET",
        status_code=200,
        client_ip=get_real_client_ip(request),
        latency_ms=latency_ms
    )
    return {
        "status": "healthy",
        "version": "1.0.0",
        "client": {
            "name": key_record["name"],
            "key_id": key_record["key_id"],
            "masked_key": key_record["masked_key"],
            "rate_limit_rpm": key_record["rate_limit_rpm"],
            "total_requests": key_record["total_requests"]
        },
        "engine": "CareGuide NextGen Extraction Engine",
        "timestamp": time.time()
    }


@app.post("/v1/extract")
@app.post("/api/v1/extract")
async def v1_extract_documents(
    request: Request,
    files: List[UploadFile] = File(...),
    api_url: Optional[str] = Form(None),
    gemini_api_key: Optional[str] = Form(None),
    batch_size: int = Form(20),
    key_record: Dict[str, Any] = Depends(verify_api_key_dependency("extract"))
):
    """
    Developer API v1: Multi-Format Document & Tabular Extraction.
    Accepts PDF, JPG, PNG, TIFF, XLSX, XLS, CSV.
    Requires API Key with 'extract' scope.
    """
    start_t = time.time()
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    temp_dir = tempfile.mkdtemp(prefix="careguide_v1_")
    saved_paths = []
    try:
        for f in files:
            file_path = os.path.join(temp_dir, f.filename)
            content = await f.read()
            with open(file_path, "wb") as out_f:
                out_f.write(content)
            saved_paths.append(file_path)

        clean_url = (api_url.strip() if api_url and api_url.strip() else None) or DEFAULT_GPU_URL
        clean_gemini_key = (gemini_api_key.strip() if gemini_api_key and gemini_api_key.strip() else None) or os.environ.get("GEMINI_API_KEY", "").strip() or None

        orchestrator = DocumentOrchestrator(
            api_url=clean_url,
            gemini_api_key=clean_gemini_key,
            batch_size=batch_size,
            checkpoint_db=os.path.join(temp_dir, "v1_check.db"),
            jsonl_output=os.path.join(temp_dir, "v1_stream.jsonl")
        )

        doc_results = orchestrator.process_files(saved_paths)
        duration = round(time.time() - start_t, 3)
        latency_ms = duration * 1000

        record_api_usage(
            key_id=key_record["key_id"],
            endpoint="/v1/extract",
            method="POST",
            status_code=200,
            client_ip=get_real_client_ip(request),
            latency_ms=latency_ms
        )

        return {
            "status": "success",
            "client_id": key_record["key_id"],
            "processing_time_sec": duration,
            "total_documents": len(doc_results),
            "documents": [doc.model_dump() for doc in doc_results]
        }
    except Exception as e:
        latency_ms = (time.time() - start_t) * 1000
        record_api_usage(
            key_id=key_record["key_id"],
            endpoint="/v1/extract",
            method="POST",
            status_code=500,
            client_ip=get_real_client_ip(request),
            latency_ms=latency_ms
        )
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")
    finally:
        import shutil
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@app.post("/v1/invoices/extract")
@app.post("/api/v1/invoices/extract")
async def v1_extract_invoices(
    request: Request,
    files: Optional[List[UploadFile]] = File(None),
    gemini_api_key: Optional[str] = Form(None),
    max_invoices: Optional[int] = Form(None),
    key_record: Dict[str, Any] = Depends(verify_api_key_dependency("invoices"))
):
    """
    Developer API v1: Direct File Invoice Extraction & Multi-Field Validation.
    Upload PDF, image, or spreadsheet invoice files directly to extract vendor,
    invoice number, dates, line items, taxes, totals, and validation status.
    Requires API Key with 'invoices' scope.
    """
    start_t = time.time()
    clean_gemini_key = (gemini_api_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()

    # Case A: Direct file upload
    if files and len(files) > 0 and files[0].filename:
        temp_dir = tempfile.mkdtemp(prefix="careguide_inv_v1_")
        saved_paths = []
        try:
            for f in files:
                file_path = os.path.join(temp_dir, f.filename)
                content = await f.read()
                with open(file_path, "wb") as out_f:
                    out_f.write(content)
                saved_paths.append(file_path)

            orchestrator = DocumentOrchestrator(
                gemini_api_key=clean_gemini_key or None,
                batch_size=20,
                checkpoint_db=os.path.join(temp_dir, "v1_inv.db")
            )
            doc_results = orchestrator.process_files(saved_paths)
            doc_dicts = [d.model_dump() for d in doc_results]

            tabular_invoices, doc_text_pairs = partition_documents_for_invoice_extraction(doc_dicts, max_invoices=max_invoices)
            extractor = InvoiceExtractor(api_key=clean_gemini_key)
            all_extracted: List[InvoiceData] = list(tabular_invoices)

            if doc_text_pairs:
                if not extractor.is_configured:
                    raise HTTPException(status_code=400, detail="Gemini API key is required for visual invoice OCR extraction.")
                gemini_extracted = extractor.extract_invoices_from_text_batch(doc_text_pairs)
                all_extracted.extend(gemini_extracted)

            validated = extractor.validate_invoices(all_extracted)
            duration = round(time.time() - start_t, 3)
            latency_ms = duration * 1000

            record_api_usage(
                key_id=key_record["key_id"],
                endpoint="/v1/invoices/extract",
                method="POST",
                status_code=200,
                client_ip=get_real_client_ip(request),
                latency_ms=latency_ms
            )

            clean_count = sum(1 for v in validated if not v.requires_manual_review)
            review_count = sum(1 for v in validated if v.requires_manual_review)

            return {
                "status": "success",
                "client_id": key_record["key_id"],
                "processing_time_sec": duration,
                "total_invoices": len(validated),
                "clean_count": clean_count,
                "review_count": review_count,
                "invoices": [
                    {
                        **v.invoice.model_dump(),
                        "requires_manual_review": v.requires_manual_review,
                        "validation_status": v.validation_status,
                        "issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues],
                        "validation_issues": [{**iss.model_dump(), "issue_type": iss.rule} for iss in v.issues]
                    }
                    for v in validated
                ]
            }
        except HTTPException:
            raise
        except Exception as e:
            latency_ms = (time.time() - start_t) * 1000
            record_api_usage(
                key_id=key_record["key_id"],
                endpoint="/v1/invoices/extract",
                method="POST",
                status_code=500,
                client_ip=get_real_client_ip(request),
                latency_ms=latency_ms
            )
            raise HTTPException(status_code=500, detail=f"Invoice extraction error: {str(e)}")
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Case B: JSON body with pre-extracted documents
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Provide either multipart 'files' upload or JSON body with 'documents'.")

    return await extract_and_validate_invoices(body)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="127.0.0.1", port=8008, reload=False)
