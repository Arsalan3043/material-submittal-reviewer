"""
Submittal routes — create (returns presigned upload URLs), start (enqueue review job),
status, progress events, list. Mirrors the worker's expectations exactly: s3_key format
{tenant_id}/{project_id}/{submittal_id}/{filename}, job_type='review' with no payload
(apps/worker/worker.py::run_review reads everything it needs from the DB rows themselves).
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUser, get_current_user, get_db
from apps.api.s3 import presigned_get_url, presigned_put_url
from apps.api.section_labels import infer_declared_label
from composition import build_job_queue
from db.session import get_db as db_session_scope
from src.rag.query.query_constructor import build_query

_STREAM_POLL_SECONDS = 1
_TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")

router = APIRouter(prefix="/api/v1", tags=["submittals"])

# Built once at import time, like apps/api/s3.py's boto3 client — SQSJobQueue holds a
# boto3 client internally, and constructing a fresh one per request would redo local
# credential/config resolution on every single call for no benefit.
_job_queue = build_job_queue()


class SubmittalFileRequest(BaseModel):
    filename: str
    declared_label: str | None = None


class CreateSubmittalRequest(BaseModel):
    material_desc: str | None = None
    files: list[SubmittalFileRequest]


async def _get_project_or_404(db: AsyncSession, project_id: uuid.UUID) -> None:
    row = (
        await db.execute(text("SELECT id FROM projects WHERE id = :id"), {"id": project_id})
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")


@router.post("/projects/{project_id}/submittals")
async def create_submittal(
    project_id: uuid.UUID,
    body: CreateSubmittalRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "at least one file is required")
    await _get_project_or_404(db, project_id)

    submittal_row = (
        await db.execute(
            text(
                """
                INSERT INTO submittals (tenant_id, project_id, user_id, material_desc, status)
                VALUES (:tenant_id, :project_id, :user_id, :material_desc, 'CREATED')
                RETURNING id
                """
            ),
            {
                "tenant_id": current_user.tenant_id,
                "project_id": project_id,
                "user_id": current_user.user_id,
                "material_desc": body.material_desc,
            },
        )
    ).fetchone()
    assert submittal_row is not None  # RETURNING id always yields exactly one row on a successful INSERT
    submittal_id = submittal_row[0]

    uploads = []
    for f in body.files:
        # Fall back to the filename when the client sends no label. An unlabelled scanned
        # package classifies as others/low across the board and produces a confidently wrong
        # all-documents-missing review, so this must not depend on the UI asking — see
        # apps/api/section_labels.py for the full failure mode.
        declared_label = f.declared_label or infer_declared_label(f.filename)
        s3_key = f"{current_user.tenant_id}/{project_id}/{submittal_id}/{f.filename}"
        await db.execute(
            text(
                """
                INSERT INTO submittal_files
                    (submittal_id, tenant_id, original_name, declared_label, s3_key)
                VALUES (:submittal_id, :tenant_id, :original_name, :declared_label, :s3_key)
                """
            ),
            {
                "submittal_id": submittal_id,
                "tenant_id": current_user.tenant_id,
                "original_name": f.filename,
                "declared_label": declared_label,
                "s3_key": s3_key,
            },
        )
        uploads.append(
            {"filename": f.filename, "s3_key": s3_key, "upload_url": presigned_put_url(s3_key)}
        )

    await db.commit()
    return {"submittal_id": str(submittal_id), "uploads": uploads}


@router.post("/submittals/{submittal_id}/start")
async def start_submittal(
    submittal_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    submittal = (
        await db.execute(
            text("SELECT id, status FROM submittals WHERE id = :id"), {"id": submittal_id}
        )
    ).mappings().fetchone()
    if submittal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")
    if submittal["status"] != "CREATED":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"submittal is already {submittal['status']}"
        )

    file_count = (
        await db.execute(
            text("SELECT count(*) FROM submittal_files WHERE submittal_id = :id"),
            {"id": submittal_id},
        )
    ).scalar()
    if not file_count:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no files uploaded for this submittal yet")

    await db.execute(
        text("UPDATE submittals SET status='QUEUED', queued_at=NOW() WHERE id=:id"),
        {"id": submittal_id},
    )
    job_row = (
        await db.execute(
            text(
                """
                INSERT INTO jobs (job_type, submittal_id, status)
                VALUES ('review', :submittal_id, 'PENDING')
                RETURNING id
                """
            ),
            {"submittal_id": submittal_id},
        )
    ).fetchone()
    await db.commit()
    assert job_row is not None  # RETURNING id always yields exactly one row on a successful INSERT
    job_id = job_row[0]
    # jobs row above is the audit/status record; SQS is the actual dispatch mechanism a
    # worker long-polls (see apps/worker/worker.py). Sent after the commit so a worker can
    # never receive a job_id the DB hasn't durably recorded yet. boto3 is sync, so this runs
    # off the event loop rather than blocking it.
    await asyncio.to_thread(_job_queue.send, str(job_id))
    return {"status": "queued", "job_id": str(job_id)}


@router.get("/submittals/{submittal_id}")
async def get_submittal(
    submittal_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                """
                SELECT id, project_id, status, material_desc, recommendation,
                       report, error_message, created_at, started_at, completed_at
                FROM submittals WHERE id = :id
                """
            ),
            {"id": submittal_id},
        )
    ).mappings().fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")
    return dict(row)


@router.get("/submittals/{submittal_id}/events")
async def get_submittal_events(
    submittal_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # submittal_events has no RLS policy of its own (migration 001) — ownership is enforced
    # here by requiring the submittal lookup (which IS RLS-protected) to succeed first.
    submittal = (
        await db.execute(text("SELECT id FROM submittals WHERE id = :id"), {"id": submittal_id})
    ).fetchone()
    if submittal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")

    rows = (
        await db.execute(
            text(
                """
                SELECT sequence_number, node_name, status, created_at
                FROM submittal_events WHERE submittal_id = :id ORDER BY sequence_number
                """
            ),
            {"id": submittal_id},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


@router.get("/submittals/{submittal_id}/findings")
async def get_submittal_findings(
    submittal_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Reads the findings table (migration 006) — the persisted, stable-ID source of truth
    for individual findings, written by the worker in the same transaction as review
    completion — rather than submittals.report JSONB, which has no per-finding identity
    for a later human decision (Ticket 2) to reference. RLS-scoped via the standard
    get_db(tenant_id) pattern, same as every other route here.

    Each finding's current_decision is derived from the LATEST row in finding_decisions
    (DISTINCT ON ... ORDER BY created_at DESC) at read time, per Ticket 2's explicit
    instruction — findings.py's `decisions` table is append-only, and no mutable status
    column exists anywhere to go stale.
    """
    submittal = (
        await db.execute(text("SELECT id FROM submittals WHERE id = :id"), {"id": submittal_id})
    ).fetchone()
    if submittal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")

    rows = (
        await db.execute(
            text(
                """
                SELECT f.id, f.category, f.severity, f.description, f.action_required,
                       f.clause_reference, f.spec_document_id, f.spec_page,
                       f.source_document_id, f.source_page, f.confidence, f.pipeline_node,
                       f.model_version, f.prompt_version, f.pipeline_version, f.created_at,
                       d.id AS decision_id, d.actor_user_id AS decision_actor_user_id,
                       d.action AS decision_action, d.reason_code AS decision_reason_code,
                       d.note AS decision_note, d.corrected_fields AS decision_corrected_fields,
                       d.created_at AS decision_created_at
                FROM findings f
                LEFT JOIN LATERAL (
                    SELECT id, actor_user_id, action, reason_code, note, corrected_fields, created_at
                    FROM finding_decisions
                    WHERE finding_id = f.id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) d ON true
                WHERE f.submittal_id = :id
                ORDER BY f.created_at
                """
            ),
            {"id": submittal_id},
        )
    ).mappings().fetchall()

    findings_out = []
    for r in rows:
        row = dict(r)
        current_decision = None
        if row.pop("decision_id") is not None:
            current_decision = {
                "actor_user_id": str(row.pop("decision_actor_user_id")),
                "action": row.pop("decision_action"),
                "reason_code": row.pop("decision_reason_code"),
                "note": row.pop("decision_note"),
                "corrected_fields": _maybe_parse(row.pop("decision_corrected_fields")),
                "created_at": row.pop("decision_created_at"),
            }
        else:
            for key in (
                "decision_actor_user_id",
                "decision_action",
                "decision_reason_code",
                "decision_note",
                "decision_corrected_fields",
                "decision_created_at",
            ):
                row.pop(key, None)
        row["current_decision"] = current_decision
        findings_out.append(row)
    return findings_out


