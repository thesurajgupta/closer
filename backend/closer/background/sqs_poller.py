"""SQS poll loop for a deployed CLOSER worker.

Kept deliberately small and separate from the agent: long-polls the queue,
hands each message to the same `handle_event` the local scheduler uses, and
deletes it only after the handler returns. A crash before the delete means the
message reappears — which is exactly why every side effect is claimed under an
idempotency key first.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..observability.telemetry import configure_logging, logger
from .worker import handle_event


def poll_forever(queue_url: str | None = None, wait_seconds: int = 20) -> None:  # pragma: no cover
    import boto3  # imported lazily so the demo needs no AWS dependency

    configure_logging()
    queue_url = queue_url or os.environ["CLOSER_QUEUE_URL"]
    sqs = boto3.client("sqs")
    logger.info("polling %s", queue_url)
    while True:
        response = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=5, WaitTimeSeconds=wait_seconds,
            MessageAttributeNames=["All"],
        )
        for message in response.get("Messages", []):
            body: dict[str, Any] = json.loads(message["Body"])
            try:
                result = handle_event(body.get("detail", body))
                logger.info("handled %s -> %s", body.get("detail-type"), result)
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            except Exception:
                # Left on the queue; SQS redrive moves it to the DLQ after the
                # configured number of receives.
                logger.exception("failed to handle message")
