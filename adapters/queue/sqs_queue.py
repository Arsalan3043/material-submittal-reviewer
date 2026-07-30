"""
The seam over real SQS. Translates botocore exceptions into core.errors.QueueError
subclasses; the worker loop and the routers that enqueue jobs never see botocore directly.
"""
from __future__ import annotations

import boto3
import botocore.exceptions

from core.errors import QueueTransientError
from core.models import QueueMessage
from core.ports import JobQueuePort

# Long polling — SQS holds the connection open up to this many seconds waiting for a
# message before returning empty, instead of the worker hammering ReceiveMessage every
# couple seconds for nothing. Matches the console setting made on the queue itself.
_WAIT_TIME_SECONDS = 20


class SQSJobQueue(JobQueuePort):
    def __init__(self, queue_url: str, region: str) -> None:
        self._queue_url = queue_url
        self._client = boto3.client("sqs", region_name=region)

    def send(self, job_id: str) -> None:
        try:
            self._client.send_message(QueueUrl=self._queue_url, MessageBody=job_id)
        except (
            botocore.exceptions.EndpointConnectionError,
            botocore.exceptions.ConnectionError,
            botocore.exceptions.ClientError,
        ) as exc:
            raise QueueTransientError(f"failed to enqueue job {job_id}: {exc}", cause=exc) from exc

    def receive(self) -> QueueMessage | None:
        try:
            response = self._client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=_WAIT_TIME_SECONDS,
            )
        except (
            botocore.exceptions.EndpointConnectionError,
            botocore.exceptions.ConnectionError,
            botocore.exceptions.ClientError,
        ) as exc:
            raise QueueTransientError(f"failed to receive from queue: {exc}", cause=exc) from exc

        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        return QueueMessage(job_id=message["Body"], receipt_handle=message["ReceiptHandle"])

    def delete(self, receipt_handle: str) -> None:
        try:
            self._client.delete_message(QueueUrl=self._queue_url, ReceiptHandle=receipt_handle)
        except (
            botocore.exceptions.EndpointConnectionError,
            botocore.exceptions.ConnectionError,
            botocore.exceptions.ClientError,
        ) as exc:
            raise QueueTransientError(f"failed to delete message: {exc}", cause=exc) from exc
