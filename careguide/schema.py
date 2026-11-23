"""
Data schemas for CareGuide document ingestion and extraction.
"""

from enum import Enum
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    PDF = "PDF"
    IMAGE = "IMAGE"
    SPREADSHEET = "SPREADSHEET"
    TEXT = "TEXT"
    UNKNOWN = "UNKNOWN"


class ExtractionType(str, Enum):
    DIGITAL_TEXT = "DIGITAL_TEXT"
    UNLIMITED_OCR = "UNLIMITED_OCR"
    TABULAR = "TABULAR"
    FAILED = "FAILED"


class ProcessingStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    CORRUPTED = "CORRUPTED"
    SKIPPED = "SKIPPED"


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class TextBlock(BaseModel):
    text: str
    bbox: Optional[BoundingBox] = None
    confidence: float = 1.0


class PageResult(BaseModel):
    index: int
    type: ExtractionType
    status: ProcessingStatus
    text: str = ""
    blocks: List[TextBlock] = Field(default_factory=list)
    tables: List[List[List[Any]]] = Field(default_factory=list)
    preprocessing_applied: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    engine: Optional[str] = None
    image_bytes: Optional[bytes] = Field(default=None, exclude=True)


def is_bangla_text(text: Optional[str]) -> bool:
    """Return True if text contains Bengali script characters (Unicode range 0x0980 - 0x09FF)."""
    if not text:
        return False
    return any('\u0980' <= ch <= '\u09FF' for ch in text)


def needs_gemini_fallback(page: PageResult) -> Tuple[bool, str]:
    """
    Check if a page/element qualifies for the Gemini Fallback Packet:
    1. Bangla script detected in recognized text.
    2. Status is CORRUPTED or FAILED.
    3. Confidence is < 85% (< 0.85).
    4. Text is empty on a visual scan/image.
    """
    if is_bangla_text(page.text):
        return True, "bangla_detected"
    if page.status in (ProcessingStatus.CORRUPTED, ProcessingStatus.FAILED):
        return True, f"status_{page.status.value.lower()}"
    if page.confidence < 0.85:
        return True, f"low_confidence_{page.confidence:.2f}"
    if not page.text.strip() and page.type != ExtractionType.DIGITAL_TEXT:
        return True, "empty_visual_text"
    return False, ""


class SheetResult(BaseModel):
    name: str
    index: int
    row_count: int = 0
    col_count: int = 0
    columns: List[str] = Field(default_factory=list)
    data: List[Dict[str, Any]] = Field(default_factory=list)
    status: ProcessingStatus = ProcessingStatus.SUCCESS
    error: Optional[str] = None


class DocumentResult(BaseModel):
    doc_id: str
    filename: str
    file_path: str
    file_type: DocumentType
    mime_type: str
    file_size_bytes: int
    status: ProcessingStatus
    processing_time_ms: float = 0.0
    total_pages_or_sheets: int = 0
    pages: List[PageResult] = Field(default_factory=list)
    sheets: List[SheetResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class BatchSummary(BaseModel):
    batch_id: str
    total_files: int = 0
    total_pages: int = 0
    total_sheets: int = 0
    successful_files: int = 0
    corrupted_or_failed_files: int = 0
    total_duration_sec: float = 0.0
    average_throughput_pages_per_sec: float = 0.0
    checkpoint_resumed_count: int = 0
    documents: List[DocumentResult] = Field(default_factory=list)
