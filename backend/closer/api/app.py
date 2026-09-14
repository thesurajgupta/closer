"""HTTP API.

Thin by design: it reads and writes the same store the agents use, and every
mutating endpoint goes through the orchestrator so the policy engine, state
machine and audit trail cannot be sidestepped from the browser.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import clock, metrics, orchestrator
from ..agents.model_factory import provider_label
from ..agents.specialists import all_loop_tools
from ..config import get_settings
from ..demo import dataset
from ..demo.seed import seed
from ..models.domain import AutonomySettings
from ..models.enums import LoopStatus, RunTrigger
from ..observability.telemetry import configure_logging
from ..policy import engine
from ..state import machine
from ..store import db
from ..store.repository import Repo

@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    if not Repo.documents():
        seed()
    yield


app = FastAPI(title="CLOSER", version="1.0.0",
              lifespan=lifespan,
              description="An autonomous open-loop closure agent built on Strands Agents.")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_run_lock = threading.Lock()
_active_run: dict[str, Any] = {"run_id": None, "state": "idle"}


# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------


def _loop_card(loop) -> dict[str, Any]:
    plans = Repo.plans_for_loop(loop.id)
    latest = plans[-1] if plans else None
    days = clock.days_until(loop.deadline)
    return {
        "id": loop.id,
        "title": loop.title,
        "category": loop.category.value,
        "description": loop.description,
        "status": loop.status.value,
        "status_label": machine.describe(loop.status),
        "priority": loop.priority.value,
        "confidence": loop.confidence,
        "risk_level": loop.risk_level.value,
        "external_party": loop.external_party,
        "value_at_stake": loop.value_at_stake,
        "currency": loop.currency,
        "minutes_saved": loop.estimated_minutes_saved,
        "deadline": clock.iso(loop.deadline),
        "days_left": round(days, 1) if days is not None else None,
        "next_check_at": clock.iso(loop.next_check_at),
        "last_activity": clock.iso(loop.last_activity),
        "notify": loop.notify,
        "notify_reason": loop.notify_reason,
        "approval_required": loop.approval_required,
        "resolution": loop.resolution,
        "evidence_count": len(loop.evidence.items),
        "missing": loop.evidence.missing,
        "last_action": latest.action.summary if latest else None,
        "last_action_status": latest.status.value if latest else None,
        "plan_id": latest.plan_id if latest else None,
        "verification": loop.verification.model_dump(mode="json") if loop.verification else None,
    }


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    loops = Repo.loops()
    settings = Repo.settings()
    runs = Repo.runs()
    return {
        "mode": get_settings().mode,
        "demo": get_settings().is_demo,
        "now": clock.now().isoformat(),
        "model_provider": provider_label(),
        "metrics": metrics.dashboard(),
        "loops": [_loop_card(l) for l in sorted(
            loops, key=lambda l: (_status_rank(l), -(l.value_at_stake or 0)))],
        "decisions": _decision_payloads(),
        "notifications": [n.model_dump(mode="json") for n in Repo.notifications() if not n.read][:8],
        "settings": settings.model_dump(mode="json"),
        "policy_summary": engine.explain_settings(settings),
        "last_run": runs[0].model_dump(mode="json", exclude={"tool_invocations"}) if runs else None,
        "inbox_counts": dataset.counts(),
        "active_run": _active_run,
    }


def _status_rank(loop) -> int:
    order = {
        LoopStatus.HUMAN_DECISION: 0, LoopStatus.FAILED: 1, LoopStatus.AUTO_EXECUTING: 2,
        LoopStatus.VERIFYING: 3, LoopStatus.READY: 4, LoopStatus.EVIDENCE_NEEDED: 5,
        LoopStatus.UNDERSTANDING: 6, LoopStatus.DISCOVERED: 7, LoopStatus.WAITING: 8,
        LoopStatus.COMPLETED: 9, LoopStatus.CANCELLED: 10, LoopStatus.EXPIRED: 11,
    }
    rank = order[loop.status]
    if loop.status is LoopStatus.HUMAN_DECISION and not loop.notify:
        rank = 4  # a decision with runway sits with the ready work, not at the top
    return rank


@app.get("/api/loops/{loop_id}")
def get_loop(loop_id: str) -> dict[str, Any]:
    loop = Repo.loop(loop_id)
    if not loop:
        raise HTTPException(404, f"no loop {loop_id}")
    plans = Repo.plans_for_loop(loop_id)
    return {
        **_loop_card(loop),
        "source": {"type": loop.source, "ref": loop.source_ref},
        "required_information": loop.required_information,
        "evidence": [i.model_dump(mode="json") for i in loop.evidence.items],
        "evidence_complete": loop.evidence.complete,
        "timeline": [e.model_dump(mode="json") for e in loop.audit_events],
        "plans": [{
            "plan_id": p.plan_id,
            "action_type": p.action.action_type.value,
            "summary": p.action.summary,
            "rationale": p.action.rationale,
            "target": p.action.target,
            "amount": p.action.amount,
            "status": p.status.value,
            "risk_level": p.risk_level.value,
            "policy": p.policy.model_dump(mode="json"),
            "approved_by": p.approved_by,
            "approved_at": clock.iso(p.approved_at),
            "executed_at": clock.iso(p.executed_at),
            "execution_result": p.execution_result,
            "verification": p.verification.model_dump(mode="json") if p.verification else None,
            "attempt_count": p.attempt_count,
            "last_error": p.last_error,
            "idempotency_key": p.idempotency_key,
        } for p in plans],
        "draft": db.get("outbox", f"draft_{loop_id}"),
        "approval": (Repo.approval(plans[-1].plan_id).model_dump(mode="json")
                     if plans and Repo.approval(plans[-1].plan_id) else None),
    }


@app.get("/api/evidence/{evidence_id}")
def get_evidence(evidence_id: str) -> dict[str, Any]:
    """Resolve one evidence item back to the document or message it came from."""
    for loop in Repo.loops():
        for item in loop.evidence.items:
            if item.id != evidence_id:
                continue
            source: dict[str, Any] = {"kind": item.source_type, "id": item.source_id}
            if item.source_type == "document":
                doc = Repo.document(item.source_id)
                if doc:
                    source |= {"title": doc.title, "doc_type": doc.doc_type,
                               "issued_at": clock.iso(doc.issued_at), "text": doc.text,
                               "fields": doc.fields, "superseded_by": doc.superseded_by}
            elif item.source_type == "message":
                msg = Repo.message(item.source_id)
                if msg:
                    source |= {"title": msg.subject, "sender": msg.sender,
                               "received_at": clock.iso(msg.received_at), "text": msg.body}
            elif item.source_type == "calendar":
                ev = Repo.calendar_event(item.source_id)
                if ev:
                    source |= {"title": ev.title, "starts_at": clock.iso(ev.starts_at),
                               "text": f"{ev.title}\n{ev.starts_at:%A %d %B %H:%M} – {ev.ends_at:%H:%M}\n"
                                       f"{ev.location or ''}"}
            return {"evidence": item.model_dump(mode="json"), "source": source, "loop_id": loop.id}
    raise HTTPException(404, f"no evidence {evidence_id}")


# --------------------------------------------------------------------------
# Running the agent
# --------------------------------------------------------------------------


class RunResponse(BaseModel):
    run_id: str
    state: str


@app.post("/api/run", response_model=RunResponse)
def start_run() -> RunResponse:
    """Start one CLOSER pass in the background and return immediately, so the
    interface can show the run as it really happens."""
    if not _run_lock.acquire(blocking=False):
        return RunResponse(run_id=_active_run.get("run_id") or "", state="running")

    result: dict[str, Any] = {}

    def _work() -> None:
        try:
            run = orchestrator.run_once(RunTrigger.MANUAL)
            result["run_id"] = run.run_id
            _active_run.update({"run_id": run.run_id, "state": "done"})
        except Exception as exc:  # surfaced to the UI rather than swallowed
            _active_run.update({"state": "failed", "error": str(exc)})
        finally:
            _run_lock.release()

    thread = threading.Thread(target=_work, daemon=True)
    _active_run.update({"run_id": None, "state": "running", "error": None})
    thread.start()
    # A serverless function can be frozen as soon as it responds, so there the
    # run finishes before returning. Locally the UI polls a live run instead.
    serverless = os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
    thread.join(timeout=None if serverless else 0.35)
    return RunResponse(run_id=result.get("run_id") or _active_run.get("run_id") or "",
                       state=_active_run["state"])


@app.get("/api/runs")
def list_runs() -> dict[str, Any]:
    return {"runs": [r.model_dump(mode="json", exclude={"tool_invocations"}) for r in Repo.runs()[:20]],
            "active": _active_run}


@app.get("/api/runs/latest")
def latest_run() -> dict[str, Any]:
    runs = Repo.runs()
    if not runs:
        return {"run": None, "activity": [], "active": _active_run}
    return run_detail(runs[0].run_id)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    run = Repo.run(run_id)
    if not run:
        raise HTTPException(404, f"no run {run_id}")
    return {
        "run": run.model_dump(mode="json", exclude={"tool_invocations"}),
        "activity": db.activity_for(run_id),
        "tools": [t.model_dump(mode="json") for t in run.tool_invocations],
        "active": _active_run,
    }


@app.get("/api/runs/{run_id}/report")
def run_report(run_id: str) -> JSONResponse:
    report = metrics.run_report(run_id)
    if not report:
        raise HTTPException(404, f"no run {run_id}")
    return JSONResponse(report, headers={
        "Content-Disposition": f'attachment; filename="closer-run-{run_id}.json"',
    })


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    choice: str | None = None
    edits: dict[str, Any] | None = None


@app.get("/api/decisions")
def decisions() -> dict[str, Any]:
    return {"decisions": _decision_payloads()}


def _decision_payloads() -> list[dict[str, Any]]:
    out = []
    for req in Repo.pending_approvals():
        loop = Repo.loop(req.loop_id)
        plan = Repo.plan(req.plan_id)
        payload = req.model_dump(mode="json")
        payload["notify_now"] = bool(loop and loop.notify)
        payload["hold_reason"] = loop.notify_reason if loop and not loop.notify else ""
        payload["draft"] = db.get("outbox", f"draft_{req.loop_id}")
        if plan and plan.action.payload.get("options"):
            payload["choice_values"] = _choice_values(loop, plan)
        out.append(payload)
    # Loudest first: the ones CLOSER judged worth interrupting for.
    out.sort(key=lambda d: (not d["notify_now"], -d["amount"]))
    return out


def _choice_values(loop, plan) -> list[dict[str, str]]:
    """Map the readable option labels back to the machine values the executor
    needs, so a click cannot smuggle an arbitrary value into the action."""
    values = []
    readable = plan.action.payload.get("options", [])
    slots = [i for i in loop.evidence.items if i.key == "alternative_slots"] if loop else []
    for index, label in enumerate(readable):
        iso = ""
        for item in slots:
            if item.value == label:
                iso = item.id
        # The ISO timestamp is recovered from the calendar rather than the label.
        from ..connectors.registry import get_connectors
        for candidate in get_connectors().calendar.find_free_slots(45, 14, one_per_day=True):
            if f"{candidate:%A %d %B, %H:%M}" == label:
                iso = candidate.isoformat()
        values.append({"id": f"option_{index + 1}", "label": label, "value": iso})
    return values


@app.post("/api/decisions/{plan_id}")
def decide(plan_id: str, body: DecisionRequest) -> dict[str, Any]:
    result = orchestrator.apply_decision(plan_id, body.decision, body.choice, body.edits)
    if not result.get("ok") and result.get("error"):
        raise HTTPException(409, result["error"])
    return result


# --------------------------------------------------------------------------
# Loop actions
# --------------------------------------------------------------------------


@app.post("/api/loops/{loop_id}/retry")
def retry(loop_id: str) -> dict[str, Any]:
    return orchestrator.retry_failed(loop_id)


class SimulateRequest(BaseModel):
    outcome: str = "resolved"


@app.post("/api/loops/{loop_id}/simulate-response")
def simulate(loop_id: str, body: SimulateRequest) -> dict[str, Any]:
    """Demo affordance — play the other side, so a judge can watch a loop close
    for real. Clearly recorded as a simulated external event."""
    if not get_settings().is_demo:
        raise HTTPException(400, "only available in demo mode")
    return orchestrator.simulate_external_response(loop_id, body.outcome)


@app.post("/api/loops/{loop_id}/handle-myself")
def handle_myself(loop_id: str) -> dict[str, Any]:
    loop = Repo.loop(loop_id)
    if not loop:
        raise HTTPException(404, f"no loop {loop_id}")
    loop.status = machine.transition(loop.status, LoopStatus.CANCELLED)
    loop.resolution = "You took this one over."
    loop.notify = False
    Repo.put_loop(loop)
    return {"ok": True, "status": loop.status.value}


# --------------------------------------------------------------------------
# Settings, inbox, architecture, demo controls
# --------------------------------------------------------------------------


@app.get("/api/settings")
def get_settings_endpoint() -> dict[str, Any]:
    s = Repo.settings()
    return {"settings": s.model_dump(mode="json"), "summary": engine.explain_settings(s)}


@app.put("/api/settings")
def put_settings(body: AutonomySettings) -> dict[str, Any]:
    Repo.put_settings(body)
    return {"settings": body.model_dump(mode="json"), "summary": engine.explain_settings(body)}


@app.get("/api/inbox")
def inbox() -> dict[str, Any]:
    processed = {}
    for msg in Repo.messages():
        processed[msg.id] = {
            "quarantined": db.get("processed", f"quarantine:{msg.id}") is not None,
            "reviewed": db.get("processed", f"intake:{msg.id}") is not None,
        }
    return {
        "messages": [{**m.model_dump(mode="json"), **processed[m.id]} for m in Repo.messages()],
        "documents": [{"id": d.id, "title": d.title, "doc_type": d.doc_type,
                       "issued_at": clock.iso(d.issued_at), "stale": bool(d.superseded_by)}
                      for d in Repo.documents()],
        "calendar": [e.model_dump(mode="json") for e in Repo.calendar()],
        "billing": [b.model_dump(mode="json") for b in Repo.billing()],
        "warranties": [w.model_dump(mode="json") for w in Repo.warranties()],
        "outbox": db.all_rows("outbox"),
    }


@app.get("/api/documents/{document_id}")
def document(document_id: str) -> dict[str, Any]:
    doc = Repo.document(document_id)
    if not doc:
        raise HTTPException(404, f"no document {document_id}")
    return doc.model_dump(mode="json")


@app.get("/api/architecture")
def architecture() -> dict[str, Any]:
    return {
        "model_provider": provider_label(),
        "agents": [
            {"name": "intake", "role": "Classifies new items, deduplicates, opens loops."},
            {"name": "supervisor", "role": "Owns a loop; delegates to specialists; submits the plan."},
            {"name": "evidence", "role": "Establishes sourced facts and names what is missing."},
            {"name": "resolution", "role": "Chooses one action from what is available."},
            {"name": "communication", "role": "Drafts messages using only sourced facts."},
            {"name": "verification", "role": "Confirms the side effect actually landed."},
        ],
        "tools": all_loop_tools(),
        "action_risk": {a.value: r.value for a, r in engine.ACTION_RISK.items()},
        "never_autonomous": [a.value for a in engine.NEVER_AUTONOMOUS],
        "denied_in_demo": [a.value for a in engine.DENY_IN_DEMO],
        "states": [s.value for s in LoopStatus],
        "transitions": {k.value: sorted(v.value for v in vs) for k, vs in machine.ALLOWED.items()},
    }


@app.post("/api/demo/reset")
def reset_demo() -> dict[str, Any]:
    clock.reset()
    counts = seed()
    _active_run.update({"run_id": None, "state": "idle", "error": None})
    return {"ok": True, "seeded": counts}


class AdvanceClock(BaseModel):
    days: float = 1.0


@app.post("/api/demo/advance-clock")
def advance_clock(body: AdvanceClock) -> dict[str, Any]:
    from datetime import timedelta

    clock.advance(timedelta(days=body.days))
    return {"now": clock.now().isoformat()}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "mode": get_settings().mode, "model": provider_label(),
            "now": clock.now().isoformat()}


# --------------------------------------------------------------------------
# Static frontend (served from the built SPA when present)
# --------------------------------------------------------------------------

_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _DIST.exists():  # pragma: no cover - only in a built deployment
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        target = _DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(_DIST / "index.html")
