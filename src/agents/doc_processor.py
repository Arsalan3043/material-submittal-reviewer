from __future__ import annotations

import json

from langsmith import traceable
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.classifier import classify_document, classify_uploaded_file
from src.parsers.pdf_parser import (
    extract_page_text_from_bytes,
    extract_text_from_bytes,
    get_page_count,
    is_separator_page,
)

_MODEL = "gpt-4o-mini"
_MAX_COVER_CHARS = 3000

# Single-file submittals with more pages than this are treated as bundled packages.
_BUNDLED_THRESHOLD = 20

# Option A: minimum separator pages needed to use separator-based splitting.
_MIN_SEPARATORS = 2

# Option B: sample interval and window size for sparse sampling fallback.
# Step=3 ensures sections as short as 1-2 pages are sampled at least once.
_SAMPLE_STEP = 3
_SAMPLE_WINDOW = 3  # pages of context per sample point

# Option B: stop scanning after this many consecutive steps with no new doc type.
_EARLY_STOP_STEPS = 5

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


def _make_virtual_doc(
    filename: str,
    page: int,
    text: str,
    result,
) -> dict:
    """Build a ClassifiedDocument dict for a virtual section inside a bundled PDF."""
    virtual_fn = f"{filename}[{result.doc_type.value}:p{page + 1}]"
    return ClassifiedDocument(
        filename=virtual_fn,
        doc_type=result.doc_type,
        confidence=result.confidence,
        reasoning=result.reasoning,
        key_indicators=result.key_indicators,
        text_preview=text[:500],
        page_count=1,
        declared_label=None,
        mismatch_flagged=False,
    ).model_dump()


def _classify_page_window(content: bytes, start: int, page_count: int) -> tuple[str, str]:
    """
    Extract up to _SAMPLE_WINDOW pages starting at `start` and return (combined_text, first_page_text).
    Used by both Option A and Option B.
    """
    texts = []
    for p in range(start, min(start + _SAMPLE_WINDOW, page_count)):
        t = extract_page_text_from_bytes(content, p)
        if t.strip():
            texts.append(t)
    combined = "\n".join(texts)
    first = texts[0] if texts else ""
    return combined, first


# ── Option A: separator-based splitting ───────────────────────────────────────

def _find_separator_pages(content: bytes, page_count: int) -> list[int]:
    """Return 0-indexed page numbers that are UAE routing-slip / separator pages."""
    return [
        i for i in range(page_count)
        if is_separator_page(extract_page_text_from_bytes(content, i))
    ]


def _classify_by_separators(
    filename: str,
    content: bytes,
    separator_pages: list[int],
    page_count: int,
) -> tuple[dict[str, dict], str]:
    """
    Classify the first content page of each section defined by separator pages.
    Section 0 starts at page 0 (if page 0 is not itself a separator).
    Each subsequent section starts one page after its separator.
    """
    sep_set = set(separator_pages)
    # First content page of each section
    section_starts: list[int] = []
    if 0 not in sep_set:
        section_starts.append(0)
    for sep in sorted(sep_set):
        nxt = sep + 1
        if nxt < page_count and nxt not in sep_set:
            section_starts.append(nxt)

    sections: dict[str, dict] = {}
    seen_types: set[DocType] = set()
    cover_text = ""

    for start in section_starts:
        text = extract_page_text_from_bytes(content, start)
        if not text.strip():
            continue
        result = classify_document(text)
        if result.doc_type not in seen_types and result.confidence in ("high", "medium"):
            seen_types.add(result.doc_type)
            sections[f"{filename}[{result.doc_type.value}:p{start + 1}]"] = _make_virtual_doc(
                filename, start, text, result
            )
            if result.doc_type == DocType.COVER_PAGE and not cover_text:
                cover_text = text

    return sections, cover_text


# ── Option B: sparse sampling with early stop ─────────────────────────────────

