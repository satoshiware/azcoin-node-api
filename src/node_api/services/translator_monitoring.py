from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from node_api.services import translator_logs as tl
from node_api.settings import Settings

_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/health",
        "/api/v1/global",
        "/api/v1/server",
        "/api/v1/server/channels",
        "/api/v1/sv1/clients",
    }
)
_SV1_CLIENT_PATH = re.compile(r"^/api/v1/sv1/clients/[\w.-]{1,128}$")


def _monitoring_allowed_path(path: str) -> bool:
    if path in _EXACT_PATHS:
        return True
    return bool(_SV1_CLIENT_PATH.fullmatch(path))


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    t = httpx.Timeout(timeout)
    with httpx.Client(timeout=t, follow_redirects=False) as client:
        r = client.get(url)
        return r.status_code, r.content


def _normalize_base_url(base: str | None) -> str | None:
    if not base or not str(base).strip():
        return None
    b = str(base).strip().rstrip("/")
    if not (b.startswith("http://") or b.startswith("https://")):
        return None
    return b


def is_monitoring_configured(settings: Settings) -> bool:
    return _normalize_base_url(settings.translator_monitoring_base_url) is not None


def _build_url(base: str, path: str, query: dict[str, int]) -> str:
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base.rstrip('/')}{path}"
    allowed_q = {k: query[k] for k in ("offset", "limit") if k in query}
    if allowed_q:
        url = f"{url}?{urlencode(allowed_q)}"
    return url


def _parse_json_body(status_code: int, body: bytes) -> Any | None:
    if status_code != 200 or not body:
        return None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeError):
        return None


def fetch_allowlisted(
    settings: Settings,
    path: str,
    query: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Read-only GET to an allowlisted translator monitoring path."""
    base = _normalize_base_url(settings.translator_monitoring_base_url)
    if base is None:
        return {
            "status": "unconfigured",
            "configured": False,
            "data": None,
            "detail": None,
        }
    if not _monitoring_allowed_path(path):
        return {
            "status": "degraded",
            "configured": True,
            "data": None,
            "detail": "path_not_allowlisted",
        }
    q = query or {}
    for k in q:
        if k not in ("offset", "limit"):
            return {
                "status": "degraded",
                "configured": True,
                "data": None,
                "detail": "invalid_query_param",
            }
    url = _build_url(base, path, q)
    timeout = float(settings.translator_monitoring_timeout_secs)
    try:
        code, raw = _http_get(url, timeout)
    except (httpx.TimeoutException, httpx.RequestError) as e:
        return {
            "status": "degraded",
            "configured": True,
            "data": None,
            "detail": type(e).__name__,
        }
    except OSError as e:
        return {
            "status": "degraded",
            "configured": True,
            "data": None,
            "detail": type(e).__name__,
        }

    data = _parse_json_body(code, raw)
    if code != 200 or data is None:
        return {
            "status": "degraded",
            "configured": True,
            "data": None,
            "detail": f"http_{code}",
        }
    return {"status": "ok", "configured": True, "data": data, "detail": None}


def _extract_channel_count(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("channels", "items", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return len(v)
    return None


def _extract_client_count(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("clients", "connections", "items", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return len(v)
    return None


def probe_monitoring_metrics(settings: Settings) -> dict[str, Any]:
    """Light probe for merged status: health + optional channel/client counts."""
    base = _normalize_base_url(settings.translator_monitoring_base_url)
    if base is None:
        return {
            "monitoring_status": "unconfigured",
            "upstream_channels": None,
            "downstream_clients": None,
            "detail": None,
        }
    health = fetch_allowlisted(settings, "/api/v1/health", None)
    if health["status"] != "ok":
        return {
            "monitoring_status": "degraded",
            "upstream_channels": None,
            "downstream_clients": None,
            "detail": health.get("detail"),
        }
    ch = fetch_allowlisted(settings, "/api/v1/server/channels", None)
    cl = fetch_allowlisted(settings, "/api/v1/sv1/clients", None)
    channels_n = _extract_channel_count(ch.get("data")) if ch["status"] == "ok" else None
    clients_n = _extract_client_count(cl.get("data")) if cl["status"] == "ok" else None
    partial = ch["status"] != "ok" or cl["status"] != "ok"
    return {
        "monitoring_status": "degraded" if partial else "ok",
        "upstream_channels": channels_n,
        "downstream_clients": clients_n,
        "detail": None if not partial else "partial_fetch",
    }


def _merged_overall_status(
    log: dict[str, Any],
    mon: dict[str, Any],
    *,
    log_configured: bool,
    monitoring_configured: bool,
) -> Literal["ok", "degraded", "unconfigured"]:
    if not log_configured and not monitoring_configured:
        return "unconfigured"
    log_bad = log_configured and log["log_status"] == "degraded"
    mon_bad = monitoring_configured and mon["monitoring_status"] == "degraded"
    if log_bad or mon_bad:
        return "degraded"
    log_ok_enough = (not log_configured) or (log["log_status"] == "ok")
    mon_ok_enough = (not monitoring_configured) or (mon["monitoring_status"] == "ok")
    if log_ok_enough and mon_ok_enough:
        return "ok"
    return "degraded"


def translator_merged_status_payload(settings: Settings) -> dict[str, Any]:
    log = tl.translator_log_panel(settings)
    log_c = bool(log["log_configured"])
    mon_c = is_monitoring_configured(settings)
    mon = probe_monitoring_metrics(settings) if mon_c else {
        "monitoring_status": "unconfigured",
        "upstream_channels": None,
        "downstream_clients": None,
        "detail": None,
    }
    configured_any = log_c or mon_c
    overall = _merged_overall_status(
        log,
        mon,
        log_configured=log_c,
        monitoring_configured=mon_c,
    )
    return {
        "status": overall,
        "configured": configured_any,
        "log_configured": log_c,
        "monitoring_configured": mon_c,
        "log_status": log["log_status"],
        "monitoring_status": mon["monitoring_status"],
        "last_event_ts": log["last_event_ts"],
        "recent_error_count": log["recent_error_count"],
        "upstream_channels": mon.get("upstream_channels"),
        "downstream_clients": mon.get("downstream_clients"),
        "log_path": log["log_path"],
    }

