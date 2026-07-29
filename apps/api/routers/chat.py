"""
Chat routes — post-review Q&A. Built entirely from submittals.report (JSONB) + the
project's authority; deliberately does NOT depend on knowledge_store_path (migration 004).
query_agent_node only reads authority/submittal_id/spec_clause/report from state — none of
its three routes (spec_rag, submittal_rag, report_json) require the knowledge store itself,
so this works today without the local-disk-sharing assumption that column exists for.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUser, get_current_user, get_db
from src.agents.query_agent import query_agent_node

router = APIRouter(prefix="/api/v1/submittals", tags=["chat"])


class ChatRequest(BaseModel):
    question: str


@router.post("/{submittal_id}/chat")
async def ask_chat(
    submittal_id: uuid.UUID,
    body: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                """
                SELECT s.status, s.report, p.authority
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
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"submittal is {row['status']}, not COMPLETED yet"
        )

    report = row["report"]
    if isinstance(report, str):  # driver-dependent: asyncpg doesn't always auto-decode JSONB
        report = json.loads(report)
    report = report or {}

    state = {
        "authority": row["authority"],
        "submittal_id": str(submittal_id),
        "spec_clause": report.get("spec_clause", ""),
        "report": report,
    }

    answer = query_agent_node(state, body.question)

    await db.execute(
        text(
            """
            INSERT INTO chat_turns
                (submittal_id, tenant_id, user_id, question, answer, route, sources)
            VALUES
                (:submittal_id, :tenant_id, :user_id, :question, :answer, :route,
                 CAST(:sources AS JSONB))
            """
        ),
        {
            "submittal_id": submittal_id,
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.user_id,
            "question": body.question,
            "answer": answer.answer,
            "route": answer.source.value,
            "sources": json.dumps(answer.source_references),
        },
    )
    await db.commit()

    return {
        "answer": answer.answer,
        "source": answer.source.value,
        "source_references": answer.source_references,
        "confidence": answer.confidence,
    }


@router.get("/{submittal_id}/chat")
async def get_chat_history(
    submittal_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    submittal = (
        await db.execute(text("SELECT id FROM submittals WHERE id = :id"), {"id": submittal_id})
    ).fetchone()
    if submittal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submittal not found")

    rows = (
        await db.execute(
            text(
                """
                SELECT question, answer, route, sources, created_at
                FROM chat_turns WHERE submittal_id = :id ORDER BY created_at
                """
            ),
            {"id": submittal_id},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]
