"""
Interfaces Layer 2 depends on — owned by the core, implemented by adapters/ (per
architecture-kit/references/layers.md: "the core owns the contract; adapters conform to
it"). Start with one file; split into core/ports/ per-domain modules past ~400 lines.

ReviewPipelinePort and JobQueuePort exist so far. Per the kit's volatility rule — "an
interface with one implementation and no volatility justification is indirection, not
abstraction" — the rest from notes/architecture.md §3.3 (ObjectStoragePort, SpecIndexPort,
SubmittalRepository, IdentityPort) are added when their own phase actually needs them
(Phase 4 for the vector store, Phase 5 for the repositories), not speculatively now.

ReviewPipelinePort exists ahead of its "natural" phase because it's the prerequisite for
everything else in Phase 0: it's what lets tests and the eval harness run against
FakeReviewPipeline instead of paying real OpenAI cost per run.

JobQueuePort exists for Phase 1: it's the seam between apps/worker/worker.py's main loop and
whatever's actually dispatching jobs (SQS today via adapters/queue/sqs_queue.py) — a second
implementation isn't planned, but the seam is what makes the worker loop testable without a
real AWS account, the same reason ReviewPipelinePort exists.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from core.models import QueueMessage, ReviewRequest, ReviewResult


@runtime_checkable
class ReviewPipelinePort(Protocol):
    """The seam over the frozen src/ pipeline. Designed from THIS codebase's actual call
    site (apps/worker/worker.py::run_review) rather than LangGraph's API — per the kit:
    "design the interface from the caller's needs, not from the vendor's SDK." A method
    named `stream(state, stream_mode=...)` would just be LangGraph renamed; `run()` taking
    a domain request and a plain callback is not.
    """

    def run(
        self,
        request: ReviewRequest,
        on_stage_complete: Callable[[str], None],
    ) -> ReviewResult:
        """Runs one full review. Calls `on_stage_complete(node_name)` once per pipeline
        stage as it finishes — the caller decides what that means (write a
        submittal_events row, print to a log, do nothing in a test). Raises a typed
        core.errors.PipelineError subclass on failure; never lets a vendor exception
        (openai.*, chromadb.*) escape this boundary."""
        ...


@runtime_checkable
class JobQueuePort(Protocol):
    """The seam over SQS. Deliberately minimal — `send`, `receive`, `delete` — because
    that's the entire vocabulary apps/worker/worker.py's loop and the API routers that
    enqueue jobs actually need. No `extend_visibility`/heartbeat method: the queue's
    visibility timeout is configured generously enough (20 min) to cover the real pipeline's
    ~6-minute runtime with margin, so extending it mid-job isn't needed yet — add it if a job
    ever legitimately runs long enough to need it, not before.

    The message body is intentionally just a job id, not the full job payload: the `jobs`
    Postgres table stays the single source of truth for job_type/submittal_id/payload (the
    audit record), and SQS is purely the dispatch/retry mechanism layered on top of it — two
    copies of the same data drifting apart is a worse failure mode than one extra DB read per
    job.
    """

    def send(self, job_id: str) -> None:
        """Enqueues a job for a worker to pick up. Raises core.errors.QueueError subclasses
        on failure; never lets botocore exceptions escape this boundary."""
        ...

    def receive(self) -> QueueMessage | None:
        """Long-polls for one message. Returns None if the queue was empty for the whole
        poll window (not an error — the normal "nothing to do right now" case)."""
        ...

    def delete(self, receipt_handle: str) -> None:
        """Acks a message — removes it from the queue permanently. Call this after a job
        succeeds AND after a job fails non-retryably (both cases: this message should never
        be redelivered). Do NOT call this for a retryable failure — leaving the message alone
        lets it become visible again after the visibility timeout for automatic redelivery."""
        ...
