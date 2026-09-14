"""Deterministic identifier generation.

Random UUIDs would make every demo run produce a different evidence file, which
makes the judge's downloadable run report non-reproducible. Instead IDs are
content-derived hashes with a stable prefix.
"""

from __future__ import annotations

import hashlib
import itertools
import threading

_counters: dict[str, itertools.count] = {}
_lock = threading.Lock()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def sequence_id(prefix: str) -> str:
    """Monotonic per-process id, used for ephemeral things like activity rows."""
    with _lock:
        counter = _counters.setdefault(prefix, itertools.count(1))
        return f"{prefix}_{next(counter):06d}"


def reset_sequences() -> None:
    with _lock:
        _counters.clear()


def idempotency_key(loop_id: str, action_type: str, target: str, payload_digest: str) -> str:
    """Two runs that propose the same action against the same loop produce the
    same key, so the executor can refuse a duplicate side effect after a crash."""
    return stable_id("idem", loop_id, action_type, target, payload_digest)


def digest(obj: object) -> str:
    return hashlib.sha256(repr(obj).encode("utf-8")).hexdigest()[:16]