@router.get("/submittals/{submittal_id}/stream")
async def stream_submittal_progress(
    submittal_id: uuid.UUID,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Server-Sent Events replacement for client-side polling of /events and /{id}. Chosen
    deliberately over Postgres LISTEN/NOTIFY (the "true push" alternative): the actual
    bottleneck in review latency is the ~5-minute silent gap inside doc_processor, which
    emits no event either way — so real-time push over the currently-instant events buys
    negligible user-facing benefit for meaningfully more moving parts (a raw connection
    outside the pool, reconnect/missed-notification handling). This still polls Postgres,
    just server-side on a 1s interval instead of the browser making a new HTTP request
    every 2s, forwarding changes over one held-open connection.

    The `Depends(get_db)` session above is used ONLY for the existence check before the
    response is constructed. It is NOT reused inside event_source() — FastAPI tears down
    `yield`-based dependencies as soon as the route function returns (i.e. the moment the
    StreamingResponse object is constructed), which happens well before the generator
    body actually runs. Confirmed the hard way: reusing `db` inside the generator raised
    `asyncpg.InvalidTextRepresentationError: invalid input syntax for type uuid: ""` on
    the very first poll tick — the session was already torn down. Each tick below opens
    its own short-lived session instead, which also removes the alternative tradeoff this
    endpoint used to have (holding one connection + one open transaction for the full
    ~6-minute life of a stream).

    Native EventSource can't send an Authorization header, which is why the frontend
    consumes this with fetch() + a manual reader instead of `new EventSource(...)`.
    """
    exists = (
        await db.execute(text("SELECT id FROM submittals WHERE id = :id"), {"id": submittal_id})
    ).fetchone()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")

    tenant_id = current_user.tenant_id

    async def event_source():
        last_seq = -1
        while True:
            if await request.is_disconnected():
                break

            async with db_session_scope(tenant_id=tenant_id) as tick_db:
                submittal_row = (
                    await tick_db.execute(
                        text("SELECT status FROM submittals WHERE id = :id"),
                        {"id": submittal_id},
                    )
                ).mappings().fetchone()

                new_events = (
                    await tick_db.execute(
                        text(
                            """
                            SELECT sequence_number, node_name, created_at
                            FROM submittal_events
                            WHERE submittal_id = :id AND sequence_number > :last_seq
                            ORDER BY sequence_number
                            """
                        ),
                        {"id": submittal_id, "last_seq": last_seq},
                    )
                ).mappings().fetchall()

            if submittal_row is None:
                break

            for e in new_events:
                last_seq = e["sequence_number"]
                payload = {
                    "sequence_number": e["sequence_number"],
                    "node_name": e["node_name"],
                    "created_at": e["created_at"].isoformat(),
                }
                yield f"event: node_complete\ndata: {json.dumps(payload)}\n\n"

            yield f"event: status\ndata: {json.dumps({'status': submittal_row['status']})}\n\n"

            if submittal_row["status"] in _TERMINAL_STATUSES:
                yield (
                    f"event: done\n"
                    f"data: {json.dumps({'status': submittal_row['status']})}\n\n"
                )
                break

            await asyncio.sleep(_STREAM_POLL_SECONDS)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disables response buffering on nginx-style reverse proxies — without this,
            # a proxy in front of the API in production could hold the whole stream until
            # it closes, defeating the point.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/projects/{project_id}/submittals")
async def list_project_submittals(
    project_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT id, status, material_desc, recommendation, created_at, completed_at
                FROM submittals WHERE project_id = :project_id ORDER BY created_at DESC
                """
            ),
            {"project_id": project_id},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


