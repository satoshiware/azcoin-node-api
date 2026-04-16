from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Query

from node_api.routes.v1 import miners as miners_route

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _status_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_envelope(status: str, data: dict[str, Any], detail: Any) -> dict[str, Any]:
    return {"status": status, "data": data, "detail": detail}


def _empty_hashrate_data(
    *,
    window: str,
    bucket: str,
    miner_id: str | None,
    last_updated_ts: str,
) -> dict[str, Any]:
    return {
        "series": [],
        "window": window,
        "bucket": bucket,
        "miner_id": miner_id,
        "unit": "hps",
        "last_updated_ts": last_updated_ts,
    }


def _empty_shares_data(
    *,
    window: str,
    bucket: str,
    miner_id: str | None,
    last_updated_ts: str,
) -> dict[str, Any]:
    return {
        "series": {
            "submitted": [],
            "accepted": [],
            "rejected": [],
        },
        "window": window,
        "bucket": bucket,
        "miner_id": miner_id,
        "last_updated_ts": last_updated_ts,
    }


def _normalize_items() -> tuple[list[dict[str, Any]] | None, str | None]:
    raw = miners_route._fetch_translator_miners_envelope()
    if not isinstance(raw, dict):
        return None, None

    source_status = raw.get("status")
    records = miners_route._extract_records(raw.get("data"))
    if source_status not in {"ok", "degraded"} or records is None:
        return None, None

    normalized_items: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        normalized, _partial = miners_route._normalize_record(record)
        if normalized is None:
            continue
        normalized_items.append(normalized)

    return normalized_items, source_status


def _hashrate_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _single_point(ts: str, value: int | float) -> list[dict[str, Any]]:
    return [{"ts": ts, "value": value}]


def _share_counters(
    item: dict[str, Any],
) -> tuple[int | float | None, int | float | None, int | float | None]:
    accepted = _hashrate_number(item.get("accepted_shares"))
    rejected = _hashrate_number(item.get("rejected_shares"))
    submitted = None
    if accepted is not None and rejected is not None:
        submitted = accepted + rejected
    return submitted, accepted, rejected


@router.get("/hashrate")
def metrics_hashrate(
    window: Literal["15m", "1h", "6h", "24h", "7d"] = Query(...),
    bucket: Literal["1m", "5m", "15m", "1h"] = Query(...),
    miner_id: str | None = Query(default=None),
) -> dict[str, Any]:
    last_updated_ts = _status_timestamp()
    data = _empty_hashrate_data(
        window=window,
        bucket=bucket,
        miner_id=miner_id,
        last_updated_ts=last_updated_ts,
    )

    normalized_items, source_status = _normalize_items()
    if normalized_items is None:
        return _status_envelope(
            "error",
            data,
            {"code": "TRANSLATOR_UNAVAILABLE", "message": "Translator hashrate source unavailable"},
        )

    if miner_id is not None:
        miner = next((item for item in normalized_items if item["miner_id"] == miner_id), None)
        if miner is None:
            return _status_envelope(
                "degraded",
                data,
                {"code": "MINER_NOT_FOUND", "message": "Requested miner was not found"},
            )

        hashrate = _hashrate_number(miner.get("hashrate"))
        if hashrate is None:
            return _status_envelope(
                "degraded",
                data,
                {"code": "HASHRATE_UNAVAILABLE", "message": "Current miner hashrate unavailable"},
            )

        data["series"] = [{"ts": last_updated_ts, "hashrate": hashrate}]
        if source_status != "ok":
            return _status_envelope(
                "degraded",
                data,
                {"source_status": source_status},
            )
        return _status_envelope("ok", data, None)

    connected_hashrates = [
        hashrate
        for item in normalized_items
        if item.get("connected") is True
        for hashrate in [_hashrate_number(item.get("hashrate"))]
        if hashrate is not None
    ]
    if not connected_hashrates:
        detail: dict[str, Any] = {
            "code": "HASHRATE_UNAVAILABLE",
            "message": "Current aggregate hashrate unavailable",
        }
        if source_status != "ok":
            detail["source_status"] = source_status
        return _status_envelope("degraded", data, detail)

    data["series"] = [{"ts": last_updated_ts, "hashrate": sum(connected_hashrates)}]
    if source_status != "ok":
        return _status_envelope(
            "degraded",
            data,
            {"source_status": source_status},
        )
    return _status_envelope("ok", data, None)


@router.get("/shares")
def metrics_shares(
    window: Literal["15m", "1h", "6h", "24h", "7d"] = Query(...),
    bucket: Literal["1m", "5m", "15m", "1h"] = Query(...),
    miner_id: str | None = Query(default=None),
) -> dict[str, Any]:
    last_updated_ts = _status_timestamp()
    data = _empty_shares_data(
        window=window,
        bucket=bucket,
        miner_id=miner_id,
        last_updated_ts=last_updated_ts,
    )

    normalized_items, source_status = _normalize_items()
    if normalized_items is None:
        return _status_envelope(
            "error",
            data,
            {"code": "TRANSLATOR_UNAVAILABLE", "message": "Translator share source unavailable"},
        )

    if miner_id is not None:
        miner = next((item for item in normalized_items if item["miner_id"] == miner_id), None)
        if miner is None:
            return _status_envelope(
                "degraded",
                data,
                {"code": "MINER_NOT_FOUND", "message": "Requested miner was not found"},
            )

        submitted, accepted, rejected = _share_counters(miner)
        if accepted is None and rejected is None and submitted is None:
            return _status_envelope(
                "degraded",
                data,
                {
                    "code": "SHARE_COUNTERS_UNAVAILABLE",
                    "message": "Current miner share counters unavailable",
                },
            )

        if submitted is not None:
            data["series"]["submitted"] = _single_point(last_updated_ts, submitted)
        if accepted is not None:
            data["series"]["accepted"] = _single_point(last_updated_ts, accepted)
        if rejected is not None:
            data["series"]["rejected"] = _single_point(last_updated_ts, rejected)

        if source_status != "ok":
            return _status_envelope("degraded", data, {"source_status": source_status})
        return _status_envelope("ok", data, None)

    accepted_total = 0
    rejected_total = 0
    have_accepted = False
    have_rejected = False
    for item in normalized_items:
        if item.get("connected") is not True:
            continue
        accepted = _hashrate_number(item.get("accepted_shares"))
        rejected = _hashrate_number(item.get("rejected_shares"))
        if accepted is not None:
            accepted_total += accepted
            have_accepted = True
        if rejected is not None:
            rejected_total += rejected
            have_rejected = True

    if not have_accepted and not have_rejected:
        detail: dict[str, Any] = {
            "code": "SHARE_COUNTERS_UNAVAILABLE",
            "message": "Current aggregate share counters unavailable",
        }
        if source_status != "ok":
            detail["source_status"] = source_status
        return _status_envelope("degraded", data, detail)

    if have_accepted:
        data["series"]["accepted"] = _single_point(last_updated_ts, accepted_total)
    if have_rejected:
        data["series"]["rejected"] = _single_point(last_updated_ts, rejected_total)
    if have_accepted and have_rejected:
        data["series"]["submitted"] = _single_point(last_updated_ts, accepted_total + rejected_total)

    if source_status != "ok":
        return _status_envelope("degraded", data, {"source_status": source_status})
    return _status_envelope("ok", data, None)
