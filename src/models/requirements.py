from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.models.submittal import DocType


# ── Enums ──────────────────────────────────────────────────────────────────────


class RequirementType(str, Enum):
    """Semantic classification of what a spec requirement is checking.

    Used by the verification engine to route to the right verification logic
    AND to infer default evidence sources and comparison_table_required.
    """
    DIMENSION    = "dimension"    # physical measurement: "wall thickness ≥ 6 mm"
    STANDARD     = "standard"     # code/standard compliance: "comply with BS 6920"
    MATERIAL     = "material"     # material type/grade: "HDPE PE100"
    TEST         = "test"         # requires a lab test result: "tested to ASTM C39"
    CERTIFICATE  = "certificate"  # third-party certification: "NSF 61 certified"
    APPROVAL     = "approval"     # authority approval: "previously approved by ADM"
    PERFORMANCE  = "performance"  # performance value without mandatory test: "tensile strength ≥ 500 N"
    INSTALLATION = "installation" # how/where to install: "laid 300 mm above pipeline"
    EXPERIENCE   = "experience"   # track record: "5 years proven use in UAE"
    ADMINISTRATIVE = "administrative"  # doc submission: "contractor shall submit"
    WARRANTY     = "warranty"     # guarantee period: "10-year warranty required"
    PROCEDURAL   = "procedural"   # process requirement: "method statement required"
    OTHER        = "other"


class VerificationStatus(str, Enum):
    SATISFIED         = "satisfied"
    NON_COMPLIANT     = "non_compliant"
    PARTIALLY_VERIFIED = "partially_verified"
    MISSING_EVIDENCE  = "missing_evidence"
    NOT_APPLICABLE    = "not_applicable"   # requirement doesn't apply to this material


class VerificationMethod(str, Enum):
    """How the verification was performed — drives confidence interpretation."""
    NUMERIC_COMPARISON     = "numeric_comparison"      # deterministic: 6.3 >= 6.0
    STRING_MATCH           = "string_match"            # exact/near-exact string match
    SEMANTIC_MATCH         = "semantic_match"          # embedding similarity
    CERTIFICATE_VALIDATION = "certificate_validation"  # certificate presence + date
    DATE_VALIDATION        = "date_validation"         # expiry / age checks
    LLM_REASONING          = "llm_reasoning"           # LLM judgment call


# ── Value representation ────────────────────────────────────────────────────────


class ExpectedValue(BaseModel):
    """Structured representation of the value a requirement expects.

    For numeric requirements (DIMENSION, TEST), operator/numeric_min/unit are
    populated so verification is deterministic Python math — no LLM call needed.
    For ranges ("150–200 mm"), both numeric_min and numeric_max are set.
    For non-numeric requirements (STANDARD, CERTIFICATE), only text is populated.
    """
    text: str | None = None           # raw: "BS 6920:2000", "HDPE PE100", "6 mm"
    operator: str | None = None       # ">=", "<=", "==", "in_range"
    numeric_min: float | None = None  # lower bound (or single value for non-range)
    numeric_max: float | None = None  # upper bound — only for in_range
    unit: str | None = None           # "mm", "MPa", "%", "bar"

    @model_validator(mode="after")
    def _validate_range(self) -> ExpectedValue:
        if self.operator == "in_range":
            if self.numeric_min is None or self.numeric_max is None:
                raise ValueError("in_range operator requires both numeric_min and numeric_max")
            if self.numeric_min > self.numeric_max:
                raise ValueError("numeric_min must be <= numeric_max for in_range")
        return self

    def is_numeric(self) -> bool:
        return self.numeric_min is not None

    def check(self, actual: float) -> bool:
        """Deterministic numeric check — only call when is_numeric() is True."""
        if self.operator == ">=" and self.numeric_min is not None:
            return actual >= self.numeric_min
        if self.operator == "<=" and self.numeric_min is not None:
            return actual <= self.numeric_min
        if self.operator == "==" and self.numeric_min is not None:
            return actual == self.numeric_min
        if self.operator == "in_range" and self.numeric_min is not None and self.numeric_max is not None:
            return self.numeric_min <= actual <= self.numeric_max
        return False


# ── Evidence expectation ────────────────────────────────────────────────────────


