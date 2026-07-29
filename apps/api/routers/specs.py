"""
Spec routes — presigned upload for spec PDFs, enqueue indexing, list what's indexed.
Write operations gated to tenant_admin/super_admin: spec libraries are shared reference
data (spec_documents has no tenant_id — see migration 002), so any tenant_admin can index
one, not just whoever's tenant happens to "own" it (nobody does).
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUser, get_current_user, get_db
from apps.api.s3 import presigned_put_url

router = APIRouter(prefix="/api/v1/specs", tags=["specs"])


def _require_tenant_admin(current_user: CurrentUser) -> None:
    if current_user.role not in ("tenant_admin", "super_admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant_admin role required")


class SpecUploadUrlRequest(BaseModel):
    authority: str
    network: str
    filename: str


class SpecIndexRequest(BaseModel):
    authority: str
    network: str
    filename: str
    s3_key: str


@router.post("/upload-url")
async def spec_upload_url(
    body: SpecUploadUrlRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    _require_tenant_admin(current_user)
    s3_key = f"specs/{body.authority}/{body.network}/{body.filename}"
    return {"s3_key": s3_key, "upload_url": presigned_put_url(s3_key)}


@router.post("/index")
async def spec_index(
    body: SpecIndexRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_tenant_admin(current_user)
    row = (
        await db.execute(
            text(
                """
                INSERT INTO spec_documents
                    (authority, network_name, source_file, source_s3_key, qdrant_collection)
                VALUES (:authority, :network, :filename, :s3_key, :collection)
                ON CONFLICT (authority, network_name, source_file)
                DO UPDATE SET source_s3_key = EXCLUDED.source_s3_key
                RETURNING id
                """
            ),
            {
                "authority": body.authority,
                "network": body.network,
                "filename": body.filename,
                "s3_key": body.s3_key,
                # "qdrant_collection" per the column's forward-looking name (Phase 4) — holds
                # the current local ChromaDB collection name until that migration happens.
                "collection": f"{body.authority.lower()}_specifications",
            },
        )
    ).fetchone()
    spec_document_id = row[0]

    job_row = (
        await db.execute(
            text(
                """
                INSERT INTO jobs (job_type, payload, status)
                VALUES ('index', CAST(:payload AS JSONB), 'PENDING')
                RETURNING id
                """
            ),
            {"payload": json.dumps({"spec_document_id": str(spec_document_id)})},
        )
    ).fetchone()
    await db.commit()
    return {"spec_document_id": str(spec_document_id), "job_id": str(job_row[0])}


@router.get("")
async def list_specs(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT id, authority, network_name, source_file, chunk_count, indexed_at
                FROM spec_documents ORDER BY authority, network_name
                """
            )
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]
