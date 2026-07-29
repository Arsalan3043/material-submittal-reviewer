"""add knowledge_store_path to submittals, for chat

Revision ID: 004
Revises: 003
Create Date: 2026-07-24

Chat (query_agent.py::handle_query) needs the SubmittalKnowledgeStore that doc_processor
built during the review — but src/models/knowledge_store.py::save()/load_store() write
straight to local disk (STORE_DIR from src/config/paths.py), bypassing file_io.py entirely.
That file isn't file_io.py, so per CLAUDE.md rule 1 it's not something this build is allowed
to touch — it has to be worked around, not patched.

This is fine, not a workaround-of-a-bug: the Phase 6 deploy target
(planning/05_build_plan_for_claude_code.md) runs the API and worker on the SAME EC2 host via
docker-compose, so both processes sharing one local disk path is a valid assumption for the
actual target architecture, not a distributed-systems bug waiting to happen. It WOULD become
a real problem the day the API and worker ever get split across separate machines — worth
remembering if that split ever happens.

knowledge_store_path is a local filesystem path (data/knowledge_stores/{id}.json), not an S3
key — kept as its own column rather than overloading `store_s3_key` (already present, unused,
reserved for its originally-planned purpose of an S3 copy of the report) specifically so the
column name never lies about what kind of path it holds.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("submittals", sa.Column("knowledge_store_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("submittals", "knowledge_store_path")
