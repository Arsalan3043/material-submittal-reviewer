from __future__ import annotations

import json

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.pdf_parser import extract_text_from_bytes, get_page_count

# Proven in Phase 2 Experiment A: gpt-4o-mini achieves 96.2% effective accuracy.
_MODEL = "gpt-4o-mini"
_MAX_TEXT_CHARS = 2000

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


class _ClassificationResult(BaseModel):
    doc_type: DocType
    confidence: str
    reasoning: str
    key_indicators: list[str]


_SYSTEM_PROMPT = """You are a document classifier for UAE construction material submittals.
Identify the document type from its text content. Return JSON only — no extra text.

Document types and their distinguishing features:

cover_page
  Submittal title page. Contains: material name, submittal reference number,
  specification clause reference (e.g. Clause 07100), contractor name,
  consultant name, manufacturer name and address, supplier name and address.
  No technical data — purely identifying/routing information.

msdf
  Material Source Declaration Form. Formal project or authority form.
  Fields for: material origin, country of manufacture, manufacturer details,
  declaration checkboxes or signature/stamp section.

specification_copy
  Extract from project technical specification document. Dense technical text
  with clause numbers (e.g. 03300.2.3, 07100.1.1), ASTM/BS/ISO standard codes,
  material performance requirements and numerical values.

boq
  Bill of Quantities. Table with columns: Item No. / Description / Unit / Quantity.
  Purpose is quantity take-off; no approval or compliance content.

drawing
  Engineering drawing. Sparse text, has drawing title block with project name,
  drawing number, revision table, scale, grid references. Content is mostly
  graphical (lines, dimensions, symbols); extracted text is minimal.

comparison_table
  Technical comparison of specified vs proposed material. Must have columns or
  rows structured around: Specified / Proposed / Deviation or Compliance / Remarks.
  Core submittal review document.

technical_datasheet
  Manufacturer product datasheet or catalogue page. Contains: product model
  numbers, technical property tables, performance data, certifications held,
  application guidance from the manufacturer.

test_report
  Third-party or laboratory test results. Contains: test method codes
  (ASTM/BS/ISO), numerical test results, test date, accredited lab name,
  sample description, pass/fail against standard limits.

ded_registration
  UAE Department of Economic Development company registration certificate.
  Government-issued. Contains: trade name, license number, license type,
  expiry date, licensed activities list.

manufacturer_guarantee
  Letter on manufacturer company letterhead guaranteeing product quality and
  performance for a stated period (e.g. 10 years). Signed and stamped.

previous_approval
  Prior approval letter, certificate, or official stamp from an engineer,
  consultant, or authority confirming an earlier approval of the same material.

method_statement
  Step-by-step installation or application procedure document. Contains:
  scope, referenced standards, preparatory work steps, application steps,
  safety precautions, quality control checks.

maf
  Material Approval Form. Official form with structured fields:
  material description, contractor reference, consultant reference,
  approval status box (Approved / Conditionally Approved / Rejected / Resubmit).

others
  Does not fit any category above.

Return this JSON structure:
{
  "doc_type": "<one of the types above>",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence explaining the deciding factor>",
  "key_indicators": ["<specific text fragment 1>", "<specific text fragment 2>"]
}"""


def classify_document(
    text: str,
    declared_label: str | None = None,
) -> _ClassificationResult:
    """
    Classify a document from its extracted text.
    If declared_label is provided, the prompt includes the submitted section
    label — enabling mismatch detection (proven 100% accurate in Experiment A).
    """
    user_msg = f"Classify this document:\n\n{text[:_MAX_TEXT_CHARS]}"
    if declared_label:
        user_msg += f"\n\n[Submitted under section: '{declared_label}']"

    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = response.choices[0].message.content
    try:
        return _ClassificationResult.model_validate_json(raw)
    except (ValidationError, ValueError):
        parsed = json.loads(raw)
        return _ClassificationResult(
            doc_type=parsed.get("doc_type", DocType.OTHERS),
            confidence=parsed.get("confidence", "low"),
            reasoning=parsed.get("reasoning", "parse fallback"),
            key_indicators=parsed.get("key_indicators", []),
        )


def classify_uploaded_file(
    filename: str,
    content: bytes,
    declared_label: str | None = None,
    max_pages: int = 2,
) -> ClassifiedDocument:
    """
    Full pipeline: PDF bytes → text extraction → classification → ClassifiedDocument.
    Handles OCR fallback internally via pdf_parser.
    """
    text = extract_text_from_bytes(content, max_pages=max_pages)
    page_count = get_page_count(content)

    result = classify_document(text, declared_label=declared_label)

    # Determine if document was placed in the wrong section.
    # Exception: maf in Index 8 (Previous Approvals) is a known UAE convention.
    mismatch = False
    if declared_label and result.doc_type != DocType.OTHERS:
        expected = _LABEL_TO_DOCTYPE.get(declared_label)
        if expected and result.doc_type != expected:
            # maf placed in Previous Approvals section is intentional, not a mismatch
            if not (
                result.doc_type == DocType.MAF
                and expected == DocType.PREVIOUS_APPROVAL
            ):
                mismatch = True

    return ClassifiedDocument(
        filename=filename,
        doc_type=result.doc_type,
        confidence=result.confidence,
        reasoning=result.reasoning,
        key_indicators=result.key_indicators,
        text_preview=text[:500],
        page_count=page_count,
        declared_label=declared_label,
        mismatch_flagged=mismatch,
    )


# Maps declared section labels (from UI or filename) to expected DocType.
# Used only for mismatch detection — not for routing logic.
_LABEL_TO_DOCTYPE: dict[str, DocType] = {
    "BOQ & Drawings":                                     DocType.BOQ,
    "Copies of Relevant Specifications":                  DocType.SPECIFICATION_COPY,
    "Technical Comparison Table":                         DocType.COMPARISON_TABLE,
    "Manufacturer's Technical Data":                      DocType.TECHNICAL_DATASHEET,
    "Recent Test Reports and Certificates":               DocType.TEST_REPORT,
    "Department of Economic Development (Registration)":  DocType.DED_REGISTRATION,
    "Manufacturer/Supplier Guarantee":                    DocType.MANUFACTURER_GUARANTEE,
    "Previous Approvals":                                 DocType.PREVIOUS_APPROVAL,
    "Applicator's Method Statement":                      DocType.METHOD_STATEMENT,
    "Material Approval Form":                             DocType.MAF,
    "Material Source Declaration Form":                   DocType.MSDF,
}
