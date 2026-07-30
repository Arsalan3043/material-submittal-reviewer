"""
The composition root (architecture-kit/references/layers.md § "Wiring"). The only file
allowed to import a concrete adapter from every layer — everywhere else imports ports.
Grows one factory function per port as each phase introduces one (spec index in Phase 4,
repositories in Phase 5).
"""
from __future__ import annotations

from adapters.pipeline.langgraph_pipeline import LangGraphReviewPipeline
from adapters.queue.sqs_queue import SQSJobQueue
from config import get_settings
from core.ports import JobQueuePort, ReviewPipelinePort


def build_review_pipeline() -> ReviewPipelinePort:
    """The real pipeline, wired for actual runtime use (apps/worker/worker.py). Tests
    construct adapters.pipeline.fake_pipeline.FakeReviewPipeline directly instead of
    calling this — no test should go through the composition root."""
    return LangGraphReviewPipeline()


def build_job_queue() -> JobQueuePort:
    """The real SQS queue, wired for actual runtime use (apps/worker/worker.py, and the API
    routers that enqueue jobs). Tests construct a fake JobQueuePort directly instead of
    calling this — no test should go through the composition root."""
    settings = get_settings()
    return SQSJobQueue(queue_url=settings.sqs_queue_url, region=settings.aws_region)
