from __future__ import annotations

from rapidfuzz import fuzz

from src.models.findings import Finding, Severity

_STAGE = "consistency_check"

# Score threshold: below this, names are considered different (potential inconsistency).
# 85 allows for common abbreviation variants (e.g. "LLC" vs "L.L.C.") and OCR noise.
_FUZZY_THRESHOLD = 85


def fuzzy_match(name_a: str, name_b: str, threshold: int = _FUZZY_THRESHOLD) -> bool:
    """Return True if name_a and name_b are similar enough to be the same entity."""
    if not name_a or not name_b:
        return True  # can't check — not an inconsistency
    score = fuzz.token_sort_ratio(name_a.strip(), name_b.strip())
    return score >= threshold


def check_name_consistency(
    entity_name: str,
    entity_label: str,
    doc_texts: dict[str, str],  # {filename: text} — pre-filtered by caller
) -> list[Finding]:
    """
    Check that entity_name (e.g. manufacturer name from cover page) appears
    consistently across the provided documents.
    Uses rapidfuzz token_sort_ratio to handle OCR noise and abbreviation variants.
    doc_texts is pre-filtered to relevant doc types by the calling agent.
    """
    findings: list[Finding] = []

    for filename, text in doc_texts.items():
        if not text:
            continue
        # partial_ratio checks if entity_name appears as a near-substring anywhere in the text.
        # This handles honorifics ("M/s."), all-caps variants, and OCR noise robustly.
        score = fuzz.partial_ratio(entity_name.lower(), text.lower())
        if score < _FUZZY_THRESHOLD:
            findings.append(Finding(
                stage=_STAGE,
                document=filename,
                description=(
                    f"{entity_label} name '{entity_name}' (from cover page) "
                    f"may not match what appears in '{filename}'."
                ),
                severity=Severity.WARNING,
                action_required=(
                    f"Verify {entity_label.lower()} name is consistent across all documents."
                ),
            ))

    return findings


def check_manufacturer_consistency(
    manufacturer_name: str,
    doc_texts: dict[str, str],
) -> list[Finding]:
    return check_name_consistency(manufacturer_name, "Manufacturer", doc_texts)


def check_supplier_consistency(
    supplier_name: str,
    doc_texts: dict[str, str],
) -> list[Finding]:
    return check_name_consistency(supplier_name, "Supplier", doc_texts)
