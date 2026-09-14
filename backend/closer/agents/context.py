"""Per-run agent context.

Tools need to know which run and which loop they are acting for, and need a
place to emit activity. Passing that through every tool signature would pollute
the schemas the model sees, so it travels in a context variable instead — the
model can only ever supply the domain arguments.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from .. import clock
from ..models.domain import ActivityStep, AgentRun, ToolInvocation
from ..observability.telemetry import TELEMETRY
from ..store import db


@dataclass
class RunContext:
    run: AgentRun
    loop_id: str | None = None
    agent: str = "supervisor"
    scratch: dict[str, Any] = field(default_factory=dict)

    def activity(self, label: str, state: str = "done", detail: str = "", loop_id: str | None = None) -> None:
        step = ActivityStep(
            at=clock.now(), run_id=self.run.run_id, label=label, state=state,  # type: ignore[arg-type]
            loop_id=loop_id or self.loop_id, agent=self.agent, detail=detail,
        )
        db.append_activity(self.run.run_id, step.model_dump(mode="json"))
        TELEMETRY.emit("activity", step.model_dump(mode="json"))

    def record_tool(self, tool: str, input_summary: str, output_summary: str,
                    status: str, latency_ms: int) -> None:
        inv = ToolInvocation(
            id=f"{self.run.run_id}:{len(self.run.tool_invocations) + 1}",
            run_id=self.run.run_id, loop_id=self.loop_id, agent=self.agent, tool=tool,
            input_summary=input_summary[:240], output_summary=output_summary[:240],
            status=status,  # type: ignore[arg-type]
            latency_ms=latency_ms, at=clock.now(),
        )
        self.run.tool_invocations.append(inv)
        TELEMETRY.emit("tool", inv.model_dump(mode="json"))


_current: ContextVar[RunContext | None] = ContextVar("closer_run_context", default=None)


def current() -> RunContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("no active CLOSER run context — tools must run inside orchestrator.run()")
    return ctx


def set_context(ctx: RunContext) -> Any:
    return _current.set(ctx)


def reset_context(token: Any) -> None:
    _current.reset(token)


@contextmanager
def acting_as(agent: str, loop_id: str | None = None) -> Iterator[RunContext]:
    ctx = current()
    prev_agent, prev_loop = ctx.agent, ctx.loop_id
    ctx.agent = agent
    if loop_id is not None:
        ctx.loop_id = loop_id
    try:
        yield ctx
    finally:
        ctx.agent, ctx.loop_id = prev_agent, prev_loop
