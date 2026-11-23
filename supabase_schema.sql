-- ========================================================
-- CareGuide Supabase Database Schema
-- Multi-Format Document Extraction & Task 3 Invoice Audit
-- ========================================================

-- 1. Extraction Jobs Table
CREATE TABLE IF NOT EXISTS public.extraction_jobs (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'COMPLETED',
    total_files INT DEFAULT 0,
    total_pages INT DEFAULT 0,
    total_sheets INT DEFAULT 0,
    duration_sec NUMERIC(10, 2) DEFAULT 0,
    throughput_pages_per_sec NUMERIC(10, 2) DEFAULT 0,
    summary JSONB DEFAULT '{}'::jsonb
);

-- 2. Extracted Documents Table
CREATE TABLE IF NOT EXISTS public.documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT REFERENCES public.extraction_jobs(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SUCCESS',
    engine_routing TEXT,
    processing_time_sec NUMERIC(10, 2) DEFAULT 0,
    page_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 3. Document Pages Table
CREATE TABLE IF NOT EXISTS public.document_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES public.documents(id) ON DELETE CASCADE,
    page_number INT NOT NULL,
    type TEXT DEFAULT 'EXTRACTED',
    confidence NUMERIC(5, 4) DEFAULT 1.0,
    engine TEXT,
    text TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Invoices Table (Task 3 Structured Intelligence)
CREATE TABLE IF NOT EXISTS public.invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT,
    source_filename TEXT NOT NULL,
    invoice_number TEXT,
    invoice_date TEXT,
    supplier_name TEXT,
    customer_name TEXT,
    currency TEXT DEFAULT 'USD',
    subtotal NUMERIC(15, 2),
    tax NUMERIC(15, 2),
    total NUMERIC(15, 2),
    requires_manual_review BOOLEAN DEFAULT FALSE,
    validation_status TEXT DEFAULT 'CLEAN',
    raw_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Invoice Line Items Table
CREATE TABLE IF NOT EXISTS public.invoice_line_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID REFERENCES public.invoices(id) ON DELETE CASCADE,
    item_number INT,
    description TEXT,
    quantity NUMERIC(15, 4),
    unit_price NUMERIC(15, 2),
    amount NUMERIC(15, 2),
    math_match BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Validation Audit Issues Table (Task 3 Audit Findings)
CREATE TABLE IF NOT EXISTS public.validation_issues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID REFERENCES public.invoices(id) ON DELETE CASCADE,
    rule TEXT NOT NULL,
    issue_type TEXT,
    severity TEXT NOT NULL DEFAULT 'ERROR',
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for high-speed search and deduplication checks
CREATE INDEX IF NOT EXISTS idx_invoices_invoice_number ON public.invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_supplier ON public.invoices(supplier_name);
CREATE INDEX IF NOT EXISTS idx_invoices_created ON public.invoices(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_line_items_invoice ON public.invoice_line_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_issues_invoice ON public.validation_issues(invoice_id);

-- Enable Row Level Security (RLS) with full read/write for service keys
ALTER TABLE public.extraction_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoice_line_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.validation_issues ENABLE ROW LEVEL SECURITY;

-- Allow anon & service access policies for public schema
DROP POLICY IF EXISTS "Public access to extraction_jobs" ON public.extraction_jobs;
CREATE POLICY "Public access to extraction_jobs" ON public.extraction_jobs FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Public access to documents" ON public.documents;
CREATE POLICY "Public access to documents" ON public.documents FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Public access to document_pages" ON public.document_pages;
CREATE POLICY "Public access to document_pages" ON public.document_pages FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Public access to invoices" ON public.invoices;
CREATE POLICY "Public access to invoices" ON public.invoices FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Public access to invoice_line_items" ON public.invoice_line_items;
CREATE POLICY "Public access to invoice_line_items" ON public.invoice_line_items FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Public access to validation_issues" ON public.validation_issues;
CREATE POLICY "Public access to validation_issues" ON public.validation_issues FOR ALL USING (true) WITH CHECK (true);
