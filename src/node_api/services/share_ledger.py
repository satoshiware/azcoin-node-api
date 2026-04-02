from __future__ import annotations

import os
import sqlite3
import threading
import time
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
    return os.getenv("AZ_SHARE_DB_PATH") or os.getenv("AZ_MINING_DB_PATH") or "/data/shares.db"


def _connection() -> sqlite3.Connection:
    if _CONN is None:
        raise RuntimeError("Share ledger is not initialized")
    return _CONN


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns that may be missing from older schema versions."""
    existing: dict[str, set[str]] = {}
    for table in ("shares", "workers"):
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing[table] = {row["name"] for row in rows}

    migrations: list[str] = []
    if "ts_ms" not in existing.get("shares", set()):
        migrations.append(
            "ALTER TABLE shares ADD COLUMN ts_ms INTEGER NOT NULL DEFAULT 0"
        )
    if "duplicate" not in existing.get("shares", set()):
        migrations.append(
            "ALTER TABLE shares ADD COLUMN duplicate INTEGER NOT NULL DEFAULT 0"
        )
    if "share_diff" not in existing.get("shares", set()):
        migrations.append(
            "ALTER TABLE shares ADD COLUMN share_diff REAL NOT NULL DEFAULT 0.0"
        )
    if "dup" not in existing.get("workers", set()):
        migrations.append(
            "ALTER TABLE workers ADD COLUMN dup INTEGER NOT NULL DEFAULT 0"
        )
    if "best_share_diff" not in existing.get("workers", set()):
        migrations.append(
            "ALTER TABLE workers ADD COLUMN best_share_diff REAL NOT NULL DEFAULT 0.0"
        )

    for stmt in migrations:
        conn.execute(stmt)


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
              ts_ms INTEGER NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS blocks(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              height INTEGER NOT NULL UNIQUE,
              block_hash TEXT NOT NULL,
              reward REAL NOT NULL,
              worker TEXT NOT NULL,
              ts INTEGER NOT NULL,
              confirmed INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_blocks_worker ON blocks(worker);
            CREATE INDEX IF NOT EXISTS idx_blocks_ts ON blocks(ts);
            """
        )

        _migrate_schema(conn)
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
            ts_ms = int(payload.get("ts_ms", ts * 1000))
            conn.execute(
                """
                INSERT INTO shares(
                  ts, ts_ms, worker, job_id, extranonce2, ntime, nonce,
                  accepted, duplicate, share_diff, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ts_ms,
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


_REQUIRED_BLOCK_KEYS = ("height", "block_hash", "reward", "worker", "ts")


def ingest_block(payload: dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_BLOCK_KEYS if k not in payload]
    if missing:
        raise ValueError(f"Missing required block keys: {', '.join(missing)}")

    conn = _connection()

    with _WRITE_LOCK:
        with conn:
            conn.execute(
                """
                INSERT INTO blocks(height, block_hash, reward, worker, ts, confirmed)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(height) DO UPDATE SET
                  block_hash = excluded.block_hash,
                  reward = excluded.reward,
                  worker = excluded.worker,
                  ts = excluded.ts,
                  confirmed = excluded.confirmed
                """,
                (
                    int(payload["height"]),
                    str(payload["block_hash"]),
                    float(payload["reward"]),
                    str(payload["worker"]),
                    int(payload["ts"]),
                    1 if payload.get("confirmed", False) else 0,
                ),
            )


def list_blocks(limit: int = 50) -> list[dict[str, Any]]:
    conn = _connection()

    with _WRITE_LOCK:
        rows = conn.execute(
            """
            SELECT height, block_hash, reward, worker, ts, confirmed
            FROM blocks
            ORDER BY height DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        username, miner_name = _parse_worker_name(d["worker"])
        d["username"] = username
        d["miner_name"] = miner_name
        result.append(d)
    return result


def _parse_worker_name(worker: str) -> tuple[str, str]:
    """Split 'username.minername' on the first dot; no dot → miner_name is ''."""
    dot_idx = worker.find(".")
    if dot_idx >= 0:
        return worker[:dot_idx], worker[dot_idx + 1:]
    return worker, ""


def _enrich_workers(
    items: list[dict[str, Any]], conn: sqlite3.Connection, now_ts: int,
) -> None:
    for item in items:
        total = item["accepted"] + item["rejected"]
        item["total_shares"] = total
        item["acceptance_rate"] = item["accepted"] / total if total else 0.0
        item["rejection_rate"] = item["rejected"] / total if total else 0.0
        item["duplicate_rate"] = item["dup"] / total if total else 0.0
        item["last_share_ts"] = item["last_seen"]
        item["seconds_since_last_share"] = max(0, now_ts - item["last_seen"])
        worker_str = item["name"]
        username, miner_name = _parse_worker_name(worker_str)
        item["raw_worker"] = worker_str
        item["username"] = username
        item["miner_name"] = miner_name

    worker_names = [item["name"] for item in items]
    if not worker_names:
        return

    cutoff_5m = now_ts - 300
    cutoff_15m = now_ts - 900
    cutoff_1h = now_ts - 3600
    placeholders = ",".join("?" * len(worker_names))
    rows = conn.execute(
        f"""
        SELECT worker,
               SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS count_5m,
               SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS count_15m,
               COUNT(*) AS count_1h
        FROM shares
        WHERE worker IN ({placeholders}) AND ts >= ?
        GROUP BY worker
        """,
        [cutoff_5m, cutoff_15m] + worker_names + [cutoff_1h],
    ).fetchall()

    counts_by_worker = {row["worker"]: dict(row) for row in rows}

    for item in items:
        counts = counts_by_worker.get(item["name"], {})
        item["recent_share_counts"] = {
            "5m": counts.get("count_5m", 0),
            "15m": counts.get("count_15m", 0),
            "1h": counts.get("count_1h", 0),
        }

    hashrate_rows = conn.execute(
        f"""
        SELECT worker, SUM(share_diff) AS sum_diff
        FROM shares
        WHERE worker IN ({placeholders}) AND ts >= ? AND accepted = 1
        GROUP BY worker
        """,
        worker_names + [cutoff_15m],
    ).fetchall()
    hashrate_by_worker = {row["worker"]: row["sum_diff"] for row in hashrate_rows}

    for item in items:
        sum_diff = hashrate_by_worker.get(item["name"], 0.0) or 0.0
        item["hashrate_miner"] = (sum_diff * 4294967296.0) / 900.0 if sum_diff else 0.0

    block_rows = conn.execute(
        f"""
        SELECT worker, COUNT(*) AS blocks_found, COALESCE(SUM(reward), 0.0) AS rewards_total
        FROM blocks
        WHERE worker IN ({placeholders})
        GROUP BY worker
        """,
        worker_names,
    ).fetchall()
    blocks_by_worker = {r["worker"]: dict(r) for r in block_rows}

    for item in items:
        bdata = blocks_by_worker.get(item["name"], {})
        item["blocks_found"] = bdata.get("blocks_found", 0)
        item["rewards_total"] = bdata.get("rewards_total", 0.0)


def list_workers() -> list[dict[str, Any]]:
    conn = _connection()
    now_ts = int(time.time())

    with _WRITE_LOCK:
        rows = conn.execute(
            """
            SELECT worker AS name, first_seen, last_seen, accepted, rejected, dup, best_share_diff
            FROM workers
            ORDER BY last_seen DESC, worker ASC
            """
        ).fetchall()
        items = [dict(row) for row in rows]
        _enrich_workers(items, conn, now_ts)

    return items


def get_worker(worker: str, include_recent: bool = True) -> dict[str, Any] | None:
    conn = _connection()
    now_ts = int(time.time())

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
        _enrich_workers([item], conn, now_ts)

        username = item["username"]
        all_worker_rows = conn.execute(
            "SELECT worker, accepted, rejected, dup FROM workers"
        ).fetchall()
        siblings = [
            dict(r) for r in all_worker_rows
            if _parse_worker_name(r["worker"])[0] == username
        ]
        sibling_names = [s["worker"] for s in siblings]
        cutoff_15m = now_ts - 900
        user_sum_diff = 0.0
        if sibling_names:
            ph = ",".join("?" * len(sibling_names))
            hr_row = conn.execute(
                f"""
                SELECT COALESCE(SUM(share_diff), 0.0) AS sd
                FROM shares
                WHERE worker IN ({ph}) AND ts >= ? AND accepted = 1
                """,
                sibling_names + [cutoff_15m],
            ).fetchone()
            user_sum_diff = hr_row["sd"] if hr_row else 0.0
        total_acc = sum(s["accepted"] for s in siblings)
        total_rej = sum(s["rejected"] for s in siblings)
        user_blocks_row = conn.execute(
            f"""
            SELECT COUNT(*) AS bf, COALESCE(SUM(reward), 0.0) AS rt
            FROM blocks WHERE worker IN ({ph})
            """,
            sibling_names,
        ).fetchone() if sibling_names else None
        user_bf = user_blocks_row["bf"] if user_blocks_row else 0
        user_rt = user_blocks_row["rt"] if user_blocks_row else 0.0
        item["user_summary"] = {
            "username": username,
            "hashrate_user": (user_sum_diff * 4294967296.0) / 900.0 if user_sum_diff else 0.0,
            "miner_count": len(siblings),
            "total_accepted": total_acc,
            "total_rejected": total_rej,
            "total_dup": sum(s["dup"] for s in siblings),
            "total_shares": total_acc + total_rej,
            "blocks_found": user_bf,
            "rewards_total": user_rt,
        }

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


def list_users() -> list[dict[str, Any]]:
    conn = _connection()
    now_ts = int(time.time())

    with _WRITE_LOCK:
        rows = conn.execute(
            "SELECT worker, first_seen, last_seen, accepted, rejected, dup FROM workers"
        ).fetchall()

        user_groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            d = dict(row)
            uname = _parse_worker_name(d["worker"])[0]
            user_groups.setdefault(uname, []).append(d)

        all_worker_names = [d["worker"] for d in (dict(r) for r in rows)]
        hashrate_by_worker: dict[str, float] = {}
        blocks_by_worker: dict[str, dict[str, Any]] = {}
        if all_worker_names:
            cutoff_15m = now_ts - 900
            ph = ",".join("?" * len(all_worker_names))
            hr_rows = conn.execute(
                f"""
                SELECT worker, SUM(share_diff) AS sum_diff
                FROM shares
                WHERE worker IN ({ph}) AND ts >= ? AND accepted = 1
                GROUP BY worker
                """,
                all_worker_names + [cutoff_15m],
            ).fetchall()
            hashrate_by_worker = {r["worker"]: r["sum_diff"] for r in hr_rows}

            blk_rows = conn.execute(
                f"""
                SELECT worker, COUNT(*) AS bf, COALESCE(SUM(reward), 0.0) AS rt
                FROM blocks
                WHERE worker IN ({ph})
                GROUP BY worker
                """,
                all_worker_names,
            ).fetchall()
            blocks_by_worker = {r["worker"]: dict(r) for r in blk_rows}

        result = []
        for uname, workers_in_group in user_groups.items():
            total_acc = sum(w["accepted"] for w in workers_in_group)
            total_rej = sum(w["rejected"] for w in workers_in_group)
            last_seen = max(w["last_seen"] for w in workers_in_group)
            first_seen = min(w["first_seen"] for w in workers_in_group)
            user_sum_diff = sum(
                (hashrate_by_worker.get(w["worker"], 0.0) or 0.0)
                for w in workers_in_group
            )
            user_bf = sum(
                blocks_by_worker.get(w["worker"], {}).get("bf", 0)
                for w in workers_in_group
            )
            user_rt = sum(
                blocks_by_worker.get(w["worker"], {}).get("rt", 0.0)
                for w in workers_in_group
            )
            result.append({
                "username": uname,
                "hashrate_user": (user_sum_diff * 4294967296.0) / 900.0 if user_sum_diff else 0.0,
                "miner_count": len(workers_in_group),
                "total_accepted": total_acc,
                "total_rejected": total_rej,
                "total_dup": sum(w["dup"] for w in workers_in_group),
                "total_shares": total_acc + total_rej,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "seconds_since_last_share": max(0, now_ts - last_seen),
                "blocks_found": user_bf,
                "rewards_total": user_rt,
            })

        result.sort(key=lambda u: (-u["last_seen"], u["username"]))

    return result


def get_user(username: str) -> dict[str, Any] | None:
    conn = _connection()
    now_ts = int(time.time())

    with _WRITE_LOCK:
        all_names = conn.execute("SELECT worker FROM workers").fetchall()
        matching_names = [
            r["worker"] for r in all_names
            if _parse_worker_name(r["worker"])[0] == username
        ]
        if not matching_names:
            return None

        ph = ",".join("?" * len(matching_names))
        rows = conn.execute(
            f"""
            SELECT worker AS name, first_seen, last_seen, accepted, rejected, dup, best_share_diff
            FROM workers
            WHERE worker IN ({ph})
            ORDER BY last_seen DESC, worker ASC
            """,
            matching_names,
        ).fetchall()
        miners = [dict(r) for r in rows]
        _enrich_workers(miners, conn, now_ts)

        total_acc = sum(m["accepted"] for m in miners)
        total_rej = sum(m["rejected"] for m in miners)
        last_seen = max(m["last_seen"] for m in miners)
        first_seen = min(m["first_seen"] for m in miners)

        cutoff_15m = now_ts - 900
        hr_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(share_diff), 0.0) AS sd
            FROM shares
            WHERE worker IN ({ph}) AND ts >= ? AND accepted = 1
            """,
            matching_names + [cutoff_15m],
        ).fetchone()
        user_sum_diff = hr_row["sd"] if hr_row else 0.0

        blk_row = conn.execute(
            f"""
            SELECT COUNT(*) AS bf, COALESCE(SUM(reward), 0.0) AS rt
            FROM blocks WHERE worker IN ({ph})
            """,
            matching_names,
        ).fetchone()
        user_bf = blk_row["bf"] if blk_row else 0
        user_rt = blk_row["rt"] if blk_row else 0.0

    return {
        "username": username,
        "hashrate_user": (user_sum_diff * 4294967296.0) / 900.0 if user_sum_diff else 0.0,
        "miner_count": len(miners),
        "total_accepted": total_acc,
        "total_rejected": total_rej,
        "total_dup": sum(m["dup"] for m in miners),
        "total_shares": total_acc + total_rej,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "seconds_since_last_share": max(0, now_ts - last_seen),
        "blocks_found": user_bf,
        "rewards_total": user_rt,
        "miners": miners,
    }
