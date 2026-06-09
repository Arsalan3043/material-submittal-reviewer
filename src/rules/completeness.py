from __future__ import annotations

from src.config.base_profile import AuthorityProfile
from src.models.findings import Finding, Severity
from src.models.submittal import ClassifiedDocument, DocType

_STAGE = "completeness_check"

# Human-readable label for each DocType in findings reports.
_DOCTYPE_LABELS: dict[DocType, str] = {
    DocType.COVER_PAGE:             "Cover page",
    DocType.MSDF:                   "Material Source Declaration Form (MSDF)",
    DocType.SPECIFICATION_COPY:     "Copies of relevant specification sections",
    DocType.BOQ:                    "Bill of Quantities (BOQ)",
    DocType.DRAWING:                "Engineering drawings",
    DocType.COMPARISON_TABLE:       "Technical comparison table",
    DocType.TECHNICAL_DATASHEET:    "Manufacturer's technical data / catalogue",
    DocType.TEST_REPORT:            "Test reports and certificates",
    DocType.DED_REGISTRATION:       "DED registration certificate",
    DocType.MANUFACTURER_GUARANTEE: "Manufacturer/supplier guarantee",
    DocType.PREVIOUS_APPROVAL:      "Previous approvals",
    DocType.METHOD_STATEMENT:       "Applicator's method statement",
    DocType.MAF:                    "Material Approval Form (MAF)",
    DocType.OTHERS:                 "Other documents",
}


def check_completeness(
    classified_docs: dict[str, ClassifiedDocument],
    profile: AuthorityProfile,
) -> tuple[list[Finding], list[str]]:
    """
    Compare classified documents against the authority's required list.
    Returns (findings, missing_document_labels).

    Rules from Experiment A:
    - maf and previous_approval are both acceptable for Index 8.
    - others section has no expected type — never flagged as wrong or missing.
    - Review always continues regardless of missing documents (CLAUDE.md rule).
    """
    present_types: set[DocType] = {doc.doc_type for doc in classified_docs.values()}

    # maf and previous_approval are interchangeable for Index 8 (UAE convention)
    if DocType.MAF in present_types:
        present_types.add(DocType.PREVIOUS_APPROVAL)
    if DocType.PREVIOUS_APPROVAL in present_types:
        present_types.add(DocType.MAF)

    findings: list[Finding] = []
    missing_labels: list[str] = []

    for required_type in profile.required_doc_types:
        if required_type not in present_types:
            label = _DOCTYPE_LABELS.get(required_type, required_type.value)
            missing_labels.append(label)
            findings.append(Finding(
                stage=_STAGE,
                document="Submittal package",
                description=f"Missing required document: {label}.",
                severity=Severity.CRITICAL,
                action_required=f"Include {label} in resubmission.",
            ))

    # Flag documents placed in the wrong section (mismatch_flagged = True),
    # except for the known maf/previous_approval Index 8 convention.
    for doc in classified_docs.values():
        if doc.mismatch_flagged:
            findings.append(Finding(
                stage=_STAGE,
                document=doc.filename,
                description=(
                    f"Wrong document type in section '{doc.declared_label}': "
                    f"expected section content but found {doc.doc_type.value}."
                ),
                severity=Severity.WARNING,
                action_required=f"Move {doc.filename} to the correct section.",
            ))

    return findings, missing_labels
