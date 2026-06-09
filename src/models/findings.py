from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, model_validator


class Severity(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    CRITICAL = "critical"


class Finding(BaseModel):
    stage: str
    document: str
    description: str
    severity: Severity
    action_required: str


class TableRowFinding(BaseModel):
    parameter: str
    specified_value: str
    proposed_value: str
    deviation_declared: str
    measured_value: str
    specified_correct: bool
    proposed_verified: bool
    measured_verified: bool
    deviation_accurate: bool
    missing_from_spec: bool
    finding: str
    severity: Severity


class ReviewReport(BaseModel):
    submittal_id: str
    authority: str
    material_description: str
    spec_clause: str
    review_date: str
    completeness_findings: list[Finding]
    boq_drawing_findings: list[Finding]
    spec_verification_findings: list[Finding]
    validity_findings: list[Finding]
    avl_findings: list[Finding]
    statement_findings: list[Finding]
    table_audit_findings: list[TableRowFinding]
    consistency_findings: list[Finding]
    others_findings: list[Finding]
    critical_count: int = 0
    warning_count: int = 0
    missing_documents: list[str]
    overall_recommendation: Literal["APPROVE", "CONDITIONAL", "RESUBMIT"]
    summary_comments: str

    @model_validator(mode="after")
    def _compute_counts(self) -> ReviewReport:
        standard_findings: list[Finding] = (
            self.completeness_findings
            + self.boq_drawing_findings
            + self.spec_verification_findings
            + self.validity_findings
            + self.avl_findings
            + self.statement_findings
            + self.consistency_findings
            + self.others_findings
        )
        self.critical_count = sum(
            1 for f in standard_findings if f.severity == Severity.CRITICAL
        ) + sum(
            1 for r in self.table_audit_findings if r.severity == Severity.CRITICAL
        )
        self.warning_count = sum(
            1 for f in standard_findings if f.severity == Severity.WARNING
        ) + sum(
            1 for r in self.table_audit_findings if r.severity == Severity.WARNING
        )
        return self
