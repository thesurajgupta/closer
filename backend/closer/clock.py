"""A single injectable clock.

The demo is reproducible because *nothing* calls datetime.now() directly; the
dataset is authored relative to `CLOSER_DEMO_NOW`, so a judge running the demo
in 2027 still sees "warranty expires in 9 months".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import get_settings

_offset = timedelta(0)


def now() -> datetime:
    s = get_settings()
    if s.is_demo:
        return datetime.fromisoformat(s.demo_now) + _offset
    return datetime.now()


def advance(delta: timedelta) -> None:
    """Move the demo clock forward (used by the 'simulate provider reply' flow
    and by tests that need a deadline to lapse)."""
    global _offset
    _offset += delta


def reset() -> None:
    global _offset
    _offset = timedelta(0)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def days_until(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (dt - now()).total_seconds() / 86400.0
