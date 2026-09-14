"""Resolution tools: from evidence to exactly one recommended action.

The agent can only choose from the actions this module says are available for
the loop's category and evidence state — which is itself a subset of the global
action allowlist. There is no path from a model's imagination to an executed
side effect.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from strands import tool

from .. import clock
from ..agents import context as run_context
from ..ids import stable_id
from ..models.domain import AuditEvent, ProposedAction
from ..models.enums import ActionType, LoopCategory
from ..policy import engine
from ..store.repository import Repo
from ._base import err, instrumented, ok

# What is even conceivable for each category, before evidence is considered.
CATEGORY_ACTIONS: dict[LoopCategory, list[ActionType]] = {
    LoopCategory.WARRANTY: [ActionType.SUBMIT_WARRANTY_CLAIM, ActionType.PREPARE_DRAFT,
                            ActionType.RECORD_FINDING, ActionType.NO_ACTION],
    LoopCategory.BILLING: [ActionType.SUBMIT_BILLING_DISPUTE, ActionType.PREPARE_DRAFT,
                           ActionType.RECORD_FINDING, ActionType.NO_ACTION],
    LoopCategory.REFUND: [ActionType.SEND_MESSAGE, ActionType.PREPARE_DRAFT, ActionType.SCHEDULE_FOLLOW_UP],
    LoopCategory.APPOINTMENT: [ActionType.RESCHEDULE_APPOINTMENT, ActionType.CONFIRM_APPOINTMENT,
                               ActionType.SEND_MESSAGE, ActionType.NO_ACTION],
    LoopCategory.DOCUMENT_REQUEST: [ActionType.UPLOAD_DOCUMENT, ActionType.PREPARE_DRAFT,
                                    ActionType.RECORD_FINDING],
    LoopCategory.RENEWAL: [ActionType.RECORD_FINDING, ActionType.SCHEDULE_FOLLOW_UP, ActionType.SEND_MESSAGE],
    LoopCategory.DELIVERY: [ActionType.RESCHEDULE_APPOINTMENT, ActionType.SCHEDULE_FOLLOW_UP],
    LoopCategory.OTHER: [ActionType.SCHEDULE_FOLLOW_UP, ActionType.SEND_MESSAGE, ActionType.NO_ACTION],
}


def _fact(loop, *labels: str) -> str:
    for label in labels:
        for item in loop.evidence.items:
            if item.label.lower() == label.lower():
                return item.value
    return ""


@tool
@instrumented()
def list_available_actions(loop_id: str) -> dict[str, Any]:
    """List the actions that are actually available for this loop right now,
    given its category, its evidence and the user's autonomy settings.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    candidates = CATEGORY_ACTIONS[loop.category]
    settings = Repo.settings()
    available, blocked = [], []
    for action_type in candidates:
        risk = engine.risk_for(action_type)
        if action_type in engine.DENY_IN_DEMO:
            blocked.append({"action": action_type.value, "why": "CLOSER never moves money."})
            continue
        pref = engine._pref_for(risk, action_type, settings)
        if pref == "never":
            blocked.append({"action": action_type.value, "why": "Your settings say never."})
            continue
        available.append({"action": action_type.value, "risk": risk.value,
                          "needs_approval": pref == "ask"})
    loop.available_actions = [ActionType(a["action"]) for a in available]
    Repo.put_loop(loop)
    return ok(f"{len(available)} action(s) available.", {
        "available_actions": [a["action"] for a in available],
    }, options=available, blocked=blocked)


@tool
@instrumented()
def recommend_action(loop_id: str) -> dict[str, Any]:
    """Choose the single action that best closes this loop, using only the
    evidence on file. Returns the recommendation with its reasoning and the
    evidence it rests on.

    Args:
        loop_id: The loop identifier.
    """
    ctx = run_context.current()
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")

    builder = {
        LoopCategory.WARRANTY: _warranty,
        LoopCategory.BILLING: _billing,
        LoopCategory.APPOINTMENT: _appointment,
        LoopCategory.DOCUMENT_REQUEST: _document_request,
        LoopCategory.RENEWAL: _renewal,
        LoopCategory.DELIVERY: _delivery,
        LoopCategory.OTHER: _followup,
        LoopCategory.REFUND: _followup,
    }[loop.category]
    action, extra = builder(loop)

    if action.action_type not in CATEGORY_ACTIONS[loop.category]:
        return err(f"{action.action_type.value} is not available for a {loop.category.value} loop.")

    loop.recommended_action = action
    loop.value_at_stake = action.amount or loop.value_at_stake
    loop.estimated_minutes_saved = extra.get("minutes_saved", 15)
    loop.add_audit(AuditEvent(
        id=stable_id("aud", loop_id, "recommend", action.action_type.value), at=clock.now(),
        actor="closer", agent="resolution", run_id=ctx.run.run_id, kind="decided",
        message=action.summary, data={"rationale": action.rationale,
                                      "action_type": action.action_type.value},
    ))
    Repo.put_loop(loop)
    ctx.activity(f"Decided: {action.summary}", loop_id=loop_id)
    facts = {
        "recommended_action_type": action.action_type.value,
        "recommendation_summary": action.summary,
        "needs_message": action.action_type in (ActionType.SEND_MESSAGE, ActionType.SUBMIT_WARRANTY_CLAIM,
                                                ActionType.PREPARE_DRAFT),
        "action_amount": action.amount,
        **extra,
    }
    return ok(action.summary, facts, rationale=action.rationale,
              evidence_ids=action.evidence_ids, target=action.target)


