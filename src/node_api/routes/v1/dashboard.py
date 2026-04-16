from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from fastapi import APIRouter

from node_api.routes.v1 import alerts as alerts_route
from node_api.routes.v1 import miners as miners_route
from node_api.routes.v1 import node as node_route
from node_api.routes.v1 import services as services_route
from node_api.services import translator_monitoring as tm
from node_api.settings import get_settings

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_API_START_MONOTONIC = time.monotonic()


def _status_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_envelope(status: str, data: dict[str, Any], detail: Any) -> dict[str, Any]:
    return {"status": status, "data": data, "detail": detail}


def _api_uptime_secs() -> int | None:
    uptime = time.monotonic() - _API_START_MONOTONIC
    if uptime < 0:
        return 0
    return int(uptime)


def _empty_dashboard_data(last_updated_ts: str) -> dict[str, Any]:
    return {
        "api": {
            "healthy": True,
            "uptime_secs": _api_uptime_secs(),
        },
        "translator": {
            "reachable": None,
            "monitoring_status": None,
            "downstream_client_count": None,
            "upstream_channel_count": None,
            "total_hashrate": None,
            "last_updated_ts": None,
        },
        "shares": {
            "submitted": None,
            "acknowledged": None,
            "rejected": None,
            "best_diff": None,
        },
        "node": {
            "synced": None,
            "blocks": None,
            "headers": None,
            "peer_count": None,
            "verification_progress": None,
        },
        "services": {
            "aztranslator": {
                "status": "unknown",
                "uptime_secs": None,
                "pid": None,
            },
            "azcoin_node_api": {
                "status": "unknown",
                "uptime_secs": None,
                "pid": None,
            },
        },
        "alerts": {
            "active_count": 0,
            "items": [],
        },
        "last_updated_ts": last_updated_ts,
    }


def _safe_fetch(fetcher) -> Any:
    try:
        return fetcher()
    except Exception:
        return None


def _fetch_node_status_envelope() -> dict[str, Any]:
    return node_route.node_status()


def _fetch_services_status_envelope() -> dict[str, Any]:
    return services_route.services_status()


def _fetch_alerts_envelope() -> dict[str, Any]:
    return alerts_route.alerts()


def _fetch_translator_monitoring_snapshot() -> dict[str, Any] | None:
    settings = get_settings()
    if not tm.is_monitoring_configured(settings):
        return None
    return tm.probe_monitoring_metrics(settings)


def _fetch_translator_miners_envelope() -> dict[str, Any]:
    return miners_route._fetch_translator_miners_envelope()


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _empty_translator_aggregate_data() -> dict[str, Any]:
    return {
        "total_hashrate": None,
        "shares": {
            "submitted": None,
            "acknowledged": None,
            "rejected": None,
            "best_diff": None,
        },
    }


def _translate_translator_aggregates() -> tuple[dict[str, Any], bool, bool]:
    raw = _fetch_translator_miners_envelope()
    if not isinstance(raw, dict):
        return _empty_translator_aggregate_data(), False, False

    source_status = raw.get("status")
    records = miners_route._extract_records(raw.get("data"))
    if source_status not in {"ok", "degraded"} or records is None:
        return _empty_translator_aggregate_data(), False, False

    normalized_items: list[dict[str, Any]] = []
    partial_records = 0
    for record in records:
        if not isinstance(record, dict):
            partial_records += 1
            continue
        normalized, _partial = miners_route._normalize_record(record)
        if normalized is None:
            partial_records += 1
            continue
        normalized_items.append(normalized)

    connected_items = [item for item in normalized_items if item.get("connected") is not False]
    connected_hashrates = [
        hashrate
        for item in connected_items
        for hashrate in [_number_or_none(item.get("hashrate"))]
        if hashrate is not None
    ]
    best_diffs = [
        best_diff
        for item in connected_items
        for best_diff in [_number_or_none(item.get("best_diff"))]
        if best_diff is not None
    ]

    acknowledged_total = 0
    rejected_total = 0
    have_acknowledged = False
    have_rejected = False
    for item in connected_items:
        acknowledged = _number_or_none(item.get("accepted_shares"))
        rejected = _number_or_none(item.get("rejected_shares"))
        if acknowledged is not None:
            acknowledged_total += acknowledged
            have_acknowledged = True
        if rejected is not None:
            rejected_total += rejected
            have_rejected = True

    shares = {
        "submitted": (
            acknowledged_total + rejected_total if have_acknowledged and have_rejected else None
        ),
        "acknowledged": acknowledged_total if have_acknowledged else None,
        "rejected": rejected_total if have_rejected else None,
        "best_diff": max(best_diffs) if best_diffs else None,
    }
    data = {
        "total_hashrate": sum(connected_hashrates) if connected_hashrates else None,
        "shares": shares,
    }
    return data, True, source_status != "ok" or partial_records > 0


