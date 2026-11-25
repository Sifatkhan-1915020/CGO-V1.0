"""
PDF extraction engine using PyMuPDF strictly for rasterization and Baidu Unlimited-OCR (Kaggle GPU)
with 200 DPI rendering, OpenCV image enhancement, and corrupted page fault isolation.
"""

import time
from typing import Generator
import fitz  # PyMuPDF (used strictly for page-to-image rasterization)
from .schema import PageResult, ExtractionType, ProcessingStatus
from .preprocessor import ImageEnhancer
from .ocr_client import OCRClient


class PDFExtractor:
    """Streams and extracts text from multi-page PDFs by rasterizing pages and dispatching batches to Baidu Unlimited-OCR."""

    def __init__(self, ocr_client: OCRClient, min_digital_chars: int = 40, batch_size: int = 20):
        self.ocr_client = ocr_client
        self.min_digital_chars = min_digital_chars
        self.batch_size = batch_size

    def extract_document(self, file_path: str, filename: str) -> Generator[PageResult, None, None]:
        """
        Stream page results in batches of up to batch_size (default 20) pages from a multi-page PDF.
        Every PDF page is rasterized at 200 DPI and dispatched to Baidu Unlimited-OCR (Kaggle GPU).
        Never relies on embedded PyMuPDF text layer extraction (avoids broken Bangla/font-encoding glyphs).
        """
        try:
            doc = fitz.open(file_path)
        except Exception as e:
            yield PageResult(
                index=1,
                type=ExtractionType.FAILED,
                status=ProcessingStatus.CORRUPTED,
                error=f"Unrecoverable PDF corruption: {e}"
            )
            return

        total_pages = doc.page_count

        # Process document in windows of batch_size (default 20 pages per pass)
        for chunk_start in range(0, total_pages, self.batch_size):
            chunk_end = min(chunk_start + self.batch_size, total_pages)
            pages_to_ocr = []

            for page_idx in range(chunk_start, chunk_end):
                page_num = page_idx + 1
                start_t = time.time()

                try:
                    page = doc.load_page(page_idx)
                except Exception as e:
                    yield PageResult(
                        index=page_num,
                        type=ExtractionType.FAILED,
                        status=ProcessingStatus.CORRUPTED,
                        error=f"Corrupted page {page_num}: {e}"
                    )
                    continue

                # Always rasterize PDF page at 200 DPI -> Baidu Unlimited-OCR (Kaggle GPU)
                try:
                    pix = page.get_pixmap(dpi=200)
                    raw_bytes = pix.tobytes("png")
                    pix = None  # Free pixmap immediately

                    # Optimize resolution & enhance quality
                    enhanced_bytes, applied_ops = ImageEnhancer.enhance(
                        raw_bytes,
                        deskew=True,
                        remove_shadows=True,
                        adaptive_threshold=True,
                        denoise=True,
                        output_format=".jpg"
                    )

                    pages_to_ocr.append({
                        "page_num": page_num,
                        "filename": f"{filename}_p{page_num}.jpg",
                        "image_bytes": enhanced_bytes,
                        "applied_ops": applied_ops,
                        "start_t": start_t
                    })
                except Exception as raster_err:
                    yield PageResult(
                        index=page_num,
                        type=ExtractionType.UNLIMITED_OCR,
                        status=ProcessingStatus.FAILED,
                        error=f"Rasterization failed on page {page_num}: {raster_err}"
                    )

            # Dispatch batch of rendered PDF pages to Baidu Unlimited-OCR (Kaggle GPU) in ONE pass
            if pages_to_ocr:
                batch_payload = [(item["filename"], item["image_bytes"]) for item in pages_to_ocr]

                if len(batch_payload) == 1:
                    item = pages_to_ocr[0]
                    res = self.ocr_client.ocr_page(item["image_bytes"], item["filename"])
                    ocr_results = [res]
                else:
                    ocr_results = self.ocr_client.ocr_batch(batch_payload)

                for item, ocr_res in zip(pages_to_ocr, ocr_results):
                    elapsed_ms = round((time.time() - item["start_t"]) * 1000, 2)
                    status = ProcessingStatus.SUCCESS if ocr_res.get("status") == "SUCCESS" else ProcessingStatus.FAILED

                    yield PageResult(
                        index=item["page_num"],
                        type=ExtractionType.UNLIMITED_OCR,
                        status=status,
                        text=ocr_res.get("text", ""),
                        preprocessing_applied=item["applied_ops"],
                        confidence=ocr_res.get("confidence", 0.95),
                        elapsed_ms=elapsed_ms,
                        error=ocr_res.get("error"),
                        engine=ocr_res.get("engine", "Baidu Unlimited-OCR (GPU)"),
                        image_bytes=item["image_bytes"]
                    )

        doc.close()
