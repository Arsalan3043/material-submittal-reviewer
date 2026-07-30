"""
In-memory JobQueuePort double — zero AWS calls, zero network. Mirrors real SQS's essential
behavior: a receipt handle is a per-receive token (not the job id itself), and a message
stays "in flight" (not re-receivable) until deleted, so tests exercise the same ack
discipline the real worker loop depends on.
"""
from __future__ import annotations

import uuid
from collections import deque

from core.models import QueueMessage
from core.ports import JobQueuePort


class FakeJobQueue(JobQueuePort):
    def __init__(self) -> None:
        self._pending: deque[str] = deque()
        self._in_flight: dict[str, str] = {}  # receipt_handle -> job_id

    def send(self, job_id: str) -> None:
        self._pending.append(job_id)

    def receive(self) -> QueueMessage | None:
        if not self._pending:
            return None
        job_id = self._pending.popleft()
        receipt_handle = str(uuid.uuid4())
        self._in_flight[receipt_handle] = job_id
        return QueueMessage(job_id=job_id, receipt_handle=receipt_handle)

    def delete(self, receipt_handle: str) -> None:
        self._in_flight.pop(receipt_handle, None)

    def requeue_in_flight(self) -> None:
        """Test helper simulating a visibility-timeout redelivery: puts every un-acked
        message back on the pending queue, as SQS would after the timeout expires."""
        for job_id in self._in_flight.values():
            self._pending.append(job_id)
        self._in_flight.clear()
