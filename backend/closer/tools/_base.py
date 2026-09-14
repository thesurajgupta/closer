"""Shared plumbing for CLOSER tools.

Every tool is: typed in, typed out, validated, deterministic, described, and
instrumented. Tools return a uniform envelope so the planner can merge `facts`
and so failures read as useful errors rather than stack traces.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from ..agents import context as run_context
from ..observability.telemetry import TELEMETRY


def ok(summary: str, facts: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "summary": summary, "facts": facts or {}, **extra}


def err(summary: str, hint: str = "", facts: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "summary": summary, "hint": hint, "facts": facts or {}}


def instrumented(activity: str | None = None) -> Callable:
    """Wrap a tool so its latency, status and effect land in the run record and
    in the live activity feed."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = run_context.current()
            started = time.perf_counter()
            status = "success"
            try:
                with TELEMETRY.span(f"tool.{fn.__name__}", agent=ctx.agent, loop_id=ctx.loop_id):
                    result = fn(*args, **kwargs)
                if isinstance(result, dict) and result.get("ok") is False:
                    status = "error"
                return result
            except Exception as exc:  # surface as a tool error, never a crash
                status = "error"
                return err(f"{fn.__name__} failed: {exc}", hint="The agent should try a different approach.")
            finally:
                latency = int((time.perf_counter() - started) * 1000)
                summary = ""
                try:
                    summary = str(kwargs or args)[:200]
                except Exception:
                    pass
                ctx.record_tool(fn.__name__, summary, status, status, latency)
                if activity:
                    ctx.activity(activity, state="done" if status == "success" else "failed")

        return wrapper

    return decorator
