"""
Phase 2 — Experiment C: Table Audit Detection Rate Test

Tests GPT-4o-mini's ability to detect deliberate mistakes in comparison table rows
by cross-referencing the row against spec context, datasheet context, and test
report context.

Material used: Detectable Warning Tape for Irrigation Pipelines (Kangaroo Plastics ME LLC)
Source:        Real values from submittal_02 spec, datasheet, and test report OCR.

10 test cases:
  3 correct rows   (expect PASS  — no false positives)
  7 rows with      (expect FLAG  — detection rate ≥ 85%)
    planted errors

Error types planted:
  T02 — Specified value wrong in table (quotes wrong spec value)
  T03 — Proposed value below specification minimum
  T04 — Deviation not declared when values differ
  T05 — Measured value does not match test report
  T06 — Compliance claim wrong (proposed ≠ specified, remarks say "Comply")
  T07 — Proposed value not supported by datasheet
  T09 — Deviation declared but magnitude is inaccurate

Evaluation:
  Detection rate     = errors correctly flagged  / total rows with errors  (target ≥85%)
  False positive rate = clean rows incorrectly flagged / total clean rows

Run from project root:
    python experiments/llm/audit_accuracy_test.py

Results saved to:
    experiments/llm/results/audit_results.json
"""

from __future__ import annotations

import json
from pathlib import Path
from enum import Enum

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

MODEL = "gpt-4o-mini"

RESULTS_DIR = Path("experiments/llm/results")


_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


# ── Pydantic Output Models ─────────────────────────────────────────────────────

class AuditSeverity(str, Enum):
    PASS     = "pass"
    WARNING  = "warning"
    CRITICAL = "critical"


class CheckType(str, Enum):
    SPECIFIED_CHECK  = "specified_check"   # table's specified value vs actual spec
    PROPOSED_CHECK   = "proposed_check"    # table's proposed value vs datasheet
    MEASURED_CHECK   = "measured_check"    # table's measured value vs test report
    DEVIATION_CHECK  = "deviation_check"   # deviation declared vs actual difference
    COMPLIANCE_CHECK = "compliance_check"  # remarks/compliance claim vs values


class AuditFinding(BaseModel):
    check_type: CheckType
    severity:   AuditSeverity
    finding:    str   # clear description of the issue
    expected:   str   # what was expected
    found:      str   # what was found in the table


class RowAuditResult(BaseModel):
    parameter:      str
    overall_status: AuditSeverity
    findings:       list[AuditFinding]
    notes:          str


# ── 10 Test Cases ──────────────────────────────────────────────────────────────
# All values derived from real submittal_02 documents:
#   Spec:      Section 24.3.3 Detectable Underground Tape — Irrigation Systems Volume III
#   Datasheet: Kangaroo Plastics ME LLC — Technical Data Sheet
#   Test:      Kangaroo Plastics ME LLC — Test Report (Aug 2023)

