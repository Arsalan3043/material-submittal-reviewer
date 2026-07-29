"""
Submittal routes — create (returns presigned upload URLs), start (enqueue review job),
status, progress events, list. Mirrors the worker's expectations exactly: s3_key format
{tenant_id}/{project_id}/{submittal_id}/{filename}, job_type='review' with no payload
(apps/worker/worker.py::run_review reads everything it needs from the DB rows themselves).
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUser, get_current_user, get_db
from apps.api.s3 import presigned_get_url, presigned_put_url
from src.rag.query.query_constructor import build_query

router = APIRouter(prefix="/api/v1", tags=["submittals"])


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
    submittal_id = submittal_row[0]

    uploads = []
    for f in body.files:
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
                "declared_label": f.declared_label,
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
    return {"status": "queued", "job_id": str(job_row[0])}


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
