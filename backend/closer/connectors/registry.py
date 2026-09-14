"""Connector registry — the single place demo and live wiring diverge."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import get_settings
from .base import (
    BillingConnector,
    CalendarConnector,
    DeliveryConnector,
    DocumentConnector,
    EmailConnector,
    WarrantyConnector,
)
from .demo import (
    DemoBillingConnector,
    DemoCalendarConnector,
    DemoDeliveryConnector,
    DemoDocumentConnector,
    DemoEmailConnector,
    DemoWarrantyConnector,
)


@dataclass
class Connectors:
    email: EmailConnector
    calendar: CalendarConnector
    documents: DocumentConnector
    billing: BillingConnector
    warranty: WarrantyConnector
    delivery: DeliveryConnector
    simulated: bool = True


_registry: Connectors | None = None


def get_connectors() -> Connectors:
    global _registry
    if _registry is None:
        settings = get_settings()
        if not settings.is_demo:  # pragma: no cover - live wiring is credential-gated
            raise RuntimeError(
                "CLOSER_MODE=live requires live connector implementations. "
                "Implement the interfaces in closer/connectors/base.py and register them here; "
                "the agents and tools do not change."
            )
        _registry = Connectors(
            email=DemoEmailConnector(),
            calendar=DemoCalendarConnector(),
            documents=DemoDocumentConnector(),
            billing=DemoBillingConnector(),
            warranty=DemoWarrantyConnector(),
            delivery=DemoDeliveryConnector(),
            simulated=True,
        )
    return _registry


def reset_connectors() -> None:
    global _registry
    _registry = None
