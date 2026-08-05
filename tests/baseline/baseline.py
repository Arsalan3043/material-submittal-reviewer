"""
Manual diff viewer for the review pipeline's output, plus two sanity guards.

This is NOT a regression gate, and it is NOT the eval harness (notes/11 ticket 9 — that one
asks "is the agent correct?" and needs Arsalan's graded golden set).

It WAS designed as an automated gate — "capture today's output, fail if a later run differs
structurally." That assumption broke on 2026-08-05: two runs of the SAME submittal, same
commit, seconds apart, produced different critical_counts (7 vs 4) and, worse, a deterministic
compliance check flipped FAIL -> PASS because table_auditor's pdfplumber+LLM extraction read a
different measured value (4.03mm vs 6.0mm) from the same PDF. Logged as F5 in
notes/12_pipeline_findings.md — it's a real product defect, not a baseline-tooling bug.

Given that, `check` cannot tell you "this diff is because of your code change" — it can only
show you a diff, which may be your change, may be the pipeline's own variance, or both mixed
together. There is no reliable way to separate them from a single before/after run. So:

  - For additive src/ changes (tickets 1, 4, 8 — new Optional fields populated from values
    already in scope, no prompt or control-flow change) the real safety check is READING THE
    DIFF OF THE .py FILE, not running the pipeline and comparing JSON.
  - For a behavioral refactor (ticket 7 — authority config moves from Python to Postgres) run
    `check` once as a sanity spot-check, expect it to show differences even if the refactor is
    perfect, and read them for anything structurally implausible (a category that should still
    exist disappearing entirely) rather than treating any diff as failure.
  - Real correctness — "is the output actually right" — is what ticket 9's golden-set eval
    exists for, and it already accounts for this: its own spec calls for fuzzy matching
    against ground truth, not exact-match against a prior run, for exactly this reason.

What's still worth keeping, independent of all that:

  preflight() / assert_pipeline_actually_ran() — these don't compare runs, they catch a run
  that did nothing useful (dead OPENAI_API_KEY, swallowed into others/low — see F1 in
  notes/12_pipeline_findings.md). Worth running any time you exercise the pipeline manually.

Usage:
    python -m tests.baseline.baseline capture   # save a reference (costs real OpenAI money)
    python -m tests.baseline.baseline check     # diff against it — informational, never fails
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Same as apps/api/main.py, apps/worker/worker.py and conftest.py. Without it this script gets
# no OPENAI_API_KEY, every LLM call raises, doc_processor swallows the failure into
# others/low (src/agents/doc_processor.py:323), and the run still produces a complete-looking
# report. That silently produced two worthless baselines before it was caught — hence the
# preflight and post-run checks below, not just this call.
load_dotenv(REPO_ROOT / ".env")

# Declared section labels come from the same module the API uses, so the baseline exercises
# production's labelling behaviour rather than a second copy of it that can drift. Safe at
# module scope — section_labels.py is pure Python with no pipeline dependencies, which keeps
# tests/unit/test_baseline.py importable at zero cost.
from apps.api.section_labels import infer_declared_label  # noqa: E402

SUBMITTAL_DIR = REPO_ROOT / "Test Submittal"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

# Pinned, NOT datetime.now(). validity_checker does certificate-expiry arithmetic against
# review_date, so a moving date would silently change findings as certificates age — the
# baseline would drift on its own and every check would look like a regression. Change this
# constant only deliberately, and re-capture when you do.
REVIEW_DATE = "2026-01-15"

# Constant fakes: the pipeline only passes these through to LangSmith metadata, but keeping
# them fixed means the request is identical between capture and check.
TENANT_ID = "00000000-0000-0000-0000-0000000000t1"
PROJECT_ID = "00000000-0000-0000-0000-0000000000p1"

# The eight list[Finding] categories on ReviewReport. table_audit is handled separately —
# TableRowFinding has a different shape.
FINDING_CATEGORIES = [
    "completeness_findings",
    "boq_drawing_findings",
    "spec_verification_findings",
    "validity_findings",
    "avl_findings",
    "statement_findings",
    "consistency_findings",
    "others_findings",
]

# The verdict booleans on TableRowFinding — these are the audit's actual decisions.
TABLE_VERDICT_FIELDS = [
    "specified_correct",
    "proposed_verified",
    "measured_verified",
    "deviation_accurate",
    "missing_from_spec",
]


# ── Report normalisation ────────────────────────────────────────────────────────


def _as_dict(obj) -> dict:
    """Reports and findings cross the src/ seam as either Pydantic models or plain dicts
    depending on the code path (see ReviewResult's docstring). Accept both."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _severity(value) -> str:
    """Severity is a str Enum; model_dump(mode="json") gives a str, but a raw .model_dump()
    or a hand-built dict may still carry the Enum."""
    return getattr(value, "value", value)


def signature(report: dict) -> dict:
    """The decision-bearing shape of a report. Free text is deliberately excluded."""
    report = _as_dict(report)
    sig: dict = {
        "overall_recommendation": report.get("overall_recommendation"),
        "critical_count": report.get("critical_count"),
        "warning_count": report.get("warning_count"),
        "missing_documents": sorted(report.get("missing_documents") or []),
        "categories": {},
    }

    for category in FINDING_CATEGORIES:
        findings = [_as_dict(f) for f in (report.get(category) or [])]
        sig["categories"][category] = {
            "count": len(findings),
            # Sorted so that a pure reordering of findings — which carries no meaning —
            # doesn't read as a regression.
            "findings": sorted(
                (
                    {
                        "stage": f.get("stage"),
                        "document": f.get("document"),
                        "severity": _severity(f.get("severity")),
                    }
                    for f in findings
                ),
                key=lambda d: (d["stage"] or "", d["document"] or "", d["severity"] or ""),
            ),
        }

    rows = [_as_dict(r) for r in (report.get("table_audit_findings") or [])]
    sig["categories"]["table_audit_findings"] = {
        "count": len(rows),
        "findings": sorted(
            (
                {
                    "parameter": r.get("parameter"),
                    "severity": _severity(r.get("severity")),
                    **{k: r.get(k) for k in TABLE_VERDICT_FIELDS},
                }
                for r in rows
            ),
            key=lambda d: (d["parameter"] or "", d["severity"] or ""),
        ),
    }
    return sig


def prose(report: dict) -> dict:
    """The soft-compare surface: free text, keyed so it can be diffed per finding."""
    report = _as_dict(report)
    out: dict[str, list[str]] = {}
    for category in FINDING_CATEGORIES:
        out[category] = [
            f"{_as_dict(f).get('description', '')} || {_as_dict(f).get('action_required', '')}"
            for f in (report.get(category) or [])
        ]
    out["summary_comments"] = [report.get("summary_comments", "")]
    return out


# ── Running the pipeline ────────────────────────────────────────────────────────


def discover_submittals() -> list[Path]:
    if not SUBMITTAL_DIR.is_dir():
        sys.exit(f"submittal folder not found: {SUBMITTAL_DIR}")
    dirs = sorted(d for d in SUBMITTAL_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
    if not dirs:
        sys.exit(f"no submittal subfolders in {SUBMITTAL_DIR}")
    return dirs


def preflight() -> None:
    """Fail before spending anything if the run can't produce a valid baseline."""
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit(
            "OPENAI_API_KEY is not set (looked in the environment and .env).\n"
            "Every LLM call would fail, and doc_processor swallows those failures into\n"
            "others/low — the run would finish and produce a confident, worthless report."
        )


def assert_pipeline_actually_ran(store_path: str | None, folder_name: str) -> None:
    """Reject a run where the LLM never succeeded.

    doc_processor catches classification exceptions and substitutes others/low with
    `reasoning="Classification failed: ..."`. A run with no API key, an expired key, or a
    rate limit therefore looks identical to a real run from the outside: all 11 nodes green,
    a full report, a RESUBMIT recommendation, every document reported missing. Checking the
    knowledge store is the only reliable way to tell the two apart, so it's done here rather
    than left to whoever reads the snapshots.
    """
    if not store_path or not Path(store_path).exists():
        sys.exit(f"{folder_name}: no knowledge store written — cannot verify the run.")

    store = json.loads(Path(store_path).read_text())
    sections = store.get("sections", [])
    if not sections:
        sys.exit(f"{folder_name}: knowledge store has no sections — nothing was classified.")

    classified = [s for s in sections if s.get("doc_type") != "others"]
    if not classified:
        sys.exit(
            f"{folder_name}: every one of {len(sections)} documents classified as "
            f"'others'.\nThat is the signature of failing LLM calls being swallowed, not a "
            f"bad submittal.\nCheck OPENAI_API_KEY, network, and rate limits — then re-run. "
            f"No snapshot was saved."
        )


def run_one(folder: Path, authority: str) -> dict:
    """Run the real pipeline over one submittal folder and return its report dict."""
    pdfs = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        sys.exit(f"no PDFs in {folder}")

    # Imported here, not at module scope, so signature()/_diff() stay importable with zero
    # pipeline dependencies installed — that's what lets tests/unit/test_baseline.py run at
    # zero OpenAI and zero AWS cost, per the session rules in notes/11.
    from adapters.pipeline.langgraph_pipeline import LangGraphReviewPipeline
    from core.models import ReviewRequest

    file_contents = {p.name: p.read_bytes() for p in pdfs}
    # Derived from filenames exactly as the API does for unlabelled uploads — see
    # apps/api/section_labels.py for why labels are not optional on scanned packages.
    # Deterministic (pure string matching), so the baseline stays reproducible.
    declared_labels = {p.name: infer_declared_label(p.name) for p in pdfs}
    labelled = sum(1 for v in declared_labels.values() if v)
    print(f"    labels: {labelled}/{len(pdfs)} matched to a section", flush=True)

    request = ReviewRequest(
        submittal_id=folder.name,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        authority=authority,
        review_date=REVIEW_DATE,
        file_contents=file_contents,
        declared_labels=declared_labels,
    )

    stages: list[str] = []

    def on_stage_complete(node: str) -> None:
        stages.append(node)
        print(f"      · {node}", flush=True)

    print(f"  → {folder.name}  ({len(pdfs)} PDFs)", flush=True)
    result = LangGraphReviewPipeline().run(request, on_stage_complete)
    assert_pipeline_actually_ran(result.knowledge_store_path, folder.name)
    return _as_dict(result.report)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


# ── Commands ────────────────────────────────────────────────────────────────────


def cmd_capture(authority: str) -> int:
    preflight()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    folders = discover_submittals()
    print(f"Capturing baseline for {len(folders)} submittals (authority={authority})\n")

    for folder in folders:
        report = run_one(folder, authority)
        snapshot = {
            "submittal": folder.name,
            "authority": authority,
            "review_date": REVIEW_DATE,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": _git_commit(),
            "signature": signature(report),
            "prose": prose(report),
            # Full report kept for eyeballing and for reconstructing a richer signature
            # later without paying for another run.
            "report": report,
        }
        path = SNAPSHOT_DIR / f"{folder.name}.json"
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
        print(f"    saved {path.relative_to(REPO_ROOT)}\n")

    print(f"Baseline captured at commit {_git_commit()}. Commit these snapshots to git.")
    return 0


def _diff(old, new, path: str = "") -> list[str]:
    """Recursive structural diff, reported as human-readable leaf paths."""
    if type(old) is not type(new):
        return [f"{path}: type {type(old).__name__} → {type(new).__name__}"]
    if isinstance(old, dict):
        out = []
        for key in sorted(set(old) | set(new)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in old:
                out.append(f"{sub}: ADDED  {new[key]!r}")
            elif key not in new:
                out.append(f"{sub}: REMOVED  {old[key]!r}")
            else:
                out.extend(_diff(old[key], new[key], sub))
        return out
    if isinstance(old, list):
        if len(old) != len(new):
            return [f"{path}: {len(old)} item(s) → {len(new)} item(s)"]
        out = []
        for i, (a, b) in enumerate(zip(old, new)):
            out.extend(_diff(a, b, f"{path}[{i}]"))
        return out
    return [] if old == new else [f"{path}: {old!r} → {new!r}"]


def cmd_check(authority: str) -> int:
    preflight()
    folders = discover_submittals()
    missing = [f.name for f in folders if not (SNAPSHOT_DIR / f"{f.name}.json").exists()]
    if missing:
        sys.exit(f"no baseline for: {', '.join(missing)}\nRun `capture` first.")

    print(f"Diffing {len(folders)} submittals against the saved snapshot (authority={authority})")
    print("Informational only — see this file's docstring for why a diff here does not, by")
    print("itself, mean your change caused it. The pipeline has measured run-to-run variance")
    print("independent of any code change (F5, notes/12_pipeline_findings.md).\n")

    decisions: dict[str, list[str]] = {}
    prose_drift: dict[str, list[str]] = {}

    for folder in folders:
        snapshot = json.loads((SNAPSHOT_DIR / f"{folder.name}.json").read_text())
        report = run_one(folder, authority)

        hard = _diff(snapshot["signature"], signature(report))
        soft = _diff(snapshot.get("prose", {}), prose(report))
        if hard:
            decisions[folder.name] = hard
        if soft:
            prose_drift[folder.name] = soft
        print(f"    {'differs' if hard else 'identical'}\n")

    if prose_drift:
        print("─" * 70)
        print("Wording drift (free text only — never decision-bearing)\n")
        for name, diffs in prose_drift.items():
            print(f"  {name}")
            for d in diffs[:10]:
                print(f"    {d}")
            if len(diffs) > 10:
                print(f"    … {len(diffs) - 10} more")
            print()

    print("─" * 70)
    if not decisions:
        print("No decision-level differences from the snapshot.")
        return 0

    print(f"{len(decisions)} submittal(s) differ from the snapshot on decision-bearing fields:\n")
    for name, diffs in decisions.items():
        print(f"  {name}")
        for d in diffs:
            print(f"    {d}")
        print()
    print("Read these yourself — this is not a pass/fail result. A difference here can be your")
    print("change, the pipeline's own variance, or both. See the module docstring.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("command", choices=["capture", "check"])
    parser.add_argument(
        "--authority",
        default="ADM",
        help="Authority profile to review against (default: ADM).",
    )
    args = parser.parse_args()
    return cmd_capture(args.authority) if args.command == "capture" else cmd_check(args.authority)


if __name__ == "__main__":
    raise SystemExit(main())
