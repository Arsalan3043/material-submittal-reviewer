from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class DocType(str, Enum):
    """Document types proven in Phase 2 Experiment A (classifier_test.py)."""
    COVER_PAGE             = "cover_page"
    MSDF                   = "msdf"
    SPECIFICATION_COPY     = "specification_copy"
    BOQ                    = "boq"
    DRAWING                = "drawing"
    COMPARISON_TABLE       = "comparison_table"
    TECHNICAL_DATASHEET    = "technical_datasheet"
    TEST_REPORT            = "test_report"
    DED_REGISTRATION       = "ded_registration"
    MANUFACTURER_GUARANTEE = "manufacturer_guarantee"
    PREVIOUS_APPROVAL      = "previous_approval"
    METHOD_STATEMENT       = "method_statement"
    MAF                    = "maf"
    OTHERS                 = "others"


class SubmittalMetadata(BaseModel):
    """
    Lightweight record created at upload time and stored alongside the review.
    submittal_id is always a plain UUID — scoping lives here as fields, not in the ID.
    """
    submittal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    authority: str
    project_name: str = ""
    material_description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    # Production fields — add when multi-tenancy is needed:
    # user_id: str | None = None
    # tenant_id: str | None = None


class UploadedFile(BaseModel):
    filename: str
    content: bytes
    declared_index: int | None = None
    declared_label: str | None = None

    model_config = {"arbitrary_types_allowed": True}


class ClassifiedDocument(BaseModel):
    """Result of classifying a single PDF from the submittal package."""
    filename: str
    doc_type: DocType
    confidence: str          # "high" | "medium" | "low"
    reasoning: str
    key_indicators: list[str]
    text_preview: str = ""
    page_count: int = 1
    declared_label: str | None = None
    # True when actual doc_type differs from what the declared section implied.
    # maf placed in Index 8 (Previous Approvals) is NOT a mismatch — it is a
    # known UAE convention (experiment_findings.md, Experiment A, Decision 1).
    mismatch_flagged: bool = False
