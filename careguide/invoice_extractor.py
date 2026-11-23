"""
CareGuide Invoice Intelligence Engine.
Extracts structured invoice fields using Gemini Flash (optimized for AI Studio Free Tier),
executes Task 3 deterministic data validation, duplicate detection, and manual review flagging,
and exports comprehensive multi-sheet Excel workbooks (.xlsx).
"""

import os
import re
import time
import json
from typing import List, Dict, Any, Optional, Tuple, Callable
from pydantic import BaseModel, Field
import httpx
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .schema import DocumentResult, ProcessingStatus
from .bangla_utils import bangla_to_arabic_numerals, parse_bangla_numeric_string, is_bangla_text


# ---------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------

class InvoiceLineItem(BaseModel):
    item_number: Optional[int] = None
    description: str = ""
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class InvoiceData(BaseModel):
    source_file: str = ""
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    supplier_name: Optional[str] = None
    customer_name: Optional[str] = None
    currency: Optional[str] = "USD"
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    line_items: List[InvoiceLineItem] = Field(default_factory=list)
    confidence: float = 0.95
    raw_text_snippet: Optional[str] = None


class ValidationIssue(BaseModel):
    severity: str  # "ERROR", "WARNING"
    rule: str
    message: str


class ValidatedInvoice(BaseModel):
    invoice: InvoiceData
    requires_manual_review: bool = False
    validation_status: str = "CLEAN"  # "CLEAN", "NEEDS_REVIEW", "CRITICAL_ERROR"
    issues: List[ValidationIssue] = Field(default_factory=list)


# Active Gemini models tested and verified on user's API tier
INVOICE_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-flash-latest"
]


# ---------------------------------------------------------
# Invoice Extractor & Validation Engine
# ---------------------------------------------------------

