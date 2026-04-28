from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from node_api.settings import Settings

_DEFAULT_DB_PATH = ".data/translator_blocks_found.sqlite3"


def utc_iso_from_unix(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class TranslatorBlocksFoundStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self.initialize()

    @classmethod
    def from_settings(cls, settings: Settings) -> "TranslatorBlocksFoundStore":
        path = settings.translator_blocks_found_db_path or _DEFAULT_DB_PATH
        return cls(path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS translator_blocks_found_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity_key TEXT NOT NULL,
                    detected_time INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    worker_identity TEXT NOT NULL,
                    authorized_worker_name TEXT,
                    downstream_user_identity TEXT,
                    upstream_user_identity TEXT,
                    blocks_found_before INTEGER NOT NULL,
                    blocks_found_after INTEGER NOT NULL,
                    blocks_found_delta INTEGER NOT NULL,
                    share_work_sum_at_detection TEXT,
                    shares_acknowledged_at_detection INTEGER,
                    shares_submitted_at_detection INTEGER,
                    shares_rejected_at_detection INTEGER,
                    blockhash TEXT,
                    blockhash_status TEXT NOT NULL DEFAULT 'unresolved',
                    correlation_status TEXT NOT NULL DEFAULT 'counter_delta_only',
                    raw_snapshot_json TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE (
                        identity_key,
                        detected_time,
                        blocks_found_before,
                        blocks_found_after
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_tbfe_detected_time
                ON translator_blocks_found_events (detected_time DESC, id DESC);

                CREATE INDEX IF NOT EXISTS idx_tbfe_worker_identity
                ON translator_blocks_found_events (worker_identity);

                CREATE INDEX IF NOT EXISTS idx_tbfe_channel_id
                ON translator_blocks_found_events (channel_id);

                CREATE INDEX IF NOT EXISTS idx_tbfe_blockhash_status
                ON translator_blocks_found_events (blockhash_status);

                CREATE TABLE IF NOT EXISTS translator_blocks_found_poller_state (
                    identity_key TEXT PRIMARY KEY,
                    worker_identity TEXT NOT NULL,
                    authorized_worker_name TEXT,
                    upstream_user_identity TEXT,
                    last_channel_id INTEGER,
                    last_blocks_found INTEGER NOT NULL,
                    last_share_work_sum TEXT,
                    last_seen_time INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )

    def get_poller_state(self, identity_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    identity_key,
                    worker_identity,
                    authorized_worker_name,
                    upstream_user_identity,
                    last_channel_id,
                    last_blocks_found,
                    last_share_work_sum,
                    last_seen_time,
                    updated_at
                FROM translator_blocks_found_poller_state
                WHERE identity_key = ?
                """,
                (identity_key,),
            ).fetchone()
        return self._row_to_dict(row)

    def upsert_poller_state(
        self,
        *,
        identity_key: str,
        worker_identity: str,
        authorized_worker_name: str | None,
        upstream_user_identity: str | None,
        last_channel_id: int | None,
        last_blocks_found: int,
        last_share_work_sum: str | None,
        last_seen_time: int,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO translator_blocks_found_poller_state (
                    identity_key,
                    worker_identity,
                    authorized_worker_name,
                    upstream_user_identity,
                    last_channel_id,
                    last_blocks_found,
                    last_share_work_sum,
                    last_seen_time,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    worker_identity = excluded.worker_identity,
                    authorized_worker_name = excluded.authorized_worker_name,
                    upstream_user_identity = excluded.upstream_user_identity,
                    last_channel_id = excluded.last_channel_id,
                    last_blocks_found = excluded.last_blocks_found,
                    last_share_work_sum = excluded.last_share_work_sum,
                    last_seen_time = excluded.last_seen_time,
                    updated_at = excluded.updated_at
                """,
                (
                    identity_key,
                    worker_identity,
                    authorized_worker_name,
                    upstream_user_identity,
                    last_channel_id,
                    int(last_blocks_found),
                    last_share_work_sum,
                    int(last_seen_time),
                    now,
                ),
            )

    def insert_event(self, event: dict[str, Any]) -> bool:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO translator_blocks_found_events (
                    identity_key,
                    detected_time,
                    channel_id,
                    worker_identity,
                    authorized_worker_name,
                    downstream_user_identity,
                    upstream_user_identity,
                    blocks_found_before,
                    blocks_found_after,
                    blocks_found_delta,
                    share_work_sum_at_detection,
                    shares_acknowledged_at_detection,
                    shares_submitted_at_detection,
                    shares_rejected_at_detection,
                    blockhash,
                    blockhash_status,
                    correlation_status,
                    raw_snapshot_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["identity_key"],
                    int(event["detected_time"]),
                    int(event["channel_id"]),
                    event["worker_identity"],
                    event.get("authorized_worker_name"),
                    event.get("downstream_user_identity"),
                    event.get("upstream_user_identity"),
                    int(event["blocks_found_before"]),
                    int(event["blocks_found_after"]),
                    int(event["blocks_found_delta"]),
                    event.get("share_work_sum_at_detection"),
                    event.get("shares_acknowledged_at_detection"),
                    event.get("shares_submitted_at_detection"),
                    event.get("shares_rejected_at_detection"),
                    event.get("blockhash"),
                    event.get("blockhash_status", "unresolved"),
                    event.get("correlation_status", "counter_delta_only"),
                    event.get("raw_snapshot_json"),
                    now,
                    now,
                ),
            )
        return cur.rowcount > 0

    def list_events(
        self,
        *,
        start_time: int | None,
        end_time: int | None,
        limit: int,
        worker_identity: str | None,
        channel_id: int | None,
        blockhash_status: str | None,
    ) -> tuple[int, list[dict[str, Any]]]:
        where: list[str] = []
        params: list[Any] = []
        if start_time is not None:
            where.append("detected_time >= ?")
            params.append(int(start_time))
        if end_time is not None:
            where.append("detected_time < ?")
            params.append(int(end_time))
        if worker_identity is not None:
            where.append("worker_identity = ?")
            params.append(worker_identity)
        if channel_id is not None:
            where.append("channel_id = ?")
            params.append(int(channel_id))
        if blockhash_status is not None:
            where.append("blockhash_status = ?")
            params.append(blockhash_status)

        where_sql = ""
        if where:
            where_sql = "WHERE " + " AND ".join(where)

        with self._connect() as conn:
            total = conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM translator_blocks_found_events
                {where_sql}
                """,
                tuple(params),
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT
                    detected_time,
                    channel_id,
                    worker_identity,
                    authorized_worker_name,
                    downstream_user_identity,
                    upstream_user_identity,
                    blocks_found_before,
                    blocks_found_after,
                    blocks_found_delta,
                    share_work_sum_at_detection,
                    shares_acknowledged_at_detection,
                    shares_submitted_at_detection,
                    shares_rejected_at_detection,
                    blockhash,
                    blockhash_status,
                    correlation_status
                FROM translator_blocks_found_events
                {where_sql}
                ORDER BY detected_time DESC, id DESC
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()

        total_value = 0 if total is None else int(total["total"])
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            detected_time = int(item["detected_time"])
            item["detected_time_iso"] = utc_iso_from_unix(detected_time)
            items.append(item)
        return total_value, items

    def event_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM translator_blocks_found_events"
            ).fetchone()
        return 0 if row is None else int(row["total"])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)
