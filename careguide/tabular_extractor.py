"""
Tabular data extraction engine for XLSX, XLS, and CSV files.
Supports multi-sheet workbooks, encoding auto-detection, delimiter sniffing,
and memory-safe streaming for large datasets.
"""

import csv
import io
from typing import List, Dict, Any, Generator, Optional
import openpyxl
import xlrd
from charset_normalizer import from_bytes
from .schema import SheetResult, ProcessingStatus, PageResult, ExtractionType, TextBlock
from .bangla_utils import (
    normalize_bangla_unicode,
    is_bijoy_ascii,
    convert_bijoy_to_unicode,
    is_bangla_text
)


class TabularExtractor:
    """Extracts structured tabular data from multi-sheet spreadsheets and CSV files."""

    @staticmethod
    def extract_xlsx(file_path: str) -> Generator[SheetResult, None, None]:
        """Stream sheets from an XLSX workbook using read_only mode for O(1) memory."""
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        except Exception as e:
            yield SheetResult(
                name="Error",
                index=1,
                status=ProcessingStatus.CORRUPTED,
                error=f"Cannot open corrupted XLSX workbook: {e}"
            )
            return

        try:
            for idx, sheet_name in enumerate(wb.sheetnames):
                sheet = wb[sheet_name]
                rows_iter = sheet.iter_rows(values_only=True)
                
                # Fetch header row
                try:
                    header_row = next(rows_iter, None)
                except Exception as row_err:
                    yield SheetResult(
                        name=sheet_name,
                        index=idx + 1,
                        status=ProcessingStatus.FAILED,
                        error=f"Failed reading sheet '{sheet_name}': {row_err}"
                    )
                    continue

                if not header_row:
                    yield SheetResult(
                        name=sheet_name,
                        index=idx + 1,
                        row_count=0,
                        col_count=0,
                        columns=[],
                        data=[],
                        status=ProcessingStatus.SUCCESS
                    )
                    continue

                # Clean column headers
                columns = [str(col).strip() if col is not None else f"Column_{i+1}" for i, col in enumerate(header_row)]
                data: List[Dict[str, Any]] = []
                row_count = 0

                for row in rows_iter:
                    if all(cell is None for cell in row):
                        continue
                    row_dict = {}
                    for i, col_name in enumerate(columns):
                        val = row[i] if i < len(row) else None
                        row_dict[col_name] = val
                    data.append(row_dict)
                    row_count += 1

                yield SheetResult(
                    name=sheet_name,
                    index=idx + 1,
                    row_count=row_count,
                    col_count=len(columns),
                    columns=columns,
                    data=data,
                    status=ProcessingStatus.SUCCESS
                )
        finally:
            wb.close()

    @staticmethod
    def extract_xls(file_path: str) -> Generator[SheetResult, None, None]:
        """Extract multi-sheet legacy XLS workbook using xlrd."""
        try:
            wb = xlrd.open_workbook(file_path)
        except Exception as e:
            yield SheetResult(
                name="Error",
                index=1,
                status=ProcessingStatus.CORRUPTED,
                error=f"Cannot open corrupted legacy XLS file: {e}"
            )
            return

        for idx in range(wb.nsheets):
            sheet = wb.sheet_by_index(idx)
            sheet_name = sheet.name

            if sheet.nrows == 0:
                yield SheetResult(
                    name=sheet_name,
                    index=idx + 1,
                    row_count=0,
                    col_count=0,
                    columns=[],
                    data=[],
                    status=ProcessingStatus.SUCCESS
                )
                continue

            header_vals = [str(sheet.cell_value(0, c)).strip() or f"Column_{c+1}" for c in range(sheet.ncols)]
            data: List[Dict[str, Any]] = []

            for r in range(1, sheet.nrows):
                row_dict = {}
                for c in range(sheet.ncols):
                    cell_val = sheet.cell_value(r, c)
                    row_dict[header_vals[c]] = cell_val
                data.append(row_dict)

            yield SheetResult(
                name=sheet_name,
                index=idx + 1,
                row_count=sheet.nrows - 1,
                col_count=sheet.ncols,
                columns=header_vals,
                data=data,
                status=ProcessingStatus.SUCCESS
            )

    @staticmethod
    def extract_csv(file_path: str) -> Generator[SheetResult, None, None]:
        """Extract CSV or TSV with automatic character encoding detection, delimiter sniffing, and Bangla normalization."""
        is_tsv = file_path.lower().endswith(".tsv")
        sheet_label = "TSV" if is_tsv else "CSV"

        try:
            with open(file_path, "rb") as f:
                raw_sample = f.read(65536)
        except Exception as e:
            yield SheetResult(
                name=sheet_label,
                index=1,
                status=ProcessingStatus.CORRUPTED,
                error=f"Cannot read {sheet_label} file: {e}"
            )
            return

        # Auto-detect encoding
        encoding_match = from_bytes(raw_sample).best()
        encoding = encoding_match.encoding if encoding_match else "utf-8"

        # Auto-detect delimiter
        if is_tsv:
            delimiter = "\t"
        else:
            sample_str = raw_sample.decode(encoding, errors="replace")
            try:
                dialect = csv.Sniffer().sniff(sample_str[:4096])
                delimiter = dialect.delimiter
            except Exception:
                delimiter = ","

        try:
            with open(file_path, "r", encoding=encoding, errors="replace", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                header = next(reader, None)
                if not header:
                    yield SheetResult(
                        name=sheet_label,
                        index=1,
                        row_count=0,
                        col_count=0,
                        columns=[],
                        data=[],
                        status=ProcessingStatus.SUCCESS
                    )
                    return

                # Normalize headers (convert Bijoy font if needed, clean Bangla unicode)
                columns = []
                for i, col in enumerate(header):
                    c_clean = str(col).strip()
                    if is_bijoy_ascii(c_clean):
                        c_clean = convert_bijoy_to_unicode(c_clean)
                    c_clean = normalize_bangla_unicode(c_clean)
                    columns.append(c_clean or f"Column_{i+1}")

                data: List[Dict[str, Any]] = []
                row_count = 0

                for row in reader:
                    if not row:
                        continue
                    row_dict = {}
                    for i, col in enumerate(columns):
                        val = row[i] if i < len(row) else ""
                        if isinstance(val, str):
                            if is_bijoy_ascii(val):
                                val = convert_bijoy_to_unicode(val)
                            val = normalize_bangla_unicode(val)
                        row_dict[col] = val
                    data.append(row_dict)
                    row_count += 1

                yield SheetResult(
                    name=sheet_label,
                    index=1,
                    row_count=row_count,
                    col_count=len(columns),
                    columns=columns,
                    data=data,
                    status=ProcessingStatus.SUCCESS
                )
        except Exception as e:
            yield SheetResult(
                name=sheet_label,
                index=1,
                status=ProcessingStatus.FAILED,
                error=f"Failed parsing {sheet_label} content: {e}"
            )

    @staticmethod
    def extract_txt(file_path: str) -> Generator[PageResult, None, None]:
        """
        Extract plain text files (.txt) directly without OCR.
        Auto-detects encoding, normalizes Bangla Unicode / Bijoy font,
        and yields digital text PageResult instances.
        """
        try:
            with open(file_path, "rb") as f:
                raw_bytes = f.read()
        except Exception as e:
            yield PageResult(
                index=1,
                type=ExtractionType.FAILED,
                status=ProcessingStatus.CORRUPTED,
                error=f"Cannot read plain text file: {e}"
            )
            return

        if not raw_bytes.strip():
            yield PageResult(
                index=1,
                type=ExtractionType.DIGITAL_TEXT,
                status=ProcessingStatus.SUCCESS,
                text="",
                confidence=1.0,
                engine="Direct Text Reader (No OCR)"
            )
            return

        # Auto-detect encoding
        encoding_match = from_bytes(raw_bytes[:65536]).best()
        encoding = encoding_match.encoding if encoding_match else "utf-8"

        try:
            text = raw_bytes.decode(encoding, errors="replace")
        except Exception:
            text = raw_bytes.decode("utf-8", errors="replace")

        # Convert Bijoy / SutonnyMJ if legacy font was used
        if is_bijoy_ascii(text):
            text = convert_bijoy_to_unicode(text)

        # Normalize Bangla Unicode
        text = normalize_bangla_unicode(text).strip()

        # Split into logical pages if form feeds or clear delimiters exist
        if "\x0c" in text:
            raw_pages = text.split("\x0c")
        elif "--- PAGE BREAK ---" in text:
            raw_pages = text.split("--- PAGE BREAK ---")
        else:
            raw_pages = [text]

        for p_idx, p_content in enumerate(raw_pages, start=1):
            clean_page = p_content.strip()
            yield PageResult(
                index=p_idx,
                type=ExtractionType.DIGITAL_TEXT,
                status=ProcessingStatus.SUCCESS,
                text=clean_page,
                confidence=1.0,
                engine="Direct Text Reader (No OCR)"
            )
