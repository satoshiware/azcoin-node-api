from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from node_api.settings import get_settings


def _client(monkeypatch, **env: str) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("TRANSLATOR_LOG_PATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    from node_api import main as main_module

    return TestClient(main_module.create_app())


def test_translator_status_unconfigured(monkeypatch) -> None:
    client = _client(monkeypatch)
    r = client.get("/v1/translator/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unconfigured"
    assert body["configured"] is False
    assert body["log_configured"] is False
    assert body["monitoring_configured"] is False
    assert body["log_path"] is None


def test_translator_status_degraded_missing_file(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing.log"
    client = _client(monkeypatch, TRANSLATOR_LOG_PATH=str(missing))
    r = client.get("/v1/translator/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["configured"] is True
    assert body["log_configured"] is True
    assert body["log_status"] == "degraded"
    assert body["monitoring_configured"] is False
    assert body["monitoring_status"] == "unconfigured"


def test_translator_tail_parses_plain_line(monkeypatch, tmp_path: Path) -> None:
    logf = tmp_path / "t.log"
    line = "2026-04-10T21:02:48.715038Z INFO translator_sv2::module: Downstream connected"
    logf.write_text(line + "\n", encoding="utf-8")
    client = _client(monkeypatch, TRANSLATOR_LOG_PATH=str(logf))
    r = client.get(
        "/v1/translator/logs/tail",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == "2026-04-10T21:02:48.715038Z"
    assert row["level"] == "INFO"
    assert row["target"] == "translator_sv2::module"
    assert row["message"] == "Downstream connected"
    assert row["category"] == "downstream.connect"
    assert row["raw"] == line


def test_translator_tail_filters_level(monkeypatch, tmp_path: Path) -> None:
    logf = tmp_path / "t.log"
    logf.write_text(
        "2026-04-10T21:02:48Z INFO a::m: hi\n"
        "2026-04-10T21:02:49Z WARN a::m: caution\n",
        encoding="utf-8",
    )
    client = _client(monkeypatch, TRANSLATOR_LOG_PATH=str(logf))
    r = client.get(
        "/v1/translator/logs/tail",
        params={"level": "INFO"},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["level"] == "INFO"


def test_translator_errors_recent_only_warn_and_error(monkeypatch, tmp_path: Path) -> None:
    logf = tmp_path / "t.log"
    logf.write_text(
        "2026-04-10T21:02:48Z INFO a::m: ok\n"
        "2026-04-10T21:02:49Z WARN a::m: w\n"
        "2026-04-10T21:02:50Z ERROR a::m: e\n",
        encoding="utf-8",
    )
    client = _client(monkeypatch, TRANSLATOR_LOG_PATH=str(logf))
    r = client.get(
        "/v1/translator/errors/recent",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {x["level"] for x in rows} == {"WARN", "ERROR"}
    assert rows[0]["level"] == "ERROR"


def test_mining_share_routes_removed(monkeypatch) -> None:
    client = _client(monkeypatch)
    h = {"Authorization": "Bearer testtoken"}
    assert client.post("/v1/mining/share", json={}, headers=h).status_code == 404
    assert client.get("/v1/mining/workers", headers=h).status_code == 404


def test_category_submit_and_authorize_from_plain_lines() -> None:
    from node_api.services.translator_logs import parse_log_line

    r1 = parse_log_line("2026-01-01T00:00:00Z INFO t::stratum: mining.submit accepted")
    assert r1 is not None
    assert r1.category == "submit"

    r2 = parse_log_line("2026-01-01T00:00:01Z INFO t::auth: authorize worker foo")
    assert r2 is not None
    assert r2.category == "authorize"


def test_category_upstream_disconnect_from_json_line() -> None:
    from node_api.services.translator_logs import parse_log_line

    raw = (
        '{"ts":"2026-04-10T21:05:00Z","level":"INFO",'
        '"target":"translator_sv2::upstream","message":"Upstream disconnected"}'
    )
    r = parse_log_line(raw)
    assert r is not None
    assert r.category == "upstream.disconnect"


def test_translator_summary_counts(monkeypatch, tmp_path: Path) -> None:
    logf = tmp_path / "s.log"
    logf.write_text(
        "2026-04-10T21:00:00Z INFO a::m: ok\n"
        "2026-04-10T21:00:01Z WARN a::m: w\n"
        "2026-04-10T21:00:02Z ERROR a::m: e\n",
        encoding="utf-8",
    )
    client = _client(monkeypatch, TRANSLATOR_LOG_PATH=str(logf))
    r = client.get(
        "/v1/translator/summary",
        params={"lines": 100},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["total_records_scanned"] == 3
    assert body["counts_by_level"] == {"INFO": 1, "WARN": 1, "ERROR": 1}
    assert body["counts_by_category"]["warn"] == 1
    assert body["counts_by_category"]["error"] == 1
    assert body["recent_error_count"] == 2
    assert body["last_event_ts"] == "2026-04-10T21:00:02Z"


def test_translator_monitoring_global_unconfigured(monkeypatch) -> None:
    client = _client(monkeypatch)
    r = client.get("/v1/translator/global", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unconfigured"
    assert body["configured"] is False
    assert body["data"] is None


def test_translator_monitoring_global_degraded_on_timeout(monkeypatch) -> None:
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")

    def _boom(_url: str, _timeout: float) -> tuple[int, bytes]:
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _boom,
    )
    r = client.get("/v1/translator/global", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["configured"] is True
    assert body["data"] is None


def test_translator_monitoring_normalizes_global(monkeypatch) -> None:
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    payload = {"version": "1.2.3", "role": "translator"}

    def _fake(url: str, _timeout: float) -> tuple[int, bytes]:
        assert "/api/v1/global" in url
        assert "?" not in url
        return (200, json.dumps(payload).encode())

    monkeypatch.setattr("node_api.services.translator_monitoring._http_get", _fake)
    r = client.get("/v1/translator/global", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["configured"] is True
    assert body["data"] == payload


def test_translator_monitoring_normalizes_server(monkeypatch) -> None:
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    payload = {"listen": "0.0.0.0:5000"}

    def _fake(url: str, _timeout: float) -> tuple[int, bytes]:
        assert url.endswith("/api/v1/server")
        return (200, json.dumps(payload).encode())

    monkeypatch.setattr("node_api.services.translator_monitoring._http_get", _fake)
    r = client.get("/v1/translator/upstream", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json()["data"] == payload


def test_translator_monitoring_normalizes_sv1_clients(monkeypatch) -> None:
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    payload = {"clients": [{"id": "a"}, {"id": "b"}]}

    def _fake(url: str, _timeout: float) -> tuple[int, bytes]:
        assert "/api/v1/sv1/clients" in url
        assert "offset=0" in url
        assert "limit=10" in url
        return (200, json.dumps(payload).encode())

    monkeypatch.setattr("node_api.services.translator_monitoring._http_get", _fake)
    r = client.get(
        "/v1/translator/downstreams",
        params={"offset": 0, "limit": 10},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    assert r.json()["data"] == payload


def test_translator_merged_status_logs_only_ok(monkeypatch, tmp_path: Path) -> None:
    logf = tmp_path / "ok.log"
    logf.write_text("2026-01-01T00:00:00Z INFO a::b: hi\n", encoding="utf-8")
    client = _client(monkeypatch, TRANSLATOR_LOG_PATH=str(logf))
    r = client.get("/v1/translator/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["log_configured"] is True
    assert b["monitoring_configured"] is False
    assert b["monitoring_status"] == "unconfigured"


def test_translator_merged_status_monitoring_only_ok(monkeypatch) -> None:
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")

    def _fake(url: str, _timeout: float) -> tuple[int, bytes]:
        path = url.split("?", 1)[0]
        if path.endswith("/api/v1/health"):
            return (200, b'{"ok":true}')
        if "/api/v1/server/channels" in path:
            return (200, b'{"channels":[]}')
        if path.endswith("/api/v1/sv1/clients"):
            return (200, b'{"clients":[]}')
        return (500, b"")

    monkeypatch.setattr("node_api.services.translator_monitoring._http_get", _fake)
    r = client.get("/v1/translator/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["log_configured"] is False
    assert b["monitoring_configured"] is True
    assert b["monitoring_status"] == "ok"
    assert b["upstream_channels"] == 0
    assert b["downstream_clients"] == 0


def test_translator_merged_status_both_configured_ok(monkeypatch, tmp_path: Path) -> None:
    logf = tmp_path / "both.log"
    logf.write_text("2026-01-01T00:00:00Z INFO a::b: hi\n", encoding="utf-8")
    client = _client(
        monkeypatch,
        TRANSLATOR_LOG_PATH=str(logf),
        TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9",
    )

    def _fake(url: str, _timeout: float) -> tuple[int, bytes]:
        path = url.split("?", 1)[0]
        if path.endswith("/api/v1/health"):
            return (200, b'{"ok":true}')
        if "/api/v1/server/channels" in path:
            return (200, b'{"channels":[{"x":1}]}')
        if path.endswith("/api/v1/sv1/clients"):
            return (200, b'{"clients":[{"id":"c"}]}')
        return (500, b"")

    monkeypatch.setattr("node_api.services.translator_monitoring._http_get", _fake)
    r = client.get("/v1/translator/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["log_configured"] is True
    assert b["monitoring_configured"] is True
    assert b["upstream_channels"] == 1
    assert b["downstream_clients"] == 1


def test_malformed_lines_skipped_in_tail(monkeypatch, tmp_path: Path) -> None:
    logf = tmp_path / "m.log"
    logf.write_text(
        "not a valid log line at all\n"
        "{broken json\n"
        "2026-04-10T21:00:00Z INFO x::y: good line\n",
        encoding="utf-8",
    )
    client = _client(monkeypatch, TRANSLATOR_LOG_PATH=str(logf))
    r = client.get("/v1/translator/logs/tail", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["message"] == "good line"
