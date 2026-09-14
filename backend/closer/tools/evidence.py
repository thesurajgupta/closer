"""Evidence tools.

Every fact CLOSER states in the interface comes from one of these tools and
carries the document, message or record it came from. Nothing is asserted
without a source, and a source that has been superseded is marked stale rather
than quietly used.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from strands import tool

from .. import clock
from ..agents import context as run_context
from ..connectors.registry import get_connectors
from ..ids import stable_id
from ..models.domain import AuditEvent, EvidenceBundle, EvidenceItem
from ..models.enums import LoopCategory
from ..policy import sanitize
from ..store.repository import Repo
from ._base import err, instrumented, ok
from .loops import REQUIRED_INFORMATION


# Fields worth keeping even when they answer no explicit requirement — they are
# what a person would look for on a receipt.
ALWAYS_KEEP = {"merchant", "provider", "amount", "currency", "product", "reference",
               "invoice_number", "order_number", "policy_number", "tracking"}


def _bucket(loop_id: str) -> list[EvidenceItem]:
    ctx = run_context.current()
    return ctx.scratch.setdefault("evidence", {}).setdefault(loop_id, [])


def _add(loop_id: str, item: EvidenceItem) -> None:
    bucket = _bucket(loop_id)
    if not any(e.id == item.id for e in bucket):
        bucket.append(item)


def _ev_id(loop_id: str, label: str) -> str:
    """Normalised so that the same fact recorded by two tools — "Invoice number"
    and "invoice_number" — collapses into one evidence item."""
    return stable_id("ev", loop_id, re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"))


def _satisfy(loop_id: str, *keys: str) -> None:
    """Mark required information as established. Kept separate from evidence
    items because some requirements are satisfied by a derivation rather than by
    a single quotable field."""
    ctx = run_context.current()
    ctx.scratch.setdefault("satisfied", {}).setdefault(loop_id, set()).update(k for k in keys if k)


def _satisfied(loop_id: str) -> set[str]:
    ctx = run_context.current()
    return set(ctx.scratch.get("satisfied", {}).get(loop_id, set()))


def _stem(token: str) -> str:
    for suffix in ("ies", "es", "ed", "ing", "s", "y"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(name: str) -> set[str]:
    return {_stem(t) for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) > 3}


def _matches(required: str, known: set[str]) -> bool:
    """A requirement is met if something carrying the same idea is known.
    `expiry` is met by `expires`; `options` by `option_a`; `current_policy` by
    `policy_number`."""
    req = required.lower()
    req_tokens = _tokens(req)
    for candidate in known:
        cand = candidate.lower()
        if req == cand or req in cand or cand in req:
            return True
        if req_tokens & _tokens(cand):
            return True
    return False


@tool
@instrumented("Searching your documents")
def search_documents(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the user's documents. Returns matches with their type, date and
    whether a newer version has superseded them.

    Args:
        query: Free-text search terms.
        limit: Maximum number of documents to return (1-10).
    """
    limit = max(1, min(int(limit), 10))
    hits = get_connectors().documents.search(query, limit)
    # A document that mentions a different company is not evidence about this
    # one, however many generic words it shares.
    ctx = run_context.current()
    loop = Repo.loop(ctx.loop_id) if ctx.loop_id else None
    party = (loop.external_party or "") if loop else ""
    party_tokens = {t for t in re.split(r"[^a-z0-9]+", party.lower()) if len(t) > 3
                    and t not in {"test", "com", "mail"}}
    if party_tokens:
        on_topic = [h for h in hits
                    if party_tokens <= {t for t in re.split(r"[^a-z0-9]+",
                                                            (h["title"] + " " + " ".join(h.get("entities", []))
                                                             + " " + h["text"][:600]).lower()) if len(t) > 3}]
        if on_topic:
            hits = on_topic
    return ok(f"{len(hits)} document(s) matched '{query}'.", {
        "candidate_document_ids": [h["id"] for h in hits],
    }, results=[{
        "id": h["id"], "title": h["title"], "type": h["doc_type"],
        "issued_at": h.get("issued_at"), "stale": bool(h.get("superseded_by")),
    } for h in hits])


