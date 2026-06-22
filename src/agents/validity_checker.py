from __future__ import annotations

from datetime import date
from langsmith import traceable

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, Severity
from src.models.knowledge_store import load_store
from src.models.submittal import DocType
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
    store = load_store(state["knowledge_store_id"])

    raw_date = state.get("review_date")
    today: date | None = date.fromisoformat(raw_date) if raw_date else None

    findings: list[dict] = []

    for section in store.sections:
        if section.doc_type == DocType.DED_REGISTRATION:
            findings.extend(f.model_dump() for f in check_ded_registration(section.text, section.filename, today=today))

        elif section.doc_type == DocType.TEST_REPORT:
            findings.extend(f.model_dump() for f in check_test_report(section.text, section.filename, today=today))

        elif section.doc_type == DocType.MANUFACTURER_GUARANTEE:
            findings.extend(f.model_dump() for f in check_guarantee(section.text, section.filename))

    if not findings:
        findings.append(Finding(
            stage="validity_checks",
            document="all",
            description="No dated documents found to verify (DED, test reports, guarantee).",
            severity=Severity.WARNING,
            action_required="Ensure all required dated documents are included in the submittal.",
        ).model_dump())

    return {**state, "validity_findings": findings}
