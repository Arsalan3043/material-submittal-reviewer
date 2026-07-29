"""
FastAPI dependencies. get_current_user verifies the bearer token then looks up the
matching Postgres user row — the ONLY place tenant_id is inferred rather than passed
in explicitly. Every route after that uses current_user.tenant_id, never a client-
supplied one, and get_db sets RLS's app.tenant_id from that same trusted value.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth import verify_token
from db.session import get_db as _get_db_ctx

_security = HTTPBearer()


@dataclass
class CurrentUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    email: str


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_security),
) -> CurrentUser:
    claims = verify_token(creds.credentials)
    cognito_sub = claims.get("sub")

    # tenant_id genuinely isn't known yet at this point — this lookup runs with no RLS
    # tenant context (users has no RLS policy, only projects/submittals/chat_turns do,
    # per migration 001), scoped only by cognito_sub, which came from a verified signature.
    async with _get_db_ctx(tenant_id=None) as db:
        row = (
            await db.execute(
                text("SELECT id, tenant_id, role, email FROM users WHERE cognito_sub = :sub"),
                {"sub": cognito_sub},
            )
        ).mappings().fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token is valid but no matching account exists — "
            "accounts are provisioned by an admin, not self-registered",
        )

    return CurrentUser(
        user_id=row["id"], tenant_id=row["tenant_id"], role=row["role"], email=row["email"]
    )


async def get_db(
    current_user: CurrentUser = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    async with _get_db_ctx(tenant_id=current_user.tenant_id) as db:
        yield db
