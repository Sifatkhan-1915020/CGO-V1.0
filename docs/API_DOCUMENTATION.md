# CareGuide Developer REST API & System Setup Guide

> **Version**: 1.0.0  
> **Interactive Swagger UI**: [https://ocr-project-puce.vercel.app/docs](https://ocr-project-puce.vercel.app/docs)  
> **OpenAPI 3.1 Spec**: [https://ocr-project-puce.vercel.app/openapi.json](https://ocr-project-puce.vercel.app/openapi.json)  
> **Main Web Workspace**: [https://ocr-project-puce.vercel.app](https://ocr-project-puce.vercel.app)  

---

## 📑 Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Quick Start & Local Server Setup](#2-quick-start--local-server-setup)
3. [Authentication & API Key Management](#3-authentication--api-key-management)
4. [CLI Key Generator (`create_key.py`)](#4-cli-key-generator-create_keypy)
5. [Developer API (`/v1`) Reference](#5-developer-api-v1-reference)
   - [Health Check (`GET /v1/health`)](#51-health-check-get-v1health)
   - [Document & Tabular Extraction (`POST /v1/extract`)](#52-document--tabular-extraction-post-v1extract)
   - [Invoice Extraction & Financial Validation (`POST /v1/invoices/extract`)](#53-invoice-extraction--financial-validation-post-v1invoicesextract)
6. [API Key Management Endpoints](#6-api-key-management-endpoints)
7. [Python & Node.js Code Examples](#7-python--nodejs-code-examples)
8. [Error Codes & Rate Limiting](#8-error-codes--rate-limiting)
9. [Production Deployment (Vercel + Cloud GPU)](#9-production-deployment-vercel--cloud-gpu)

---

## 1. Architecture Overview

CareGuide is an enterprise multi-format document extraction and financial audit engine. It ingests complex documents across multiple formats (**PDF up to 1,000 pages, PNG, JPG, TIFF, multi-sheet XLSX, XLS, CSV**) and extracts structured text, markdown, tabular data, and audited invoice records.

```mermaid
flowchart TD
    Client["Client / Third-Party App"]
    VercelEdge["Vercel Global Edge CDN<br/>(React UI + Swagger UI /docs)"]
    FastAPIServer["FastAPI Backend Server<br/>(:8008 /v1 endpoints)"]
    AuthGuard["API Key & Rate Limit Guard<br/>(SHA-256 + Sliding Window RPM)"]
    Orchestrator["Document Orchestrator"]
    OCRModels["Baidu Unlimited-OCR / RapidOCR<br/>(ONNX & GPU Accelerated)"]
    GeminiFallback["Gemini Vision AI Fallback<br/>(Bangla & <85% Confidence)"]
    InvoiceAuditor["Invoice Extractor & Task 3 Validator<br/>(Math Cross-Checks & Multi-Sheet Excel)"]
    TelemetryStore["SQLite & Redis Telemetry Cache<br/>(Metrics & Prometheus /metrics)"]

    Client -->|HTTPS + X-API-Key| VercelEdge
    Client -->|Direct REST API| FastAPIServer
    VercelEdge -->|Rewrite /api/*| FastAPIServer
    FastAPIServer --> AuthGuard
    AuthGuard --> Orchestrator
    Orchestrator --> OCRModels
    Orchestrator -.->|Packet Fallback| GeminiFallback
    Orchestrator --> InvoiceAuditor
    FastAPIServer --> TelemetryStore
```

### Key Technical Capabilities:
* **Hybrid OCR Architecture**: Local RapidOCR (ONNX) + Remote Baidu Unlimited-OCR (Kaggle/Colab GPU) + Google Gemini Vision AI fallback (<85% confidence packet rescue and Bangla OCR).
* **High-Throughput Tabular Register**: Direct streaming extraction for Excel workbooks and tabular TSV/CSV files with column alignment and schema normalization.
* **Task 3 Financial Invoice Validation**: Automated verification of mathematical consistency (`Subtotal + Tax == Total`), line-item matching, date sanity, vendor identification, duplicate detection, and manual review tagging.

---

## 2. Quick Start & Local Server Setup

### 2.1 Prerequisites
* **Python**: 3.10, 3.11, or 3.12
* **Node.js**: 18+ (for frontend development)
* **System Libraries**: `poppler-utils` and `libgl1` (for PDF rendering and OpenCV image processing)

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y poppler-utils libgl1-mesa-glx
```

### 2.2 Environment Configuration
Clone the repository and create your local `.env` configuration:

```bash
git clone https://github.com/Sifatkhan-1915020/CGO-V1.0.git
cd CGO-V1.0
cp .env.example .env
```

Edit `.env` with your parameters:
```env
# Google Gemini Vision API Key (Required for Bangla & <85% confidence OCR fallback)
GEMINI_API_KEY="AIzaSyYourGeminiApiKeyHere"

# Optional Cloud GPU URL (Kaggle or Colab Cloudflare tunnel)
API_URL="https://observed-blend-imagination-wed.trycloudflare.com"

# System Database Path
CAREGUIDE_SYSTEM_DB="careguide_system.db"

# Redis Telemetry URL (Defaults to embedded cache if not running)
REDIS_URL="redis://127.0.0.1:6379/0"
```

### 2.3 Python Virtual Environment & Installation
```bash
# Option A: Using uv (Fastest)
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Option B: Standard Python venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2.4 Starting the FastAPI Backend Server
```bash
python api_server.py
# Server starts on http://127.0.0.1:8008
```

You can verify the backend is running by opening:
* Local Health Check: `http://localhost:8008/api/health`
* Local Swagger Docs: `http://localhost:8008/api/docs`

---

## 3. Authentication & API Key Management

All developer endpoints under `/v1/*` require an active CareGuide API key.

### 3.1 Authentication Headers
Include your key in either of the following HTTP headers:

```http
X-API-Key: cg_live_YOUR_API_KEY
```
*or*
```http
Authorization: Bearer cg_live_YOUR_API_KEY
```

### 3.2 Key Format & Security
* **Production Prefix**: `cg_live_` (e.g. `cg_live_a1b2c3d4e5f6...`)
* **Test Prefix**: `cg_test_` (for sandbox experimentation)
* **Cryptographic Storage**: Raw secret keys are displayed **once** upon creation. The backend calculates and stores only the **SHA-256 hash** (`hashlib.sha256(raw_key.encode()).hexdigest()`), ensuring zero plain-text leaks in database breaches.
* **Masked Display**: Keys are masked for audit logs (e.g., `cg_live_...9f2a`).

### 3.3 Scopes
API keys support granular permissions:
* `extract`: Multi-format document, image, and tabular extraction (`/v1/extract`).
* `invoices`: Invoice extraction and Task 3 financial auditing (`/v1/invoices/extract`).
* `health`: System status and quota verification (`/v1/health`).
* `admin`: Complete administrative access to generate, revoke, and audit keys.
* `*`: Wildcard access granting all scopes.

---

## 4. CLI Key Generator (`create_key.py`)

CareGuide includes a command-line tool, [create_key.py](file:///home/sifat-khan/Downloads/CareGuide/create_key.py), to manage API keys directly from your terminal.

```bash
# 1. Generate a production key for a client/app:
python create_key.py customer-001

# 2. Generate with custom rate limit (RPM), quota, validity, and scopes:
python create_key.py partner-app --rpm 120 --limit 25000 --days 60 --scopes extract,invoices

# 3. Generate a sandbox/test key (cg_test_...):
python create_key.py sandbox-client --test

# 4. List all registered API keys (secrets are masked):
python create_key.py --list

# 5. Inspect key details, usage counts, and recent latency logs:
python create_key.py --info key_1aa50010084cd28b

# 6. Revoke an API key immediately:
python create_key.py --revoke key_1aa50010084cd28b
```

### Example CLI Output:
```text
========================================================================
             🛡️  CareGuide API Key Management Engine  🛡️
========================================================================

✅ New CareGuide API Key Created Successfully!

  • Client / App Name : customer-001
  • Key ID            : key_5fa04e28c9b2
  • Key Type          : 🚀 Production (Live)
  • Masked Display    : cg_live_...4d9f
  • Scopes Granted    : extract, invoices, health
  • Rate Limit (RPM)  : 60 req/min
  • Total Quota       : Unlimited
  • Expiration        : 2027-02-21T12:00:00Z
  • Created At (UTC)  : 2026-11-23T12:00:00Z

========================================================================
  🔑  RAW SECRET API KEY (Shown ONCE only — store in secure vault):
  cg_live_4xMkd83kLs09QkzWcIZpMQMSKQ892_VjWcIZpMQ
========================================================================
```

---

## 5. Developer API (`/v1`) Reference

### 5.1 Health Check (`GET /v1/health`)
Verifies API server connectivity and returns authenticated client quota statistics.

* **Endpoint**: `/v1/health` (alias: `/api/v1/health`)
* **Method**: `GET`
* **Scope Required**: `health` or `*`

#### Request (cURL)
```bash
curl -s -X GET "https://ocr-project-puce.vercel.app/v1/health" \
     -H "X-API-Key: cg_live_YOUR_KEY"
```

#### Response (`200 OK`)
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "client": {
    "name": "customer-001",
    "key_id": "key_5fa04e28c9b2",
    "masked_key": "cg_live_...4d9f",
    "rate_limit_rpm": 60,
    "total_requests": 142
  },
  "engine": "CareGuide NextGen Extraction Engine",
  "timestamp": 1795431600.25
}
```

---

### 5.2 Document & Tabular Extraction (`POST /v1/extract`)
Uploads and extracts structured text, tables, and OCR tokens from one or more documents.

* **Endpoint**: `/v1/extract` (alias: `/api/v1/extract`)
* **Method**: `POST`
* **Content-Type**: `multipart/form-data`
* **Scope Required**: `extract` or `*`
* **Supported Formats**: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.xlsx`, `.xls`, `.csv`

#### Form Parameters:
| Field | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `files` | `file[]` | **Yes** | — | One or more documents/spreadsheets to extract. |
| `batch_size` | `integer` | No | `20` | Pages processed per epoch pass. |
| `api_url` | `string` | No | Env default | Custom Cloud GPU tunnel URL. |
| `gemini_api_key` | `string` | No | Env default | Overrides Gemini Vision key for fallback. |

#### Request (cURL)
```bash
curl -X POST "https://ocr-project-puce.vercel.app/v1/extract" \
     -H "X-API-Key: cg_live_YOUR_KEY" \
     -F "files=@invoice.pdf" \
     -F "files=@balance_sheet.xlsx" \
     -F "batch_size=20"
```

#### Response (`200 OK`)
```json
{
  "status": "success",
  "client_id": "key_5fa04e28c9b2",
  "processing_time_sec": 1.482,
  "total_documents": 2,
  "documents": [
    {
      "filename": "invoice.pdf",
      "format": "PDF",
      "status": "SUCCESS",
      "pages": [
        {
          "page_number": 1,
          "text": "INVOICE #INV-2026-0042\nDate: 2026-11-20\nTotal: $1,450.00",
          "markdown": "## INVOICE #INV-2026-0042\n**Date**: 2026-11-20\n**Total**: $1,450.00",
          "tables": [],
          "confidence": 0.962,
          "engine_used": "RAPID_OCR_ONNX"
        }
      ],
      "sheets": []
    },
    {
      "filename": "balance_sheet.xlsx",
      "format": "XLSX",
      "status": "SUCCESS",
      "pages": [],
      "sheets": [
        {
          "sheet_name": "Q3 Financials",
          "rows_count": 25,
          "columns_count": 6,
          "headers": ["Account", "Code", "Debit", "Credit", "Balance", "Notes"],
          "sample_rows": [
            ["Cash at Bank", "1010", "45000.00", "0.00", "45000.00", "Operational"]
          ]
        }
      ]
    }
  ]
}
```

---

### 5.3 Invoice Extraction & Financial Validation (`POST /v1/invoices/extract`)
Extracts financial records (vendor, invoice number, line items, taxes, totals) and executes Task 3 financial auditing.

* **Endpoint**: `/v1/invoices/extract` (alias: `/api/v1/invoices/extract`)
* **Method**: `POST`
* **Content-Type**: `multipart/form-data` *(Direct file upload)* **OR** `application/json` *(Pre-extracted JSON)*
* **Scope Required**: `invoices` or `*`

#### Mode A: Direct File Upload (`multipart/form-data`)
```bash
curl -X POST "https://ocr-project-puce.vercel.app/v1/invoices/extract" \
     -H "Authorization: Bearer cg_live_YOUR_KEY" \
     -F "files=@vendor_invoice.pdf"
```

#### Mode B: JSON Payload (`application/json`)
```bash
curl -X POST "https://ocr-project-puce.vercel.app/v1/invoices/extract" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer cg_live_YOUR_KEY" \
     -d '{
       "documents": [
         {
           "filename": "invoice_sample.txt",
           "pages": [
             { "page_number": 1, "text": "Acme Corp Invoice #9012 Date: 2026-10-15 Subtotal: 100.00 Tax: 10.00 Total: 110.00" }
           ]
         }
       ]
     }'
```

#### Response (`200 OK`)
```json
{
  "status": "success",
  "client_id": "key_5fa04e28c9b2",
  "processing_time_sec": 0.842,
  "total_invoices": 1,
  "clean_count": 1,
  "review_count": 0,
  "invoices": [
    {
      "vendor_name": "Acme Corp",
      "invoice_number": "9012",
      "invoice_date": "2026-10-15",
      "currency": "USD",
      "subtotal": 100.00,
      "tax_amount": 10.00,
      "total_amount": 110.00,
      "line_items": [
        {
          "description": "Enterprise Platform License",
          "quantity": 1,
          "unit_price": 100.00,
          "amount": 100.00
        }
      ],
      "requires_manual_review": false,
      "validation_status": "CLEAN",
      "issues": []
    }
  ]
}
```

> **Task 3 Validation Shield**: If mathematical inconsistencies occur (e.g., `Subtotal + Tax != Total`) or mandatory fields (Vendor/Date) are missing, `requires_manual_review` is set to `true`, and specific violations are flagged in the `issues` array.

---

## 6. API Key Management Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/keys/generate` | Generates a new API key with custom rate limits and scopes. |
| `GET` | `/api/keys` | Lists all registered keys (masked format). |
| `GET` | `/api/keys/verify` | Verifies the caller's active key status and remaining quota. |
| `GET` | `/api/keys/{key_id}` | Retrieves key metadata, error counts, and latency statistics. |
| `DELETE` | `/api/keys/{key_id}` | Instantly revokes an API key. |

### Example: Programmatic Key Generation (`POST /api/keys/generate`)
```bash
curl -X POST "http://localhost:8008/api/keys/generate" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "microservice-ocr",
       "scopes": ["extract", "invoices", "health"],
       "rate_limit_rpm": 120,
       "total_limit": 50000,
       "expires_in_days": 180
     }'
```

---

## 7. Python & Node.js Code Examples

### 7.1 Python SDK Example (`careguide_client.py`)
```python
import requests

class CareGuideClient:
    def __init__(self, api_key: str, base_url: str = "https://ocr-project-puce.vercel.app"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": self.api_key}

    def health(self) -> dict:
        res = requests.get(f"{self.base_url}/v1/health", headers=self.headers)
        res.raise_for_status()
        return res.json()

    def extract_documents(self, file_paths: list[str]) -> dict:
        files = [("files", (fp, open(fp, "rb"), "application/octet-stream")) for fp in file_paths]
        res = requests.post(f"{self.base_url}/v1/extract", headers=self.headers, files=files)
        res.raise_for_status()
        return res.json()

    def extract_invoice(self, file_path: str) -> dict:
        with open(file_path, "rb") as f:
            files = [("files", (file_path, f, "application/pdf"))]
            res = requests.post(f"{self.base_url}/v1/invoices/extract", headers=self.headers, files=files)
            res.raise_for_status()
            return res.json()

# Usage
client = CareGuideClient(api_key="cg_live_YOUR_KEY_HERE")
print("Health:", client.health())
result = client.extract_documents(["invoice.pdf", "report.xlsx"])
print("Total Documents Processed:", result["total_documents"])
```

### 7.2 Node.js (TypeScript / JavaScript) Example
```javascript
import fs from 'fs';

const API_KEY = 'cg_live_YOUR_KEY_HERE';
const BASE_URL = 'https://ocr-project-puce.vercel.app';

async function extractInvoice(filePath) {
  const formData = new FormData();
  const fileBlob = new Blob([fs.readFileSync(filePath)]);
  formData.append('files', fileBlob, filePath);

  const response = await fetch(`${BASE_URL}/v1/invoices/extract`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${API_KEY}`
    },
    body: formData
  });

  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${await response.text()}`);
  }

  const data = await response.json();
  console.log('Audited Invoices:', data.invoices);
}

extractInvoice('dummy_bangla_invoice.csv');
```

---

## 8. Error Codes & Rate Limiting

The API uses standard HTTP response status codes:

| Code | Status | Reason & Action |
| :--- | :--- | :--- |
| `200` | `OK` | Request processed successfully. |
| `400` | `Bad Request` | Missing file parameters, unreadable payload, or corrupted document. |
| `401` | `Unauthorized` | Missing, revoked, or expired API key. Check `X-API-Key` header. |
| `403` | `Forbidden` | API key lacks the required scope (e.g. attempting `/v1/invoices/extract` with only `extract` scope). |
| `404` | `Not Found` | Key ID or requested resource does not exist. |
| `429` | `Rate Limit Exceeded` | Key has exceeded its requests-per-minute (RPM) window or total lifetime quota. |
| `500` | `Internal Server Error` | Backend extraction pipeline error. Check server logs. |

---

## 9. Production Deployment (Vercel + Cloud GPU)

### 9.1 Frontend on Vercel
The frontend, interactive Swagger UI (`/docs`), and OpenAPI schema (`/openapi.json`) are deployed on **Vercel**:
* Vercel repository: `https://github.com/Sifatkhan-1915020/CGO-V1.0`
* Build command: `npm --prefix frontend run build`
* Output directory: `frontend/dist`
* Live URL: [https://ocr-project-puce.vercel.app](https://ocr-project-puce.vercel.app)

### 9.2 Running Remote Cloud GPU Worker (Kaggle / Colab)

CG-OCR supports dual network connection options for remote GPU worker instances:

#### Option A: Tailscale WireGuard Mesh (Recommended)
Connects your Kaggle/Colab GPU instance directly into your private Tailnet via peer-to-peer WireGuard:
```bash
# On Kaggle / Colab Notebook Cell:
!curl -fsSL https://tailscale.com/install.sh | sh
!tailscale up --authkey=YOUR_EPHEMERAL_AUTH_KEY --hostname=kaggle-gpu
!python colab_server.py
```
* **Endpoint URL**: `http://100.x.y.z:8000` (or MagicDNS `http://kaggle-gpu.tailnet-name.ts.net:8000`).
* Set in `.env`: `TAILSCALE_URL=http://100.x.y.z:8000` or configure directly in the Web UI Settings.

#### Option B: Cloudflare Quick Tunnel (Default)
Generates an instant public HTTPS proxy URL without requiring an account:
```bash
# On Colab / Kaggle
python colab_server.py
```
* **Endpoint URL**: `https://xxxx.trycloudflare.com`.
* Set in `.env`: `CLOUDFLARE_TUNNEL_URL=https://xxxx.trycloudflare.com` or paste into Web UI Settings.

#### ⚖️ Architectural Comparison: Tailscale vs. Cloudflare Tunnel

| Vector | 🛡️ Tailscale WireGuard Mesh (Option) | 🌐 Cloudflare Quick Tunnel (Default) |
|---|---|---|
| **Public Exposure** | 🔒 **Zero public internet exposure** (restricted strictly to authenticated Tailnet devices). Immune to public crawlers and port scanners. | ⚠️ **Publicly accessible endpoint** (`trycloudflare.com` can be accessed by anyone on the internet unless protected by Cloudflare Access). |
| **Request Timeout** | ⏱️ **Unlimited** (direct peer-to-peer connection without proxy cutoffs; ideal for 1,000-page batch runs). | ⚠️ **100s Hard Limit** (Cloudflare Free Tier terminates HTTP connections taking >100 seconds with `Error 524`). |
| **Network Throughput** | 🚀 **Direct P2P WireGuard** (low latency UDP tunneling directly between Kaggle/Colab and your host). | ⚡ Routed through Cloudflare CDN edge data centers (subject to edge hair-pinning). |
| **Endpoint Stability** | 📌 **Permanent static private IP** (`100.x.y.z`) or MagicDNS name that persists across notebook restarts. | ⚠️ **Random URL** generated on every tunnel restart. |
| **Account Requirement** | Requires a free Tailscale account and generating an ephemeral auth key (`tskey-auth-...`). | ⚡ **Zero account required** (starts instantly with 1 command). |
| **Installation** | Tailscale client must be installed on host and GPU machine. | Zero installation on client; only `cloudflared` binary on GPU machine. |
