"""
Phase 4 — Agent Scenario Tests

Runs 5 end-to-end scenarios through the full LangGraph pipeline and asserts
each produces the expected findings. Tests are real integration tests — they
call OpenAI and ChromaDB. Mark fast unit tests with pytest -m "not integration".

Run all:
    pytest experiments/agents/scenario_tests.py -v

Run one:
    pytest experiments/agents/scenario_tests.py::test_scenario_01 -v

Results are written to experiments/agents/results/ after each run.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Make src importable when running from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agents.orchestrator import compile_review_graph  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────────────

DATA_DIR       = Path(__file__).parent.parent / "data"
FINDINGS_DIR   = DATA_DIR / "expected_findings"
SUBMITTALS_DIR = DATA_DIR / "sample_submittals"
RESULTS_DIR    = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_scenario(scenario_id: int) -> dict:
    path = FINDINGS_DIR / f"scenario_{scenario_id:02d}.json"
    with open(path) as f:
        return json.load(f)


def _load_submittal_files(folder: str) -> dict[str, bytes]:
    folder_path = SUBMITTALS_DIR / folder
    files = {}
    for pdf_path in sorted(folder_path.glob("*.pdf")):
        files[pdf_path.name] = pdf_path.read_bytes()
    if not files:
        pytest.skip(f"No PDF files found in {folder_path}")
    return files


_SCENARIO_REVIEW_DATE = "2024-12-01"  # Fixed date so validity checks don't depend on when the test runs

def _run_pipeline(authority: str, submittal_id: str, file_contents: dict[str, bytes]) -> dict:
    graph = compile_review_graph()
    initial_state: dict = {
        "authority": authority,
        "submittal_id": submittal_id,
        "file_contents": file_contents,
        "declared_labels": {fn: None for fn in file_contents},
        "review_date": _SCENARIO_REVIEW_DATE,
    }
    return graph.invoke(initial_state)


def _collect_all_findings(final_state: dict) -> list[dict]:
    """Flatten every stage's findings into one list, injecting a 'stage' key."""
    stage_keys = {
        "completeness_findings":        "completeness",
        "boq_drawing_findings":         "boq_drawing",
        "spec_verification_findings":   "spec_verification",
        "validity_findings":            "validity",
        "avl_findings":                 "avl_check",
        "statement_findings":           "statement",
        "table_audit_findings":         "table_audit",
        "consistency_findings":         "consistency",
        "others_findings":              "others",
    }
    result = []
    for key, stage_name in stage_keys.items():
        for finding in final_state.get(key, []):
            result.append({**finding, "stage": stage_name})
    return result


def _finding_matches(finding: dict, rule: dict) -> bool:
    """Return True if a finding dict satisfies a required/forbidden rule."""
    if rule.get("stage") and finding.get("stage") != rule["stage"]:
        return False
    if rule.get("severity") and finding.get("severity") != rule["severity"]:
        return False
    needle = rule.get("description_contains", "").lower()
    if needle:
        haystack = " ".join([
            finding.get("description", ""),
            finding.get("finding", ""),
            finding.get("parameter", ""),
            finding.get("action_required", ""),
        ]).lower()
        if needle not in haystack:
            return False
    return True


def _assert_scenario(final_state: dict, expected: dict) -> list[str]:
    """
    Check all assertions defined in the expected fixture.
    Returns a list of failure messages (empty = all passed).
    """
    failures: list[str] = []
    report      = final_state.get("report", {})
    all_findings = _collect_all_findings(final_state)

    # 1. Overall recommendation
    rec = expected.get("overall_recommendation")
    if rec is not None:
        actual = report.get("overall_recommendation")
        if actual != rec:
            failures.append(
                f"overall_recommendation: expected {rec!r}, got {actual!r}"
            )

    # 2. Critical count bounds
    actual_criticals = report.get("critical_count", 0)
    if "min_critical_count" in expected:
        if actual_criticals < expected["min_critical_count"]:
            failures.append(
                f"critical_count: expected ≥{expected['min_critical_count']}, got {actual_criticals}"
            )
    if "max_critical_count" in expected:
        if actual_criticals > expected["max_critical_count"]:
            failures.append(
                f"critical_count: expected ≤{expected['max_critical_count']}, got {actual_criticals}"
            )

    # 3. All stages must complete (all finding keys present in state)
    if expected.get("all_stages_must_complete"):
        required_keys = [
            "completeness_findings", "boq_drawing_findings", "spec_verification_findings",
            "validity_findings", "avl_findings", "statement_findings",
            "table_audit_findings", "consistency_findings", "others_findings",
        ]
        for key in required_keys:
            if key not in final_state:
                failures.append(f"Pipeline stopped early — {key} missing from state")

    # 4. AVL check must have run (TAQA scenarios)
    if expected.get("avl_check_must_run"):
        if "avl_findings" not in final_state:
            failures.append("avl_check_must_run: avl_findings key missing from final state")

    # 5. Required findings — each rule must be satisfied by at least count_at_least findings
    for rule in expected.get("required_findings", []):
        count_needed = rule.get("count_at_least", 1)
        matched = [f for f in all_findings if _finding_matches(f, rule)]
        if len(matched) < count_needed:
            failures.append(
                f"Required finding not satisfied: "
                f"stage={rule.get('stage')!r} "
                f"severity={rule.get('severity')!r} "
                f"contains={rule.get('description_contains')!r} — "
                f"expected ≥{count_needed}, found {len(matched)}"
            )

    # 6. Forbidden findings — none of these may appear
    for rule in expected.get("forbidden_findings", []):
        matched = [f for f in all_findings if _finding_matches(f, rule)]
        if matched:
            reason = rule.get("reason", "forbidden finding present")
            failures.append(
                f"Forbidden finding detected ({reason}): {matched[0]}"
            )

    # 7. Every finding must reference a source document (scenario_05)
    if expected.get("all_findings_must_have_source"):
        sourceless = [
            f for f in all_findings
            if not f.get("document") and not f.get("parameter")
        ]
        if sourceless:
            failures.append(
                f"all_findings_must_have_source: {len(sourceless)} finding(s) have no source document"
            )

    return failures


