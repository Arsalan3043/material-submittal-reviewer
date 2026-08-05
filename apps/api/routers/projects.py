"""Project routes — create/list/detail, attach spec libraries."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUser, get_current_user, get_db
from apps.api.section_labels import SECTION_LABELS
from src.config import get_authority_profile

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str
    authority: str  # "ADM" | "TAQA" — first-launch scope, see planning docs
    description: str | None = None


class AttachSpecRequest(BaseModel):
    spec_document_id: uuid.UUID


@router.post("")
async def create_project(
    body: CreateProjectRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.authority not in ("ADM", "TAQA"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "authority must be ADM or TAQA")
    row = (
        await db.execute(
            text(
                """
                INSERT INTO projects (tenant_id, name, authority, description, created_by)
                VALUES (:tenant_id, :name, :authority, :description, :created_by)
                RETURNING id, name, authority, description, created_at
                """
            ),
            {
                "tenant_id": current_user.tenant_id,
                "name": body.name,
                "authority": body.authority,
                "description": body.description,
                "created_by": current_user.user_id,
            },
        )
    ).mappings().fetchone()
    await db.commit()
    return dict(row)


@router.get("")
async def list_projects(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # No WHERE tenant_id here — RLS (app.tenant_id, set by get_db from the verified token)
    # is what actually restricts this, not this query.
    rows = (
        await db.execute(
            text(
                "SELECT id, name, authority, description, created_at "
                "FROM projects ORDER BY created_at DESC"
            )
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                "SELECT id, name, authority, description, created_at "
                "FROM projects WHERE id = :id"
            ),
            {"id": project_id},
        )
    ).mappings().fetchone()
    if row is None:
        # RLS makes "exists but belongs to another tenant" and "doesn't exist" look the
        # same here — that's correct: never confirm another tenant's UUID is real.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return dict(row)


@router.get("/{project_id}/sections")
async def get_project_sections(
    project_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Section labels the upload picker offers for this project's authority.

    Served per-project rather than per-authority so the client needs only the project id,
    and so adding an authority never touches the frontend. Both profiles share the same
    10-item index today (TAQAProfile inherits ADMProfile's), but that's a fact about the
    profiles, not an assumption made here.
    """
    row = (
        await db.execute(
            text("SELECT authority FROM projects WHERE id = :id"), {"id": project_id}
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    get_authority_profile(row[0])  # 400s on an authority with no profile, rather than
    # returning labels the pipeline can't act on.
    return {"authority": row[0], "sections": SECTION_LABELS}


@router.post("/{project_id}/specs")
async def attach_spec(
    project_id: uuid.UUID,
    body: AttachSpecRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = (
        await db.execute(text("SELECT id FROM projects WHERE id = :id"), {"id": project_id})
    ).fetchone()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    spec = (
        await db.execute(
            text("SELECT id FROM spec_documents WHERE id = :id"), {"id": body.spec_document_id}
        )
    ).fetchone()
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "spec_document not found")

    await db.execute(
        text(
            """
            INSERT INTO project_specs (project_id, tenant_id, spec_document_id)
            VALUES (:project_id, :tenant_id, :spec_document_id)
            ON CONFLICT (project_id, spec_document_id) DO NOTHING
            """
        ),
        {
            "project_id": project_id,
            "tenant_id": current_user.tenant_id,
            "spec_document_id": body.spec_document_id,
        },
    )
    await db.commit()
    return {"status": "attached"}
