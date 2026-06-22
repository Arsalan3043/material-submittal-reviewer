from __future__ import annotations

import json

import fitz
from langsmith import traceable
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.knowledge_store import DocumentSection, SubmittalKnowledgeStore
from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.classifier import classify_document, classify_uploaded_file
from src.parsers.pdf_parser import (
    extract_page_text_from_bytes,
    extract_text_from_bytes,
    get_page_count,
    is_separator_page,
)
from src.parsers.table_extractor import extract_all_table_rows

_MODEL = "gpt-4o-mini"
_MAX_COVER_CHARS = 3000

# Single-file submittals with more pages than this are treated as bundled packages.
_BUNDLED_THRESHOLD = 20

# Option A: minimum separator pages needed to use separator-based splitting.
_MIN_SEPARATORS = 2

# Option B: sample interval and window size for sparse sampling fallback.
# Step=3 + Window=3 covers every page once, catching adjacent 1-page sections
# (e.g. DED on p55 and guarantee on p57 with a separator between them).
_SAMPLE_STEP = 3
_SAMPLE_WINDOW = 3

# Stop scanning after this many consecutive steps with no new doc type.
_EARLY_STOP_STEPS = 5

# Maximum pages to extract per section — prevents runaway extraction into the
# next section when separator pages are not detected (sparse-sampling path).
_MAX_SECTION_PAGES = 20

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


_COVER_SYSTEM_PROMPT = """You are extracting structured information from a UAE construction material submittal cover page.
Extract the following fields exactly as they appear. If a field is not found, return an empty string.

Return JSON only:
{
  "material_description": "...",
  "spec_clause": "...",
  "manufacturer_name": "...",
  "manufacturer_address": "...",
  "supplier_name": "...",
  "supplier_address": "..."
}"""


@traceable(name="extract_cover_page")
def _extract_cover_page(text: str) -> dict:
    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _COVER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract from this cover page:\n\n{text[:_MAX_COVER_CHARS]}"},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {
            "material_description": "",
            "spec_clause": "",
            "manufacturer_name": "",
            "manufacturer_address": "",
            "supplier_name": "",
            "supplier_address": "",
        }


def _extract_pages_as_bytes(content: bytes, page_indices: list[int]) -> bytes:
    """Extract specific pages (0-indexed) from a PDF and return as new PDF bytes."""
    src = fitz.open(stream=content, filetype="pdf")
    out = fitz.open()
    for p in page_indices:
        if 0 <= p < src.page_count:
            out.insert_pdf(src, from_page=p, to_page=p)
    result = out.tobytes()
    src.close()
    out.close()
    return result


def _extract_section_text(
    content: bytes,
    start: int,
    page_count: int,
    sep_set: set[int],
) -> tuple[str, list[int]]:
    """
    Extract text forward from `start` (0-indexed) until a section boundary.
    Stops at the first of:
      - a page in sep_set (known separator from Option A),
      - a page detected as a separator by is_separator_page(),
      - _MAX_SECTION_PAGES pages reached.
    Returns (combined_text, 1-indexed page list).
    """
    texts: list[str] = []
    pages: list[int] = []
    for p in range(start, min(start + _MAX_SECTION_PAGES, page_count)):
        page_text = extract_page_text_from_bytes(content, p)
        if p != start and (p in sep_set or is_separator_page(page_text)):
            break
        if page_text.strip():
            texts.append(page_text)
            pages.append(p + 1)   # 1-indexed
    return "\n\n".join(texts), pages


# ── Option A: separator-based splitting ──────────────────────────────────────

def _find_separator_pages(content: bytes, page_count: int) -> list[int]:
    """Return 0-indexed page numbers that are UAE routing-slip / separator pages."""
    return [
        i for i in range(page_count)
        if is_separator_page(extract_page_text_from_bytes(content, i))
    ]


