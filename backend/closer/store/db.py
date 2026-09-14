"""SQLite-backed store.

Structured truth lives here (loops, plans, approvals, runs, evidence refs) — the
same shape maps 1:1 onto the DynamoDB single-table design in deploy/aws. Nothing
that matters is kept only in the model's context window.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from ..config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents      (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages       (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar       (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS billing        (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS warranties     (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS loops          (id TEXT PRIMARY KEY, dedupe_key TEXT, status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans          (id TEXT PRIMARY KEY, loop_id TEXT, status TEXT, idem TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals      (id TEXT PRIMARY KEY, loop_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs           (id TEXT PRIMARY KEY, started_at TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity       (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications  (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings_kv    (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS processed      (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS side_effects   (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox         (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS external_state (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_loops_status ON loops(status);
CREATE INDEX IF NOT EXISTS idx_plans_loop   ON plans(loop_id);
CREATE INDEX IF NOT EXISTS idx_plans_idem   ON plans(idem);
CREATE INDEX IF NOT EXISTS idx_activity_run ON activity(run_id);
"""

_local = threading.local()


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    path = get_settings().db_path
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    _local.conn = conn
    _local.path = path
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.path = None


def reset() -> None:
    conn = connect()
    for table in (
        "documents", "messages", "calendar", "billing", "warranties", "loops", "plans",
        "approvals", "runs", "activity", "notifications", "settings_kv", "processed",
        "side_effects", "outbox", "external_state",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


# --- generic json-doc helpers ------------------------------------------------


def put(table: str, key: str, data: dict[str, Any], **columns: Any) -> None:
    conn = connect()
    cols = ["id", *columns.keys(), "data"] if table != "settings_kv" else ["key", "value"]
    if table == "settings_kv":
        conn.execute("INSERT OR REPLACE INTO settings_kv(key,value) VALUES(?,?)", (key, json.dumps(data)))
    elif table in {"processed", "side_effects", "external_state"}:
        conn.execute(f"INSERT OR REPLACE INTO {table}(key,data) VALUES(?,?)", (key, json.dumps(data, default=str)))
    else:
        placeholders = ",".join("?" for _ in cols)
        values = [key, *columns.values(), json.dumps(data, default=str)]
        conn.execute(f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({placeholders})", values)
    conn.commit()


def get(table: str, key: str) -> dict[str, Any] | None:
    conn = connect()
    id_col = "key" if table in {"settings_kv", "processed", "side_effects", "external_state"} else "id"
    val_col = "value" if table == "settings_kv" else "data"
    row = conn.execute(f"SELECT {val_col} AS v FROM {table} WHERE {id_col}=?", (key,)).fetchone()
    return json.loads(row["v"]) if row else None


def all_rows(table: str, where: str = "", params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    conn = connect()
    val_col = "value" if table == "settings_kv" else "data"
    sql = f"SELECT {val_col} AS v FROM {table} {where}"
    return [json.loads(r["v"]) for r in conn.execute(sql, tuple(params)).fetchall()]


def delete(table: str, key: str) -> None:
    conn = connect()
    id_col = "key" if table in {"settings_kv", "processed", "side_effects", "external_state"} else "id"
    conn.execute(f"DELETE FROM {table} WHERE {id_col}=?", (key,))
    conn.commit()


def append_activity(run_id: str, data: dict[str, Any]) -> None:
    conn = connect()
    conn.execute("INSERT INTO activity(run_id,data) VALUES(?,?)", (run_id, json.dumps(data, default=str)))
    conn.commit()


def activity_for(run_id: str) -> list[dict[str, Any]]:
    conn = connect()
    rows = conn.execute("SELECT data FROM activity WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def claim_once(key: str, data: dict[str, Any]) -> bool:
    """Atomic idempotency claim. Returns False if this key was already claimed —
    this is what survives a mid-run process crash."""
    conn = connect()
    try:
        conn.execute("INSERT INTO side_effects(key,data) VALUES(?,?)", (key, json.dumps(data, default=str)))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_claim(key: str) -> dict[str, Any] | None:
    return get("side_effects", key)
