"""
The worker: one process, one loop — claim a job, run it, mark it done. No Celery/SQS.

Run it with:  python -m apps.worker.worker

`run_review` is the important part: it wraps the EXISTING, untouched LangGraph pipeline
(src/agents/orchestrator.py::compile_review_graph — the real entry point, confirmed in
PROJECT_STATE.md §1; the build plan's `get_graph()` was only ever a placeholder name).
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.worker.jobs import claim_next_job, mark_done, mark_failed
from apps.worker.version import PIPELINE_VERSION
from db.session import SyncSessionLocal
from src.agents.doc_processor import stage_files
from src.agents.orchestrator import compile_review_graph
from src.parsers.file_io import load_pdf_bytes
from src.rag.indexing.indexer import index_spec_pdf

POLL_INTERVAL_SECONDS = 2


def _s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


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


def run_review(db: Session, submittal_id: uuid.UUID, s3_bucket: str) -> None:
    submittal = _get_submittal(db, submittal_id)
    files = _get_files(db, submittal_id)
    if not files:
        raise ValueError(f"submittal {submittal_id} has no submittal_files rows to review")

    file_contents = {
        f["original_name"]: load_pdf_bytes(_s3_uri(s3_bucket, f["s3_key"])) for f in files
    }
    declared_labels = {f["original_name"]: f["declared_label"] for f in files}

    # MUST run before compile_review_graph().stream() — doc_processor_node is the first
    # node in the graph and pops these bytes out of the staging dict immediately.
    stage_files(str(submittal_id), file_contents, declared_labels)

    initial_state: dict = {
        "authority": submittal["authority"],
        "submittal_id": str(submittal_id),
        "project_id": str(submittal["project_id"]),
        "tenant_id": str(submittal["tenant_id"]),
        "review_date": str(date.today()),
    }

    db.execute(
        text("UPDATE submittals SET status='RUNNING', started_at=NOW() WHERE id=:id"),
        {"id": submittal_id},
    )
    db.commit()

    graph = compile_review_graph()
    final_state: dict = {}
    seq = 0
    for event in graph.stream(initial_state, stream_mode="updates"):
        node_name, node_state = next(iter(event.items()))
        # Each node returns the full spread state (project convention — see
        # PROJECT_STATE.md §2 / engineering_log Issue 4.1), so this merge is a superset,
        # not a partial accumulation, but it's kept defensive rather than assumed.
        final_state = {**final_state, **node_state}
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

    report = final_state.get("report")
    if report is None:
        raise RuntimeError(
            f"submittal {submittal_id}: graph completed without producing a report — "
            f"final_state keys: {sorted(final_state.keys())}"
        )

    # Already computed by spec_verifier on every review (SubmittalReviewState carries them
    # under these exact keys per src/agents/state.py) — previously discarded once the graph
    # finished. Persisting them is what makes clickable spec/evidence citations possible;
    # see migration 005.
    requirements_artifact = final_state.get("requirements_artifact")
    verification_artifact = final_state.get("verification_artifact")

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
            "report": json.dumps(report),
            "rec": report.get("overall_recommendation"),
            "v": PIPELINE_VERSION,
            "id": submittal_id,
            # Local disk path (see migration 004) — valid because Phase 6 runs the API
            # and worker on the same EC2 host; chat (apps/api/routers/chat.py) reads it.
            "ksp": final_state.get("knowledge_store_id"),
            "req_artifact": json.dumps(requirements_artifact) if requirements_artifact else None,
            "ver_artifact": json.dumps(verification_artifact) if verification_artifact else None,
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

    pdf_bytes = load_pdf_bytes(_s3_uri(s3_bucket, row["source_s3_key"]))

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


def _handle_job(db: Session, job: dict, s3_bucket: str) -> None:
    if job["job_type"] == "review":
        run_review(db, job["submittal_id"], s3_bucket)
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


def run_worker(s3_bucket: str) -> None:
    print(f"[worker] starting — pipeline_version={PIPELINE_VERSION}, bucket={s3_bucket}")
    while True:
        with SyncSessionLocal() as db:
            job = claim_next_job(db)
            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            print(f"[worker] claimed job {job['id']} type={job['job_type']}")
            try:
                _handle_job(db, job, s3_bucket)
                mark_done(db, job["id"])
                print(f"[worker] job {job['id']} done")
            except Exception as exc:
                db.rollback()
                mark_failed(db, job["id"])
                if job["submittal_id"] is not None:
                    db.execute(
                        text(
                            "UPDATE submittals SET status='FAILED', error_message=:err WHERE id=:id"
                        ),
                        {"err": str(exc), "id": job["submittal_id"]},
                    )
                    db.commit()
                print(f"[worker] job {job['id']} FAILED: {exc}")


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()

    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise SystemExit("S3_BUCKET_NAME must be set to run the worker")
    run_worker(bucket)
