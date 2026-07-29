"""
Job-queue primitives on top of the `jobs` table (see migrations/versions/001_initial_schema.py).

No Celery, no SQS, no broker — the queue IS the `jobs` table. `claim_next_job` is the one
statement that makes this safe: `FOR UPDATE SKIP LOCKED` lets multiple workers poll the same
table without ever double-claiming a row, using nothing but Postgres row locking. This is a
deliberate simplicity choice (CLAUDE.md rule 3 / planning/05_build_plan_for_claude_code.md
Phase 2) appropriate for the first 20-40 clients, not a stopgap — there is no plan to
"graduate" to Celery later, only a trigger table if job volume ever demands it.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def claim_next_job(db: Session) -> dict | None:
    """Atomically claim the oldest PENDING job, or return None if the queue is empty."""
    row = (
        db.execute(
            text(
                """
                UPDATE jobs
                SET status = 'RUNNING', picked_at = NOW(), attempts = attempts + 1
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status = 'PENDING'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, job_type, submittal_id, payload
                """
            )
        )
        .mappings()
        .fetchone()
    )
    db.commit()
    return dict(row) if row is not None else None


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
    return row[0]