TEST_CASES = [
    # ── T01 — PASS ──────────────────────────────────────────────────────────────
    {
        "id": "T01",
        "description": "Width — all values correct and consistent (expect PASS)",
        "row": {
            "parameter": "Width",
            "specified":  "150mm minimum",
            "proposed":   "150mm",
            "deviation":  "",
            "measured":   "150mm",
            "remarks":    "Comply",
        },
        "spec_context": (
            "The tape shall be not less than 150mm wide and shall have the phrase "
            "'CAUTION — IRRIGATION PIPELINE AND CONTROL CABLES' in English and Arabic "
            "stamped in black letters and repeated at maximum intervals of two meters."
        ),
        "datasheet_context": (
            "Product: Detectable Warning Tape. "
            "Dimension: 150mm Width x 250 meter Length. "
            "Dimensional Tolerance: +10%."
        ),
        "test_report_context": (
            "Width: 150mm — within specification. "
            "Length: 250 meter. Thickness: 150 micron."
        ),
        "has_error":            False,
        "expected_check_types": [],
    },

    # ── T02 — CRITICAL: Specified value wrong ────────────────────────────────────
    {
        "id": "T02",
        "description": "Width — specified value in table says 100mm, but spec requires 150mm minimum (CRITICAL)",
        "row": {
            "parameter": "Width",
            "specified":  "100mm minimum",    # WRONG — spec says 150mm
            "proposed":   "150mm",
            "deviation":  "",
            "measured":   "150mm",
            "remarks":    "Comply",
        },
        "spec_context": (
            "The tape shall be not less than 150mm wide and shall have the phrase "
            "'CAUTION — IRRIGATION PIPELINE AND CONTROL CABLES' in English and Arabic "
            "stamped in black letters and repeated at maximum intervals of two meters."
        ),
        "datasheet_context": (
            "Product: Detectable Warning Tape. "
            "Dimension: 150mm Width x 250 meter Length."
        ),
        "test_report_context": "Width: 150mm — Pass.",
        "has_error":            True,
        "expected_check_types": ["specified_check"],
    },

    # ── T03 — CRITICAL: Proposed below minimum, compliance wrong ────────────────
    {
        "id": "T03",
        "description": "Width — proposed 130mm is below 150mm minimum; compliance claim 'Comply' is wrong (CRITICAL)",
        "row": {
            "parameter": "Width",
            "specified":  "150mm minimum",
            "proposed":   "130mm",            # BELOW MINIMUM
            "deviation":  "",
            "measured":   "130mm",
            "remarks":    "Comply",           # WRONG — does not comply
        },
        "spec_context": (
            "The tape shall be not less than 150mm wide."
        ),
        "datasheet_context": (
            "Product width: 130mm (available as special order, non-standard width)."
        ),
        "test_report_context": (
            "Width: 130mm. Note: below the 150mm minimum required by specification."
        ),
        "has_error":            True,
        "expected_check_types": ["proposed_check", "compliance_check"],
    },

    # ── T04 — WARNING: Deviation exists but not declared ────────────────────────
    {
        "id": "T04",
        "description": "Thickness — proposed 120 microns differs from specified 150 microns, but no deviation declared (WARNING)",
        "row": {
            "parameter": "Thickness",
            "specified":  "150 microns",
            "proposed":   "120 microns",      # DIFFERS from specified
            "deviation":  "",                 # MISSING — should declare deviation
            "measured":   "120 microns",
            "remarks":    "Comply",
        },
        "spec_context": (
            "Standard film thickness: 150 microns minimum. "
            "Composition: 12 micron PET + 12 micron Al foil + 126 micron PE laminate."
        ),
        "datasheet_context": (
            "Standard Thickness: 150 μm (12μm PET + 12μm Al foil + 126μm PE laminate). "
            "The product supplied has thickness 120 microns."
        ),
        "test_report_context": "Thickness measured: 120 microns.",
        "has_error":            True,
        "expected_check_types": ["deviation_check"],
    },

    # ── T05 — CRITICAL: Measured value does not match test report ───────────────
    {
        "id": "T05",
        "description": "Elongation — table says measured 700%, but test report records 550% MD (CRITICAL)",
        "row": {
            "parameter": "Elongation at Break (MD)",
            "specified":  "400% minimum (longitudinal)",
            "proposed":   "550%",
            "deviation":  "",
            "measured":   "700%",             # WRONG — test report says 550%
            "remarks":    "Comply",
        },
        "spec_context": (
            "Elongation at break: minimum 400% longitudinal direction per BS-EN ISO 527-3."
        ),
        "datasheet_context": (
            "Elongation: MD 400% (PE only), TM 500% (PE only)."
        ),
        "test_report_context": (
            "Elongation per BS-EN ISO 527-3:1996 — "
            "MD (Machine Direction): 550% — Pass (above 400% minimum). "
            "TD (Transverse Direction): 620%."
        ),
        "has_error":            True,
        "expected_check_types": ["measured_check"],
    },

    # ── T06 — CRITICAL: Compliance claim wrong (wrong colour proposed) ───────────
    {
        "id": "T06",
        "description": "Colour — proposed Blue does not match specified Yellow; remarks say 'Comply' incorrectly (CRITICAL)",
        "row": {
            "parameter": "Color",
            "specified":  "Yellow",
            "proposed":   "Blue",             # WRONG COLOUR
            "deviation":  "",
            "measured":   "",
            "remarks":    "Comply",           # WRONG — Blue ≠ Yellow
        },
        "spec_context": (
            "Tape colour shall be Yellow for irrigation pipeline marking. "
            "Tapes shall remain legible and colour fast in soil conditions at pH 2.5 to 11.0."
        ),
        "datasheet_context": (
            "Color/Shade: Yellow. "
            "Standard product colour for irrigation pipeline tape is Yellow. "
            "Product supplied: Blue."
        ),
        "test_report_context": "Colour inspected: Blue.",
        "has_error":            True,
        "expected_check_types": ["proposed_check", "compliance_check"],
    },

    # ── T07 — CRITICAL: Proposed value not supported by datasheet ───────────────
    {
        "id": "T07",
        "description": "Roll Length — proposed 300m is not available per datasheet; standard is 250m only (CRITICAL)",
        "row": {
            "parameter": "Length per Roll",
            "specified":  "250 meters minimum",
            "proposed":   "300 meters",       # NOT IN DATASHEET
            "deviation":  "",
            "measured":   "",
            "remarks":    "Comply",
        },
        "spec_context": (
            "Tape shall be supplied in rolls with minimum length of 250 meters per roll."
        ),
        "datasheet_context": (
            "Dimension: 150mm Width x 250 meter Length (standard). "
            "Custom roll lengths are not available. "
            "Only standard 250 meter rolls are manufactured."
        ),
        "test_report_context": "Length: 250 meters (standard roll).",
        "has_error":            True,
        "expected_check_types": ["proposed_check"],
    },

    # ── T08 — PASS ──────────────────────────────────────────────────────────────
    {
        "id": "T08",
        "description": "Tensile Strength — proposed and measured values exceed specification minimum (expect PASS)",
        "row": {
            "parameter": "Tensile Strength (MD)",
            "specified":  "140 kg/cm² minimum (longitudinal)",
            "proposed":   "158 kg/cm² (MD)",
            "deviation":  "",
            "measured":   "158 kg/cm²",
            "remarks":    "Comply",
        },
        "spec_context": (
            "Tensile strength: minimum 140 kg/cm² longitudinal direction per BS-EN ISO 527-3. "
            "Tapes must remain intact and functional under normal burial and excavation conditions."
        ),
        "datasheet_context": (
            "Tensile Strength: MD 14 N/mm² (≈143 kg/cm²), T 12 N/mm² (≈122 kg/cm²). "
            "Test standard: BS-EN ISO 527-3."
        ),
        "test_report_context": (
            "Tensile Strength per BS-EN ISO 527-3:1996 — "
            "MD: 158 kg/cm² — Pass (above 140 kg/cm² minimum). "
            "TD: 146 kg/cm²."
        ),
        "has_error":            False,
        "expected_check_types": [],
    },

    # ── T09 — WARNING: Deviation declared but magnitude is inaccurate ────────────
    {
        "id": "T09",
        "description": "Tear Strength (TD) — deviation declared as '10% below specification' but actual deviation is 23% (WARNING)",
        "row": {
            "parameter": "Tear Strength (Transverse)",
            "specified":  "260 gf minimum",
            "proposed":   "200 gf",
            "deviation":  "10% below specification",  # INACCURATE — actual is ~23%
            "measured":   "200 gf",
            "remarks":    "Comply with approved deviation",
        },
        "spec_context": (
            "Tear strength: minimum 260 gf transverse direction per BS-2782 Part-3 360-A."
        ),
        "datasheet_context": (
            "Tear Strength: MD 220 gf, T 200 gf per BS-2782."
        ),
        "test_report_context": (
            "Tear Strength per BS-2782 PART-3 360-A:1991 — "
            "MD: 392 gf (Pass). TD: 200 gf. "
            "Note: TD value is below the 260 gf minimum."
        ),
        "has_error":            True,
        "expected_check_types": ["deviation_check"],
    },

    # ── T10 — PASS ──────────────────────────────────────────────────────────────
    {
        "id": "T10",
        "description": "Chemical Resistance — all values consistent with spec, datasheet, and test report (expect PASS)",
        "row": {
            "parameter": "Chemical Resistance",
            "specified":  "No effect at pH 2.5 to 11.0 soil conditions",
            "proposed":   "Resistant to acids and alkalis; no effect in standard soil chemicals",
            "deviation":  "",
            "measured":   "No major changes after 168 hrs in pH 2.5 and pH 11.0 solutions",
            "remarks":    "Comply",
        },
        "spec_context": (
            "Tapes shall remain legible and colour fast in soil conditions at pH values "
            "of 2.5 to 11.0 inclusive. The tapes shall be of the type specially manufactured "
            "for making and locating underground utilities."
        ),
        "datasheet_context": (
            "Chemical Resistance: No Effect — Water, Alcohol Mix, Oil, 10% NaCl, "
            "5% Acetic Acid, 5% NaOH. "
            "Printing remains unaffected in all conditions. "
            "Tapes are resistant to environmental stress and sub-soil chemicals."
        ),
        "test_report_context": (
            "Chemical Resistance per ASTM D543-06: "
            "Specimens immersed in pH 2.5 sulphuric acid and pH 11 ammonium hydroxide for 168 hrs. "
            "Result: No major changes in surface, legible after immersion. Pass."
        ),
        "has_error":            False,
        "expected_check_types": [],
    },
]


