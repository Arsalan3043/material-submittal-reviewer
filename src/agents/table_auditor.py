from __future__ import annotations

import json

from langsmith import traceable
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.findings import Severity, TableRowFinding
from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.table_extractor import TableRow, extract_all_table_rows
from src.rag.query.context_assembler import EMPTY_CONTEXT_SENTINEL, assemble_spec_context

_MODEL = "gpt-4o-mini"

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


_ROW_AUDIT_SYSTEM_PROMPT = """You are auditing one row of a UAE construction material submittal comparison table.

Your task: verify each cell against evidence and return a structured audit result.

DEVIATION RULE (critical — read carefully):
- A deviation only exists when the proposed value is BELOW the specified minimum, ABOVE the specified maximum,
  or categorically different (e.g. wrong product type, wrong standard).
- A proposed value that EXCEEDS a minimum requirement is NOT a deviation — it is compliant.
- Example: specified "min 12% water reduction", proposed "15% water reduction" → NOT a deviation.
- Only flag deviation_accurate=false if a deviation was declared but is factually wrong,
  or if no deviation was declared but the proposed value genuinely fails the spec requirement.

Return JSON only:
{
  "specified_correct": true | false,
  "proposed_verified": true | false,
  "measured_verified": true | false,
  "deviation_accurate": true | false,
  "missing_from_spec": true | false,
  "finding": "one-sentence summary of the audit result for this row",
  "severity": "pass" | "warning" | "critical"
}

Rules:
- specified_correct: Is the specified value consistent with the authority spec context provided?
- proposed_verified: Can the proposed value be confirmed from the datasheet context?
- measured_verified: Can the measured value be confirmed from the test report context? If measured is empty, set true (not tested is acceptable).
- deviation_accurate: Is the deviation cell accurate? Empty deviation on a compliant row = accurate (true).
- missing_from_spec: Is this parameter not mentioned in the retrieved spec context at all?
- If spec context is unavailable, set specified_correct=true (benefit of doubt) and note it in finding."""


@traceable(name="audit_single_row")
def _audit_row(
    row: TableRow,
    spec_context: str,
    datasheet_context: str,
    test_context: str,
) -> TableRowFinding:
    user_msg = (
        f"PARAMETER: {row.parameter}\n"
        f"SPECIFIED VALUE: {row.specified}\n"
        f"PROPOSED VALUE: {row.proposed}\n"
        f"DEVIATION DECLARED: {row.deviation}\n"
        f"MEASURED VALUE: {row.measured}\n"
        f"REMARKS: {row.remarks}\n\n"
        f"AUTHORITY SPEC CONTEXT:\n{spec_context[:1500]}\n\n"
        f"DATASHEET CONTEXT:\n{datasheet_context[:1000]}\n\n"
        f"TEST REPORT CONTEXT:\n{test_context[:1000]}"
    )

    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _ROW_AUDIT_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        parsed = json.loads(response.choices[0].message.content)
    except Exception:
        parsed = {}

    severity_map = {"pass": Severity.PASS, "warning": Severity.WARNING, "critical": Severity.CRITICAL}
    return TableRowFinding(
        parameter=row.parameter,
        specified_value=row.specified,
        proposed_value=row.proposed,
        deviation_declared=row.deviation,
        measured_value=row.measured,
        specified_correct=parsed.get("specified_correct", True),
        proposed_verified=parsed.get("proposed_verified", False),
        measured_verified=parsed.get("measured_verified", True),
        deviation_accurate=parsed.get("deviation_accurate", True),
        missing_from_spec=parsed.get("missing_from_spec", False),
        finding=parsed.get("finding", "Audit result unavailable."),
        severity=severity_map.get(parsed.get("severity", "warning"), Severity.WARNING),
    )


def _get_text_for_type(
    doc_type: DocType,
    classified: dict[str, dict],
    file_contents: dict[str, bytes],
) -> str:
    from src.parsers.pdf_parser import extract_text_from_bytes
    for filename, doc_dict in classified.items():
        doc = ClassifiedDocument.model_validate(doc_dict)
        if doc.doc_type == doc_type:
            content = file_contents.get(filename, b"")
            if content:
                return extract_text_from_bytes(content, max_pages=5)
    return ""


@traceable(name="table_auditor_agent")
def table_auditor_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 4 — Table Auditor (highest value agent).

    For each row in the comparison table:
    - Verifies the specified value against the authority spec (RAG)
    - Verifies the proposed value against the technical datasheet
    - Verifies the measured value against the test report
    - Checks deviation accuracy using the deviation rule
    """
    authority: str = state.get("authority", "ADM")
    spec_clause: str = state.get("spec_clause", "")
    classified: dict[str, dict] = state.get("classified_documents", {})
    file_contents: dict[str, bytes] = state.get("file_contents", {})

    findings: list[dict] = []

    # Find and extract the comparison table
    table_rows: list[TableRow] = []
    for filename, doc_dict in classified.items():
        doc = ClassifiedDocument.model_validate(doc_dict)
        if doc.doc_type == DocType.COMPARISON_TABLE:
            content = file_contents.get(filename, b"")
            if content:
                table_rows = extract_all_table_rows(content)
            break

    if not table_rows:
        return {**state, "table_audit_findings": findings}

    # Retrieve supporting contexts once (shared across all rows)
    spec_context = ""
    if spec_clause:
        raw = assemble_spec_context(
            question=f"Material property requirements for clause {spec_clause}",
            clause_ref=spec_clause,
            authority=authority,
        )
        spec_context = "" if raw == EMPTY_CONTEXT_SENTINEL else raw

    datasheet_text = _get_text_for_type(DocType.TECHNICAL_DATASHEET, classified, file_contents)
    test_text = _get_text_for_type(DocType.TEST_REPORT, classified, file_contents)

    for row in table_rows:
        if not row.parameter.strip():
            continue
        finding = _audit_row(row, spec_context, datasheet_text, test_text)
        findings.append(finding.model_dump())

    return {**state, "table_audit_findings": findings}
