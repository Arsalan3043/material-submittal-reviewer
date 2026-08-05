"""
Unit tests for the regression-baseline comparison logic.

Zero OpenAI, zero AWS, zero pipeline dependencies — baseline.py imports the pipeline
lazily inside run_one() precisely so this file can exercise signature()/_diff() on
hand-built report dicts.

What's being protected here is the check's trustworthiness in both directions: it must
catch every real decision change (or a src/ regression ships silently), and it must stay
quiet on meaningless variation like finding order or prose wording (or it cries wolf and
stops being read).
"""

from __future__ import annotations

from tests.baseline.baseline import _diff, prose, signature


def _report(**overrides) -> dict:
    base = {
        "overall_recommendation": "CONDITIONAL",
        "critical_count": 1,
        "warning_count": 2,
        "missing_documents": ["BOQ", "Drawings"],
        "spec_verification_findings": [
            {
                "stage": "spec_verification",
                "document": "submittal_package",
                "severity": "critical",
                "description": "PN rating mismatch",
                "action_required": "Resubmit",
            }
        ],
        "validity_findings": [
            {
                "stage": "validity",
                "document": "test_report",
                "severity": "warning",
                "description": "Certificate expires soon",
                "action_required": "Provide renewal",
            }
        ],
        "table_audit_findings": [
            {
                "parameter": "PN Rating",
                "severity": "warning",
                "specified_correct": True,
                "proposed_verified": False,
                "measured_verified": True,
                "deviation_accurate": True,
                "missing_from_spec": False,
            }
        ],
    }
    base.update(overrides)
    return base


# ── Stability: things that must NOT be reported as regressions ──────────────────


def test_identical_reports_produce_no_diff():
    assert _diff(signature(_report()), signature(_report())) == []


def test_finding_order_is_not_a_regression():
    """Findings arrive in whatever order nodes append them; reordering carries no meaning."""
    a = _report(
        completeness_findings=[
            {"stage": "completeness", "document": "boq", "severity": "warning"},
            {"stage": "completeness", "document": "msdf", "severity": "critical"},
        ]
    )
    b = _report(
        completeness_findings=[
            {"stage": "completeness", "document": "msdf", "severity": "critical"},
            {"stage": "completeness", "document": "boq", "severity": "warning"},
        ]
    )
    assert _diff(signature(a), signature(b)) == []


def test_missing_documents_order_is_not_a_regression():
    a = _report(missing_documents=["Drawings", "BOQ"])
    b = _report(missing_documents=["BOQ", "Drawings"])
    assert _diff(signature(a), signature(b)) == []


def test_prose_changes_do_not_affect_the_hard_signature():
    """Wording drift at temperature=0 is real but meaningless — it must not fail a check."""
    reworded = _report(
        spec_verification_findings=[
            {
                "stage": "spec_verification",
                "document": "submittal_package",
                "severity": "critical",
                "description": "The PN rating does not match the specification.",
                "action_required": "Please resubmit with compliant material.",
            }
        ]
    )
    assert _diff(signature(_report()), signature(reworded)) == []
    # …but it IS surfaced on the soft channel, so a substantive reasoning change is visible.
    assert _diff(prose(_report()), prose(reworded)) != []


# ── Sensitivity: things that MUST be reported ───────────────────────────────────


def test_recommendation_change_is_caught():
    diffs = _diff(signature(_report()), signature(_report(overall_recommendation="RESUBMIT")))
    assert any("overall_recommendation" in d for d in diffs)


def test_severity_change_is_caught():
    downgraded = _report(
        spec_verification_findings=[
            {
                "stage": "spec_verification",
                "document": "submittal_package",
                "severity": "warning",
                "description": "PN rating mismatch",
                "action_required": "Resubmit",
            }
        ]
    )
    assert _diff(signature(_report()), signature(downgraded)) != []


def test_new_finding_is_caught():
    extra = _report(
        validity_findings=[
            *_report()["validity_findings"],
            {
                "stage": "validity",
                "document": "ded_registration",
                "severity": "critical",
                "description": "Expired",
                "action_required": "Renew",
            },
        ]
    )
    diffs = _diff(signature(_report()), signature(extra))
    assert any("validity_findings" in d for d in diffs)


def test_dropped_finding_is_caught():
    assert _diff(signature(_report()), signature(_report(spec_verification_findings=[]))) != []


def test_table_audit_verdict_flip_is_caught():
    """The audit's booleans are its actual decisions — a flip is a behaviour change."""
    flipped = _report(
        table_audit_findings=[
            {**_report()["table_audit_findings"][0], "proposed_verified": True}
        ]
    )
    diffs = _diff(signature(_report()), signature(flipped))
    assert any("proposed_verified" in d for d in diffs)


def test_missing_document_change_is_caught():
    assert _diff(signature(_report()), signature(_report(missing_documents=["BOQ"]))) != []


# ── Shape handling ──────────────────────────────────────────────────────────────


def test_all_nine_categories_are_covered():
    """A category missing from the signature is a blind spot — findings there would change
    without the check noticing."""
    categories = signature(_report())["categories"]
    assert len(categories) == 9
    assert "table_audit_findings" in categories


def test_absent_categories_are_treated_as_empty():
    """Nodes that produce nothing may omit the key entirely rather than sending []."""
    sig = signature({"overall_recommendation": "APPROVE"})
    assert sig["categories"]["avl_findings"] == {"count": 0, "findings": []}


def test_pydantic_style_objects_are_accepted():
    """Reports cross the src/ seam as models or dicts depending on the path."""

    class FakeSeverity:
        value = "critical"

    findings = [
        {"stage": "avl", "document": "avl", "severity": FakeSeverity(), "description": "x"}
    ]
    sig = signature({"avl_findings": findings})
    assert sig["categories"]["avl_findings"]["findings"][0]["severity"] == "critical"
