from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from node_api.settings import Settings

# Read at most this many bytes from the end when tailing (avoids loading huge files).
_MAX_TAIL_BYTES = 2 * 1024 * 1024

_PLAIN_HEAD_RE = re.compile(r"^(\S+)\s+(\S+)\s+")


@dataclass(frozen=True)
class TranslatorLogRecord:
    ts: str
    level: str
    target: str
    category: str
    message: str
    raw: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ts": self.ts,
            "level": self.level,
            "target": self.target,
            "category": self.category,
            "message": self.message,
            "raw": self.raw,
        }


def _normalize_level(value: str | None) -> str:
    if not value:
        return "INFO"
    s = str(value).strip().upper()
    if s == "WARNING":
        return "WARN"
    return s


def _combined_lower(target: str, message: str) -> str:
    return f"{target} {message}".lower()


def _has_disconnect_signal(text: str) -> bool:
    return any(
        p in text
        for p in (
            "disconnect",
            "disconnected",
            "connection closed",
            "closed connection",
            "lost connection",
            "peer closed",
        )
    )


def _has_connect_signal(text: str) -> bool:
    return any(
        p in text
        for p in (
            "connect",
            "connected",
            "connection established",
            "established connection",
            "accepted connection",
            "incoming connection",
        )
    )


def _upstream_context(target: str, message: str) -> bool:
    t, m = target.lower(), message.lower()
    return "upstream" in t or "upstream" in m or "upstream" in _combined_lower(target, message)


def _downstream_context(target: str, message: str) -> bool:
    t, m = target.lower(), message.lower()
    c = _combined_lower(target, message)
    return "downstream" in t or "downstream" in m or "downstream" in c


def _category_shutdown(target: str, message: str) -> bool:
    c = _combined_lower(target, message)
    return any(
        p in c
        for p in (
            "shutdown",
            "shutting down",
            "graceful shutdown",
            "exiting",
            "stopped server",
            "server stopped",
        )
    )


def _category_startup(target: str, message: str) -> bool:
    c = _combined_lower(target, message)
    if _category_shutdown(target, message):
        return False
    return any(
        p in c
        for p in (
            "startup",
            "starting up",
            "server starting",
            "initialized",
            "listening on",
            "listening at",
            "ready to accept",
            "spawned",
        )
    )


def _category_upstream_disconnect(target: str, message: str) -> bool:
    if not _upstream_context(target, message):
        return False
    c = _combined_lower(target, message)
    return _has_disconnect_signal(c)


def _category_upstream_connect(target: str, message: str) -> bool:
    if not _upstream_context(target, message):
        return False
    c = _combined_lower(target, message)
    if _has_disconnect_signal(c):
        return False
    return _has_connect_signal(c)


def _category_downstream_disconnect(target: str, message: str) -> bool:
    if not _downstream_context(target, message):
        return False
    c = _combined_lower(target, message)
    return _has_disconnect_signal(c)


def _category_downstream_connect(target: str, message: str) -> bool:
    if not _downstream_context(target, message):
        return False
    c = _combined_lower(target, message)
    if _has_disconnect_signal(c):
        return False
    return _has_connect_signal(c)


def _category_authorize(target: str, message: str) -> bool:
    c = _combined_lower(target, message)
    return any(
        p in c
        for p in (
            "authoriz",
            "mining.authorize",
            "authorize ",
            "authorized ",
            "authorization",
        )
    )


def _category_submit(target: str, message: str) -> bool:
    c = _combined_lower(target, message)
    return any(
        p in c
        for p in (
            "mining.submit",
            "submit share",
            "share submitted",
            "submitted share",
            "submitting",
        )
    )


def _category_difficulty(target: str, message: str) -> bool:
    c = _combined_lower(target, message)
    return any(
        p in c
        for p in (
            "difficulty",
            "set_difficulty",
            "mining.set_difficulty",
            "difficulty update",
            "retarget",
        )
    )


def _category_job(target: str, message: str) -> bool:
    c = _combined_lower(target, message)
    return any(
        p in c
        for p in (
            "mining.notify",
            "new job",
            "new work",
            "job_id",
            "block template",
            "getblocktemplate",
            "clean_jobs",
            "notify::",
        )
    )


