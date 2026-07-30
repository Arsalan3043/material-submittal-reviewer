"""composition.py — the one file allowed to import concrete adapters. Just proves the
wiring is correct; never calls .run() (that would hit the real, paid src/ pipeline)."""
from __future__ import annotations

from adapters.pipeline.langgraph_pipeline import LangGraphReviewPipeline
from composition import build_review_pipeline
from core.ports import ReviewPipelinePort


def test_build_review_pipeline_returns_a_port_conforming_instance() -> None:
    pipeline = build_review_pipeline()
    assert isinstance(pipeline, ReviewPipelinePort)
    assert isinstance(pipeline, LangGraphReviewPipeline)
