"""
File validation, magic-byte sniffing, and corruption defense shield.
"""

import os
import zipfile
from enum import Enum
from typing import Optional, Tuple
from PIL import Image

# Enforce image decompression bomb ceiling (prevents OOM from malicious or corrupt images)
Image.MAX_IMAGE_PIXELS = 100_000_000


class FileFormat(str, Enum):
    PDF = "PDF"
    JPG = "JPG"
    PNG = "PNG"
    TIFF = "TIFF"
    WEBP = "WEBP"
    XLSX = "XLSX"
    XLS = "XLS"
    CSV = "CSV"
    TSV = "TSV"
    TXT = "TXT"
    UNKNOWN = "UNKNOWN"


class ValidatedFile:
    def __init__(
        self,
        path: str,
        format: FileFormat,
        is_valid: bool,
        size_bytes: int,
        mime_type: str,
        error_message: Optional[str] = None
    ):
        self.path = path
        self.filename = os.path.basename(path)
        self.format = format
        self.is_valid = is_valid
        self.size_bytes = size_bytes
        self.mime_type = mime_type
        self.error_message = error_message

    def __repr__(self):
        return f"<ValidatedFile {self.filename} ({self.format.value}) valid={self.is_valid}>"


class FileValidator:
    """Inspects file signatures, validates structure, and detects corrupted files."""

    MAGIC_SIGNATURES = {
        FileFormat.PDF: [b"%PDF-"],
        FileFormat.PNG: [b"\x89PNG\r\n\x1a\n"],
        FileFormat.JPG: [b"\xff\xd8\xff"],
        FileFormat.TIFF: [b"II*\x00", b"MM\x00*"],
        FileFormat.XLSX: [b"PK\x03\x04"],
        FileFormat.XLS: [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    }

    MIME_MAP = {
        FileFormat.PDF: "application/pdf",
        FileFormat.PNG: "image/png",
        FileFormat.JPG: "image/jpeg",
        FileFormat.TIFF: "image/tiff",
        FileFormat.WEBP: "image/webp",
        FileFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        FileFormat.XLS: "application/vnd.ms-excel",
        FileFormat.CSV: "text/csv",
        FileFormat.TSV: "text/tab-separated-values",
        FileFormat.TXT: "text/plain",
        FileFormat.UNKNOWN: "application/octet-stream",
    }

    @classmethod
    def sniff_format(cls, header: bytes, filename: str) -> FileFormat:
        """Identify format by magic bytes first, falling back to extension."""
        # Check WEBP signature: starts with RIFF and has WEBP at offset 8
        if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return FileFormat.WEBP

        for fmt, sigs in cls.MAGIC_SIGNATURES.items():
            for sig in sigs:
                if header.startswith(sig):
                    # Further check if it's XLSX (ZIP containing openxml)
                    if fmt == FileFormat.XLSX:
                        return FileFormat.XLSX
                    return fmt

        # Fallback check by file extension
        ext = os.path.splitext(filename)[1].lower().strip(".")
        ext_map = {
            "pdf": FileFormat.PDF,
            "jpg": FileFormat.JPG,
            "jpeg": FileFormat.JPG,
            "png": FileFormat.PNG,
            "tif": FileFormat.TIFF,
            "tiff": FileFormat.TIFF,
            "webp": FileFormat.WEBP,
            "xlsx": FileFormat.XLSX,
            "xls": FileFormat.XLS,
            "csv": FileFormat.CSV,
            "tsv": FileFormat.TSV,
            "txt": FileFormat.TXT,
        }
        return ext_map.get(ext, FileFormat.UNKNOWN)

    @classmethod
    def validate(cls, file_path: str) -> ValidatedFile:
        """Validate a file path, checking existence, header magic bytes, and basic integrity."""
        if not os.path.exists(file_path):
            return ValidatedFile(
                path=file_path,
                format=FileFormat.UNKNOWN,
                is_valid=False,
                size_bytes=0,
                mime_type=cls.MIME_MAP[FileFormat.UNKNOWN],
                error_message="File does not exist."
            )

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            return ValidatedFile(
                path=file_path,
                format=FileFormat.UNKNOWN,
                is_valid=False,
                size_bytes=0,
                mime_type=cls.MIME_MAP[FileFormat.UNKNOWN],
                error_message="File is empty (0 bytes). Corrupted."
            )

        # Read first 512 bytes for header inspection
        try:
            with open(file_path, "rb") as f:
                header = f.read(512)
        except Exception as e:
            return ValidatedFile(
                path=file_path,
                format=FileFormat.UNKNOWN,
                is_valid=False,
                size_bytes=file_size,
                mime_type=cls.MIME_MAP[FileFormat.UNKNOWN],
                error_message=f"Could not read file header: {e}"
            )

        detected_format = cls.sniff_format(header, os.path.basename(file_path))

        # Perform deep format-specific integrity check
        is_valid, err_msg = cls._verify_integrity(file_path, detected_format, header)

        return ValidatedFile(
            path=file_path,
            format=detected_format,
            is_valid=is_valid,
            size_bytes=file_size,
            mime_type=cls.MIME_MAP.get(detected_format, "application/octet-stream"),
            error_message=err_msg
        )

    @classmethod
    def _verify_integrity(cls, file_path: str, fmt: FileFormat, header: bytes) -> Tuple[bool, Optional[str]]:
        """Verify format-specific structural integrity."""
        if fmt == FileFormat.UNKNOWN:
            return False, "Unsupported or unrecognized file format."

        # PDF verification
        if fmt == FileFormat.PDF:
            if not header.startswith(b"%PDF-"):
                return False, "Corrupted PDF: Missing %PDF- header signature."
            return True, None

        # Image verification (JPG, PNG, TIFF, WEBP)
        if fmt in (FileFormat.JPG, FileFormat.PNG, FileFormat.TIFF, FileFormat.WEBP):
            try:
                with Image.open(file_path) as img:
                    img.verify()
                return True, None
            except Exception as e:
                # We will still attempt partial salvage in the image preprocessor,
                # but flag it here as partially corrupted
                return False, f"Image stream corruption detected: {e}"

        # XLSX verification (Valid ZIP archive)
        if fmt == FileFormat.XLSX:
            try:
                if not zipfile.is_zipfile(file_path):
                    return False, "Corrupted XLSX: Not a valid ZIP container."
                with zipfile.ZipFile(file_path, "r") as zf:
                    names = zf.namelist()
                    if not any("[Content_Types].xml" in n or "xl/" in n for n in names):
                        return False, "Corrupted XLSX: Missing OpenXML spreadsheet structures."
                return True, None
            except Exception as e:
                return False, f"Corrupted XLSX archive: {e}"

        # XLS verification
        if fmt == FileFormat.XLS:
            if not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                return False, "Corrupted XLS: Missing OLE Compound Document header."
            return True, None

        # CSV, TSV & TXT verification
        if fmt in (FileFormat.CSV, FileFormat.TSV, FileFormat.TXT):
            return True, None

        return True, None