def _derive_category(level: str, target: str, message: str) -> str:
    lvl = level.upper()
    if _category_shutdown(target, message):
        return "shutdown"
    if _category_startup(target, message):
        return "startup"
    if _category_upstream_disconnect(target, message):
        return "upstream.disconnect"
    if _category_upstream_connect(target, message):
        return "upstream.connect"
    if _category_downstream_disconnect(target, message):
        return "downstream.disconnect"
    if _category_downstream_connect(target, message):
        return "downstream.connect"
    if _category_authorize(target, message):
        return "authorize"
    if _category_submit(target, message):
        return "submit"
    if _category_difficulty(target, message):
        return "difficulty.update"
    if _category_job(target, message):
        return "job"
    if lvl == "ERROR" or " error" in f" {_combined_lower(target, message)}":
        return "error"
    if lvl == "WARN":
        return "warn"
    return "log"


def _coerce_ts(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s


def _parse_json_line(raw: str) -> TranslatorLogRecord | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data:
        return None

    ts = (
        _coerce_ts(data.get("ts"))
        or _coerce_ts(data.get("timestamp"))
        or _coerce_ts(data.get("@timestamp"))
        or _coerce_ts(data.get("time"))
    )
    level = _normalize_level(
        data.get("level")
        or data.get("@level")
        or data.get("severity")
        or data.get("lvl")
    )
    target = str(data.get("target") or data.get("logger") or data.get("module") or "").strip()
    message = str(
        data.get("message")
        or data.get("msg")
        or data.get("@message")
        or data.get("text")
        or ""
    ).strip()

    if not ts:
        ts = ""

    category = _derive_category(level, target, message)
    return TranslatorLogRecord(
        ts=ts or "",
        level=level,
        target=target,
        category=category,
        message=message,
        raw=raw,
    )


def _parse_plain_line(line: str) -> TranslatorLogRecord | None:
    stripped = line.strip()
    if not stripped:
        return None
    m = _PLAIN_HEAD_RE.match(stripped)
    if not m:
        return None
    ts = m.group(1).strip()
    level = _normalize_level(m.group(2))
    rest = stripped[m.end() :]
    if ": " not in rest:
        return None
    target, message = rest.rsplit(": ", 1)
    target = target.strip()
    message = message.strip()
    category = _derive_category(level, target, message)
    return TranslatorLogRecord(
        ts=ts,
        level=level,
        target=target,
        category=category,
        message=message,
        raw=line,
    )


def parse_log_line(raw: str) -> TranslatorLogRecord | None:
    try:
        line = raw.rstrip("\r\n")
        if not line.strip():
            return None
        parsed = _parse_json_line(line)
        if parsed is not None:
            return parsed
        return _parse_plain_line(line)
    except Exception:
        return None


def read_tail_lines(path: Path, max_lines: int) -> list[str]:
    """Return up to max_lines complete lines from the end of the file (file order, oldest first)."""
    if max_lines < 1:
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []

    read_from = 0
    if size > _MAX_TAIL_BYTES:
        read_from = size - _MAX_TAIL_BYTES

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            if read_from > 0:
                f.seek(read_from)
                f.readline()  # drop potential partial first line
            text = f.read()
    except OSError:
        return []

    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def translator_log_path(settings: Settings) -> Path | None:
    p = settings.translator_log_path
    if not p:
        return None
    return Path(p).expanduser()


def path_readable_file(path: Path) -> tuple[bool, bool]:
    """Return (exists, readable). exists implies path.exists() and is_file()."""
    try:
        if not path.exists():
            return False, False
        if not path.is_file():
            return False, False
        if not os.access(path, os.R_OK):
            return True, False
        with path.open("rb") as f:
            f.read(1)
        return True, True
    except OSError:
        return path.exists() and path.is_file(), False


def load_tail_records(path: Path, line_count: int) -> list[TranslatorLogRecord]:
    lines = read_tail_lines(path, line_count)
    out: list[TranslatorLogRecord] = []
    for line in lines:
        try:
            rec = parse_log_line(line)
        except Exception:
            continue
        if rec is not None:
            out.append(rec)
    return out


def newest_first(records: Iterable[TranslatorLogRecord]) -> list[TranslatorLogRecord]:
    return list(reversed(list(records)))


def filter_records(
    records: list[TranslatorLogRecord],
    *,
    level: str | None = None,
    contains: str | None = None,
    category: str | None = None,
) -> list[TranslatorLogRecord]:
    out = records
    if level:
        want = level.strip().upper()
        out = [r for r in out if r.level.upper() == want]
    if contains:
        needle = contains.lower()
        out = [
            r
            for r in out
            if needle in r.message.lower()
            or needle in r.raw.lower()
            or needle in r.target.lower()
        ]
    if category:
        want_cat = category.strip()
        out = [r for r in out if r.category == want_cat]
    return out


def translator_log_panel(settings: Settings) -> dict[str, Any]:
    """Log file side-panel for merged status (and legacy log-only status mapping)."""
    path = translator_log_path(settings)
    if path is None:
        return {
            "log_configured": False,
            "log_status": "unconfigured",
            "log_path": None,
            "exists": False,
            "readable": False,
            "last_event_ts": None,
            "recent_error_count": 0,
        }

    resolved = str(path.resolve())
    exists, readable = path_readable_file(path)

    if not exists or not readable:
        return {
            "log_configured": True,
            "log_status": "degraded",
            "log_path": resolved,
            "exists": exists,
            "readable": readable,
            "last_event_ts": None,
            "recent_error_count": 0,
        }

    try:
        if path.stat().st_size == 0:
            return {
                "log_configured": True,
                "log_status": "degraded",
                "log_path": resolved,
                "exists": True,
                "readable": True,
                "last_event_ts": None,
                "recent_error_count": 0,
            }
    except OSError:
        return {
            "log_configured": True,
            "log_status": "degraded",
            "log_path": resolved,
            "exists": True,
            "readable": False,
            "last_event_ts": None,
            "recent_error_count": 0,
        }

    window = settings.translator_log_max_lines
    records = load_tail_records(path, window)
    last_ts: str | None = None
    if records:
        last_ts = records[-1].ts or None
    err_count = sum(1 for r in records if r.level in ("ERROR", "WARN"))

    return {
        "log_configured": True,
        "log_status": "ok",
        "log_path": resolved,
        "exists": True,
        "readable": True,
        "last_event_ts": last_ts,
        "recent_error_count": err_count,
    }


def translator_status_payload(settings: Settings) -> dict[str, Any]:
    """Legacy log-only status shape (used by log summary aggregation)."""
    lp = translator_log_panel(settings)
    return {
        "status": lp["log_status"],
        "configured": lp["log_configured"],
        "log_path": lp["log_path"],
        "exists": lp["exists"],
        "readable": lp["readable"],
        "last_event_ts": lp["last_event_ts"],
        "recent_error_count": lp["recent_error_count"],
    }


def translator_summary_payload(settings: Settings, scan_lines: int) -> dict[str, Any]:
    """Aggregate tail statistics from at most ``scan_lines`` log lines (clamped 1..2000)."""
    scan_lines = max(1, min(int(scan_lines), 2000))
    st = translator_status_payload(settings)
    base: dict[str, Any] = {
        "status": st["status"],
        "configured": st["configured"],
        "log_path": st["log_path"],
        "exists": st["exists"],
        "readable": st["readable"],
        "total_records_scanned": 0,
        "counts_by_level": {},
        "counts_by_category": {},
        "last_event_ts": st.get("last_event_ts"),
        "recent_error_count": 0,
    }
    if st["status"] != "ok":
        return base

    path = translator_log_path(settings)
    if path is None:
        return base

    records = load_tail_records(path, scan_lines)
    base["total_records_scanned"] = len(records)
    base["counts_by_level"] = dict(Counter(r.level for r in records))
    base["counts_by_category"] = dict(Counter(r.category for r in records))
    if records:
        base["last_event_ts"] = records[-1].ts or None
    else:
        base["last_event_ts"] = None
    base["recent_error_count"] = sum(1 for r in records if r.level in ("ERROR", "WARN"))
    return base
