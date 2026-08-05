"""
findings table RLS (migration 006) — the actual defense-in-depth test notes/
11_pilot_bar_tickets.md Ticket 1 asks for: a second tenant must not be able to read
another tenant's findings, even with no WHERE tenant_id clause, because the database
enforces it, not just application code.

Setup uses SyncSessionLocal (worker_app, BYPASSRLS — see db/session.py's docstring) since
seeding needs to write across two tenants freely. The actual assertions run through
db.session.get_db(tenant_id=...) — the real api_app role, RLS-constrained — the same
session constructor apps/api/routers/submittals.py's routes use. Zero OpenAI/AWS cost;
needs a reachable Postgres, same as test_worker_run_review.py.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from db.session import SyncSessionLocal, engine, get_db


async def _run_and_dispose(coro):
    """asyncio.run() opens and closes a fresh event loop per call, but db/session.py's
    `engine` (and its asyncpg connection pool) is a single module-level instance shared
    across every test in the process. asyncpg connections are bound to the loop that
    created them, so a pooled connection surviving into the next test's asyncio.run() call
    collides with "Future attached to a different loop". Disposing the pool at the end of
    each loop forces a fresh connection (and fresh loop binding) next time."""
    try:
        return await coro
    finally:
        await engine.dispose()

pytestmark = pytest.mark.integration


@pytest.fixture
def two_tenants_with_findings():
    """Seeds two independent tenant/user/project/submittal/finding graphs."""
    try:
        with SyncSessionLocal() as db:
            tenants = {}
            for label in ("a", "b"):
                tenant_id = uuid.uuid4()
                user_id = uuid.uuid4()
                project_id = uuid.uuid4()
                submittal_id = uuid.uuid4()
                finding_id = uuid.uuid4()

                db.execute(
                    text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
                    {"id": tenant_id, "slug": f"rls-test-{label}-{tenant_id.hex[:8]}"},
                )
                db.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, cognito_sub, email, role) "
                        "VALUES (:id, :tid, :sub, 'test@example.com', 'reviewer')"
                    ),
                    {"id": user_id, "tid": tenant_id, "sub": f"sub-{user_id.hex[:8]}"},
                )
                db.execute(
                    text(
                        "INSERT INTO projects (id, tenant_id, name, authority, created_by) "
                        "VALUES (:id, :tid, 'P', 'ADM', :uid)"
                    ),
                    {"id": project_id, "tid": tenant_id, "uid": user_id},
                )
                db.execute(
                    text(
                        "INSERT INTO submittals (id, tenant_id, project_id, user_id, status) "
                        "VALUES (:id, :tid, :pid, :uid, 'COMPLETED')"
                    ),
                    {"id": submittal_id, "tid": tenant_id, "pid": project_id, "uid": user_id},
                )
                db.execute(
                    text(
                        """
                        INSERT INTO findings
                            (id, tenant_id, project_id, submittal_id, category, severity,
                             description, pipeline_node, pipeline_version)
                        VALUES
                            (:id, :tid, :pid, :sid, 'completeness', 'critical',
                             :desc, 'completeness', 'v1')
                        """
                    ),
                    {
                        "id": finding_id,
                        "tid": tenant_id,
                        "pid": project_id,
                        "sid": submittal_id,
                        "desc": f"finding belonging to tenant {label}",
                    },
                )
                tenants[label] = {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "submittal_id": submittal_id,
                    "finding_id": finding_id,
                }
            db.commit()
    except OperationalError as exc:
        pytest.skip(f"no reachable Postgres for integration test: {exc}")

    yield tenants

    with SyncSessionLocal() as db:
        for t in tenants.values():
            db.execute(text("DELETE FROM findings WHERE tenant_id=:id"), {"id": t["tenant_id"]})
            db.execute(text("DELETE FROM submittals WHERE id=:id"), {"id": t["submittal_id"]})
            db.execute(text("DELETE FROM projects WHERE id=:id"), {"id": t["project_id"]})
            db.execute(text("DELETE FROM users WHERE tenant_id=:id"), {"id": t["tenant_id"]})
            db.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": t["tenant_id"]})
        db.commit()


def test_tenant_cannot_read_another_tenants_findings_even_without_a_where_clause(
    two_tenants_with_findings,
) -> None:
    tenants = two_tenants_with_findings

    async def _select_all_findings_as(tenant_id: uuid.UUID) -> list[dict]:
        async with get_db(tenant_id=tenant_id) as db:
            rows = (await db.execute(text("SELECT id, tenant_id, description FROM findings"))).mappings().fetchall()
            return [dict(r) for r in rows]

    async def _both() -> tuple[list[dict], list[dict]]:
        # Both queries must run inside the same asyncio.run() — the shared async engine's
        # connection pool (db/session.py's module-level `engine`) binds connections to
        # whichever event loop first used them, so two separate asyncio.run() calls in one
        # test collide ("Future attached to a different loop").
        return (
            await _select_all_findings_as(tenants["a"]["tenant_id"]),
            await _select_all_findings_as(tenants["b"]["tenant_id"]),
        )

    rows_as_a, rows_as_b = asyncio.run(_run_and_dispose(_both()))

    assert [r["id"] for r in rows_as_a] == [tenants["a"]["finding_id"]]
    assert rows_as_a[0]["description"] == "finding belonging to tenant a"

    assert [r["id"] for r in rows_as_b] == [tenants["b"]["finding_id"]]
    assert rows_as_b[0]["description"] == "finding belonging to tenant b"


def test_tenant_cannot_fetch_another_tenants_finding_by_id_directly(
    two_tenants_with_findings,
) -> None:
    tenants = two_tenants_with_findings

    async def _select_finding_by_id(tenant_id: uuid.UUID, finding_id: uuid.UUID):
        async with get_db(tenant_id=tenant_id) as db:
            row = (
                await db.execute(text("SELECT id FROM findings WHERE id=:id"), {"id": finding_id})
            ).fetchone()
            return row

    # Tenant A explicitly querying by tenant B's finding_id gets nothing — RLS blocks it
    # even when the caller supplies the exact primary key.
    row = asyncio.run(
        _run_and_dispose(
            _select_finding_by_id(tenants["a"]["tenant_id"], tenants["b"]["finding_id"])
        )
    )
    assert row is None