class EvidenceExpectation(BaseModel):
    """Describes what submitted documents are needed to satisfy a requirement.

    Extracted by the LLM from the spec wording — not derived from a hardcoded
    RequirementType → evidence mapping.  The spec wording determines this:
      "tested according to" → test_report in required_sources
      "comply with"         → datasheet or certificate in optional_sources (ANY)
      "certified to"        → certificate in required_sources
    """
    required_sources: list[DocType] = Field(default_factory=list)
    # All required_sources must provide evidence — equivalent to ALL.

    optional_sources: list[DocType] = Field(default_factory=list)
    # Evidence pool — minimum_optional_matches of these must confirm.

    minimum_optional_matches: int = 0
    # 0 means optional_sources are truly optional (extra confidence only).
    # 1 means ANY ONE of optional_sources must confirm.
    # len(optional_sources) means ALL of optional_sources must confirm.


# ── Core requirement model ──────────────────────────────────────────────────────


class SpecRequirement(BaseModel):
    """A single verifiable requirement extracted from an authority spec clause.

    Produced by the Requirement Extractor and stored in ReviewRequirementsArtifact.
    Consumed by the Requirement Verification Engine.
    """
    id: str                              # e.g. "R-001"
    requirement_type: RequirementType
    normalized_requirement: str          # "Minimum wall thickness ≥ 6 mm"
    expected_value: ExpectedValue
    evidence_expectation: EvidenceExpectation

    # Provenance — links back to the exact spec text
    source_clause: str                   # "26.3.2"
    source_page: int
    source_text: str                     # exact snippet from spec

    mandatory: bool = True               # False for conditional / informational requirements
    comparison_table_required: bool = True  # False for installation/experience/admin requirements


# ── Artifact produced after requirement extraction ──────────────────────────────


class ReviewRequirementsArtifact(BaseModel):
    """All requirements extracted from the spec clause for one material review.

    Produced once per review by the Spec Verifier agent (Phase 1 of 3).
    Shared with all downstream agents via SubmittalReviewState.
    """
    submittal_id: str
    authority: str
    spec_clause: str                     # raw clause reference from cover page
    normalized_clause: str               # e.g. "02810" after normalize_clause_ref()
    material_description: str
    requirements: list[SpecRequirement]
    extraction_model: str = "gpt-4o"


# ── Evidence snippet ────────────────────────────────────────────────────────────


class EvidenceSnippet(BaseModel):
    """A piece of text from a submitted document that supports (or contradicts) a requirement."""
    document_type: DocType
    source_document: str                 # filename: "Wavin HDPE Datasheet.pdf"
    page: int
    text: str                            # exact extracted snippet
    extracted_value: float | None = None # parsed numeric if applicable: 6.3


# ── Verification result ─────────────────────────────────────────────────────────


class RequirementVerification(BaseModel):
    """Compliance result for one SpecRequirement.

    Produced by the Requirement Verification Engine.
    Consumed by the Report Compiler, Query Agent, and Streamlit UI.
    Everything downstream consumes this artifact — not raw PDFs or RAG chunks.
    """
    requirement_id: str                  # matches SpecRequirement.id
    requirement_summary: str             # copy of normalized_requirement for readability
    status: VerificationStatus
    verification_method: VerificationMethod
    confidence: float                    # 0.0 – 1.0

    evidence_found: list[EvidenceSnippet] = Field(default_factory=list)
    missing_evidence: list[DocType] = Field(default_factory=list)
    contradictions: list[EvidenceSnippet] = Field(default_factory=list)

    reasoning: str                       # explanation of how the status was reached


# ── Artifact produced after verification ────────────────────────────────────────


class RequirementVerificationArtifact(BaseModel):
    """All verification results for one material review.

    Report Compiler, Query Agent, and UI all read from this single artifact.
    """
    submittal_id: str
    authority: str
    spec_clause: str
    material_description: str
    verifications: list[RequirementVerification]

    @property
    def satisfied_count(self) -> int:
        return sum(1 for v in self.verifications if v.status == VerificationStatus.SATISFIED)

    @property
    def non_compliant_count(self) -> int:
        return sum(1 for v in self.verifications if v.status == VerificationStatus.NON_COMPLIANT)

    @property
    def missing_evidence_count(self) -> int:
        return sum(1 for v in self.verifications if v.status == VerificationStatus.MISSING_EVIDENCE)

    @property
    def mandatory_failures(self) -> list[str]:
        """Requirement IDs that are mandatory and not satisfied."""
        return [
            v.requirement_id for v in self.verifications
            if v.status in (VerificationStatus.NON_COMPLIANT, VerificationStatus.MISSING_EVIDENCE)
        ]