def _build_sections_by_separators(
    filename: str,
    content: bytes,
    separator_pages: list[int],
    page_count: int,
) -> tuple[list[DocumentSection], str]:
    """
    Option A: classify the first content page of each section defined by separator
    pages, then extract the full section text up to the next separator boundary.
    """
    sep_set = set(separator_pages)

    section_starts: list[int] = []
    if 0 not in sep_set:
        section_starts.append(0)
    for sep in sorted(sep_set):
        nxt = sep + 1
        if nxt < page_count and nxt not in sep_set:
            section_starts.append(nxt)

    sections: list[DocumentSection] = []
    seen_types: set[DocType] = set()
    cover_text = ""

    for start in section_starts:
        first_text = extract_page_text_from_bytes(content, start)
        if not first_text.strip():
            continue
        result = classify_document(first_text)
        if result.doc_type not in seen_types and result.confidence in ("high", "medium"):
            seen_types.add(result.doc_type)
            section_text, pages = _extract_section_text(content, start, page_count, sep_set)
            sections.append(DocumentSection(
                doc_type=result.doc_type,
                text=section_text,
                pages=pages,
                confidence=result.confidence,
                filename=f"{filename}[{result.doc_type.value}:p{start + 1}]",
            ))
            if result.doc_type == DocType.COVER_PAGE and not cover_text:
                cover_text = section_text

    return sections, cover_text


# ── Option B: sparse sampling with early stop ─────────────────────────────────

def _build_sections_by_sparse_sampling(
    filename: str,
    content: bytes,
    page_count: int,
    sep_set: set[int],
) -> tuple[list[DocumentSection], str]:
    """
    Option B: classify each page in a sampling window individually, then extract
    the full section text until the next detected boundary.

    sep_set is passed as boundary hints even when too few to trigger Option A.
    Separator pages within windows are skipped before classification.
    """
    sections: list[DocumentSection] = []
    seen_types: set[DocType] = set()
    cover_text = ""
    consecutive_no_new = 0

    for start in range(0, page_count, _SAMPLE_STEP):
        found_new_this_step = False

        for p in range(start, min(start + _SAMPLE_WINDOW, page_count)):
            page_text = extract_page_text_from_bytes(content, p)
            if not page_text.strip() or is_separator_page(page_text):
                continue
            result = classify_document(page_text)
            if result.doc_type not in seen_types and result.confidence in ("high", "medium"):
                seen_types.add(result.doc_type)
                found_new_this_step = True
                section_text, pages = _extract_section_text(content, p, page_count, sep_set)
                sections.append(DocumentSection(
                    doc_type=result.doc_type,
                    text=section_text,
                    pages=pages,
                    confidence=result.confidence,
                    filename=f"{filename}[{result.doc_type.value}:p{p + 1}]",
                ))
                if result.doc_type == DocType.COVER_PAGE and not cover_text:
                    cover_text = section_text

        if found_new_this_step:
            consecutive_no_new = 0
        else:
            consecutive_no_new += 1
            if consecutive_no_new >= _EARLY_STOP_STEPS:
                break

    return sections, cover_text


# ── Option C: hybrid entry point ──────────────────────────────────────────────

def _build_bundled_sections(
    filename: str,
    content: bytes,
    page_count: int,
) -> tuple[list[DocumentSection], str]:
    """
    Option C: scan for separators first (free), route to A if enough found,
    otherwise fall back to B passing separator hints for boundary detection.
    """
    separator_pages = _find_separator_pages(content, page_count)
    if len(separator_pages) >= _MIN_SEPARATORS:
        return _build_sections_by_separators(filename, content, separator_pages, page_count)
    return _build_sections_by_sparse_sampling(filename, content, page_count, set(separator_pages))


# ── Main agent node ───────────────────────────────────────────────────────────

