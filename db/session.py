"""
SQLAlchemy engine/session setup — async for the API (Phase 3), sync for the worker
(Phase 2). These deliberately use DIFFERENT Postgres roles, not the same DATABASE_URL —
see migration 003 for why this matters. Short version: this codebase's first live
cross-tenant RLS test (Phase 3) found a real data leak, because both the API and the
worker were connecting as the `postgres` superuser, and superusers always bypass RLS
regardless of FORCE ROW LEVEL SECURITY. api_app (this file's DATABASE_URL) is a regular,
RLS-constrained role — the defense-in-depth layer that matters for an HTTP-facing process.
worker_app (WORKER_DATABASE_URL) is BYPASSRLS, deliberately: it must read a submittal's own
tenant_id from the submittals table before it can know what to `SET LOCAL app.tenant_id`
to — a chicken-and-egg problem plain RLS can't resolve for a trusted backend process.

`get_db(tenant_id)` is the FastAPI dependency: it opens a session and, for every request,
runs `SET LOCAL app.tenant_id = ...` inside the same transaction so the RLS policies in
migration 001 (`current_setting('app.tenant_id', true)::UUID`) scope every query
automatically — a route can forget a WHERE clause and RLS still holds the line, as long as
the connection isn't a superuser/BYPASSRLS role in the first place.

`SET LOCAL` (not `SET`) is deliberate: it only lasts for the current transaction, so pooled
connections never leak one tenant's context into the next request that reuses the connection.

Migrations (migrations/env.py) run as the `postgres` superuser via ADMIN_DATABASE_URL —
DDL (CREATE TABLE, CREATE ROLE) needs privileges neither runtime role has or should have.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

# The API's runtime role — must stay non-superuser, non-BYPASSRLS (api_app, migration 003).
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://api_app:api_app_dev_password@localhost:5432/material_submittal_reviewer",
)

# The worker's runtime role — BYPASSRLS on purpose (worker_app, migration 003).
WORKER_DATABASE_URL = os.environ.get(
    "WORKER_DATABASE_URL",
    "postgresql+asyncpg://worker_app:worker_app_dev_password@localhost:5432/material_submittal_reviewer",
)

# The superuser role — DDL only (Alembic). Never used by application runtime code.
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/material_submittal_reviewer",
)


def _to_sync(asyncpg_url: str) -> str:
    return asyncpg_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def sync_database_url() -> str:
    """psycopg2 URL for Alembic (migrations/env.py) — always the admin/superuser role."""
    return _to_sync(ADMIN_DATABASE_URL)


engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

sync_engine = create_engine(_to_sync(WORKER_DATABASE_URL), echo=False, pool_pre_ping=True)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)


@asynccontextmanager
async def get_db(tenant_id: uuid.UUID | str | None = None) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a session. If tenant_id is given, sets app.tenant_id for the duration of the
    transaction so RLS policies on projects/submittals/chat_turns apply. Pass tenant_id=None
    for tenant-agnostic work (migrations, the job-queue worker's own bookkeeping on `jobs`,
    which has no tenant_id column and is not RLS-protected).
    """
    async with SessionLocal() as session:
        async with session.begin():
            if tenant_id is not None:
                # Postgres's SET/SET LOCAL does not support bind parameters ($1) — it
                # requires a literal in the statement text (confirmed: asyncpg raises
                # PostgresSyntaxError on "SET LOCAL app.tenant_id = $1"). Safe to inline
                # directly here because uuid.UUID(...) below raises on anything that
                # isn't a well-formed UUID — the resulting string can only ever contain
                # hex digits and hyphens, so there's no injection surface.
                validated_tenant_id = uuid.UUID(str(tenant_id))
                await session.execute(
                    text(f"SET LOCAL app.tenant_id = '{validated_tenant_id}'")
                )
            yield session
