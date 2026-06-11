from __future__ import annotations

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, ReviewReport, TableRowFinding


def generate_report_from_state(state: SubmittalReviewState) -> ReviewReport:
    """
    Reconstruct a ReviewReport from state after the LangGraph pipeline completes.
    Use this when you need a typed ReviewReport object from the raw state dict
    (e.g. in the Streamlit UI or PDF generator).
    """
    raw_report = state.get("report")
    if raw_report:
        return ReviewReport.model_validate(raw_report)

    # Fallback: build directly from per-stage findings in state (should not be needed
    # if report_compiler_node ran successfully, but guards against partial runs)
    def _findings(key: str) -> list[Finding]:
        return [Finding.model_validate(d) for d in state.get(key, [])]

    def _table_findings() -> list[TableRowFinding]:
        return [TableRowFinding.model_validate(d) for d in state.get("table_audit_findings", [])]

    from datetime import date
    return ReviewReport(
        submittal_id=state.get("submittal_id", ""),
        authority=state.get("authority", "ADM"),
        material_description=state.get("material_description", ""),
        spec_clause=state.get("spec_clause", ""),
        review_date=date.today().isoformat(),
        completeness_findings=_findings("completeness_findings"),
        boq_drawing_findings=_findings("boq_drawing_findings"),
        spec_verification_findings=_findings("spec_verification_findings"),
        validity_findings=_findings("validity_findings"),
        avl_findings=_findings("avl_findings"),
        statement_findings=_findings("statement_findings"),
        table_audit_findings=_table_findings(),
        consistency_findings=_findings("consistency_findings"),
        others_findings=_findings("others_findings"),
        missing_documents=state.get("missing_documents", []),
        overall_recommendation="RESUBMIT",
        summary_comments="Review did not complete successfully. Check agent logs.",
    )
