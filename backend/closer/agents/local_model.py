"""A Strands `Model` provider that runs locally and deterministically.

This is a real model provider: Strands drives it through the normal event loop —
it receives the conversation, the tool specifications and the system prompt, and
it emits Bedrock-shaped streaming events including `toolUse` blocks. Strands then
executes the tools, appends the results and calls it again. Nothing about the
agent loop, tool execution, state machine, policy engine or verification is
bypassed.

What it does *not* have is a neural network. Instead of sampling, it
forward-chains over `closer.agents.reasoning`, using the facts returned by the
tools it has already called. That keeps the demo reproducible on a laptop with
no credentials, while `CLOSER_MODEL_PROVIDER=bedrock` swaps in Claude on Amazon
Bedrock without a single change to the agents or tools.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from pydantic import BaseModel
from strands.models.model import Model
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from .reasoning import RULE_SETS, Rule, Step

_ROLE_MARKER = "closer-agent-role:"


class LocalPlannerModel(Model):
    """Deterministic, rule-driven Strands model provider."""

    provider_name = "closer-local-planner"

    def __init__(self, role: str, seed_facts: dict[str, Any] | None = None, max_steps: int = 14) -> None:
        self.role = role
        self._config: dict[str, Any] = {
            "role": role,
            "max_steps": max_steps,
            "context_window_limit": 200_000,
            "model_id": f"closer-local-planner:{role}",
        }
        self.seed_facts = dict(seed_facts or {})
        self._rules: list[Rule] = sorted(RULE_SETS[role], key=lambda r: r.priority)
        self.trace: list[str] = []

    # -- Model interface ---------------------------------------------------

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self._config

    async def structured_output(
        self, output_model: type[BaseModel], prompt: Messages, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Structured output is produced from accumulated tool facts rather than
        from generated text, so it can never be malformed."""
        facts = self._collect_facts(prompt)
        payload = {k: v for k, v in facts.items() if k in output_model.model_fields}
        yield {"output": output_model.model_validate(payload)}

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        available = {spec["name"] for spec in (tool_specs or [])}
        facts = self._collect_facts(messages)
        called = self._count_tool_calls(messages)

        step = self._next_step(facts, called, available)
        self.trace.append(f"{self.role}:{step.tool or 'final'}")

        yield {"messageStart": {"role": "assistant"}}

        if step.tool:
            tool_use_id = f"tu_{self.role}_{sum(called.values()) + 1}"
            yield {"contentBlockStart": {"start": {"toolUse": {"name": step.tool, "toolUseId": tool_use_id}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(step.args or {})}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step.final or "Done."}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}

        yield {
            "metadata": {
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0},
            }
        }

    # -- reasoning ---------------------------------------------------------

    def _next_step(self, facts: dict[str, Any], called: dict[str, int], available: set[str]) -> Step:
        if sum(called.values()) >= int(self._config["max_steps"]):
            return Step(tool=None, final="Step budget reached; stopping and reporting what I have.")
        for rule in self._rules:
            try:
                if not rule.when(facts, called):
                    continue
                step = rule.then(facts, called)
            except (KeyError, TypeError):
                # A rule whose inputs are not yet present simply does not fire.
                continue
            if step.tool is None:
                return step
            if step.tool not in available:
                continue
            if called.get(step.tool, 0) > 0 and rule.id.endswith(".repeat") is False:
                # Never call the same tool twice with the same intent; that is a
                # loop, not reasoning.
                continue
            return step
        return Step(tool=None, final="Nothing further to do.")

    def _collect_facts(self, messages: Messages) -> dict[str, Any]:
        """Merge the `facts` object returned by every tool result so far.

        Later results shadow earlier ones, which is what makes the rule base
        reactive: a tool that reports `warranty_active: false` changes which
        branch fires next."""
        facts: dict[str, Any] = dict(self.seed_facts)
        for message in messages:
            for block in message.get("content", []):
                result = block.get("toolResult") if isinstance(block, dict) else None
                if not result:
                    continue
                for item in result.get("content", []):
                    payload = item.get("json")
                    if payload is None and "text" in item:
                        try:
                            payload = json.loads(item["text"])
                        except (ValueError, TypeError):
                            payload = None
                    if isinstance(payload, dict):
                        if isinstance(payload.get("facts"), dict):
                            facts.update(payload["facts"])
                        elif result.get("status") == "success":
                            facts.update({k: v for k, v in payload.items() if not k.startswith("_")})
        return facts

    @staticmethod
    def _count_tool_calls(messages: Messages) -> dict[str, int]:
        counts: dict[str, int] = {}
        for message in messages:
            if message.get("role") != "assistant":
                continue
            for block in message.get("content", []):
                use = block.get("toolUse") if isinstance(block, dict) else None
                if use:
                    counts[use["name"]] = counts.get(use["name"], 0) + 1
        return counts
