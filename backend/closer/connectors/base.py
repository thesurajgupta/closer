"""Connector interfaces.

The agent never talks to the outside world directly. It proposes an action; the
executor calls a connector. Swapping demo connectors for real ones (Gmail,
Google Calendar, a merchant API) requires no change to any agent or tool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ConnectorResult:
    ok: bool
    external_ref: str = ""
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    simulated: bool = True


class ConnectorError(RuntimeError):
    """Raised for a *retryable* connector failure."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmailConnector(ABC):
    name = "email"

    @abstractmethod
    def list_messages(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def send(self, to: str, subject: str, body: str, thread_id: str | None, idem: str) -> ConnectorResult: ...

    @abstractmethod
    def thread_state(self, external_ref: str) -> dict[str, Any]: ...


class CalendarConnector(ABC):
    name = "calendar"

    @abstractmethod
    def list_events(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def find_free_slots(self, duration_minutes: int, within_days: int, not_before: datetime | None = None,
                        one_per_day: bool = False) -> list[datetime]: ...

    @abstractmethod
    def reschedule(self, event_id: str, new_start: datetime, idem: str) -> ConnectorResult: ...

    @abstractmethod
    def confirm(self, event_id: str, idem: str) -> ConnectorResult: ...


class DocumentConnector(ABC):
    name = "documents"

    @abstractmethod
    def search(self, query: str, limit: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get(self, doc_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def upload(self, portal: str, doc_id: str, reference: str, idem: str) -> ConnectorResult: ...


class BillingConnector(ABC):
    name = "billing"

    @abstractmethod
    def statements(self, provider: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def dispute(self, provider: str, invoice_ref: str, amount: float, reason: str, idem: str) -> ConnectorResult: ...

    @abstractmethod
    def dispute_state(self, external_ref: str) -> dict[str, Any]: ...


class WarrantyConnector(ABC):
    name = "warranty"

    @abstractmethod
    def registry(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def submit_claim(self, merchant: str, payload: dict[str, Any], idem: str) -> ConnectorResult: ...

    @abstractmethod
    def claim_state(self, external_ref: str) -> dict[str, Any]: ...


class DeliveryConnector(ABC):
    name = "delivery"

    @abstractmethod
    def book_redelivery(self, tracking: str, slot: datetime, idem: str) -> ConnectorResult: ...

    @abstractmethod
    def delivery_state(self, tracking: str) -> dict[str, Any]: ...
