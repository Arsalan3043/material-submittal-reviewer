"""
Flattens a completed review's report dict into rows for `INSERT INTO findings`
(migration 006). Pure and DB-free — apps/worker/worker.py does the actual writing —
so this is directly unit-testable with a synthetic report dict, zero DB/OpenAI/AWS cost.

NOT importing anything from src/agents/ or src/models/: `report` here is already the plain
dict that comes out of ReviewPipelinePort.run() (core.models.ReviewResult.report), the same
shape both LangGraphReviewPipeline and FakeReviewPipeline produce. Working off the dict
keeps this module decoupled from the frozen src/ pydantic models entirely.
"""
from __future__ import annotations

import uuid

# report dict key -> findings.category. Every one of the 8 standard *_findings lists
# shares the Finding shape (stage/document/description/severity/action_required);
# table_audit_findings is handled separately below since TableRowFinding's shape differs.
_STANDARD_REPORT_KEYS: dict[str, str] = {
    "completeness_findings": "completeness",
    "boq_drawing_findings": "boq_drawing",
    "spec_verification_findings": "spec_verification",
    "validity_findings": "validity",
    "avl_findings": "avl",
    "statement_findings": "statement",
    "consistency_findings": "consistency",
    "others_findings": "others",
}

# findings.category -> the real LangGraph node name that produced it (PROJECT_STATE.md
# §2's node table) — deliberately not identical to the category string (e.g.
# "spec_verification" category / "spec_verifier" node, "table_audit" / "table_auditor").
_CATEGORY_NODE: dict[str, str] = {
    "completeness": "completeness",
    "boq_drawing": "boq_drawing",
    "spec_verification": "spec_verifier",
    "validity": "validity_checker",
    "avl": "avl_check",
    "statement": "statement",
    "table_audit": "table_auditor",
    "consistency": "consistency",
    "others": "others",
}

# src/models/findings.py::Severity is "pass"/"warning"/"critical". findings.severity uses
# the notes/10_stage1_product_and_data_spec.md §A1 vocabulary ("observation" instead of
# "pass") — this is the one translation between the two.
_SEVERITY_MAP: dict[str, str] = {"pass": "observation", "warning": "warning", "critical": "critical"}


def _map_severity(raw: str) -> str:
    key = str(raw).lower()
    if key not in _SEVERITY_MAP:
        raise ValueError(f"unrecognized finding severity: {raw!r}")
    return _SEVERITY_MAP[key]


def extract_findings(
    report: dict,
    *,
    tenant_id: str,
    project_id: str,
    submittal_id: str,
    pipeline_version: str,
) -> list[dict]:
    """Returns one dict per finding, each with a fresh finding id, ready to bind directly
    against the INSERT in apps/worker/worker.py::run_review. clause_reference/
    spec_document_id/spec_page/source_document_id/source_page/confidence/model_version/
    prompt_version are always None here — see db/models.py::Finding's docstring for why."""

    def _base_row(category: str) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "submittal_id": submittal_id,
            "category": category,
            "clause_reference": None,
            "spec_document_id": None,
            "spec_page": None,
            "source_document_id": None,
            "source_page": None,
            "confidence": None,
            "pipeline_node": _CATEGORY_NODE[category],
            "model_version": None,
            "prompt_version": None,
            "pipeline_version": pipeline_version,
        }

    rows: list[dict] = []

    for report_key, category in _STANDARD_REPORT_KEYS.items():
        for finding in report.get(report_key) or []:
            row = _base_row(category)
            row["severity"] = _map_severity(finding["severity"])
            row["description"] = finding["description"]
            row["action_required"] = finding.get("action_required")
            rows.append(row)

    for table_row in report.get("table_audit_findings") or []:
        row = _base_row("table_audit")
        row["severity"] = _map_severity(table_row["severity"])
        row["description"] = table_row["finding"]
        row["action_required"] = None
        rows.append(row)

    return rows
