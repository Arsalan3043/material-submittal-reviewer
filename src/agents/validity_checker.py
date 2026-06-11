from __future__ import annotations

from langsmith import traceable

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, Severity
from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.pdf_parser import extract_text_from_bytes
from src.rules.date_checker import (
    check_ded_registration,
    check_guarantee,
    check_test_report,
)


@traceable(name="validity_checker_agent")
def validity_checker_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 3 — Validity Checker.

    Pure rule-based date/expiry checks. No AI involved.
    Checks: DED registration, test reports, guarantee period.
    All findings are recorded; review always continues regardless of outcome.
    """
    classified: dict[str, dict] = state.get("classified_documents", {})
    file_contents: dict[str, bytes] = state.get("file_contents", {})

    findings: list[dict] = []

    for filename, doc_dict in classified.items():
        doc = ClassifiedDocument.model_validate(doc_dict)
        content = file_contents.get(filename, b"")
        if not content:
            continue

        text = extract_text_from_bytes(content, max_pages=3)

        if doc.doc_type == DocType.DED_REGISTRATION:
            result = check_ded_registration(text, filename)
            findings.append(result.model_dump())

        elif doc.doc_type == DocType.TEST_REPORT:
            result = check_test_report(text, filename)
            findings.append(result.model_dump())

        elif doc.doc_type == DocType.MANUFACTURER_GUARANTEE:
            result = check_guarantee(text, filename)
            findings.append(result.model_dump())

    if not findings:
        findings.append(Finding(
            stage="validity_checks",
            document="all",
            description="No dated documents found to verify (DED, test reports, guarantee).",
            severity=Severity.WARNING,
            action_required="Ensure all required dated documents are included in the submittal.",
        ).model_dump())

    return {**state, "validity_findings": findings}