@tool
@instrumented()
def get_document(document_id: str) -> dict[str, Any]:
    """Read one document in full.

    Args:
        document_id: The document identifier.
    """
    raw = get_connectors().documents.get(document_id)
    if not raw:
        return err(f"No document with id '{document_id}'.")
    return ok(raw["title"], {"document_id": document_id, "document_stale": bool(raw.get("superseded_by"))},
              content=sanitize.envelope(raw["title"], raw["text"], document_id),
              fields=raw.get("fields", {}))


@tool
@instrumented("Reading documents")
def extract_document_fields(document_ids: list[str], fields: list[str]) -> dict[str, Any]:
    """Pull specific named fields out of a set of documents, keeping the source
    of every value.

    Args:
        document_ids: Documents to read.
        fields: Field names to look for, e.g. ["serial_number", "purchase_date"].
    """
    ctx = run_context.current()
    loop_id = ctx.loop_id or ""
    found: dict[str, dict[str, str]] = {}
    stale_used: list[str] = []
    docs = [d for d in (get_connectors().documents.get(i) for i in document_ids) if d]
    # A superseded document is not a source of truth. It is recorded as having
    # been seen and skipped, so the user can tell CLOSER did not use it.
    for raw in docs:
        if raw.get("superseded_by"):
            stale_used.append(raw["id"])
    for raw in [d for d in docs if not d.get("superseded_by")]:
        doc_id = raw["id"]
        for name, value in raw.get("fields", {}).items():
            if name in found:
                continue
            # Keep what answers a requirement; leave the rest out so the
            # evidence panel stays readable.
            if fields and name not in ALWAYS_KEEP and not any(_matches(f, {name}) for f in fields):
                continue
            found[name] = {"value": value, "document_id": doc_id, "title": raw["title"],
                           "stale": bool(raw.get("superseded_by"))}
    for name, meta in found.items():
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, name), key=name,
            label=name.replace("_", " ").capitalize(), value=meta["value"],
            source_type="document", source_id=meta["document_id"], source_title=meta["title"],
            locator=f"field:{name}", stale=meta["stale"],
        ))
    _satisfy(loop_id, *found.keys())
    missing = [f for f in fields if not _matches(f, set(found))]
    return ok(f"Read {len(document_ids)} document(s); found {len(found)} of {len(fields)} fields.", {
        "extracted_fields": {k: v["value"] for k, v in found.items()},
        "missing": missing,
        "stale_sources": stale_used,
        "evidence_count": len(_bucket(loop_id)),
    })


