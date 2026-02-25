from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

_WRITE_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: str | None = None


def _connection() -> sqlite3.Connection:
    if _CONN is None:
        raise RuntimeError("Share ledger is not initialized")
    return _CONN


def init_ledger(db_path: str) -> None:
    global _CONN, _DB_PATH

    if _CONN is not None and _DB_PATH == db_path:
        return

    with _WRITE_LOCK:
        if _CONN is not None and _DB_PATH == db_path:
            return

        if _CONN is not None:
            _CONN.close()
            _CONN = None
            _DB_PATH = None

        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workers(
              name TEXT PRIMARY KEY,
              first_seen INTEGER NOT NULL,
              last_seen INTEGER NOT NULL,
              accepted INTEGER NOT NULL DEFAULT 0,
              rejected INTEGER NOT NULL DEFAULT 0,
              dup INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS shares(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              ts_ms INTEGER NOT NULL,
              remote TEXT NOT NULL,
              worker TEXT NOT NULL,
              job_id TEXT NOT NULL,
              difficulty INTEGER NOT NULL,
              accepted INTEGER NOT NULL,
              reason TEXT,
              extranonce2 TEXT NOT NULL,
              ntime TEXT NOT NULL,
              nonce TEXT NOT NULL,
              version_bits TEXT,
              accepted_unvalidated INTEGER NOT NULL,
              created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_shares_ts_ms ON shares(ts_ms);
            CREATE INDEX IF NOT EXISTS idx_shares_worker ON shares(worker);
            CREATE INDEX IF NOT EXISTS idx_shares_accepted ON shares(accepted);
            """
        )
        conn.commit()

        _CONN = conn
        _DB_PATH = db_path


def record_share(event: dict[str, Any]) -> None:
    conn = _connection()

    accepted = 1 if bool(event["accepted"]) else 0
    rejected = 0 if accepted else 1
    is_duplicate = 1 if (event.get("reason") or "").lower() == "duplicate" else 0
    now_ts = int(time.time())
    worker = str(event["worker"])
    share_ts = int(event["ts"])

    with _WRITE_LOCK:
        conn.execute(
            """
            INSERT INTO shares(
              ts, ts_ms, remote, worker, job_id, difficulty, accepted, reason,
              extranonce2, ntime, nonce, version_bits, accepted_unvalidated, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                share_ts,
                int(event["ts_ms"]),
                str(event["remote"]),
                worker,
                str(event["job_id"]),
                int(event.get("difficulty", 1)),
                accepted,
                event.get("reason"),
                str(event["extranonce2"]),
                str(event["ntime"]),
                str(event["nonce"]),
                event.get("version_bits"),
                1 if bool(event.get("accepted_unvalidated", True)) else 0,
                now_ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO workers(name, first_seen, last_seen, accepted, rejected, dup)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
              last_seen = excluded.last_seen,
              accepted = workers.accepted + ?,
              rejected = workers.rejected + ?,
              dup = workers.dup + ?
            """,
            (
                worker,
                share_ts,
                share_ts,
                accepted,
                rejected,
                is_duplicate,
                accepted,
                rejected,
                is_duplicate,
            ),
        )
        conn.commit()


def list_workers(limit: int = 1000) -> list[dict[str, Any]]:
    conn = _connection()
    safe_limit = max(1, min(int(limit), 5000))
    rows = conn.execute(
        """
        SELECT name, first_seen, last_seen, accepted, rejected, dup
        FROM workers
        ORDER BY last_seen DESC, name ASC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_worker(name: str) -> dict[str, Any] | None:
    conn = _connection()
    row = conn.execute(
        """
        SELECT name, first_seen, last_seen, accepted, rejected, dup
        FROM workers
        WHERE name = ?
        """,
        (name,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)
