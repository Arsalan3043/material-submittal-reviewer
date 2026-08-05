"""persist findings with stable IDs

Revision ID: 006
Revises: 005
Create Date: 2026-08-05

notes/11_pilot_bar_tickets.md Ticket 1. Findings previously existed only as
submittals.report JSONB — no per-finding identity, so nothing (a human decision, a
citation, an eval-harness comparison) could reference one finding stably across page
loads or re-reads. This table gives every finding a UUID and makes it immutable: nothing
ever UPDATEs a row after insert (a human decision goes in a separate append-only table,
Ticket 2).

clause_reference/spec_document_id/spec_page/source_document_id/source_page are nullable
and, as of this migration, populated as NULL for every category — see db/models.py::Finding
docstring for why (the frozen Finding/TableRowFinding models don't carry per-finding
citation provenance outside a fragile regex scrape). Same for model_version/prompt_version.

RLS policy mirrors migration 001's tenant_isolation pattern exactly (FORCE, not just
ENABLE — see that migration's comment for why FORCE matters against the table owner).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "submittal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submittals.id"),
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("action_required", sa.Text(), nullable=True),
        sa.Column("clause_reference", sa.Text(), nullable=True),
        sa.Column(
            "spec_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("spec_documents.id"),
            nullable=True,
        ),
        sa.Column("spec_page", sa.Integer(), nullable=True),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submittal_files.id"),
            nullable=True,
        ),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("pipeline_node", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "category IN ('completeness','boq_drawing','spec_verification','validity',"
            "'avl','statement','table_audit','consistency','others')",
            name="ck_findings_category",
        ),
        sa.CheckConstraint(
            "severity IN ('critical','warning','observation')", name="ck_findings_severity"
        ),
    )
    op.create_index("idx_findings_submittal", "findings", ["submittal_id"])
    op.create_index("idx_findings_tenant_project", "findings", ["tenant_id", "project_id"])

    op.execute("ALTER TABLE findings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE findings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON findings
            USING (tenant_id = current_setting('app.tenant_id', true)::UUID)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON findings")
    op.execute("ALTER TABLE findings NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE findings DISABLE ROW LEVEL SECURITY")
    op.drop_table("findings")
