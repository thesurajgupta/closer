"""Command line entry point.

    python -m closer seed         load the synthetic household
    python -m closer run          one full CLOSER pass, printed as it happens
    python -m closer decisions    show what is waiting for a human
    python -m closer approve ID   approve one plan (optionally --choice)
    python -m closer report [ID]  write the run evidence bundle to stdout
    python -m closer schedule     run the background scheduler in the foreground
    python -m closer serve        start the API (and the built UI if present)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import clock, metrics, orchestrator
from .agents.model_factory import provider_label
from .background.scheduler import Scheduler
from .demo.seed import seed as seed_demo
from .models.enums import RunTrigger
from .observability.telemetry import configure_logging
from .state import machine
from .store import db
from .store.repository import Repo

BOLD, DIM, GREEN, AMBER, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"


def cmd_seed(_: argparse.Namespace) -> int:
    counts = seed_demo()
    print(f"{BOLD}Loaded the synthetic household{RESET}")
    for key, value in counts.items():
        print(f"  {value:>3}  {key}")
    print(f"{DIM}Everything is fictional. No real person, company or account is involved.{RESET}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if not Repo.documents():
        seed_demo()
    if args.quiet:
        configure_logging(level=40)
    run = orchestrator.run_once(RunTrigger.MANUAL)

    print()
    print(f"{BOLD}CLOSER run {run.run_id}{RESET}  {DIM}{provider_label()}{RESET}")
    print()
    for step in db.activity_for(run.run_id):
        glyph = {"done": "✓", "running": "●", "skipped": "○", "failed": "✕"}[step["state"]]
        colour = GREEN if step["state"] == "done" else AMBER if step["state"] != "failed" else "\033[31m"
        print(f"  {colour}{glyph}{RESET} {step['label']}  {DIM}{step.get('agent') or ''}{RESET}")

    print()
    print(f"{BOLD}Result{RESET}")
    print(f"  {run.events_scanned} items read · {run.events_ignored} needed nothing · "
          f"{run.duplicates_suppressed} duplicates suppressed · "
          f"{run.injection_attempts_blocked} injection attempt(s) blocked")
    print(f"  {run.loops_discovered} loops found · {run.autonomous_actions} handled alone · "
          f"{run.loops_completed} closed · {run.loops_waiting} waiting · "
          f"{run.decisions_required} need a person")
    print()
    for loop in Repo.loops():
        mark = "!" if loop.notify else " "
        print(f"  {mark} {machine.describe(loop.status):<18} {loop.title}")
        if loop.resolution:
            print(f"      {DIM}{loop.resolution}{RESET}")
        elif loop.notify_reason:
            print(f"      {DIM}{loop.notify_reason}{RESET}")
    print()
    m = metrics.dashboard()
    print(f"{BOLD}{m['time_saved_label']}{RESET} of admin avoided · "
          f"{BOLD}₹{m['value_touched']:,.0f}{RESET} recovered or protected · "
          f"{BOLD}{m['needs_you']}{RESET} interruption(s) out of {m['total_loops']} loops")
    print(f"{DIM}{m['disclaimer']}{RESET}")
    return 0


def cmd_decisions(_: argparse.Namespace) -> int:
    pending = Repo.pending_approvals()
    if not pending:
        print("Nothing needs you.")
        return 0
    for req in pending:
        loop = Repo.loop(req.loop_id)
        print()
        print(f"{AMBER}{BOLD}{'NEEDS YOUR DECISION' if loop and loop.notify else 'READY WHEN YOU ARE'}{RESET}")
        print(f"{BOLD}{req.title}{RESET}   {DIM}{req.plan_id}{RESET}")
        print()
        print("  WHAT HAPPENED")
        print(f"    {req.what_happened}")
        print()
        print("  WHAT I FOUND")
        for line in req.what_i_found:
            print(f"    • {line}")
        print()
        print("  RECOMMENDATION")
        print(f"    {req.recommendation}")
        print()
        print("  IF YOU APPROVE")
        for line in req.what_happens_if_approved:
            print(f"    → {line}")
        if req.choices:
            print()
            print("  YOUR OPTIONS")
            for choice in req.choices:
                print(f"    [{choice['id']}] {choice['label']}")
        print()
        print(f"  WHY I'M ASKING: {req.why_asking}")
        print(f"  {DIM}approve with: python -m closer approve {req.plan_id}{RESET}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    result = orchestrator.apply_decision(args.plan_id, "reject" if args.reject else "approve", args.choice)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def cmd_report(args: argparse.Namespace) -> int:
    run_id = args.run_id
    if not run_id:
        runs = Repo.runs()
        if not runs:
            print("no runs yet", file=sys.stderr)
            return 1
        run_id = runs[0].run_id
    print(json.dumps(metrics.run_report(run_id), indent=2, default=str))
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if not Repo.documents():
        seed_demo()
    configure_logging()
    scheduler = Scheduler()
    print(f"{BOLD}CLOSER background scheduler{RESET}")
    for schedule in scheduler.schedules:
        print(f"  {schedule.name:<26} every {schedule.every_minutes} min")
    print(f"{DIM}Locally this stands in for EventBridge → SQS → agent runtime. "
          f"Ctrl-C to stop.{RESET}")
    scheduler.start(interval_seconds=args.interval)
    try:
        while True:
            import time

            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
        print("stopped")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("closer.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_now(_: argparse.Namespace) -> int:
    print(clock.now().isoformat())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="closer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="load the synthetic household").set_defaults(fn=cmd_seed)

    run = sub.add_parser("run", help="one full CLOSER pass")
    run.add_argument("--quiet", action="store_true", help="suppress the structured log")
    run.set_defaults(fn=cmd_run)

    sub.add_parser("decisions", help="show what is waiting for a human").set_defaults(fn=cmd_decisions)

    approve = sub.add_parser("approve", help="approve or reject one plan")
    approve.add_argument("plan_id")
    approve.add_argument("--choice", default=None, help="ISO timestamp for a choice-based decision")
    approve.add_argument("--reject", action="store_true")
    approve.set_defaults(fn=cmd_approve)

    report = sub.add_parser("report", help="print the run evidence bundle")
    report.add_argument("run_id", nargs="?", default=None)
    report.set_defaults(fn=cmd_report)

    schedule = sub.add_parser("schedule", help="run the background scheduler")
    schedule.add_argument("--interval", type=int, default=30)
    schedule.set_defaults(fn=cmd_schedule)

    serve = sub.add_parser("serve", help="start the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(fn=cmd_serve)

    sub.add_parser("now", help="print the demo clock").set_defaults(fn=cmd_now)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
