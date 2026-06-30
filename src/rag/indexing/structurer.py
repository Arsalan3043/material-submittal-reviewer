from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.rag.indexing.pdf_loader import RawSpecPage


@dataclass
class SpecSection:
    authority: str
    network: str
    source_file: str
    division: str       # e.g. "02"      — from footer
    section: str        # e.g. "02810"   — from footer (primary filter key)
    clause: str         # e.g. "26.3.2"  — subsection number from body text
    chapter_name: str   # road only: "earthworks" — "" for div/section specs
    text: str
    page_nums: list[int] = field(default_factory=list)


# ── Subsection boundary detection ──────────────────────────────────────────────
#
# Within the body text of a section, subsections are headed by lines like:
#   "26.3.2 General"  (storm water / irrigation)
#   "2.1.1 Scope"     (road)
#   "13.1.6 Submittals"
#
# Pattern: ≥2 levels of dot-separated integers at the start of a line,
# followed by a space and at least one word character.
# Single-level numbers (plain "26") are NOT treated as subsection starts —
# they are article or part numbers in the spec numbering scheme and appear
# in the middle of paragraphs.

_SUBSECTION_RE = re.compile(
    r'^(\d+\.\d+(?:\.\d+)*)\s+\w',
    re.MULTILINE,
)

# ── Fallback: body-text clause detection (kept for pages with no footer) ───────
#
# Matches lines that start with a spec clause number directly:
#   "03300 Cast-in-Place Concrete", "33 40 00 Storm Drainage"
# This was the original sole detection method.  It is now the fallback only.

_BODY_CLAUSE_RE = re.compile(
    r'^(\d{2,6}(?:\s\d{2}\s\d{2})?(?:\.\d+)*)\s+\w',
    re.MULTILINE,
)

_DIVISION_PREFIX_RE = re.compile(r'^(\d{2})')


# ── Internal helpers ───────────────────────────────────────────────────────────

def _extract_body_clause(text: str) -> str:
    """Return the first clause number found in body text, or ''."""
    m = _BODY_CLAUSE_RE.search(text)
    return m.group(1).strip() if m else ""


def _split_into_subsections(
    body_text: str,
    section_id: str,
    division_id: str,
    chapter_name: str,
    authority: str,
    network: str,
    source_file: str,
    page_nums: list[int],
) -> list[SpecSection]:
    """
    Split a section's combined body text at subsection heading boundaries.

    Each subsection becomes one SpecSection with:
      section  = section_id from footer  (e.g. "02810")
      clause   = subsection number       (e.g. "26.3.2")

    Any text before the first detected subsection heading is kept as a
    preamble chunk under clause = section_id (the section-level overview).

    If no subsection headings are found the entire body is returned as a
    single SpecSection under clause = section_id.
    """
    matches = list(_SUBSECTION_RE.finditer(body_text))

    if not matches:
        return [SpecSection(
            authority=authority, network=network, source_file=source_file,
            division=division_id, section=section_id,
            clause=section_id,
            chapter_name=chapter_name,
            text=body_text.strip(),
            page_nums=page_nums,
        )]

    results: list[SpecSection] = []

    # Preamble: text before the first subsection heading
    preamble = body_text[:matches[0].start()].strip()
    if preamble:
        results.append(SpecSection(
            authority=authority, network=network, source_file=source_file,
            division=division_id, section=section_id,
            clause=section_id,
            chapter_name=chapter_name,
            text=preamble,
            page_nums=page_nums,
        ))

    # One SpecSection per detected subsection
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body_text)
        sub_text = body_text[start:end].strip()
        if sub_text:
            results.append(SpecSection(
                authority=authority, network=network, source_file=source_file,
                division=division_id, section=section_id,
                clause=match.group(1),   # e.g. "26.3.2"
                chapter_name=chapter_name,
                text=sub_text,
                page_nums=page_nums,
            ))

    return results


# ── Public API ─────────────────────────────────────────────────────────────────

def structure_pages(pages: list[RawSpecPage]) -> list[SpecSection]:
    """
    Convert raw spec pages into SpecSection objects.

    Strategy (in priority order):

    1. Footer-driven (primary path — storm water, irrigation, road):
       Consecutive pages that share the same footer section_id are grouped
       into one section block.  The combined body text is then split at
       subsection heading boundaries (e.g. "26.3.2 General").
       Result: one SpecSection per subsection, with accurate section metadata
       derived directly from the footer rather than body-text guessing.

    2. Body-text fallback (for pages where footer parsing returned no section_id):
       The original clause-boundary detection via _BODY_CLAUSE_RE is used as
       before.  This handles any spec PDFs that don't follow the standard
       ADM/TAQA footer format.
    """
    # ── Group consecutive pages by section_id ─────────────────────────────────
    # A group is a run of pages that share the same section_id (including "").
    # When section_id changes, a new group starts.
    groups: list[list[RawSpecPage]] = []
    current: list[RawSpecPage] = []

    for page in pages:
        if current and page.section_id != current[0].section_id:
            groups.append(current)
            current = []
        current.append(page)
    if current:
        groups.append(current)

    # ── Process each group into SpecSection objects ────────────────────────────
    all_sections: list[SpecSection] = []

    for group in groups:
        # Combine body text from all pages in the group (skip blank pages)
        body_text = "\n\n".join(p.text for p in group if p.text.strip())
        if not body_text.strip():
            continue

        page_nums = [p.page_num for p in group]
        first = group[0]
        section_id = first.section_id

        if section_id:
            # ── Primary path: footer-driven ───────────────────────────────────
            subsections = _split_into_subsections(
                body_text=body_text,
                section_id=section_id,
                division_id=first.division_id,
                chapter_name=first.chapter_name,
                authority=first.authority,
                network=first.network,
                source_file=first.source_file,
                page_nums=page_nums,
            )
            all_sections.extend(subsections)

        else:
            # ── Fallback path: body-text clause detection ─────────────────────
            clause_id = _extract_body_clause(body_text)
            section = clause_id.split(".")[0] if clause_id else ""
            m = _DIVISION_PREFIX_RE.match(section.replace(" ", ""))
            division = m.group(1) if m else "00"

            all_sections.append(SpecSection(
                authority=first.authority,
                network=first.network,
                source_file=first.source_file,
                division=division,
                section=section,
                clause=clause_id,
                chapter_name="",
                text=body_text.strip(),
                page_nums=page_nums,
            ))

    return all_sections
