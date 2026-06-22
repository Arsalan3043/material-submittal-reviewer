from __future__ import annotations

from langsmith import traceable
from rapidfuzz import fuzz

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, Severity
from src.models.knowledge_store import load_store
from src.models.submittal import DocType

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

    store = load_store(state["knowledge_store_id"])
    manufacturer_name: str = store.manufacturer_name

    if not manufacturer_name:
        findings.append(Finding(
            stage="avl_check",
            document="avl",
            description="Manufacturer name not available from cover page. AVL check skipped.",
            severity=Severity.WARNING,
            action_required="Ensure manufacturer name is on the cover page.",
        ).model_dump())
        return {**state, "avl_findings": findings}

    # Find the AVL document — may be classified as PREVIOUS_APPROVAL or OTHERS
    avl_text = ""
    avl_filename = ""
    for section in store.sections:
        if section.doc_type in (DocType.PREVIOUS_APPROVAL, DocType.OTHERS):
            if "approved vendor" in section.text.lower() or "vendor list" in section.text.lower():
                avl_text = section.text
                avl_filename = section.filename
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
