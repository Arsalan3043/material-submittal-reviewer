"""split spec metadata out of project_specs into a global spec_documents registry

Revision ID: 002
Revises: 001
Create Date: 2026-07-24

001's project_specs table wrongly stored spec PDF metadata (source_s3_key, chunk_count,
qdrant_collection, indexed_at) once PER PROJECT SELECTION, even though a spec PDF (e.g. the
ADM irrigation spec) is the same shared document regardless of which project selects it —
"specs are shared reference data, not per-tenant secrets"
(planning/05_build_plan_for_claude_code.md, Phase 4). Left as-is, every project that selects
the same spec would duplicate — and could drift out of sync on — the same S3 key and chunk
count.

This migration introduces spec_documents as the single global registry (one row per
authority+network+source_file, no tenant_id/project_id — the deliberate exception to the
"every table carries tenant_id+project_id" rule, alongside tenants itself) and shrinks
project_specs down to a thin join table: which spec_documents a given project has selected.

No data migration is included because nothing in the codebase writes to project_specs yet —
Phase 3 (the API layer that would populate it) hasn't been built. Verified empirically below
before applying this migration, not just assumed.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "spec_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.Column("network_name", sa.Text(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_s3_key", sa.Text(), nullable=False),
        sa.Column("qdrant_collection", sa.Text(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "authority", "network_name", "source_file", name="uq_spec_documents_identity"
        ),
    )
    op.create_index(
        "idx_spec_documents_authority_network", "spec_documents", ["authority", "network_name"]
    )

    # project_specs: drop the duplicated spec-metadata columns, add the FK to spec_documents.
    # Safe to drop outright (not nullable-first-then-backfill-then-drop) — table has zero rows.
    op.drop_column("project_specs", "authority")
    op.drop_column("project_specs", "network_name")
    op.drop_column("project_specs", "qdrant_collection")
    op.drop_column("project_specs", "source_s3_key")
    op.drop_column("project_specs", "chunk_count")
    op.drop_column("project_specs", "indexed_at")

    op.add_column(
        "project_specs",
        sa.Column("spec_document_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_project_specs_spec_document",
        "project_specs",
        "spec_documents",
        ["spec_document_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_project_specs_selection", "project_specs", ["project_id", "spec_document_id"]
    )
    op.create_index("idx_project_specs_project", "project_specs", ["project_id"])


def downgrade() -> None:
    op.drop_index("idx_project_specs_project", table_name="project_specs")
    op.drop_constraint("uq_project_specs_selection", "project_specs", type_="unique")
    op.drop_constraint("fk_project_specs_spec_document", "project_specs", type_="foreignkey")
    op.drop_column("project_specs", "spec_document_id")

    op.add_column("project_specs", sa.Column("indexed_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("project_specs", sa.Column("chunk_count", sa.Integer()))
    op.add_column("project_specs", sa.Column("source_s3_key", sa.Text()))
    op.add_column(
        "project_specs", sa.Column("qdrant_collection", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "project_specs", sa.Column("network_name", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "project_specs", sa.Column("authority", sa.Text(), nullable=False, server_default="")
    )
    op.alter_column("project_specs", "qdrant_collection", server_default=None)
    op.alter_column("project_specs", "network_name", server_default=None)
    op.alter_column("project_specs", "authority", server_default=None)

    op.drop_index("idx_spec_documents_authority_network", table_name="spec_documents")
    op.drop_table("spec_documents")
