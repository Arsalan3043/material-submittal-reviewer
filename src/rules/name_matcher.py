from __future__ import annotations

from rapidfuzz import fuzz

from src.models.findings import Finding, Severity
from src.models.submittal import ClassifiedDocument, DocType

_STAGE = "consistency_check"

# Score threshold: below this, names are considered different (potential inconsistency).
# 85 allows for common abbreviation variants (e.g. "LLC" vs "L.L.C.") and OCR noise.
_FUZZY_THRESHOLD = 85

# Document types where the manufacturer name is expected to appear.
_MANUFACTURER_DOC_TYPES = frozenset([
    DocType.TECHNICAL_DATASHEET,
    DocType.TEST_REPORT,
    DocType.MANUFACTURER_GUARANTEE,
    DocType.DED_REGISTRATION,
    DocType.MSDF,
    DocType.COMPARISON_TABLE,
])

_SUPPLIER_DOC_TYPES = frozenset([
    DocType.DED_REGISTRATION,
    DocType.MSDF,
    DocType.COVER_PAGE,
])


def fuzzy_match(name_a: str, name_b: str, threshold: int = _FUZZY_THRESHOLD) -> bool:
    """Return True if name_a and name_b are similar enough to be the same entity."""
    if not name_a or not name_b:
        return True  # can't check — not an inconsistency
    score = fuzz.token_sort_ratio(name_a.strip(), name_b.strip())
    return score >= threshold


def check_name_consistency(
    entity_name: str,
    entity_label: str,
    classified_docs: dict[str, ClassifiedDocument],
    doc_types_to_check: frozenset[DocType],
) -> list[Finding]:
    """
    Check that entity_name (e.g. manufacturer name from cover page) appears
    consistently in all relevant documents.
    Uses rapidfuzz token_sort_ratio to handle OCR noise and abbreviation variants.
    Ambiguous cases (score near threshold) are escalated to AI in the agent layer.
    """
    findings: list[Finding] = []

    for filename, doc in classified_docs.items():
        if doc.doc_type not in doc_types_to_check:
            continue
        if not doc.text_preview:
            continue

        if not fuzzy_match(entity_name, _extract_entity_from_preview(entity_name, doc.text_preview)):
            # rapidfuzz found low similarity — flag for review
            findings.append(Finding(
                stage=_STAGE,
                document=filename,
                description=(
                    f"{entity_label} name '{entity_name}' (from cover page) "
                    f"may not match what appears in {doc.doc_type.value} '{filename}'."
                ),
                severity=Severity.WARNING,
                action_required=(
                    f"Verify {entity_label.lower()} name is consistent across all documents."
                ),
            ))

    return findings


def _extract_entity_from_preview(entity_name: str, text_preview: str) -> str:
    """
    Extract the best candidate name from text_preview to compare against entity_name.
    Simple approach: return the longest line that shares at least one keyword with entity_name.
    """
    keywords = set(entity_name.lower().split())
    best_line = ""
    best_overlap = 0
    for line in text_preview.splitlines():
        line_words = set(line.lower().split())
        overlap = len(keywords & line_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_line = line
    return best_line if best_line else text_preview[:80]


def check_manufacturer_consistency(
    manufacturer_name: str,
    classified_docs: dict[str, ClassifiedDocument],
) -> list[Finding]:
    return check_name_consistency(
        entity_name=manufacturer_name,
        entity_label="Manufacturer",
        classified_docs=classified_docs,
        doc_types_to_check=_MANUFACTURER_DOC_TYPES,
    )


def check_supplier_consistency(
    supplier_name: str,
    classified_docs: dict[str, ClassifiedDocument],
) -> list[Finding]:
    return check_name_consistency(
        entity_name=supplier_name,
        entity_label="Supplier",
        classified_docs=classified_docs,
        doc_types_to_check=_SUPPLIER_DOC_TYPES,
    )
