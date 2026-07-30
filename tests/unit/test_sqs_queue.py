"""adapters/queue/sqs_queue.py — the real SQS adapter, tested against a mocked boto3 client
(unittest.mock, not moto — avoids a new test dependency for what's a thin translation layer).
Zero real AWS calls, zero network."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from adapters.queue.sqs_queue import SQSJobQueue
from core.errors import QueueTransientError
from core.ports import JobQueuePort

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/test-queue"


def _queue_with_mock_client() -> tuple[SQSJobQueue, MagicMock]:
    with patch("adapters.queue.sqs_queue.boto3.client") as mock_boto_client:
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        queue = SQSJobQueue(queue_url=QUEUE_URL, region="us-east-1")
    return queue, mock_client


def test_satisfies_the_port() -> None:
    queue, _ = _queue_with_mock_client()
    assert isinstance(queue, JobQueuePort)


def test_send_calls_send_message_with_job_id_as_body() -> None:
    queue, mock_client = _queue_with_mock_client()
    queue.send("job-123")
    mock_client.send_message.assert_called_once_with(QueueUrl=QUEUE_URL, MessageBody="job-123")


def test_receive_returns_none_when_no_messages() -> None:
    queue, mock_client = _queue_with_mock_client()
    mock_client.receive_message.return_value = {}
    assert queue.receive() is None


def test_receive_maps_sqs_message_to_queue_message() -> None:
    queue, mock_client = _queue_with_mock_client()
    mock_client.receive_message.return_value = {
        "Messages": [{"Body": "job-123", "ReceiptHandle": "receipt-abc"}]
    }
    message = queue.receive()
    assert message is not None
    assert message.job_id == "job-123"
    assert message.receipt_handle == "receipt-abc"


def test_delete_calls_delete_message_with_receipt_handle() -> None:
    queue, mock_client = _queue_with_mock_client()
    queue.delete("receipt-abc")
    mock_client.delete_message.assert_called_once_with(
        QueueUrl=QUEUE_URL, ReceiptHandle="receipt-abc"
    )


def test_send_translates_botocore_client_error_to_queue_transient_error() -> None:
    queue, mock_client = _queue_with_mock_client()
    mock_client.send_message.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "InternalError", "Message": "boom"}}, "SendMessage"
    )
    with pytest.raises(QueueTransientError):
        queue.send("job-123")


def test_receive_translates_botocore_client_error_to_queue_transient_error() -> None:
    queue, mock_client = _queue_with_mock_client()
    mock_client.receive_message.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "InternalError", "Message": "boom"}}, "ReceiveMessage"
    )
    with pytest.raises(QueueTransientError):
        queue.receive()
