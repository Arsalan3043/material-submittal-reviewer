from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass
class RawSpecPage:
    authority: str
    network: str       # e.g. "irrigation", "storm_water", "road"
    source_file: str   # original PDF filename
    page_num: int      # 0-indexed
    text: str


def load_spec_pdf(
    pdf_path: Path | str,
    authority: str,
    network: str,
) -> list[RawSpecPage]:
    """
    Extract all text pages from a specification PDF.
    Returns one RawSpecPage per page, skipping blank pages.

    network is the spec book category (e.g. "irrigation") used as the
    metadata filter key in exp05 — carried through to production as-is.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    pages: list[RawSpecPage] = []
    try:
        for i in range(doc.page_count):
            text = doc[i].get_text().strip()
            if text:
                pages.append(RawSpecPage(
                    authority=authority,
                    network=network,
                    source_file=pdf_path.name,
                    page_num=i,
                    text=text,
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
    e.g. {"irrigation_spec": "irrigation", "road_spec": "road"}
    """
    spec_dir = Path(spec_dir)
    all_pages: list[RawSpecPage] = []
    for pdf_path in sorted(spec_dir.glob("*.pdf")):
        network = network_map.get(pdf_path.stem, pdf_path.stem)
        all_pages.extend(load_spec_pdf(pdf_path, authority, network))
    return all_pages
