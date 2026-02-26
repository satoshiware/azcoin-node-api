from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: str | None = None
_REQUIRED_SHARE_KEYS = (
    "ts",
    "worker",
    "job_id",
    "extranonce2",
    "ntime",
    "nonce",
    "accepted",
    "duplicate",
    "share_diff",
    "reason",
)


def _connection_factory(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _db_path() -> str:
    return os.getenv("AZ_MINING_DB_PATH", "/data/mining.db")


def _connection() -> sqlite3.Connection:
    if _CONN is None:
        raise RuntimeError("Share ledger is not initialized")
    return _CONN


def init_db() -> None:
    global _CONN, _DB_PATH

    db_path = _db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if _CONN is not None and _DB_PATH == db_path:
        return

    with _WRITE_LOCK:
        if _CONN is not None and _DB_PATH == db_path:
            return

        if _CONN is not None:
            _CONN.close()
            _CONN = None
            _DB_PATH = None

        conn = _connection_factory(db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shares(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              worker TEXT NOT NULL,
              job_id TEXT NOT NULL,
              extranonce2 TEXT NOT NULL,
              ntime TEXT NOT NULL,
              nonce TEXT NOT NULL,
              accepted INTEGER NOT NULL,
              duplicate INTEGER NOT NULL,
              share_diff REAL NOT NULL,
              reason TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workers(
              worker TEXT PRIMARY KEY,
              first_seen INTEGER NOT NULL,
              last_seen INTEGER NOT NULL,
              accepted INTEGER NOT NULL,
              rejected INTEGER NOT NULL,
              dup INTEGER NOT NULL,
              best_share_diff REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_shares_worker_ts ON shares(worker, ts);
            CREATE INDEX IF NOT EXISTS idx_shares_ts ON shares(ts);
            """
        )
        conn.commit()

        _CONN = conn
        _DB_PATH = db_path


def ingest_share(payload: dict[str, Any]) -> None:
    missing_keys = [key for key in _REQUIRED_SHARE_KEYS if key not in payload]
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise ValueError(f"Missing required share keys: {missing}")

    conn = _connection()

    ts = int(payload["ts"])
    worker = str(payload["worker"])
    accepted = 1 if bool(payload["accepted"]) else 0
    rejected = 0 if accepted else 1
    duplicate = 1 if bool(payload["duplicate"]) else 0
    share_diff = float(payload["share_diff"])
    reason = "" if payload["reason"] is None else str(payload["reason"])

    with _WRITE_LOCK:
        with conn:
            conn.execute(
                """
                INSERT INTO shares(
                  ts, worker, job_id, extranonce2, ntime, nonce,
                  accepted, duplicate, share_diff, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    worker,
                    str(payload["job_id"]),
                    str(payload["extranonce2"]),
                    str(payload["ntime"]),
                    str(payload["nonce"]),
                    accepted,
                    duplicate,
                    share_diff,
                    reason,
                ),
            )
            conn.execute(
                """
                INSERT INTO workers(
                  worker, first_seen, last_seen, accepted, rejected, dup, best_share_diff
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker) DO UPDATE SET
                  last_seen = MAX(workers.last_seen, excluded.last_seen),
                  accepted = workers.accepted + excluded.accepted,
                  rejected = workers.rejected + excluded.rejected,
                  dup = workers.dup + excluded.dup,
                  best_share_diff = MAX(workers.best_share_diff, excluded.best_share_diff)
                """,
                (
                    worker,
                    ts,
                    ts,
                    accepted,
                    rejected,
                    duplicate,
                    share_diff,
                ),
            )


def list_workers() -> list[dict[str, Any]]:
    conn = _connection()

    with _WRITE_LOCK:
        rows = conn.execute(
            """
            SELECT worker AS name, first_seen, last_seen, accepted, rejected, dup, best_share_diff
            FROM workers
            ORDER BY last_seen DESC, worker ASC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_worker(worker: str, include_recent: bool = True) -> dict[str, Any] | None:
    conn = _connection()

    with _WRITE_LOCK:
        row = conn.execute(
            """
            SELECT worker AS name, first_seen, last_seen, accepted, rejected, dup, best_share_diff
            FROM workers
            WHERE worker = ?
            """,
            (worker,),
        ).fetchone()
        if row is None:
            return None

        item: dict[str, Any] = dict(row)
        if include_recent:
            recent_rows = conn.execute(
                """
                SELECT id, ts, worker, job_id, extranonce2, ntime, nonce,
                       accepted, duplicate, share_diff, reason
                FROM shares
                WHERE worker = ?
                ORDER BY ts DESC, id DESC
                LIMIT 50
                """,
                (worker,),
            ).fetchall()
            item["recent_shares"] = [dict(recent_row) for recent_row in recent_rows]

    return item
