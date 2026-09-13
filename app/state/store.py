"""
State store for the orchestrator.

Design rule from the implementation doc: one table per contract type, keyed
by the same IDs the JSON contracts already use, storing the raw JSON blob
plus a couple of indexed lookup columns. This means an agent's schema can
grow a new field mid-build without a migration -- the blob just gets bigger.

SQLite is used (not pure in-memory dicts) so the catalog and agent log
survive a server restart mid-demo, and so the log is available for a
post-mortem after the run.
"""
from __future__ import annotations
import json
import sqlite3
import threading
from typing import Any, Iterable, Optional

from app.config import DB_PATH

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_conn = _connect()


def init_db() -> None:
    with _lock:
        cur = _conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS objects (
                object_id TEXT PRIMARY KEY,
                object_type TEXT,
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ephemerides (
                object_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS conjunctions (
                id TEXT PRIMARY KEY,
                primary_id TEXT,
                secondary_id TEXT,
                tca_utc TEXT,
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS risk_records (
                id TEXT PRIMARY KEY,
                primary_id TEXT,
                secondary_id TEXT,
                risk_tier TEXT,
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS maneuver_candidates (
                primary_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS evaluated_candidates (
                primary_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS decisions (
                primary_id TEXT PRIMARY KEY,
                decision TEXT,
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS constraints (
                scope TEXT PRIMARY KEY,        -- 'global' or a specific primary_id
                payload TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_log (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT,
                status TEXT,
                detail TEXT,
                ts TEXT
            );
            """
        )
        _conn.commit()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _upsert(table: str, key_col: str, key_val: str, payload: dict, extra_cols: Optional[dict] = None) -> None:
    extra_cols = extra_cols or {}
    cols = [key_col] + list(extra_cols.keys()) + ["payload", "updated_at"]
    vals = [key_val] + list(extra_cols.values()) + [json.dumps(payload), _now_iso()]
    placeholders = ",".join("?" for _ in cols)
    col_names = ",".join(cols)
    update_clause = ",".join(f"{c}=excluded.{c}" for c in cols if c != key_col)
    sql = (
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
        f"ON CONFLICT({key_col}) DO UPDATE SET {update_clause}"
    )
    with _lock:
        _conn.execute(sql, vals)
        _conn.commit()


def _get(table: str, key_col: str, key_val: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute(f"SELECT payload FROM {table} WHERE {key_col}=?", (key_val,)).fetchone()
    return json.loads(row["payload"]) if row else None


def _all(table: str) -> list[dict]:
    with _lock:
        rows = _conn.execute(f"SELECT payload FROM {table}").fetchall()
    return [json.loads(r["payload"]) for r in rows]


# ---- objects (Tracking Agent output) ----
def save_object(obj: dict) -> None:
    _upsert("objects", "object_id", obj["object_id"], obj, {"object_type": obj.get("object_type")})


def get_object(object_id: str) -> Optional[dict]:
    return _get("objects", "object_id", object_id)


def all_objects() -> list[dict]:
    return _all("objects")


def clear_objects() -> None:
    with _lock:
        _conn.execute("DELETE FROM objects")
        _conn.commit()


def reset_run() -> None:
    """Clear derived simulation data, preserving operator constraints and logs."""
    with _lock:
        for table in ("objects","ephemerides","conjunctions","risk_records",
                      "maneuver_candidates","evaluated_candidates","decisions"):
            _conn.execute(f"DELETE FROM {table}")
        _conn.execute("DELETE FROM constraints WHERE scope LIKE 'report:%'")
        _conn.commit()


# ---- ephemerides (Prediction Agent output) ----
def save_ephemeris(eph: dict) -> None:
    _upsert("ephemerides", "object_id", eph["object_id"], eph)


def get_ephemeris(object_id: str) -> Optional[dict]:
    return _get("ephemerides", "object_id", object_id)


def all_ephemerides() -> list[dict]:
    return _all("ephemerides")


# ---- conjunctions (Collision Detection Agent output) ----
def save_conjunctions(conjunctions: list[dict]) -> None:
    with _lock:
        _conn.execute("DELETE FROM conjunctions")
        for c in conjunctions:
            cid = f"{c['primary_id']}_vs_{c['secondary_id']}"
            _conn.execute(
                "INSERT INTO conjunctions (id, primary_id, secondary_id, tca_utc, payload, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (cid, c["primary_id"], c["secondary_id"], c["tca_utc"], json.dumps(c), _now_iso()),
            )
        _conn.commit()


def all_conjunctions() -> list[dict]:
    return _all("conjunctions")


def get_conjunction(primary_id: str, secondary_id: str) -> Optional[dict]:
    return _get("conjunctions", "id", f"{primary_id}_vs_{secondary_id}")


# ---- risk records (Risk Assessment Agent output) ----
def save_risk(risk: dict) -> None:
    rid = f"{risk['primary_id']}_vs_{risk['secondary_id']}"
    _upsert("risk_records", "id", rid, risk,
            {"primary_id": risk["primary_id"], "secondary_id": risk["secondary_id"], "risk_tier": risk["risk_tier"]})


def get_risk(primary_id: str, secondary_id: str) -> Optional[dict]:
    return _get("risk_records", "id", f"{primary_id}_vs_{secondary_id}")


def all_risks() -> list[dict]:
    return _all("risk_records")


# ---- maneuver candidates / evaluated candidates / decisions ----
def save_maneuver_candidates(primary_id: str, payload: dict) -> None:
    _upsert("maneuver_candidates", "primary_id", primary_id, payload)


def save_evaluated_candidates(primary_id: str, payload: dict) -> None:
    _upsert("evaluated_candidates", "primary_id", primary_id, payload)


def save_decision(decision: dict) -> None:
    _upsert("decisions", "primary_id", decision["primary_id"], decision, {"decision": decision["decision"]})


def get_decision(primary_id: str) -> Optional[dict]:
    return _get("decisions", "primary_id", primary_id)


def all_decisions() -> list[dict]:
    return _all("decisions")


# ---- constraints (operator commands / mission defaults) ----
def save_constraints(scope: str, constraints: dict) -> None:
    _upsert("constraints", "scope", scope, constraints)


def get_constraints(scope: str) -> Optional[dict]:
    return _get("constraints", "scope", scope)


# ---- agent log (persisted mirror of the SSE stream) ----
def append_log(agent: str, status: str, detail: str, ts: str) -> dict:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO agent_log (agent, status, detail, ts) VALUES (?,?,?,?)",
            (agent, status, detail, ts),
        )
        _conn.commit()
        event_id = cur.lastrowid
    return {"event_id": event_id, "agent": agent, "status": status, "detail": detail, "ts": ts}


def recent_log(limit: int = 200) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT event_id, agent, status, detail, ts FROM agent_log ORDER BY event_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
