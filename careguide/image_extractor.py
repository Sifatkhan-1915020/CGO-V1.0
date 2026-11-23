"""
Image extraction engine for JPG, PNG, WEBP, and multi-frame TIFF files.
Processes images in epochs of 20 images per pass (dispatched to OCR at once),
combining them into consolidated documents with '=== this image: {filename} ===' headers.
"""

import os
import io
import time
import hashlib
from typing import Generator, List, Tuple, Optional, Callable
from PIL import Image, ImageFile, ImageSequence
from .schema import DocumentResult, DocumentType, PageResult, ExtractionType, ProcessingStatus
from .preprocessor import ImageEnhancer
from .ocr_client import OCRClient

ImageFile.LOAD_TRUNCATED_IMAGES = True


class ImageExtractor:
    """Streams and extracts text from images (JPG, PNG, WEBP) and multi-frame TIFFs in 20-image epochs."""

    def __init__(self, ocr_client: OCRClient):
        self.ocr_client = ocr_client

    def extract_document(self, file_path: str, filename: str) -> Generator[PageResult, None, None]:
        """
        Extract text from an image or multi-frame TIFF file.
        Yields PageResult for each frame/image with '=== this image: {filename} ===' prefix.
        """
        try:
            pil_img = Image.open(file_path)
        except Exception as e:
            raw_fallback_bytes = None
            try:
                with open(file_path, "rb") as rf:
                    raw_fallback_bytes = rf.read()
            except Exception:
                pass
            yield PageResult(
                index=1,
                type=ExtractionType.FAILED,
                status=ProcessingStatus.CORRUPTED,
                text=f"=== this image: {filename} ===\n\n[Cannot open image: {e}]",
                error=f"Cannot open corrupted image file: {e}",
                image_bytes=raw_fallback_bytes
            )
            return

        frame_idx = 0
        try:
            for frame in ImageSequence.Iterator(pil_img):
                frame_idx += 1
                start_t = time.time()

                try:
                    # Convert frame to PNG byte buffer
                    buf = io.BytesIO()
                    frame_rgb = frame.convert("RGB")
                    frame_rgb.save(buf, format="PNG")
                    raw_bytes = buf.getvalue()

                    # Enhance image quality (deskew, shadow removal, contrast optimization)
                    enhanced_bytes, applied_ops = ImageEnhancer.enhance(
                        raw_bytes,
                        deskew=True,
                        remove_shadows=True,
                        adaptive_threshold=True,
                        denoise=True,
                        output_format=".jpg"
                    )

                    # Send to OCR (remote Kaggle GPU or local RapidOCR fallback)
                    ocr_response = self.ocr_client.ocr_page(
                        enhanced_bytes,
                        filename=f"{filename}_frame{frame_idx}.jpg",
                        prompt="Transcribe all text and tables with exact layout in clean Markdown."
                    )

                    ocr_text = ocr_response.get("text", "")

                    # If enhanced image yielded no text, retry on raw_bytes to guard against over-binarization
                    if not ocr_text.strip():
                        raw_ocr = self.ocr_client.ocr_page(
                            raw_bytes,
                            filename=f"{filename}_frame{frame_idx}.jpg",
                            prompt="Transcribe all text and tables with exact layout in clean Markdown."
                        )
                        if raw_ocr.get("text", "").strip():
                            ocr_text = raw_ocr.get("text", "")
                            applied_ops.append("raw_fallback")

                    elapsed_ms = round((time.time() - start_t) * 1000, 2)
                    status = ProcessingStatus.SUCCESS if (ocr_response.get("status") == "SUCCESS" or ocr_text.strip()) else ProcessingStatus.FAILED

                    n_frames = getattr(pil_img, "n_frames", 1)
                    label = f"{filename} (Frame {frame_idx})" if n_frames > 1 else filename
                    formatted_text = f"=== this image: {label} ===\n\n{ocr_text.strip()}"

                    yield PageResult(
                        index=frame_idx,
                        type=ExtractionType.UNLIMITED_OCR,
                        status=status,
                        text=formatted_text,
                        preprocessing_applied=applied_ops,
                        confidence=ocr_response.get("confidence", 0.95),
                        elapsed_ms=elapsed_ms,
                        error=ocr_response.get("error") if status == ProcessingStatus.FAILED else None,
                        engine=ocr_response.get("engine", "Image OCR"),
                        image_bytes=enhanced_bytes
                    )
                except Exception as frame_err:
                    yield PageResult(
                        index=frame_idx,
                        type=ExtractionType.UNLIMITED_OCR,
                        status=ProcessingStatus.CORRUPTED,
                        text=f"=== this image: {filename} ===\n\n[Error processing frame {frame_idx}: {frame_err}]",
                        error=f"Error processing frame {frame_idx}: {frame_err}",
                        image_bytes=raw_bytes if 'raw_bytes' in locals() else None
                    )
        finally:
            pil_img.close()

    def extract_epoch(
        self,
        image_items: List[Tuple[str, str]],
        epoch_index: int = 1,
        progress_callback: Optional[Callable] = None
    ) -> DocumentResult:
        """
        Process a batch of up to 20 images at one time, combining them into a single consolidated file.
        Manually puts '=== this image: {filename} ===' before each picture's extracted content.
        """
        start_t = time.time()
        pages: List[PageResult] = []
        batch_payload: List[Tuple[str, bytes]] = []
        preprocessed_info = []

        # 1. Preprocess all images in this epoch (scaled to max 1600px with cv2.INTER_AREA)
        for idx, (fpath, fname) in enumerate(image_items):
            p_start = time.time()
            try:
                with Image.open(fpath) as pil_img:
                    frame = next(ImageSequence.Iterator(pil_img))
                    buf = io.BytesIO()
                    frame_rgb = frame.convert("RGB")
                    frame_rgb.save(buf, format="PNG")
                    raw_bytes = buf.getvalue()

                enhanced_bytes, applied_ops = ImageEnhancer.enhance(
                    raw_bytes,
                    deskew=True,
                    remove_shadows=True,
                    adaptive_threshold=True,
                    denoise=True,
                    output_format=".jpg"
                )
                batch_payload.append((fname, enhanced_bytes))
                preprocessed_info.append({
                    "fpath": fpath,
                    "fname": fname,
                    "raw_bytes": raw_bytes,
                    "enhanced_bytes": enhanced_bytes,
                    "applied_ops": applied_ops,
                    "start_t": p_start
                })
            except Exception as e:
                # Attempt to read raw bytes even if PIL failed
                raw_fallback_bytes = None
                try:
                    with open(fpath, "rb") as rf:
                        raw_fallback_bytes = rf.read()
                except Exception:
                    pass

                pages.append(
                    PageResult(
                        index=idx + 1,
                        type=ExtractionType.FAILED,
                        status=ProcessingStatus.CORRUPTED,
                        text=f"=== this image: {fname} ===\n\n[Corrupted image: {e}]",
                        error=str(e),
                        engine="File Validator",
                        image_bytes=raw_fallback_bytes
                    )
                )

        # 2. Pass up to 20 images AT A TIME to the OCR system
        if batch_payload:
            if progress_callback:
                progress_callback(
                    f"Epoch {epoch_index}",
                    f"Dispatching batch of {len(batch_payload)} images to OCR engine at one time..."
                )
            ocr_results = self.ocr_client.ocr_batch(batch_payload)

            for item_info, ocr_res in zip(preprocessed_info, ocr_results):
                fname = item_info["fname"]
                raw_bytes = item_info["raw_bytes"]
                applied_ops = item_info["applied_ops"]
                p_start = item_info["start_t"]

                ocr_text = ocr_res.get("text", "")

                # If enhanced image yielded no text, retry on raw bytes
                if not ocr_text.strip():
                    raw_ocr = self.ocr_client.ocr_page(
                        raw_bytes,
                        filename=fname,
                        prompt="Transcribe all text and tables with exact layout in clean Markdown."
                    )
                    if raw_ocr.get("text", "").strip():
                        ocr_text = raw_ocr.get("text", "")
                        applied_ops.append("raw_fallback")

                elapsed_ms = round((time.time() - p_start) * 1000, 2)
                status = ProcessingStatus.SUCCESS if (ocr_res.get("status") == "SUCCESS" or ocr_text.strip()) else ProcessingStatus.FAILED

                # Manually put '=== this image: {filename} ===' before each picture starts
                formatted_text = f"=== this image: {fname} ===\n\n{ocr_text.strip()}"

                p_res = PageResult(
                    index=len(pages) + 1,
                    type=ExtractionType.UNLIMITED_OCR,
                    status=status,
                    text=formatted_text,
                    preprocessing_applied=applied_ops,
                    confidence=ocr_res.get("confidence", 0.95),
                    elapsed_ms=elapsed_ms,
                    error=ocr_res.get("error") if status == ProcessingStatus.FAILED else None,
                    engine=ocr_res.get("engine", "Image OCR"),
                    image_bytes=item_info["enhanced_bytes"]
                )
                pages.append(p_res)
                if progress_callback:
                    progress_callback(f"Epoch {epoch_index}", f"Image {p_res.index}/{len(image_items)}: '{fname}' -> {status.value}", p_res)

        total_elapsed = round((time.time() - start_t) * 1000, 2)
        any_failed = any(p.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for p in pages)
        all_failed = len(pages) > 0 and all(p.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for p in pages)

        if all_failed:
            doc_status = ProcessingStatus.CORRUPTED
        elif any_failed:
            doc_status = ProcessingStatus.PARTIAL_SUCCESS
        else:
            doc_status = ProcessingStatus.SUCCESS

        epoch_filename = f"images_epoch_{epoch_index:02d}.md"
        doc_hash = hashlib.sha256(epoch_filename.encode()).hexdigest()[:12]
        total_size = sum(os.path.getsize(f[0]) for f in image_items if os.path.exists(f[0]))

        return DocumentResult(
            doc_id=doc_hash,
            filename=epoch_filename,
            file_path=image_items[0][0] if image_items else "epoch_images",
            file_type=DocumentType.IMAGE,
            mime_type="text/markdown",
            file_size_bytes=total_size,
            status=doc_status,
            processing_time_ms=total_elapsed,
            total_pages_or_sheets=len(pages),
            pages=pages,
            sheets=[]
        )
