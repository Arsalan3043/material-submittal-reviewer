"""
One settings object, validated at startup, read from exactly one place. Replaces the ~10
scattered `os.environ` reads across apps/api/auth.py, apps/api/s3.py, apps/api/main.py,
apps/worker/worker.py, db/session.py (see notes/architecture.md V2) — those reads meant a
missing var crashed at import time inside a random module instead of at boot with a clear
message, and made every one of those modules impossible to unit-test without real
environment variables set.

Local dev reads from .env (python-dotenv, unchanged). Production (Phase 2) will source the
same variable names from AWS Systems Manager Parameter Store instead — this file's shape
doesn't change either way, only where the process's environment gets populated from before
Settings() is constructed.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # tolerate vars belonging to other tools sharing this .env
    )

    # ── Required — no default, so a missing value fails at boot, not at 2am on first use ──
    openai_api_key: str
    database_url: str
    s3_bucket_name: str
    cognito_user_pool_id: str
    cognito_app_client_id: str
    sqs_queue_url: str

    # ── Required with sensible non-secret defaults ──
    aws_region: str = "us-east-1"
    cognito_region: str = "us-east-1"
    frontend_origins: str = "http://localhost:3000"
    pipeline_version: str = "v1"

    # ── Optional integrations — absence degrades, never crashes ──
    cohere_api_key: str | None = None
    langchain_api_key: str | None = Field(default=None, alias="LANGCHAIN_API_KEY")
    langchain_tracing_v2: bool = False
    langchain_project: str | None = None

    # ── Worker/API-specific DB roles (db/session.py already implements this split —
    # see PROJECT_STATE.md §13.4 for why api_app/worker_app must differ) ──
    worker_database_url: str | None = None
    admin_database_url: str | None = None

    @property
    def frontend_origin_list(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Cached — Settings() re-parses env on every call otherwise. One instance per process."""
    return Settings()  # type: ignore[call-arg]  # pydantic-settings fills required fields from env
