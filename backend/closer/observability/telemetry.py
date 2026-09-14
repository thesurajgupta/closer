"""Local observability: structured event log + tool metrics.

In an AgentCore/CloudWatch deployment these same records are emitted as EMF
metrics and structured logs (see deploy/aws/observability.md). Locally they back
the "Evidence for judges" panel.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from .. import clock

logger = logging.getLogger("closer")


def configure_logging(level: int = logging.INFO) -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s closer %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


@dataclass
class Span:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    status: str = "success"
    error: str | None = None


class Telemetry:
    """Collects spans for the current run and streams activity to subscribers."""

    def __init__(self) -> None:
        self.spans: list[Span] = []
        self._subscribers: list[Any] = []

    def subscribe(self, queue: Any) -> None:
        self._subscribers.append(queue)

    def unsubscribe(self, queue: Any) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        record = {"kind": kind, "at": clock.now().isoformat(), **payload}
        logger.info("%s %s", kind, json.dumps(payload, default=str)[:400])
        for q in list(self._subscribers):
            try:
                q.put_nowait(record)
            except Exception:  # a slow consumer must never break agent execution
                pass

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        span = Span(name=name, attributes=attributes)
        start = time.perf_counter()
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.error = str(exc)
            raise
        finally:
            span.latency_ms = int((time.perf_counter() - start) * 1000)
            self.spans.append(span)


TELEMETRY = Telemetry()
