"""
Gemini Vision AI fallback engine for Bangla (বাংলা) readability and low-confidence (<85%) OCR enhancement.
Supports google-genai SDK with direct HTTP REST fallback for maximum resilience.
"""

import os
import re
import time
import base64
from typing import Optional, Dict, Any, Tuple, List, Callable
import httpx

DEFAULT_BANGLA_PROMPT = (
    "You are an expert bilingual OCR and document transcription engine specializing in "
    "Bengali / Bangla (বাংলা) and English documents, including medical prescriptions, "
    "hospital lab reports, clinical records, and structured tables.\n\n"
    "Transcribe all text from this image with 100% exact fidelity:\n"
    "1. Accurately transcribe all Bengali / Bangla words, conjuncts (যুক্তবর্ণ), "
    "vowel signs (কার/ফলা), digits (বাংলা ও ইংরেজি), and medical abbreviations.\n"
    "2. Accurately transcribe all English words, patient details, and numeric values.\n"
    "3. Preserve the visual structure using clean Markdown (headings, bullet points, tables).\n"
    "4. Output ONLY the exact transcribed text without any conversational preamble or commentary."
)

FALLBACK_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-flash-latest"
]


def build_packet_prompt(elements: List[Dict[str, Any]]) -> str:
    """Build a multi-element prompt with strict delimiter instructions for up to 20 elements."""
    n = len(elements)
    headers = []
    for idx, elem in enumerate(elements, start=1):
        headers.append(
            f"Element {idx}/{n}: ID='{elem['element_id']}' | File='{elem['filename']}' | Page/Frame={elem['page_index']} (Reason: {elem.get('trigger_reason', 'fallback')})"
        )
    header_summary = "\n".join(headers)

    return (
        "You are an expert bilingual OCR and document transcription engine specializing in "
        "Bengali / Bangla (বাংলা) and English documents, including prescriptions, diagnostic sheets, "
        "and clinical records.\n\n"
        f"You are given a packet of {n} document page/image element(s) to transcribe:\n"
        f"{header_summary}\n\n"
        "TRANSCRIPTION RULES:\n"
        "1. For EACH element in order, transcribe all visible text, numbers, and tables with 100% exact fidelity.\n"
        "2. Accurately transcribe all Bengali / Bangla words, complex conjuncts (যুক্তবর্ণ), vowel marks (কার/ফলা), and numbers.\n"
        "3. Accurately transcribe all English words, clinical terms, dosages, abbreviations, and table values.\n"
        "4. Preserve formatting using clean Markdown headings, bullet points, and tables.\n\n"
        "MANDATORY ELEMENT SEPARATION FORMAT:\n"
        "You MUST clearly separate each element using the EXACT delimiter tags below:\n\n"
        "=== START ELEMENT [i/n]: {element_id} ===\n"
        "[Transcribed text/markdown for this element]\n"
        "=== END ELEMENT [i/n]: {element_id} ===\n\n"
        f"Rules: Output exactly {n} delimited sections in the same order as provided. "
        "Never merge text between different elements. Do NOT output any intro or commentary outside the tags."
    )


