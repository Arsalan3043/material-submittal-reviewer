from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import NamedTuple

from src.models.findings import Finding, Severity

_STAGE = "validity_check"

# Common date patterns in UAE construction documents.
_DATE_PATTERNS = [
    r"\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})\b",   # DD/MM/YYYY or DD-MM-YYYY
    r"\b(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})\b",   # YYYY-MM-DD
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b",
    r"\bExpir(?:y|es?|ation)(?: Date)?[:\s]+(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})\b",
]

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class _DateMatch(NamedTuple):
    value: date
    label: str  # "expiry", "issue", "test_date", etc.


def _parse_date(text: str, idx: int) -> date | None:
    """Try to parse a date from a regex match group tuple."""
    try:
        parts = [p for p in text if p]
        if len(parts) == 3:
            a, b, c = parts[0], parts[1], parts[2]
            # Month abbreviation
            if a.lower() in _MONTH_ABBR:
                return date(_int(c), _MONTH_ABBR[a.lower()], _int(b))
            if b.lower() in _MONTH_ABBR:
                return date(_int(c), _MONTH_ABBR[b.lower()], _int(a))
            # Numeric — detect YYYY-MM-DD vs DD/MM/YYYY
            ia, ib, ic = _int(a), _int(b), _int(c)
            if ia > 31:
                return date(ia, ib, ic)          # YYYY-MM-DD
            return date(ic, ib, ia)              # DD/MM/YYYY
    except (ValueError, TypeError):
        return None
    return None


def _int(s: str) -> int:
    return int(s.strip())


def _extract_dates(text: str) -> list[date]:
    found: list[date] = []
    for pattern in _DATE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            d = _parse_date(m.groups(), 0)
            if d and date(2000, 1, 1) <= d <= date(2040, 12, 31):
                found.append(d)
    return sorted(set(found))


def check_ded_registration(text: str, document: str, today: date | None = None) -> list[Finding]:
    """
    Check DED registration certificate for expiry.
    Rule: expiry date must be in the future.
    """
    today = today or date.today()
    findings: list[Finding] = []
    dates = _extract_dates(text)

    expiry_keywords = ["expiry", "expires", "valid until", "valid to", "expiration"]
    expiry_date: date | None = None

    for kw in expiry_keywords:
        match = re.search(
            rf"{kw}[:\s]+(\d{{1,2}})[\/\-\.](\d{{1,2}})[\/\-\.](\d{{4}})",
            text, re.IGNORECASE,
        )
        if match:
            d = _parse_date(match.groups(), 0)
            if d:
                expiry_date = d
                break

    if expiry_date is None and dates:
        # Use the latest date found as the likely expiry
        expiry_date = max(dates)

    if expiry_date is None:
        findings.append(Finding(
            stage=_STAGE,
            document=document,
            description="DED registration: could not extract expiry date from certificate.",
            severity=Severity.WARNING,
            action_required="Verify DED registration expiry date manually.",
        ))
        return findings

    if expiry_date < today:
        findings.append(Finding(
            stage=_STAGE,
            document=document,
            description=f"DED registration expired on {expiry_date.isoformat()}.",
            severity=Severity.CRITICAL,
            action_required="Submit a current, valid DED registration certificate.",
        ))
    elif expiry_date < today + timedelta(days=30):
        findings.append(Finding(
            stage=_STAGE,
            document=document,
            description=f"DED registration expires soon ({expiry_date.isoformat()}).",
            severity=Severity.WARNING,
            action_required="Verify DED registration will remain valid during project execution.",
        ))

    return findings


def check_test_report(
    text: str,
    document: str,
    max_age_days: int = 3 * 365,
    today: date | None = None,
) -> list[Finding]:
    """
    Check test report age. ADM requires test reports be no older than 3 years.
    """
    today = today or date.today()
    findings: list[Finding] = []
    dates = _extract_dates(text)

    if not dates:
        findings.append(Finding(
            stage=_STAGE,
            document=document,
            description="Test report: could not extract test date.",
            severity=Severity.WARNING,
            action_required="Verify test report date and ensure it is within the required period.",
        ))
        return findings

    # Use the most recent date as the test/issue date
    report_date = max(dates)
    age_days = (today - report_date).days

    if age_days > max_age_days:
        years = max_age_days // 365
        findings.append(Finding(
            stage=_STAGE,
            document=document,
            description=(
                f"Test report dated {report_date.isoformat()} is "
                f"{age_days // 365} year(s) old (maximum allowed: {years} years)."
            ),
            severity=Severity.CRITICAL,
            action_required=f"Submit test reports issued within the last {years} years.",
        ))

    return findings


def check_guarantee(
    text: str,
    document: str,
    required_years: int | None = None,
) -> list[Finding]:
    """
    Check manufacturer/supplier guarantee.
    Extracts stated guarantee period and compares against spec requirement.
    """
    findings: list[Finding] = []

    period_match = re.search(
        r"(\d+)\s*(year|yr|month|mo)",
        text, re.IGNORECASE,
    )

    if period_match is None:
        findings.append(Finding(
            stage=_STAGE,
            document=document,
            description="Guarantee document: could not determine guarantee period.",
            severity=Severity.WARNING,
            action_required="Verify guarantee period meets specification requirements.",
        ))
        return findings

    value = int(period_match.group(1))
    unit = period_match.group(2).lower()
    period_years = value if unit.startswith("y") else value / 12

    if required_years and period_years < required_years:
        findings.append(Finding(
            stage=_STAGE,
            document=document,
            description=(
                f"Guarantee period ({value} {unit}s) is below the required "
                f"{required_years} years."
            ),
            severity=Severity.CRITICAL,
            action_required=f"Provide guarantee for minimum {required_years} years.",
        ))

    return findings
