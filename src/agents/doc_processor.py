from __future__ import annotations

import json

from langsmith import traceable
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.config import get_authority_profile
from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.classifier import classify_uploaded_file
from src.parsers.pdf_parser import extract_text_from_bytes

_MODEL = "gpt-4o-mini"
_MAX_COVER_CHARS = 3000

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


class _CoverPageData:
    material_description: str = ""
    spec_clause: str = ""
    manufacturer_name: str = ""
    manufacturer_address: str = ""
    supplier_name: str = ""
    supplier_address: str = ""


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


@traceable(name="doc_processor_agent")
def doc_processor_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 1 — Document Processor.

    1. Classifies each uploaded PDF by document type.
    2. Extracts cover page fields (material, clause, manufacturer, supplier).
    3. Writes classified_documents and cover page fields to shared state.
    """
    file_contents: dict[str, bytes] = state.get("file_contents", {})
    declared_labels: dict[str, str | None] = state.get("declared_labels", {})

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
                page_count=1,
                declared_label=declared_label,
                mismatch_flagged=False,
            )
        classified[filename] = doc.model_dump()

        # Extract cover page fields from the identified cover page document
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