@tool
@instrumented("Checking warranty cover")
def check_warranty_status(loop_id: str) -> dict[str, Any]:
    """Work out whether a warranty is still active for the product a loop is
    about, using the purchase record and the warranty terms.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    source_text = ""
    msg = Repo.message(loop.source_ref)
    if msg:
        source_text = f"{msg.subject} {msg.body}"
    registry = get_connectors().warranty.registry()

    best, score = None, 0
    for rec in registry:
        tokens = [t.lower() for t in re.split(r"[^a-z0-9]+", rec["product"], flags=re.I) if len(t) > 3]
        hits = sum(1 for t in tokens if t in (source_text + " " + loop.description).lower())
        if hits > score:
            best, score = rec, hits
    if not best:
        return ok("No product in the warranty register matches this loop.", {
            "warranty_found": False, "warranty_active": False,
            "missing": ["product", "serial_number", "purchase_date"],
        })

    purchased = datetime.fromisoformat(best["purchased_at"])
    expires = purchased + timedelta(days=30.44 * best["months"])
    active = expires > clock.now()
    days_left = (expires - clock.now()).days

    receipt = Repo.document(best["receipt_document_id"]) if best["receipt_document_id"] else None
    warranty_doc = Repo.document(best["warranty_document_id"]) if best["warranty_document_id"] else None
    invoice_number = receipt.fields.get("invoice_number", "") if receipt else ""
    claim_channel = warranty_doc.fields.get("claim_channel", "") if warranty_doc else ""

    for key, label, value, src in (
        ("product", "Product", best["product"], best["receipt_document_id"]),
        ("purchase_date", "Purchase date", purchased.date().isoformat(), best["receipt_document_id"]),
        ("invoice_number", "Invoice number", invoice_number, best["receipt_document_id"]),
        ("warranty_months", "Warranty period", f"{best['months']} months", best["warranty_document_id"]),
        ("warranty_status", "Warranty status",
         "Active" if active else f"Expired {expires.date().isoformat()}", best["warranty_document_id"]),
        ("serial_number", "Serial number", best["serial"] or "not recorded", best["receipt_document_id"]),
        ("claim_channel", "Where claims go", claim_channel, best["warranty_document_id"]),
    ):
        doc = Repo.document(src) if src else None
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, label), key=key, label=label, value=value or "not recorded",
            source_type="document" if doc else "derived",
            source_id=src or "warranty-register", source_title=doc.title if doc else "Warranty register",
            locator="fields", confidence=1.0 if value else 0.0,
        ))
    if source_text.strip():
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, "fault"), key="fault_description", label="Reported fault",
            value=loop.description, source_type="message", source_id=loop.source_ref,
            source_title=msg.subject if msg else "Your note", locator="body",
        ))
    _satisfy(loop_id, "product", "purchase_date", "warranty_months", "warranty_status",
             "fault_description", *( ["serial_number"] if best["serial"] else []),
             *( ["invoice_number"] if invoice_number else []),
             *( ["claim_channel"] if claim_channel else []))

    # Now that the product is known, give the loop a title a person would write.
    if loop.title.startswith("Warranty claim may be"):
        product_name = best["product"].split(" Front")[0]
        loop.title = (f"Warranty claim for the {product_name}" if active
                      else f"{product_name} — warranty check")
        loop.value_at_stake = best["coverage_value"] if active else 0.0
    if not loop.external_party:
        loop.external_party = best["merchant"]
    Repo.put_loop(loop)

    missing = [] if best["serial"] else ["serial_number"]
    return ok(
        f"{best['product']}: warranty {'active' if active else 'expired'} "
        f"({'expires ' if active else 'expired '}{expires.date().isoformat()}).",
        {
            "warranty_found": True, "warranty_active": active, "warranty_expires": expires.isoformat(),
            "warranty_days_left": days_left, "product": best["product"], "serial_number": best["serial"],
            "merchant": best["merchant"], "coverage_value": best["coverage_value"],
            "claim_channel": (Repo.document(best["warranty_document_id"]).fields.get("claim_channel", "")
                              if best["warranty_document_id"] else ""),
            "invoice_number": (Repo.document(best["receipt_document_id"]).fields.get("invoice_number", "")
                               if best["receipt_document_id"] else ""),
            "purchase_date": purchased.date().isoformat(),
            "missing": missing,
            "evidence_count": len(_bucket(loop_id)),
        })


@tool
@instrumented("Comparing your invoices")
def compare_billing_periods(loop_id: str) -> dict[str, Any]:
    """Compare the charge that triggered a loop against the previous period and
    the expected plan price, and characterise the difference.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    provider = loop.external_party or ""
    records = [b for b in Repo.billing()
               if provider.lower() in b.provider.lower() or b.provider.lower() in loop.title.lower()]
    if len(records) < 2:
        records = Repo.billing()
        records = [b for b in records if b.provider.lower() in loop.title.lower()]
    if not records:
        return ok("No billing history found for this provider.", {
            "billing_anomaly": False, "missing": ["previous_amount", "charged_amount"],
        })
    records.sort(key=lambda b: b.charged_at)
    current, previous = records[-1], records[-2] if len(records) > 1 else records[-1]
    delta = current.amount - previous.amount
    expected = current.expected_amount if current.expected_amount is not None else previous.amount
    duplicate = abs(current.amount - 2 * expected) < 1.0

    invoice_number = (Repo.document(current.document_id).fields.get("invoice_number", "")
                      if current.document_id else "")
    for key, label, value, src in (
        ("previous_amount", "Previous charge", f"INR {previous.amount:,.2f} ({previous.period})",
         previous.document_id),
        ("charged_amount", "Current charge", f"INR {current.amount:,.2f} ({current.period})",
         current.document_id),
        ("expected_amount", "Expected charge", f"INR {expected:,.2f}", current.document_id),
        ("invoice_number", "Invoice number", invoice_number, current.document_id),
        ("provider", "Provider", current.provider, current.document_id),
    ):
        doc = Repo.document(src) if src else None
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, label), key=key, label=label, value=value or "not recorded",
            source_type="billing" if not doc else "document",
            source_id=src or current.id, source_title=doc.title if doc else f"{current.provider} billing record",
            locator="line items",
        ))
    _satisfy(loop_id, "previous_amount", "charged_amount", "expected_amount", "provider",
             *( ["invoice_number"] if invoice_number else []))

    if duplicate and "charged twice" not in loop.title:
        loop.title = f"{current.provider} charged you twice"
        loop.value_at_stake = round(current.amount - expected, 2)
        Repo.put_loop(loop)
    elif not duplicate and abs(current.amount - expected) > 1.0:
        loop.title = f"{current.provider} charge went up"
        Repo.put_loop(loop)

    return ok(
        f"{current.provider}: INR {current.amount:,.2f} this period against INR {expected:,.2f} expected"
        + (" — the plan rental appears twice." if duplicate else "."),
        {
            "billing_anomaly": abs(current.amount - expected) > 1.0,
            "duplicate_charge": duplicate,
            "provider": current.provider, "charged_amount": current.amount,
            "expected_amount": expected, "previous_amount": previous.amount,
            "discrepancy": round(delta, 2), "overcharge": round(current.amount - expected, 2),
            "invoice_number": (Repo.document(current.document_id).fields.get("invoice_number", "")
                               if current.document_id else ""),
            "period": current.period,
            "evidence_count": len(_bucket(loop_id)),
        })


