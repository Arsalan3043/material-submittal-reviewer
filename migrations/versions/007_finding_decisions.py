"""decision event log + reason codes

Revision ID: 007
Revises: 006
Create Date: 2026-08-05

notes/11_pilot_bar_tickets.md Ticket 2. Two tables:

reason_codes — global, shared taxonomy (no tenant_id/project_id, same rationale as
spec_documents: reference data Arsalan manages centrally, not per-tenant). Deliberately a
DB table, not a Python enum (notes/10_stage1_product_and_data_spec.md §A3), seeded below
with the starting 10-code set so it's usable immediately without a follow-up data migration.

finding_decisions — append-only human-decision log on findings (migration 006). Never
updated or deleted; a finding's current decision state is derived by reading the latest row
for that finding_id at query time (apps/api/routers/submittals.py), not stored as a mutable
column anywhere. The composite FK on (reason_code, action) is a real DB-level guarantee that
a reason code scoped to one action (e.g. a dismiss-only code) can't be attached to a
different action's decision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REASON_CODES = [
    # (code, action, label)
    ("false_positive_clause_misread", "dismiss", "AI misread the spec clause"),
    ("false_positive_doc_misread", "dismiss", "AI misread the submittal document"),
    ("not_applicable_material", "dismiss", "Clause doesn't apply to this material type"),
    ("duplicate", "dismiss", "Already flagged elsewhere in the report"),
    ("superseded", "dismiss", "Resolved by another document in the package"),
    ("severity_wrong", "edit", "Severity was incorrect"),
    ("clause_wrong_but_issue_real", "edit", "Wrong clause cited, but the issue is real"),
    ("description_imprecise", "edit", "Description was imprecise or unclear"),
    ("confirmed_as_stated", "confirm", "Confirmed exactly as the AI stated it"),
    (
        "confirmed_with_additional_context",
        "confirm",
        "Confirmed, with additional context added",
    ),
]


def upgrade() -> None:
    op.create_table(
        "reason_codes",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("action", sa.Text(), primary_key=True, nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "action IN ('confirm','dismiss','edit')", name="ck_reason_codes_action"
        ),
        sa.UniqueConstraint("code", "action", name="uq_reason_codes_code_action"),
    )
    op.bulk_insert(
        sa.table(
            "reason_codes",
            sa.column("code", sa.Text()),
            sa.column("action", sa.Text()),
            sa.column("label", sa.Text()),
        ),
        [{"code": code, "action": action, "label": label} for code, action, label in _REASON_CODES],
    )

    op.create_table(
        "finding_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id"),
            nullable=False,
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
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("corrected_fields", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "action IN ('confirm','dismiss','edit')", name="ck_finding_decisions_action"
        ),
        sa.CheckConstraint(
            "action = 'edit' OR corrected_fields IS NULL",
            name="ck_finding_decisions_corrected_fields_only_on_edit",
        ),
        sa.ForeignKeyConstraint(
            ["reason_code", "action"],
            ["reason_codes.code", "reason_codes.action"],
            name="fk_finding_decisions_reason_code_action",
        ),
    )
    op.create_index("idx_finding_decisions_finding", "finding_decisions", ["finding_id"])
    op.create_index(
        "idx_finding_decisions_tenant_project", "finding_decisions", ["tenant_id", "project_id"]
    )

    op.execute("ALTER TABLE finding_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finding_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON finding_decisions
            USING (tenant_id = current_setting('app.tenant_id', true)::UUID)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON finding_decisions")
    op.execute("ALTER TABLE finding_decisions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finding_decisions DISABLE ROW LEVEL SECURITY")
    op.drop_table("finding_decisions")
    op.drop_table("reason_codes")
