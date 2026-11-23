<div align="center">

# ⚡ CG-OCR Enterprise
### Autonomous Document, Spreadsheet & Invoice Intelligence Engine
#### High-Throughput Dual-Routing • Zero-Trust Anti-Bot Security • Redis & Grafana Telemetry

<br/>

[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18.0+-61DAFB.svg?style=for-the-badge&logo=react&logoColor=black)](https://reactjs.org/)
[![Vite](https://img.shields.io/badge/Vite-5.4+-646CFF.svg?style=for-the-badge&logo=vite&logoColor=white)](https://vitejs.dev/)
[![Redis](https://img.shields.io/badge/Redis-7.2_Telemetry-DC382D.svg?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io)
[![Grafana](https://img.shields.io/badge/Grafana-Prometheus_OpenMetrics-F46800.svg?style=for-the-badge&logo=grafana&logoColor=white)](https://grafana.com)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL_Cloud-3ECF8E.svg?style=for-the-badge&logo=supabase&logoColor=white)](https://supabase.com)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![UI Style](https://img.shields.io/badge/Design-Apple_Dark_Minimalist-000000.svg?style=for-the-badge&logo=apple&logoColor=white)](https://github.com/Sifatkhan-1915020/OCR-Project)

<br/>

**Architected & Engineered by [Sifat Khan](https://github.com/Sifatkhan-1915020)**  
*Department of Biomedical Engineering, Khulna University of Engineering & Technology (KUET)*

<br/>

<p align="center">
  <b>CG-OCR</b> is an enterprise-grade, memory-bounded document, spreadsheet, and invoice processing system built for high throughput. It couples deterministic sub-15ms digital triage (0 token cost) with <b>Baidu/Unlimited-OCR GPU acceleration</b>, <b>Gemini Vision AI fallback for complex tabular and multilingual typography</b>, zero-trust <b>Anti-Bot OTP Guard security</b>, <b>Redis 7.2 fast-buffers</b>, and <b>Prometheus/Grafana live observability</b>.
</p>

<p align="center">
  <a href="#-interactive-ui-gallery--screenshot-walkthrough"><b>📸 UI Screenshot Gallery</b></a> •
  <a href="#-system-architecture--functional-flows"><b>🏛️ System Architecture Flows</b></a> •
  <a href="#-how-to-use-the-whole-system-complete-manual"><b>📖 Operator Manual</b></a> •
  <a href="#-security-capabilities--threat-protection"><b>🛡️ Security Blueprint</b></a> •
  <a href="#-redis-telemetry--grafana-observability"><b>📊 Observability</b></a> •
  <a href="#-api-endpoints-reference"><b>🔌 API Reference</b></a>
</p>

</div>

---

## 📸 Interactive UI Gallery & Screenshot Walkthrough

Explore the live functional modules of the CG-OCR Enterprise platform below. Each section represents a key component of the end-to-end ingestion, authentication, processing, and auditing lifecycle.

<br/>

<div align="center">

### 1. Landing Page & Performance Architecture
*Apple-inspired dark minimalist command interface featuring real-time system metrics, dual-routing status, digital triage latency benchmarks (<15ms), and instant 1-click access.*

<img src="docs/screenshots/01_landing_page.jpg" alt="CG-OCR Minimalist Landing Page" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 2. Employee Login System
*Zero-trust authentication gateway protected by automated Anti-Bot OTP Guard. Enforces email whitelist validation and human verification checks before initiating token generation.*

<img src="docs/screenshots/02_login_system.png" alt="CG-OCR Employee Sign-In Portal" width="65%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 3. OTP Verification Guard
*Cryptographic one-time password verification dialog. 6-digit one-time codes are generated on the server with a 300-second TTL and dispatched exclusively via authenticated TLS SMTP to the user's Gmail inbox with zero client-side leakage.*

<img src="docs/screenshots/03_otp_verification.png" alt="Anti-Bot OTP Verification Screen" width="65%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 4. Multi-Format Document & Spreadsheet Upload Section
*High-capacity drag-and-drop ingestion zone capable of batching up to 1,000 document pages and spreadsheets. Supports multi-page PDFs, TIFF, JPG, PNG, WEBP, and tabular datasets (XLSX, CSV, TSV).*

<img src="docs/screenshots/04_upload_section.jpg" alt="Document & Spreadsheet Ingestion Queue" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 5. Enterprise Security Section & Real-Time Threat Tracking
*Infrastructure health telemetry feed coupled with real-time audit logs of unauthorized login attempts, capturing external public WAN IP addresses (`103.196.235.141`), Geo-location (`Dhaka, BD`), and resolved hardware user-agent fingerprints.*

<img src="docs/screenshots/05_security_section.jpg" alt="Security Health & Infrastructure Audit" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/>

*Detailed view of the live threat monitoring table capturing blocked intrusion incidents:*

<img src="docs/screenshots/05b_security_threat_table.png" alt="Flagged Unauthorized Login Attempts Incident Table" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 6. OCR Engine & Sliding Window Processing Section
*Live execution console and telemetry terminal showing the 20-element sliding window batch processor in action, GPU model allocation, and real-time execution logs.*

<img src="docs/screenshots/06_ocr_section.png" alt="OCR Execution Engine & Sliding Window Terminal" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 7. Financial Invoice Extraction & Mathematical Audit Section
*Task 3 financial intelligence engine extracting structured invoices (e.g. invoice `#CHL-2026-0100`, Meghna Trading Corporation). Performs automated mathematical audits ($\text{Subtotal} + \text{Tax} = \text{Total}$) with 1-click Excel, Supabase, JSON, and ZIP export.*

<img src="docs/screenshots/07_extraction_section.jpg" alt="Invoice Extraction and Financial Validation Audit" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 8. Team Collaboration & Workspace Chat Section
*Integrated persistent team messaging channel allowing authenticated workspace operators, auditors, and administrators to communicate, share invoice audit notes, and collaborate in real time.*

<img src="docs/screenshots/08_team_chat_section.jpg" alt="Authenticated Team Collaboration & Chat" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

<br/><br/>

### 9. Infrastructure Telemetry & Redis / Grafana Hub
*Live system observability suite with real-time HTTP throughput (RPS), sub-15ms P95 latency curves, host CPU utilization, Redis hit ratio (98.6%), and one-click Grafana dashboard export.*

<img src="docs/screenshots/09_infrastructure_observability.jpg" alt="Grafana Telemetry & Redis Observability Hub" width="95%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 12px 35px rgba(0,0,0,0.85);" />

</div>

---

## 🏛️ System Architecture & Functional Flows

### 1. End-to-End Ingestion & Dual-Route Extraction Engine
```mermaid
flowchart TD
    A["User Upload (PDF, Invoices, XLSX, CSV, TSV, Images)"] --> B{"Magic-Byte Pre-Filter & Sanitizer"}
    
    %% Fast Path (Deterministic)
    B -->|"Digital Vector PDF / Spreadsheet"| C["Deterministic Fast-Path (<15ms)"]
    C --> D["PyMuPDF / openpyxl / csv.Sniffer"]
    D -->|"0 Token Burn"| J["Structured Extracted Text & Tables"]
    
    %% Heavy Path (Scanned Invoices / Media)
    B -->|"Scanned Document / Raster Image"| E["OpenCV Preprocessor (cv2.INTER_AREA Max 1600px)"]
    E --> F["20-Element Sliding Epoch Batcher"]
    F --> G["Baidu/Unlimited-OCR (Kaggle/Colab Dual-GPU)"]
    
    %% Quality Gate & AI Fallback
    G --> H{"Confidence < 85% OR Bengali Text?"}
    H -->|"Yes (<0.85 / বাংলা)"| I["Gemini Vision AI (20-Item Packet Fallback)"]
    H -->|"No (>= 0.85 Clean)"| J
    I --> J
    
    %% Financial Audit & Data Persistence
    J --> K["Task 3 Invoice Validation Audit (Math Balancing)"]
    K --> L["Redis Telemetry Fast-Buffer (<0.2ms)"]
    L --> M["Supabase PostgreSQL Cloud DB (Auto-Sync)"]
    L --> N["Prometheus Exporter (/metrics)"]
    N --> O["Grafana Live Telemetry Dashboard"]
    K --> P["Excel (.xlsx) / JSON Report / ZIP Exporter"]
```

<br/>

### 2. Zero-Trust Authentication & Anti-Bot OTP Guard Flow
```mermaid
sequenceDiagram
    autonumber
    actor User as Employee / Intruder
    participant Client as React Client (Vite)
    participant Server as FastAPI Security Gateway
    participant Guard as Anti-Bot OTP Guard & Rate Limiter
    participant Redis as Redis 7.2 Session Store
    participant SMTP as Google TLS SMTP Relay
    participant Audit as Security Event Logger

    User->>Client: Submits Email & Captcha Guard
    Client->>Server: POST /api/auth/request-otp (Client WAN IP & UA)
    Server->>Guard: Validate Email Whitelist & Sliding Rate Window
    
    alt Unregistered / Unauthorized Email
        Guard->>Audit: Log UNAUTHORIZED_LOGIN_ATTEMPT (Real IP + Device)
        Audit-->>Server: Incident Recorded & Flagged
        Server-->>Client: 403 Forbidden ("Access Denied: Unregistered Account")
    else Exceeded Rate Limit (>3 req / 60s)
        Guard->>Audit: Log OTP_GUARD_BOT_ATTACK (CRITICAL)
        Server-->>Client: 429 Too Many Requests ("Rate limit exceeded")
    else Authorized Employee
        Guard->>Redis: Store Cryptographic OTP (TTL 300s, 3 attempts max)
        Guard->>SMTP: Dispatch Code via TLS SMTP (smtp.gmail.com:587)
        SMTP-->>User: Delivers 6-Digit Code to Inbox
        Server-->>Client: 200 OK ("Code dispatched to email")
    end

    User->>Client: Enters 6-Digit Verification Code
    Client->>Server: POST /api/auth/verify-otp
    alt Incorrect Code (3 Attempts Exhausted)
        Server->>Guard: Increment Failure Count
        Server->>Audit: Log FAILED_OTP_VERIFICATION / BRUTE_FORCE_LOCKOUT
        Server-->>Client: 400 Bad Request ("Invalid code")
    else Valid OTP Code
        Server->>Redis: Invalidate Code (Zero-Reuse Guarantee)
        Server-->>Client: 200 OK + Cryptographic JWT Session Token
        Client-->>User: Grants Workspace Access
    end
```

<br/>

### 3. Redis Fast-Buffer & Prometheus/Grafana Telemetry Stream
```mermaid
flowchart LR
    A["HTTP Ingestion & OCR Execution"] --> B["FastAPI Telemetry Middleware"]
    B --> C["Prometheus Metrics Registry (/metrics)"]
    B --> D["Redis Telemetry Engine (<0.2ms)"]
    
    subgraph "Redis 7.2 Cache Namespaces"
        D --> E["otp:guard (Rate Limits & Sessions)"]
        D --> F["ocr:cache (Deduplication Hashes)"]
        D --> G["auth:whitelist (Employee Set)"]
        D --> H["telemetry:grafana (OpenMetrics Buffer)"]
    end
    
    C --> I["Grafana Agent / Prometheus Scraper (5s Interval)"]
    I --> J["Grafana Dashboard Panels"]
    J --> K["Throughput RPS"]
    J --> L["P95 Latency Curve"]
    J --> M["Redis Hit Ratio %"]
    J --> N["Host CPU & RAM Load"]
    J --> O["Security Intrusions Blocked"]
```

---

## ⚡ Core Engineering Highlights

<table>
  <tr>
    <td width="50%">
      <h3>Deterministic Fast-Path (&lt;15ms)</h3>
      <p>Digital vector PDFs, spreadsheets (XLSX, XLS), and delimited files (CSV, TSV) completely bypass heavy deep-learning networks. Structured text and tabular schemas are extracted in sub-15ms with <b>0 token consumption</b>.</p>
    </td>
    <td width="50%">
      <h3>Baidu/Unlimited-OCR Acceleration</h3>
      <p>Processes dense scanned media using a 3B vision-language architecture with Reference Sliding Window Attention running on Kaggle/Colab Dual T4 GPUs. Tunnelled securely via zero-configuration Cloudflare HTTPS endpoints.</p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>Gemini Vision AI Fallback</h3>
      <p>When line confidence scores drop below 85% or Bengali (<code>বাংলা</code>) typographic complexity is detected, batches are multiplexed into <b>20-element sliding packets</b> and routed to Gemini Vision AI for 98.4% accuracy recovery.</p>
    </td>
    <td width="50%">
      <h3>Adaptive Downscaling & Compression</h3>
      <p>Images larger than 1600px are automatically normalized via <code>cv2.INTER_AREA</code> downsampling and high-efficiency JPEG compression, slashing network transmission payload by <b>~90%</b> (~250 KB vs 2.5 MB) with zero legibility penalty.</p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>Redis Telemetry & Fast-Buffer</h3>
      <p>Engineered for high-concurrency caching with seamless failover to an embedded Redis 7.2-compatible cache. Achieves sub-0.2ms round-trip latency for session handling, OTP anti-replay guards, and document hash deduplication.</p>
    </td>
    <td width="50%">
      <h3>Grafana & Prometheus Observability</h3>
      <p>Native OpenMetrics exporter exposed at <code>/metrics</code>. Feeds live telemetry directly into Prometheus and Grafana dashboards, monitoring real-time request rates, P95 latency distributions, CPU/RAM utilization, and security event triggers.</p>
    </td>
  </tr>
</table>

---

## 🛡️ Security Capabilities & Threat Protection

CG-OCR enforces enterprise-grade zero-trust access control with real-time hardware telemetry:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   CG-OCR SECURITY SHIELD & TELEMETRY                   │
├───────────────────────┬────────────────────────────────────────────────┤
│ Security Vector       │ Implementation & Defense Guarantee             │
├───────────────────────┼────────────────────────────────────────────────┤
│ Anti-Bot OTP Guard    │ Sliding rate-limit (max 3 req/60s). Rapid-fire │
│                       │ flood attacks trigger critical rate limits.    │
├───────────────────────┼────────────────────────────────────────────────┤
│ Brute-Force Lockout   │ Strict 3-attempt ceiling per OTP session. Upon │
│                       │ 3rd failure, code is purged and IP is flagged. │
├───────────────────────┼────────────────────────────────────────────────┤
│ Real Network Tracking │ Resolves client external public WAN IP         │
│                       │ (103.196.235.141) and ISP/City (Dhaka, BD)     │
│                       │ rather than internal 127.0.0.1 loopback.       │
├───────────────────────┼────────────────────────────────────────────────┤
│ Device Fingerprinting │ Decodes raw user agents into human-readable    │
│                       │ profiles: "Windows 11 PC • Google Chrome v131" │
├───────────────────────┼────────────────────────────────────────────────┤
│ Zero Client-Side Leak │ OTP codes are NEVER exposed in API responses   │
│                       │ or frontend state. Sent exclusively via TLS.   │
├───────────────────────┼────────────────────────────────────────────────┤
│ Authenticated SMTP    │ Google TLS 587 relay with App Passwords.       │
│                       │ Bypasses third-party mailer spam filters.      │
├───────────────────────┼────────────────────────────────────────────────┤
│ Whitelist Isolation   │ Admin dashboard manages authorized employees.  │
│                       │ Unlisted emails are rejected with 403 blocks.  │
└───────────────────────┴────────────────────────────────────────────────┘
```

---

## 📖 How to Use the Whole System (Complete Manual)

<details open>
<summary><b>Click to expand / collapse: Comprehensive Step-by-Step Operator Guide</b></summary>
<br/>

### 1. Accessing the Workspace
* Open your browser and navigate to `http://localhost:3000` (or your deployed production URL).
* The **Minimalist Landing Page** provides an immediate overview of active pipelines, GPU worker status, and triage latency benchmarks.

### 2. Administrator Access & Whitelisting
1. Click **1-Click Admin Access (MVP)** on the landing page or top navigation bar.
2. The administrative panel allows you to:
   * View all active authorized employee email addresses.
   * Add new employee email accounts (e.g., `auditor@careguide.com`).
   * Instantly revoke access for terminated personnel.

### 3. Employee Sign-In (Protected by Anti-Bot OTP Guard)
1. Click **Employee Sign-In**.
2. Enter your authorized corporate email address.
3. Complete the Anti-Bot confirmation checkbox.
4. Click **Request Verification Code**:
   * The server checks your email against the authorized database.
   * An unauthorized email instantly creates a **HIGH SEVERITY** security audit log capturing your IP and device.
   * If authorized, a secure 6-digit cryptographic code is dispatched to your Gmail inbox via TLS SMTP.
5. Retrieve the code from your inbox, input the 6 digits into the verification field, and click **Verify & Enter Workspace**.

### 4. Ingesting Documents, Spreadsheets & Invoices
1. Switch to the **OCR & Extraction** tab.
2. Drag and drop any supported document format into the ingestion zone:
   * Multi-page digital or scanned PDFs (up to 1,000 pages).
   * Scanned invoices and receipts (JPG, PNG, TIFF, WEBP).
   * Tabular ledger files (XLSX, XLS, CSV, TSV).
3. Expand the **Live Processing Telemetry & System Logs** viewer to monitor real-time worker allocation and memory buffers.
4. Click **Start OCR Extraction**:
   * Digital PDFs and tabular sheets finish in `<15ms` using deterministic parsing.
   * Scanned files are preprocessed (`cv2.INTER_AREA`) and dispatched in 20-page epochs to GPU OCR.
   * Degraded or handwritten Bengali lines are sent to Gemini Vision AI for high-accuracy fallback.

### 5. Reviewing Invoice Audits & Mathematical Validation
1. Upon extraction completion, navigate to the **Task 3: Invoices & Data Validation Audit** tab.
2. Inspect extracted invoice records:
   * Supplier / Vendor and Customer identification.
   * Invoice Number (e.g. `#CHL-2026-0100`) and Billing Date.
   * Subtotal, VAT/Tax rate, and Grand Total balance check ($\text{Subtotal} + \text{Tax} = \text{Total}$).
   * Line-item breakdown with quantities, unit rates, item totals, and mathematical discrepancy flags.

### 6. Exporting Results & Cloud Synchronization
* **Download Invoices (.xlsx)**: Export a multi-tab Microsoft Excel workbook containing clean invoice schemas and itemized breakdowns.
* **Save to Supabase**: Auto-commit records to 6 normalized PostgreSQL tables (`documents`, `invoices`, `invoice_items`, `rules`, `logs`, `stats`).
* **JSON Report & ZIP**: Download machine-readable audit reports or complete file bundles for downstream ETL pipelines.

### 7. Telemetry, Threat Monitoring & Team Chat
* **Status & Security Tab**:
  * Review live health across Backend, GPU Worker, Gemini AI, Supabase, Redis, and Grafana.
  * Inspect the **Flagged Unauthorized Login Attempts** table to investigate blocked intrusion attempts with real IP and device fingerprints.
  * Execute live Redis latency tests (<0.2ms) and export the Grafana dashboard JSON.
* **Team Chat Tab**:
  * Exchange real-time, persistent messages and audit notes with team members across the workspace.

</details>

---

## 📊 Redis Telemetry & Grafana Observability

CG-OCR natively exports open observability metrics ready for scraping by Prometheus and Grafana.

### Prometheus Scrape Configuration (`prometheus.yml`)
Add this configuration snippet to your Prometheus or Grafana Agent configuration:

```yaml
scrape_configs:
  - job_name: 'cg-ocr-engine'
    scrape_interval: 5s
    metrics_path: '/metrics'
    static_configs:
      - targets: ['127.0.0.1:8008']
```

### Ready-to-Import Grafana Dashboard
Download the pre-configured Grafana dashboard JSON directly from the running server:
```bash
curl -O http://127.0.0.1:8008/api/telemetry/grafana/export
```
*Import the exported `CG_OCR_Grafana_Dashboard.json` via Grafana (`Dashboards -> Import`) to visualize live panels for HTTP Throughput (RPS), P95 Latency, Redis Cache Hit Ratio, System Error Rates, and Security Threat Counters.*

---

## 🚀 Quick Start Guide

### Prerequisites
* **Python 3.10+** (Tested on Python 3.11)
* **Node.js 18+** & `npm`

### 1. Clone the Repository
```bash
git clone https://github.com/Sifatkhan-1915020/OCR-Project.git
cd OCR-Project
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and configure your credentials:
```bash
cp .env.example .env
```
```env
# Google Gemini API Key (for Vision OCR & Multilingual Fallback)
GEMINI_API_KEY=your_gemini_api_key_here

# Supabase PostgreSQL Configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_or_service_role_key

# Authenticated SMTP Email Relay (for OTP Guard)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_google_app_password
SMTP_FROM=your_email@gmail.com

# Redis Telemetry Engine (Optional, defaults to high-speed embedded cache)
REDIS_URL=redis://127.0.0.1:6379/0
```

### 3. Install Backend Dependencies
```bash
python -m pip install -r requirements.txt
```

### 4. Install Frontend Dependencies
```bash
npm --prefix frontend install
```

### 5. Launch the Application

#### Terminal 1: FastAPI Backend
```bash
python api_server.py
```
*Backend server initializes on `http://127.0.0.1:8008`.*

#### Terminal 2: React Frontend (Vite)
```bash
npm --prefix frontend run dev
```
*Vite frontend initializes on `http://localhost:3000`.*

---

## 🔌 API Endpoints Reference

| Method | Endpoint | Description | Auth Required |
| :--- | :--- | :--- | :---: |
| `GET` | `/api/health` | Comprehensive infrastructure & telemetry status | Public |
| `GET` | `/metrics` | **Prometheus / OpenMetrics** scrape target for Grafana | Public |
| `GET` | `/api/telemetry/redis` | Redis memory, hit ratio, latency, and namespaces | Public |
| `POST` | `/api/telemetry/redis/ping` | Live Redis PING round-trip verification | Public |
| `POST` | `/api/telemetry/redis/test` | Live Redis key write & read verification | Public |
| `GET` | `/api/telemetry/grafana` | Real-time timeseries curves (RPS, Latency, CPU, RAM) | Public |
| `GET` | `/api/telemetry/grafana/export` | Download ready-to-import Grafana dashboard JSON | Public |
| `GET` | `/api/auth/employees` | Retrieve authorized employee email whitelist | Admin |
| `POST` | `/api/auth/employees` | Authorize new employee email address | Admin |
| `DELETE` | `/api/auth/employees/{email}` | Revoke employee authorization | Admin |
| `POST` | `/api/auth/request-otp` | Request 6-digit OTP code (OTP Guard Protected) | Public |
| `POST` | `/api/auth/verify-otp` | Verify 6-digit OTP and issue session token | Public |
| `GET` | `/api/security/events` | Retrieve real-time security intrusion audit events | Employee / Admin |
| `POST` | `/api/extract` | Multi-format document extraction execution | Session Token |
| `POST` | `/api/supabase/save/invoices` | Persist validated invoices to Supabase PostgreSQL | Session Token |
| `GET` | `/api/chat/messages` | Fetch persistent team collaboration messages | Session Token |
| `POST` | `/api/chat/messages` | Post new message to employee collaboration channel | Session Token |

---

## 🚢 Production Deployment to Vercel

The frontend is optimized for continuous deployment on **Vercel**:

1. Push your changes to GitHub: **`https://github.com/Sifatkhan-1915020/OCR-Project`**.
2. Open **[vercel.com/new](https://vercel.com/new)** and import `OCR-Project`.
3. Set **Root Directory** to `frontend` (Framework Preset: `Vite`).
4. Add environment variables if required (`VITE_API_URL` pointing to your hosted FastAPI backend).
5. Click **Deploy**. Vercel will build and assign an instant production URL with global CDN acceleration.

---

## 📜 License & Credits

* **Architecture & Development**: [Sifat Khan](https://github.com/Sifatkhan-1915020) (KUET Biomedical Engineering).
* **Core Models**: `baidu/Unlimited-OCR` (Baidu Research) & Google Gemini Vision AI.
* **License**: Released under the [MIT License](LICENSE).
