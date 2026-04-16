from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from node_api.routes.v1 import node as node_route
from node_api.routes.v1 import services as services_route
from node_api.services import translator_monitoring as tm
from node_api.settings import get_settings

router = APIRouter(prefix="/alerts", tags=["alerts"])

_RECENT_RESTART_WINDOW_SECS = 900


def _status_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_envelope(status: str, data: dict[str, Any], detail: Any) -> dict[str, Any]:
    return {"status": status, "data": data, "detail": detail}


def _alert_detail_or_none(detail: Any) -> dict[str, Any] | None:
    if detail is None:
        return None
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str):
        value = detail.strip()
        return {"reason": value} if value else None
    return None


def _build_alert(
    *,
    alert_id: str,
    source: str,
    severity: str,
    message: str,
    last_checked_ts: str,
    detail: Any = None,
) -> dict[str, Any]:
    return {
        "id": alert_id,
        "source": source,
        "severity": severity,
        "message": message,
        "active": True,
        "since_ts": None,
        "last_checked_ts": last_checked_ts,
        "detail": _alert_detail_or_none(detail),
    }


def _fetch_node_status_envelope() -> dict[str, Any]:
    return node_route.node_status()


def _fetch_services_status_envelope() -> dict[str, Any]:
    return services_route.services_status()


def _fetch_translator_monitoring_snapshot() -> dict[str, Any]:
    settings = get_settings()
    if not tm.is_monitoring_configured(settings):
        return {
            "monitoring_status": "unconfigured",
            "downstream_clients": None,
            "detail": "unconfigured",
        }
    return tm.probe_monitoring_metrics(settings)


def _add_dependency_failure(failures: list[str], dependency: str) -> None:
    if dependency not in failures:
        failures.append(dependency)


def _safe_fetch(fetcher) -> Any:
    try:
        return fetcher()
    except Exception:
        return None


def _is_node_usable(envelope: dict[str, Any]) -> bool:
    return envelope.get("detail") is None and isinstance(envelope.get("data"), dict)


def _service_restart_alert(
    *,
    alert_id: str,
    service_name: str,
    service_payload: dict[str, Any],
    severity: str,
    message: str,
    last_checked_ts: str,
) -> dict[str, Any] | None:
    uptime_secs = service_payload.get("uptime_secs")
    if not isinstance(uptime_secs, (int, float)) or isinstance(uptime_secs, bool):
        return None
    if uptime_secs >= _RECENT_RESTART_WINDOW_SECS:
        return None
    return _build_alert(
        alert_id=alert_id,
        source="service",
        severity=severity,
        message=message,
        last_checked_ts=last_checked_ts,
        detail={"service_name": service_name, "uptime_secs": uptime_secs},
    )


@router.get("")
def alerts() -> dict[str, Any]:
    last_updated_ts = _status_timestamp()
    items: list[dict[str, Any]] = []
    dependency_failures: list[str] = []
    meaningful_evaluation = False

    translator_snapshot = _safe_fetch(_fetch_translator_monitoring_snapshot)
    if isinstance(translator_snapshot, dict) and "monitoring_status" in translator_snapshot:
        meaningful_evaluation = True
        monitoring_status = translator_snapshot.get("monitoring_status")
        downstream_clients = translator_snapshot.get("downstream_clients")
        if monitoring_status != "ok" or downstream_clients is None:
            _add_dependency_failure(dependency_failures, "translator")
            items.append(
                _build_alert(
                    alert_id="translator_unavailable",
                    source="translator",
                    severity="critical",
                    message="Translator monitoring unavailable",
                    last_checked_ts=last_updated_ts,
                    detail=translator_snapshot.get("detail"),
                )
            )
        elif downstream_clients == 0:
            items.append(
                _build_alert(
                    alert_id="no_downstream_miners",
                    source="translator",
                    severity="warning",
                    message="No downstream miners connected to translator",
                    last_checked_ts=last_updated_ts,
                    detail={"downstream_clients": 0},
                )
            )

    node_envelope = _safe_fetch(_fetch_node_status_envelope)
    if isinstance(node_envelope, dict) and _is_node_usable(node_envelope):
        meaningful_evaluation = True
        node_data = node_envelope["data"]
        if node_data.get("synced") is False or node_data.get("initial_block_download") is True:
            items.append(
                _build_alert(
                    alert_id="node_not_synced",
                    source="node",
                    severity="warning",
                    message="AZCoin node is not fully synced",
                    last_checked_ts=last_updated_ts,
                    detail={
                        "synced": node_data.get("synced"),
                        "initial_block_download": node_data.get("initial_block_download"),
                    },
                )
            )
    else:
        _add_dependency_failure(dependency_failures, "node")

    services_envelope = _safe_fetch(_fetch_services_status_envelope)
    if isinstance(services_envelope, dict) and isinstance(services_envelope.get("data"), dict):
        service_data = services_envelope["data"]
        services_status = services_envelope.get("status")
        if services_status in {"ok", "degraded"}:
            meaningful_evaluation = True
            if services_status != "ok":
                _add_dependency_failure(dependency_failures, "service")

            translator_service = service_data.get("aztranslator")
            if isinstance(translator_service, dict):
                alert = _service_restart_alert(
                    alert_id="recent_service_restart_aztranslator",
                    service_name="aztranslator.service",
                    service_payload=translator_service,
                    severity="warning",
                    message="aztranslator.service restarted recently",
                    last_checked_ts=last_updated_ts,
                )
                if alert is not None:
                    items.append(alert)

            api_service = service_data.get("azcoin_node_api")
            if isinstance(api_service, dict):
                alert = _service_restart_alert(
                    alert_id="recent_service_restart_azcoin_node_api",
                    service_name="azcoin-node-api.service",
                    service_payload=api_service,
                    severity="info",
                    message="azcoin-node-api.service restarted recently",
                    last_checked_ts=last_updated_ts,
                )
                if alert is not None:
                    items.append(alert)
        else:
            _add_dependency_failure(dependency_failures, "service")
    else:
        _add_dependency_failure(dependency_failures, "service")

    data = {
        "items": items,
        "count": len(items),
        "last_updated_ts": last_updated_ts,
    }

    if not dependency_failures:
        return _status_envelope("ok", data, None)
    if meaningful_evaluation:
        return _status_envelope(
            "degraded",
            data,
            {"unavailable_dependencies": dependency_failures},
        )
    return _status_envelope(
        "error",
        data,
        {
            "code": "ALERT_EVALUATION_UNAVAILABLE",
            "message": "Alert evaluation unavailable",
        },
    )
