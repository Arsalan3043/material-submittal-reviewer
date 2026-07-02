from __future__ import annotations

from typing import TypedDict


class SubmittalReviewState(TypedDict, total=False):
    """
    Shared state passed between all LangGraph nodes.

    TypedDict (not Pydantic BaseModel) for LangGraph 0.1.x compatibility.
    Pydantic objects are stored as dicts via .model_dump() and reconstructed
    at the point of use with Model.model_validate().

    All keys use total=False so nodes can write only the fields they own.
    """

    # ── Inputs ────────────────────────────────────────────────────────────
    authority: str                      # "ADM" or "TAQA"
    submittal_id: str
    review_date: str                    # ISO date string e.g. "2025-01-01"; defaults to today if absent
    # PDF bytes are staged via stage_files() before graph invocation — never in state.

    # ── Knowledge layer (Agent 1 output) ──────────────────────────────────
    # File path to the SubmittalKnowledgeStore JSON on disk.
    # Replaces classified_documents + all cover page fields.
    # State carries only this string (~100 chars) — no PDF bytes, no large dicts.
    knowledge_store_id: str

    # ── Requirement-centric artifacts (spec_verifier output) ─────────────
    # ReviewRequirementsArtifact.model_dump() — extracted spec requirements
    requirements_artifact: dict
    # RequirementVerificationArtifact.model_dump() — compliance results per requirement
    verification_artifact: dict

    # ── Per-stage findings (each stored as list[Finding.model_dump()]) ────
    completeness_findings: list[dict]
    boq_drawing_findings: list[dict]
    spec_verification_findings: list[dict]
    validity_findings: list[dict]
    avl_findings: list[dict]
    statement_findings: list[dict]
    # list[TableRowFinding.model_dump()]
    table_audit_findings: list[dict]
    consistency_findings: list[dict]
    others_findings: list[dict]

    # ── Metadata ──────────────────────────────────────────────────────────
    # document types determined to be absent
    missing_documents: list[str]

    # ── Final output ──────────────────────────────────────────────────────
    # ReviewReport.model_dump()
    report: dict
    review_complete: bool

    # ── Query mode (post-review chat) ─────────────────────────────────────
    # list[ConversationTurn.model_dump()]
    conversation_history: list[dict]
