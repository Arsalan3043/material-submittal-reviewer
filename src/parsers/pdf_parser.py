from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

# 2× zoom gives ~144 DPI effective resolution.
# Tesseract accuracy degrades badly at native PDF ~72 DPI.
# Proven in Phase 2 Experiment A — all real UAE submittals are scanned.
_OCR_ZOOM = fitz.Matrix(2.0, 2.0)

# Pages with fewer native-text characters than this threshold are treated as scanned.
_MIN_NATIVE_CHARS = 50


def _ocr_page(page: fitz.Page) -> str:
    pix = page.get_pixmap(matrix=_OCR_ZOOM)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)


def _extract_page_text(page: fitz.Page) -> str:
    native = page.get_text().strip()
    if len(native) >= _MIN_NATIVE_CHARS:
        return native
    ocr = _ocr_page(page).strip()
    return ocr if ocr else native


def extract_text_from_path(
    pdf_path: Path | str,
    max_pages: int | None = None,
) -> str:
    doc = fitz.open(str(pdf_path))
    try:
        n = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        parts = [_extract_page_text(doc[i]) for i in range(n)]
    finally:
        doc.close()
    return "\n".join(p for p in parts if p)


def extract_text_from_bytes(
    content: bytes,
    max_pages: int | None = None,
) -> str:
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        n = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        parts = [_extract_page_text(doc[i]) for i in range(n)]
    finally:
        doc.close()
    return "\n".join(p for p in parts if p)


def get_page_count(content: bytes) -> int:
    doc = fitz.open(stream=content, filetype="pdf")
    count = doc.page_count
    doc.close()
    return count


def extract_page_text_from_bytes(content: bytes, page_num: int) -> str:
    """Extract text from a single page (0-indexed)."""
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        if page_num >= doc.page_count:
            return ""
        return _extract_page_text(doc[page_num])
    finally:
        doc.close()


def is_separator_page(text: str, max_words: int = 60) -> bool:
    """
    Returns True if a page looks like a UAE submittal index/routing slip.
    UAE separator pages contain the routing columns:
      'Authority | Employer | Engineer Lead / Consultant | Contractor'
    and very few words (typically 7–20).
    Proven accurate in Phase 2 Experiment A Scenario 2.
    """
    words = text.split()
    if len(words) > max_words:
        return False
    text_lower = text.lower()
    # UAE-specific routing slip pattern (high confidence, zero false positives)
    uae_keywords = {"authority", "employer", "engineer", "contractor"}
    if uae_keywords.issubset(set(text_lower.split())):
        return True
    # Generic index section fallback
    import re
    _SEPARATOR_PATTERNS = [
        r"\bcover page\b", r"\bindex\s*\d+\b", r"\btab\s*\d+\b",
        r"\bappendix\b", r"\bboq\b", r"\bbill of quantities\b",
    ]
    return any(re.search(p, text_lower) for p in _SEPARATOR_PATTERNS)
