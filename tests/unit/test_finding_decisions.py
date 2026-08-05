"""
finding_decisions + reason_codes (migration 007, notes/11_pilot_bar_tickets.md Ticket 2).
Zero OpenAI/AWS cost, needs a reachable Postgres (same pattern as
test_worker_run_review.py / test_findings_rls.py): decision recording, latest-wins
derivation, reason_code required and action-matched at the DB level, and cross-tenant RLS.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from db.session import SyncSessionLocal, engine, get_db

pytestmark = pytest.mark.integration


async def _run_and_dispose(coro):
    """See tests/unit/test_findings_rls.py for why: the shared async engine's connection
    pool must not carry a connection bound to a previous asyncio.run()'s closed loop."""
    try:
        return await coro
    finally:
        await engine.dispose()


@pytest.fixture
def seeded_finding():
    """One tenant/user/project/submittal/finding graph — a plain finding to attach
    decisions to, independent of the pipeline entirely."""
    try:
        with SyncSessionLocal() as db:
            tenant_id = uuid.uuid4()
            user_id = uuid.uuid4()
            project_id = uuid.uuid4()
            submittal_id = uuid.uuid4()
            finding_id = uuid.uuid4()

            db.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
                {"id": tenant_id, "slug": f"decisions-test-{tenant_id.hex[:8]}"},
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
                         'Missing MSDF.', 'completeness', 'v1')
                    """
                ),
                {"id": finding_id, "tid": tenant_id, "pid": project_id, "sid": submittal_id},
            )
            db.commit()
    except OperationalError as exc:
        pytest.skip(f"no reachable Postgres for integration test: {exc}")

    yield {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "project_id": project_id,
        "submittal_id": submittal_id,
        "finding_id": finding_id,
    }

    with SyncSessionLocal() as db:
        db.execute(text("DELETE FROM finding_decisions WHERE finding_id=:id"), {"id": finding_id})
        db.execute(text("DELETE FROM findings WHERE id=:id"), {"id": finding_id})
        db.execute(text("DELETE FROM submittals WHERE id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
        db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
        db.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
        db.commit()


def _insert_decision(
    db,
    *,
    finding_id,
    tenant_id,
    project_id,
    submittal_id,
    actor_user_id,
    action,
    reason_code,
    note=None,
    corrected_fields=None,
):
    import json

    return db.execute(
        text(
            """
            INSERT INTO finding_decisions
                (finding_id, tenant_id, project_id, submittal_id, actor_user_id,
                 action, reason_code, note, corrected_fields)
            VALUES
                (:finding_id, :tenant_id, :project_id, :submittal_id, :actor_user_id,
                 :action, :reason_code, :note, CAST(:corrected_fields AS JSONB))
            RETURNING id, created_at
            """
        ),
        {
            "finding_id": finding_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "submittal_id": submittal_id,
            "actor_user_id": actor_user_id,
            "action": action,
            "reason_code": reason_code,
            "note": note,
            "corrected_fields": json.dumps(corrected_fields) if corrected_fields else None,
        },
    ).mappings().fetchone()


def test_decision_is_recorded_and_queryable(seeded_finding) -> None:
    f = seeded_finding
    with SyncSessionLocal() as db:
        row = _insert_decision(
            db,
            finding_id=f["finding_id"],
            tenant_id=f["tenant_id"],
            project_id=f["project_id"],
            submittal_id=f["submittal_id"],
            actor_user_id=f["user_id"],
            action="dismiss",
            reason_code="duplicate",
            note="already covered above",
        )
        db.commit()
        assert row is not None

    with SyncSessionLocal() as db:
        stored = (
            db.execute(
                text("SELECT action, reason_code, note FROM finding_decisions WHERE id=:id"),
                {"id": row["id"]},
            )
            .mappings()
            .fetchone()
        )
    assert stored is not None
    assert stored["action"] == "dismiss"
    assert stored["reason_code"] == "duplicate"
    assert stored["note"] == "already covered above"


def test_latest_decision_wins_while_full_history_survives(seeded_finding) -> None:
    f = seeded_finding
    with SyncSessionLocal() as db:
        _insert_decision(
            db,
            finding_id=f["finding_id"],
            tenant_id=f["tenant_id"],
            project_id=f["project_id"],
            submittal_id=f["submittal_id"],
            actor_user_id=f["user_id"],
            action="confirm",
            reason_code="confirmed_as_stated",
        )
        db.commit()

    with SyncSessionLocal() as db:
        _insert_decision(
            db,
            finding_id=f["finding_id"],
            tenant_id=f["tenant_id"],
            project_id=f["project_id"],
            submittal_id=f["submittal_id"],
            actor_user_id=f["user_id"],
            action="dismiss",
            reason_code="not_applicable_material",
            note="doesn't apply here",
        )
        db.commit()

    with SyncSessionLocal() as db:
        # Same DISTINCT-latest-per-finding shape as apps/api/routers/submittals.py's
        # findings query — proven directly here rather than through HTTP (no
        # FastAPI-route-testing harness exists yet in this codebase).
        latest = (
            db.execute(
                text(
                    """
                    SELECT action, reason_code, note FROM finding_decisions
                    WHERE finding_id = :id ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"id": f["finding_id"]},
            )
            .mappings()
            .fetchone()
        )
        history_count = db.execute(
            text("SELECT count(*) FROM finding_decisions WHERE finding_id=:id"),
            {"id": f["finding_id"]},
        ).scalar()

    assert latest is not None
    assert latest["action"] == "dismiss"
    assert latest["reason_code"] == "not_applicable_material"
    assert latest["note"] == "doesn't apply here"
    assert history_count == 2  # both rows remain — append-only, nothing overwritten


