from __future__ import annotations

import json
from datetime import date

from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, ReviewReport, Severity, TableRowFinding
from src.models.knowledge_store import load_store
from src.models.requirements import RequirementVerificationArtifact, VerificationStatus

_MODEL = "gpt-4o-mini"

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = wrap_openai(OpenAI())
    return _client


_SUMMARY_SYSTEM_PROMPT = """You are writing the summary comments section of a UAE construction material submittal review report.

Write 2-4 concise professional sentences summarising the review outcome.
Mention: overall recommendation, the most significant issue(s) found (if any), and what the contractor must do next.
If requirement-level compliance data is provided, use it to be specific about which requirements failed.
Tone: formal technical English, objective, clear.

Return JSON only: {"summary_comments": "..."}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _load_verification_artifact(state: SubmittalReviewState) -> RequirementVerificationArtifact | None:
    raw = state.get("verification_artifact")
    if not raw:
        return None
    try:
        return RequirementVerificationArtifact.model_validate(raw)
    except Exception:
        return None


def _build_requirement_digest(artifact: RequirementVerificationArtifact | None) -> str:
    """
    Build a concise requirement-level compliance summary for the LLM summary call.
    Each requirement shows its status so the LLM can write specific, grounded comments.
    """
    if not artifact or not artifact.verifications:
        return ""

    _ICON = {
        VerificationStatus.SATISFIED:          "PASS",
        VerificationStatus.NON_COMPLIANT:      "FAIL",
        VerificationStatus.PARTIALLY_VERIFIED: "PARTIAL",
        VerificationStatus.MISSING_EVIDENCE:   "MISSING",
        VerificationStatus.NOT_APPLICABLE:     "N/A",
    }

    lines = ["Requirement-level compliance:"]
    for v in artifact.verifications:
        icon = _ICON.get(v.status, v.status.value.upper())
        lines.append(f"  [{icon}] {v.requirement_summary}")
        if v.status in (VerificationStatus.NON_COMPLIANT, VerificationStatus.MISSING_EVIDENCE):
            lines.append(f"         → {v.reasoning}")

    return "\n".join(lines)


def _determine_recommendation(
    critical_count: int,
    warning_count: int,
    artifact: RequirementVerificationArtifact | None,
) -> str:
    """
    Determine overall recommendation using both Finding severity counts and
    structured requirement-level compliance results.

    Priority (highest to lowest):
    1. Any critical Finding → RESUBMIT
    2. Any NON_COMPLIANT requirement → RESUBMIT (regardless of warning count)
    3. Any MISSING_EVIDENCE on a requirement + existing warnings → RESUBMIT
    4. Only MISSING_EVIDENCE (no other criticals) → CONDITIONAL
    5. >2 warnings → CONDITIONAL
    6. Otherwise → APPROVE
    """
    if critical_count > 0:
        return "RESUBMIT"

    if artifact:
        if artifact.non_compliant_count > 0:
            return "RESUBMIT"
        if artifact.missing_evidence_count > 0:
            return "RESUBMIT" if warning_count > 0 else "CONDITIONAL"

    if warning_count > 2:
        return "CONDITIONAL"

    return "APPROVE"


# ── Main node ──────────────────────────────────────────────────────────────────

@traceable(name="report_compiler_agent")
def report_compiler_node(state: SubmittalReviewState) -> dict:
    """
    Agent 7 — Report Compiler.

    Gathers all stage findings, uses RequirementVerificationArtifact for
    precise recommendation logic and a richer summary digest, then builds
    the final ReviewReport.

    RequirementVerificationArtifact is written to state by spec_verifier and
    read here — it gives the compiler requirement-level compliance status
    (SATISFIED / NON_COMPLIANT / MISSING_EVIDENCE) rather than just raw
    Finding severity counts.
    """
    store = load_store(state["knowledge_store_id"])

    completeness = _to_findings(state.get("completeness_findings", []))
    boq_drawing  = _to_findings(state.get("boq_drawing_findings", []))
    spec_verif   = _to_findings(state.get("spec_verification_findings", []))
    validity     = _to_findings(state.get("validity_findings", []))
    avl          = _to_findings(state.get("avl_findings", []))
    statement    = _to_findings(state.get("statement_findings", []))
    table_audit  = _to_table_findings(state.get("table_audit_findings", []))
    consistency  = _to_findings(state.get("consistency_findings", []))
    others       = _to_findings(state.get("others_findings", []))

    all_standard = (
        completeness + boq_drawing + spec_verif + validity
        + avl + statement + consistency + others
    )
    critical_count = (
        sum(1 for f in all_standard if f.severity == Severity.CRITICAL)
        + sum(1 for f in table_audit if f.severity == Severity.CRITICAL)
    )
    warning_count = (
        sum(1 for f in all_standard if f.severity == Severity.WARNING)
        + sum(1 for f in table_audit if f.severity == Severity.WARNING)
    )

    # Load structured requirement compliance results from spec_verifier
    verification_artifact = _load_verification_artifact(state)

    recommendation = _determine_recommendation(
        critical_count, warning_count, verification_artifact
    )

    # Build digest for the LLM summary — include requirement-level context when available
    critical_descriptions = [
        f.description for f in all_standard if f.severity == Severity.CRITICAL
    ][:5]
    critical_descriptions += [
        f.finding for f in table_audit if f.severity == Severity.CRITICAL
    ][:3]

    req_digest = _build_requirement_digest(verification_artifact)

    digest = (
        f"Authority: {state.get('authority', 'ADM')}\n"
        f"Material: {store.material_description or 'unknown'}\n"
        f"Clause: {store.spec_clause or 'unknown'}\n"
        f"Recommendation: {recommendation}\n"
        f"Critical issues ({critical_count}): {'; '.join(critical_descriptions) or 'None'}\n"
        f"Warnings: {warning_count}\n"
        f"Missing documents: {', '.join(state.get('missing_documents', [])) or 'None'}\n"
        + (f"\n{req_digest}" if req_digest else "")
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
        material_description=store.material_description,
        spec_clause=store.spec_clause,
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
