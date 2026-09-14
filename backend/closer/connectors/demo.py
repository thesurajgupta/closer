"""Demo connectors.

These operate on the local synthetic world and are honest about it: every result
is flagged `simulated=True`, and the UI labels any action taken through them as
happening inside the demo environment. CLOSER never claims a synthetic action
happened in the real world.

They also model real-world unpleasantness: a connector that fails the first time
(so retry/backoff is exercised), and external state that only becomes
"confirmed" after a provider-side delay (so verification is real).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from .. import clock
from ..ids import stable_id
from ..store import db
from ..store.repository import Repo
from .base import (
    BillingConnector,
    CalendarConnector,
    ConnectorError,
    ConnectorResult,
    DeliveryConnector,
    DocumentConnector,
    EmailConnector,
    WarrantyConnector,
)

FAIL_ONCE = os.getenv("CLOSER_DEMO_FAIL_ONCE", "").split(",")


def _external(key: str) -> dict[str, Any]:
    return db.get("external_state", key) or {}


def _set_external(key: str, data: dict[str, Any]) -> None:
    db.put("external_state", key, data)


def _maybe_fail(op: str) -> None:
    """Deterministic first-attempt failure for the ops named in
    CLOSER_DEMO_FAIL_ONCE, so retry behaviour is demonstrable on demand."""
    if op not in FAIL_ONCE:
        return
    key = f"failonce:{op}"
    if not _external(key):
        _set_external(key, {"failed_at": clock.now().isoformat()})
        raise ConnectorError(f"{op}: provider endpoint temporarily unavailable (503)", retryable=True)


class DemoEmailConnector(EmailConnector):
    def list_messages(self) -> list[dict[str, Any]]:
        return [m.model_dump(mode="json") for m in Repo.messages()]

    def send(self, to: str, subject: str, body: str, thread_id: str | None, idem: str) -> ConnectorResult:
        _maybe_fail("email.send")
        ref = stable_id("out", idem)
        db.put("outbox", ref, {
            "id": ref, "to": to, "subject": subject, "body": body,
            "thread_id": thread_id, "sent_at": clock.now().isoformat(), "simulated": True,
        })
        _set_external(f"thread:{ref}", {"status": "delivered", "replies": [], "sent_at": clock.now().isoformat()})
        return ConnectorResult(ok=True, external_ref=ref, detail=f"Queued to {to} in the demo mail environment.")

    def thread_state(self, external_ref: str) -> dict[str, Any]:
        return _external(f"thread:{external_ref}") or {"status": "unknown"}


class DemoCalendarConnector(CalendarConnector):
    def list_events(self) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in Repo.calendar()]

    def find_free_slots(self, duration_minutes: int, within_days: int, not_before: datetime | None = None,
                        one_per_day: bool = False) -> list[datetime]:
        busy = [(e.starts_at, e.ends_at) for e in Repo.calendar()]
        floor = not_before or clock.now()
        slots: list[datetime] = []
        day = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
        # Rotate the preferred hour by day so alternatives read like real
        # options rather than three slots on one afternoon.
        hours = [(11, 0), (14, 0), (16, 30)]
        for offset in range(1, within_days + 1):
            d = day + timedelta(days=offset)
            if d.weekday() >= 5:  # keep weekday appointments only
                continue
            found_today = False
            rota = d.weekday() % len(hours)
            ordered = hours[rota:] + hours[:rota]
            for hour, minute in ordered:
                start = d.replace(hour=hour, minute=minute)
                end = start + timedelta(minutes=duration_minutes)
                if start <= floor:
                    continue
                if any(s < end and start < e for s, e in busy):
                    continue
                slots.append(start)
                found_today = True
                if one_per_day:
                    break
            if one_per_day and found_today:
                continue
        return slots

    def reschedule(self, event_id: str, new_start: datetime, idem: str) -> ConnectorResult:
        _maybe_fail("calendar.reschedule")
        event = Repo.calendar_event(event_id)
        if not event:
            raise ConnectorError(f"calendar event {event_id} not found", retryable=False)
        duration = event.ends_at - event.starts_at
        event.starts_at = new_start
        event.ends_at = new_start + duration
        event.confirmed = True
        Repo.put_calendar(event)
        _set_external(f"cal:{event_id}", {"status": "rescheduled", "starts_at": new_start.isoformat()})
        return ConnectorResult(ok=True, external_ref=event_id,
                               detail=f"Moved to {new_start:%a %d %b %H:%M} in the demo calendar.",
                               data={"starts_at": new_start.isoformat()})

    def confirm(self, event_id: str, idem: str) -> ConnectorResult:
        event = Repo.calendar_event(event_id)
        if not event:
            raise ConnectorError(f"calendar event {event_id} not found", retryable=False)
        event.confirmed = True
        Repo.put_calendar(event)
        _set_external(f"cal:{event_id}", {"status": "confirmed", "starts_at": event.starts_at.isoformat()})
        return ConnectorResult(ok=True, external_ref=event_id, detail="Confirmed in the demo calendar.")


class DemoDocumentConnector(DocumentConnector):
    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        terms = [t for t in query.lower().replace(",", " ").split() if len(t) > 2]
        scored = []
        for doc in Repo.documents():
            haystack = " ".join([doc.title, doc.doc_type, doc.text, " ".join(doc.tags),
                                 " ".join(doc.entities), " ".join(f"{k} {v}" for k, v in doc.fields.items())]).lower()
            score = sum(haystack.count(t) for t in terms)
            if score:
                scored.append((score, doc))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [d.model_dump(mode="json") | {"_score": s} for s, d in scored[:limit]]

    def get(self, doc_id: str) -> dict[str, Any] | None:
        doc = Repo.document(doc_id)
        return doc.model_dump(mode="json") if doc else None

    def upload(self, portal: str, doc_id: str, reference: str, idem: str) -> ConnectorResult:
        _maybe_fail("documents.upload")
        doc = Repo.document(doc_id)
        if not doc:
            raise ConnectorError(f"document {doc_id} not found", retryable=False)
        ref = stable_id("upl", idem)
        _set_external(f"upload:{ref}", {
            "status": "received", "portal": portal, "document": doc_id,
            "reference": reference, "at": clock.now().isoformat(),
        })
        return ConnectorResult(ok=True, external_ref=ref,
                               detail=f"Uploaded '{doc.title}' to {portal} (demo portal), ref {reference}.")


class DemoBillingConnector(BillingConnector):
    def statements(self, provider: str | None = None) -> list[dict[str, Any]]:
        return [b.model_dump(mode="json") for b in Repo.billing()
                if provider is None or b.provider.lower() == provider.lower()]

    def dispute(self, provider: str, invoice_ref: str, amount: float, reason: str, idem: str) -> ConnectorResult:
        _maybe_fail("billing.dispute")
        ref = stable_id("disp", idem)
        _set_external(f"dispute:{ref}", {
            "status": "acknowledged", "provider": provider, "invoice": invoice_ref,
            "amount": amount, "reason": reason, "filed_at": clock.now().isoformat(),
            "expected_settlement_days": 7, "withdrawable": True,
        })
        return ConnectorResult(ok=True, external_ref=ref,
                               detail=f"{provider} self-service portal acknowledged reversal request "
                                      f"{ref} for INR {amount:,.2f} on invoice {invoice_ref}.",
                               data={"expected_settlement_days": 7})

    def dispute_state(self, external_ref: str) -> dict[str, Any]:
        return _external(f"dispute:{external_ref}") or {"status": "unknown"}


class DemoWarrantyConnector(WarrantyConnector):
    def registry(self) -> list[dict[str, Any]]:
        return [w.model_dump(mode="json") for w in Repo.warranties()]

    def submit_claim(self, merchant: str, payload: dict[str, Any], idem: str) -> ConnectorResult:
        _maybe_fail("warranty.submit_claim")
        ref = stable_id("clm", idem)
        _set_external(f"claim:{ref}", {
            "status": "received", "merchant": merchant, "payload": payload,
            "filed_at": clock.now().isoformat(), "sla_days": 3,
        })
        return ConnectorResult(ok=True, external_ref=ref,
                               detail=f"{merchant} service desk (demo) issued claim reference {ref}.",
                               data={"sla_days": 3})

    def claim_state(self, external_ref: str) -> dict[str, Any]:
        return _external(f"claim:{external_ref}") or {"status": "unknown"}


class DemoDeliveryConnector(DeliveryConnector):
    def book_redelivery(self, tracking: str, slot: datetime, idem: str) -> ConnectorResult:
        _maybe_fail("delivery.book_redelivery")
        ref = stable_id("dlv", idem)
        _set_external(f"delivery:{tracking}", {
            "status": "redelivery_booked", "slot": slot.isoformat(), "ref": ref,
        })
        return ConnectorResult(ok=True, external_ref=ref,
                               detail=f"Redelivery booked for {slot:%a %d %b %H:%M} (demo courier).",
                               data={"slot": slot.isoformat()})

    def delivery_state(self, tracking: str) -> dict[str, Any]:
        return _external(f"delivery:{tracking}") or {"status": "unknown"}