def test_reason_code_is_required(seeded_finding) -> None:
    f = seeded_finding
    with SyncSessionLocal() as db, pytest.raises(IntegrityError):
        db.execute(
            text(
                """
                INSERT INTO finding_decisions
                    (finding_id, tenant_id, project_id, submittal_id, actor_user_id, action)
                VALUES (:finding_id, :tenant_id, :project_id, :submittal_id, :actor_user_id, 'confirm')
                """
            ),
            {
                "finding_id": f["finding_id"],
                "tenant_id": f["tenant_id"],
                "project_id": f["project_id"],
                "submittal_id": f["submittal_id"],
                "actor_user_id": f["user_id"],
            },
        )
        db.commit()


def test_reason_code_must_match_the_decisions_action(seeded_finding) -> None:
    f = seeded_finding
    with SyncSessionLocal() as db, pytest.raises(IntegrityError):
        # "duplicate" is a dismiss-only reason code — using it on a confirm action must
        # fail the composite FK (reason_code, action) -> reason_codes(code, action).
        _insert_decision(
            db,
            finding_id=f["finding_id"],
            tenant_id=f["tenant_id"],
            project_id=f["project_id"],
            submittal_id=f["submittal_id"],
            actor_user_id=f["user_id"],
            action="confirm",
            reason_code="duplicate",
        )
        db.commit()


def test_corrected_fields_rejected_for_non_edit_action(seeded_finding) -> None:
    f = seeded_finding
    with SyncSessionLocal() as db, pytest.raises(IntegrityError):
        _insert_decision(
            db,
            finding_id=f["finding_id"],
            tenant_id=f["tenant_id"],
            project_id=f["project_id"],
            submittal_id=f["submittal_id"],
            actor_user_id=f["user_id"],
            action="confirm",
            reason_code="confirmed_as_stated",
            corrected_fields={"severity": "warning"},
        )
        db.commit()


def test_edit_action_persists_corrected_fields(seeded_finding) -> None:
    f = seeded_finding
    with SyncSessionLocal() as db:
        row = _insert_decision(
            db,
            finding_id=f["finding_id"],
            tenant_id=f["tenant_id"],
            project_id=f["project_id"],
            submittal_id=f["submittal_id"],
            actor_user_id=f["user_id"],
            action="edit",
            reason_code="severity_wrong",
            corrected_fields={"severity": "warning"},
        )
        db.commit()

    with SyncSessionLocal() as db:
        stored = (
            db.execute(
                text("SELECT corrected_fields FROM finding_decisions WHERE id=:id"),
                {"id": row["id"]},
            )
            .mappings()
            .fetchone()
        )
    assert stored is not None
    corrected = stored["corrected_fields"]
    if isinstance(corrected, str):
        import json

        corrected = json.loads(corrected)
    assert corrected == {"severity": "warning"}