@tool
@instrumented("Checking the provider's policy")
def read_provider_policy(provider: str) -> dict[str, Any]:
    """Read the provider's own terms to find out how this kind of problem is
    supposed to be resolved, and whether a prior notice explains the change.

    Args:
        provider: Provider name.
    """
    ctx = run_context.current()
    loop_id = ctx.loop_id or ""
    hits = get_connectors().documents.search(f"{provider} policy terms notice", 4)
    policy = next((h for h in hits if h["doc_type"] in ("policy", "letter")), None)
    if not policy:
        return ok(f"No published terms found for {provider}.", {"policy_found": False,
                                                                "missing": ["provider_policy"]})
    fields = policy.get("fields", {})
    explains = None
    if "new_amount" in fields:
        explains = (f"A price revision to INR {float(fields['new_amount']):,.2f} was notified on "
                    f"{policy.get('issued_at', '')[:10]}, {fields.get('notice_given_days', '?')} days in advance.")
    _add(loop_id, EvidenceItem(
        id=_ev_id(loop_id, "policy"), key="provider_policy", label="Provider policy", value=policy["title"],
        source_type="document", source_id=policy["id"], source_title=policy["title"], locator="terms",
    ))
    _satisfy(loop_id, "provider_policy")
    if explains:
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, "prior notice"), key="prior_notice", label="Prior notice", value=explains,
            source_type="document", source_id=policy["id"], source_title=policy["title"], locator="body",
        ))
    return ok(policy["title"], {
        "policy_found": True, "policy_document_id": policy["id"],
        "provider_policy": fields.get("duplicate_charge_policy", policy["text"][:300]),
        "self_service_reversal": "self-service portal" in policy["text"].lower(),
        "change_explained_by_notice": bool(explains),
        "explanation": explains or "",
        "evidence_count": len(_bucket(loop_id)),
    })