def _translate_translator_status(
    snapshot: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool, bool]:
    if snapshot is None:
        return {
            "reachable": None,
            "monitoring_status": None,
            "downstream_client_count": None,
            "upstream_channel_count": None,
            "total_hashrate": None,
            "last_updated_ts": None,
        }, False, False

    monitoring_status = snapshot.get("monitoring_status")
    detail = snapshot.get("detail")
    downstream_clients = snapshot.get("downstream_clients")
    upstream_channels = snapshot.get("upstream_channels")

    if monitoring_status == "ok":
        status = "ok"
        reachable = True
        usable = True
        degraded = False
        last_updated_ts = _status_timestamp()
    elif monitoring_status == "degraded" and detail == "partial_fetch":
        status = "degraded"
        reachable = True
        usable = True
        degraded = True
        last_updated_ts = _status_timestamp()
    elif monitoring_status == "degraded":
        status = "error"
        reachable = False
        usable = False
        degraded = True
        downstream_clients = None
        upstream_channels = None
        last_updated_ts = None
    else:
        status = None
        reachable = None
        usable = False
        degraded = False
        downstream_clients = None
        upstream_channels = None
        last_updated_ts = None

    return {
        "reachable": reachable,
        "monitoring_status": status,
        "downstream_client_count": (
            downstream_clients if isinstance(downstream_clients, int) else None
        ),
        "upstream_channel_count": upstream_channels if isinstance(upstream_channels, int) else None,
        "total_hashrate": None,
        "last_updated_ts": last_updated_ts,
    }, usable, degraded


@router.get("/summary")
def dashboard_summary() -> dict[str, Any]:
    last_updated_ts = _status_timestamp()
    data = _empty_dashboard_data(last_updated_ts)
    dependency_failures: list[str] = []
    meaningful_usable = 0

    translator_snapshot = _safe_fetch(_fetch_translator_monitoring_snapshot)
    translator_data, translator_usable, translator_degraded = _translate_translator_status(
        translator_snapshot
        if isinstance(translator_snapshot, dict) or translator_snapshot is None
        else None
    )
    translator_aggregate_data = _empty_translator_aggregate_data()
    translator_aggregate_usable = False
    translator_aggregate_degraded = False
    translator_aggregate_result = _safe_fetch(_translate_translator_aggregates)
    if (
        isinstance(translator_aggregate_result, tuple)
        and len(translator_aggregate_result) == 3
        and isinstance(translator_aggregate_result[0], dict)
        and isinstance(translator_aggregate_result[1], bool)
        and isinstance(translator_aggregate_result[2], bool)
    ):
        translator_aggregate_data = translator_aggregate_result[0]
        translator_aggregate_usable = translator_aggregate_result[1]
        translator_aggregate_degraded = translator_aggregate_result[2]

    translator_data["total_hashrate"] = translator_aggregate_data.get("total_hashrate")
    data["shares"] = translator_aggregate_data.get("shares", data["shares"])
    data["translator"] = translator_data
    if translator_usable or translator_aggregate_usable:
        meaningful_usable += 1
    if not translator_usable or not translator_aggregate_usable:
        dependency_failures.append("translator")
    if (
        translator_degraded or translator_aggregate_degraded
    ) and "translator" not in dependency_failures:
        dependency_failures.append("translator")

    node_envelope = _safe_fetch(_fetch_node_status_envelope)
    if (
        isinstance(node_envelope, dict)
        and node_envelope.get("detail") is None
        and isinstance(node_envelope.get("data"), dict)
    ):
        node_data = node_envelope["data"]
        data["node"] = {
            "synced": node_data.get("synced"),
            "blocks": node_data.get("blocks"),
            "headers": node_data.get("headers"),
            "peer_count": node_data.get("peer_count"),
            "verification_progress": node_data.get("verification_progress"),
        }
        meaningful_usable += 1
    else:
        dependency_failures.append("node")

    services_envelope = _safe_fetch(_fetch_services_status_envelope)
    if isinstance(services_envelope, dict) and isinstance(services_envelope.get("data"), dict):
        services_data = services_envelope["data"]
        data["services"] = {
            "aztranslator": {
                "status": services_data.get("aztranslator", {}).get("status", "unknown"),
                "uptime_secs": services_data.get("aztranslator", {}).get("uptime_secs"),
                "pid": services_data.get("aztranslator", {}).get("pid"),
            },
            "azcoin_node_api": {
                "status": services_data.get("azcoin_node_api", {}).get("status", "unknown"),
                "uptime_secs": services_data.get("azcoin_node_api", {}).get("uptime_secs"),
                "pid": services_data.get("azcoin_node_api", {}).get("pid"),
            },
        }
        meaningful_usable += 1
        if services_envelope.get("status") != "ok":
            dependency_failures.append("services")
    else:
        dependency_failures.append("services")

    alerts_envelope = _safe_fetch(_fetch_alerts_envelope)
    if isinstance(alerts_envelope, dict) and isinstance(alerts_envelope.get("data"), dict):
        alerts_data = alerts_envelope["data"]
        items = alerts_data.get("items")
        if not isinstance(items, list):
            items = []
        count = alerts_data.get("count")
        data["alerts"] = {
            "active_count": count if isinstance(count, int) else len(items),
            "items": items,
        }
        if alerts_envelope.get("status") != "error":
            meaningful_usable += 1
        else:
            dependency_failures.append("alerts")
    else:
        dependency_failures.append("alerts")

    if not dependency_failures:
        return _status_envelope("ok", data, None)
    if meaningful_usable > 0:
        return _status_envelope(
            "degraded",
            data,
            {"unavailable_dependencies": dependency_failures},
        )
    return _status_envelope(
        "error",
        data,
        {
            "code": "DASHBOARD_COMPOSITION_UNAVAILABLE",
            "message": "Dashboard composition unavailable",
        },
    )
