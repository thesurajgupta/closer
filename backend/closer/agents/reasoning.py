"""The reasoning rule base used by the local deterministic model provider.

CLOSER runs on a real Strands agent loop. In demo mode there may be no Bedrock
or Anthropic credentials available, so the *weights* are replaced by this
forward-chaining rule base, which plays the same role a hosted LLM plays: given
the tool schemas it has been offered and the facts returned by the tools it has
already called, it decides which tool to call next and when it has enough to
answer.

It is genuinely reactive — every branch below is taken on the basis of data that
came back from a tool, not on a pre-recorded script. Swap in Bedrock and the
tools, policy, state machine, executor and verifier are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Facts = dict[str, Any]
Called = dict[str, int]


@dataclass
class Step:
    """Either a tool call or a final answer."""

    tool: str | None
    args: dict[str, Any] | None = None
    final: str | None = None


@dataclass
class Rule:
    id: str
    when: Callable[[Facts, Called], bool]
    then: Callable[[Facts, Called], Step]
    priority: int = 50


def _not_called(name: str) -> Callable[[Facts, Called], bool]:
    return lambda facts, called: called.get(name, 0) == 0


# ---------------------------------------------------------------------------
# INTAKE — classify one inbound item into a loop, a duplicate, or nothing
# ---------------------------------------------------------------------------

INTAKE_RULES = [
    Rule(
        id="intake.read_item",
        priority=10,
        when=lambda f, c: _not_called("get_inbox_item")(f, c),
        then=lambda f, c: Step(tool="get_inbox_item", args={"item_id": f["_item_id"]}),
    ),
    Rule(
        id="intake.untrusted_content_is_a_finding",
        priority=15,
        when=lambda f, c: f.get("quarantined") is True and _not_called("record_security_finding")(f, c),
        then=lambda f, c: Step(tool="record_security_finding", args={
            "item_id": f["_item_id"],
            "finding": "Message contains instruction-shaped content from a lookalike sender.",
        }),
    ),
    Rule(
        id="intake.stop_after_quarantine",
        priority=16,
        when=lambda f, c: f.get("quarantined") is True and c.get("record_security_finding", 0) > 0,
        then=lambda f, c: Step(tool=None, final="Quarantined. No loop created from untrusted content."),
    ),
    Rule(
        id="intake.classify",
        priority=20,
        when=lambda f, c: _not_called("classify_item")(f, c),
        then=lambda f, c: Step(tool="classify_item", args={"item_id": f["_item_id"]}),
    ),
    Rule(
        id="intake.ignore_irrelevant",
        priority=25,
        when=lambda f, c: f.get("actionable") is False,
        then=lambda f, c: Step(tool=None, final=f"Ignored: {f.get('classification_reason', 'not actionable')}"),
    ),
    Rule(
        id="intake.check_duplicate",
        priority=30,
        when=lambda f, c: f.get("actionable") is True and _not_called("check_duplicate")(f, c),
        then=lambda f, c: Step(tool="check_duplicate", args={"dedupe_key": f["dedupe_key"], "item_id": f["_item_id"]}),
    ),
    Rule(
        id="intake.duplicate_stops_here",
        priority=35,
        when=lambda f, c: f.get("duplicate") is True,
        then=lambda f, c: Step(tool=None, final=f"Duplicate of {f.get('existing_loop_id')} — suppressed."),
    ),
    Rule(
        id="intake.create_loop",
        priority=40,
        when=lambda f, c: f.get("duplicate") is False and _not_called("create_open_loop")(f, c),
        then=lambda f, c: Step(tool="create_open_loop", args={
            "item_id": f["_item_id"],
            "category": f["category"],
            "title": f["title"],
            "description": f["description"],
            "dedupe_key": f["dedupe_key"],
        }),
    ),
    Rule(
        id="intake.done",
        priority=90,
        when=lambda f, c: f.get("loop_id") is not None,
        then=lambda f, c: Step(tool=None, final=f"Opened loop {f.get('loop_id')} ({f.get('category')})."),
    ),
]


# ---------------------------------------------------------------------------
# SUPERVISOR — decide which specialist a loop needs, then hand off
# ---------------------------------------------------------------------------

SUPERVISOR_RULES = [
    Rule(
        id="sup.load_loop",
        priority=10,
        when=lambda f, c: _not_called("get_open_loop")(f, c),
        then=lambda f, c: Step(tool="get_open_loop", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="sup.gather_evidence",
        priority=20,
        when=lambda f, c: f.get("loop_status") is not None and _not_called("delegate_to_evidence_agent")(f, c),
        then=lambda f, c: Step(tool="delegate_to_evidence_agent", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="sup.decide_resolution",
        priority=30,
        when=lambda f, c: c.get("delegate_to_evidence_agent", 0) > 0 and _not_called("delegate_to_resolution_agent")(f, c),
        then=lambda f, c: Step(tool="delegate_to_resolution_agent", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="sup.draft_communication",
        priority=40,
        when=lambda f, c: f.get("needs_message") is True and _not_called("delegate_to_communication_agent")(f, c),
        then=lambda f, c: Step(tool="delegate_to_communication_agent", args={
            "loop_id": f["_loop_id"], "action_type": f.get("recommended_action_type", "SEND_MESSAGE"),
        }),
    ),
    Rule(
        id="sup.submit_plan",
        priority=50,
        when=lambda f, c: f.get("recommended_action_type") is not None
        and (f.get("needs_message") is not True or c.get("delegate_to_communication_agent", 0) > 0)
        and _not_called("submit_action_plan")(f, c),
        then=lambda f, c: Step(tool="submit_action_plan", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="sup.done",
        priority=90,
        when=lambda f, c: c.get("submit_action_plan", 0) > 0,
        then=lambda f, c: Step(tool=None, final=f"Plan submitted for {f['_loop_id']}: "
                                                f"{f.get('plan_summary', 'see plan')}"),
    ),
    Rule(
        id="sup.nothing_to_do",
        priority=95,
        when=lambda f, c: c.get("delegate_to_resolution_agent", 0) > 0 and f.get("recommended_action_type") is None,
        then=lambda f, c: Step(tool=None, final="No action available for this loop."),
    ),
]


# ---------------------------------------------------------------------------
# EVIDENCE — find the facts and say honestly what is missing
# ---------------------------------------------------------------------------

EVIDENCE_RULES = [
    Rule(
        id="ev.load",
        priority=10,
        when=lambda f, c: _not_called("get_open_loop")(f, c),
        then=lambda f, c: Step(tool="get_open_loop", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="ev.search",
        priority=20,
        when=lambda f, c: f.get("evidence_query") is not None and _not_called("search_documents")(f, c),
        then=lambda f, c: Step(tool="search_documents", args={"query": f["evidence_query"], "limit": 5}),
    ),
    Rule(
        id="ev.warranty_lookup",
        priority=25,
        when=lambda f, c: f.get("category") == "WARRANTY" and _not_called("check_warranty_status")(f, c),
        then=lambda f, c: Step(tool="check_warranty_status", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="ev.billing_compare",
        priority=25,
        when=lambda f, c: f.get("category") in ("BILLING", "REFUND") and _not_called("compare_billing_periods")(f, c),
        then=lambda f, c: Step(tool="compare_billing_periods", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="ev.billing_policy",
        priority=27,
        when=lambda f, c: f.get("billing_anomaly") is True and _not_called("read_provider_policy")(f, c),
        then=lambda f, c: Step(tool="read_provider_policy", args={"provider": f.get("provider", "")}),
    ),
    Rule(
        id="ev.calendar_conflict",
        priority=25,
        when=lambda f, c: f.get("category") == "APPOINTMENT" and _not_called("check_calendar_conflicts")(f, c),
        then=lambda f, c: Step(tool="check_calendar_conflicts", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="ev.alternatives",
        priority=27,
        when=lambda f, c: f.get("conflict_detected") is True and _not_called("find_alternative_slots")(f, c),
        then=lambda f, c: Step(tool="find_alternative_slots", args={"loop_id": f["_loop_id"], "count": 2}),
    ),
    Rule(
        id="ev.document_request",
        priority=25,
        when=lambda f, c: f.get("category") == "DOCUMENT_REQUEST" and _not_called("resolve_document_request")(f, c),
        then=lambda f, c: Step(tool="resolve_document_request", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="ev.extract_fields",
        priority=30,
        when=lambda f, c: bool(f.get("candidate_document_ids")) and _not_called("extract_document_fields")(f, c),
        then=lambda f, c: Step(tool="extract_document_fields", args={
            "document_ids": f["candidate_document_ids"][:4],
            "fields": f.get("required_information", []),
        }),
    ),
    Rule(
        id="ev.record",
        priority=60,
        when=lambda f, c: _not_called("record_evidence")(f, c) and (
            c.get("extract_document_fields", 0) > 0
            or c.get("check_warranty_status", 0) > 0
            or c.get("compare_billing_periods", 0) > 0
            or c.get("check_calendar_conflicts", 0) > 0
            or c.get("resolve_document_request", 0) > 0
        ),
        then=lambda f, c: Step(tool="record_evidence", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="ev.done",
        priority=90,
        when=lambda f, c: c.get("record_evidence", 0) > 0,
        then=lambda f, c: Step(tool=None, final=f"Evidence recorded: "
                                                f"{f.get('evidence_count', 0)} facts, "
                                                f"{len(f.get('missing', []) or [])} missing."),
    ),
]


# ---------------------------------------------------------------------------
# RESOLUTION — turn evidence into exactly one recommended action
# ---------------------------------------------------------------------------

RESOLUTION_RULES = [
    Rule(
        id="res.load",
        priority=10,
        when=lambda f, c: _not_called("get_open_loop")(f, c),
        then=lambda f, c: Step(tool="get_open_loop", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="res.options",
        priority=20,
        when=lambda f, c: _not_called("list_available_actions")(f, c),
        then=lambda f, c: Step(tool="list_available_actions", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="res.no_options",
        priority=25,
        when=lambda f, c: c.get("list_available_actions", 0) > 0 and not f.get("available_actions"),
        then=lambda f, c: Step(tool=None, final="No permitted action fits this loop."),
    ),
    Rule(
        id="res.recommend",
        priority=30,
        when=lambda f, c: bool(f.get("available_actions")) and _not_called("recommend_action")(f, c),
        then=lambda f, c: Step(tool="recommend_action", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="res.done",
        priority=90,
        when=lambda f, c: c.get("recommend_action", 0) > 0,
        then=lambda f, c: Step(tool=None, final=f"Recommended: {f.get('recommended_action_type')} — "
                                                f"{f.get('recommendation_summary', '')}"),
    ),
]


# ---------------------------------------------------------------------------
# COMMUNICATION — draft a message that only contains sourced facts
# ---------------------------------------------------------------------------

COMMUNICATION_RULES = [
    Rule(
        id="com.load",
        priority=10,
        when=lambda f, c: _not_called("get_loop_evidence")(f, c),
        then=lambda f, c: Step(tool="get_loop_evidence", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="com.draft",
        priority=20,
        when=lambda f, c: c.get("get_loop_evidence", 0) > 0 and _not_called("prepare_message")(f, c),
        then=lambda f, c: Step(tool="prepare_message", args={
            "loop_id": f["_loop_id"], "action_type": f.get("_action_type", "SEND_MESSAGE"),
        }),
    ),
    Rule(
        id="com.verify_claims",
        priority=30,
        when=lambda f, c: c.get("prepare_message", 0) > 0 and _not_called("check_message_is_sourced")(f, c),
        then=lambda f, c: Step(tool="check_message_is_sourced", args={"loop_id": f["_loop_id"]}),
    ),
    Rule(
        id="com.done",
        priority=90,
        when=lambda f, c: c.get("check_message_is_sourced", 0) > 0,
        then=lambda f, c: Step(tool=None, final=f"Draft prepared and fact-checked "
                                                f"({f.get('unsourced_claims', 0)} unsourced claims)."),
    ),
]


# ---------------------------------------------------------------------------
# VERIFICATION — did the side effect actually land?
# ---------------------------------------------------------------------------

VERIFICATION_RULES = [
    Rule(
        id="ver.check",
        priority=10,
        when=lambda f, c: _not_called("check_external_state")(f, c),
        then=lambda f, c: Step(tool="check_external_state", args={"plan_id": f["_plan_id"]}),
    ),
    Rule(
        id="ver.record",
        priority=20,
        when=lambda f, c: c.get("check_external_state", 0) > 0 and _not_called("record_verification")(f, c),
        then=lambda f, c: Step(tool="record_verification", args={"plan_id": f["_plan_id"]}),
    ),
    Rule(
        id="ver.done",
        priority=90,
        when=lambda f, c: c.get("record_verification", 0) > 0,
        then=lambda f, c: Step(tool=None, final=f"Verification: {f.get('verification_status')}"),
    ),
]


RULE_SETS: dict[str, list[Rule]] = {
    "intake": INTAKE_RULES,
    "supervisor": SUPERVISOR_RULES,
    "evidence": EVIDENCE_RULES,
    "resolution": RESOLUTION_RULES,
    "communication": COMMUNICATION_RULES,
    "verification": VERIFICATION_RULES,
}
