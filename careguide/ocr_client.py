"""
HTTP client for communicating with the remote baidu/Unlimited-OCR Kaggle/Colab Server.
Includes automatic local RapidOCR fallback, Gemini Vision AI fallback for Bangla / low-confidence (<85%),
retries, and timeout guardrails.
"""

import time
from typing import Optional, Dict, Any, List, Tuple
import httpx
from .gemini_fallback import GeminiOCRClient


class OCRClient:
    """Client for dispatching preprocessed document images to Unlimited-OCR, RapidOCR, and Gemini Vision AI."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3
    ):
        self.api_url = api_url.rstrip("/") if api_url else None
        self.timeout = timeout
        self.max_retries = max_retries
        self._local_ocr = None
        self.gemini_client = GeminiOCRClient(api_key=gemini_api_key)

    @property
    def is_connected(self) -> bool:
        return self.api_url is not None

    def _get_local_ocr(self):
        """Lazily initialize the local RapidOCR engine."""
        if self._local_ocr is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._local_ocr = RapidOCR()
            except Exception:
                self._local_ocr = False
        return self._local_ocr if self._local_ocr is not False else None

    def local_ocr(self, image_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Perform high-accuracy on-device OCR using RapidOCR (PP-OCRv4 ONNX)."""
        engine = self._get_local_ocr()
        if engine is None:
            return {
                "status": "FAILED",
                "error": "No local OCR engine available.",
                "text": "",
                "confidence": 0.0
            }

        try:
            start_t = time.time()
            result, _ = engine(image_bytes)
            elapsed_ms = round((time.time() - start_t) * 1000, 2)

            if not result:
                return {
                    "status": "SUCCESS",
                    "filename": filename,
                    "text": "",
                    "confidence": 0.0,
                    "elapsed_ms": elapsed_ms,
                    "engine": "RapidOCR (Local)"
                }

            text_lines = [line[1] for line in result]
            full_text = "\n".join(text_lines)
            avg_conf = float(sum(line[2] for line in result) / len(result))

            return {
                "status": "SUCCESS",
                "filename": filename,
                "text": full_text,
                "confidence": round(avg_conf, 2),
                "elapsed_ms": elapsed_ms,
                "engine": "RapidOCR (Local)"
            }
        except Exception as e:
            return {
                "status": "FAILED",
                "error": f"Local OCR error: {e}",
                "text": "",
                "confidence": 0.0
            }

    @staticmethod
    def detect_tunnel(url: Optional[str]) -> Dict[str, str]:
        """Detect connection architecture (Tailscale, Cloudflare, Localhost, etc.)."""
        if not url:
            return {
                "type": "none",
                "label": "Standby / Local OCR",
                "protocol": "In-Process / Direct",
                "security": "Local Device Only",
                "timeout": "Unlimited"
            }
        u = url.lower().strip()
        if "ts.net" in u or "100." in u:
            return {
                "type": "tailscale",
                "label": "Tailscale WireGuard Mesh",
                "protocol": "WireGuard P2P (Encrypted)",
                "security": "Zero Public Exposure (Tailnet Only)",
                "timeout": "Unlimited (>100s Safe)"
            }
        elif "trycloudflare.com" in u or "cloudflare" in u:
            return {
                "type": "cloudflare",
                "label": "Cloudflare Quick Tunnel",
                "protocol": "HTTPS Reverse Proxy",
                "security": "Public HTTPS Edge",
                "timeout": "100s Cloudflare Limit"
            }
        elif "127.0.0.1" in u or "localhost" in u:
            return {
                "type": "localhost",
                "label": "Localhost / Direct Loopback",
                "protocol": "HTTP Loopback",
                "security": "Local Device Only",
                "timeout": "Unlimited"
            }
        else:
            return {
                "type": "custom",
                "label": "Custom Remote URL",
                "protocol": "HTTP/HTTPS",
                "security": "Remote Host",
                "timeout": "Standard"
            }

    def health_check(self) -> Dict[str, Any]:
        """Check status of Kaggle GPU server, local RapidOCR, and Gemini Vision AI."""
        tunnel_info = self.detect_tunnel(self.api_url)
        status_info = {
            "kaggle_gpu": None,
            "tunnel_info": tunnel_info,
            "local_ocr": "available" if self._get_local_ocr() is not None else "unavailable",
            "gemini_fallback": "configured" if self.gemini_client.is_configured else "not_configured"
        }

        if self.api_url:
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(f"{self.api_url}/health")
                    if resp.status_code == 200:
                        gpu_data = resp.json()
                        if isinstance(gpu_data, dict):
                            gpu_data["tunnel_info"] = tunnel_info
                        status_info["kaggle_gpu"] = gpu_data
                    else:
                        status_info["kaggle_gpu"] = {"status": "unhealthy", "code": resp.status_code, "tunnel_info": tunnel_info}
            except Exception as e:
                status_info["kaggle_gpu"] = {"status": "error", "message": str(e), "tunnel_info": tunnel_info}

        return status_info

    def _is_error_text(self, text: str) -> bool:
        """Check if returned text is actually a model error message."""
        if not text:
            return False
        clean = text.strip()
        error_indicators = (
            "Error:",
            "Inference error:",
            "Chat error:",
            "RuntimeError:",
            "CUDA error",
            "wrapper_CUDA_cat",
            "Expected all tensors to be on the same device"
        )
        return any(clean.startswith(ind) or ind in clean for ind in error_indicators)

    def ocr_page(
        self,
        image_bytes: bytes,
        filename: str,
        prompt: Optional[str] = None,
        immediate_fallback: bool = False
    ) -> Dict[str, Any]:
        """
        Send a preprocessed page image to Unlimited-OCR or local RapidOCR.
        Returns first-pass OCR result with confidence score.
        If immediate_fallback is True and confidence < 85%, invokes Gemini immediately.
        Otherwise, DocumentOrchestrator pools low-confidence elements into 20-element packets.
        """
        initial_res = None

        if self.api_url:
            url = f"{self.api_url}/ocr/page"
            files = {"file": (filename, image_bytes, "image/png")}
            data = {"prompt": prompt} if prompt else {}

            for attempt in range(1, self.max_retries + 1):
                try:
                    with httpx.Client(timeout=self.timeout) as client:
                        resp = client.post(url, files=files, data=data)
                        if resp.status_code == 200:
                            res_json = resp.json()
                            extracted_text = res_json.get("text", "")
                            if not self._is_error_text(extracted_text):
                                res_json["engine"] = "Baidu Unlimited-OCR (GPU)"
                                initial_res = res_json
                                break
                        elif resp.status_code in (502, 503, 504):
                            time.sleep(0.5)
                        else:
                            break
                except Exception:
                    time.sleep(0.5)

        if initial_res is None:
            # Fall back to local RapidOCR
            initial_res = self.local_ocr(image_bytes, filename)

        if immediate_fallback:
            initial_conf = initial_res.get("confidence", 0.0)
            has_text = bool(initial_res.get("text", "").strip())
            if (initial_conf < 0.85 or not has_text) and self.gemini_client.is_configured:
                gemini_res = self.gemini_client.transcribe_image(image_bytes, filename)
                if gemini_res.get("status") == "SUCCESS" and gemini_res.get("text", "").strip():
                    gemini_res["previous_confidence"] = initial_conf
                    return gemini_res

        return initial_res

    def ocr_batch(
        self,
        pages: List[Tuple[str, bytes]],
        prompt: Optional[str] = None,
        immediate_fallback: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Send a batch of up to 20 preprocessed page images in a single HTTP request to the GPU server.
        Returns first-pass OCR results with confidence scores.
        """
        results = []

        if self.api_url:
            url = f"{self.api_url}/ocr/batch"
            files = [("files", (fname, img_bytes, "image/jpeg")) for fname, img_bytes in pages]
            data = {"prompt": prompt} if prompt else {}
            batch_timeout = max(self.timeout, len(pages) * 5.0)

            try:
                with httpx.Client(timeout=batch_timeout) as client:
                    resp = client.post(url, files=files, data=data)
                    if resp.status_code == 200:
                        raw_results = resp.json().get("results", [])
                        if len(raw_results) == len(pages):
                            results = raw_results
            except Exception:
                pass

        if not results:
            results = [self.local_ocr(img_bytes, fname) for fname, img_bytes in pages]

        final_results = []
        for (fname, img_bytes), res in zip(pages, results):
            text = res.get("text", "")
            if res.get("status") != "SUCCESS" or self._is_error_text(text):
                res = self.local_ocr(img_bytes, fname)

            if immediate_fallback:
                conf = res.get("confidence", 0.0)
                if (conf < 0.85 or not res.get("text", "").strip()) and self.gemini_client.is_configured:
                    gemini_res = self.gemini_client.transcribe_image(img_bytes, fname)
                    if gemini_res.get("status") == "SUCCESS" and gemini_res.get("text", "").strip():
                        gemini_res["previous_confidence"] = conf
                        res = gemini_res

            final_results.append(res)

        return final_results
