from __future__ import annotations

import json

from langsmith import traceable
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.findings import Severity, TableRowFinding
from src.models.knowledge_store import load_store
from src.models.submittal import DocType
from src.parsers.table_extractor import TableRow
from src.rag.query.context_assembler import EMPTY_CONTEXT_SENTINEL, assemble_spec_context

_MODEL = "gpt-4o-mini"

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


_BATCH_AUDIT_SYSTEM_PROMPT = """You are auditing ALL rows of a UAE construction material submittal comparison table in one pass.

Shared context (applies to every row):
- AUTHORITY SPEC: what the specification requires
- DATASHEET: manufacturer's technical data for the proposed product
- TEST REPORT: lab test results for the proposed product

For EACH row return one audit object. The rows array in your response must be the same length
and in the same order as the rows array in the request.

DEVIATION RULE (critical):
- A deviation only exists when the proposed value is BELOW the specified minimum,
  ABOVE the specified maximum, or categorically wrong (wrong product type / standard).
- A proposed value that EXCEEDS a minimum is compliant — NOT a deviation.
- Example: specified "min 12% water reduction", proposed "15%" → deviation_accurate=true, severity=pass.
- Flag deviation_accurate=false only when a deviation was declared but is factually wrong,
  OR when no deviation was declared but the proposed value genuinely fails the spec.

Field definitions:
- specified_correct  : Is the specified value consistent with the authority spec context?
- proposed_verified  : Can the proposed value be confirmed from the datasheet?
- measured_verified  : Can the measured value be confirmed from the test report?
                       If measured is empty → set true (not tested is acceptable).
- deviation_accurate : Is the deviation cell accurate?
                       Empty deviation on a compliant row = accurate (true).
- missing_from_spec  : Is this parameter not mentioned in the spec context at all?
- finding            : One-sentence summary for this row.
- severity           : "pass" | "warning" | "critical"

If spec context is unavailable, set specified_correct=true (benefit of doubt).

Return JSON only — no markdown, no extra keys:
{
  "rows": [
    {
      "specified_correct": true,
      "proposed_verified": false,
      "measured_verified": true,
      "deviation_accurate": true,
      "missing_from_spec": false,
      "finding": "...",
      "severity": "pass"
    }
  ]
}"""


def _build_rows_block(rows: list[TableRow]) -> str:
    lines = []
    for i, r in enumerate(rows):
        lines.append(
            f"Row {i}: parameter={r.parameter!r} | specified={r.specified!r} | "
            f"proposed={r.proposed!r} | deviation={r.deviation!r} | "
            f"measured={r.measured!r} | remarks={r.remarks!r}"
        )
    return "\n".join(lines)


@traceable(name="table_auditor_agent_batch")
def _audit_all_rows(
    rows: list[TableRow],
    spec_context: str,
    datasheet_context: str,
    test_context: str,
) -> list[TableRowFinding]:
    """Single LLM call that audits every table row at once."""
    user_msg = (
        f"AUTHORITY SPEC CONTEXT:\n{spec_context[:2000]}\n\n"
        f"DATASHEET CONTEXT:\n{datasheet_context[:1500]}\n\n"
        f"TEST REPORT CONTEXT:\n{test_context[:1500]}\n\n"
        f"ROWS TO AUDIT ({len(rows)} total):\n{_build_rows_block(rows)}"
    )

    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _BATCH_AUDIT_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        parsed_rows = json.loads(response.choices[0].message.content).get("rows", [])
    except Exception:
        parsed_rows = []

    severity_map = {"pass": Severity.PASS, "warning": Severity.WARNING, "critical": Severity.CRITICAL}
    results: list[TableRowFinding] = []

    for i, row in enumerate(rows):
        r = parsed_rows[i] if i < len(parsed_rows) else {}
        results.append(TableRowFinding(
            parameter=row.parameter,
            specified_value=row.specified,
            proposed_value=row.proposed,
            deviation_declared=row.deviation,
            measured_value=row.measured,
            specified_correct=r.get("specified_correct", True),
            proposed_verified=r.get("proposed_verified", False),
            measured_verified=r.get("measured_verified", True),
            deviation_accurate=r.get("deviation_accurate", True),
            missing_from_spec=r.get("missing_from_spec", False),
            finding=r.get("finding", "Audit result unavailable."),
            severity=severity_map.get(r.get("severity", "warning"), Severity.WARNING),
        ))

    return results


@traceable(name="table_auditor_agent")
def table_auditor_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 4 — Table Auditor (highest value agent).

    Table rows were pre-parsed by doc_processor (pdfplumber + LLM, done once).
    This node reads structured rows directly from the knowledge store — no PDF bytes.
    All rows are audited in a single batched LLM call.
    Shared context (spec, datasheet, test report) is retrieved once and reused.
    """
    store = load_store(state["knowledge_store_id"])
    authority: str = state.get("authority", "ADM")

    # Rows pre-parsed by doc_processor — reconstruct TableRow objects from stored dicts
    table_rows: list[TableRow] = [TableRow.model_validate(r) for r in store.table_rows]

    if not table_rows:
        return {**state, "table_audit_findings": []}

    # Retrieve supporting contexts once — shared across all rows
    spec_context = ""
    if store.spec_clause:
        raw = assemble_spec_context(clause_ref=store.spec_clause, authority=authority)
        spec_context = "" if raw == EMPTY_CONTEXT_SENTINEL else raw

    datasheet_text = store.get_text(DocType.TECHNICAL_DATASHEET)
    test_text = store.get_text(DocType.TEST_REPORT)

    # Single LLM call for all rows
    findings = _audit_all_rows(table_rows, spec_context, datasheet_text, test_text)

    return {**state, "table_audit_findings": [f.model_dump() for f in findings]}
