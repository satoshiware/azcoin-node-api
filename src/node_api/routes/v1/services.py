from __future__ import annotations

from datetime import datetime, timezone
import subprocess
import time
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/services", tags=["services"])

_AZTRANSLATOR_SERVICE = "aztranslator.service"
_AZCOIN_NODE_API_SERVICE = "azcoin-node-api.service"
_SERVICE_NAMES = {
    "aztranslator": _AZTRANSLATOR_SERVICE,
    "azcoin_node_api": _AZCOIN_NODE_API_SERVICE,
}
_KNOWN_SERVICE_STATUSES = {"active", "inactive", "failed"}
_SYSTEMCTL_TIMEOUT_SECONDS = 2.0


class ServiceInspectionError(Exception):
    """Local service inspection backend is unavailable."""


def _status_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_envelope(status: str, data: dict[str, Any], detail: Any) -> dict[str, Any]:
    return {"status": status, "data": data, "detail": detail}


def _empty_service_status(service_name: str, last_updated_ts: str) -> dict[str, Any]:
    return {
        "service_name": service_name,
        "status": "unknown",
        "uptime_secs": None,
        "pid": None,
        "last_updated_ts": last_updated_ts,
    }


def _parse_systemctl_show(output: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key] = value
    return parsed


def _normalize_service_status(active_state: str | None, load_state: str | None) -> str:
    if load_state == "not-found":
        return "unknown"
    if active_state in _KNOWN_SERVICE_STATUSES:
        return active_state
    return "unknown"


def _pid_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        pid = int(value)
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid


def _uptime_secs_or_none(value: str | None, service_status: str) -> int | None:
    if service_status != "active" or value is None:
        return None

    try:
        started_usec = int(value)
    except ValueError:
        return None

    if started_usec <= 0:
        return None

    if hasattr(time, "CLOCK_BOOTTIME"):
        now_seconds = time.clock_gettime(time.CLOCK_BOOTTIME)
    else:
        now_seconds = time.monotonic()

    uptime_seconds = now_seconds - (started_usec / 1_000_000)
    if uptime_seconds < 0:
        return 0
    return int(uptime_seconds)


def _inspect_service(service_name: str, last_updated_ts: str) -> tuple[dict[str, Any], bool]:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service_name,
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=ExecMainPID",
                "--property=ActiveEnterTimestampMonotonic",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceInspectionError from exc

    if result.returncode != 0:
        return _empty_service_status(service_name, last_updated_ts), False

    fields = _parse_systemctl_show(result.stdout)
    status = _normalize_service_status(fields.get("ActiveState"), fields.get("LoadState"))
    payload = {
        "service_name": service_name,
        "status": status,
        "uptime_secs": _uptime_secs_or_none(fields.get("ActiveEnterTimestampMonotonic"), status),
        "pid": _pid_or_none(fields.get("ExecMainPID")),
        "last_updated_ts": last_updated_ts,
    }
    return payload, status != "unknown"


@router.get("/status")
def services_status() -> dict[str, Any]:
    last_updated_ts = _status_timestamp()

    try:
        service_data: dict[str, dict[str, Any]] = {}
        service_usable: dict[str, bool] = {}
        for key, service_name in _SERVICE_NAMES.items():
            payload, usable = _inspect_service(service_name, last_updated_ts)
            service_data[key] = payload
            service_usable[key] = usable
    except ServiceInspectionError:
        data = {
            key: _empty_service_status(service_name, last_updated_ts)
            for key, service_name in _SERVICE_NAMES.items()
        }
        return _status_envelope(
            "error",
            data,
            {
                "code": "SERVICE_INSPECTION_UNAVAILABLE",
                "message": "Local service inspection unavailable",
            },
        )

    usable_count = sum(1 for usable in service_usable.values() if usable)
    unavailable_services = [key for key, usable in service_usable.items() if not usable]

    if usable_count == len(_SERVICE_NAMES):
        status = "ok"
        detail = None
    else:
        status = "degraded"
        detail = {"unavailable_services": unavailable_services} if unavailable_services else None

    return _status_envelope(status, service_data, detail)
