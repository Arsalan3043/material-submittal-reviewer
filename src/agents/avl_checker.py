from __future__ import annotations

from langsmith import traceable
from rapidfuzz import fuzz

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, Severity
from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.pdf_parser import extract_text_from_bytes

_FUZZY_THRESHOLD = 85


def _search_avl_text(manufacturer_name: str, avl_text: str) -> bool:
    """Return True if manufacturer name appears in AVL text (fuzzy match)."""
    lines = [line.strip() for line in avl_text.splitlines() if line.strip()]
    for line in lines:
        if fuzz.token_sort_ratio(manufacturer_name.lower(), line.lower()) >= _FUZZY_THRESHOLD:
            return True
    return False


@traceable(name="avl_checker_agent")
def avl_checker_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 6 — AVL Checker (TAQA only, no-op for ADM).

    Checks whether the manufacturer is listed on the Approved Vendor List
    uploaded as part of the submittal package. Records the finding and
    continues — the review is never stopped by AVL status.
    """
    authority: str = state.get("authority", "ADM")
    findings: list[dict] = []

    if authority != "TAQA":
        return {**state, "avl_findings": findings}

    manufacturer_name: str = state.get("manufacturer_name", "")
    classified: dict[str, dict] = state.get("classified_documents", {})
    file_contents: dict[str, bytes] = state.get("file_contents", {})

    if not manufacturer_name:
        findings.append(Finding(
            stage="avl_check",
            document="avl",
            description="Manufacturer name not available from cover page. AVL check skipped.",
            severity=Severity.WARNING,
            action_required="Ensure manufacturer name is on the cover page.",
        ).model_dump())
        return {**state, "avl_findings": findings}

    # Find the AVL document in the submittal (classified as OTHERS or PREVIOUS_APPROVAL)
    avl_text = ""
    avl_filename = ""
    for filename, doc_dict in classified.items():
        doc = ClassifiedDocument.model_validate(doc_dict)
        # AVL may be uploaded under PREVIOUS_APPROVAL or OTHERS section
        if doc.doc_type in (DocType.PREVIOUS_APPROVAL, DocType.OTHERS):
            content = file_contents.get(filename, b"")
            if content:
                text = extract_text_from_bytes(content, max_pages=20)
                if "approved vendor" in text.lower() or "vendor list" in text.lower():
                    avl_text = text
                    avl_filename = filename
                    break

    if not avl_text:
        findings.append(Finding(
            stage="avl_check",
            document="avl",
            description="Approved Vendor List (AVL) document not found in submittal package.",
            severity=Severity.CRITICAL,
            action_required="Include the TAQA Approved Vendor List as part of the submittal.",
        ).model_dump())
        return {**state, "avl_findings": findings}

    found = _search_avl_text(manufacturer_name, avl_text)

    if found:
        findings.append(Finding(
            stage="avl_check",
            document=avl_filename,
            description=f"Manufacturer '{manufacturer_name}' is listed on the TAQA Approved Vendor List.",
            severity=Severity.PASS,
            action_required="No action required.",
        ).model_dump())
    else:
        findings.append(Finding(
            stage="avl_check",
            document=avl_filename,
            description=f"Manufacturer '{manufacturer_name}' was NOT found on the TAQA Approved Vendor List.",
            severity=Severity.CRITICAL,
            action_required="Verify manufacturer name spelling. If correct, manufacturer must be added to AVL before approval.",
        ).model_dump())

    return {**state, "avl_findings": findings}