def _classify_by_sparse_sampling(
    filename: str,
    content: bytes,
    page_count: int,
) -> tuple[dict[str, dict], str]:
    """
    Sample every _SAMPLE_STEP pages, classifying each page in the window
    individually so adjacent documents of different types (e.g. DED on p55,
    guarantee on p57) are both captured in the same step.
    Stop after _EARLY_STOP_STEPS consecutive steps that yield no new doc type.
    """
    sections: dict[str, dict] = {}
    seen_types: set[DocType] = set()
    cover_text = ""
    consecutive_no_new = 0

    for start in range(0, page_count, _SAMPLE_STEP):
        found_new_this_step = False

        for p in range(start, min(start + _SAMPLE_WINDOW, page_count)):
            text = extract_page_text_from_bytes(content, p)
            if not text.strip():
                continue
            result = classify_document(text)
            if result.doc_type not in seen_types and result.confidence in ("high", "medium"):
                seen_types.add(result.doc_type)
                found_new_this_step = True
                sections[f"{filename}[{result.doc_type.value}:p{p + 1}]"] = _make_virtual_doc(
                    filename, p, text, result
                )
                if result.doc_type == DocType.COVER_PAGE and not cover_text:
                    cover_text = text

        if found_new_this_step:
            consecutive_no_new = 0
        else:
            consecutive_no_new += 1
            if consecutive_no_new >= _EARLY_STOP_STEPS:
                break

    return sections, cover_text


# ── Option C: hybrid entry point ──────────────────────────────────────────────

def _classify_bundled_pdf(
    filename: str,
    content: bytes,
    page_count: int,
) -> tuple[dict[str, dict], str]:
    """
    Hybrid (Option C):
    1. Scan all pages for UAE separator/routing-slip pages — no LLM cost.
    2. If >= _MIN_SEPARATORS found → Option A (classify first page per section).
    3. Otherwise → Option B (sparse sampling every 10 pages, early stop).
    """
    separator_pages = _find_separator_pages(content, page_count)
    if len(separator_pages) >= _MIN_SEPARATORS:
        return _classify_by_separators(filename, content, separator_pages, page_count)
    return _classify_by_sparse_sampling(filename, content, page_count)


# ── Main agent node ───────────────────────────────────────────────────────────

@traceable(name="doc_processor_agent")
def doc_processor_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 1 — Document Processor.

    Handles two upload formats:
    - Individual files: one PDF per index item (standard path, one classify call per file).
    - Bundled PDF: entire submittal as one large PDF — detected by single file + high
      page count, then split using Option C hybrid (separator scan → sparse sampling).

    Writes classified_documents and cover page fields to shared state.
    """
    file_contents: dict[str, bytes] = state.get("file_contents", {})
    declared_labels: dict[str, str | None] = state.get("declared_labels", {})
    num_files = len(file_contents)

    classified: dict[str, dict] = {}
    cover_data: dict = {
        "material_description": "",
        "spec_clause": "",
        "manufacturer_name": "",
        "manufacturer_address": "",
        "supplier_name": "",
        "supplier_address": "",
    }

    for filename, content in file_contents.items():
        declared_label = declared_labels.get(filename)
        page_count = get_page_count(content)

        if num_files == 1 and page_count >= _BUNDLED_THRESHOLD:
            # ── Bundled submittal path ────────────────────────────────────────
            sections, cover_text = _classify_bundled_pdf(filename, content, page_count)
            classified.update(sections)
            if cover_text:
                cover_data = _extract_cover_page(cover_text)
        else:
            # ── Individual file path ──────────────────────────────────────────
            try:
                doc: ClassifiedDocument = classify_uploaded_file(
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
            classified[filename] = doc.model_dump()

            if doc.doc_type == DocType.COVER_PAGE:
                text = extract_text_from_bytes(content, max_pages=2)
                cover_data = _extract_cover_page(text)

    updates: SubmittalReviewState = {
        "classified_documents": classified,
        "material_description": cover_data.get("material_description", ""),
        "spec_clause": cover_data.get("spec_clause", ""),
        "manufacturer_name": cover_data.get("manufacturer_name", ""),
        "manufacturer_address": cover_data.get("manufacturer_address", ""),
        "supplier_name": cover_data.get("supplier_name", ""),
        "supplier_address": cover_data.get("supplier_address", ""),
    }
    return {**state, **updates}
