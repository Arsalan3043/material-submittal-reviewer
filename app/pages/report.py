from __future__ import annotations

import streamlit as st

from src.models.findings import Finding, ReviewReport, Severity, TableRowFinding


# ── Helpers ───────────────────────────────────────────────────────────────────

def _render_finding(f: Finding) -> None:
    body = f"**{f.document}**\n\n{f.description}\n\n*Action required: {f.action_required}*"
    if f.severity == Severity.CRITICAL:
        st.error(body)
    elif f.severity == Severity.WARNING:
        st.warning(body)
    else:
        st.success(body)


def _stage_expander(title: str, findings: list[Finding]) -> None:
    n        = len(findings)
    critical = sum(1 for f in findings if f.severity == Severity.CRITICAL)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)

    tag = ""
    if critical:
        tag = f" — {critical} critical"
    elif warnings:
        tag = f" — {warnings} warning(s)"
    elif n:
        tag = f" — {n} finding(s)"

    # Auto-expand stages that have at least one critical issue.
    with st.expander(f"{title}{tag}", expanded=critical > 0):
        if not findings:
            st.caption("No issues found.")
            return
        for f in findings:
            _render_finding(f)


def _render_table_audit(rows: list[TableRowFinding]) -> None:
    with st.expander(
        f"Stage 8  —  Comparison Table Audit — {len(rows)} row(s)",
        expanded=any(r.severity == Severity.CRITICAL for r in rows),
    ):
        if not rows:
            st.caption("No comparison table rows found.")
            return

        # Summary bar across all rows
        crit = sum(1 for r in rows if r.severity == Severity.CRITICAL)
        warn = sum(1 for r in rows if r.severity == Severity.WARNING)
        ok   = len(rows) - crit - warn
        st.caption(f"{ok} pass  |  {warn} warning  |  {crit} critical")
        st.divider()

        for r in rows:
            header = f"**{r.parameter}**  — {r.severity.value.upper()}"
            if r.severity == Severity.CRITICAL:
                st.error(header)
            elif r.severity == Severity.WARNING:
                st.warning(header)
            else:
                st.success(header)

            col1, col2, col3, col4 = st.columns(4)
            col1.markdown(f"**Specified**\n\n{r.specified_value or '—'}")
            col2.markdown(f"**Proposed**\n\n{r.proposed_value or '—'}")
            col3.markdown(f"**Deviation**\n\n{r.deviation_declared or '—'}")
            col4.markdown(f"**Measured**\n\n{r.measured_value or '—'}")

            checks = []
            if not r.specified_correct:   checks.append("Specified value incorrect")
            if not r.proposed_verified:   checks.append("Proposed value not verified against datasheet")
            if not r.measured_verified:   checks.append("Measured value not verified against test report")
            if not r.deviation_accurate:  checks.append("Deviation declaration inaccurate")
            if r.missing_from_spec:       checks.append("Parameter missing from submitted spec")

            if checks:
                st.caption("Issues: " + "  |  ".join(checks))
            if r.finding:
                st.caption(f"Finding: {r.finding}")
            st.divider()


