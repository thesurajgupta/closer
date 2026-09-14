"""Loop tools: read a loop, and make the small, legal state changes an agent is
allowed to request. Status changes always go through the state machine."""

from __future__ import annotations

from typing import Any

from strands import tool

from .. import clock
from ..agents import context as run_context
from ..ids import stable_id
from ..models.domain import AuditEvent
from ..models.enums import LoopCategory, LoopStatus, Priority
from ..state import machine
from ..store.repository import Repo
from ._base import err, instrumented, ok

# What each category of loop needs before anyone should act on it.
REQUIRED_INFORMATION: dict[LoopCategory, list[str]] = {
    LoopCategory.WARRANTY: ["product", "serial_number", "purchase_date", "invoice_number",
                            "warranty_months", "fault_description", "claim_channel"],
    LoopCategory.BILLING: ["provider", "expected_amount", "charged_amount", "previous_amount",
                           "invoice_number", "provider_policy"],
    LoopCategory.REFUND: ["provider", "amount", "reason", "invoice_number"],
    LoopCategory.APPOINTMENT: ["appointment_time", "conflict", "alternative_slots"],
    LoopCategory.DOCUMENT_REQUEST: ["requested_document", "acceptable_age", "available_document", "portal"],
    LoopCategory.RENEWAL: ["current_policy", "expiry", "options"],
    LoopCategory.DELIVERY: ["tracking", "hold_period", "available_slot"],
    LoopCategory.OTHER: ["reference", "promised_response_time"],
}

EVIDENCE_QUERY: dict[LoopCategory, str] = {
    LoopCategory.WARRANTY: "warranty receipt invoice serial purchase product",
    LoopCategory.BILLING: "invoice billing policy plan monthly rental",
    LoopCategory.REFUND: "refund invoice settlement",
    LoopCategory.APPOINTMENT: "appointment treatment plan",
    LoopCategory.DOCUMENT_REQUEST: "statement proof address utility",
    LoopCategory.RENEWAL: "policy renewal options premium",
    LoopCategory.DELIVERY: "delivery notice tracking parcel",
    LoopCategory.OTHER: "ticket reference",
}


@tool
@instrumented()
def get_open_loop(loop_id: str) -> dict[str, Any]:
    """Read an open loop: what it is, what state it is in, what is known and
    what still has to be established before acting.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    required = REQUIRED_INFORMATION[loop.category]
    days = clock.days_until(loop.deadline)
    party = loop.external_party or ""
    query = " ".join(filter(None, [party, EVIDENCE_QUERY[loop.category]]))
    return ok(f"{loop.title} — {machine.describe(loop.status)}", {
        "_loop_id": loop.id, "loop_status": loop.status.value, "category": loop.category.value,
        "title": loop.title, "description": loop.description,
        "external_party": loop.external_party or "", "source_ref": loop.source_ref,
        "deadline_days": round(days, 1) if days is not None else None,
        "required_information": required,
        "evidence_query": query,
        "evidence_count": len(loop.evidence.items),
        "evidence_complete": loop.evidence.complete,
        "missing": loop.evidence.missing,
        "value_at_stake": loop.value_at_stake,
        "attempt_count": loop.attempt_count,
    })


@tool
@instrumented()
def update_open_loop(loop_id: str, status: str = "", priority: str = "", note: str = "") -> dict[str, Any]:
    """Request a state change on a loop. The transition is validated against the
    loop state machine; an illegal transition is refused.

    Args:
        loop_id: The loop identifier.
        status: Optional target status.
        priority: Optional new priority (LOW, NORMAL, HIGH, URGENT).
        note: Short note for the loop's timeline.
    """
    ctx = run_context.current()
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    if status:
        try:
            target = LoopStatus(status)
        except ValueError:
            return err(f"'{status}' is not a loop status.",
                       hint=f"Valid: {', '.join(s.value for s in LoopStatus)}")
        try:
            loop.status = machine.transition(loop.status, target)
        except machine.IllegalTransition as exc:
            return err(str(exc), hint="The loop state machine rejected this; pick a legal next state.")
    if priority:
        try:
            loop.priority = Priority(priority)
        except ValueError:
            return err(f"'{priority}' is not a priority.")
    if note:
        loop.add_audit(AuditEvent(
            id=stable_id("aud", loop_id, note, clock.now().isoformat()), at=clock.now(),
            actor="closer", agent=ctx.agent, kind="note", message=note, run_id=ctx.run.run_id,
        ))
    loop.last_activity = clock.now()
    Repo.put_loop(loop)
    return ok(f"{loop.title} is now {machine.describe(loop.status)}.",
              {"loop_status": loop.status.value, "priority": loop.priority.value})


LOOP_TOOLS = [get_open_loop, update_open_loop]
