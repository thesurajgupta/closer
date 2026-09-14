"""Typed repositories over the JSON-document store."""

from __future__ import annotations

import json
from typing import Any

from ..models.domain import (
    ActionPlan,
    AgentRun,
    ApprovalRequest,
    AutonomySettings,
    BillingRecord,
    CalendarEvent,
    Document,
    InboxMessage,
    Notification,
    OpenLoop,
    WarrantyRecord,
)
from ..models.enums import ActionStatus, LoopStatus
from . import db


def _dump(model: Any) -> dict[str, Any]:
    return json.loads(model.model_dump_json())


class Repo:
    # --- source material ---------------------------------------------------
    @staticmethod
    def put_document(doc: Document) -> None:
        db.put("documents", doc.id, _dump(doc))

    @staticmethod
    def documents() -> list[Document]:
        return [Document(**d) for d in db.all_rows("documents")]

    @staticmethod
    def document(doc_id: str) -> Document | None:
        raw = db.get("documents", doc_id)
        return Document(**raw) if raw else None

    @staticmethod
    def put_message(msg: InboxMessage) -> None:
        db.put("messages", msg.id, _dump(msg))

    @staticmethod
    def messages() -> list[InboxMessage]:
        return sorted((InboxMessage(**m) for m in db.all_rows("messages")), key=lambda m: m.received_at)

    @staticmethod
    def message(msg_id: str) -> InboxMessage | None:
        raw = db.get("messages", msg_id)
        return InboxMessage(**raw) if raw else None

    @staticmethod
    def put_calendar(ev: CalendarEvent) -> None:
        db.put("calendar", ev.id, _dump(ev))

    @staticmethod
    def calendar() -> list[CalendarEvent]:
        return sorted((CalendarEvent(**e) for e in db.all_rows("calendar")), key=lambda e: e.starts_at)

    @staticmethod
    def calendar_event(ev_id: str) -> CalendarEvent | None:
        raw = db.get("calendar", ev_id)
        return CalendarEvent(**raw) if raw else None

    @staticmethod
    def put_billing(rec: BillingRecord) -> None:
        db.put("billing", rec.id, _dump(rec))

    @staticmethod
    def billing() -> list[BillingRecord]:
        return sorted((BillingRecord(**b) for b in db.all_rows("billing")), key=lambda b: b.charged_at)

    @staticmethod
    def put_warranty(rec: WarrantyRecord) -> None:
        db.put("warranties", rec.id, _dump(rec))

    @staticmethod
    def warranties() -> list[WarrantyRecord]:
        return [WarrantyRecord(**w) for w in db.all_rows("warranties")]

    # --- loops -------------------------------------------------------------
    @staticmethod
    def put_loop(loop: OpenLoop) -> None:
        db.put("loops", loop.id, _dump(loop), dedupe_key=loop.dedupe_key, status=loop.status.value)

    @staticmethod
    def loop(loop_id: str) -> OpenLoop | None:
        raw = db.get("loops", loop_id)
        return OpenLoop(**raw) if raw else None

    @staticmethod
    def loops() -> list[OpenLoop]:
        return sorted((OpenLoop(**l) for l in db.all_rows("loops")), key=lambda l: l.created_at)

    @staticmethod
    def loop_by_dedupe(key: str) -> OpenLoop | None:
        rows = db.all_rows("loops", "WHERE dedupe_key=?", (key,))
        return OpenLoop(**rows[0]) if rows else None

    @staticmethod
    def active_loops() -> list[OpenLoop]:
        return [l for l in Repo.loops() if not l.status.is_terminal]

    # --- plans / approvals -------------------------------------------------
    @staticmethod
    def put_plan(plan: ActionPlan) -> None:
        db.put("plans", plan.plan_id, _dump(plan), loop_id=plan.loop_id, status=plan.status.value,
               idem=plan.idempotency_key)

    @staticmethod
    def plan(plan_id: str) -> ActionPlan | None:
        raw = db.get("plans", plan_id)
        return ActionPlan(**raw) if raw else None

    @staticmethod
    def plans() -> list[ActionPlan]:
        return sorted((ActionPlan(**p) for p in db.all_rows("plans")), key=lambda p: p.created_at)

    @staticmethod
    def plans_for_loop(loop_id: str) -> list[ActionPlan]:
        return sorted((ActionPlan(**p) for p in db.all_rows("plans", "WHERE loop_id=?", (loop_id,))),
                      key=lambda p: p.created_at)

    @staticmethod
    def plan_by_idempotency(key: str) -> ActionPlan | None:
        rows = db.all_rows("plans", "WHERE idem=?", (key,))
        return ActionPlan(**rows[0]) if rows else None

    @staticmethod
    def pending_approvals() -> list[ApprovalRequest]:
        out = []
        for raw in db.all_rows("approvals"):
            req = ApprovalRequest(**raw)
            plan = Repo.plan(req.plan_id)
            if plan and plan.status == ActionStatus.AWAITING_APPROVAL:
                out.append(req)
        return out

    @staticmethod
    def put_approval(req: ApprovalRequest) -> None:
        db.put("approvals", req.plan_id, _dump(req), loop_id=req.loop_id)

    @staticmethod
    def approval(plan_id: str) -> ApprovalRequest | None:
        raw = db.get("approvals", plan_id)
        return ApprovalRequest(**raw) if raw else None

    # --- runs --------------------------------------------------------------
    @staticmethod
    def put_run(run: AgentRun) -> None:
        db.put("runs", run.run_id, _dump(run), started_at=run.started_at.isoformat())

    @staticmethod
    def run(run_id: str) -> AgentRun | None:
        raw = db.get("runs", run_id)
        return AgentRun(**raw) if raw else None

    @staticmethod
    def runs() -> list[AgentRun]:
        return sorted((AgentRun(**r) for r in db.all_rows("runs")), key=lambda r: r.started_at, reverse=True)

    # --- notifications & prefs --------------------------------------------
    @staticmethod
    def put_notification(n: Notification) -> None:
        db.put("notifications", n.id, _dump(n))

    @staticmethod
    def notifications() -> list[Notification]:
        return sorted((Notification(**n) for n in db.all_rows("notifications")), key=lambda n: n.at, reverse=True)

    @staticmethod
    def settings() -> AutonomySettings:
        raw = db.get("settings_kv", "autonomy")
        return AutonomySettings(**raw) if raw else AutonomySettings()

    @staticmethod
    def put_settings(s: AutonomySettings) -> None:
        db.put("settings_kv", "autonomy", _dump(s))

    # --- processed-event ledger (deduplication) ----------------------------
    @staticmethod
    def mark_processed(key: str, data: dict[str, Any]) -> None:
        db.put("processed", key, data)

    @staticmethod
    def is_processed(key: str) -> bool:
        return db.get("processed", key) is not None
