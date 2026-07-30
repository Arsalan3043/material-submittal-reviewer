"""adapters/pipeline/fake_pipeline.py — the zero-cost double that stands in for the real,
paid ~6-minute LangGraph pipeline everywhere except one final real-adapter smoke test."""
from __future__ import annotations

import pytest

from adapters.pipeline.fake_pipeline import FakeReviewPipeline
from core.errors import PipelineTransientError
from core.models import ReviewRequest, ReviewResult
from core.ports import ReviewPipelinePort


def _request(authority: str) -> ReviewRequest:
    return ReviewRequest(
        submittal_id="sub-1",
        tenant_id="tenant-1",
        project_id="project-1",
        authority=authority,
        review_date="2026-07-29",
        file_contents={"spec.pdf": b"%PDF-fake"},
        declared_labels={"spec.pdf": "datasheet"},
    )


def test_fake_pipeline_satisfies_the_port() -> None:
    assert isinstance(FakeReviewPipeline(), ReviewPipelinePort)


def test_taq_authority_emits_avl_check_node() -> None:
    seen: list[str] = []
    FakeReviewPipeline().run(_request("TAQA"), on_stage_complete=seen.append)
    assert "avl_check" in seen
    assert "skip_avl" not in seen


def test_non_taqa_authority_emits_skip_avl_node() -> None:
    seen: list[str] = []
    FakeReviewPipeline().run(_request("ADM"), on_stage_complete=seen.append)
    assert "skip_avl" in seen
    assert "avl_check" not in seen


def test_emits_all_eleven_stages_in_order() -> None:
    seen: list[str] = []
    FakeReviewPipeline().run(_request("ADM"), on_stage_complete=seen.append)
    assert seen == [
        "doc_processor",
        "completeness",
        "boq_drawing",
        "spec_verifier",
        "validity_checker",
        "skip_avl",
        "statement",
        "table_auditor",
        "consistency",
        "others",
        "report_compiler",
    ]


def test_default_result_is_a_valid_review_result() -> None:
    result = FakeReviewPipeline().run(_request("ADM"), on_stage_complete=lambda _: None)
    assert isinstance(result, ReviewResult)
    assert result.report["overall_recommendation"] == "APPROVE"
    assert result.report["submittal_id"] == "sub-1"


def test_custom_result_is_returned_verbatim() -> None:
    custom = ReviewResult(report={"overall_recommendation": "REJECT"})
    result = FakeReviewPipeline(result=custom).run(_request("ADM"), on_stage_complete=lambda _: None)
    assert result is custom


def test_raises_after_emitting_all_stage_callbacks() -> None:
    seen: list[str] = []
    failure = PipelineTransientError("simulated failure")
    with pytest.raises(PipelineTransientError):
        FakeReviewPipeline(raises=failure).run(_request("ADM"), on_stage_complete=seen.append)
    assert len(seen) == 11
