"""
CareGuide Document Summary & Q&A Engine.
Synthesizes executive summaries and answers specific questions from extracted pages and sheets
using strictly ONE Gemini API call per user query.
"""

import os
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

from careguide.usage_tracker import usage_tracker, extract_gemini_usage, calculate_cost_usd


ANALYZER_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-flash-latest",
]

MAX_CONTEXT_CHARS = 120_000


class DocumentAnalyzer:
    """Runs 1-call-per-query Gemini Summary and Question Answering over extracted document content."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10)

    @staticmethod
    def build_context(
        documents: List[Dict[str, Any]],
        max_chars: int = MAX_CONTEXT_CHARS,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Convert extracted documents (pages and sheets) into a compact, cited context string.
        Returns (context_text, metadata).
        """
        sections: List[str] = []
        pages_covered = 0
        sheets_covered = 0
        total_rows_covered = 0

        for doc in documents:
            fname = doc.get("filename", "unknown_document")
            pages = doc.get("pages") or []
            sheets = doc.get("sheets") or []

            if pages:
                doc_lines = [f"=== DOCUMENT: {fname} ({len(pages)} page(s)) ==="]
                for p in pages:
                    p_idx = p.get("index", "?")
                    p_text = (p.get("text") or "").strip()
                    if p_text:
                        doc_lines.append(f"[Page {p_idx}]\n{p_text}")
                        pages_covered += 1
                    elif p.get("error"):
                        doc_lines.append(f"[Page {p_idx}] (Extraction error: {p.get('error')})")
                sections.append("\n\n".join(doc_lines))

            if sheets:
                for s in sheets:
                    s_name = s.get("name", "Sheet")
                    cols = s.get("columns") or []
                    data = s.get("data") or []
                    row_count = s.get("row_count", len(data))
                    sheets_covered += 1
                    total_rows_covered += len(data)

                    sheet_lines = [
                        f"=== SPREADSHEET: {fname} | Sheet '{s_name}' ({row_count} rows, {len(cols)} cols) ===",
                        "Columns: " + " | ".join(str(c) for c in cols),
                    ]
                    for r_idx, row in enumerate(data, start=1):
                        if isinstance(row, dict):
                            row_vals = [str(row.get(c, "")) for c in cols]
                        elif isinstance(row, (list, tuple)):
                            row_vals = [str(v) for v in row]
                        else:
                            row_vals = [str(row)]
                        sheet_lines.append(f"Row {r_idx}: " + " | ".join(row_vals))
                    sections.append("\n".join(sheet_lines))

        full_text = "\n\n".join(sections).strip()
        truncated = False
        if len(full_text) > max_chars:
            full_text = (
                full_text[:max_chars]
                + f"\n\n[Note: Extracted text truncated at {max_chars:,} characters to fit a single-call prompt window.]"
            )
            truncated = True

        meta = {
            "documents_count": len(documents),
            "pages_covered": pages_covered,
            "sheets_covered": sheets_covered,
            "rows_covered": total_rows_covered,
            "context_chars": len(full_text),
            "truncated": truncated,
        }
        return full_text, meta

    @staticmethod
    def _build_prompt(context_text: str, mode: str, question: Optional[str]) -> str:
        """Build a grounded, anti-hallucination prompt for either Summary or Q&A."""
        if mode == "qa" and question and question.strip():
            task_block = (
                "TASK: Answer the user's specific question using ONLY the extracted document data below.\n"
                f"USER QUESTION: {question.strip()}\n\n"
                "RESPONSE GUIDELINES:\n"
                "1. Give a direct, clear, well-formatted answer first.\n"
                "2. Cite exact sources inline like [filename, Page X] or [Sheet 'Name', Row Y].\n"
                "3. Support both English and Bengali (বাংলা) content faithfully without altering names or numbers.\n"
                "4. If the extracted data does NOT contain the answer, clearly state that it is not found in the extracted text—never guess or invent details."
            )
        else:
            task_block = (
                "TASK: Provide a clear, comprehensive executive summary of the extracted document data below.\n\n"
                "FORMAT YOUR SUMMARY WITH THESE SECTIONS:\n"
                "### 1. Document Overview\n"
                "- Document type, scope, languages/formats, and total pages or sheets.\n\n"
                "### 2. Key Facts, Entities & Figures\n"
                "- Important names, organizations, dates, IDs, financial totals, and key metrics with source citations like [Page X] or [Sheet 'Name', Row Y].\n\n"
                "### 3. Main Takeaways\n"
                "- Concise bullet points capturing the core findings or conclusions.\n\n"
                "### 4. Gaps or Extraction Notes (if any)\n"
                "- Mention unreadable sections, truncated data, or missing fields only if present.\n\n"
                "STRICT GROUNDING: Use ONLY facts explicitly present in the extracted text below. Never fabricate values."
            )

        return (
            "You are CareGuide's Document Intelligence Assistant.\n"
            f"{task_block}\n\n"
            "EXTRACTED DOCUMENT DATA:\n"
            f"{context_text}"
        )

    def _call_gemini_once(self, prompt: str) -> Tuple[str, str, Dict[str, int]]:
        """
        Execute strictly ONE successful Gemini generation call (SDK first, HTTP REST fallback).
        Returns (answer_text, model_used, usage_dict).
        """
        last_err = ""
        t0 = time.time()

        # 1. Try google-genai SDK first
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            for model_name in ANALYZER_MODELS:
                try:
                    config = types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=4096,
                    )
                    res = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=config,
                    )
                    if res.text and res.text.strip():
                        usage = extract_gemini_usage(res)
                        usage_tracker.record_call(
                            feature="doc_analyze",
                            model=model_name,
                            prompt_tokens=usage["prompt_tokens"],
                            candidates_tokens=usage["candidates_tokens"],
                            total_tokens=usage["total_tokens"],
                            latency_ms=(time.time() - t0) * 1000.0,
                            status="success",
                        )
                        return res.text.strip(), model_name, usage
                except Exception as exc:
                    last_err = str(exc)
                    continue
        except Exception as exc:
            last_err = str(exc)

        # 2. Direct HTTP REST fallback (still 1 successful call)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
            },
        }

        for model_name in ANALYZER_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
            try:
                with httpx.Client(timeout=45.0) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            txt = "".join(p.get("text", "") for p in parts).strip()
                            if txt:
                                usage = extract_gemini_usage(data)
                                usage_tracker.record_call(
                                    feature="doc_analyze",
                                    model=model_name,
                                    prompt_tokens=usage["prompt_tokens"],
                                    candidates_tokens=usage["candidates_tokens"],
                                    total_tokens=usage["total_tokens"],
                                    latency_ms=(time.time() - t0) * 1000.0,
                                    status="success",
                                )
                                return txt, model_name, usage
                    else:
                        last_err = f"HTTP {resp.status_code}: {resp.text[:160]}"
            except Exception as exc:
                last_err = str(exc)

        usage_tracker.record_call(
            feature="doc_analyze",
            model=ANALYZER_MODELS[0],
            latency_ms=(time.time() - t0) * 1000.0,
            status="failed",
        )
        raise RuntimeError(f"Gemini analysis failed: {last_err}")

    def analyze(
        self,
        documents: List[Dict[str, Any]],
        mode: str = "summary",
        question: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a single-call Gemini summary or Q&A over extracted documents."""
        if not self.is_configured:
            raise ValueError("Gemini API key is required for AI Summary & Q&A.")

        normalized_mode = "qa" if (mode == "qa" or (question and question.strip() and mode != "summary")) else "summary"
        if normalized_mode == "qa" and (not question or not question.strip()):
            raise ValueError("Please enter a question to ask about the extracted data.")

        context_text, meta = self.build_context(documents)
        if not context_text:
            raise ValueError("No readable extracted text or table rows found to analyze.")

        start_t = time.time()
        prompt = self._build_prompt(context_text, normalized_mode, question)
        answer, model_used, usage = self._call_gemini_once(prompt)
        latency_ms = round((time.time() - start_t) * 1000, 1)
        cost_usd = calculate_cost_usd(model_used, usage["prompt_tokens"], usage["candidates_tokens"])

        return {
            "status": "success",
            "mode": normalized_mode,
            "question": question.strip() if question else None,
            "answer": answer,
            "model": model_used,
            "api_calls": 1,
            "usage": {**usage, "cost_usd": cost_usd},
            "latency_ms": latency_ms,
            "context_meta": meta,
        }
