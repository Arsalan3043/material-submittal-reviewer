"""
Finding decisions — the human confirm/dismiss/edit event log (migration 007,
notes/11_pilot_bar_tickets.md Ticket 2) and the reason-code taxonomy behind it. A finding
row (migration 006, Ticket 1) stays immutable; every human action is a new append-only row
here instead. Reason codes are global reference data (no tenant_id/project_id — see
db/models.py::ReasonCode), so the /reason-codes route needs no RLS scoping.
"""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUser, get_current_user, get_db

router = APIRouter(prefix="/api/v1", tags=["findings"])


class RecordDecisionRequest(BaseModel):
    action: Literal["confirm", "dismiss", "edit"]
    reason_code: str
    note: str | None = None
    corrected_fields: dict | None = None


def _maybe_parse(value):
    """asyncpg's JSON decoding for raw text() queries isn't guaranteed — same defensive
    pattern already used in chat.py, submittals.py, and worker.py."""
    if isinstance(value, str):
        return json.loads(value)
    return value


@router.post("/findings/{finding_id}/decisions", status_code=status.HTTP_201_CREATED)
async def record_finding_decision(
    finding_id: uuid.UUID,
    body: RecordDecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.corrected_fields is not None and body.action != "edit":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "corrected_fields is only valid for action='edit'"
        )

    # RLS-scoped: a finding_id belonging to another tenant simply won't be found here,
    # same pattern as every other route in this API.
    finding = (
        await db.execute(
            text("SELECT tenant_id, project_id, submittal_id FROM findings WHERE id = :id"),
            {"id": finding_id},
        )
    ).mappings().fetchone()
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")

    valid_reason = (
        await db.execute(
            text(
                "SELECT 1 FROM reason_codes WHERE code = :code AND action = :action"
            ),
            {"code": body.reason_code, "action": body.action},
        )
    ).fetchone()
    if valid_reason is None:
        # Friendlier than letting the composite FK constraint raise a raw IntegrityError —
        # this is the exact case that constraint exists to prevent (db/models.py::
        # FindingDecision docstring), checked here first so it surfaces as a clean 400.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{body.reason_code}' is not a valid reason code for action '{body.action}'",
        )

    row = (
        await db.execute(
            text(
                """
                INSERT INTO finding_decisions
                    (finding_id, tenant_id, project_id, submittal_id, actor_user_id,
                     action, reason_code, note, corrected_fields)
                VALUES
                    (:finding_id, :tenant_id, :project_id, :submittal_id, :actor_user_id,
                     :action, :reason_code, :note, CAST(:corrected_fields AS JSONB))
                RETURNING id, created_at
                """
            ),
            {
                "finding_id": finding_id,
                "tenant_id": finding["tenant_id"],
                "project_id": finding["project_id"],
                "submittal_id": finding["submittal_id"],
                "actor_user_id": current_user.user_id,
                "action": body.action,
                "reason_code": body.reason_code,
                "note": body.note,
                "corrected_fields": (
                    json.dumps(body.corrected_fields) if body.corrected_fields is not None else None
                ),
            },
        )
    ).mappings().fetchone()
    await db.commit()
    assert row is not None  # RETURNING id always yields exactly one row on a successful INSERT

    return {
        "id": str(row["id"]),
        "finding_id": str(finding_id),
        "actor_user_id": str(current_user.user_id),
        "action": body.action,
        "reason_code": body.reason_code,
        "note": body.note,
        "corrected_fields": body.corrected_fields,
        "created_at": row["created_at"],
    }


@router.get("/findings/{finding_id}/decisions")
async def get_finding_decisions(
    finding_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full, ordered decision history for one finding — the "full history is queryable"
    half of Ticket 1's acceptance criterion. RLS-scoped via the same finding lookup as the
    POST route above."""
    finding = (
        await db.execute(text("SELECT id FROM findings WHERE id = :id"), {"id": finding_id})
    ).fetchone()
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")

    rows = (
        await db.execute(
            text(
                """
                SELECT id, actor_user_id, action, reason_code, note, corrected_fields, created_at
                FROM finding_decisions WHERE finding_id = :id ORDER BY created_at
                """
            ),
            {"id": finding_id},
        )
    ).mappings().fetchall()
    return [
        {
            "id": str(r["id"]),
            "actor_user_id": str(r["actor_user_id"]),
            "action": r["action"],
            "reason_code": r["reason_code"],
            "note": r["note"],
            "corrected_fields": _maybe_parse(r["corrected_fields"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("/reason-codes")
async def list_reason_codes(
    action: Literal["confirm", "dismiss", "edit"] | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Global reference data (db/models.py::ReasonCode) — no RLS, same as spec_documents.
    Still behind auth (Depends(get_current_user)) since it's only useful to a signed-in
    reviewer, not because the data itself is tenant-scoped."""
    if action is not None:
        rows = (
            await db.execute(
                text("SELECT code, action, label FROM reason_codes WHERE action = :action ORDER BY code"),
                {"action": action},
            )
        ).mappings().fetchall()
    else:
        rows = (
            await db.execute(text("SELECT code, action, label FROM reason_codes ORDER BY action, code"))
        ).mappings().fetchall()
    return [dict(r) for r in rows]
