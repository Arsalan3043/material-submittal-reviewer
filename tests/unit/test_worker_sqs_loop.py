"""apps/worker/worker.py::_process_message — the SQS-driven receive/claim/handle/ack cycle
that replaced the old Postgres SKIP LOCKED loop. Real Postgres (worker_app role), zero
network calls: S3 is monkeypatched, the queue is FakeJobQueue, the pipeline is
FakeReviewPipeline. Marked `integration` for the DB dependency only — never calls OpenAI or
real AWS, same as tests/unit/test_worker_run_review.py."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from adapters.pipeline.fake_pipeline import FakeReviewPipeline
from adapters.queue.fake_queue import FakeJobQueue
from apps.worker import worker as worker_module
from core.errors import PipelineInvalidInputError, PipelineTransientError
from db.session import SyncSessionLocal

pytestmark = pytest.mark.integration

TEST_S3_BUCKET = "test-bucket-unused"


@pytest.fixture
def seeded_job(monkeypatch: pytest.MonkeyPatch):
    """Creates a throwaway tenant/user/project/submittal/submittal_files/jobs graph — a
    'review' job in PENDING, ready to be claimed exactly like a real SQS-dispatched one."""
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
                    "VALUES (:id, :tid, :pid, :uid, 'QUEUED')"
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
            job_row = db.execute(
                text(
                    "INSERT INTO jobs (job_type, submittal_id, status) "
                    "VALUES ('review', :sid, 'PENDING') RETURNING id"
                ),
                {"sid": submittal_id},
            ).fetchone()
            assert job_row is not None
            job_id = job_row[0]
            db.commit()
    except OperationalError as exc:
        pytest.skip(f"no reachable Postgres for integration test: {exc}")

    yield submittal_id, job_id

    with SyncSessionLocal() as db:
        db.execute(text("DELETE FROM jobs WHERE id=:id"), {"id": job_id})
        db.execute(text("DELETE FROM submittal_events WHERE submittal_id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM submittal_files WHERE submittal_id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM submittals WHERE id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
        db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
        db.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
        db.commit()


def _job_status(job_id: uuid.UUID) -> str | None:
    with SyncSessionLocal() as db:
        return db.execute(text("SELECT status FROM jobs WHERE id=:id"), {"id": job_id}).scalar()


def _submittal_status(submittal_id: uuid.UUID) -> str | None:
    with SyncSessionLocal() as db:
        return db.execute(
            text("SELECT status FROM submittals WHERE id=:id"), {"id": submittal_id}
        ).scalar()


def test_process_message_success_marks_done_and_acks(seeded_job) -> None:
    submittal_id, job_id = seeded_job
    queue = FakeJobQueue()
    queue.send(str(job_id))
    message = queue.receive()
    assert message is not None

    worker_module._process_message(message, TEST_S3_BUCKET, FakeReviewPipeline(), queue)

    assert _job_status(job_id) == "DONE"
    assert _submittal_status(submittal_id) == "COMPLETED"
    assert queue.receive() is None  # acked — nothing left, even after a hypothetical redelivery
    queue.requeue_in_flight()
    assert queue.receive() is None


def test_process_message_retryable_failure_requeues_without_acking(seeded_job) -> None:
    submittal_id, job_id = seeded_job
    queue = FakeJobQueue()
    queue.send(str(job_id))
    message = queue.receive()
    assert message is not None
    pipeline = FakeReviewPipeline(raises=PipelineTransientError("simulated LLM outage"))

    worker_module._process_message(message, TEST_S3_BUCKET, pipeline, queue)

    assert _job_status(job_id) == "PENDING"  # reverted so a redelivery can re-claim it
    assert _submittal_status(submittal_id) == "QUEUED"
    assert queue.receive() is None  # still in flight, not acked, not yet redelivered
    queue.requeue_in_flight()  # simulate the visibility timeout expiring
    redelivered = queue.receive()
    assert redelivered is not None
    assert redelivered.job_id == str(job_id)


def test_process_message_non_retryable_failure_fails_and_acks(seeded_job) -> None:
    submittal_id, job_id = seeded_job
    queue = FakeJobQueue()
    queue.send(str(job_id))
    message = queue.receive()
    assert message is not None
    pipeline = FakeReviewPipeline(raises=PipelineInvalidInputError("corrupt PDF"))

    worker_module._process_message(message, TEST_S3_BUCKET, pipeline, queue)

    assert _job_status(job_id) == "FAILED"
    assert _submittal_status(submittal_id) == "FAILED"
    queue.requeue_in_flight()
    assert queue.receive() is None  # acked — a non-retryable failure must never redeliver


def test_process_message_unknown_job_id_acks_without_touching_db() -> None:
    queue = FakeJobQueue()
    bogus_job_id = str(uuid.uuid4())
    queue.send(bogus_job_id)
    message = queue.receive()
    assert message is not None

    worker_module._process_message(message, TEST_S3_BUCKET, FakeReviewPipeline(), queue)

    queue.requeue_in_flight()
    assert queue.receive() is None  # acked, not left dangling
