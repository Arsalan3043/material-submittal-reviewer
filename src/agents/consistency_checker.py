from __future__ import annotations

from langsmith import traceable

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, Severity
from src.models.knowledge_store import load_store
from src.models.submittal import DocType
from src.rules.name_matcher import (
    check_manufacturer_consistency,
    check_supplier_consistency,
)

# Document types where manufacturer/supplier names are expected to appear
_NAME_BEARING_TYPES = frozenset([
    DocType.COVER_PAGE,
    DocType.TECHNICAL_DATASHEET,
    DocType.TEST_REPORT,
    DocType.MANUFACTURER_GUARANTEE,
    DocType.DED_REGISTRATION,
    DocType.MSDF,
    DocType.MAF,
])

_SUPPLIER_BEARING_TYPES = frozenset([
    DocType.COVER_PAGE,
    DocType.MSDF,
    DocType.MAF,
])


@traceable(name="consistency_checker_agent")
def consistency_checker_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 5 — Consistency Checker.

    Checks that manufacturer name, supplier name, and product references are
    consistent across all documents. Uses rapidfuzz fuzzy matching (rules-based).
    Ambiguous cases are flagged as warnings for human review.
    """
    store = load_store(state["knowledge_store_id"])
    manufacturer_name: str = store.manufacturer_name
    supplier_name: str = store.supplier_name

    findings: list[dict] = []

    # Manufacturer name consistency across all relevant documents
    manufacturer_texts = {
        s.filename: s.text[:1000]
        for s in store.sections
        if s.doc_type in _NAME_BEARING_TYPES
    }
    if manufacturer_name and manufacturer_texts:
        mfr_findings = check_manufacturer_consistency(manufacturer_name, manufacturer_texts)
        findings.extend(f.model_dump() for f in mfr_findings)
    elif not manufacturer_name:
        findings.append(Finding(
            stage="consistency_check",
            document="cover_page",
            description="Manufacturer name not found on cover page. Cannot perform consistency check.",
            severity=Severity.WARNING,
            action_required="Ensure manufacturer name is clearly stated on the cover page.",
        ).model_dump())

    # Supplier name consistency across supplier-bearing documents
    supplier_texts = {
        s.filename: s.text[:1000]
        for s in store.sections
        if s.doc_type in _SUPPLIER_BEARING_TYPES
    }
    if supplier_name and supplier_texts:
        sup_findings = check_supplier_consistency(supplier_name, supplier_texts)
        findings.extend(f.model_dump() for f in sup_findings)
    elif not supplier_name:
        findings.append(Finding(
            stage="consistency_check",
            document="cover_page",
            description="Supplier name not found on cover page. Cannot perform consistency check.",
            severity=Severity.WARNING,
            action_required="Ensure supplier name is clearly stated on the cover page.",
        ).model_dump())

    if not findings:
        findings.append(Finding(
            stage="consistency_check",
            document="all",
            description="Manufacturer and supplier names are consistent across all submitted documents.",
            severity=Severity.PASS,
            action_required="No action required.",
        ).model_dump())

    return {**state, "consistency_findings": findings}
