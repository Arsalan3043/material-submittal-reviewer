"""apps/worker/worker.py::run_review — the Phase 0 seam proven against a real Postgres
transaction (worker_app role) but zero network calls: S3 is monkeypatched and the pipeline
is FakeReviewPipeline. Requires a reachable dev database (DATABASE_URL/.env), same as every
other test that touched db/session.py before this file existed — marked `integration`
because of that DB dependency, not because it spends real money (it never calls OpenAI)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from adapters.pipeline.fake_pipeline import FakeReviewPipeline
from apps.worker import worker as worker_module
from core.errors import PipelineTransientError
from db.session import SyncSessionLocal

pytestmark = pytest.mark.integration

TEST_S3_BUCKET = "test-bucket-unused"


@pytest.fixture
def seeded_submittal(monkeypatch: pytest.MonkeyPatch):
    """Creates a throwaway tenant/user/project/submittal/submittal_files graph, monkeypatches
    S3 download to avoid any real network call, and cleans everything up afterward."""
    monkeypatch.setattr(
        worker_module, "_load_pdf_bytes_typed", lambda uri: b"%PDF-fake content"
    )

    try:
        with SyncSessionLocal() as db:
            tenant_id = uuid.uuid4()
            user_id = uuid.uuid4()
            project_id = uuid.uuid4()
            submittal_id = uuid.uuid4()

            db.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Test Tenant', :slug)"),
                {"id": tenant_id, "slug": f"test-tenant-{tenant_id.hex[:8]}"},
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
                    "VALUES (:id, :tid, 'Test Project', 'ADM', :uid)"
                ),
                {"id": project_id, "tid": tenant_id, "uid": user_id},
            )
            db.execute(
                text(
                    "INSERT INTO submittals (id, tenant_id, project_id, user_id, status) "
                    "VALUES (:id, :tid, :pid, :uid, 'CREATED')"
                ),
                {"id": submittal_id, "tid": tenant_id, "pid": project_id, "uid": user_id},
            )
            db.execute(
                text(
                    "INSERT INTO submittal_files (submittal_id, tenant_id, original_name, "
                    "declared_label, s3_key) VALUES (:sid, :tid, 'datasheet.pdf', 'datasheet', "
                    "'unused/key.pdf')"
                ),
                {"sid": submittal_id, "tid": tenant_id},
            )
            db.commit()
    except OperationalError as exc:
        pytest.skip(f"no reachable Postgres for integration test: {exc}")

    yield submittal_id

    with SyncSessionLocal() as db:
        db.execute(text("DELETE FROM submittal_events WHERE submittal_id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM submittal_files WHERE submittal_id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM submittals WHERE id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
        db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
        db.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
        db.commit()


def test_run_review_completes_and_persists_report(seeded_submittal: uuid.UUID) -> None:
    submittal_id = seeded_submittal
    with SyncSessionLocal() as db:
        worker_module.run_review(db, submittal_id, TEST_S3_BUCKET, FakeReviewPipeline())

    with SyncSessionLocal() as db:
        row = (
            db.execute(
                text(
                    "SELECT status, recommendation, report IS NOT NULL AS has_report "
                    "FROM submittals WHERE id=:id"
                ),
                {"id": submittal_id},
            )
            .mappings()
            .fetchone()
        )
        assert row is not None
        assert row["status"] == "COMPLETED"
        assert row["recommendation"] == "APPROVE"
        assert row["has_report"] is True

        event_count = db.execute(
            text("SELECT count(*) FROM submittal_events WHERE submittal_id=:id"),
            {"id": submittal_id},
        ).scalar()
        assert event_count == 11


def test_run_review_propagates_pipeline_error_without_marking_completed(
    seeded_submittal: uuid.UUID,
) -> None:
    submittal_id = seeded_submittal
    failing_pipeline = FakeReviewPipeline(raises=PipelineTransientError("simulated LLM outage"))

    with SyncSessionLocal() as db, pytest.raises(PipelineTransientError):
        worker_module.run_review(db, submittal_id, TEST_S3_BUCKET, failing_pipeline)

    with SyncSessionLocal() as db:
        status = db.execute(
            text("SELECT status FROM submittals WHERE id=:id"), {"id": submittal_id}
        ).scalar()
        # run_review itself doesn't catch pipeline errors — that's _handle_job's job
        # (apps/worker/worker.py::run_worker's except block). Here we're only proving
        # run_review leaves the row at RUNNING, not silently COMPLETED, on failure.
        assert status == "RUNNING"