# --- per-category resolution logic -----------------------------------------


def _warranty(loop) -> tuple[ProposedAction, dict[str, Any]]:
    status = _fact(loop, "Warranty status")
    product = _fact(loop, "Product") or "the product"
    serial = _fact(loop, "Serial number")
    ev_ids = [i.id for i in loop.evidence.items]

    if status.startswith("Expired") or not status:
        return ProposedAction(
            action_type=ActionType.RECORD_FINDING,
            summary=f"No claim available — the {product} warranty {status.lower() or 'could not be confirmed'}.",
            rationale=("The warranty period has elapsed, so a claim would be refused. Recording this so the "
                       "matter is closed rather than left hanging."),
            target="internal", evidence_ids=ev_ids, reversible=True,
        ), {"minutes_saved": 20, "closes_loop": True, "no_action_reason": "warranty expired"}

    if serial in ("", "not recorded"):
        return ProposedAction(
            action_type=ActionType.PREPARE_DRAFT,
            summary=f"Prepare the {product} claim, but the serial number is missing.",
            rationale=("The merchant requires a serial number and none appears in the purchase record, so the "
                       "claim is prepared and held rather than submitted incomplete."),
            target="internal", evidence_ids=ev_ids, reversible=True,
        ), {"minutes_saved": 25, "blocked_on": "serial_number"}

    channel = _fact(loop, "Where claims go")
    merchant = channel or _fact(loop, "Merchant") or "the manufacturer"
    return ProposedAction(
        action_type=ActionType.SUBMIT_WARRANTY_CLAIM,
        summary=f"Submit a warranty service request for the {product}.",
        rationale=(f"The warranty is {status.lower()}, the purchase record, serial number and fault "
                   "description are all on file, and the terms list exactly these as the requirements."),
        target=merchant, amount=loop.value_at_stake or 0.0, evidence_ids=ev_ids, reversible=True,
        payload={"product": product, "serial": serial,
                 "purchase_date": _fact(loop, "Purchase date"),
                 "invoice_number": _fact(loop, "Invoice number"),
                 "retailer": _fact(loop, "Merchant"),
                 "fault": _fact(loop, "Reported fault")},
    ), {"minutes_saved": 45}


def _billing(loop) -> tuple[ProposedAction, dict[str, Any]]:
    ev_ids = [i.id for i in loop.evidence.items]
    notice = _fact(loop, "Prior notice")
    current = _fact(loop, "Current charge")
    expected = _fact(loop, "Expected charge")

    if notice:
        return ProposedAction(
            action_type=ActionType.RECORD_FINDING,
            summary="Charge is correct — the increase was notified in advance.",
            rationale=f"{notice} The higher charge matches that notice, so there is nothing to dispute.",
            target="internal", evidence_ids=ev_ids, reversible=True,
        ), {"minutes_saved": 20, "closes_loop": True, "no_action_reason": "explained by prior notice"}

    overcharge = 0.0
    for item in loop.evidence.items:
        if item.label == "Current charge":
            try:
                overcharge = float(item.value.split("INR ")[1].split(" ")[0].replace(",", ""))
            except (IndexError, ValueError):
                pass
    exp = 0.0
    for item in loop.evidence.items:
        if item.label == "Expected charge":
            try:
                exp = float(item.value.split("INR ")[1].replace(",", ""))
            except (IndexError, ValueError):
                pass
    delta = round(overcharge - exp, 2)
    provider = loop.external_party or "the provider"
    for item in loop.evidence.items:
        if item.source_type == "billing":
            provider = item.source_title.replace(" billing record", "")
            break

    return ProposedAction(
        action_type=ActionType.SUBMIT_BILLING_DISPUTE,
        summary=f"Ask {provider} to reverse the duplicated INR {delta:,.2f} through their own portal.",
        rationale=(f"{current} against {expected}. The provider's published terms say a duplicate line item "
                   "reported through the self-service portal is reversed within 7 working days, with no agent "
                   "contact needed. The request can be withdrawn before settlement."),
        target=provider, amount=delta, evidence_ids=ev_ids, reversible=True,
        payload={"invoice": _fact(loop, "Invoice number"), "reason": "duplicate plan rental line item"},
    ), {"minutes_saved": 30, "overcharge": delta}


