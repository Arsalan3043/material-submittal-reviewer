"""
SQLAlchemy models for the project-level, multi-tenant schema.

Mirrors the SQL in planning/05_build_plan_for_claude_code.md exactly — table names,
column names, constraints, and indexes match 1:1 so the Alembic migration generated from
these models is a straight translation, not a reinterpretation.

Every table that isn't purely global (tenants, spec_documents) carries tenant_id; every table
scoped to a project also carries project_id directly (not just via a submittal join) per
CLAUDE.md rule 2 — this is the one thing expensive to retrofit, so it is not inferred through
joins anywhere. spec_documents is the deliberate exception: spec PDFs are shared reference
data, not per-tenant secrets (see planning/05_build_plan_for_claude_code.md Phase 4), so it
carries neither tenant_id nor project_id — see migration 002 for the full rationale.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    users: Mapped[list[User]] = relationship(back_populates="tenant")
    projects: Mapped[list[Project]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin','tenant_admin','reviewer')", name="ck_users_role"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    cognito_sub: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("idx_projects_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    authority: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="projects")
    specs: Mapped[list[ProjectSpec]] = relationship(back_populates="project")
    submittals: Mapped[list[Submittal]] = relationship(back_populates="project")


class SpecDocument(Base):
    """
    Global registry of indexed spec PDFs — one row per (authority, network, source_file),
    never per-tenant or per-project. Specs are shared reference data (the same ADM irrigation
    spec is the same PDF for every tenant), so unlike every other table in this schema this
    one deliberately carries no tenant_id/project_id. See migration 002 for why: the original
    Phase 1 project_specs table wrongly duplicated these fields once per project selection.
    """

    __tablename__ = "spec_documents"
    __table_args__ = (
        UniqueConstraint(
            "authority", "network_name", "source_file", name="uq_spec_documents_identity"
        ),
        Index("idx_spec_documents_authority_network", "authority", "network_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    authority: Mapped[str] = mapped_column(Text, nullable=False)
    network_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    qdrant_collection: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    project_specs: Mapped[list[ProjectSpec]] = relationship(back_populates="spec_document")


class ProjectSpec(Base):
    """Join table: which spec_documents a project has selected. Thin by design — all the
    actual spec metadata (S3 key, chunk count, indexed_at) lives once on SpecDocument."""

    __tablename__ = "project_specs"
    __table_args__ = (
        UniqueConstraint("project_id", "spec_document_id", name="uq_project_specs_selection"),
        Index("idx_project_specs_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    spec_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spec_documents.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="specs")
    spec_document: Mapped[SpecDocument] = relationship(back_populates="project_specs")


class Submittal(Base):
    __tablename__ = "submittals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_submittals_status",
        ),
        Index("idx_submittals_project", "project_id"),
        Index("idx_submittals_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    material_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="CREATED")
    pipeline_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Local disk path, not S3 — see migration 004 for why this is a separate, honestly-named
    # column rather than repurposing store_s3_key.
    knowledge_store_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # ReviewRequirementsArtifact / RequirementVerificationArtifact model_dump()s — carry the
    # clause/page provenance report's Finding objects drop. See migration 005.
    requirements_artifact: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    verification_artifact: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    project: Mapped[Project] = relationship(back_populates="submittals")
    files: Mapped[list[SubmittalFile]] = relationship(back_populates="submittal")
    events: Mapped[list[SubmittalEvent]] = relationship(back_populates="submittal")
    chat_turns: Mapped[list[ChatTurn]] = relationship(back_populates="submittal")


class SubmittalFile(Base):
    __tablename__ = "submittal_files"

    id: Mapped[uuid.UUID] = _uuid_pk()
    submittal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submittals.id"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    declared_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    submittal: Mapped[Submittal] = relationship(back_populates="files")


class SubmittalEvent(Base):
    __tablename__ = "submittal_events"
    __table_args__ = (
        UniqueConstraint("submittal_id", "sequence_number", name="uq_submittal_events_seq"),
        Index("idx_submittal_events_seq", "submittal_id", "sequence_number"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    submittal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submittals.id"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    submittal: Mapped[Submittal] = relationship(back_populates="events")


class ChatTurn(Base):
    __tablename__ = "chat_turns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    submittal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submittals.id"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    route: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    submittal: Mapped[Submittal] = relationship(back_populates="chat_turns")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','DONE','FAILED')", name="ck_jobs_status"
        ),
        Index("idx_jobs_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    submittal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submittals.id"), nullable=True
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    picked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
