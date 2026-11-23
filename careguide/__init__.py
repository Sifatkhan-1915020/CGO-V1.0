"""
CareGuide Multi-Format Ingestion & Extraction Engine
Supports PDF, JPG, PNG, TIFF, XLSX, XLS, and CSV.
"""

__version__ = "1.0.0"

from .schema import DocumentResult, PageResult, SheetResult, BatchSummary, is_bangla_text, needs_gemini_fallback
from .validator import FileValidator, ValidatedFile, FileFormat
from .orchestrator import DocumentOrchestrator
from .invoice_extractor import (
    InvoiceExtractor,
    InvoiceData,
    InvoiceLineItem,
    ValidatedInvoice,
    ValidationIssue,
)
from .supabase_client import SupabaseClient
from .api_keys import (
    generate_api_key,
    validate_api_key,
    revoke_api_key,
    reactivate_api_key,
    delete_api_key,
    list_api_keys,
    get_api_key_by_id,
    get_api_key_usage_stats,
    record_api_usage,
)

__all__ = [
    "DocumentResult",
    "PageResult",
    "SheetResult",
    "BatchSummary",
    "FileValidator",
    "ValidatedFile",
    "FileFormat",
    "DocumentOrchestrator",
    "is_bangla_text",
    "needs_gemini_fallback",
    "InvoiceExtractor",
    "InvoiceData",
    "InvoiceLineItem",
    "ValidatedInvoice",
    "ValidationIssue",
    "SupabaseClient",
    "generate_api_key",
    "validate_api_key",
    "revoke_api_key",
    "reactivate_api_key",
    "delete_api_key",
    "list_api_keys",
    "get_api_key_by_id",
    "get_api_key_usage_stats",
    "record_api_usage",
]