def _appointment(loop) -> tuple[ProposedAction, dict[str, Any]]:
    ev_ids = [i.id for i in loop.evidence.items]
    conflict = _fact(loop, "Clashes with")
    slots = [i.value for i in loop.evidence.items if i.label == "Free slot"]
    if not conflict:
        return ProposedAction(
            action_type=ActionType.CONFIRM_APPOINTMENT,
            summary="Confirm the appointment.",
            rationale="The appointment is unconfirmed but nothing else is booked against it.",
            target=loop.external_party or "the clinic", evidence_ids=ev_ids, reversible=True,
        ), {"minutes_saved": 10}
    return ProposedAction(
        action_type=ActionType.RESCHEDULE_APPOINTMENT,
        summary=f"Move the appointment — it clashes with {conflict.split(',')[0]}.",
        rationale=("Two things are booked over each other and the clash is with something you marked "
                   "important. Both alternatives below are free in your calendar, but which one suits you "
                   "is a judgement I shouldn't make for you."),
        target=loop.external_party or "the clinic", evidence_ids=ev_ids, reversible=True,
        payload={"options": slots},
    ), {"minutes_saved": 20, "requires_choice": True, "choices": slots}


def _document_request(loop) -> tuple[ProposedAction, dict[str, Any]]:
    ev_ids = [i.id for i in loop.evidence.items]
    doc = _fact(loop, "Document to send")
    if not doc:
        return ProposedAction(
            action_type=ActionType.RECORD_FINDING,
            summary="Nothing current enough to send — you'll need a fresh statement.",
            rationale="Every matching document on file is older than the acceptable window.",
            target="internal", evidence_ids=ev_ids, reversible=True,
        ), {"minutes_saved": 15, "blocked_on": "current document"}
    party = loop.external_party or "the requester"
    return ProposedAction(
        action_type=ActionType.UPLOAD_DOCUMENT,
        summary=f"Send {doc.split(' (')[0]} to {party}.",
        rationale=("They asked for a current proof of address and this is the only document on file inside "
                   "their acceptable window. An older statement was found and deliberately not used."),
        target=party, evidence_ids=ev_ids, reversible=True,
        payload={"document_title": doc},
    ), {"minutes_saved": 25}


def _renewal(loop) -> tuple[ProposedAction, dict[str, Any]]:
    ev_ids = [i.id for i in loop.evidence.items]
    days = clock.days_until(loop.deadline)
    lead = max(1.0, (days or 14) - 7)
    return ProposedAction(
        action_type=ActionType.SCHEDULE_FOLLOW_UP,
        summary=(f"Watching the renewal — I'll bring you the two options "
                 f"{'in %d days' % int(lead) if lead >= 1 else 'shortly'}, before cover ends."),
        rationale=("Both options are already extracted and the choice is yours, but the cover does not end "
                   f"for {int(days) if days else 'some'} days. Interrupting you now would be noise; I'll "
                   "raise it while there is still time to act."),
        target=loop.external_party or "the insurer", evidence_ids=ev_ids, reversible=True,
        payload={"check_in_days": lead},
    ), {"minutes_saved": 20, "watch_only": True}


def _delivery(loop) -> tuple[ProposedAction, dict[str, Any]]:
    from ..connectors.registry import get_connectors

    ev_ids = [i.id for i in loop.evidence.items]
    slots = get_connectors().calendar.find_free_slots(60, within_days=4)
    slot = slots[0] if slots else clock.now().replace(hour=11, minute=0, second=0, microsecond=0)
    return ProposedAction(
        action_type=ActionType.RESCHEDULE_APPOINTMENT,
        summary=f"Book the free redelivery slot on {slot:%A %d %B at %H:%M}.",
        rationale=("The courier holds the parcel for a limited period and redelivery is free and "
                   "changeable, so booking a slot now costs nothing and avoids a return."),
        target=loop.external_party or "the courier", evidence_ids=ev_ids, reversible=True,
        payload={"tracking": _fact(loop, "Tracking") or "CVS-88213904",
                 "chosen_slot": slot.isoformat()},
    ), {"minutes_saved": 15, "chosen_slot": slot.isoformat()}


def _followup(loop) -> tuple[ProposedAction, dict[str, Any]]:
    ev_ids = [i.id for i in loop.evidence.items]
    days = clock.days_until(loop.deadline)
    return ProposedAction(
        action_type=ActionType.SCHEDULE_FOLLOW_UP,
        summary=f"Keep chasing {loop.external_party or 'them'} and check back in three days.",
        rationale=("They promised a response and have not delivered it. Scheduling the next check rather "
                   "than pestering them today."),
        target=loop.external_party or "external party", evidence_ids=ev_ids, reversible=True,
        payload={"check_in_days": 3, "deadline_days": days},
    ), {"minutes_saved": 10}


RESOLUTION_TOOLS = [list_available_actions, recommend_action]