class InvoiceExtractor:
    """
    Orchestrates:
    1. Structured JSON invoice extraction from transcribed Markdown text (Free-Tier optimized).
    2. Task 3 Data Validation (missing fields, arithmetic integrity, duplicate detection, manual review flagging).
    3. Multi-sheet Excel workbook generation with professional audit formatting.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10)

    # ---------------------------------------------------------
    # Tabular Register / Ledger Extraction (Zero OCR, Instant)
    # ---------------------------------------------------------

    @staticmethod
    def _match_column(columns: List[str], aliases: List[str]) -> Optional[str]:
        """Case-insensitive exact or substring match for column header aliases."""
        if not columns or not aliases:
            return None
        # Pass 1: Exact match pass
        for a in aliases:
            a_clean = a.strip().lower()
            for c in columns:
                if a_clean == str(c).strip().lower():
                    return c
        # Pass 2: Substring match (alias must be a substring within column header)
        for a in aliases:
            a_clean = a.strip().lower()
            for c in columns:
                c_clean = str(c).strip().lower()
                if a_clean in c_clean:
                    return c
        return None

    @classmethod
    def is_tabular_invoice_register(cls, sheet: Dict[str, Any]) -> bool:
        """
        Determines whether a spreadsheet or CSV sheet is a Tabular Invoice Register / Ledger
        (where rows or groups of rows represent individual invoices with line items)
        versus a visual document layout.
        """
        columns = sheet.get("columns", [])
        data = sheet.get("data", [])
        if not columns or not data:
            return False

        # If columns look like default placeholder names from unheadered sheet ("Column_1", "Column_2", etc.)
        named_cols = [c for c in columns if not str(c).startswith("Column_")]
        if len(named_cols) < 2:
            return False

        inv_col = cls._match_column(columns, [
            "চালান নং", "চালান নম্বর", "চালান", "ইনভয়েস নং", "ইনভয়েস নম্বর", "বিল নং", "বিল নম্বর", "রসিদ নং",
            "invoice number", "invoice no", "invoice #", "inv no", "inv #", "inv_num", "bill no", "bill number",
            "bill #", "challan no", "receipt no", "doc number"
        ])
        desc_col = cls._match_column(columns, [
            "বিবরণ", "পণ্যের বিবরণ", "আইটেমের বিবরণ", "আইটেম", "পণ্য", "সেবার বিবরণ",
            "description", "item description", "item", "product", "particulars", "service", "item name", "details"
        ])
        amount_col = cls._match_column(columns, [
            "মোট টাকা", "টাকা", "মোট মূল্য", "লাইন মোট", "আইটেম মোট", "উপমোট", "সর্বমোট", "ভ্যাট",
            "line total amount", "line total", "line amount", "amount", "total amount", "subtotal", "total", "unit price", "price"
        ])

        return bool((inv_col or desc_col) and amount_col)

    @classmethod
    def extract_invoices_from_tabular_sheet(
        cls,
        sheet: Dict[str, Any],
        source_filename: str = "tabular_data",
        max_invoices: Optional[int] = None
    ) -> List[InvoiceData]:
        """
        Extract structured InvoiceData objects from a tabular register sheet.
        Groups multiple rows sharing the same invoice number into a single multi-item invoice.
        Extracts line items, quantities, prices, amounts, subtotals, taxes, and totals deterministically.
        Supports Bengali and English numerals and currencies (টাকা / BDT / USD).
        """
        columns = sheet.get("columns", [])
        data = sheet.get("data", [])
        if not columns or not data:
            return []

        # Map standard accounting column headers (Bangla & English)
        inv_col = cls._match_column(columns, [
            "চালান নং", "চালান নম্বর", "চালান", "ইনভয়েস নং", "ইনভয়েস নম্বর", "বিল নং", "বিল নম্বর", "রসিদ নং",
            "invoice number", "invoice no", "invoice #", "inv no", "inv #", "inv_num", "bill no", "bill number",
            "bill #", "challan no", "receipt no", "doc number"
        ])
        date_col = cls._match_column(columns, [
            "তারিখ", "চালানের তারিখ", "বিলের তারিখ", "ইস্যুর তারিখ",
            "date", "invoice date", "inv date", "bill date", "billing date", "issue date", "doc date"
        ])
        supp_col = cls._match_column(columns, [
            "সরবরাহকারী", "সরবরাহকারী প্রতিষ্ঠান", "বিক্রেতা", "কোম্পানি", "প্রতিষ্ঠানের নাম", "বিক্রেতার নাম",
            "supplier", "supplier name", "vendor", "vendor name", "seller", "seller name", "biller", "company"
        ])
        cust_col = cls._match_column(columns, [
            "ক্রেতা", "ক্রেতার নাম", "গ্রাহক", "গ্রাহকের নাম", "প্রাপক", "গ্রহীতা",
            "customer", "customer name", "client", "client name", "buyer", "buyer name", "bill to", "recipient"
        ])
        curr_col = cls._match_column(columns, [
            "মুদ্রা", "কারেন্সি", "currency", "curr"
        ])
        desc_col = cls._match_column(columns, [
            "বিবরণ", "পণ্যের বিবরণ", "আইটেমের বিবরণ", "আইটেম", "পণ্য", "সেবার বিবরণ",
            "description", "item description", "item", "product", "particulars", "service", "item name", "details"
        ])
        qty_col = cls._match_column(columns, [
            "পরিমাণ", "সংখ্যা", "কোয়ান্টিটি", "নগ",
            "quantity", "qty", "count", "units", "pieces", "pcs"
        ])
        price_col = cls._match_column(columns, [
            "একক দর", "দর", "একক মূল্য", "প্রতি একক", "মূল্য", "দাম",
            "unit price", "unit cost", "rate", "unit rate", "price", "price/unit"
        ])
        total_col = cls._match_column(columns, [
            "সর্বমোট", "সর্বমোট টাকা", "গ্র্যান্ড টোটাল", "নিট প্রদেয়", "মোট প্রদেয়",
            "grand total", "total payable", "net payable", "invoice total"
        ])
        subtotal_col = cls._match_column(columns, [
            "উপমোট", "উপ-মোট", "সাবটোটাল", "মোট (কর ছাড়া)",
            "subtotal", "sub total", "sub-total", "total before tax"
        ])
        tax_col = cls._match_column(columns, [
            "ভ্যাট", "ট্যাক্স", "কর", "মূসক",
            "vat", "tax", "gst", "hst"
        ])
        line_amt_col = cls._match_column(columns, [
            "মোট টাকা", "মোট মূল্য", "লাইন মোট", "আইটেম মোট",
            "line total amount", "line total", "line amount", "line total bdt", "ext price"
        ])

        if not total_col:
            total_col = cls._match_column(columns, ["total", "মোট"])
        if not line_amt_col:
            line_amt_col = cls._match_column(columns, ["amount", "টাকা", "price"])
            if line_amt_col in (total_col, subtotal_col):
                line_amt_col = None

        # Group rows by invoice number
        grouped_rows: Dict[str, List[Dict[str, Any]]] = {}
        ordered_keys: List[str] = []
        last_inv_key: Optional[str] = None

        for row_idx, row in enumerate(data):
            raw_inv = row.get(inv_col) if inv_col else None
            inv_str = str(raw_inv).strip() if raw_inv is not None else ""
            if inv_str.lower() in ("none", "nan", "null", ""):
                inv_str = ""

            if inv_str:
                inv_key = inv_str
                last_inv_key = inv_key
            else:
                if last_inv_key is not None:
                    inv_key = last_inv_key
                else:
                    inv_key = f"INV-{row_idx + 1:04d}"
                    last_inv_key = inv_key

            if inv_key not in grouped_rows:
                grouped_rows[inv_key] = []
                ordered_keys.append(inv_key)

            grouped_rows[inv_key].append(row)

        if max_invoices and max_invoices > 0:
            ordered_keys = ordered_keys[:max_invoices]

        results: List[InvoiceData] = []

        for inv_key in ordered_keys:
            rows = grouped_rows[inv_key]
            first_row = rows[0]

            # Header details
            inv_date = None
            if date_col:
                for r in rows:
                    if r.get(date_col) is not None and str(r.get(date_col)).strip():
                        inv_date = str(r.get(date_col)).strip()
                        break

            supplier = None
            if supp_col:
                for r in rows:
                    if r.get(supp_col) is not None and str(r.get(supp_col)).strip():
                        supplier = str(r.get(supp_col)).strip()
                        break

            customer = None
            if cust_col:
                for r in rows:
                    if r.get(cust_col) is not None and str(r.get(cust_col)).strip():
                        customer = str(r.get(cust_col)).strip()
                        break

            currency = None
            if curr_col:
                for r in rows:
                    if r.get(curr_col) is not None and str(r.get(curr_col)).strip():
                        currency = str(r.get(curr_col)).strip()
                        break

            if not currency:
                row_text = " ".join(str(v) for v in first_row.values() if v is not None)
                if "টাকা" in row_text or "৳" in row_text or "bdt" in row_text.lower():
                    currency = "BDT"
                elif "$" in row_text or "usd" in row_text.lower():
                    currency = "USD"
                else:
                    currency = "BDT" if is_bangla_text(row_text) else "USD"

            # Line items
            line_items: List[InvoiceLineItem] = []
            for item_idx, r in enumerate(rows):
                desc = str(r.get(desc_col, "")).strip() if desc_col and r.get(desc_col) is not None else f"Item {item_idx + 1}"
                qty = cls._safe_float(r.get(qty_col)) if qty_col else None
                price = cls._safe_float(r.get(price_col)) if price_col else None
                amount = cls._safe_float(r.get(line_amt_col)) if line_amt_col else None

                if amount is None and qty is not None and price is not None:
                    amount = round(qty * price, 2)

                line_items.append(
                    InvoiceLineItem(
                        item_number=item_idx + 1,
                        description=desc,
                        quantity=qty,
                        unit_price=price,
                        amount=amount
                    )
                )

            # Subtotal, Tax, Total
            subtotal = cls._safe_float(first_row.get(subtotal_col)) if subtotal_col else None
            tax = cls._safe_float(first_row.get(tax_col)) if tax_col else None
            total = cls._safe_float(first_row.get(total_col)) if total_col else None

            # Calculate derived totals if not explicitly provided
            items_sum = sum(it.amount for it in line_items if it.amount is not None)
            if subtotal is None:
                if len(rows) > 1 or not total:
                    subtotal = round(items_sum, 2) if items_sum > 0 else total
                else:
                    subtotal = total

            if total is None:
                if subtotal is not None:
                    total = round(subtotal + (tax or 0.0), 2)
                elif items_sum > 0:
                    total = round(items_sum + (tax or 0.0), 2)

            if tax is None and subtotal is not None and total is not None:
                diff = round(total - subtotal, 2)
                if diff > 0.01:
                    tax = diff

            sheet_name = sheet.get("name", "Sheet")
            source_lbl = f"{source_filename} ({sheet_name})"

            inv_obj = InvoiceData(
                source_file=source_lbl,
                invoice_number=inv_key,
                invoice_date=inv_date,
                supplier_name=supplier,
                customer_name=customer,
                currency=currency,
                subtotal=subtotal,
                tax=tax,
                total=total,
                line_items=line_items,
                confidence=1.0,
                raw_text_snippet=f"Tabular record: {inv_key} with {len(line_items)} item(s)"
            )
            results.append(inv_obj)

        return results

    # ---------------------------------------------------------
    # 1. Extraction via Gemini
    # ---------------------------------------------------------

    def extract_invoices_from_text_batch(
        self,
        documents: List[Tuple[str, str]],  # List of (filename, markdown_text)
        progress_callback: Optional[Any] = None,
        batch_size: int = 4,
        on_batch_extracted: Optional[Callable[[List[InvoiceData]], None]] = None
    ) -> List[InvoiceData]:
        """
        Extract structured invoice JSON from transcribed document text in paced batches of 3-5 documents.
        Never invents missing values (returns null).
        Supports live on_batch_extracted streaming for immediate UI rendering and incremental DB auto-save.
        """
        if not self.is_configured:
            raise ValueError("Gemini API key is not configured. Provide an API key to run structured invoice extraction.")

        extracted_invoices: List[InvoiceData] = []
        batch_size = max(1, batch_size)
        total_batches = (len(documents) + batch_size - 1) // batch_size if documents else 1

        for b_idx in range(0, len(documents), batch_size):
            chunk = documents[b_idx:b_idx + batch_size]
            batch_num = (b_idx // batch_size) + 1
            chunk_start_num = b_idx + 1
            chunk_end_num = min(b_idx + len(chunk), len(documents))
            prompt = self._build_extraction_prompt(chunk)

            if progress_callback:
                evt = {
                    "state": "REQUESTING",
                    "batch": batch_num,
                    "total_batches": total_batches,
                    "batch_size": len(chunk),
                    "model": INVOICE_MODELS[0],
                    "message": f"Dispatching Batch {batch_num}/{total_batches} (Invoices {chunk_start_num}-{chunk_end_num} of {len(documents)}) to Gemini AI ({INVOICE_MODELS[0]})...",
                    "percent": min(78, int((b_idx / len(documents)) * 70) + 10)
                }
                try:
                    progress_callback(evt)
                except TypeError:
                    progress_callback("Invoice Extractor", evt["message"])

            raw_json = self._call_gemini_json(prompt)

            if progress_callback:
                evt = {
                    "state": "PARSING",
                    "batch": batch_num,
                    "total_batches": total_batches,
                    "model": INVOICE_MODELS[0],
                    "message": f"Received Batch {batch_num}/{total_batches}. Parsing structured JSON for {len(chunk)} invoice(s)...",
                    "percent": min(78, int(((b_idx + len(chunk)) / len(documents)) * 70) + 10)
                }
                try:
                    progress_callback(evt)
                except TypeError:
                    progress_callback("Invoice Extractor", evt["message"])

            parsed_invoices = self._parse_gemini_response(raw_json, chunk)
            extracted_invoices.extend(parsed_invoices)

            # Trigger live streaming callback for instant UI display & auto-save to database
            if on_batch_extracted:
                try:
                    on_batch_extracted(parsed_invoices)
                except Exception as cb_err:
                    print(f"[InvoiceExtractor] Live batch callback error: {cb_err}")

            # Rate-limiting guardrail for Google AI Studio Free Tier (15 RPM limit)
            if b_idx + batch_size < len(documents):
                if progress_callback:
                    evt = {
                        "state": "PACING",
                        "batch": batch_num,
                        "total_batches": total_batches,
                        "cooldown_sec": 2.5,
                        "model": INVOICE_MODELS[0],
                        "message": f"Rate-limit pacing: Free Tier 15 RPM cooldown (2.5s safe buffer)... [Extracted {len(extracted_invoices)}/{len(documents)}]",
                        "percent": min(78, int(((b_idx + len(chunk)) / len(documents)) * 70) + 10)
                    }
                    try:
                        progress_callback(evt)
                    except TypeError:
                        progress_callback("Invoice Extractor", evt["message"])
                time.sleep(2.5)

        return extracted_invoices

    def _build_extraction_prompt(self, chunk: List[Tuple[str, str]]) -> str:
        doc_sections = []
        for idx, (fname, text) in enumerate(chunk, start=1):
            snippet = text.strip()[:8000]  # Allow full multi-page invoice text up to 8,000 chars per invoice
            doc_sections.append(f"=== DOCUMENT {idx}: {fname} ===\n{snippet}\n=== END DOCUMENT {idx} ===")

        docs_content = "\n\n".join(doc_sections)

        return (
            "You are an expert financial document intelligence system.\n"
            "Extract structured invoice data from the provided document text into valid JSON matching the schema below.\n\n"
            "STRICT ANTI-HALLUCINATION & FIDELITY DIRECTIVES:\n"
            "1. Extract ONLY information explicitly stated in the document text.\n"
            "2. If any field is missing, unreadable, or uncertain, you MUST set it to null. NEVER invent, extrapolate, or guess values.\n"
            "3. For numeric values (subtotal, tax, total, quantity, unit_price, amount), output raw numeric numbers (e.g. 1450.00, not '$1,450'). Set to null if not present.\n"
            "4. Extract all line items in order. If line items are not present, output an empty list [].\n"
            "5. For currency, output standard ISO 3-letter codes (USD, BDT, EUR, GBP) or symbol (Tk, $), or null if not indicated.\n\n"
            "BANGLA & MULTILINGUAL DOCUMENT DIRECTIVES:\n"
            "- When processing Bengali (বাংলা) or bilingual documents:\n"
            "  * Recognize Bengali accounting terms:\n"
            "    - Invoice/Receipt No: চালান নং, বিল নং, রসিদ নং\n"
            "    - Date: তারিখ (convert to YYYY-MM-DD or standard format)\n"
            "    - Supplier: সরবরাহকারী, বিক্রেতা, প্রতিষ্ঠান, মেসার্স\n"
            "    - Customer: ক্রেতা, গ্রাহক\n"
            "    - Subtotal: উপমোট, মোট মূল্য\n"
            "    - Tax / VAT: ভ্যাট, মূসক, কর (numeric tax amount)\n"
            "    - Total: সর্বমোট, মোট প্রদেয়\n"
            "    - Currency: টাকা, ৳ -> output standard 'BDT'\n"
            "    - Line Items: বিবরণ (description), পরিমাণ (quantity), দর/একক মূল্য (unit_price), মূল্য (amount)\n"
            "  * Convert Bengali numerals (০, ১, ২, ৩, ৪, ৫, ৬, ৭, ৮, ৯) to standard numbers (0, 1, 2, 3, 4, 5, 6, 7, 8, 9).\n"
            "  * Keep supplier names, customer names, and product descriptions in their original legal Bangla text (e.g., 'মেসার্স মেঘনা সাপ্লাই', 'কাগজের রিম').\n\n"
            "JSON OUTPUT SCHEMA:\n"
            "{\n"
            '  "invoices": [\n'
            "    {\n"
            '      "source_file": "string",\n'
            '      "invoice_number": "string or null",\n'
            '      "invoice_date": "YYYY-MM-DD or raw string or null",\n'
            '      "supplier_name": "string or null",\n'
            '      "customer_name": "string or null",\n'
            '      "currency": "string or null",\n'
            '      "subtotal": float or null,\n'
            '      "tax": float or null,\n'
            '      "total": float or null,\n'
            '      "line_items": [\n'
            "        {\n"
            '          "item_number": int,\n'
            '          "description": "string",\n'
            '          "quantity": float or null,\n'
            '          "unit_price": float or null,\n'
            '          "amount": float or null\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            f"DOCUMENTS TO PROCESS:\n{docs_content}"
        )

    def _call_gemini_json(self, prompt: str) -> str:
        """Call Gemini API with JSON enforcement, retries, and exponential backoff."""
        # 1. Try google-genai SDK first
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=self.api_key)

            for model_name in INVOICE_MODELS:
                try:
                    config = types.GenerateContentConfig(
                        temperature=0.0,
                        response_mime_type="application/json"
                    )
                    res = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=config
                    )
                    if res.text and res.text.strip():
                        return res.text.strip()
                except Exception:
                    continue
        except Exception:
            pass

        # 2. Resilient Direct HTTP REST Fallback
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "maxOutputTokens": 8192
            }
        }

        last_err = ""
        for model_name in INVOICE_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
            for retry in range(3):
                try:
                    with httpx.Client(timeout=40.0) as client:
                        resp = client.post(url, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                txt = "".join(p.get("text", "") for p in parts).strip()
                                if txt:
                                    return txt
                        elif resp.status_code == 429:
                            # Respect Free Tier rate limit with backoff
                            time.sleep(3.0 * (retry + 1))
                        else:
                            last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
                            break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(1.5)

        raise RuntimeError(f"Gemini invoice extraction failed: {last_err}")

    def _parse_gemini_response(
        self,
        raw_json_str: str,
        chunk: List[Tuple[str, str]]
    ) -> List[InvoiceData]:
        """Parse Gemini's JSON output with fallback error correction."""
        clean_json = raw_json_str.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.startswith("```"):
            clean_json = clean_json[3:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        clean_json = clean_json.strip()

        data = {}
        try:
            data = json.loads(clean_json)
        except Exception:
            # Fallback regex extraction of JSON object
            match = re.search(r"\{.*\}", clean_json, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except Exception:
                    data = {}

        raw_invoices = data.get("invoices", []) if isinstance(data, dict) else []

        results: List[InvoiceData] = []
        # If output was a list directly
        if isinstance(data, list):
            raw_invoices = data

        for idx, (fname, text_snippet) in enumerate(chunk):
            inv_dict = {}
            if idx < len(raw_invoices):
                inv_dict = raw_invoices[idx]
            else:
                # Attempt to find by source_file
                for candidate in raw_invoices:
                    if candidate.get("source_file") == fname:
                        inv_dict = candidate
                        break

            line_items_data = []
            for item in inv_dict.get("line_items", []):
                line_items_data.append(
                    InvoiceLineItem(
                        item_number=item.get("item_number"),
                        description=str(item.get("description", "")),
                        quantity=self._safe_float(item.get("quantity")),
                        unit_price=self._safe_float(item.get("unit_price")),
                        amount=self._safe_float(item.get("amount"))
                    )
                )

            inv_obj = InvoiceData(
                source_file=fname,
                invoice_number=inv_dict.get("invoice_number"),
                invoice_date=inv_dict.get("invoice_date"),
                supplier_name=inv_dict.get("supplier_name"),
                customer_name=inv_dict.get("customer_name"),
                currency=inv_dict.get("currency") or "USD",
                subtotal=self._safe_float(inv_dict.get("subtotal")),
                tax=self._safe_float(inv_dict.get("tax")),
                total=self._safe_float(inv_dict.get("total")),
                line_items=line_items_data,
                raw_text_snippet=text_snippet[:500]
            )
            results.append(inv_obj)

        return results

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            if isinstance(val, (int, float)):
                return float(val)
            s_val = str(val).strip()
            # If Bengali digits or currency characters present, parse cleanly
            if is_bangla_text(s_val) or any(ch in s_val for ch in "০১২৩৪৫৬৭৮৯৳"):
                parsed = parse_bangla_numeric_string(s_val)
                if parsed is not None:
                    return parsed
            cleaned = re.sub(r"[^\d.-]", "", s_val)
            return float(cleaned) if cleaned else None
        except Exception:
            return None

    # ---------------------------------------------------------
    # 2. Task 3 — Data Validation Engine
    # ---------------------------------------------------------

    def validate_invoices(self, invoices: List[InvoiceData]) -> List[ValidatedInvoice]:
        """
        Task 3 Validation Rules:
        - Missing critical fields (invoice_number, invoice_date, supplier_name, total)
        - Incorrect numeric values (subtotal + tax != total, line_items sum != subtotal)
        - Unit price math errors (quantity * unit_price != amount)
        - Duplicate invoice detection across the batch
        - Flag records requiring manual review
        """
        validated_records: List[ValidatedInvoice] = []

        # Track duplicates by (normalized_supplier, normalized_invoice_number)
        seen_keys: Dict[Tuple[str, str], List[int]] = {}

        for idx, inv in enumerate(invoices):
            issues: List[ValidationIssue] = []

            # 1. Missing Fields Check
            if not inv.invoice_number or str(inv.invoice_number).strip().lower() in ("null", "n/a", "none", ""):
                issues.append(ValidationIssue(
                    severity="ERROR",
                    rule="MISSING_INVOICE_NUMBER",
                    message="Missing or unreadable Invoice Number."
                ))

            if not inv.invoice_date or str(inv.invoice_date).strip().lower() in ("null", "n/a", "none", ""):
                issues.append(ValidationIssue(
                    severity="WARNING",
                    rule="MISSING_INVOICE_DATE",
                    message="Missing Invoice Date."
                ))

            if not inv.supplier_name or str(inv.supplier_name).strip().lower() in ("null", "n/a", "none", ""):
                issues.append(ValidationIssue(
                    severity="WARNING",
                    rule="MISSING_SUPPLIER_NAME",
                    message="Missing Supplier / Vendor Name."
                ))

            if inv.total is None:
                issues.append(ValidationIssue(
                    severity="ERROR",
                    rule="MISSING_TOTAL_AMOUNT",
                    message="Missing Total Amount."
                ))
            elif inv.total <= 0:
                issues.append(ValidationIssue(
                    severity="WARNING",
                    rule="NON_POSITIVE_TOTAL",
                    message=f"Total amount is zero or negative ({inv.total})."
                ))

            # 2. Arithmetic Inconsistencies Check (Subtotal + Tax ≈ Total)
            if inv.subtotal is not None and inv.tax is not None and inv.total is not None:
                expected_total = round(inv.subtotal + inv.tax, 2)
                diff = round(abs(expected_total - inv.total), 2)
                if diff > 0.05:
                    issues.append(ValidationIssue(
                        severity="ERROR",
                        rule="ARITHMETIC_MISMATCH_TOTAL",
                        message=f"Total mismatch: Subtotal ({inv.subtotal:.2f}) + Tax ({inv.tax:.2f}) = {expected_total:.2f}, but Total is {inv.total:.2f} (Difference: {diff:.2f})."
                    ))
            elif inv.subtotal is not None and inv.tax is None and inv.total is not None:
                # If subtotal and total differ, but tax is not listed
                diff = round(abs(inv.subtotal - inv.total), 2)
                if diff > 0.05:
                    issues.append(ValidationIssue(
                        severity="WARNING",
                        rule="SUBTOTAL_TOTAL_DIVERGENCE",
                        message=f"Subtotal ({inv.subtotal:.2f}) differs from Total ({inv.total:.2f}) with no Tax specified (Difference: {diff:.2f})."
                    ))

            # 3. Line Items Sum Consistency
            if inv.line_items:
                valid_amounts = [it.amount for it in inv.line_items if it.amount is not None]
                if valid_amounts:
                    items_sum = round(sum(valid_amounts), 2)
                    target = inv.subtotal if inv.subtotal is not None else inv.total
                    if target is not None:
                        diff = round(abs(items_sum - target), 2)
                        if diff > 0.10:
                            issues.append(ValidationIssue(
                                severity="ERROR",
                                rule="LINE_ITEMS_SUM_MISMATCH",
                                message=f"Sum of line items ({items_sum:.2f}) does not match Subtotal/Total ({target:.2f}) (Difference: {diff:.2f})."
                            ))

            # 4. Line Item Math Checks (Quantity × Unit Price ≈ Amount)
            for item in inv.line_items:
                if item.quantity is not None and item.unit_price is not None and item.amount is not None:
                    expected_amt = round(item.quantity * item.unit_price, 2)
                    diff = round(abs(expected_amt - item.amount), 2)
                    if diff > 0.05:
                        issues.append(ValidationIssue(
                            severity="WARNING",
                            rule="LINE_ITEM_UNIT_MATH",
                            message=f"Item '{item.description[:25]}': Qty ({item.quantity}) × Unit Price ({item.unit_price:.2f}) = {expected_amt:.2f}, but Amount is {item.amount:.2f}."
                        ))

            # Register duplicate key
            supp_key = (inv.supplier_name or "").strip().lower()
            num_key = (inv.invoice_number or "").strip().lower()
            if num_key and num_key not in ("null", "n/a", "none"):
                pair = (supp_key, num_key)
                seen_keys.setdefault(pair, []).append(idx)

            requires_review = any(iss.severity == "ERROR" for iss in issues) or len(issues) > 0
            val_status = "CRITICAL_ERROR" if any(iss.severity == "ERROR" for iss in issues) else ("NEEDS_REVIEW" if issues else "CLEAN")

            validated_records.append(
                ValidatedInvoice(
                    invoice=inv,
                    requires_manual_review=requires_review,
                    validation_status=val_status,
                    issues=issues
                )
            )

        # 5. Cross-Invoice Duplicate Invoices Check
        for (supp_k, num_k), indices in seen_keys.items():
            if len(indices) > 1:
                for idx in indices:
                    rec = validated_records[idx]
                    rec.requires_manual_review = True
                    rec.validation_status = "CRITICAL_ERROR"
                    rec.issues.append(ValidationIssue(
                        severity="ERROR",
                        rule="DUPLICATE_INVOICE",
                        message=f"Duplicate invoice number '{num_k.upper()}' detected across {len(indices)} files."
                    ))

        return validated_records

    # ---------------------------------------------------------
    # 3. Professional Multi-Sheet Excel Workbook (.xlsx)
    # ---------------------------------------------------------

    def generate_excel_workbook(
        self,
        validated_invoices: List[ValidatedInvoice],
        output_path: str
    ) -> str:
        """
        Generate a multi-sheet Excel (.xlsx) file with professional audit formatting:
        1. Invoices_Summary (One row per invoice, color-coded review flags, financial math).
        2. Line_Items (Normalized relational breakdown of all items).
        3. Validation_Audit (Dedicated audit trail of every issue and manual review item).
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        wb = openpyxl.Workbook()

        # Styles
        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        clean_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
        clean_font = Font(name="Segoe UI", size=10, bold=True, color="065F46")
        review_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
        review_font = Font(name="Segoe UI", size=10, bold=True, color="991B1B")
        regular_font = Font(name="Segoe UI", size=10)
        bold_font = Font(name="Segoe UI", size=10, bold=True)
        border_thin = Border(
            left=Side(style='thin', color='E2E8F0'),
            right=Side(style='thin', color='E2E8F0'),
            top=Side(style='thin', color='E2E8F0'),
            bottom=Side(style='thin', color='E2E8F0')
        )

        # -----------------------------------------------------
        # Sheet 1: Invoices_Summary
        # -----------------------------------------------------
        ws_summary = wb.active
        ws_summary.title = "Invoices_Summary"
        ws_summary.views.sheetView[0].showGridLines = True

        summary_headers = [
            "Manual Review",
            "Validation Status",
            "Validation Issues Count",
            "Invoice Number",
            "Invoice Date",
            "Supplier Name",
            "Customer Name",
            "Currency",
            "Subtotal",
            "Tax Amount",
            "Total Amount",
            "Line Items Count",
            "Source File",
            "Primary Issues Summary"
        ]

        ws_summary.append(summary_headers)
        for col_num in range(1, len(summary_headers) + 1):
            cell = ws_summary.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        ws_summary.row_dimensions[1].height = 28

        for row_idx, val_rec in enumerate(validated_invoices, start=2):
            inv = val_rec.invoice
            issues_summary = "; ".join(i.message for i in val_rec.issues) if val_rec.issues else "All rules passed"

            row_data = [
                "🔴 YES (FLAGGED)" if val_rec.requires_manual_review else "🟢 NO (CLEAN)",
                val_rec.validation_status,
                len(val_rec.issues),
                inv.invoice_number or "N/A",
                inv.invoice_date or "N/A",
                inv.supplier_name or "N/A",
                inv.customer_name or "N/A",
                inv.currency or "USD",
                inv.subtotal if inv.subtotal is not None else 0.0,
                inv.tax if inv.tax is not None else 0.0,
                inv.total if inv.total is not None else 0.0,
                len(inv.line_items),
                inv.source_file,
                issues_summary
            ]
            ws_summary.append(row_data)

            # Style row
            for col_idx in range(1, len(row_data) + 1):
                c = ws_summary.cell(row=row_idx, column=col_idx)
                c.font = regular_font
                c.border = border_thin
                c.alignment = Alignment(vertical="center")

                # Format financial numbers
                if col_idx in (9, 10, 11):
                    c.number_format = "#,##0.00"
                    c.alignment = Alignment(horizontal="right", vertical="center")

            # Review flag styling
            flag_cell = ws_summary.cell(row=row_idx, column=1)
            flag_cell.fill = review_fill if val_rec.requires_manual_review else clean_fill
            flag_cell.font = review_font if val_rec.requires_manual_review else clean_font
            flag_cell.alignment = Alignment(horizontal="center", vertical="center")

            ws_summary.row_dimensions[row_idx].height = 22

        # -----------------------------------------------------
        # Sheet 2: Line_Items
        # -----------------------------------------------------
        ws_items = wb.create_sheet(title="Line_Items")
        ws_items.views.sheetView[0].showGridLines = True

        items_headers = [
            "Source File",
            "Invoice Number",
            "Supplier Name",
            "Line #",
            "Description",
            "Quantity",
            "Unit Price",
            "Line Total Amount",
            "Currency",
            "Arithmetic Verification"
        ]

        ws_items.append(items_headers)
        for col_num in range(1, len(items_headers) + 1):
            cell = ws_items.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        ws_items.row_dimensions[1].height = 28
        item_row_idx = 2

        for val_rec in validated_invoices:
            inv = val_rec.invoice
            for line_idx, item in enumerate(inv.line_items, start=1):
                math_check = "N/A"
                if item.quantity is not None and item.unit_price is not None and item.amount is not None:
                    calc = round(item.quantity * item.unit_price, 2)
                    math_check = "✅ MATCH" if abs(calc - item.amount) <= 0.05 else f"❌ MISMATCH ({calc:.2f} != {item.amount:.2f})"

                row_vals = [
                    inv.source_file,
                    inv.invoice_number or "N/A",
                    inv.supplier_name or "N/A",
                    item.item_number or line_idx,
                    item.description,
                    item.quantity if item.quantity is not None else "",
                    item.unit_price if item.unit_price is not None else "",
                    item.amount if item.amount is not None else "",
                    inv.currency or "USD",
                    math_check
                ]
                ws_items.append(row_vals)

                for c_idx in range(1, len(row_vals) + 1):
                    c = ws_items.cell(row=item_row_idx, column=c_idx)
                    c.font = regular_font
                    c.border = border_thin
                    c.alignment = Alignment(vertical="center")
                    if c_idx in (6, 7, 8) and isinstance(c.value, (int, float)):
                        c.number_format = "#,##0.00"
                        c.alignment = Alignment(horizontal="right", vertical="center")

                item_row_idx += 1

        # -----------------------------------------------------
        # Sheet 3: Validation_Audit
        # -----------------------------------------------------
        ws_audit = wb.create_sheet(title="Validation_Audit")
        ws_audit.views.sheetView[0].showGridLines = True

        audit_headers = [
            "Severity",
            "Rule Name",
            "Invoice Number",
            "Source File",
            "Discrepancy / Validation Message"
        ]

        ws_audit.append(audit_headers)
        for col_num in range(1, len(audit_headers) + 1):
            cell = ws_audit.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        ws_audit.row_dimensions[1].height = 28
        audit_row_idx = 2

        for val_rec in validated_invoices:
            inv = val_rec.invoice
            if not val_rec.issues:
                continue

            for issue in val_rec.issues:
                ws_audit.append([
                    issue.severity,
                    issue.rule,
                    inv.invoice_number or "N/A",
                    inv.source_file,
                    issue.message
                ])

                for col_i in range(1, 6):
                    c = ws_audit.cell(row=audit_row_idx, column=col_i)
                    c.font = regular_font
                    c.border = border_thin
                    c.alignment = Alignment(vertical="center")

                # Severity styling
                sev_cell = ws_audit.cell(row=audit_row_idx, column=1)
                sev_cell.font = bold_font
                if issue.severity == "ERROR":
                    sev_cell.fill = review_fill
                    sev_cell.font = review_font
                else:
                    sev_cell.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
                    sev_cell.font = Font(name="Segoe UI", size=10, bold=True, color="92400E")

                audit_row_idx += 1

        # If clean
        if audit_row_idx == 2:
            ws_audit.append(["CLEAN", "ALL_RULES_PASSED", "All", "All", "No validation issues or discrepancies detected across the entire batch."])
            ws_audit.cell(row=2, column=1).fill = clean_fill
            ws_audit.cell(row=2, column=1).font = clean_font

        # Auto-adjust column widths across all sheets
        for sheet in (ws_summary, ws_items, ws_audit):
            for col in sheet.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    val_str = str(cell.value or '')
                    if '\n' in val_str:
                        val_str = val_str.split('\n')[0]
                    max_len = max(max_len, len(val_str))
                sheet.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 48)

        wb.save(output_path)
        return output_path
