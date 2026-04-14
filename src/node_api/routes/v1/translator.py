from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from node_api.services import translator_logs as tl
from node_api.services import translator_monitoring as tm
from node_api.settings import Settings, get_settings

router = APIRouter(prefix="/translator", tags=["translator"])

_SUMMARY_DEFAULT_LINES = 500
_SUMMARY_MAX_LINES = 2000

_CLIENT_ID_RE = re.compile(r"^[\w.-]{1,128}$")


class TranslatorLogRecordOut(BaseModel):
    """Normalized translator log line (plain or JSON-derived)."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    level: str
    target: str
    category: str
    message: str
    raw: str


class TranslatorStatusOut(BaseModel):
    """Merged translator health: log tail signals plus live monitoring probe."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "unconfigured"]
    configured: bool
    log_configured: bool
    monitoring_configured: bool
    log_status: Literal["ok", "degraded", "unconfigured"]
    monitoring_status: Literal["ok", "degraded", "unconfigured"]
    last_event_ts: str | None = None
    recent_error_count: int = 0
    upstream_channels: int | None = None
    downstream_clients: int | None = None
    log_path: str | None = None


class TranslatorSummaryOut(BaseModel):
    """Status plus aggregates over the last ``lines`` parsed log records."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "unconfigured"]
    configured: bool
    log_path: str | None = None
    exists: bool = False
    readable: bool = False
    total_records_scanned: int = 0
    counts_by_level: dict[str, int] = Field(default_factory=dict)
    counts_by_category: dict[str, int] = Field(default_factory=dict)
    last_event_ts: str | None = None
    recent_error_count: int = 0


class TranslatorMonitoringResponse(BaseModel):
    """Allowlisted translator monitoring HTTP GET result (normalized envelope)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "unconfigured"]
    configured: bool
    data: dict[str, Any] | list[Any] | None = None
    detail: str | None = None


def _clamp_lines(lines: int, settings: Settings) -> int:
    return max(1, min(lines, settings.translator_log_max_lines))


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, 1000))


def _clamp_summary_lines(lines: int) -> int:
    return max(1, min(lines, _SUMMARY_MAX_LINES))


def _records_to_out(records: list[tl.TranslatorLogRecord]) -> list[TranslatorLogRecordOut]:
    return [TranslatorLogRecordOut.model_validate(r.to_dict()) for r in records]


def _monitoring_envelope(raw: dict[str, Any]) -> TranslatorMonitoringResponse:
    return TranslatorMonitoringResponse.model_validate(raw)


@router.get("/status", response_model=TranslatorStatusOut)
def translator_status(settings: Settings = Depends(get_settings)) -> TranslatorStatusOut:
    return TranslatorStatusOut.model_validate(tm.translator_merged_status_payload(settings))


@router.get("/summary", response_model=TranslatorSummaryOut)
def translator_summary(
    settings: Settings = Depends(get_settings),
    lines: int = Query(default=_SUMMARY_DEFAULT_LINES, ge=1, le=_SUMMARY_MAX_LINES),
) -> TranslatorSummaryOut:
    want = _clamp_summary_lines(lines)
    return TranslatorSummaryOut.model_validate(tl.translator_summary_payload(settings, want))


@router.get("/runtime", response_model=TranslatorMonitoringResponse)
def translator_runtime(settings: Settings = Depends(get_settings)) -> TranslatorMonitoringResponse:
    return _monitoring_envelope(tm.fetch_allowlisted(settings, "/api/v1/health", None))


@router.get("/global", response_model=TranslatorMonitoringResponse)
def translator_global(settings: Settings = Depends(get_settings)) -> TranslatorMonitoringResponse:
    return _monitoring_envelope(tm.fetch_allowlisted(settings, "/api/v1/global", None))


