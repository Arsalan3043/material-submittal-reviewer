"""
Phase 2 — Experiment A: Document Classifier Accuracy Test

Tests GPT-4o-mini's ability to classify material submittal documents by type.

Three scenarios:
  1. Clean classification  — 26 labeled files from submittal_02 + submittal_03
                             Ground truth derived from filenames.
  2. Separator detection   — Rule-based scan of the 69-page combined submittal_01.
                             Identifies index title pages so the pipeline can split
                             a combined PDF into logical sections.
  3. Mismatch detection    — 4 documents deliberately passed under the wrong
                             declared section label. Verifies the classifier reads
                             actual content, not just the section name.

Run from project root:
    python experiments/llm/classifier_test.py

Results saved to:
    experiments/llm/results/classifier_results.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from enum import Enum

import io

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from openai import OpenAI
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

MODEL = "gpt-4o-mini"
MAX_TEXT_CHARS = 2000       # max chars extracted per document for the prompt
SEPARATOR_MAX_WORDS = 60    # pages with fewer words are candidate separator pages

DATA_DIR = Path("experiments/data/sample_submittals")
RESULTS_DIR = Path("experiments/llm/results")

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


# ── Document Type Enum ─────────────────────────────────────────────────────────

class DocType(str, Enum):
    COVER_PAGE             = "cover_page"
    MSDF                   = "msdf"                   # Material Source Declaration Form
    SPECIFICATION_COPY     = "specification_copy"
    BOQ                    = "boq"                    # Bill of Quantities
    DRAWING                = "drawing"
    COMPARISON_TABLE       = "comparison_table"
    TECHNICAL_DATASHEET    = "technical_datasheet"
    TEST_REPORT            = "test_report"
    DED_REGISTRATION       = "ded_registration"
    MANUFACTURER_GUARANTEE = "manufacturer_guarantee"
    PREVIOUS_APPROVAL      = "previous_approval"
    METHOD_STATEMENT       = "method_statement"
    MAF                    = "maf"                    # Material Approval Form
    OTHERS                 = "others"


# ── Ground Truth (derived from submittal_02 / submittal_03 filenames) ──────────

FILENAME_TO_DOCTYPE: dict[str, DocType] = {
    # submittal_02
    "0_Cover page.pdf":                                              DocType.COVER_PAGE,
    "1_Material Source Declaration form.pdf":                        DocType.MSDF,
    "2_Copies of Relevant parts of Specs.pdf":                       DocType.SPECIFICATION_COPY,
    "2.1_BOQ.pdf":                                                   DocType.BOQ,
    "2.2_Drawings.pdf":                                              DocType.DRAWING,
    "3_Technical Comparison.pdf":                                    DocType.COMPARISON_TABLE,
    "4_Manufacturer's Technical Data - Original Catalogues.pdf":     DocType.TECHNICAL_DATASHEET,
    "5_Recent Test Reports - Certificates.pdf":                      DocType.TEST_REPORT,
    "6_Departmet of Economic Development (Registration).pdf":        DocType.DED_REGISTRATION,
    "7_Manufacturer - Suppliers Guarantee.pdf":                      DocType.MANUFACTURER_GUARANTEE,
    "8_Previous Approvals.pdf":                                      DocType.PREVIOUS_APPROVAL,
    "9_Applicators Method Statement.pdf":                            DocType.METHOD_STATEMENT,
    "10_Others.pdf":                                                 DocType.OTHERS,
    # submittal_03
    "0. Cover Page.pdf":                                             DocType.COVER_PAGE,
    "1. Material Source Declaration Form ( MSDF ).pdf":              DocType.MSDF,
    "2. Copies of Relevant Parts of Specs..pdf":                     DocType.SPECIFICATION_COPY,
    "2.1 BOQ.pdf":                                                   DocType.BOQ,
    "2.2 Drawings.pdf":                                              DocType.DRAWING,
    "3. Technical Comparison.pdf":                                   DocType.COMPARISON_TABLE,
    "4. Manufacturer's Technical Data Original Catalogues.pdf":      DocType.TECHNICAL_DATASHEET,
    "5. Recent Test Reports Certificates.pdf":                       DocType.TEST_REPORT,
    "6. Department of Economic Development  ( Registration ).pdf":   DocType.DED_REGISTRATION,
    "7. Manuafcturer Suppliers Guarantee ( as per Contract ).pdf":   DocType.MANUFACTURER_GUARANTEE,
    "8. Previous Approvals.pdf":                                     DocType.PREVIOUS_APPROVAL,
    "9. Applicator's Method Statement.pdf":                          DocType.METHOD_STATEMENT,
    "10. Others.pdf":                                                DocType.OTHERS,
}


# ── Pydantic Output Model ──────────────────────────────────────────────────────

class ClassificationResult(BaseModel):
    doc_type: DocType
    confidence: str          # "high" | "medium" | "low"
    reasoning: str
    key_indicators: list[str]


# ── PDF Text Extraction ────────────────────────────────────────────────────────

def _ocr_page(page) -> str:
    """Render a PDF page to image and extract text via Tesseract."""
    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)


def extract_text(pdf_path: Path, max_pages: int = 2) -> str:
    """Extract text from PDF, falling back to Tesseract OCR for scanned pages."""
    doc = fitz.open(str(pdf_path))
    pages = min(max_pages, doc.page_count)
    text_parts = []
    for i in range(pages):
        page = doc[i]
        native = page.get_text().strip()
        if native:
            text_parts.append(native)
        else:
            ocr = _ocr_page(page).strip()
            if ocr:
                text_parts.append(ocr)
    doc.close()
    return "\n".join(text_parts)[:MAX_TEXT_CHARS].strip()


# ── Separator Page Detection (Rule-Based, No LLM) ─────────────────────────────

_SEPARATOR_PATTERNS = [
    r"\bboq\b",
    r"\bbill of quantities\b",
    r"\bdrawing[s]?\b",
    r"\bspecification[s]?\b",
    r"\btechnical comparison\b",
    r"\bcomparison table\b",
    r"\bmanufacturer.{0,10}technical data\b",
    r"\bcatalogue[s]?\b",
    r"\btest report[s]?\b",
    r"\bcertificate[s]?\b",
    r"\bdepartment of economic development\b",
    r"\bded\b",
    r"\bguarantee\b",
    r"\bprevious approval[s]?\b",
    r"\bmethod statement\b",
    r"\bmsdf\b",
    r"\bmaf\b",
    r"\bcover page\b",
    r"\bindex\s*\d+\b",
    r"\btab\s*\d+\b",
    r"\bsection\s+\d+\b",
    r"\bappendix\b",
    r"\bothers\b",
]


def is_separator_page(text: str) -> bool:
    """Return True if the page looks like an index/section title page."""
    if len(text.split()) > SEPARATOR_MAX_WORDS:
        return False
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in _SEPARATOR_PATTERNS)


# ── Classification Prompt ──────────────────────────────────────────────────────

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


def classify_document(text: str, declared_label: str | None = None) -> ClassificationResult:
    """
    Classify a document from its extracted text.
    If declared_label is provided, the prompt includes the section the document
    was submitted under — used for mismatch detection testing.
    """
    user_msg = f"Classify this document:\n\n{text}"
    if declared_label:
        user_msg += f"\n\n[Submitted under section: '{declared_label}']"

    response = _openai().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = response.choices[0].message.content
    try:
        return ClassificationResult.model_validate_json(raw)
    except (ValidationError, ValueError):
        parsed = json.loads(raw)
        return ClassificationResult(
            doc_type=parsed.get("doc_type", DocType.OTHERS),
            confidence=parsed.get("confidence", "low"),
            reasoning=parsed.get("reasoning", "parse fallback"),
            key_indicators=parsed.get("key_indicators", []),
        )


# ── Scenario 1: Clean Classification ──────────────────────────────────────────

def run_clean_classification() -> dict:
    print("\n=== Scenario 1: Clean Classification (26 labeled documents) ===")
    results = []
    correct = 0
    total = 0

    for submittal in ("submittal_02", "submittal_03"):
        print(f"\n  -- {submittal} --")
        for pdf_path in sorted((DATA_DIR / submittal).glob("*.pdf")):
            filename = pdf_path.name
            ground_truth = FILENAME_TO_DOCTYPE.get(filename)
            if ground_truth is None:
                continue

            text = extract_text(pdf_path)
            if not text:
                print(f"  [SKIP] {filename} — no extractable text (likely scanned)")
                continue

            result = classify_document(text)
            is_correct = result.doc_type == ground_truth
            correct += is_correct
            total += 1

            mark = "✓" if is_correct else "✗"
            print(f"  [{mark}] {filename}")
            if not is_correct:
                print(f"         Expected : {ground_truth.value}")
                print(f"         Got      : {result.doc_type.value}  [{result.confidence}]")
                print(f"         Reason   : {result.reasoning}")

            results.append({
                "submittal": submittal,
                "filename": filename,
                "ground_truth": ground_truth.value,
                "predicted": result.doc_type.value,
                "confidence": result.confidence,
                "correct": is_correct,
                "reasoning": result.reasoning,
                "key_indicators": result.key_indicators,
            })

    accuracy = correct / total if total else 0.0
    print(f"\n  Result: {correct}/{total} correct — {accuracy:.1%} accuracy")
    return {
        "scenario": "clean_classification",
        "accuracy": round(accuracy, 4),
        "correct": correct,
        "total": total,
        "results": results,
    }


# ── Scenario 2: Separator Page Detection ──────────────────────────────────────

def run_separator_detection() -> dict:
    print("\n=== Scenario 2: Separator Page Detection (Combined PDF) ===")
    pdf_files = list((DATA_DIR / "submittal_01").glob("*.pdf"))
    if not pdf_files:
        print("  No combined PDF found — skipping.")
        return {"scenario": "separator_detection", "skipped": True}

    pdf_path = pdf_files[0]
    doc = fitz.open(str(pdf_path))
    total_pages = doc.page_count  # save before close
    separators = []

    print(f"  Scanning {total_pages} pages: {pdf_path.name}")
    print(f"  (OCR used on pages with no native text)\n")

    for i in range(total_pages):
        page = doc[i]
        text = page.get_text().strip()
        if not text:
            text = _ocr_page(page).strip()
        word_count = len(text.split())
        if is_separator_page(text):
            preview = text[:80].replace("\n", " ")
            separators.append({
                "page": i + 1,
                "word_count": word_count,
                "text": preview,
            })
            print(f"  [SEP] Page {i+1:2d}  ({word_count:2d} words)  {preview!r}")

    doc.close()
    content_pages = total_pages - len(separators)
    print(f"\n  Separator pages : {len(separators)}")
    print(f"  Content pages   : {content_pages}")
    print(f"  Total           : {total_pages}")

    return {
        "scenario": "separator_detection",
        "pdf": pdf_path.name,
        "total_pages": total_pages,
        "separator_count": len(separators),
        "content_pages": content_pages,
        "separators": separators,
    }


# ── Scenario 3: Mismatch Detection ────────────────────────────────────────────

_MISMATCH_CASES = [
    {
        "pdf": "submittal_02/2.1_BOQ.pdf",
        "declared_label": "Copies of Relevant Specifications",
        "expected_type": DocType.BOQ,
        "description": "BOQ placed in the Specification section",
    },
    {
        "pdf": "submittal_02/5_Recent Test Reports - Certificates.pdf",
        "declared_label": "Manufacturer's Technical Data",
        "expected_type": DocType.TEST_REPORT,
        "description": "Test report placed in Technical Datasheet section",
    },
    {
        "pdf": "submittal_02/6_Departmet of Economic Development (Registration).pdf",
        "declared_label": "Manufacturer Guarantee",
        "expected_type": DocType.DED_REGISTRATION,
        "description": "DED registration placed in Guarantee section",
    },
    {
        "pdf": "submittal_02/9_Applicators Method Statement.pdf",
        "declared_label": "Previous Approvals",
        "expected_type": DocType.METHOD_STATEMENT,
        "description": "Method statement placed in Previous Approvals section",
    },
]

_DECLARED_LABEL_TO_DOCTYPE: dict[str, str] = {
    "Copies of Relevant Specifications": "specification_copy",
    "Manufacturer's Technical Data":     "technical_datasheet",
    "Manufacturer Guarantee":            "manufacturer_guarantee",
    "Previous Approvals":                "previous_approval",
}


def run_mismatch_detection() -> dict:
    print("\n=== Scenario 3: Mismatch Detection (4 deliberately misplaced documents) ===\n")
    results = []
    correct = 0

    for case in _MISMATCH_CASES:
        pdf_path = DATA_DIR / case["pdf"]
        text = extract_text(pdf_path)
        result = classify_document(text, declared_label=case["declared_label"])

        actual_correct = result.doc_type == case["expected_type"]
        expected_from_label = _DECLARED_LABEL_TO_DOCTYPE.get(case["declared_label"], "unknown")
        mismatch_flagged = result.doc_type.value != expected_from_label
        correct += actual_correct

        mark = "✓" if actual_correct else "✗"
        print(f"  [{mark}] {case['description']}")
        print(f"         Declared section : '{case['declared_label']}'")
        print(f"         Detected type    : {result.doc_type.value}  [{result.confidence}]")
        if not actual_correct:
            print(f"         Expected         : {case['expected_type'].value}")
        if mismatch_flagged:
            print(f"         → FINDING: Wrong document in '{case['declared_label']}' section")
        print()

        results.append({
            "description": case["description"],
            "declared_label": case["declared_label"],
            "expected_type": case["expected_type"].value,
            "detected_type": result.doc_type.value,
            "confidence": result.confidence,
            "correct": actual_correct,
            "mismatch_flagged": mismatch_flagged,
            "reasoning": result.reasoning,
            "key_indicators": result.key_indicators,
        })

    accuracy = correct / len(_MISMATCH_CASES)
    print(f"  Result: {correct}/{len(_MISMATCH_CASES)} correct — {accuracy:.1%} accuracy")
    return {
        "scenario": "mismatch_detection",
        "accuracy": round(accuracy, 4),
        "correct": correct,
        "total": len(_MISMATCH_CASES),
        "results": results,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Phase 2 — Experiment A: Document Classifier Test")
    print(f"Model : {MODEL}")
    print("=" * 60)

    s1 = run_clean_classification()
    s2 = run_separator_detection()
    s3 = run_mismatch_detection()

    summary = {
        "model": MODEL,
        "clean_classification_accuracy": s1.get("accuracy"),
        "mismatch_detection_accuracy": s3.get("accuracy"),
        "separator_pages_found": len(s2.get("separators", [])),
        "scenarios": [s1, s2, s3],
    }

    output_path = RESULTS_DIR / "classifier_results.json"
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Clean classification : {s1.get('accuracy', 0):.1%}  ({s1.get('correct')}/{s1.get('total')})")
    print(f"  Mismatch detection   : {s3.get('accuracy', 0):.1%}  ({s3.get('correct')}/{s3.get('total')})")
    print(f"  Separator pages      : {len(s2.get('separators', []))}")
    print(f"\n  Full results → {output_path}")
