"""
The worker: one process, one loop — long-poll SQS for a job, run it, ack it. Dispatch moved
from Postgres `FOR UPDATE SKIP LOCKED` to SQS in Phase 1 (see apps/worker/jobs.py's
docstring); the `jobs` table stays the audit/status record either way.

Run it with:  python -m apps.worker.worker

`run_review` wraps the review pipeline through core.ports.ReviewPipelinePort instead of
calling src/agents/orchestrator.py::compile_review_graph directly — the seam that lets
tests run against adapters.pipeline.fake_pipeline.FakeReviewPipeline instead of the real
(paid, ~6-minute) thing. The real adapter, adapters.pipeline.langgraph_pipeline, still
wraps that exact same real entry point unchanged.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import botocore.exceptions
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.worker.findings import extract_findings
from apps.worker.jobs import mark_done, mark_failed, requeue_job, try_claim_job
from apps.worker.version import PIPELINE_VERSION
from composition import build_job_queue, build_review_pipeline
from core.errors import AppError, StorageObjectMissingError, StorageTransientError
from core.models import QueueMessage, ReviewRequest
from core.ports import JobQueuePort, ReviewPipelinePort
from db.session import SyncSessionLocal
from src.parsers.file_io import load_pdf_bytes
from src.rag.indexing.indexer import index_spec_pdf


def _s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def _load_pdf_bytes_typed(uri: str) -> bytes:
    """
    Wraps src/parsers/file_io.py::load_pdf_bytes (unchanged — the one sanctioned src/
    file) with typed-error translation. Directly evidence-based: both real failures hit
    during this project's own testing happened right here — a genuinely missing S3 object
    (NoSuchKey, not retryable) and a connection dropped mid-download (IncompleteRead,
    retryable). Before this, both were indistinguishable `except Exception` catches.
    """
    try:
        return load_pdf_bytes(uri)
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            raise StorageObjectMissingError(f"S3 object not found: {uri}", cause=exc) from exc
        raise StorageTransientError(f"S3 error fetching {uri}: {code}", cause=exc) from exc
    except (
        botocore.exceptions.EndpointConnectionError,
        botocore.exceptions.ConnectionError,
        ConnectionError,
    ) as exc:
        raise StorageTransientError(f"connection failure fetching {uri}: {exc}", cause=exc) from exc


def _get_submittal(db: Session, submittal_id: uuid.UUID) -> dict:
    # authority lives on projects, not submittals (Phase 1 schema — it's a project-level
    # choice made once at project creation, not per-submittal), hence the join.
    row = (
        db.execute(
            text(
                """
                SELECT s.id, s.tenant_id, s.project_id, s.user_id, s.material_desc, s.status,
                       p.authority
                FROM submittals s
                JOIN projects p ON p.id = s.project_id
                WHERE s.id = :id
                """
            ),
            {"id": submittal_id},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        raise ValueError(f"submittal {submittal_id} not found")
    return dict(row)


def _get_files(db: Session, submittal_id: uuid.UUID) -> list[dict]:
    rows = (
        db.execute(
            text(
                """
                SELECT original_name, declared_label, s3_key
                FROM submittal_files
                WHERE submittal_id = :id
                """
            ),
            {"id": submittal_id},
        )
        .mappings()
        .fetchall()
    )
    return [dict(r) for r in rows]


def run_review(
    db: Session, submittal_id: uuid.UUID, s3_bucket: str, pipeline: ReviewPipelinePort
) -> None:
    submittal = _get_submittal(db, submittal_id)
    files = _get_files(db, submittal_id)
    if not files:
        raise ValueError(f"submittal {submittal_id} has no submittal_files rows to review")

    file_contents = {
        f["original_name"]: _load_pdf_bytes_typed(_s3_uri(s3_bucket, f["s3_key"]))
        for f in files
    }
    declared_labels = {f["original_name"]: f["declared_label"] for f in files}

    request = ReviewRequest(
        submittal_id=str(submittal_id),
        tenant_id=str(submittal["tenant_id"]),
        project_id=str(submittal["project_id"]),
        authority=submittal["authority"],
        # UTC, not local time — the worker may run in a different timezone than the UAE
        # clients it serves; a fixed reference avoids the review_date shifting by a day
        # depending on which timezone the process happens to run in.
        review_date=str(datetime.now(UTC).date()),
        file_contents=file_contents,
        declared_labels=declared_labels,
    )

    db.execute(
        text("UPDATE submittals SET status='RUNNING', started_at=NOW() WHERE id=:id"),
        {"id": submittal_id},
    )
    db.commit()

    seq = 0

    def on_stage_complete(node_name: str) -> None:
        nonlocal seq
        db.execute(
            text(
                """
                INSERT INTO submittal_events (submittal_id, sequence_number, node_name, status)
                VALUES (:sid, :seq, :node, 'complete')
                """
            ),
            {"sid": submittal_id, "seq": seq, "node": node_name},
        )
        db.commit()
        seq += 1

    result = pipeline.run(request, on_stage_complete=on_stage_complete)

    # Findings are inserted in the SAME transaction as the completion UPDATE below (no
    # commit in between) — if either half fails, both roll back. A submittal never ends
    # up COMPLETED with its findings missing, or vice versa. Rows are immutable from here:
    # nothing else in the system ever UPDATEs a findings row (see db/models.py::Finding).
    findings_rows = extract_findings(
        result.report,
        tenant_id=str(submittal["tenant_id"]),
        project_id=str(submittal["project_id"]),
        submittal_id=str(submittal_id),
        pipeline_version=PIPELINE_VERSION,
    )
    if findings_rows:
        db.execute(
            text(
                """
                INSERT INTO findings
                    (id, tenant_id, project_id, submittal_id, category, severity,
                     description, action_required, clause_reference, spec_document_id,
                     spec_page, source_document_id, source_page, confidence,
                     pipeline_node, model_version, prompt_version, pipeline_version)
                VALUES
                    (:id, :tenant_id, :project_id, :submittal_id, :category, :severity,
                     :description, :action_required, :clause_reference, :spec_document_id,
                     :spec_page, :source_document_id, :source_page, :confidence,
                     :pipeline_node, :model_version, :prompt_version, :pipeline_version)
                """
            ),
            findings_rows,
        )

    db.execute(
        text(
            """
            UPDATE submittals
            SET status='COMPLETED', report=CAST(:report AS JSONB), recommendation=:rec,
                pipeline_version=:v, completed_at=NOW(), knowledge_store_path=:ksp,
                requirements_artifact=CAST(:req_artifact AS JSONB),
                verification_artifact=CAST(:ver_artifact AS JSONB)
            WHERE id=:id
            """
        ),
        {
            "report": json.dumps(result.report),
            "rec": result.report.get("overall_recommendation"),
            "v": PIPELINE_VERSION,
            "id": submittal_id,
            # Local disk path (see migration 004) — valid because Phase 6 runs the API
            # and worker on the same EC2 host; chat (apps/api/routers/chat.py) reads it.
            "ksp": result.knowledge_store_path,
            "req_artifact": (
                json.dumps(result.requirements_artifact) if result.requirements_artifact else None
            ),
            "ver_artifact": (
                json.dumps(result.verification_artifact) if result.verification_artifact else None
            ),
        },
    )
    db.commit()


def run_index(db: Session, payload: dict, s3_bucket: str) -> None:
    """
    Indexes one spec_documents row into local ChromaDB via the EXISTING, untouched
    src/rag/indexing/indexer.py::index_spec_pdf — the same function apps/pages/spec_manager.py
    already calls, and the same one used to index the 3 real ADM specs during Phase 2
    testing. index_spec_pdf needs a real filesystem path (opens it with PyMuPDF), so the S3
    object is downloaded to a temp file first — there is no bytes-in, path-out variant to
    call instead.
    """
    if isinstance(payload, str):  # driver-dependent: not guaranteed to auto-decode JSONB
        payload = json.loads(payload)
    spec_document_id = payload.get("spec_document_id")
    if not spec_document_id:
        raise ValueError("index job payload missing spec_document_id")

    row = (
        db.execute(
            text(
                """
                SELECT authority, network_name, source_file, source_s3_key, qdrant_collection
                FROM spec_documents WHERE id = :id
                """
            ),
            {"id": spec_document_id},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        raise ValueError(f"spec_document {spec_document_id} not found")

    pdf_bytes = _load_pdf_bytes_typed(_s3_uri(s3_bucket, row["source_s3_key"]))

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / row["source_file"]
        tmp_path.write_bytes(pdf_bytes)
        chunk_count = index_spec_pdf(
            pdf_path=tmp_path,
            authority=row["authority"],
            network=row["network_name"],
            collection_name=row["qdrant_collection"],
        )

    db.execute(
        text(
            "UPDATE spec_documents SET chunk_count=:n, indexed_at=NOW() WHERE id=:id"
        ),
        {"n": chunk_count, "id": spec_document_id},
    )
    db.commit()


def _handle_job(db: Session, job: dict, s3_bucket: str, pipeline: ReviewPipelinePort) -> None:
    if job["job_type"] == "review":
        run_review(db, job["submittal_id"], s3_bucket, pipeline)
    elif job["job_type"] == "index":
        run_index(db, job["payload"] or {}, s3_bucket)
    else:
        # 'embed' is a reserved job_type (Phase 1 schema comment) with no handler yet —
        # src/rag/submittal_rag/ is fully coded but never called by anything (PROJECT_STATE.md
        # §4). Fail loudly rather than silently no-op.
        raise NotImplementedError(
            f"job_type={job['job_type']!r} has no worker handler yet — 'review' and "
            f"'index' are wired, 'embed' is not."
        )


def _process_message(
    message: QueueMessage,
    s3_bucket: str,
    pipeline: ReviewPipelinePort,
    queue: JobQueuePort,
) -> None:
    """One receive-claim-handle-ack cycle, pulled out of run_worker's infinite loop so it's
    directly testable without needing to break out of a `while True`."""
    with SyncSessionLocal() as db:
        job = try_claim_job(db, message.job_id)
        if job is None:
            # Unknown job id, or already claimed by an earlier delivery of this same
            # message (SQS is at-least-once, not exactly-once) — either way there's
            # nothing to do; ack so it doesn't sit in-flight until the DLQ.
            print(f"[worker] job {message.job_id} already claimed or unknown — acking")
            queue.delete(message.receipt_handle)
            return

        print(f"[worker] claimed job {job['id']} type={job['job_type']}")
        try:
            _handle_job(db, job, s3_bucket, pipeline)
            mark_done(db, job["id"])
            queue.delete(message.receipt_handle)
            print(f"[worker] job {job['id']} done")
        except Exception as exc:  # noqa: BLE001 — the outermost process boundary: an
            # unrecognized exception must become a FAILED (or requeued) job, never a
            # dead worker process. core.errors.AppError subclasses are the specific,
            # actionable errors; this is the deliberate backstop underneath them, not a
            # substitute for typing errors properly at their source.
            db.rollback()
            # Unknown/untyped errors default to non-retryable — same philosophy as
            # adapters/pipeline/langgraph_pipeline.py's catch-all: an error we don't
            # recognize is more likely a real bug than a transient blip, and retrying
            # blindly just delays the DLQ from surfacing a real problem.
            retryable = exc.retryable if isinstance(exc, AppError) else False

            if retryable:
                # Leave the message un-acked — SQS redelivers it after the visibility
                # timeout, up to the queue's maxReceiveCount, then auto-moves it to the
                # DLQ. Revert the job to PENDING so that redelivery can re-claim it
                # (try_claim_job requires PENDING, and this job is currently RUNNING).
                requeue_job(db, job["id"])
                if job["submittal_id"] is not None:
                    db.execute(
                        text(
                            "UPDATE submittals SET status='QUEUED', error_message=:err WHERE id=:id"
                        ),
                        {"err": str(exc), "id": job["submittal_id"]},
                    )
                    db.commit()
                print(f"[worker] job {job['id']} FAILED (retryable, requeued): {exc}")
            else:
                mark_failed(db, job["id"])
                if job["submittal_id"] is not None:
                    db.execute(
                        text(
                            "UPDATE submittals SET status='FAILED', error_message=:err WHERE id=:id"
                        ),
                        {"err": str(exc), "id": job["submittal_id"]},
                    )
                    db.commit()
                queue.delete(message.receipt_handle)
                print(f"[worker] job {job['id']} FAILED (not retryable): {exc}")


def run_worker(
    s3_bucket: str,
    pipeline: ReviewPipelinePort | None = None,
    queue: JobQueuePort | None = None,
) -> None:
    """`pipeline`/`queue` are injectable so tests can pass fakes; real runtime use (the
    __main__ block below) builds the real ones via composition.py.

    receive() long-polls internally (20s), so this loop doesn't need its own sleep between
    empty polls the way the old SKIP LOCKED version did."""
    pipeline = pipeline or build_review_pipeline()
    queue = queue or build_job_queue()
    print(f"[worker] starting — pipeline_version={PIPELINE_VERSION}, bucket={s3_bucket}")
    while True:
        message = queue.receive()
        if message is None:
            continue
        _process_message(message, s3_bucket, pipeline, queue)


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()

    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise SystemExit("S3_BUCKET_NAME must be set to run the worker")
    run_worker(bucket)