def test_tenant_cannot_read_another_tenants_finding_decisions(seeded_finding) -> None:
    """Real cross-tenant RLS proof, same shape as test_findings_rls.py: seed a second
    tenant's decision via worker_app (BYPASSRLS, setup only), then read as api_app
    (RLS-constrained, the real route's role) and confirm isolation holds even with no
    WHERE tenant_id clause."""
    f = seeded_finding
    other_tenant_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    other_submittal_id = uuid.uuid4()
    other_finding_id = uuid.uuid4()

    with SyncSessionLocal() as db:
        _insert_decision(
            db,
            finding_id=f["finding_id"],
            tenant_id=f["tenant_id"],
            project_id=f["project_id"],
            submittal_id=f["submittal_id"],
            actor_user_id=f["user_id"],
            action="confirm",
            reason_code="confirmed_as_stated",
        )

        db.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T2', :slug)"),
            {"id": other_tenant_id, "slug": f"decisions-test-other-{other_tenant_id.hex[:8]}"},
        )
        db.execute(
            text(
                "INSERT INTO users (id, tenant_id, cognito_sub, email, role) "
                "VALUES (:id, :tid, :sub, 'test2@example.com', 'reviewer')"
            ),
            {"id": other_user_id, "tid": other_tenant_id, "sub": f"sub-{other_user_id.hex[:8]}"},
        )
        db.execute(
            text(
                "INSERT INTO projects (id, tenant_id, name, authority, created_by) "
                "VALUES (:id, :tid, 'P2', 'ADM', :uid)"
            ),
            {"id": other_project_id, "tid": other_tenant_id, "uid": other_user_id},
        )
        db.execute(
            text(
                "INSERT INTO submittals (id, tenant_id, project_id, user_id, status) "
                "VALUES (:id, :tid, :pid, :uid, 'COMPLETED')"
            ),
            {
                "id": other_submittal_id,
                "tid": other_tenant_id,
                "pid": other_project_id,
                "uid": other_user_id,
            },
        )
        db.execute(
            text(
                """
                INSERT INTO findings
                    (id, tenant_id, project_id, submittal_id, category, severity,
                     description, pipeline_node, pipeline_version)
                VALUES
                    (:id, :tid, :pid, :sid, 'completeness', 'critical',
                     'Other tenant finding.', 'completeness', 'v1')
                """
            ),
            {
                "id": other_finding_id,
                "tid": other_tenant_id,
                "pid": other_project_id,
                "sid": other_submittal_id,
            },
        )
        _insert_decision(
            db,
            finding_id=other_finding_id,
            tenant_id=other_tenant_id,
            project_id=other_project_id,
            submittal_id=other_submittal_id,
            actor_user_id=other_user_id,
            action="dismiss",
            reason_code="duplicate",
        )
        db.commit()

    try:
        async def _select_all_as(tenant_id: uuid.UUID) -> list[dict]:
            async with get_db(tenant_id=tenant_id) as db:
                rows = (
                    await db.execute(text("SELECT id, tenant_id FROM finding_decisions"))
                ).mappings().fetchall()
                return [dict(r) for r in rows]

        async def _both():
            return (
                await _select_all_as(f["tenant_id"]),
                await _select_all_as(other_tenant_id),
            )

        rows_as_tenant_a, rows_as_tenant_b = asyncio.run(_run_and_dispose(_both()))

        assert {r["tenant_id"] for r in rows_as_tenant_a} == {f["tenant_id"]}
        assert {r["tenant_id"] for r in rows_as_tenant_b} == {other_tenant_id}
    finally:
        with SyncSessionLocal() as db:
            db.execute(
                text("DELETE FROM finding_decisions WHERE tenant_id=:id"),
                {"id": other_tenant_id},
            )
            db.execute(text("DELETE FROM findings WHERE id=:id"), {"id": other_finding_id})
            db.execute(text("DELETE FROM submittals WHERE id=:id"), {"id": other_submittal_id})
            db.execute(text("DELETE FROM projects WHERE id=:id"), {"id": other_project_id})
            db.execute(text("DELETE FROM users WHERE id=:id"), {"id": other_user_id})
            db.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": other_tenant_id})
            db.commit()
