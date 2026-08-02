"""
The seam over frozen src/. Wraps compile_review_graph() / stage_files() completely
unchanged (src/agents/orchestrator.py, src/agents/doc_processor.py) — CLAUDE.md rule 1
forbids modifying either. This file translates between them and core.ports.ReviewPipelinePort;
it does not alter their behavior.
"""
from __future__ import annotations

from collections.abc import Callable

import openai

from core.errors import (
    PipelineInvalidInputError,
    PipelineRateLimitedError,
    PipelineTransientError,
)
from core.models import ReviewRequest, ReviewResult
from core.ports import ReviewPipelinePort
from src.agents.doc_processor import stage_files
from src.agents.orchestrator import compile_review_graph


class LangGraphReviewPipeline(ReviewPipelinePort):
    def run(
        self,
        request: ReviewRequest,
        on_stage_complete: Callable[[str], None],
    ) -> ReviewResult:
        stage_files(request.submittal_id, request.file_contents, request.declared_labels)

        initial_state: dict = {
            "authority": request.authority,
            "submittal_id": request.submittal_id,
            "project_id": request.project_id,
            "tenant_id": request.tenant_id,
            "review_date": request.review_date,
        }

        # LangSmith tags/metadata on the run config — not a change to the graph itself,
        # just how this call is traced. Lets a support question ("what happened on
        # submittal X") be answered by searching LangSmith for the submittal_id tag,
        # instead of needing to reproduce the run.
        trace_config = {
            "run_name": f"review:{request.submittal_id}",
            "tags": ["review", request.authority],
            "metadata": {
                "tenant_id": request.tenant_id,
                "project_id": request.project_id,
                "submittal_id": request.submittal_id,
            },
        }

        try:
            graph = compile_review_graph()
            final_state: dict = {}
            for event in graph.stream(initial_state, config=trace_config, stream_mode="updates"):
                node_name, node_state = next(iter(event.items()))
                # Each node returns the full spread state (project convention — see
                # PROJECT_STATE.md §2), so this merge is a superset, kept defensive
                # rather than assumed, matching the original worker.py logic exactly.
                final_state = {**final_state, **node_state}
                on_stage_complete(node_name)
        except openai.APIConnectionError as exc:
            raise PipelineTransientError(
                f"pipeline lost connection to OpenAI: {exc}", cause=exc
            ) from exc
        except openai.APITimeoutError as exc:
            raise PipelineTransientError(f"pipeline timed out calling OpenAI: {exc}", cause=exc) from exc
        except openai.RateLimitError as exc:
            raise PipelineRateLimitedError(f"OpenAI rate limit hit: {exc}", cause=exc) from exc
        except Exception as exc:
            # Deliberately NOT retryable by default: an exception we don't recognize is
            # more likely a real bug than a transient blip (unlike the network-specific
            # cases above, which we know are safe to retry). Retrying an unknown failure
            # blindly just delays the DLQ from surfacing a real problem — see
            # architecture-kit/references/error-model.md's "retrying non-retryable
            # errors" anti-pattern.
            raise PipelineInvalidInputError(
                f"unexpected pipeline failure: {exc}", cause=exc
            ) from exc

        report = final_state.get("report")
        if report is None:
            raise PipelineInvalidInputError(
                f"submittal {request.submittal_id}: graph completed without producing a "
                f"report — final_state keys: {sorted(final_state.keys())}"
            )

        return ReviewResult(
            report=report,
            requirements_artifact=final_state.get("requirements_artifact"),
            verification_artifact=final_state.get("verification_artifact"),
            knowledge_store_path=final_state.get("knowledge_store_id"),
        )
