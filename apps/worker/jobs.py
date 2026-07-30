"""
Job-queue primitives on top of the `jobs` table (see migrations/versions/001_initial_schema.py).

Dispatch is SQS (adapters/queue/sqs_queue.py) as of Phase 1 — `jobs` is now the audit/status
record, not the dispatch mechanism itself. `try_claim_job` replaces the old `claim_next_job`'s
`FOR UPDATE SKIP LOCKED` claiming: exclusivity is now SQS's job (a message is only delivered
to one worker at a time, per its visibility timeout), so this just needs to atomically flip
PENDING -> RUNNING and tell the caller whether it actually won that flip — SQS's at-least-once
delivery means the same message can legitimately arrive twice (e.g. a slow ack racing a
visibility-timeout expiry), and a second arrival must be a safe no-op, not a double-run.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def try_claim_job(db: Session, job_id: uuid.UUID | str) -> dict | None:
    """Atomically flips PENDING -> RUNNING for this job id. Returns None if the job is
    unknown or was already claimed (not PENDING) — the caller should treat that as "already
    handled, just ack the message," not an error."""
    row = (
        db.execute(
            text(
                """
                UPDATE jobs
                SET status = 'RUNNING', picked_at = NOW(), attempts = attempts + 1
                WHERE id = :id AND status = 'PENDING'
                RETURNING id, job_type, submittal_id, payload
                """
            ),
            {"id": job_id},
        )
        .mappings()
        .fetchone()
    )
    db.commit()
    return dict(row) if row is not None else None


def requeue_job(db: Session, job_id: uuid.UUID) -> None:
    """Reverts a claimed job back to PENDING after a retryable failure, so the SQS
    redelivery (message left un-acked, visibility timeout expires) can re-claim it. Without
    this, the redelivered message would find the job stuck at RUNNING and try_claim_job would
    treat it as already-claimed, silently dropping the retry."""
    db.execute(text("UPDATE jobs SET status='PENDING' WHERE id=:id"), {"id": job_id})
    db.commit()


def mark_done(db: Session, job_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE jobs SET status='DONE', finished_at=NOW() WHERE id=:id"),
        {"id": job_id},
    )
    db.commit()


def mark_failed(db: Session, job_id: uuid.UUID) -> None:
    """
    Flip the job to FAILED. `jobs` has no error_message column by design (a job is
    infrastructure plumbing, not user-facing) — the actual error is written onto
    submittals.error_message by the caller, which the frontend/API can surface.
    """
    db.execute(
        text("UPDATE jobs SET status='FAILED', finished_at=NOW() WHERE id=:id"),
        {"id": job_id},
    )
    db.commit()


def enqueue_job(
    db: Session,
    job_type: str,
    submittal_id: uuid.UUID | str | None = None,
    payload: dict | None = None,
) -> uuid.UUID:
    row = db.execute(
        text(
            """
            INSERT INTO jobs (job_type, submittal_id, payload)
            VALUES (:job_type, :submittal_id, CAST(:payload AS JSONB))
            RETURNING id
            """
        ),
        {
            "job_type": job_type,
            "submittal_id": str(submittal_id) if submittal_id is not None else None,
            "payload": json.dumps(payload) if payload is not None else None,
        },
    ).fetchone()
    db.commit()
    assert row is not None  # RETURNING id always yields exactly one row on a successful INSERT
    return row[0]
