"""
FastAPI app — Phase 3. /api/v1/me stays here as the minimal proof-of-auth route that
started this phase; everything else lives in apps/api/routers/.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# Must run before any of the imports below: apps/api/s3.py, apps/api/auth.py, and
# db/session.py all read required vars via bare os.environ[...] at import time (unlike
# apps/worker/worker.py, which already calls load_dotenv() in its __main__ block). Without
# this, LangSmith tracing for the chat route's query_agent_node call — and everything else
# read from .env — silently only works if the shell happened to export .env first.
load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.dependencies import CurrentUser, get_current_user
from apps.api.routers import chat, projects, specs, submittals

app = FastAPI(title="Material Submittal Reviewer API")

# Frontend is a separate origin (localhost:3000 in dev, a real domain later) calling this
# API directly with a Bearer token — no cookies involved, so allow_credentials stays False
# and there's no CSRF exposure from this. FRONTEND_ORIGINS is a comma-separated list so prod
# can add its real domain without a code change.
_origins = os.environ.get("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(submittals.router)
app.include_router(specs.router)
app.include_router(chat.router)


@app.get("/api/v1/me")
async def whoami(current_user: CurrentUser = Depends(get_current_user)):
    return {
        "user_id": str(current_user.user_id),
        "tenant_id": str(current_user.tenant_id),
        "role": current_user.role,
        "email": current_user.email,
    }
