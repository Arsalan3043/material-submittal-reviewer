"""adapters/pipeline/langgraph_pipeline.py — the seam over the frozen src/ LangGraph
pipeline. compile_review_graph()/stage_files() are monkeypatched with fakes so this test
never touches OpenAI, S3, or the real graph — it only proves the translation layer (typed
errors, report extraction, and the LangSmith trace config) behaves correctly."""
from __future__ import annotations

import pytest

import adapters.pipeline.langgraph_pipeline as langgraph_pipeline_module
from adapters.pipeline.langgraph_pipeline import LangGraphReviewPipeline
from core.errors import PipelineInvalidInputError
from core.models import ReviewRequest


def _request(authority: str = "ADM") -> ReviewRequest:
    return ReviewRequest(
        submittal_id="sub-1",
        tenant_id="tenant-1",
        project_id="project-1",
        authority=authority,
        review_date="2026-07-29",
        file_contents={"spec.pdf": b"%PDF-fake"},
        declared_labels={"spec.pdf": "datasheet"},
    )


class _FakeCompiledGraph:
    """Stands in for the real compiled LangGraph app. Records the config it was called
    with so the test can assert on the LangSmith tags/metadata without a real graph."""

    def __init__(self, events: list[dict]) -> None:
        self._events = events
        self.last_call: dict | None = None

    def stream(self, initial_state: dict, config: dict | None = None, **kwargs) -> list[dict]:
        self.last_call = {"initial_state": initial_state, "config": config, "kwargs": kwargs}
        return self._events


@pytest.fixture(autouse=True)
def _stub_stage_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(langgraph_pipeline_module, "stage_files", lambda *a, **kw: None)


def _patch_graph(monkeypatch: pytest.MonkeyPatch, events: list[dict]) -> _FakeCompiledGraph:
    fake_graph = _FakeCompiledGraph(events)
    monkeypatch.setattr(
        langgraph_pipeline_module, "compile_review_graph", lambda: fake_graph
    )
    return fake_graph


def test_run_passes_tenant_project_submittal_tags_into_stream_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_graph = _patch_graph(
        monkeypatch, [{"report_compiler": {"report": {"overall_recommendation": "APPROVE"}}}]
    )

    request = _request(authority="TAQA")
    LangGraphReviewPipeline().run(request, on_stage_complete=lambda _: None)

    assert fake_graph.last_call is not None
    config = fake_graph.last_call["config"]
    assert config["tags"] == ["review", "TAQA"]
    assert config["metadata"] == {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "submittal_id": "sub-1",
    }
    assert config["run_name"] == "review:sub-1"


def test_run_still_streams_with_updates_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_graph = _patch_graph(
        monkeypatch, [{"report_compiler": {"report": {"overall_recommendation": "APPROVE"}}}]
    )

    LangGraphReviewPipeline().run(_request(), on_stage_complete=lambda _: None)

    assert fake_graph.last_call is not None
    assert fake_graph.last_call["kwargs"] == {"stream_mode": "updates"}


def test_run_calls_on_stage_complete_per_node_and_returns_merged_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_graph(
        monkeypatch,
        [
            {"doc_processor": {"knowledge_store_id": "ks-1"}},
            {"report_compiler": {"report": {"overall_recommendation": "RESUBMIT"}}},
        ],
    )

    seen: list[str] = []
    result = LangGraphReviewPipeline().run(_request(), on_stage_complete=seen.append)

    assert seen == ["doc_processor", "report_compiler"]
    assert result.report == {"overall_recommendation": "RESUBMIT"}
    assert result.knowledge_store_path == "ks-1"


def test_run_raises_typed_error_when_graph_completes_without_a_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_graph(monkeypatch, [{"doc_processor": {"knowledge_store_id": "ks-1"}}])

    with pytest.raises(PipelineInvalidInputError):
        LangGraphReviewPipeline().run(_request(), on_stage_complete=lambda _: None)
