"""Lambda entrypoint for the SQS-driven worker.

Thin on purpose: unwrap the SQS envelope, call the shared handler, and report
per-message failures so SQS redrives only what actually failed.
"""

from __future__ import annotations

import json
from typing import Any

from ..observability.telemetry import configure_logging, logger
from .worker import handle_event


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:  # pragma: no cover
    configure_logging()
    failures = []
    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            result = handle_event(body.get("detail", body))
            logger.info("handled %s -> %s", body.get("detail-type"), result)
        except Exception:
            logger.exception("message failed; leaving it for redrive")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