@traceable(name="doc_processor_agent")
def doc_processor_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 1 — Knowledge Builder.

    Reads file_contents ONCE, classifies all documents, extracts full section text
    bounded by section boundaries, pre-parses comparison table rows, and extracts
    cover page metadata. Writes the result as a SubmittalKnowledgeStore JSON file
    and returns state with knowledge_store_id (a file path string).

    file_contents is NOT forwarded past this node — PDF bytes are consumed here.
    All downstream agents work from the knowledge store (text strings only).
    This keeps LangGraph state and LangSmith traces tiny.
    """
    file_contents: dict[str, bytes] = state.get("file_contents", {})
    declared_labels: dict[str, str | None] = state.get("declared_labels", {})
    submittal_id: str = state.get("submittal_id", "unknown")
    authority: str = state.get("authority", "ADM")
    num_files = len(file_contents)

    store = SubmittalKnowledgeStore(submittal_id=submittal_id, authority=authority)
    cover_text = ""
    comparison_table_bytes: bytes | None = None

    for filename, content in file_contents.items():
        declared_label = declared_labels.get(filename)
        page_count = get_page_count(content)

        if num_files == 1 and page_count >= _BUNDLED_THRESHOLD:
            # ── Bundled submittal path ────────────────────────────────────────
            sections, ct = _build_bundled_sections(filename, content, page_count)
            store.sections.extend(sections)
            if ct and not cover_text:
                cover_text = ct
            # Extract comparison table pages as a sub-PDF for upfront row parsing.
            for s in sections:
                if s.doc_type == DocType.COMPARISON_TABLE and comparison_table_bytes is None:
                    comparison_table_bytes = _extract_pages_as_bytes(
                        content, [p - 1 for p in s.pages]
                    )

        else:
            # ── Individual file path ──────────────────────────────────────────
            try:
                doc = classify_uploaded_file(
                    filename=filename,
                    content=content,
                    declared_label=declared_label,
                )
            except Exception as exc:
                doc = ClassifiedDocument(
                    filename=filename,
                    doc_type=DocType.OTHERS,
                    confidence="low",
                    reasoning=f"Classification failed: {exc}",
                    key_indicators=[],
                    text_preview="",
                    page_count=page_count,
                    declared_label=declared_label,
                    mismatch_flagged=False,
                )

            # Extract full text — classify_uploaded_file only reads 2 pages.
            full_text = extract_text_from_bytes(content, max_pages=None)
            store.sections.append(DocumentSection(
                doc_type=doc.doc_type,
                text=full_text,
                pages=list(range(1, page_count + 1)),
                confidence=doc.confidence,
                filename=filename,
                declared_label=declared_label,
                mismatch_flagged=doc.mismatch_flagged,
            ))

            if doc.doc_type == DocType.COVER_PAGE and not cover_text:
                cover_text = full_text[:_MAX_COVER_CHARS]
            if doc.doc_type == DocType.COMPARISON_TABLE and comparison_table_bytes is None:
                comparison_table_bytes = content

    # ── Extract cover page metadata ────────────────────────────────────────
    if cover_text:
        cover_data = _extract_cover_page(cover_text)
        store.material_description = cover_data.get("material_description", "")
        store.spec_clause          = cover_data.get("spec_clause", "")
        store.manufacturer_name    = cover_data.get("manufacturer_name", "")
        store.manufacturer_address = cover_data.get("manufacturer_address", "")
        store.supplier_name        = cover_data.get("supplier_name", "")
        store.supplier_address     = cover_data.get("supplier_address", "")

    # ── Pre-parse comparison table rows (pdfplumber + LLM, done once here) ─
    if comparison_table_bytes is not None:
        try:
            rows = extract_all_table_rows(comparison_table_bytes)
            store.table_rows = [r.model_dump() for r in rows if r.parameter.strip()]
        except Exception:
            store.table_rows = []

    # ── Persist knowledge store and return ────────────────────────────────
    knowledge_store_id = store.save()

    # Strip file_contents from state — bytes are fully consumed here.
    # All downstream agents read from the knowledge store file.
    new_state = {k: v for k, v in state.items() if k != "file_contents"}
    new_state["knowledge_store_id"] = knowledge_store_id
    return new_state