# ── Audit Prompt ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a technical auditor for UAE construction material submittals.

Your task: audit ONE comparison table row by cross-referencing it against the
specification, the manufacturer's datasheet, and the test report.

You will be given:
  - The comparison table row (what the contractor submitted)
  - Specification context (what the authority specification actually requires)
  - Datasheet context (what the manufacturer's datasheet states)
  - Test report context (what the laboratory test results show)

Perform ALL five checks and report findings for any that fail:

CHECK 1 — specified_check
  Is the "specified" value in the table consistent with the specification?
  Flag if the table's specified value is wrong, too low, too high, or misquotes the spec.

CHECK 2 — proposed_check
  Is the "proposed" value supported by the datasheet AND does it meet the specification?
  Flag if: (a) proposed value contradicts the datasheet, OR
           (b) proposed value does not meet the specification minimum/maximum.

CHECK 3 — measured_check
  Is the "measured" value in the table consistent with the test report?
  Flag if the table's measured value differs from the test report result.
  Note: if measured is empty AND the test report says the property was not tested, that is acceptable.

CHECK 4 — deviation_check
  If proposed value differs from specified value:
    - Is a deviation declared? If not → flag (WARNING).
    - If a deviation IS declared, is the magnitude/description accurate? If not → flag (WARNING).

CHECK 5 — compliance_check
  Is the "remarks" / compliance claim accurate?
  Flag if: the row says "Comply" but the proposed value does not actually meet the specification.

Severity rules:
  critical — value is definitively wrong, compliance is false, or required value is not met
  warning  — deviation undeclared, minor inconsistency, declared deviation is inaccurate

Return JSON only:
{
  "parameter": "<parameter name>",
  "overall_status": "pass" | "warning" | "critical",
  "findings": [
    {
      "check_type": "specified_check" | "proposed_check" | "measured_check" | "deviation_check" | "compliance_check",
      "severity": "warning" | "critical",
      "finding": "<clear description of the issue>",
      "expected": "<what was expected>",
      "found": "<what was found in the table>"
    }
  ],
  "notes": "<any additional observation>"
}

If no issues are found → return empty findings list and overall_status "pass"."""


def audit_row(case: dict) -> RowAuditResult:
    row = case["row"]
    user_msg = f"""Audit this comparison table row:

PARAMETER: {row['parameter']}
SPECIFIED:  {row['specified']}
PROPOSED:   {row['proposed']}
DEVIATION:  {row['deviation'] or '(none declared)'}
MEASURED:   {row['measured'] or '(not provided)'}
REMARKS:    {row['remarks']}

SPECIFICATION CONTEXT:
{case['spec_context']}

DATASHEET CONTEXT:
{case['datasheet_context']}

TEST REPORT CONTEXT:
{case['test_report_context']}"""

    response = _openai().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = response.choices[0].message.content
    try:
        return RowAuditResult.model_validate_json(raw)
    except (ValidationError, ValueError):
        parsed = json.loads(raw)
        findings = []
        for f in parsed.get("findings", []):
            try:
                findings.append(AuditFinding(
                    check_type=f.get("check_type", CheckType.COMPLIANCE_CHECK),
                    severity=f.get("severity", AuditSeverity.WARNING),
                    finding=f.get("finding", ""),
                    expected=f.get("expected", ""),
                    found=f.get("found", ""),
                ))
            except Exception:
                pass
        status_raw = parsed.get("overall_status", "pass")
        return RowAuditResult(
            parameter=parsed.get("parameter", row["parameter"]),
            overall_status=AuditSeverity(status_raw) if status_raw in ("pass", "warning", "critical") else AuditSeverity.PASS,
            findings=findings,
            notes=parsed.get("notes", "parse fallback"),
        )


# ── Evaluation ─────────────────────────────────────────────────────────────────

def run_audit_test() -> dict:
    print("\n=== Experiment C: Table Audit Detection Rate ===\n")
    print(f"  Model    : {MODEL}")
    print(f"  Material : Detectable Warning Tape (Kangaroo Plastics ME LLC)\n")

    error_cases   = [c for c in TEST_CASES if c["has_error"]]
    correct_cases = [c for c in TEST_CASES if not c["has_error"]]

    detected       = 0   # errors correctly flagged
    false_positives = 0  # clean rows incorrectly flagged
    results = []

    print(f"  {'ID':<4} {'Has Error':<10} {'Result':<10} {'Status':<10} {'Findings'}")
    print(f"  {'-'*4} {'-'*10} {'-'*10} {'-'*10} {'-'*40}")

    for case in TEST_CASES:
        audit = audit_row(case)
        has_error    = case["has_error"]
        was_flagged  = audit.overall_status != AuditSeverity.PASS
        correct_call = (has_error and was_flagged) or (not has_error and not was_flagged)

        if has_error and was_flagged:
            detected += 1
        if not has_error and was_flagged:
            false_positives += 1

        outcome = "✓ CORRECT" if correct_call else "✗ MISSED" if has_error else "✗ FALSE+"
        finding_summary = " | ".join(f.check_type.value for f in audit.findings) or "—"

        print(f"  {case['id']:<4} {'ERROR' if has_error else 'CLEAN':<10} {outcome:<10} {audit.overall_status.value.upper():<10} {finding_summary}")

        # Detail on failures or unexpected results
        if not correct_call:
            print(f"       Description : {case['description']}")
            if audit.findings:
                for f in audit.findings:
                    print(f"       [{f.severity.value.upper()}] {f.check_type.value}: {f.finding}")
            else:
                print(f"       (No findings returned — expected: {case['expected_check_types']})")

        results.append({
            "id":               case["id"],
            "description":      case["description"],
            "has_error":        has_error,
            "was_flagged":      was_flagged,
            "correct_call":     correct_call,
            "overall_status":   audit.overall_status.value,
            "findings":         [f.model_dump() for f in audit.findings],
            "notes":            audit.notes,
            "expected_checks":  case["expected_check_types"],
        })

    # Metrics
    n_errors   = len(error_cases)
    n_clean    = len(correct_cases)
    detect_rate  = detected / n_errors if n_errors else 0.0
    fp_rate      = false_positives / n_clean if n_clean else 0.0

    print(f"\n  {'─'*60}")
    print("  SUMMARY")
    print(f"  {'─'*60}")
    print(f"  Error cases          : {n_errors}")
    print(f"  Errors detected      : {detected}/{n_errors}  →  Detection rate  {detect_rate:.1%}")
    print(f"  Clean cases          : {n_clean}")
    print(f"  False positives      : {false_positives}/{n_clean}  →  False positive rate {fp_rate:.1%}")
    print(f"  Target detection rate: ≥85%  →  {'PASS ✓' if detect_rate >= 0.85 else 'MISS ✗'}")

    return {
        "model":                MODEL,
        "material":             "Detectable Warning Tape (Kangaroo Plastics ME LLC)",
        "total_cases":          len(TEST_CASES),
        "error_cases":          n_errors,
        "detected":             detected,
        "detection_rate":       round(detect_rate, 4),
        "clean_cases":          n_clean,
        "false_positives":      false_positives,
        "false_positive_rate":  round(fp_rate, 4),
        "target_met":           detect_rate >= 0.85,
        "results":              results,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Phase 2 — Experiment C: Table Audit Detection Rate")
    print(f"Model : {MODEL}")
    print("=" * 60)

    summary = run_audit_test()

    output_path = RESULTS_DIR / "audit_results.json"
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n  Full results → {output_path}")
