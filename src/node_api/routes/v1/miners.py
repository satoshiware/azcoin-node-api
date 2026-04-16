from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Query

from node_api.services import translator_monitoring as tm
from node_api.settings import get_settings

router = APIRouter(prefix="/miners", tags=["miners"])

_FETCH_LIMIT = 500


def _status_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_envelope(status: str, data: dict[str, Any], detail: Any) -> dict[str, Any]:
    return {"status": status, "data": data, "detail": detail}


def _empty_miners_data(
    *,
    offset: int,
    limit: int,
    sort: str,
    order: str,
    status_filter: str,
    last_updated_ts: str,
) -> dict[str, Any]:
    return {
        "items": [],
        "total": 0,
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "order": order,
        "status_filter": status_filter,
        "last_updated_ts": last_updated_ts,
    }


def _fetch_translator_miners_envelope() -> dict[str, Any]:
    settings = get_settings()
    return tm.fetch_allowlisted(
        settings,
        "/api/v1/sv1/clients",
        {"offset": 0, "limit": _FETCH_LIMIT},
    )


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _channel_id_or_none(value: Any) -> str | int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        return value
    return None


def _first_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _connected_from_record(record: dict[str, Any]) -> tuple[bool, bool]:
    for key in ("connected", "is_connected"):
        value = record.get(key)
        if isinstance(value, bool):
            return value, True

    status_value = _str_or_none(record.get("status"))
    if status_value is not None:
        normalized = status_value.strip().lower()
        if normalized in {"connected", "active", "online"}:
            return True, True
        if normalized in {"disconnected", "inactive", "offline", "closed"}:
            return False, True

    connected_since_ts = _str_or_none(
        _first_value(record, ("connected_since_ts", "connectedSinceTs", "connected_at"))
    )
    if connected_since_ts is not None:
        return True, False

    return False, False


def _normalize_record(record: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    miner_id = _str_or_none(_first_value(record, ("miner_id", "client_id", "id", "session_id")))
    if miner_id is None:
        return None, True

    connected, connected_confident = _connected_from_record(record)
    partial = not connected_confident
    connected_since_ts = _str_or_none(
        _first_value(record, ("connected_since_ts", "connectedSinceTs", "connected_at"))
    )

    normalized = {
        "miner_id": miner_id,
        "worker_name": _str_or_none(_first_value(record, ("worker_name", "workerName", "worker"))),
        "user_identity": _str_or_none(
            _first_value(record, ("user_identity", "userIdentity", "username", "user"))
        ),
        "client_ip": None,
        "channel_id": _channel_id_or_none(_first_value(record, ("channel_id", "channelId"))),
        "connected": connected,
        "hashrate": _number_or_none(_first_value(record, ("hashrate", "hashRate"))),
        "target_hex": _str_or_none(_first_value(record, ("target_hex", "targetHex", "target"))),
        "extranonce1_hex": _str_or_none(
            _first_value(record, ("extranonce1_hex", "extranonce1Hex", "extranonce1"))
        ),
        "extranonce2_len": _int_or_none(
            _first_value(record, ("extranonce2_len", "extranonce2Len"))
        ),
        "version_rolling_mask": _str_or_none(
            _first_value(record, ("version_rolling_mask", "versionRollingMask"))
        ),
        "version_rolling_min_bit": _int_or_none(
            _first_value(record, ("version_rolling_min_bit", "versionRollingMinBit"))
        ),
        "accepted_shares": _number_or_none(
            _first_value(record, ("accepted_shares", "acceptedShares"))
        ),
        "rejected_shares": _number_or_none(
            _first_value(record, ("rejected_shares", "rejectedShares"))
        ),
        "best_diff": _number_or_none(_first_value(record, ("best_diff", "bestDiff"))),
        "last_share_ts": _str_or_none(
            _first_value(record, ("last_share_ts", "lastShareTs", "last_share_at"))
        ),
        "connected_since_ts": connected_since_ts,
    }
    return normalized, partial


def _extract_records(data: Any) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("clients", "connections", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


def _sort_value(item: dict[str, Any], sort_field: str) -> Any:
    value = item.get(sort_field)
    if isinstance(value, str):
        return value.lower()
    return value


def _sort_items(items: list[dict[str, Any]], sort_field: str, order: str) -> list[dict[str, Any]]:
    present = [item for item in items if item.get(sort_field) is not None]
    missing = [item for item in items if item.get(sort_field) is None]
    present.sort(key=lambda item: _sort_value(item, sort_field), reverse=order == "desc")
    return present + missing


@router.get("")
def miners(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    sort: Literal[
        "miner_id", "worker_name", "hashrate", "last_share_ts", "connected_since_ts"
    ] = Query(default="connected_since_ts"),
    order: Literal["asc", "desc"] = Query(default="desc"),
    status: Literal["connected", "disconnected", "all"] = Query(default="all"),
) -> dict[str, Any]:
    last_updated_ts = _status_timestamp()
    empty_data = _empty_miners_data(
        offset=offset,
        limit=limit,
        sort=sort,
        order=order,
        status_filter=status,
        last_updated_ts=last_updated_ts,
    )

    raw = _fetch_translator_miners_envelope()
    if not isinstance(raw, dict):
        return _status_envelope(
            "error",
            empty_data,
            {"code": "TRANSLATOR_UNAVAILABLE", "message": "Translator miner data unavailable"},
        )

    source_status = raw.get("status")
    records = _extract_records(raw.get("data"))
    if source_status not in {"ok", "degraded"} or records is None:
        return _status_envelope(
            "error",
            empty_data,
            {"code": "TRANSLATOR_UNAVAILABLE", "message": "Translator miner data unavailable"},
        )

    normalized_items: list[dict[str, Any]] = []
    partial_records = 0

    for record in records:
        if not isinstance(record, dict):
            partial_records += 1
            continue

        normalized, partial = _normalize_record(record)
        if normalized is None:
            partial_records += 1
            continue
        if partial:
            partial_records += 1
        normalized_items.append(normalized)

    if status == "connected":
        normalized_items = [item for item in normalized_items if item["connected"] is True]
    elif status == "disconnected":
        normalized_items = [item for item in normalized_items if item["connected"] is False]

    normalized_items = _sort_items(normalized_items, sort, order)
    total = len(normalized_items)
    paged_items = normalized_items[offset : offset + limit]

    data = {
        "items": paged_items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "order": order,
        "status_filter": status,
        "last_updated_ts": last_updated_ts,
    }

    if partial_records or source_status != "ok":
        detail: dict[str, Any] = {}
        if partial_records:
            detail["partial_records"] = partial_records
        if source_status != "ok":
            detail["source_status"] = source_status
        return _status_envelope(
            "degraded",
            data,
            detail,
        )
    return _status_envelope("ok", data, None)