def _build_plain_text(report: ReviewReport) -> str:
    """Build a copy-pasteable plain-text version of the full report."""
    lines: list[str] = []

    def h(title: str) -> None:
        lines.append(f"\n{title}")
        lines.append("-" * len(title))

    lines.append("MATERIAL SUBMITTAL REVIEW REPORT")
    lines.append("=" * 40)
    lines.append(f"Submittal ID : {report.submittal_id}")
    lines.append(f"Authority    : {report.authority}")
    lines.append(f"Material     : {report.material_description}")
    lines.append(f"Spec Clause  : {report.spec_clause}")
    lines.append(f"Review Date  : {report.review_date}")
    lines.append(f"\nOVERALL RECOMMENDATION: {report.overall_recommendation}")
    lines.append(f"Critical Findings : {report.critical_count}")
    lines.append(f"Warnings          : {report.warning_count}")
    lines.append(f"Missing Documents : {len(report.missing_documents)}")

    h("SUMMARY")
    lines.append(report.summary_comments)

    if report.missing_documents:
        h("MISSING DOCUMENTS")
        for d in report.missing_documents:
            lines.append(f"  - {d}")

    def findings_block(title: str, findings: list[Finding]) -> None:
        h(title)
        if not findings:
            lines.append("  No issues found.")
            return
        for f in findings:
            lines.append(f"  [{f.severity.value.upper()}] {f.document}")
            lines.append(f"  {f.description}")
            lines.append(f"  Action: {f.action_required}")
            lines.append("")

    findings_block("STAGE 1  —  COMPLETENESS CHECK",          report.completeness_findings)
    findings_block("STAGE 2  —  BOQ & DRAWING CHECK",         report.boq_drawing_findings)
    findings_block("STAGE 3  —  SPEC VERIFICATION",           report.spec_verification_findings)
    findings_block("STAGE 4  —  VALIDITY & DATE CHECKS",      report.validity_findings)
    findings_block("STAGE 5  —  AVL CHECK",                   report.avl_findings)
    findings_block("STAGE 6  —  COMPLIANCE STATEMENT AUDIT",  report.statement_findings)
    findings_block("STAGE 7  —  CONSISTENCY CHECK",           report.consistency_findings)
    findings_block("STAGE 8  —  OTHERS",                      report.others_findings)

    h("STAGE 9  —  COMPARISON TABLE AUDIT")
    if not report.table_audit_findings:
        lines.append("  No comparison table rows found.")
    else:
        for r in report.table_audit_findings:
            lines.append(f"  [{r.severity.value.upper()}] {r.parameter}")
            lines.append(f"    Specified : {r.specified_value}")
            lines.append(f"    Proposed  : {r.proposed_value}")
            lines.append(f"    Deviation : {r.deviation_declared}")
            lines.append(f"    Measured  : {r.measured_value}")
            lines.append(f"    Finding   : {r.finding}")
            lines.append("")

    return "\n".join(lines)


# ── Main render ───────────────────────────────────────────────────────────────

def render() -> None:
    st.title("Review Report")

    # ── Guard ─────────────────────────────────────────────────────────────────
    report_dict = st.session_state.get("report")
    if not report_dict:
        st.warning("No report available. Complete a review first.")
        if st.button("Go to Upload"):
            st.session_state.page = "upload"
            st.rerun()
        return

    report = ReviewReport.model_validate(report_dict)

    # ── Header ────────────────────────────────────────────────────────────────
    col_meta, col_rec = st.columns([3, 1])
    with col_meta:
        st.markdown(f"**Material:** {report.material_description or '—'}")
        st.markdown(f"**Spec Clause:** {report.spec_clause or '—'}")
        st.caption(
            f"Authority: {report.authority}  |  "
            f"Submittal: {report.submittal_id[:8]}...  |  "
            f"Date: {report.review_date}"
        )
    with col_rec:
        rec = report.overall_recommendation
        if rec == "APPROVE":
            st.success(f"**{rec}**")
        elif rec == "CONDITIONAL":
            st.warning(f"**{rec}**")
        else:
            st.error(f"**{rec}**")

    # ── Counts ────────────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("Critical Findings", report.critical_count)
    c2.metric("Warnings",          report.warning_count)
    c3.metric("Missing Documents", len(report.missing_documents))

    # ── Summary ───────────────────────────────────────────────────────────────
    if report.summary_comments:
        st.info(report.summary_comments)

    # ── Missing documents ─────────────────────────────────────────────────────
    if report.missing_documents:
        with st.expander("Missing Documents", expanded=True):
            for d in report.missing_documents:
                st.warning(d)

    st.divider()

    # ── Per-stage findings ────────────────────────────────────────────────────
    _stage_expander("Stage 1  —  Completeness Check",         report.completeness_findings)
    _stage_expander("Stage 2  —  BOQ & Drawing Check",        report.boq_drawing_findings)
    _stage_expander("Stage 3  —  Spec Verification",          report.spec_verification_findings)
    _stage_expander("Stage 4  —  Validity & Date Checks",     report.validity_findings)
    _stage_expander("Stage 5  —  AVL Check",                  report.avl_findings)
    _stage_expander("Stage 6  —  Compliance Statement Audit", report.statement_findings)
    _render_table_audit(report.table_audit_findings)
    _stage_expander("Stage 9  —  Consistency Check",          report.consistency_findings)
    _stage_expander("Others  —  Additional Documents",        report.others_findings)

    st.divider()

    # ── Plain-text copy block ─────────────────────────────────────────────────
    st.subheader("Copy Report")
    st.caption("Select all and copy to paste into any document or email.")
    st.text_area(
        label="plain_text",
        value=_build_plain_text(report),
        height=400,
        label_visibility="collapsed",
    )

    # ── Query mode link ───────────────────────────────────────────────────────
    st.divider()
    if st.button("Ask Questions About This Review", use_container_width=True):
        st.session_state.page = "chat"
        st.rerun()
