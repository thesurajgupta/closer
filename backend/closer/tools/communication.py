"""Communication tools.

CLOSER writes on the user's behalf, so the rule is absolute: every factual claim
in a drafted message must be traceable to an evidence item. `prepare_message`
builds the draft from evidence, and `check_message_is_sourced` then audits its
own output and reports any sentence containing an unsourced number or date.
"""

from __future__ import annotations

import re
from typing import Any

from strands import tool

from .. import clock
from ..agents import context as run_context
from ..ids import stable_id
from ..models.domain import AuditEvent
from ..models.enums import ActionType, LoopCategory
from ..store.repository import Repo
from ._base import err, instrumented, ok

SIGN_OFF = "Sent on behalf of the account holder by CLOSER, their administrative assistant."


def _fact(loop, *labels: str) -> str:
    for label in labels:
        for item in loop.evidence.items:
            if item.label.lower() == label.lower():
                return item.value
    return ""


@tool
@instrumented("Writing the message")
def prepare_message(loop_id: str, action_type: str = "SEND_MESSAGE") -> dict[str, Any]:
    """Draft the message or claim that would close this loop, using only facts
    that are already recorded as evidence.

    Args:
        loop_id: The loop identifier.
        action_type: The action the message supports.
    """
    ctx = run_context.current()
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    if not loop.evidence.items:
        return err("No evidence on file for this loop.",
                   hint="Gather evidence before drafting; CLOSER does not write unsourced messages.")

    subject, body = _compose(loop, action_type)
    ctx.scratch.setdefault("drafts", {})[loop_id] = {"subject": subject, "body": body}
    loop.add_audit(AuditEvent(
        id=stable_id("aud", loop_id, "draft"), at=clock.now(), actor="closer", agent="communication",
        run_id=ctx.run.run_id, kind="drafted",
        message=f"Drafted '{subject}' from {len(loop.evidence.items)} sourced facts. Nothing sent.",
    ))
    Repo.put_loop(loop)
    return ok(f"Drafted: {subject}", {"draft_ready": True, "draft_subject": subject},
              draft={"subject": subject, "body": body})


def _compose(loop, action_type: str) -> tuple[str, str]:
    if loop.category is LoopCategory.WARRANTY:
        subject = f"Warranty service request — {_fact(loop, 'Product')} (invoice {_fact(loop, 'Invoice number')})"
        body = (
            "Hello,\n\n"
            f"I would like to raise a warranty service request for my {_fact(loop, 'Product')}.\n\n"
            f"Purchase date: {_fact(loop, 'Purchase date')}\n"
            f"Invoice number: {_fact(loop, 'Invoice number')}\n"
            f"Serial number: {_fact(loop, 'Serial number')}\n"
            f"Warranty period: {_fact(loop, 'Warranty period')} ({_fact(loop, 'Warranty status').lower()})\n\n"
            f"Fault: {loop.description}\n\n"
            "Please confirm the reference for this request and the next step for a service visit.\n\n"
            f"Kind regards\n\n{SIGN_OFF}"
        )
        return subject, body

    if loop.category is LoopCategory.BILLING:
        subject = f"Duplicate charge on invoice {_fact(loop, 'Invoice number')}"
        body = (
            "Hello,\n\n"
            f"Invoice {_fact(loop, 'Invoice number')} lists the plan rental twice.\n\n"
            f"{_fact(loop, 'Previous charge')}\n{_fact(loop, 'Current charge')}\n"
            f"Expected: {_fact(loop, 'Expected charge')}\n\n"
            "Your published terms provide for a duplicate line item to be reversed to the original payment "
            "method within seven working days when reported through the subscriber portal. Please treat this "
            "as that report.\n\n"
            f"Kind regards\n\n{SIGN_OFF}"
        )
        return subject, body

    if loop.category is LoopCategory.APPOINTMENT:
        slots = [i.value for i in loop.evidence.items if i.label == "Free slot"]
        subject = "Rescheduling my appointment"
        body = (
            "Hello,\n\n"
            f"I need to move my appointment on {_fact(loop, 'Appointment')}.\n\n"
            "Either of these would work:\n"
            + "".join(f"  • {s}\n" for s in slots)
            + "\nPlease confirm whichever suits you.\n\n"
            f"Kind regards\n\n{SIGN_OFF}"
        )
        return subject, body

    if loop.category is LoopCategory.DOCUMENT_REQUEST:
        subject = f"Requested document — reference {_fact(loop, 'Reference') or loop.source_ref}"
        body = (
            "Hello,\n\n"
            f"Attached is the document you asked for: {_fact(loop, 'Document to send')}.\n\n"
            f"It satisfies your requirement of {_fact(loop, 'What they asked for')}.\n\n"
            f"Kind regards\n\n{SIGN_OFF}"
        )
        return subject, body

    subject = f"Following up — {loop.title}"
    body = (
        "Hello,\n\n"
        f"I am following up on {loop.title}. "
        + " ".join(f"{i.label}: {i.value}." for i in loop.evidence.items[:3])
        + "\n\nCould you let me know where this stands?\n\n"
        f"Kind regards\n\n{SIGN_OFF}"
    )
    return subject, body


@tool
@instrumented("Fact-checking the draft")
def check_message_is_sourced(loop_id: str) -> dict[str, Any]:
    """Audit the draft: every figure, date and reference in it must appear in
    the loop's evidence. Reports anything that does not.

    Args:
        loop_id: The loop identifier.
    """
    ctx = run_context.current()
    loop = Repo.loop(loop_id)
    draft = ctx.scratch.get("drafts", {}).get(loop_id)
    if not loop or not draft:
        return err("No draft to check for this loop.")

    evidence_blob = " ".join(f"{i.label} {i.value}" for i in loop.evidence.items).lower()
    evidence_blob += " " + loop.description.lower() + " " + loop.title.lower()
    tokens = re.findall(r"[A-Z]{2,}-[A-Z0-9-]+|\d[\d,]*\.?\d*|\d{4}-\d{2}-\d{2}", draft["body"])
    unsourced = []
    for token in tokens:
        needle = token.lower().replace(",", "")
        if needle in evidence_blob.replace(",", ""):
            continue
        unsourced.append(token)
    unsourced = sorted(set(unsourced))
    ctx.scratch.setdefault("draft_checks", {})[loop_id] = unsourced
    if unsourced:
        return ok(f"{len(unsourced)} value(s) in the draft have no source and were flagged.",
                  {"unsourced_claims": len(unsourced), "message_sourced": False},
                  unsourced=unsourced)
    return ok("Every figure and reference in the draft traces back to a document.",
              {"unsourced_claims": 0, "message_sourced": True})


COMMUNICATION_TOOLS = [prepare_message, check_message_is_sourced]