def _maybe_parse(value):
    """asyncpg's JSON decoding for raw text() queries isn't guaranteed — same defensive
    pattern already used in chat.py and worker.py."""
    if isinstance(value, str):
        return json.loads(value)
    return value


@router.get("/submittals/{submittal_id}/citations")
async def get_submittal_citations(
    submittal_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Joins requirements_artifact (clause/page provenance) with verification_artifact
    (status/evidence) by requirement id, and resolves clickable, page-jumping presigned
    URLs for both the spec PDF and each piece of submittal evidence — the data these two
    columns exist for (migration 005).

    Which spec PDF a citation belongs to isn't stored directly — resolved the same way
    spec_verifier itself does, via the EXISTING build_query() network resolution
    (src/rag/query/query_constructor.py, imported not modified) applied to this submittal's
    own spec_clause + material_description + authority.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT s.status, s.requirements_artifact, s.verification_artifact,
                       p.authority
                FROM submittals s JOIN projects p ON p.id = s.project_id
                WHERE s.id = :id
                """
            ),
            {"id": submittal_id},
        )
    ).mappings().fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")
    if row["status"] != "COMPLETED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"submittal is {row['status']}, not COMPLETED yet")

    requirements_artifact = _maybe_parse(row["requirements_artifact"])
    verification_artifact = _maybe_parse(row["verification_artifact"])
    if not requirements_artifact or not verification_artifact:
        # Reviews completed before migration 005 have no artifact saved — nothing to cite.
        return []

    requirements_by_id = {r["id"]: r for r in requirements_artifact["requirements"]}

    # Resolve the spec network once for the whole submittal (one clause -> one network,
    # same resolution spec_verifier itself relied on to retrieve this text in the first place).
    resolved = build_query(
        spec_clause=requirements_artifact["spec_clause"],
        material_description=requirements_artifact["material_description"],
        authority=row["authority"],
    )
    spec_doc = (
        await db.execute(
            text(
                """
                SELECT source_s3_key FROM spec_documents
                WHERE authority = :authority AND network_name = :network
                LIMIT 1
                """
            ),
            {"authority": row["authority"], "network": resolved.network},
        )
    ).mappings().fetchone()
    spec_view_base = presigned_get_url(spec_doc["source_s3_key"]) if spec_doc else None

    # Evidence citations point at specific submittal files — need their S3 keys.
    files = (
        await db.execute(
            text("SELECT original_name, s3_key FROM submittal_files WHERE submittal_id = :id"),
            {"id": submittal_id},
        )
    ).mappings().fetchall()
    file_key_by_name = {f["original_name"]: f["s3_key"] for f in files}

    def evidence_view_url(snippet: dict) -> str | None:
        key = file_key_by_name.get(snippet["source_document"])
        if not key:
            return None
        return f"{presigned_get_url(key)}#page={snippet['page']}"

    citations = []
    for verification in verification_artifact["verifications"]:
        requirement = requirements_by_id.get(verification["requirement_id"])
        citations.append(
            {
                "requirement_id": verification["requirement_id"],
                "requirement_summary": verification["requirement_summary"],
                "status": verification["status"],
                "confidence": verification["confidence"],
                "reasoning": verification["reasoning"],
                "spec_citation": (
                    {
                        "clause": requirement["source_clause"],
                        "text": requirement["source_text"],
                        "page": requirement["source_page"],
                        "view_url": (
                            f"{spec_view_base}#page={requirement['source_page']}"
                            if spec_view_base
                            else None
                        ),
                    }
                    if requirement
                    else None
                ),
                "evidence_citations": [
                    {
                        "document": snippet["source_document"],
                        "page": snippet["page"],
                        "text": snippet["text"],
                        "view_url": evidence_view_url(snippet),
                    }
                    for snippet in verification.get("evidence_found", [])
                ],
            }
        )
    return citations