def demultiplex_packet_response(
    response_text: str,
    elements: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Parse Gemini's multi-element response and map each section back to its original element.
    Handles delimiters with brackets, without brackets, missing END tags, or single element fallbacks.
    """
    n = len(elements)
    results = []

    # If only 1 element was sent and no delimiter tags were returned, take full text
    if n == 1 and "=== START ELEMENT" not in response_text:
        elem = elements[0]
        return [{
            "element_id": elem["element_id"],
            "filename": elem["filename"],
            "page_index": elem["page_index"],
            "text": response_text.strip(),
            "confidence": 0.98,
            "status": "SUCCESS",
            "trigger_reason": elem.get("trigger_reason", "fallback")
        }]

    # Regex matching START tags up to the next START tag or end of text
    pattern = re.compile(
        r"===\s*START\s+ELEMENT\s*(?:\[\s*(\d+)(?:/\d+)?\s*\]|\s*(\d+)(?:/\d+)?)?\s*:\s*([^\n=]+?)\s*===(.*?)(?====\s*START\s+ELEMENT|\Z)",
        re.DOTALL | re.IGNORECASE
    )

    matches = pattern.findall(response_text)
    parsed_sections: Dict[str, str] = {}
    indexed_sections: Dict[int, str] = {}

    for m in matches:
        raw_idx = m[0] or m[1]
        elem_id = m[2].strip()
        body = m[3].strip()
        # Clean trailing END ELEMENT tag if present
        body = re.sub(r"===\s*END\s+ELEMENT.*?===", "", body, flags=re.IGNORECASE).strip()

        if elem_id:
            parsed_sections[elem_id] = body
        if raw_idx and raw_idx.isdigit():
            indexed_sections[int(raw_idx)] = body

    # Map parsed sections back to each element in order
    for idx, elem in enumerate(elements, start=1):
        target_id = elem["element_id"]
        found_text = None

        # 1. Exact ID match
        if target_id in parsed_sections:
            found_text = parsed_sections[target_id]
        else:
            # 2. Fuzzy ID match (substring)
            for k, v in parsed_sections.items():
                if target_id in k or k in target_id:
                    found_text = v
                    break

        # 3. Positional index match (1-based)
        if found_text is None and idx in indexed_sections:
            found_text = indexed_sections[idx]

        # 4. Fallback if not found
        if found_text is None:
            found_text = elem.get("original_text", "")
            status = "FAILED" if not found_text else "PARTIAL_SUCCESS"
            conf = elem.get("original_confidence", 0.0)
        else:
            status = "SUCCESS"
            conf = 0.98

        results.append({
            "element_id": elem["element_id"],
            "filename": elem["filename"],
            "page_index": elem["page_index"],
            "text": found_text.strip(),
            "confidence": conf,
            "status": status,
            "trigger_reason": elem.get("trigger_reason", "fallback")
        })

    return results


class GeminiOCRClient:
    """Client for routing low-confidence (<85%), corrupted, and Bangla documents to Gemini Vision AI."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10)

    def verify_key(self) -> Tuple[bool, str]:
        """Test if the provided Gemini API key is valid and active."""
        if not self.is_configured:
            return False, "No Gemini API key provided."

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
        try:
            with httpx.Client(timeout=8.0) as client:
                res = client.get(url)
                if res.status_code == 200:
                    return True, "Gemini API Key is valid and active."
                return False, f"API Key rejected: HTTP {res.status_code} ({res.text[:120]})"
        except Exception as e:
            return False, f"Connection error: {e}"

    def transcribe_packet(
        self,
        packet: List[Dict[str, Any]],
        progress_callback: Optional[Callable] = None
    ) -> List[Dict[str, Any]]:
        """
        Process a packet of up to 20 elements (Bangla, low-confidence <85%, or corrupted)
        in a single combined multi-image Gemini Vision AI call.
        Distinguishes each element using strict delimiter tags and demultiplexes the response.
        If packet has >20 elements, automatically processes in slices of 20.
        """
        if not packet:
            return []

        if not self.is_configured:
            return [
                {
                    "element_id": elem["element_id"],
                    "filename": elem["filename"],
                    "page_index": elem["page_index"],
                    "text": elem.get("original_text", ""),
                    "confidence": elem.get("original_confidence", 0.0),
                    "status": "FAILED",
                    "error": "Gemini API key is not configured.",
                    "trigger_reason": elem.get("trigger_reason", "fallback")
                }
                for elem in packet
            ]

        # If packet exceeds 20 elements, chunk into sub-packets of 20
        if len(packet) > 20:
            all_results = []
            for start_idx in range(0, len(packet), 20):
                sub_chunk = packet[start_idx:start_idx + 20]
                chunk_results = self.transcribe_packet(sub_chunk, progress_callback)
                all_results.extend(chunk_results)
            return all_results

        start_t = time.time()
        n = len(packet)
        packet_prompt = build_packet_prompt(packet)

        if progress_callback:
            progress_callback(
                "Gemini AI",
                f"Dispatching packet of {n} element(s) (Bangla/<85% conf/corrupted) to Gemini Vision AI..."
            )

        # 1. Try google-genai SDK first
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            contents = []
            for idx, elem in enumerate(packet, start=1):
                contents.append(f"=== ELEMENT {idx}/{n}: {elem['element_id']} ===")
                contents.append(types.Part.from_bytes(data=elem["image_bytes"], mime_type="image/jpeg"))
            contents.append(packet_prompt)

            for model_name in FALLBACK_MODELS:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=contents
                    )
                    if response.text and response.text.strip():
                        demuxed = demultiplex_packet_response(response.text.strip(), packet)
                        elapsed_ms = round((time.time() - start_t) * 1000, 2)
                        for r in demuxed:
                            r["elapsed_ms"] = elapsed_ms
                            r["engine"] = f"Gemini Vision AI ({model_name} - Packet Fallback)"
                            r["engine_model"] = model_name
                        return demuxed
                except Exception:
                    continue
        except Exception:
            pass

        # 2. Resilient Direct REST API Fallback
        parts = []
        for idx, elem in enumerate(packet, start=1):
            parts.append({"text": f"=== ELEMENT {idx}/{n}: {elem['element_id']} ==="})
            b64_img = base64.b64encode(elem["image_bytes"]).decode("utf-8")
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_img}})
        parts.append({"text": packet_prompt})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 8192
            }
        }

        timeout_sec = max(45.0, n * 5.0)
        last_error = "Unknown error"

        for model_name in FALLBACK_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
            try:
                with httpx.Client(timeout=timeout_sec) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            c_parts = candidates[0].get("content", {}).get("parts", [])
                            raw_text = "".join(p.get("text", "") for p in c_parts).strip()
                            if raw_text:
                                demuxed = demultiplex_packet_response(raw_text, packet)
                                elapsed_ms = round((time.time() - start_t) * 1000, 2)
                                for r in demuxed:
                                    r["elapsed_ms"] = elapsed_ms
                                    r["engine"] = f"Gemini Vision AI ({model_name} - Packet Fallback)"
                                    r["engine_model"] = model_name
                                return demuxed
                    else:
                        last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as e:
                last_error = str(e)

        # If all Gemini models failed, return original elements with error note
        return [
            {
                "element_id": elem["element_id"],
                "filename": elem["filename"],
                "page_index": elem["page_index"],
                "text": elem.get("original_text", ""),
                "confidence": elem.get("original_confidence", 0.0),
                "status": "FAILED",
                "error": f"Gemini packet fallback failed: {last_error}",
                "trigger_reason": elem.get("trigger_reason", "fallback")
            }
            for elem in packet
        ]

    def transcribe_image(
        self,
        image_bytes: bytes,
        filename: str,
        prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """Single-image transcription convenience wrapper delegating to packet architecture."""
        single_packet = [{
            "element_id": f"{filename}#p1",
            "filename": filename,
            "page_index": 1,
            "image_bytes": image_bytes,
            "trigger_reason": "single_image_call",
            "original_text": "",
            "original_confidence": 0.0
        }]
        results = self.transcribe_packet(single_packet)
        if results:
            res = results[0]
            return {
                "status": res["status"],
                "filename": filename,
                "text": res["text"],
                "confidence": res.get("confidence", 0.98),
                "elapsed_ms": res.get("elapsed_ms", 0.0),
                "engine": res.get("engine", "Gemini Vision AI (Single)"),
                "error": res.get("error")
            }
        return {"status": "FAILED", "error": "Transcription failed.", "text": ""}
