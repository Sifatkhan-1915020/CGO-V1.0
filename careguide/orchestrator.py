"""
High-scale concurrency and batch orchestrator.
Coordinates validation, hybrid extraction, 20-image/20-page epoch batching,
checkpoint resumption, and live telemetry.
"""

import os
import glob
import time
import hashlib
from typing import List, Generator, Optional, Callable, Tuple
from tqdm import tqdm

from .schema import (
    DocumentResult,
    DocumentType,
    PageResult,
    SheetResult,
    ProcessingStatus,
    BatchSummary,
    ExtractionType,
    needs_gemini_fallback,
    is_bangla_text,
)
from .validator import FileValidator, ValidatedFile, FileFormat
from .ocr_client import OCRClient
from .pdf_extractor import PDFExtractor
from .image_extractor import ImageExtractor
from .tabular_extractor import TabularExtractor
from .checkpoint import CheckpointLedger


class DocumentOrchestrator:
    """Orchestrates multi-file, multi-format extraction with resilient scaling."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        checkpoint_db: str = "careguide_checkpoint.db",
        jsonl_output: str = "careguide_stream.jsonl",
        batch_id: Optional[str] = None,
        batch_size: int = 20
    ):
        self.ocr_client = OCRClient(api_url=api_url, gemini_api_key=gemini_api_key)
        self.checkpoint = CheckpointLedger(db_path=checkpoint_db, jsonl_path=jsonl_output)
        self.batch_id = batch_id or f"batch_{int(time.time())}"
        self.batch_size = batch_size
        self.pdf_extractor = PDFExtractor(ocr_client=self.ocr_client, batch_size=batch_size)
        self.image_extractor = ImageExtractor(ocr_client=self.ocr_client)
        self.tabular_extractor = TabularExtractor()

    @staticmethod
    def resolve_paths(targets: List[str]) -> List[str]:
        """Resolve files, folders, and glob patterns into a sorted list of unique files."""
        matched_files = set()
        for t in targets:
            if os.path.isfile(t):
                matched_files.add(os.path.abspath(t))
            elif os.path.isdir(t):
                for root, _, files in os.walk(t):
                    for f in files:
                        matched_files.add(os.path.abspath(os.path.join(root, f)))
            else:
                for match in glob.glob(t, recursive=True):
                    if os.path.isfile(match):
                        matched_files.add(os.path.abspath(match))
        return sorted(list(matched_files))

    def process_file(
        self,
        file_path: str,
        progress_callback: Optional[Callable] = None,
        run_fallback: bool = True
    ) -> DocumentResult:
        """Process a single document, streaming pages/sheets with checkpointing."""
        start_t = time.time()
        file_hash = hashlib.sha256(file_path.encode()).hexdigest()[:12]
        filename = os.path.basename(file_path)

        if progress_callback:
            progress_callback(filename, f"Validating signature and format...")

        # 1. Validation & Corruption Check
        validated: ValidatedFile = FileValidator.validate(file_path)

        if not validated.is_valid:
            if progress_callback:
                progress_callback(filename, f"Validation Failed: {validated.error_message}")
            elapsed_ms = round((time.time() - start_t) * 1000, 2)
            doc_type = DocumentType.UNKNOWN
            if validated.format == FileFormat.PDF:
                doc_type = DocumentType.PDF
            elif validated.format in (FileFormat.JPG, FileFormat.PNG, FileFormat.TIFF, FileFormat.WEBP):
                doc_type = DocumentType.IMAGE
            elif validated.format in (FileFormat.XLSX, FileFormat.XLS, FileFormat.CSV, FileFormat.TSV):
                doc_type = DocumentType.SPREADSHEET
            elif validated.format == FileFormat.TXT:
                doc_type = DocumentType.TEXT

            return DocumentResult(
                doc_id=file_hash,
                filename=filename,
                file_path=file_path,
                file_type=doc_type,
                mime_type=validated.mime_type,
                file_size_bytes=validated.size_bytes,
                status=ProcessingStatus.CORRUPTED,
                processing_time_ms=elapsed_ms,
                total_pages_or_sheets=0,
                pages=[],
                sheets=[],
                error=validated.error_message
            )

        # 2. Route by Format
        pages: List[PageResult] = []
        sheets: List[SheetResult] = []
        doc_type = DocumentType.UNKNOWN

        if validated.format == FileFormat.PDF:
            doc_type = DocumentType.PDF
            for page in self.pdf_extractor.extract_document(file_path, filename):
                self.checkpoint.record_page(self.batch_id, file_hash, page)
                pages.append(page)
                if progress_callback:
                    enh = f" (CV: {', '.join(page.preprocessing_applied)})" if page.preprocessing_applied else ""
                    progress_callback(filename, f"Page {page.index} [{page.type.value}]{enh} - {page.status.value}", page)

        elif validated.format in (FileFormat.JPG, FileFormat.PNG, FileFormat.TIFF, FileFormat.WEBP):
            doc_type = DocumentType.IMAGE
            for page in self.image_extractor.extract_document(file_path, filename):
                self.checkpoint.record_page(self.batch_id, file_hash, page)
                pages.append(page)
                if progress_callback:
                    enh = f" (CV: {', '.join(page.preprocessing_applied)})" if page.preprocessing_applied else ""
                    progress_callback(filename, f"Frame {page.index} [{page.type.value}]{enh} - {page.status.value}", page)

        elif validated.format in (FileFormat.XLSX, FileFormat.XLS, FileFormat.CSV, FileFormat.TSV):
            doc_type = DocumentType.SPREADSHEET
            if validated.format == FileFormat.XLSX:
                stream = self.tabular_extractor.extract_xlsx(file_path)
            elif validated.format == FileFormat.XLS:
                stream = self.tabular_extractor.extract_xls(file_path)
            else:
                stream = self.tabular_extractor.extract_csv(file_path)

            for sheet in stream:
                self.checkpoint.record_sheet(self.batch_id, file_hash, sheet)
                sheets.append(sheet)
                if progress_callback:
                    progress_callback(filename, f"Sheet '{sheet.name}' ({sheet.row_count} rows, {sheet.col_count} cols) - {sheet.status.value}", sheet)

        elif validated.format == FileFormat.TXT:
            doc_type = DocumentType.TEXT
            for page in self.tabular_extractor.extract_txt(file_path):
                self.checkpoint.record_page(self.batch_id, file_hash, page)
                pages.append(page)
                if progress_callback:
                    progress_callback(filename, f"Text Page {page.index} [NO OCR] - {page.status.value}", page)

        elapsed_ms = round((time.time() - start_t) * 1000, 2)
        total_items = len(pages) if pages else len(sheets)

        any_failed = any(p.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for p in pages) or \
                     any(s.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for s in sheets)
        all_failed = (len(pages) > 0 and all(p.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for p in pages)) or \
                     (len(sheets) > 0 and all(s.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for s in sheets))

        if all_failed:
            status = ProcessingStatus.CORRUPTED
        elif any_failed:
            status = ProcessingStatus.PARTIAL_SUCCESS
        else:
            status = ProcessingStatus.SUCCESS

        if total_items == 0 and not any_failed:
            status = ProcessingStatus.SUCCESS

        doc_result = DocumentResult(
            doc_id=file_hash,
            filename=filename,
            file_path=file_path,
            file_type=doc_type,
            mime_type=validated.mime_type,
            file_size_bytes=validated.size_bytes,
            status=status,
            processing_time_ms=elapsed_ms,
            total_pages_or_sheets=total_items,
            pages=pages,
            sheets=sheets
        )

        if run_fallback and self.ocr_client.gemini_client.is_configured and pages:
            self._execute_fallback_packets([doc_result], progress_callback=progress_callback)

        return doc_result

    process_document = process_file  # Backwards compatibility alias

    def process_files(
        self,
        targets: List[str],
        progress_callback: Optional[Callable] = None
    ) -> List[DocumentResult]:
        """
        Process a list of heterogeneous files:
        - PDFs: Digital triage (<15ms) + 20-page batching for scanned pages.
        - Tabular (XLSX, XLS, CSV, TSV): Direct multi-sheet tabular extraction.
        - Images (JPG, PNG, TIFF, WEBP): Checks total image count, groups into epochs of 20 images,
          passes 20 images at a time to OCR, and combines each 20-image group into a single file
          with '=== this image: {filename} ===' before each picture starts.
        """
        results: List[DocumentResult] = []
        pdf_files = []
        tabular_files = []
        image_files: List[Tuple[str, str]] = []

        # 1. Validation & Categorization
        for target in targets:
            validated = FileValidator.validate(target)
            if not validated.is_valid:
                results.append(self.process_file(target, progress_callback))
                continue

            if validated.format == FileFormat.PDF:
                pdf_files.append(target)
            elif validated.format in (FileFormat.XLSX, FileFormat.XLS, FileFormat.CSV, FileFormat.TSV):
                tabular_files.append(target)
            elif validated.format in (FileFormat.JPG, FileFormat.PNG, FileFormat.TIFF, FileFormat.WEBP):
                image_files.append((target, os.path.basename(target)))
            else:
                results.append(self.process_file(target, progress_callback))

        # 2. Process PDFs (pass run_fallback=False to pool all pages into 20-element packets)
        for pdf_path in pdf_files:
            results.append(self.process_file(pdf_path, progress_callback, run_fallback=False))

        # 3. Process Tabular files
        for tab_path in tabular_files:
            results.append(self.process_file(tab_path, progress_callback, run_fallback=False))

        # 4. Process Images with 20-image Epoch Batching
        num_images = len(image_files)
        if num_images > 0:
            if progress_callback:
                progress_callback(
                    "Image Dispatch",
                    f"Found {num_images} image(s). Grouping into epochs of {self.batch_size} images per file pass..."
                )

            if num_images == 1:
                # Single image file
                single_doc = self.process_file(image_files[0][0], progress_callback, run_fallback=False)
                results.append(single_doc)
            else:
                # Multiple images: Group into 20-image epochs, making a single file per 20 images
                epoch_idx = 1
                for chunk_start in range(0, num_images, self.batch_size):
                    chunk = image_files[chunk_start:chunk_start + self.batch_size]
                    if progress_callback:
                        progress_callback(
                            f"Epoch {epoch_idx}",
                            f"Processing Epoch {epoch_idx} ({len(chunk)} images): pass {len(chunk)} images at a time to OCR..."
                        )

                    epoch_doc = self.image_extractor.extract_epoch(
                        chunk,
                        epoch_index=epoch_idx,
                        progress_callback=progress_callback
                    )

                    # Persist each page in checkpoint ledger
                    for page in epoch_doc.pages:
                        self.checkpoint.record_page(self.batch_id, epoch_doc.doc_id, page)

                    results.append(epoch_doc)
                    epoch_idx += 1

        # 5. Combined Gemini Fallback Packets (Bangla, <85% conf, or corrupted/failed)
        if self.ocr_client.gemini_client.is_configured:
            self._execute_fallback_packets(results, progress_callback=progress_callback)
        else:
            # Check if any pages qualified for fallback and alert the user
            unrescued = []
            for doc in results:
                for page in doc.pages:
                    needs, reason = needs_gemini_fallback(page)
                    if needs:
                        unrescued.append((doc.filename, page.index, page.confidence, reason))
            if unrescued and progress_callback:
                progress_callback(
                    "Gemini AI Fallback",
                    f"⚠️ {len(unrescued)} element(s) scored <85% confidence or contain Bangla (e.g. {unrescued[0][2]*100:.1f}%), "
                    f"but Gemini API Key is not configured. Click Settings (⚙️) to enter your Gemini API key to auto-rescue with Gemini Vision AI."
                )

        return results

    def _execute_fallback_packets(
        self,
        documents: List[DocumentResult],
        progress_callback: Optional[Callable] = None
    ):
        """
        Scan all pages across documents. Any page that:
        - Contains Bangla text (বাংলা)
        - Has confidence level < 85% (< 0.85)
        - Has status CORRUPTED or FAILED
        - Has empty text on a visual scan
        is combined into a Fallback Queue.
        Elements are bundled into packets of at most 20 elements per packet.
        If elements <= 20, all are combinedly passed in a single packet to Gemini.
        Distinguishes between each element using strict header delimiters and demultiplexes results.
        """
        fallback_queue = []

        for doc in documents:
            for page in doc.pages:
                needs_fallback, reason = needs_gemini_fallback(page)
                if not needs_fallback:
                    continue

                img_bytes = page.image_bytes

                # If image_bytes was not cached, attempt on-demand recovery from file
                if img_bytes is None and doc.file_path and os.path.exists(doc.file_path):
                    if doc.file_type == DocumentType.PDF:
                        try:
                            import fitz
                            with fitz.open(doc.file_path) as d:
                                if 0 <= page.index - 1 < d.page_count:
                                    p = d.load_page(page.index - 1)
                                    pix = p.get_pixmap(dpi=200)
                                    img_bytes = pix.tobytes("jpeg")
                        except Exception:
                            pass
                    elif doc.file_type == DocumentType.IMAGE:
                        try:
                            with open(doc.file_path, "rb") as f:
                                img_bytes = f.read()
                        except Exception:
                            pass

                if img_bytes is not None:
                    real_name = doc.filename
                    if page.text and page.text.strip().startswith("=== this image:"):
                        try:
                            hdr = page.text.strip().split("\n")[0]
                            parsed_name = hdr.replace("=== this image:", "").replace("===", "").strip()
                            if parsed_name:
                                real_name = parsed_name
                        except Exception:
                            pass

                    fallback_queue.append({
                        "doc": doc,
                        "page": page,
                        "element_id": f"{real_name}#p{page.index}",
                        "filename": real_name,
                        "page_index": page.index,
                        "image_bytes": img_bytes,
                        "trigger_reason": reason,
                        "original_confidence": page.confidence,
                        "original_status": page.status.value,
                        "original_text": page.text
                    })

        total_fallback = len(fallback_queue)
        if total_fallback == 0:
            return

        packet_size = 20
        total_packets = (total_fallback + packet_size - 1) // packet_size

        if progress_callback:
            progress_callback(
                "Fallback Queue",
                f"Aggregated {total_fallback} element(s) requiring Gemini fallback (Bangla/<85% conf/corrupted). "
                f"Grouped into {total_packets} packet(s) of up to {packet_size} elements each."
            )

        for p_idx, start_i in enumerate(range(0, total_fallback, packet_size), start=1):
            packet_items = fallback_queue[start_i:start_i + packet_size]
            cur_packet_len = len(packet_items)

            if progress_callback:
                progress_callback(
                    f"Packet {p_idx}/{total_packets}",
                    f"Passing packet of {cur_packet_len} element(s) combinedly to Gemini Vision AI..."
                )

            # Build multi-image payload
            gemini_payload = [
                {
                    "element_id": item["element_id"],
                    "filename": item["filename"],
                    "page_index": item["page_index"],
                    "image_bytes": item["image_bytes"],
                    "trigger_reason": item["trigger_reason"],
                    "original_text": item["original_text"],
                    "original_confidence": item["original_confidence"]
                }
                for item in packet_items
            ]

            transcribed_items = self.ocr_client.gemini_client.transcribe_packet(
                gemini_payload,
                progress_callback=progress_callback
            )

            # Map results by element_id
            results_by_id = {r["element_id"]: r for r in transcribed_items}

            for item in packet_items:
                elem_id = item["element_id"]
                page = item["page"]
                doc = item["doc"]

                res = results_by_id.get(elem_id)
                if not res:
                    continue

                if res.get("status") == "SUCCESS" and res.get("text", "").strip():
                    new_text = res["text"].strip()
                    # Retain '=== this image: {filename} ===' header for images
                    if doc.file_type == DocumentType.IMAGE:
                        if not new_text.startswith("=== this image"):
                            new_text = f"=== this image: {item['filename']} ===\n\n{new_text}"

                    page.text = new_text
                    page.confidence = res.get("confidence", 0.98)
                    page.status = ProcessingStatus.SUCCESS
                    page.type = ExtractionType.UNLIMITED_OCR
                    page.engine = res.get("engine", "Gemini Vision AI (Packet Fallback)")
                    page.error = None

                    # Update checkpoint ledger
                    self.checkpoint.record_page(self.batch_id, doc.doc_id, page)

                    if progress_callback:
                        progress_callback(
                            item["filename"],
                            f"Element {item['page_index']} rescued by Gemini Packet ({item['trigger_reason']})",
                            page
                        )

            # Free image bytes memory for processed packet
            for item in packet_items:
                item["page"].image_bytes = None

        # Re-evaluate overall document statuses
        for doc in documents:
            if doc.pages:
                any_failed = any(p.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for p in doc.pages)
                all_failed = all(p.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED) for p in doc.pages)
                if all_failed:
                    doc.status = ProcessingStatus.CORRUPTED
                elif any_failed:
                    doc.status = ProcessingStatus.PARTIAL_SUCCESS
                else:
                    doc.status = ProcessingStatus.SUCCESS

    def process_batch(self, targets: List[str], show_progress: bool = True) -> BatchSummary:
        """Process a collection of files or directories, yielding aggregate telemetry."""
        files = self.resolve_paths(targets)
        batch_start = time.time()

        documents = self.process_files(files)

        total_pages = sum(len(d.pages) for d in documents)
        total_sheets = sum(len(d.sheets) for d in documents)
        successful_files = sum(1 for d in documents if d.status in (ProcessingStatus.SUCCESS, ProcessingStatus.PARTIAL_SUCCESS))
        failed_files = sum(1 for d in documents if d.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED))

        duration_sec = round(time.time() - batch_start, 2)
        throughput = round(total_pages / duration_sec, 2) if duration_sec > 0 else float(total_pages)

        return BatchSummary(
            batch_id=self.batch_id,
            total_files=len(files),
            total_pages=total_pages,
            total_sheets=total_sheets,
            successful_files=successful_files,
            corrupted_or_failed_files=failed_files,
            total_duration_sec=duration_sec,
            average_throughput_pages_per_sec=throughput,
            documents=documents
        )
