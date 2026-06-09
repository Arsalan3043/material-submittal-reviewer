from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.rag.indexing.pdf_loader import RawSpecPage


@dataclass
class SpecSection:
    authority: str
    network: str
    source_file: str
    division: str      # e.g. "03" (concrete)
    section: str       # e.g. "03300" (cast-in-place concrete)
    clause: str        # e.g. "03300.2.3"
    text: str
    page_nums: list[int] = field(default_factory=list)


# Matches common spec numbering patterns:
#   03300, 03300.1, 03300.1.2, 33 40 00, etc.
_CLAUSE_PATTERN = re.compile(
    r"^(\d{2,6}(?:\s\d{2}\s\d{2})?(?:\.\d+)*)\s+\w",
    re.MULTILINE,
)

# Division extracted from first two digits of section number
_DIVISION_RE = re.compile(r"^(\d{2})")


def _extract_clause_id(text: str) -> str | None:
    """Return the first clause number found in text, or None."""
    m = _CLAUSE_PATTERN.search(text)
    return m.group(1).strip() if m else None


def _extract_division(section: str) -> str:
    m = _DIVISION_RE.match(section.replace(" ", ""))
    return m.group(1) if m else "00"


def structure_pages(pages: list[RawSpecPage]) -> list[SpecSection]:
    """
    Group raw spec pages into SpecSection objects by clause boundary.
    Each distinct clause number starts a new section.
    Pages without a recognisable clause number are appended to the previous section.
    """
    sections: list[SpecSection] = []
    current: SpecSection | None = None

    for page in pages:
        clause_id = _extract_clause_id(page.text)

        if clause_id and (current is None or clause_id != current.clause):
            # Save the previous section if it has content
            if current and current.text.strip():
                sections.append(current)
            # Start a new section
            division = _extract_division(clause_id)
            current = SpecSection(
                authority=page.authority,
                network=page.network,
                source_file=page.source_file,
                division=division,
                section=clause_id.split(".")[0],
                clause=clause_id,
                text=page.text,
                page_nums=[page.page_num],
            )
        else:
            if current is None:
                # Leading pages before first clause — create a placeholder
                current = SpecSection(
                    authority=page.authority,
                    network=page.network,
                    source_file=page.source_file,
                    division="00",
                    section="",
                    clause="",
                    text=page.text,
                    page_nums=[page.page_num],
                )
            else:
                current.text += "\n" + page.text
                current.page_nums.append(page.page_num)

    if current and current.text.strip():
        sections.append(current)

    return sections