def _save_result(scenario_id: int, final_state: dict, failures: list[str]) -> None:
    """Persist the full state and assertion result to the results directory."""
    result = {
        "scenario_id":  scenario_id,
        "run_at":       datetime.utcnow().isoformat(),
        "passed":       len(failures) == 0,
        "failures":     failures,
        "report":       final_state.get("report", {}),
        "finding_counts": {
            "completeness":     len(final_state.get("completeness_findings", [])),
            "boq_drawing":      len(final_state.get("boq_drawing_findings", [])),
            "spec_verification":len(final_state.get("spec_verification_findings", [])),
            "validity":         len(final_state.get("validity_findings", [])),
            "avl":              len(final_state.get("avl_findings", [])),
            "statement":        len(final_state.get("statement_findings", [])),
            "table_audit":      len(final_state.get("table_audit_findings", [])),
            "consistency":      len(final_state.get("consistency_findings", [])),
            "others":           len(final_state.get("others_findings", [])),
        },
    }
    out_path = RESULTS_DIR / f"scenario_{scenario_id:02d}_result.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)


# ── Scenario runner ───────────────────────────────────────────────────────────

def _run_scenario(scenario_id: int) -> tuple[dict, list[str]]:
    """Load fixture, run pipeline, assert, save result. Returns (state, failures)."""
    scenario    = _load_scenario(scenario_id)
    if scenario.get("needs_pdf_data"):
        pytest.skip(scenario.get("skip_reason", f"scenario_{scenario_id:02d} needs PDF data"))

    file_contents = _load_submittal_files(scenario["submittal_folder"])
    final_state   = _run_pipeline(
        authority    = scenario["authority"],
        submittal_id = f"SCENARIO_{scenario_id:02d}",
        file_contents= file_contents,
    )
    failures = _assert_scenario(final_state, scenario["expected"])
    _save_result(scenario_id, final_state, failures)
    return final_state, failures


# ── Test functions ────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_scenario_01_complete_correct_submittal():
    """Complete correct ADM submittal — pipeline must return APPROVE with zero criticals."""
    _, failures = _run_scenario(1)
    assert not failures, "\n".join(failures)


@pytest.mark.integration
def test_scenario_02_missing_two_documents():
    """ADM submittal with 2 missing index documents — must flag both, still return RESUBMIT."""
    _, failures = _run_scenario(2)
    assert not failures, "\n".join(failures)


@pytest.mark.integration
def test_scenario_03_wrong_table_values():
    """ADM submittal with wrong comparison table values — table auditor must flag at least 1 critical."""
    _, failures = _run_scenario(3)
    assert not failures, "\n".join(failures)


@pytest.mark.integration
def test_scenario_04_taqa_avl_failure():
    """Same submittal under TAQA authority — AVL check must run, flag manufacturer, pipeline must not stop."""
    _, failures = _run_scenario(4)
    assert not failures, "\n".join(failures)


@pytest.mark.integration
def test_scenario_05_multiple_issues_none_missed():
    """Same submittal as scenario_03 — comprehensive pass, all issues found, every finding has a source."""
    state_03, _ = _run_scenario(3)
    state_05, failures_05 = _run_scenario(5)

    # Scenario_05 must find at least as many criticals as scenario_03 found
    criticals_03 = state_03.get("report", {}).get("critical_count", 0)
    criticals_05 = state_05.get("report", {}).get("critical_count", 0)
    if criticals_05 < criticals_03:
        failures_05.append(
            f"Scenario 05 found fewer criticals ({criticals_05}) than scenario 03 ({criticals_03}) "
            f"— same submittal must not produce fewer findings"
        )

    assert not failures_05, "\n".join(failures_05)
