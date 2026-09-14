"""The specialist agents, and the supervisor that delegates to them.

This is a Strands supervisor pattern: the specialists are real `Agent`s, exposed
to the supervisor as tools. The supervisor decides which specialist a loop needs
and hands off; each specialist has its own bounded tool set, so no agent has more
reach than its job requires.
"""

from __future__ import annotations

from typing import Any

from strands import Agent, tool

from ..agents import context as run_context
from ..config import get_settings
from ..tools.communication import COMMUNICATION_TOOLS
from ..tools.evidence import EVIDENCE_TOOLS
from ..tools.intake import INTAKE_TOOLS
from ..tools.loops import LOOP_TOOLS, get_open_loop
from ..tools.planning import PLANNING_TOOLS
from ..tools.resolution import RESOLUTION_TOOLS
from ..tools.verification import VERIFICATION_TOOLS
from . import prompts
from .model_factory import build_model


def _agent(role: str, system_prompt: str, tools: list[Any], seed_facts: dict[str, Any] | None = None) -> Agent:
    model, _ = build_model(role, seed_facts)
    return Agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        name=f"closer-{role}",
        description=f"CLOSER {role} agent",
        callback_handler=None,
        load_tools_from_directory=False,
    )


# --- specialists ------------------------------------------------------------


def intake_agent(item_id: str) -> Agent:
    return _agent("intake", prompts.INTAKE, INTAKE_TOOLS, {"_item_id": item_id})


def evidence_agent(loop_id: str) -> Agent:
    return _agent("evidence", prompts.EVIDENCE, [*EVIDENCE_TOOLS, get_open_loop], {"_loop_id": loop_id})


def resolution_agent(loop_id: str) -> Agent:
    return _agent("resolution", prompts.RESOLUTION, [*RESOLUTION_TOOLS, get_open_loop], {"_loop_id": loop_id})


def communication_agent(loop_id: str, action_type: str) -> Agent:
    return _agent("communication", prompts.COMMUNICATION, COMMUNICATION_TOOLS,
                  {"_loop_id": loop_id, "_action_type": action_type})


def verification_agent(plan_id: str) -> Agent:
    return _agent("verification", prompts.VERIFICATION, VERIFICATION_TOOLS, {"_plan_id": plan_id})


# --- delegation tools the supervisor sees -----------------------------------


@tool
def delegate_to_evidence_agent(loop_id: str) -> dict[str, Any]:
    """Hand this loop to the evidence specialist to establish the facts from the
    user's own documents and records, and to report what is missing.

    Args:
        loop_id: The loop identifier.
    """
    ctx = run_context.current()
    with run_context.acting_as("evidence", loop_id):
        ctx.activity("Looking through your documents", state="running", loop_id=loop_id)
        agent = evidence_agent(loop_id)
        result = agent(f"Establish the facts for loop {loop_id}.")
    return _handoff_result("evidence", result, loop_id)


@tool
def delegate_to_resolution_agent(loop_id: str) -> dict[str, Any]:
    """Hand this loop to the resolution specialist to choose the single action
    that best closes it.

    Args:
        loop_id: The loop identifier.
    """
    ctx = run_context.current()
    with run_context.acting_as("resolution", loop_id):
        ctx.activity("Working out what to do", state="running", loop_id=loop_id)
        agent = resolution_agent(loop_id)
        result = agent(f"Decide the best action for loop {loop_id}.")
    return _handoff_result("resolution", result, loop_id)


@tool
def delegate_to_communication_agent(loop_id: str, action_type: str = "SEND_MESSAGE") -> dict[str, Any]:
    """Hand this loop to the communication specialist to draft the message or
    claim, using only sourced facts.

    Args:
        loop_id: The loop identifier.
        action_type: The action the message supports.
    """
    ctx = run_context.current()
    with run_context.acting_as("communication", loop_id):
        ctx.activity("Drafting on your behalf", state="running", loop_id=loop_id)
        agent = communication_agent(loop_id, action_type)
        result = agent(f"Draft the {action_type} for loop {loop_id}.")
    return _handoff_result("communication", result, loop_id)


def _handoff_result(role: str, result: Any, loop_id: str) -> dict[str, Any]:
    """Collapse a specialist's run into facts the supervisor can reason over.

    The supervisor never sees the specialist's internal deliberation — only the
    structured outcome and the state the specialist left behind."""
    from ..store.repository import Repo

    loop = Repo.loop(loop_id)
    facts: dict[str, Any] = {"_loop_id": loop_id, f"{role}_done": True}
    if loop:
        facts.update({
            "evidence_complete": loop.evidence.complete,
            "evidence_count": len(loop.evidence.items),
            "missing": loop.evidence.missing,
            "confidence": loop.confidence,
        })
        if loop.recommended_action:
            facts["recommended_action_type"] = loop.recommended_action.action_type.value
            facts["recommendation_summary"] = loop.recommended_action.summary
            facts["needs_message"] = loop.recommended_action.action_type.value in (
                "SEND_MESSAGE", "SUBMIT_WARRANTY_CLAIM", "PREPARE_DRAFT", "UPLOAD_DOCUMENT",
            )
    return {
        "ok": True,
        "summary": str(result)[:400] if result is not None else f"{role} finished",
        "facts": facts,
    }


SUPERVISOR_TOOLS = [
    get_open_loop,
    delegate_to_evidence_agent,
    delegate_to_resolution_agent,
    delegate_to_communication_agent,
    *PLANNING_TOOLS,
]


def supervisor_agent(loop_id: str) -> Agent:
    return _agent("supervisor", prompts.SUPERVISOR, SUPERVISOR_TOOLS, {"_loop_id": loop_id})


def all_loop_tools() -> list[Any]:
    """Every tool CLOSER exposes, for the architecture/evidence panel."""
    seen: dict[str, Any] = {}
    for group in (INTAKE_TOOLS, EVIDENCE_TOOLS, RESOLUTION_TOOLS, COMMUNICATION_TOOLS,
                  PLANNING_TOOLS, VERIFICATION_TOOLS, LOOP_TOOLS,
                  [delegate_to_evidence_agent, delegate_to_resolution_agent, delegate_to_communication_agent]):
        for t in group:
            spec = getattr(t, "tool_spec", None)
            name = spec["name"] if spec else getattr(t, "__name__", str(t))
            seen[name] = spec
    return [{"name": k, "description": (v or {}).get("description", "").strip().split("\n")[0]}
            for k, v in sorted(seen.items())]