@tool
@instrumented("Checking your calendar")
def check_calendar_conflicts(loop_id: str) -> dict[str, Any]:
    """Check whether the appointment behind a loop clashes with anything else,
    and how important the clashing item is.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    events = Repo.calendar()
    target = next((e for e in events if e.id == loop.source_ref), None)
    if target is None:
        target = next((e for e in events if not e.confirmed), None)
    if target is None:
        return ok("No appointment found for this loop.", {"conflict_detected": False,
                                                          "missing": ["appointment_time"]})
    clashes = [e for e in events if e.id != target.id and e.starts_at < target.ends_at and target.starts_at < e.ends_at]
    _add(loop_id, EvidenceItem(
        id=_ev_id(loop_id, "appointment"), key="appointment_time", label="Appointment",
        value=f"{target.title}, {target.starts_at:%a %d %b %H:%M}",
        source_type="calendar", source_id=target.id, source_title="Your calendar", locator="event",
    ))
    _satisfy(loop_id, "appointment_time", "conflict")
    if clashes:
        c = clashes[0]
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, "conflict"), key="conflict", label="Clashes with",
            value=f"{c.title}, {c.starts_at:%a %d %b %H:%M} ({c.importance} importance)",
            source_type="calendar", source_id=c.id, source_title="Your calendar", locator="event",
        ))
    return ok(
        f"{target.title} {'clashes with ' + clashes[0].title if clashes else 'has no clash'}.",
        {
            "conflict_detected": bool(clashes), "appointment_event_id": target.id,
            "appointment_time": target.starts_at.isoformat(),
            "appointment_confirmed": target.confirmed,
            "conflict": clashes[0].title if clashes else "",
            "conflict_importance": clashes[0].importance if clashes else "",
            "duration_minutes": int((target.ends_at - target.starts_at).total_seconds() // 60),
            "evidence_count": len(_bucket(loop_id)),
        })


@tool
@instrumented("Finding alternative times")
def find_alternative_slots(loop_id: str, count: int = 2) -> dict[str, Any]:
    """Find free slots that would suit the appointment behind a loop.

    Args:
        loop_id: The loop identifier.
        count: How many alternatives to return (1-4).
    """
    ctx = run_context.current()
    duration = 45
    not_before = None
    # Read from the evidence gathered so far in this run, not from the persisted
    # loop — the conflict check has not been filed yet.
    for item in _bucket(loop_id):
        if item.key == "appointment_time":
            event = Repo.calendar_event(item.source_id)
            if event:
                duration = int((event.ends_at - event.starts_at).total_seconds() // 60)
                not_before = event.starts_at
    slots = get_connectors().calendar.find_free_slots(
        duration, within_days=14, not_before=not_before, one_per_day=True,
    )[: max(1, min(int(count), 4))]
    _satisfy(loop_id, "alternative_slots")
    for slot in slots:
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, f"slot {slot.isoformat()}"), key="alternative_slots", label="Free slot",
            value=f"{slot:%A %d %B, %H:%M}", source_type="calendar", source_id="calendar",
            source_title="Your calendar", locator="availability",
        ))
    return ok(f"Found {len(slots)} free slot(s).", {
        "alternative_slots": [s.isoformat() for s in slots],
        "alternative_slots_readable": [f"{s:%A %d %B, %H:%M}" for s in slots],
        "evidence_count": len(_bucket(loop_id)),
    })


@tool
@instrumented("Finding the requested document")
def resolve_document_request(loop_id: str) -> dict[str, Any]:
    """Work out exactly which document a third party asked for, find the best
    matching document the user actually has, and check it is current enough.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    msg = Repo.message(loop.source_ref)
    body = f"{msg.subject} {msg.body}" if msg else loop.description
    wanted = "proof of address" if "address" in body.lower() else "supporting document"
    max_age_days = 90
    age_match = re.search(r"within the last (\d+) days", body.lower())
    if age_match:
        max_age_days = int(age_match.group(1))

    candidates = get_connectors().documents.search("statement proof address utility", 5)
    current, stale = None, []
    for cand in candidates:
        if cand.get("superseded_by"):
            stale.append(cand)
            continue
        issued = cand.get("issued_at")
        if not issued:
            continue
        age = (clock.now() - datetime.fromisoformat(issued)).days
        if age <= max_age_days and (current is None or age < current[1]):
            current = (cand, age)

    portal_match = re.search(r"(secure document portal|self-service portal|document portal)", body, re.I)
    reference = (msg.external_ref if msg else "") or ""

    _satisfy(loop_id, "requested_document", "acceptable_age", "portal", "reference")
    _add(loop_id, EvidenceItem(
        id=_ev_id(loop_id, "request"), key="requested_document", label="What they asked for",
        value=f"{wanted}, dated within {max_age_days} days",
        source_type="message", source_id=loop.source_ref, source_title=msg.subject if msg else loop.title,
        locator="body",
    ))
    for s in stale:
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, f"stale {s['id']}"), key="superseded_document",
            label="Superseded document", value=s["title"],
            source_type="document", source_id=s["id"], source_title=s["title"], locator="metadata", stale=True,
        ))
    if current:
        doc, age = current
        _satisfy(loop_id, "available_document")
        _add(loop_id, EvidenceItem(
            id=_ev_id(loop_id, "document"), key="available_document", label="Document to send",
            value=f"{doc['title']} ({age} days old)",
            source_type="document", source_id=doc["id"], source_title=doc["title"], locator="metadata",
        ))
        return ok(f"'{doc['title']}' satisfies the request ({age} days old, limit {max_age_days}).", {
            "requested_document": wanted, "acceptable_age": max_age_days,
            "available_document": doc["id"], "available_document_title": doc["title"],
            "document_age_days": age, "stale_alternatives": [s["id"] for s in stale],
            "portal": portal_match.group(0) if portal_match else "provider portal",
            "reference": reference,
            "evidence_count": len(_bucket(loop_id)),
        })
    return ok(f"No document current enough for a {wanted} (needs to be under {max_age_days} days old).", {
        "requested_document": wanted, "acceptable_age": max_age_days,
        "available_document": None, "missing": ["available_document"],
        "stale_alternatives": [s["id"] for s in stale],
        "portal": portal_match.group(0) if portal_match else "provider portal",
        "reference": reference,
        "evidence_count": len(_bucket(loop_id)),
    })


