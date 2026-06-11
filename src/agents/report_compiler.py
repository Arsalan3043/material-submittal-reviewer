from __future__ import annotations

import json
from datetime import date

from langsmith import traceable
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, ReviewReport, Severity, TableRowFinding

_MODEL = "gpt-4o-mini"

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


_SUMMARY_SYSTEM_PROMPT = """You are writing the summary comments section of a UAE construction material submittal review report.

Write 2-4 concise professional sentences summarising the review outcome.
Mention: overall status, the most significant issue(s) found (if any), and what the contractor must do next.
Tone: formal technical English, objective, clear.

Return JSON only: {"summary_comments": "..."}"""


def _to_findings(raw: list[dict]) -> list[Finding]:
    out = []
    for d in raw:
        try:
            out.append(Finding.model_validate(d))
        except Exception:
            pass
    return out


def _to_table_findings(raw: list[dict]) -> list[TableRowFinding]:
    out = []
    for d in raw:
        try:
            out.append(TableRowFinding.model_validate(d))
        except Exception:
            pass
    return out


def _determine_recommendation(
    critical_count: int,
    warning_count: int,
) -> str:
    if critical_count > 0:
        return "RESUBMIT"
    if warning_count > 2:
        return "CONDITIONAL"
    return "APPROVE"


@traceable(name="report_compiler_agent")
def report_compiler_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 7 — Report Compiler.

    Gathers all findings from state, computes counts, determines the overall
    recommendation, generates professional summary comments, and builds the
    final ReviewReport.
    """
    completeness  = _to_findings(state.get("completeness_findings", []))
    boq_drawing   = _to_findings(state.get("boq_drawing_findings", []))
    spec_verif    = _to_findings(state.get("spec_verification_findings", []))
    validity      = _to_findings(state.get("validity_findings", []))
    avl           = _to_findings(state.get("avl_findings", []))
    statement     = _to_findings(state.get("statement_findings", []))
    table_audit   = _to_table_findings(state.get("table_audit_findings", []))
    consistency   = _to_findings(state.get("consistency_findings", []))
    others        = _to_findings(state.get("others_findings", []))

    all_findings = completeness + boq_drawing + spec_verif + validity + avl + statement + consistency + others
    critical_count = sum(1 for f in all_findings if f.severity == Severity.CRITICAL)
    critical_count += sum(1 for f in table_audit if f.severity == Severity.CRITICAL)
    warning_count = sum(1 for f in all_findings if f.severity == Severity.WARNING)
    warning_count += sum(1 for f in table_audit if f.severity == Severity.WARNING)

    recommendation = _determine_recommendation(critical_count, warning_count)

    # Build a text digest for the LLM summary
    critical_descriptions = [
        f.description for f in all_findings if f.severity == Severity.CRITICAL
    ][:5]
    critical_descriptions += [
        f.finding for f in table_audit if f.severity == Severity.CRITICAL
    ][:3]

    digest = (
        f"Authority: {state.get('authority', 'ADM')}\n"
        f"Material: {state.get('material_description', 'unknown')}\n"
        f"Clause: {state.get('spec_clause', 'unknown')}\n"
        f"Recommendation: {recommendation}\n"
        f"Critical issues ({critical_count}): {'; '.join(critical_descriptions) or 'None'}\n"
        f"Warnings: {warning_count}\n"
        f"Missing documents: {', '.join(state.get('missing_documents', [])) or 'None'}"
    )

    try:
        response = _openai().chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": digest},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        summary_comments = json.loads(response.choices[0].message.content).get(
            "summary_comments", "Review complete. See individual findings for details."
        )
    except Exception:
        summary_comments = "Review complete. See individual findings for details."

    report = ReviewReport(
        submittal_id=state.get("submittal_id", ""),
        authority=state.get("authority", "ADM"),
        material_description=state.get("material_description", ""),
        spec_clause=state.get("spec_clause", ""),
        review_date=date.today().isoformat(),
        completeness_findings=completeness,
        boq_drawing_findings=boq_drawing,
        spec_verification_findings=spec_verif,
        validity_findings=validity,
        avl_findings=avl,
        statement_findings=statement,
        table_audit_findings=table_audit,
        consistency_findings=consistency,
        others_findings=others,
        missing_documents=state.get("missing_documents", []),
        overall_recommendation=recommendation,
        summary_comments=summary_comments,
    )

    return {**state, "report": report.model_dump(), "review_complete": True}
