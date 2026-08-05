"""apps/worker/findings.py::extract_findings — pure, DB-free flattening of a report dict
into findings-table rows. Zero OpenAI/AWS cost by construction (no network, no DB)."""
from __future__ import annotations

import uuid

import pytest

from apps.worker.findings import extract_findings


def _finding(severity: str, description: str = "desc", action_required: str = "do X") -> dict:
    return {
        "stage": "irrelevant",
        "document": "irrelevant.pdf",
        "description": description,
        "severity": severity,
        "action_required": action_required,
    }


def _table_row_finding(severity: str, finding: str = "row mismatch") -> dict:
    return {
        "parameter": "PN rating",
        "specified_value": "PN16",
        "proposed_value": "PN16",
        "deviation_declared": "none",
        "measured_value": "PN16",
        "specified_correct": True,
        "proposed_verified": True,
        "measured_verified": True,
        "deviation_accurate": True,
        "missing_from_spec": False,
        "finding": finding,
        "severity": severity,
    }


def _minimal_report(**overrides) -> dict:
    base: dict = {
        "completeness_findings": [],
        "boq_drawing_findings": [],
        "spec_verification_findings": [],
        "validity_findings": [],
        "avl_findings": [],
        "statement_findings": [],
        "table_audit_findings": [],
        "consistency_findings": [],
        "others_findings": [],
    }
    base.update(overrides)
    return base


def _extract(report: dict) -> list[dict]:
    return extract_findings(
        report,
        tenant_id="tenant-1",
        project_id="project-1",
        submittal_id="submittal-1",
        pipeline_version="v1",
    )


def test_empty_report_yields_no_findings() -> None:
    assert _extract(_minimal_report()) == []


def test_each_standard_category_maps_to_correct_category_and_node() -> None:
    report = _minimal_report(
        completeness_findings=[_finding("critical")],
        boq_drawing_findings=[_finding("warning")],
        spec_verification_findings=[_finding("pass")],
        validity_findings=[_finding("critical")],
        avl_findings=[_finding("warning")],
        statement_findings=[_finding("pass")],
        consistency_findings=[_finding("critical")],
        others_findings=[_finding("warning")],
    )
    rows = _extract(report)
    by_category = {r["category"]: r for r in rows}

    assert by_category["completeness"]["pipeline_node"] == "completeness"
    assert by_category["boq_drawing"]["pipeline_node"] == "boq_drawing"
    assert by_category["spec_verification"]["pipeline_node"] == "spec_verifier"
    assert by_category["validity"]["pipeline_node"] == "validity_checker"
    assert by_category["avl"]["pipeline_node"] == "avl_check"
    assert by_category["statement"]["pipeline_node"] == "statement"
    assert by_category["consistency"]["pipeline_node"] == "consistency"
    assert by_category["others"]["pipeline_node"] == "others"


def test_table_audit_findings_use_the_finding_text_field_and_table_audit_node() -> None:
    report = _minimal_report(table_audit_findings=[_table_row_finding("critical", "PN mismatch")])
    rows = _extract(report)

    assert len(rows) == 1
    assert rows[0]["category"] == "table_audit"
    assert rows[0]["pipeline_node"] == "table_auditor"
    assert rows[0]["description"] == "PN mismatch"
    assert rows[0]["action_required"] is None


@pytest.mark.parametrize(
    "raw,expected",
    [("pass", "observation"), ("warning", "warning"), ("critical", "critical")],
)
def test_severity_mapping(raw: str, expected: str) -> None:
    report = _minimal_report(completeness_findings=[_finding(raw)])
    rows = _extract(report)
    assert rows[0]["severity"] == expected


def test_unrecognized_severity_raises() -> None:
    report = _minimal_report(completeness_findings=[_finding("banana")])
    with pytest.raises(ValueError):
        _extract(report)


def test_citation_and_versioning_columns_are_null_by_design() -> None:
    report = _minimal_report(completeness_findings=[_finding("critical")])
    row = _extract(report)[0]

    for field in (
        "clause_reference",
        "spec_document_id",
        "spec_page",
        "source_document_id",
        "source_page",
        "confidence",
        "model_version",
        "prompt_version",
    ):
        assert row[field] is None, field


def test_pipeline_version_is_carried_through() -> None:
    report = _minimal_report(completeness_findings=[_finding("critical")])
    row = _extract(report)[0]
    assert row["pipeline_version"] == "v1"


def test_tenant_project_submittal_ids_are_carried_through() -> None:
    report = _minimal_report(completeness_findings=[_finding("critical")])
    row = _extract(report)[0]
    assert row["tenant_id"] == "tenant-1"
    assert row["project_id"] == "project-1"
    assert row["submittal_id"] == "submittal-1"


def test_each_finding_gets_a_distinct_valid_uuid() -> None:
    report = _minimal_report(
        completeness_findings=[_finding("critical"), _finding("warning")],
    )
    rows = _extract(report)
    ids = [uuid.UUID(r["id"]) for r in rows]
    assert len(ids) == 2
    assert ids[0] != ids[1]


def test_action_required_defaults_to_none_when_absent() -> None:
    finding = _finding("critical")
    del finding["action_required"]
    report = _minimal_report(completeness_findings=[finding])
    row = _extract(report)[0]
    assert row["action_required"] is None
