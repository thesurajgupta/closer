"""Amazon Bedrock AgentCore Runtime entrypoint.

This is the whole deployment surface. The agents, tools, policy engine, state
machine and verification are imported unchanged from `closer` — moving CLOSER
onto AgentCore is a packaging exercise, not a rewrite, which is the property the
local architecture was designed to preserve.

    pip install bedrock-agentcore bedrock-agentcore-starter-toolkit
    agentcore configure --entrypoint deploy/agentcore/agentcore_app.py
    agentcore launch
    agentcore invoke '{"action": "run"}'

Session isolation comes from AgentCore: each `session_id` gets its own runtime
sandbox. CLOSER additionally scopes every store read and write by session, so a
shared table cannot leak one household's loops into another's.
"""

from __future__ import annotations

import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # type: ignore[import-not-found]

# Force the real model path when deployed; the local deterministic planner is a
# demo affordance, not something that should ever run in production.
os.environ.setdefault("CLOSER_MODEL_PROVIDER", "bedrock")

from closer import metrics, orchestrator  # noqa: E402
from closer.background.worker import handle_event  # noqa: E402
from closer.demo.seed import seed  # noqa: E402
from closer.models.enums import RunTrigger  # noqa: E402
from closer.observability.telemetry import configure_logging  # noqa: E402
from closer.store.repository import Repo  # noqa: E402

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Single entrypoint for every trigger.

    payload:
      {"action": "run"}                                   one full pass
      {"action": "event", "detail-type": "closer.follow_up"}   an EventBridge rule
      {"action": "decide", "plan_id": "...", "decision": "approve", "choice": "..."}
      {"action": "state"}                                 current dashboard state
      {"action": "report", "run_id": "..."}               evidence bundle
    """
    configure_logging()
    action = payload.get("action", "run")

    if payload.get("seed") and not Repo.documents():
        seed()

    if action == "run":
        run = orchestrator.run_once(RunTrigger.SCHEDULED_DISCOVERY)
        return {
            "run_id": run.run_id,
            "session_id": getattr(context, "session_id", run.session_id),
            "discovered": run.loops_discovered,
            "advanced": run.loops_advanced,
            "completed": run.loops_completed,
            "waiting": run.loops_waiting,
            "decisions_required": run.decisions_required,
            "autonomous_actions": run.autonomous_actions,
            "failures": run.failures,
            "tool_calls": len(run.tool_invocations),
            # Only what the user actually needs to be told about.
            "notifications": [
                {"loop_id": loop.id, "title": loop.title, "reason": loop.notify_reason}
                for loop in Repo.loops() if loop.notify
            ],
        }

    if action == "event":
        return handle_event(payload)

    if action == "decide":
        return orchestrator.apply_decision(
            payload["plan_id"], payload.get("decision", "approve"), payload.get("choice"),
        )

    if action == "state":
        return {
            "metrics": metrics.dashboard(),
            "loops": [
                {"id": l.id, "title": l.title, "status": l.status.value, "notify": l.notify}
                for l in Repo.loops()
            ],
        }

    if action == "report":
        return metrics.run_report(payload.get("run_id") or Repo.runs()[0].run_id)

    return {"error": f"unknown action '{action}'",
            "actions": ["run", "event", "decide", "state", "report"]}


if __name__ == "__main__":  # pragma: no cover
    app.run()