@tool
@instrumented()
def record_evidence(loop_id: str) -> dict[str, Any]:
    """Attach everything gathered so far to the loop, and state plainly what is
    still missing.

    Args:
        loop_id: The loop identifier.
    """
    ctx = run_context.current()
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    items = _bucket(loop_id)
    required = REQUIRED_INFORMATION[loop.category]
    known = {i.key or i.label.lower().replace(" ", "_") for i in items
             if i.value and i.value != "not recorded"}
    known |= _satisfied(loop_id)
    # An item that came back empty is a gap, not a fact, even if a tool claimed it.
    known -= {i.key for i in items if i.key and i.value in ("", "not recorded")}
    missing = [r for r in required if not _matches(r, known)]
    complete = not missing
    loop.evidence = EvidenceBundle(items=items, missing=missing, complete=complete,
                                   notes=f"{len(items)} sourced facts.")
    loop.required_information = required
    coverage = (len(required) - len(missing)) / max(len(required), 1)
    loop.confidence = round(min(0.98, 0.45 + 0.53 * coverage), 2)
    stale_count = sum(1 for i in items if i.stale)
    loop.add_audit(AuditEvent(
        id=stable_id("aud", loop_id, "evidence", str(len(items))), at=clock.now(), actor="closer",
        agent="evidence", run_id=ctx.run.run_id, kind="evidence",
        message=(f"Collected {len(items)} sourced facts"
                 + (f"; still missing {', '.join(missing)}" if missing else "; nothing missing")),
        data={"count": len(items), "missing": missing, "stale": stale_count},
    ))
    Repo.put_loop(loop)
    ctx.activity(f"Collected {len(items)} facts" + (f", {len(missing)} still missing" if missing else ""),
                 loop_id=loop_id)
    return ok(f"{len(items)} sourced facts; {len(missing)} gap(s).", {
        "evidence_count": len(items), "missing": missing, "evidence_complete": complete,
        "stale_evidence": stale_count, "confidence": loop.confidence,
    })


@tool
@instrumented()
def get_loop_evidence(loop_id: str) -> dict[str, Any]:
    """Read back the evidence attached to a loop, so a message can be written
    using only facts that have a source.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    return ok(f"{len(loop.evidence.items)} facts on file.", {
        "evidence_count": len(loop.evidence.items),
        "evidence_complete": loop.evidence.complete,
        "missing": loop.evidence.missing,
    }, evidence=[{"id": i.id, "label": i.label, "value": i.value, "source": i.source_title, "stale": i.stale}
                 for i in loop.evidence.items])


EVIDENCE_TOOLS = [
    search_documents, get_document, extract_document_fields, check_warranty_status,
    compare_billing_periods, read_provider_policy, check_calendar_conflicts,
    find_alternative_slots, resolve_document_request, record_evidence, get_loop_evidence,
]
