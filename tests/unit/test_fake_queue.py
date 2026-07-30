"""adapters/queue/fake_queue.py — the in-memory JobQueuePort double. Confirms it mirrors the
real SQS behavior the worker loop actually depends on: a message is invisible once received,
until acked (delete) or requeued (simulated redelivery)."""
from __future__ import annotations

from adapters.queue.fake_queue import FakeJobQueue
from core.ports import JobQueuePort


def test_satisfies_the_port() -> None:
    assert isinstance(FakeJobQueue(), JobQueuePort)


def test_receive_on_empty_queue_returns_none() -> None:
    assert FakeJobQueue().receive() is None


def test_send_then_receive_round_trips_job_id() -> None:
    queue = FakeJobQueue()
    queue.send("job-1")
    message = queue.receive()
    assert message is not None
    assert message.job_id == "job-1"


def test_received_message_is_not_receivable_again_until_requeued() -> None:
    queue = FakeJobQueue()
    queue.send("job-1")
    queue.receive()
    assert queue.receive() is None  # in flight, not re-deliverable yet


def test_delete_acks_permanently() -> None:
    queue = FakeJobQueue()
    queue.send("job-1")
    message = queue.receive()
    assert message is not None
    queue.delete(message.receipt_handle)
    queue.requeue_in_flight()  # nothing in flight left to requeue
    assert queue.receive() is None


def test_requeue_in_flight_makes_unacked_messages_receivable_again() -> None:
    queue = FakeJobQueue()
    queue.send("job-1")
    first = queue.receive()
    assert first is not None
    queue.requeue_in_flight()
    second = queue.receive()
    assert second is not None
    assert second.job_id == "job-1"
    assert second.receipt_handle != first.receipt_handle  # a fresh receipt per (re)delivery
