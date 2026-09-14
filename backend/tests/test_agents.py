"""The Strands layer.

These tests assert that CLOSER is genuinely an agent system: a real Strands
`Agent` receives real tool specifications, chooses tools on the basis of what
earlier tools returned, and stops when it has enough.
"""

from strands import Agent

from closer.agents.local_model import LocalPlannerModel
from closer.agents.specialists import (
    all_loop_tools,
    evidence_agent,
    intake_agent,
    supervisor_agent,
)


def test_specialists_are_real_strands_agents(seeded, run_ctx):
    agent = intake_agent("msg_bb_invoice")
    assert isinstance(agent, Agent)
    assert agent.name == "closer-intake"
    names = {spec["name"] for spec in agent.tool_registry.get_all_tool_specs()}
    assert "classify_item" in names


def test_each_specialist_only_gets_the_tools_it_needs(seeded, run_ctx):
    evidence = {s["name"] for s in evidence_agent("l").tool_registry.get_all_tool_specs()}
    supervisor = {s["name"] for s in supervisor_agent("l").tool_registry.get_all_tool_specs()}

    # The evidence agent cannot execute or authorise anything.
    assert "submit_action_plan" not in evidence
    assert "create_open_loop" not in evidence
    # The supervisor delegates rather than doing the detailed work itself.
    assert "delegate_to_evidence_agent" in supervisor
    assert "extract_document_fields" not in supervisor


def test_every_tool_is_typed_and_described():
    for spec in all_loop_tools():
        assert spec["description"], f"{spec['name']} has no description"


def test_tool_specs_have_input_schemas(seeded, run_ctx):
    for spec in supervisor_agent("l").tool_registry.get_all_tool_specs():
        assert "inputSchema" in spec
        assert spec["inputSchema"]["json"]["type"] == "object"


def test_the_agent_loop_actually_runs_and_calls_tools(seeded, run_ctx):
    agent = intake_agent("msg_bb_invoice")
    agent("Process inbound item msg_bb_invoice.")

    used = [block["toolUse"]["name"]
            for message in agent.messages if message["role"] == "assistant"
            for block in message["content"] if "toolUse" in block]
    assert used[0] == "get_inbox_item"
    assert "classify_item" in used
    assert "create_open_loop" in used
    assert run_ctx.run.loops_discovered == 1


def test_the_planner_branches_on_what_tools_returned(seeded, run_ctx):
    """A newsletter and an invoice go down different paths — the branch is taken
    from the classifier's answer, not from a fixed script."""
    newsletter = intake_agent("msg_newsletter")
    newsletter("Process inbound item msg_newsletter.")
    newsletter_tools = [b["toolUse"]["name"] for m in newsletter.messages if m["role"] == "assistant"
                        for b in m["content"] if "toolUse" in b]

    invoice = intake_agent("msg_bb_invoice")
    invoice("Process inbound item msg_bb_invoice.")
    invoice_tools = [b["toolUse"]["name"] for m in invoice.messages if m["role"] == "assistant"
                     for b in m["content"] if "toolUse" in b]

    assert "create_open_loop" not in newsletter_tools
    assert "create_open_loop" in invoice_tools


def test_the_planner_stops_instead_of_looping_forever(seeded, run_ctx):
    model = LocalPlannerModel(role="intake", seed_facts={"_item_id": "msg_newsletter"}, max_steps=3)
    agent = Agent(model=model, tools=[], system_prompt="test", callback_handler=None,
                  load_tools_from_directory=False)
    result = agent("go")
    assert str(result)


def test_the_model_provider_is_reported_honestly(seeded):
    from closer.agents.model_factory import provider_label

    label = provider_label()
    assert "deterministic" in label.lower()
    assert "no credentials" in label.lower()


def test_facts_merge_from_tool_results_not_from_prose():
    model = LocalPlannerModel(role="intake", seed_facts={"_item_id": "x"})
    messages = [
        {"role": "assistant", "content": [{"toolUse": {"name": "get_inbox_item", "toolUseId": "1", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "1", "status": "success",
            "content": [{"json": {"ok": True, "facts": {"quarantined": True}}}],
        }}]},
    ]
    facts = model._collect_facts(messages)
    assert facts["quarantined"] is True
    assert model._count_tool_calls(messages) == {"get_inbox_item": 1}


def test_a_specialist_handoff_returns_structured_facts_not_an_essay(seeded, run_ctx):
    from closer import orchestrator
    from closer.agents.specialists import delegate_to_evidence_agent
    from closer.store.repository import Repo

    orchestrator.run_once()
    loop = next(l for l in Repo.loops() if l.title.startswith("Warranty claim for the Sterling"))
    result = delegate_to_evidence_agent(loop_id=loop.id)
    assert result["ok"] is True
    assert isinstance(result["facts"], dict)
    assert "evidence_complete" in result["facts"]
    assert "recommended_action_type" in result["facts"]
