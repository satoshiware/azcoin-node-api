from __future__ import annotations

import json
import logging
import time
from typing import Any

from node_api.services import translator_miner_work as tmw
from node_api.services.translator_blocks_found_store import TranslatorBlocksFoundStore
from node_api.settings import Settings

logger = logging.getLogger(__name__)


def stable_identity_key(row: dict[str, Any]) -> str | None:
    for key in ("worker_identity", "authorized_worker_name", "upstream_user_identity"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def poll_blocks_found_once(
    settings: Settings,
    store: TranslatorBlocksFoundStore,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, int | str]:
    snapshot_envelope = snapshot or tmw.build_miner_work_snapshot(settings)
    status = snapshot_envelope.get("status")
    if status != "ok":
        detail = snapshot_envelope.get("detail") or str(status)
        raise RuntimeError(f"translator miner-work snapshot unavailable: {detail}")

    detected_time = snapshot_envelope.get("snapshot_time")
    if not isinstance(detected_time, int):
        detected_time = int(time.time())

    data = snapshot_envelope.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("translator miner-work snapshot returned invalid items payload")

    stats = {
        "status": "ok",
        "joined_rows_seen": 0,
        "state_updates": 0,
        "events_created": 0,
        "events_deduped": 0,
        "counter_resets": 0,
        "skipped_rows": 0,
    }

    for raw_row in items:
        if not isinstance(raw_row, dict):
            stats["skipped_rows"] += 1
            continue
        if raw_row.get("join_status") != "joined":
            continue

        stats["joined_rows_seen"] += 1
        identity_key = stable_identity_key(raw_row)
        if identity_key is None:
            logger.warning(
                "Skipping joined translator row with no stable identity key",
                extra={"channel_id": raw_row.get("channel_id")},
            )
            stats["skipped_rows"] += 1
            continue

        blocks_found = raw_row.get("blocks_found")
        if not isinstance(blocks_found, int) or isinstance(blocks_found, bool):
            logger.warning(
                "Skipping joined translator row with invalid blocks_found",
                extra={"identity_key": identity_key, "channel_id": raw_row.get("channel_id")},
            )
            stats["skipped_rows"] += 1
            continue

        current_channel_id = raw_row.get("channel_id")
        if not isinstance(current_channel_id, int) or isinstance(current_channel_id, bool):
            logger.warning(
                "Skipping joined translator row with invalid channel_id",
                extra={"identity_key": identity_key},
            )
            stats["skipped_rows"] += 1
            continue

        prior = store.get_poller_state(identity_key)
        share_work_sum = raw_row.get("share_work_sum")
        share_work_sum = share_work_sum if isinstance(share_work_sum, str) else None
        upstream_user_identity = raw_row.get("upstream_user_identity")
        upstream_user_identity = (
            upstream_user_identity if isinstance(upstream_user_identity, str) else None
        )
        authorized_worker_name = raw_row.get("authorized_worker_name")
        authorized_worker_name = (
            authorized_worker_name if isinstance(authorized_worker_name, str) else None
        )

        if prior is None:
            store.upsert_poller_state(
                identity_key=identity_key,
                worker_identity=identity_key,
                authorized_worker_name=authorized_worker_name,
                upstream_user_identity=upstream_user_identity,
                last_channel_id=current_channel_id,
                last_blocks_found=blocks_found,
                last_share_work_sum=share_work_sum,
                last_seen_time=detected_time,
            )
            stats["state_updates"] += 1
            continue

        prior_blocks_found = int(prior["last_blocks_found"])
        if blocks_found > prior_blocks_found:
            event_created = store.insert_event(
                {
                    "identity_key": identity_key,
                    "detected_time": detected_time,
                    "channel_id": current_channel_id,
                    "worker_identity": identity_key,
                    "authorized_worker_name": authorized_worker_name,
                    "downstream_user_identity": raw_row.get("downstream_user_identity"),
                    "upstream_user_identity": upstream_user_identity,
                    "blocks_found_before": prior_blocks_found,
                    "blocks_found_after": blocks_found,
                    "blocks_found_delta": blocks_found - prior_blocks_found,
                    "share_work_sum_at_detection": share_work_sum,
                    "shares_acknowledged_at_detection": raw_row.get(
                        "shares_acknowledged"
                    ),
                    "shares_submitted_at_detection": raw_row.get("shares_submitted"),
                    "shares_rejected_at_detection": raw_row.get("shares_rejected"),
                    "blockhash": None,
                    "blockhash_status": "unresolved",
                    "correlation_status": "counter_delta_only",
                    "raw_snapshot_json": json.dumps(
                        raw_row, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                    ),
                }
            )
            if event_created:
                stats["events_created"] += 1
            else:
                stats["events_deduped"] += 1
            store.upsert_poller_state(
                identity_key=identity_key,
                worker_identity=identity_key,
                authorized_worker_name=authorized_worker_name,
                upstream_user_identity=upstream_user_identity,
                last_channel_id=current_channel_id,
                last_blocks_found=blocks_found,
                last_share_work_sum=share_work_sum,
                last_seen_time=detected_time,
            )
            stats["state_updates"] += 1
            continue

        if blocks_found < prior_blocks_found:
            logger.warning(
                "Translator blocks_found counter reset detected; updating state without event",
                extra={
                    "identity_key": identity_key,
                    "prior_blocks_found": prior_blocks_found,
                    "current_blocks_found": blocks_found,
                    "prior_channel_id": prior.get("last_channel_id"),
                    "current_channel_id": current_channel_id,
                },
            )
            stats["counter_resets"] += 1

        store.upsert_poller_state(
            identity_key=identity_key,
            worker_identity=identity_key,
            authorized_worker_name=authorized_worker_name,
            upstream_user_identity=upstream_user_identity,
            last_channel_id=current_channel_id,
            last_blocks_found=blocks_found,
            last_share_work_sum=share_work_sum,
            last_seen_time=detected_time,
        )
        stats["state_updates"] += 1

    return stats
