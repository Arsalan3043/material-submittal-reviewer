"""Shared fixtures for tests/unit — every test here must run at zero OpenAI/AWS cost."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_langsmith_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force tracing off regardless of what a developer's own shell happens to export.
    Nothing in tests/unit should ever call the real LangGraph pipeline or talk to
    LangSmith — this is a backstop, not the primary guarantee (FakeReviewPipeline, used
    throughout, never touches either)."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