@router.get("/upstream", response_model=TranslatorMonitoringResponse)
def translator_upstream(settings: Settings = Depends(get_settings)) -> TranslatorMonitoringResponse:
    return _monitoring_envelope(tm.fetch_allowlisted(settings, "/api/v1/server", None))


@router.get("/upstream/channels", response_model=TranslatorMonitoringResponse)
def translator_upstream_channels(
    settings: Settings = Depends(get_settings),
) -> TranslatorMonitoringResponse:
    return _monitoring_envelope(tm.fetch_allowlisted(settings, "/api/v1/server/channels", None))


@router.get("/downstreams", response_model=TranslatorMonitoringResponse)
def translator_downstreams(
    settings: Settings = Depends(get_settings),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> TranslatorMonitoringResponse:
    return _monitoring_envelope(
        tm.fetch_allowlisted(settings, "/api/v1/sv1/clients", {"offset": offset, "limit": limit})
    )


@router.get("/downstreams/{client_id}", response_model=TranslatorMonitoringResponse)
def translator_downstream_client(
    client_id: str,
    settings: Settings = Depends(get_settings),
) -> TranslatorMonitoringResponse:
    if not _CLIENT_ID_RE.fullmatch(client_id):
        if not tm.is_monitoring_configured(settings):
            return TranslatorMonitoringResponse(
                status="unconfigured",
                configured=False,
                data=None,
                detail="invalid_client_id",
            )
        return TranslatorMonitoringResponse(
            status="degraded",
            configured=True,
            data=None,
            detail="invalid_client_id",
        )
    path = f"/api/v1/sv1/clients/{client_id}"
    return _monitoring_envelope(tm.fetch_allowlisted(settings, path, None))


@router.get("/logs/tail", response_model=list[TranslatorLogRecordOut])
def translator_logs_tail(
    settings: Settings = Depends(get_settings),
    lines: int | None = Query(default=None, ge=1),
    level: str | None = Query(default=None),
    contains: str | None = Query(default=None),
) -> list[TranslatorLogRecordOut]:
    path = tl.translator_log_path(settings)
    if path is None:
        return []
    exists, readable = tl.path_readable_file(path)
    if not exists or not readable:
        return []

    want_lines = lines if lines is not None else settings.translator_log_default_lines
    want_lines = _clamp_lines(want_lines, settings)
    records = tl.load_tail_records(path, want_lines)
    ordered = tl.newest_first(records)
    filtered = tl.filter_records(ordered, level=level, contains=contains)
    return _records_to_out(filtered)


@router.get("/events/recent", response_model=list[TranslatorLogRecordOut])
def translator_events_recent(
    settings: Settings = Depends(get_settings),
    limit: int = Query(default=100, ge=1),
    category: str | None = Query(default=None),
    level: str | None = Query(default=None),
    contains: str | None = Query(default=None),
) -> list[TranslatorLogRecordOut]:
    path = tl.translator_log_path(settings)
    if path is None:
        return []
    exists, readable = tl.path_readable_file(path)
    if not exists or not readable:
        return []

    lim = _clamp_limit(limit)
    records = tl.load_tail_records(path, settings.translator_log_max_lines)
    ordered = tl.newest_first(records)
    filtered = tl.filter_records(ordered, level=level, contains=contains, category=category)
    return _records_to_out(filtered[:lim])


@router.get("/errors/recent", response_model=list[TranslatorLogRecordOut])
def translator_errors_recent(
    settings: Settings = Depends(get_settings),
    limit: int = Query(default=100, ge=1),
) -> list[TranslatorLogRecordOut]:
    path = tl.translator_log_path(settings)
    if path is None:
        return []
    exists, readable = tl.path_readable_file(path)
    if not exists or not readable:
        return []

    lim = _clamp_limit(limit)
    records = tl.load_tail_records(path, settings.translator_log_max_lines)
    ordered = tl.newest_first(records)
    errs = [r for r in ordered if r.level in ("ERROR", "WARN")]
    return _records_to_out(errs[:lim])
