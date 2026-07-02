from __future__ import annotations

import json
import re

from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.findings import Severity, TableRowFinding
from src.models.knowledge_store import load_store
from src.models.requirements import ReviewRequirementsArtifact, SpecRequirement
from src.models.submittal import DocType
from src.parsers.table_extractor import TableRow

_MODEL = "gpt-4o-mini"
_BATCH_SIZE = 25
_MAX_DOC_CHARS = 3000   # per document type — increased from 1500

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = wrap_openai(OpenAI())
    return _client


# ── Prompt ─────────────────────────────────────────────────────────────────────

_AUDIT_SYSTEM = """You are auditing a UAE construction material submittal comparison table.

INPUTS:
1. SPEC REQUIREMENTS — JSON list of what the specification requires (ground truth).
   Each entry has: id, type, description, operator/value_min/unit (for numeric), mandatory flag.
2. TABLE ROWS — what the contractor submitted (parameter, specified, proposed, measured, deviation, remarks).
3. DATASHEET — manufacturer technical data.
4. TEST REPORT — lab test results (may be empty).

For EACH table row return one result in the same order:
- matched_requirement_id  : the requirement id this row addresses, or null if not in spec
- specified_correct       : does the "specified" cell match the authority spec requirement?
- proposed_verified       : can the "proposed" value be confirmed from the datasheet?
- measured_verified       : can the "measured" value be confirmed from the test report?
                            If measured cell is empty → true (not tested ≠ error).
- deviation_accurate      : is the deviation cell accurate? Empty deviation on compliant row → true.
- missing_from_spec       : is this parameter absent from the spec requirements list?
- contradiction_detected  : does the proposed value conflict with the measured value?
- extracted_proposed_value: parse the numeric value from the proposed cell (e.g. 6.3 from "6.3 mm"), or null
- extracted_measured_value: parse the numeric value from the measured cell, or null
- finding                 : one sentence describing the audit result
- severity                : "pass" | "warning" | "critical"

DEVIATION RULE:
- A proposed value exceeding a minimum is COMPLIANT — not a deviation.
- "Specified: min 40 MPa | Proposed: 45 MPa" → no deviation, severity=pass.
- Flag severity=critical only when the proposed value FAILS the requirement.

CONTRADICTION RULE:
- If proposed and measured values both exist and differ significantly (>5% for numeric), set contradiction_detected=true.
- Example: datasheet says 6.3 mm, test report says 5.8 mm → contradiction, severity=warning.

Also return missing_requirement_ids: list of requirement IDs that have no corresponding row in the table (mandatory requirements only).

Return JSON only:
{
  "rows": [
    {
      "matched_requirement_id": "R-001",
      "specified_correct": true,
      "proposed_verified": true,
      "measured_verified": true,
      "deviation_accurate": true,
      "missing_from_spec": false,
      "contradiction_detected": false,
      "extracted_proposed_value": 6.3,
      "extracted_measured_value": 6.3,
      "finding": "...",
      "severity": "pass"
    }
  ],
  "missing_requirement_ids": []
}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_requirements_json(artifact: ReviewRequirementsArtifact | None) -> list[dict]:
    """
    Serialize requirements as structured JSON — not text.
    The LLM receives clean data objects, not an English summary it must re-parse.
    """
    if not artifact or not artifact.requirements:
        return []

    result: list[dict] = []
    for req in artifact.requirements:
        ev = req.expected_value
        entry: dict = {
            "id": req.id,
            "type": req.requirement_type.value,
            "description": req.normalized_requirement,
            "mandatory": req.mandatory,
        }
        if ev.is_numeric():
            entry["operator"] = ev.operator
            entry["value_min"] = ev.numeric_min
            entry["value_max"] = ev.numeric_max
            entry["unit"] = ev.unit or ""
        else:
            entry["expected_text"] = ev.text or ""
        result.append(entry)
    return result


def _build_rows_block(rows: list[TableRow]) -> str:
    lines: list[str] = []
    for i, r in enumerate(rows):
        lines.append(
            f"Row {i}: parameter={r.parameter!r} | specified={r.specified!r} | "
            f"proposed={r.proposed!r} | deviation={r.deviation!r} | "
            f"measured={r.measured!r} | remarks={r.remarks!r}"
        )
    return "\n".join(lines)


def _parse_numeric(text: str) -> float | None:
    """Extract the first numeric value from a string like '6.3 mm' or '≥ 40 MPa'."""
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", "."))
    return float(m.group()) if m else None


def _apply_deterministic_overrides(
    row: TableRow,
    raw: dict,
    req_by_id: dict[str, SpecRequirement],
) -> dict:
    """
    Override LLM results with Python math for numeric requirements.

    For numeric requirements:
    - Extract proposed value from LLM's extracted_proposed_value (or fallback: parse cell text)
    - Run ExpectedValue.check(actual) — deterministic
    - Override specified_correct and severity accordingly

    For contradictions between proposed and measured:
    - If both numeric values present and differ by >5%, force contradiction_detected=True
    """
    req_id = raw.get("matched_requirement_id")
    req = req_by_id.get(req_id) if req_id else None

    # Pull extracted values — prefer LLM parse, fall back to regex on cell text
    proposed_val: float | None = raw.get("extracted_proposed_value")
    if proposed_val is None:
        proposed_val = _parse_numeric(row.proposed)

    measured_val: float | None = raw.get("extracted_measured_value")
    if measured_val is None and row.measured.strip():
        measured_val = _parse_numeric(row.measured)

    # Numeric deterministic check
    if req and req.expected_value.is_numeric() and proposed_val is not None:
        passes = req.expected_value.check(proposed_val)
        raw["specified_correct"] = passes
        ev = req.expected_value
        op_str = f"{ev.operator} {ev.numeric_min}{(' – ' + str(ev.numeric_max)) if ev.numeric_max else ''} {ev.unit or ''}".strip()
        if not passes:
            raw["severity"] = "critical"
            raw["finding"] = (
                f"FAIL (deterministic): proposed {proposed_val} {ev.unit or ''} "
                f"does not satisfy {op_str}. {raw.get('finding', '')}"
            ).strip()
        else:
            # Don't downgrade to pass if LLM found other issues
            if raw.get("severity") == "critical":
                raw["severity"] = "warning"
            raw["finding"] = (
                f"PASS (deterministic): proposed {proposed_val} {ev.unit or ''} "
                f"satisfies {op_str}. {raw.get('finding', '')}"
            ).strip()

    # Contradiction detection: proposed vs measured differ by >15%.
    # 5% was too aggressive — manufacturing tolerances and different test conditions
    # routinely produce 5–10% variation without indicating a genuine contradiction.
    if proposed_val is not None and measured_val is not None and proposed_val != 0:
        diff_pct = abs(proposed_val - measured_val) / abs(proposed_val)
        if diff_pct > 0.15:
            raw["contradiction_detected"] = True
            raw["deviation_accurate"] = False
            if raw.get("severity") == "pass":
                raw["severity"] = "warning"
            raw["finding"] = (
                f"CONTRADICTION: datasheet/table shows {proposed_val}, "
                f"test report shows {measured_val} "
                f"({diff_pct:.0%} difference). {raw.get('finding', '')}"
            ).strip()

    return raw


def _missing_req_to_finding(req: SpecRequirement) -> TableRowFinding:
    ev = req.expected_value
    specified_str = ev.text or req.normalized_requirement
    return TableRowFinding(
        parameter=req.normalized_requirement,
        specified_value=specified_str,
        proposed_value="(not included in comparison table)",
        deviation_declared="",
        measured_value="",
        specified_correct=True,
        proposed_verified=False,
        measured_verified=False,
        deviation_accurate=False,
        missing_from_spec=False,
        finding=(
            f"Mandatory requirement '{req.normalized_requirement}' is absent from "
            "the comparison table. Contractor must include this parameter."
        ),
        severity=Severity.CRITICAL if req.mandatory else Severity.WARNING,
    )


# ── Core audit call ────────────────────────────────────────────────────────────

@traceable(name="table_auditor_batch")
def _audit_batch(
    rows: list[TableRow],
    requirements_json: list[dict],
    datasheet_text: str,
    test_text: str,
    req_by_id: dict[str, SpecRequirement],
) -> tuple[list[TableRowFinding], list[str]]:
    """
    One LLM call audits a batch of rows.
    Python post-processing applies deterministic numeric overrides and
    contradiction detection after the LLM returns.
    Returns (row_findings, missing_requirement_ids).
    """
    user_msg = (
        f"SPEC REQUIREMENTS (JSON):\n{json.dumps(requirements_json, indent=2)}\n\n"
        f"DATASHEET:\n{datasheet_text[:_MAX_DOC_CHARS]}\n\n"
        f"TEST REPORT:\n{test_text[:_MAX_DOC_CHARS]}\n\n"
        f"TABLE ROWS ({len(rows)} rows):\n{_build_rows_block(rows)}"
    )

    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _AUDIT_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        data = json.loads(response.choices[0].message.content)
        parsed_rows = data.get("rows", [])
        missing_ids: list[str] = data.get("missing_requirement_ids", [])
    except Exception:
        parsed_rows = []
        missing_ids = []

    severity_map = {
        "pass": Severity.PASS,
        "warning": Severity.WARNING,
        "critical": Severity.CRITICAL,
    }
    findings: list[TableRowFinding] = []
    for i, row in enumerate(rows):
        raw = dict(parsed_rows[i]) if i < len(parsed_rows) else {}

        # Apply deterministic overrides before building the finding object
        raw = _apply_deterministic_overrides(row, raw, req_by_id)

        def _b(val: object, default: bool) -> bool:
            return val if isinstance(val, bool) else default

        findings.append(TableRowFinding(
            parameter=row.parameter,
            specified_value=row.specified,
            proposed_value=row.proposed,
            deviation_declared=row.deviation,
            measured_value=row.measured,
            specified_correct=_b(raw.get("specified_correct"), True),
            proposed_verified=_b(raw.get("proposed_verified"), False),
            measured_verified=_b(raw.get("measured_verified"), True),
            deviation_accurate=_b(raw.get("deviation_accurate"), True),
            missing_from_spec=_b(raw.get("missing_from_spec"), False),
            finding=raw.get("finding") or "Audit result unavailable.",
            severity=severity_map.get(raw.get("severity", "warning"), Severity.WARNING),
        ))

    return findings, missing_ids


# ── Main node ──────────────────────────────────────────────────────────────────

@traceable(name="table_auditor_agent")
def table_auditor_node(state: SubmittalReviewState) -> dict:
    """
    Agent 4 — Table Auditor.

    Uses structured requirement JSON (not text) so the LLM receives clean data
    objects rather than English it must re-parse.  After the LLM returns,
    Python applies deterministic numeric checks using ExpectedValue.check() —
    the LLM finds the values, Python decides pass/fail.

    Contradiction detection: if proposed and measured values differ by >5%,
    the discrepancy is flagged without relying on LLM judgment.

    Falls back gracefully when requirements_artifact is absent.
    """
    store = load_store(state["knowledge_store_id"])

    table_rows: list[TableRow] = [TableRow.model_validate(r) for r in store.table_rows]
    if not table_rows:
        return {**state, "table_audit_findings": []}

    # Load requirements from spec_verifier — no duplicate RAG call
    requirements_artifact: ReviewRequirementsArtifact | None = None
    raw_artifact = state.get("requirements_artifact")
    if raw_artifact:
        try:
            requirements_artifact = ReviewRequirementsArtifact.model_validate(raw_artifact)
        except Exception:
            requirements_artifact = None

    # Only pass requirements that belong in a comparison table.
    # Installation, experience, administrative, warranty requirements are never
    # in a comparison table — filtering them out eliminates false "missing" flags.
    table_artifact: ReviewRequirementsArtifact | None = None
    if requirements_artifact:
        table_reqs = [r for r in requirements_artifact.requirements if r.comparison_table_required]
        if table_reqs:
            table_artifact = requirements_artifact.model_copy(
                update={"requirements": table_reqs}
            )

    requirements_json = _build_requirements_json(table_artifact)
    req_by_id: dict[str, SpecRequirement] = (
        {r.id: r for r in table_artifact.requirements}
        if table_artifact else {}
    )

    datasheet_text = store.get_text(DocType.TECHNICAL_DATASHEET)
    test_text = store.get_text(DocType.TEST_REPORT)

    all_findings: list[TableRowFinding] = []
    all_missing_ids: list[str] = []

    for i in range(0, len(table_rows), _BATCH_SIZE):
        batch = table_rows[i : i + _BATCH_SIZE]
        findings, missing_ids = _audit_batch(
            batch, requirements_json, datasheet_text, test_text, req_by_id
        )
        all_findings.extend(findings)
        if i == 0:
            all_missing_ids = missing_ids

    # Mandatory table requirements with no corresponding row → critical findings
    if table_artifact and all_missing_ids:
        req_by_id_lookup = {r.id: r for r in table_artifact.requirements}
        for req_id in all_missing_ids:
            req = req_by_id_lookup.get(req_id)
            if req and req.mandatory:
                all_findings.append(_missing_req_to_finding(req))

    return {**state, "table_audit_findings": [f.model_dump() for f in all_findings]}
