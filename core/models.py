"""
Domain models — Layer 2. Deliberately thin right now: Phase 0 only needs the shapes
ReviewPipelinePort passes across the src/ seam. More are added as each later phase's port
needs them (repositories in Phase 5, queue messages in Phase 1) — not speculatively now.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReviewRequest:
    """Everything the frozen src/ pipeline needs for one review. Mirrors exactly what
    apps/worker/worker.py::run_review() already assembles by hand before calling
    compile_review_graph() — this dataclass just gives that bundle a name and a type."""

    submittal_id: str
    tenant_id: str
    project_id: str
    authority: str
    review_date: str
    file_contents: dict[str, bytes]
    declared_labels: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewResult:
    """What comes out of a review. `requirements_artifact` / `verification_artifact` /
    `knowledge_store_path` are optional because older code paths (and FakeReviewPipeline)
    may not populate them — mirrors the real pipeline's `final_state.get(...)` pattern,
    which already treats these as optional (see migration 005's docstring)."""

    report: dict
    requirements_artifact: dict | None = None
    verification_artifact: dict | None = None
    knowledge_store_path: str | None = None


@dataclass(frozen=True)
class QueueMessage:
    """One received SQS message. `receipt_handle` is what `JobQueuePort.delete()` needs to
    ack it — it's a per-receive token, not the message's permanent id, and expires with the
    queue's visibility timeout. `job_id` is the only thing carried in the message body (see
    core/ports.py::JobQueuePort docstring for why): the `jobs` table row it points at is the
    actual source of truth for job_type/submittal_id/payload."""

    job_id: str
    receipt_handle: str
