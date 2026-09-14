"""Background execution.

CLOSER's whole premise is that it works while nobody is looking, so the run
engine is driven by schedules rather than by a person pressing a button. Locally
that is this scheduler; deployed, the identical triggers come from Amazon
EventBridge and land on an SQS queue that the agent runtime drains
(see deploy/aws/). The handler in `closer.background.worker` is shared by both,
so nothing about the agent changes between them.

Schedules:
  discovery       daily      — look for new open loops
  follow-up       6-hourly   — act on loops whose next check is due
  deadline sweep  hourly     — raise priority as deadlines approach, expire the past
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from .. import clock
from ..config import get_settings
from ..observability.telemetry import TELEMETRY, configure_logging, logger
from .worker import handle_event


@dataclass
class Schedule:
    name: str
    every_minutes: int
    event: dict
    last_run: float = 0.0

    def due(self, now: float) -> bool:
        return now - self.last_run >= self.every_minutes * 60


class Scheduler:
    """A minimal EventBridge stand-in: fixed-rate rules that emit events onto the
    same queue the deployed system uses."""

    def __init__(self, queue: "EventQueue | None" = None) -> None:
        settings = get_settings()
        self.queue = queue or EventQueue()
        self.schedules = [
            Schedule("closer.discovery", settings.discovery_cron_minutes,
                     {"detail-type": "closer.discovery"}),
            Schedule("closer.follow_up", settings.follow_up_cron_minutes,
                     {"detail-type": "closer.follow_up"}),
            Schedule("closer.deadline_sweep", 60, {"detail-type": "closer.deadline_sweep"}),
        ]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> int:
        now = time.time()
        fired = 0
        for schedule in self.schedules:
            if schedule.due(now):
                schedule.last_run = now
                self.queue.send(schedule.event)
                TELEMETRY.emit("schedule_fired", {"rule": schedule.name})
                fired += 1
        return fired

    def start(self, interval_seconds: int = 30) -> None:
        configure_logging()

        def loop() -> None:
            logger.info("scheduler started with %d rules", len(self.schedules))
            while not self._stop.wait(interval_seconds):
                try:
                    self.tick()
                    self.queue.drain()
                except Exception:  # a bad tick must not kill the scheduler
                    logger.exception("scheduler tick failed")

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


class EventQueue:
    """An in-process stand-in for SQS with the properties that matter: at-least-
    once delivery, a visibility window, a retry count and a dead-letter queue."""

    def __init__(self, max_receives: int = 3) -> None:
        self._messages: list[dict] = []
        self.dead_letter: list[dict] = []
        self.max_receives = max_receives
        self._lock = threading.Lock()

    def send(self, body: dict) -> None:
        with self._lock:
            self._messages.append({"body": body, "receives": 0})

    def receive(self) -> dict | None:
        with self._lock:
            if not self._messages:
                return None
            message = self._messages.pop(0)
            message["receives"] += 1
            return message

    def drain(self, handler: Callable[[dict], None] | None = None) -> int:
        """Process everything currently queued. A handler that raises gets the
        message back until it exhausts its receive count, then it is parked in
        the dead-letter queue rather than lost or retried forever."""
        handler = handler or handle_event
        processed = 0
        while True:
            message = self.receive()
            if message is None:
                return processed
            try:
                handler(message["body"])
                processed += 1
            except Exception as exc:
                logger.warning("handler failed for %s: %s", message["body"], exc)
                if message["receives"] >= self.max_receives:
                    self.dead_letter.append({**message, "error": str(exc)})
                    TELEMETRY.emit("dead_letter", {"body": message["body"], "error": str(exc)})
                else:
                    with self._lock:
                        self._messages.append(message)
                    return processed


def next_due_in(minutes: int) -> str:
    return (clock.now() + timedelta(minutes=minutes)).isoformat()
