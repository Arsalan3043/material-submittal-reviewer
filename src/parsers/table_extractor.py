from __future__ import annotations

import json

import fitz
import pytesseract
from PIL import Image
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from src.parsers.pdf_parser import _OCR_ZOOM

# gpt-4o-mini proven: 9/9 row match rate, remarks 100% (Experiment B).
# OCR is the primary path — pdfplumber is opportunistic only.
# Real UAE submittals are scanned; pdfplumber returns nothing in practice.
_MODEL = "gpt-4o-mini"
_MAX_OCR_CHARS = 4000

# Pages where OCR returns fewer than this many words are blank, image-only, or pure
# graphical pages with no table content. Word count is more reliable than character
# count because Tesseract noise produces random characters but rarely coherent words.
# A real table row like "pH | 6-8 | 7.2 | Pass" is already 4 words, so 5 is safe.
_MIN_OCR_WORDS_FOR_LLM = 5

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


class TableRow(BaseModel):
    parameter: str
    specified: str
    proposed: str
    deviation: str   # often empty — real UAE submittals omit Deviation column (Experiment B)
    measured: str    # often empty — treat as "not tested", not as error
    remarks: str     # serves as combined deviation/compliance indicator in real submittals


class TablePageResult(BaseModel):
    rows: list[TableRow]
    column_headers_raw: list[str]
    is_continuation: bool
    has_header: bool
    notes: str


_SYSTEM_PROMPT = """You are extracting comparison table data from a UAE construction material submittal.

The comparison table compares what the project specification requires against what the contractor is proposing.

Standard column types (real tables use varying names):
  parameter  — the property being compared (e.g. "Width", "Tensile Strength", "Color")
  specified  — what the specification requires (value, standard, limit)
  proposed   — what the contractor's product offers
  deviation  — any declared non-compliance or deviation (often empty or "-" if compliant)
  measured   — test result from a lab test report (often empty if not yet tested)
  remarks    — compliance status or comment ("Comply", "Non-Compliant", etc.)

Common alternative column names (proven in Experiment B — zero-shot mapping works):
  specified → "As per Spec", "Specification Requirement", "Required", "Standard"
  proposed  → "As Offered", "Offered Value", "Contractor's Proposal"
  deviation → "Deviation / Compliance", "Non-Compliance"
  measured  → "Test Result", "Actual", "Measured Value"
  remarks   → "Compliance", "Comment", "Status", "Properties"

Rules:
1. If this page is a cover page, title page, or has no table → return empty rows list.
2. If OCR text is too garbled to extract reliably → return empty rows and explain in notes.
   Do NOT invent or hallucinate table data from unreadable text.
3. If the page continues a table from a previous page (no column headers visible) → set is_continuation=true.
4. Extract every data row. Multi-line cells should be joined into one string.
5. Preserve numeric values exactly as they appear — do not round or reformat.
6. Empty cells → use empty string "". An empty deviation means "no deviation declared" (acceptable).
7. An empty measured value means "not tested" — do not treat it as an error.

Return JSON only:
{
  "rows": [
    {
      "parameter": "...",
      "specified": "...",
      "proposed":  "...",
      "deviation": "...",
      "measured":  "...",
      "remarks":   "..."
    }
  ],
  "column_headers_raw": ["raw column header 1", ...],
  "is_continuation": false,
  "has_header": true,
  "notes": "any issues or reason for empty extraction"
}"""


def _ocr_page_bytes(content: bytes, page_num: int) -> str:
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        if page_num >= doc.page_count:
            return ""
        page = doc[page_num]
        pix = page.get_pixmap(matrix=_OCR_ZOOM)
    finally:
        doc.close()
    img = Image.open(__import__("io").BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)


def _try_pdfplumber(content: bytes, page_num: int) -> list[list[str]] | None:
    """Opportunistic pdfplumber attempt for digitally-created PDFs."""
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if page_num >= len(pdf.pages):
                return None
            table = pdf.pages[page_num].extract_table()
            if not table:
                return None
            rows = [[cell or "" for cell in row] for row in table if any(cell for cell in row)]
            return rows if len(rows) >= 2 else None
    except Exception:
        return None


def _extract_with_llm(text: str) -> TablePageResult:
    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract the comparison table:\n\n{text[:_MAX_OCR_CHARS]}"},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = response.choices[0].message.content
    try:
        return TablePageResult.model_validate_json(raw)
    except (ValidationError, ValueError):
        parsed = json.loads(raw)
        rows = []
        for r in parsed.get("rows", []):
            try:
                rows.append(TableRow(
                    parameter=r.get("parameter", ""),
                    specified=r.get("specified", ""),
                    proposed=r.get("proposed", ""),
                    deviation=r.get("deviation", ""),
                    measured=r.get("measured", ""),
                    remarks=r.get("remarks", ""),
                ))
            except Exception:
                pass
        return TablePageResult(
            rows=rows,
            column_headers_raw=parsed.get("column_headers_raw", []),
            is_continuation=parsed.get("is_continuation", False),
            has_header=parsed.get("has_header", False),
            notes=parsed.get("notes", "parse fallback applied"),
        )


def extract_table_page(content: bytes, page_num: int) -> TablePageResult:
    """
    Primary: OCR → LLM.  Fallback: pdfplumber if OCR returns nothing.
    OCR-first because all real UAE submittals are scanned (Experiment B, Decision 1).

    Pages where OCR returns fewer than _MIN_OCR_WORDS_FOR_LLM words are skipped
    entirely — they are blank, graphical, or image-only pages that will always
    return empty rows. Skipping avoids one wasted LLM call per such page.
    """
    # Step 1: OCR (primary for scanned PDFs)
    ocr_text = _ocr_page_bytes(content, page_num).strip()
    if len(ocr_text.split()) >= _MIN_OCR_WORDS_FOR_LLM:
        result = _extract_with_llm(ocr_text)
        if result.rows:
            return result

    # Step 2: pdfplumber (opportunistic for digital PDFs, or when OCR was thin)
    raw_rows = _try_pdfplumber(content, page_num)
    if raw_rows:
        raw_text = "\n".join(" | ".join(cell for cell in row) for row in raw_rows)
        return _extract_with_llm(raw_text)

    return TablePageResult(
        rows=[], column_headers_raw=[], is_continuation=False,
        has_header=False, notes="No extractable content on this page.",
    )


def extract_all_table_rows(content: bytes) -> list[TableRow]:
    """
    Extract all comparison table rows from a PDF (all pages combined).
    Skips separator/cover pages automatically via LLM (returns 0 rows).
    """
    doc = fitz.open(stream=content, filetype="pdf")
    page_count = doc.page_count
    doc.close()

    all_rows: list[TableRow] = []
    for i in range(page_count):
        page_result = extract_table_page(content, i)
        all_rows.extend(page_result.rows)
    return all_rows
