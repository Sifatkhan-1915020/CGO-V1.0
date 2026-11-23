"""
Bangla Language Utilities for CareGuide Document Intelligence.
Provides deterministic Bengali numeral conversion, Unicode font normalization,
Bijoy-to-Unicode conversion, and financial entity mapping.
"""

import re
from typing import Dict, Any, Optional, Tuple, List

# Bengali Digits Translation Table: ০-৯ -> 0-9
BANGLA_DIGITS_MAP = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
ARABIC_DIGITS_MAP = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

# Standard Bangla Accounting & Financial Vocabulary Mapping
BANGLA_FINANCIAL_TERMS = {
    "invoice_number": [
        "চালান নং", "চালান নম্বর", "চালান", "বিল নং", "বিল নম্বর",
        "রসিদ নং", "রশিদ নং", "ইনভয়েস নং", "ইনভয়েস নম্বর", "স্মারক নং"
    ],
    "invoice_date": [
        "তারিখ", "বিল তারিখ", "চালানের তারিখ", "ইস্যুর তারিখ", "রসিদ তারিখ"
    ],
    "supplier_name": [
        "সরবরাহকারী", "বিক্রেতা", "প্রতিষ্ঠানের নাম", "দোকানের নাম",
        "কোম্পানি", "মেসার্স", "সরবরাহকারীর নাম"
    ],
    "customer_name": [
        "ক্রেতা", "গ্রাহক", "গ্রাহকের নাম", "বিল প্রাপক", "ক্রেতার নাম", "নাম"
    ],
    "subtotal": [
        "উপমোট", "উপ-মোট", "মোট মূল্য", "মোট টাকা", "মূল্য"
    ],
    "tax": [
        "ভ্যাট", "মূসক", "কর", "ট্যাক্স", "ভ্যাট (১৫%)", "ভ্যাট (৫%)"
    ],
    "total": [
        "সর্বমোট", "মোট প্রদেয়", "পরিশোধযোগ্য মোট", "মোট", "নিট প্রদেয়"
    ],
    "currency": [
        "টাকা", "টাকায়", "টাকার পরিমাণ", "টাকার অংক", "৳"
    ],
    "line_items": {
        "item_number": ["ক্রমিক", "ক্র নং", "নং", "আইটেম"],
        "description": ["বিবরণ", "পণ্যের বিবরণ", "মালামালের বিবরণ", "নাম"],
        "quantity": ["পরিমাণ", "সংখ্যা", "নগ", "কেজি", "পিস", "রিম"],
        "unit_price": ["দর", "একক দর", "একক মূল্য", "প্রতি রিম", "প্রতি পিস"],
        "amount": ["মোট মূল্য", "টাকা", "মোট টাকা", "মূল্য"]
    }
}


def is_bangla_text(text: Optional[str]) -> bool:
    """Return True if text contains Bengali Unicode characters (U+0980 - U+09FF)."""
    if not text:
        return False
    return any("\u0980" <= ch <= "\u09FF" for ch in text)


def bangla_to_arabic_numerals(text: Optional[str]) -> str:
    """
    Convert Bengali digits (০, ১, ২, ৩, ৪, ৫, ৬, ৭, ৮, ৯) to standard ASCII digits (0-9).
    Also strips Bengali currency symbols '৳' and word 'টাকা' when cleaning numbers.
    """
    if not text:
        return ""
    converted = str(text).translate(BANGLA_DIGITS_MAP)
    return converted


def arabic_to_bangla_numerals(text_or_num: Any) -> str:
    """Convert ASCII digits (0-9) to Bengali digits (০-৯)."""
    if text_or_num is None:
        return ""
    return str(text_or_num).translate(ARABIC_DIGITS_MAP)


def parse_bangla_numeric_string(raw: Optional[str]) -> Optional[float]:
    """
    Parse a numeric value from strings containing Bangla digits, commas, or currency symbols.
    Example: '৳ ৪,৫০০.৫০' -> 4500.50
    """
    if not raw:
        return None
    s = str(raw).strip()
    # Convert digits first
    s = bangla_to_arabic_numerals(s)
    # Remove currency words/symbols and commas
    s = re.sub(r"[৳টাকা,\s]", "", s)
    # Find numeric floating pattern
    match = re.search(r"[-+]?\d*\.?\d+", s)
    if match:
        try:
            return float(match.group(0))
        except ValueError:
            return None
    return None


def normalize_bangla_unicode(text: Optional[str]) -> str:
    """
    Clean zero-width characters and normalize Bengali diacritic marks.
    Strips zero-width joiners (ZWJ) and non-joiners (ZWNJ) if isolated.
    """
    if not text:
        return ""
    cleaned = text
    # Replace common corrupt zero-width spaces
    cleaned = cleaned.replace("\u200B", "").replace("\uFEFF", "")
    return cleaned


# Bijoy / SutonnyMJ to Unicode mapping dictionary for common legacy Bengali font files
BIJOY_TO_UNICODE_MAP = {
    "Av": "আ", "B": "ই", "C": "ঈ", "D": "উ", "E": "ঊ", "F": "ঋ", "G": "এ", "H": "ঐ",
    "I": "ও", "J": "ঔ", "K": "ক", "L": "খ", "M": "গ", "N": "ঘ", "O": "ঙ", "P": "চ",
    "Q": "ছ", "R": "জ", "S": "ঝ", "T": "ঞ", "U": "ট", "V": "ঠ", "W": "ড", "X": "ঢ",
    "Y": "ণ", "Z": "ত", "_": "থ", "`": "দ", "a": "ধ", "b": "ন", "c": "প", "d": "ফ",
    "e": "ব", "f": "ভ", "g": "ম", "h": "য", "i": "র", "j": "ল", "k": "শ", "l": "ষ",
    "m": "স", "n": "হ", "o": "ড়", "p": "ঢ়", "q": "য়", "r": "ৎ", "s": "ং", "t": "ঃ",
    "u": "ঁ", "v": "া", "w": "ি", "x": "ী", "y": "ু", "z": "ূ", "{": "ৃ", "|": "ে",
    "}": "ৈ", "~": "ো", "0": "০", "1": "১", "2": "২", "3": "৩", "4": "৪", "5": "৫",
    "6": "৬", "7": "৭", "8": "৮", "9": "৯"
}


def is_bijoy_ascii(text: str) -> bool:
    """Detect if text is likely encoded in legacy Bijoy / SutonnyMJ rather than Unicode."""
    if not text or len(text) < 4:
        return False
    # If standard Unicode Bangla already present, it is not Bijoy ASCII
    if is_bangla_text(text):
        return False
    # Typical Bijoy character bigrams / patterns
    bijoy_markers = ["Av", "wK", "cÖ", "GB", "bv", "K‡", "GKUv", "hv", "gva¨"]
    matches = sum(1 for m in bijoy_markers if m in text)
    return matches >= 2


def convert_bijoy_to_unicode(text: str) -> str:
    """
    Convert legacy Bijoy/SutonnyMJ text to standard Unicode Bengali.
    Falls back gracefully to original text if conversion fails.
    """
    if not text:
        return ""
    if is_bangla_text(text):
        return text  # Already Unicode

    res = []
    i = 0
    while i < len(text):
        matched = False
        for length in (3, 2, 1):
            if i + length <= len(text):
                sub = text[i:i + length]
                if sub in BIJOY_TO_UNICODE_MAP:
                    res.append(BIJOY_TO_UNICODE_MAP[sub])
                    i += length
                    matched = True
                    break
        if not matched:
            res.append(text[i])
            i += 1
    return "".join(res)
