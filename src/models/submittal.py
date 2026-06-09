from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


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
