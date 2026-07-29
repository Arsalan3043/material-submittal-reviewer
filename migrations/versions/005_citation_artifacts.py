"""add requirements_artifact + verification_artifact to submittals, for citations

Revision ID: 005
Revises: 004
Create Date: 2026-07-25

The findings shown in submittals.report are built from a simplified Finding object
(stage/document/description/severity/action_required — confirmed from a real report) that
drops the clause/page provenance the pipeline actually computes. src/models/requirements.py's
SpecRequirement (source_clause, source_page, source_text) and RequirementVerification
(evidence_found: list[EvidenceSnippet] with page/source_document) already carry this — they
just weren't being persisted anywhere past LangGraph state, which is discarded once a review
finishes.

This migration adds two columns so the worker can persist
final_state["requirements_artifact"] / ["verification_artifact"] (already computed on every
review, just previously thrown away) — read-only additions, no src/ changes needed.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submittals", sa.Column("requirements_artifact", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "submittals", sa.Column("verification_artifact", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("submittals", "verification_artifact")
    op.drop_column("submittals", "requirements_artifact")
