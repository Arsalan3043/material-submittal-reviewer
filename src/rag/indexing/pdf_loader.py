from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass
class RawSpecPage:
    authority: str
    network: str         # admin-typed label passed through from upload form
    source_file: str
    page_num: int        # 0-indexed
    text: str            # body text only — header and footer stripped
    header_text: str     # raw header (top zone of page)
    footer_text: str     # raw footer (bottom zone of page)
    section_id: str      # parsed from footer: "02810", "01027" — "" if not detected
    division_id: str     # parsed from footer: "02", "01"     — "" if not detected
    chapter_name: str    # road specs only: "earthworks"      — "" for div/section specs


# ── Footer parsing ─────────────────────────────────────────────────────────────
#
# Storm water:  "DIVISION 01 - SECTION 01027   PAGE 167   FIRST EDITION -DECEMBER 2016"
# Irrigation:   "DIVISION 01-SECTION 01027  page x   FIRST EDITION -DECEMBER 2016"
# Variations:   "DIVISION: 01  SECTION: 01027", "DIVISION-02 -SECTION 02810"

_DIV_SECTION_RE = re.compile(
    r'DIVISION\s*[-:]*\s*(\d{1,3})\s*[-–\s]*SECTION\s*[-:]*\s*(\d{4,6})',
    re.IGNORECASE,
)

# Road: "CHAPTER 2: EARTHWORKS" or "CHAPTER 2 - EARTHWORKS"
_CHAPTER_RE = re.compile(
    r'CHAPTER\s+(\d{1,3})\s*[:\-–]\s*([A-Z][A-Z0-9\s&/()\-]+)',
    re.IGNORECASE,
)


def _parse_footer(footer_text: str) -> tuple[str, str, str]:
    """
    Parse footer text → (division_id, section_id, chapter_name).

    Storm water / Irrigation  →  ("02", "02810", "")
    Road (chapter-based)      →  ("2",  "2",     "earthworks")
    Not recognised            →  ("",   "",      "")
    """
    m = _DIV_SECTION_RE.search(footer_text)
    if m:
        div = m.group(1).zfill(2)   # "1" → "01", "02" stays "02"
        sec = m.group(2)            # keep original: "02810", "01027"
        return div, sec, ""

    m = _CHAPTER_RE.search(footer_text)
    if m:
        num = m.group(1)
        raw_name = m.group(2)
        # The chapter name ends at the first run of 2+ spaces or the keyword
        # "page" / "first" / "edition" — everything after that is metadata noise
        # (page numbers, edition text).
        trimmed = re.split(
            r'\s{2,}|\s+(?:page|first|edition)\b', raw_name, flags=re.IGNORECASE
        )[0]
        name = re.sub(r'\s+', '_', trimmed.strip().lower()).strip('_')
        return num, num, name

    return "", "", ""


# ── Page zone splitter ─────────────────────────────────────────────────────────
#
# PyMuPDF's get_text("blocks") returns a list of:
#   (x0, y0, x1, y1, text, block_no, block_type)
# where y is measured from the top of the page (0 = top edge).
#
# We use the vertical centre of each block to assign it to a zone:
#   header zone  — top 12 % of page height
#   footer zone  — bottom 12 % of page height  (i.e. centre_y > 88 %)
#   body zone    — everything in between
#
# 12 % on an A4 PDF page (842 pt) ≈ 101 pt ≈ 3.6 cm, which comfortably
# accommodates single-line or two-line headers / footers in technical specs.

_HEADER_FRACTION = 0.12
_FOOTER_FRACTION = 0.88


def _split_page_zones(page: fitz.Page) -> tuple[str, str, str]:
    """Return (header_text, body_text, footer_text) for one PDF page."""
    page_height = page.rect.height
    header_line = page_height * _HEADER_FRACTION
    footer_line = page_height * _FOOTER_FRACTION

    header_parts: list[str] = []
    body_parts: list[str] = []
    footer_parts: list[str] = []

    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text, block_no, block_type = block
        if block_type != 0:   # skip image blocks
            continue
        text = text.strip()
        if not text:
            continue
        centre_y = (y0 + y1) / 2
        if centre_y < header_line:
            header_parts.append(text)
        elif centre_y > footer_line:
            footer_parts.append(text)
        else:
            body_parts.append(text)

    return (
        "\n".join(header_parts),
        "\n".join(body_parts),
        "\n".join(footer_parts),
    )


# ── Main loader ────────────────────────────────────────────────────────────────

def load_spec_pdf(
    pdf_path: Path | str,
    authority: str,
    network: str,
) -> list[RawSpecPage]:
    """
    Extract all pages from a specification PDF with header/footer separation.

    Each page is split into three vertical zones using PyMuPDF block bounding
    boxes.  The footer zone is parsed for division/section (or chapter) metadata
    that is stored directly on the page object and later used by the structurer
    instead of unreliable body-text regex matching.

    Returns one RawSpecPage per non-blank page.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    pages: list[RawSpecPage] = []
    try:
        for i in range(doc.page_count):
            page = doc[i]
            header_text, body_text, footer_text = _split_page_zones(page)

            if not (header_text or body_text or footer_text):
                continue  # entirely blank page

            division_id, section_id, chapter_name = _parse_footer(footer_text)

            pages.append(RawSpecPage(
                authority=authority,
                network=network,
                source_file=pdf_path.name,
                page_num=i,
                text=body_text,
                header_text=header_text,
                footer_text=footer_text,
                section_id=section_id,
                division_id=division_id,
                chapter_name=chapter_name,
            ))
    finally:
        doc.close()
    return pages


def load_spec_directory(
    spec_dir: Path | str,
    authority: str,
    network_map: dict[str, str],
) -> list[RawSpecPage]:
    """
    Load all PDFs from a directory.
    network_map: {filename_stem → network_label}
    """
    spec_dir = Path(spec_dir)
    all_pages: list[RawSpecPage] = []
    for pdf_path in sorted(spec_dir.glob("*.pdf")):
        network = network_map.get(pdf_path.stem, pdf_path.stem)
        all_pages.extend(load_spec_pdf(pdf_path, authority, network))
    return all_pages
