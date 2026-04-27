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


# ============================================================================
# /v1/translator/miner-work/snapshot tests
# ============================================================================
#
# These tests exercise the normalized join endpoint that the future ledger
# interval-snapshot endpoints will read from. They mock the same
# ``translator_monitoring._http_get`` boundary the existing translator
# passthrough tests use, so the join logic gets exercised against realistic
# upstream/downstream payloads without any real HTTP traffic.


def _miner_work_http_get_factory(
    *,
    channels_payload: dict | list | None = None,
    clients_payload: dict | list | None = None,
    channels_status: int = 200,
    clients_status: int = 200,
    channels_exc: Exception | None = None,
    clients_exc: Exception | None = None,
):
    """Build an ``_http_get`` fake routing on the two snapshot URLs.

    Either side may be configured to raise (timeout / transport error) or
    return a non-200 status; this is what drives the fail-closed tests.
    """

    def _fake(url: str, _timeout: float) -> tuple[int, bytes]:
        path = url.split("?", 1)[0]
        if path.endswith("/api/v1/server/channels"):
            if channels_exc is not None:
                raise channels_exc
            body = b"" if channels_payload is None else json.dumps(channels_payload).encode()
            return (channels_status, body)
        if path.endswith("/api/v1/sv1/clients"):
            if clients_exc is not None:
                raise clients_exc
            body = b"" if clients_payload is None else json.dumps(clients_payload).encode()
            return (clients_status, body)
        return (404, b"")

    return _fake


# Realistic-looking upstream channel rows: integer counters, big stringified
# share_work_sum / hashrate, hex targets and extranonce prefixes.
_UPSTREAM_CHANNEL_2 = {
    "channel_id": 2,
    "user_identity": "baveetstudy.miner1",
    "shares_acknowledged": 26248,
    "shares_submitted": 26249,
    "shares_rejected": 0,
    "share_work_sum": "303120556",
    "best_diff": "9523103.34082162",
    "blocks_found": 31,
    "hashrate": "4783187400000.0",
    "nominal_hashrate": "4783187400000.0",
    "target_hex": "0000ff" + "00" * 29,
    "extranonce_prefix_hex": "00010002",
    "full_extranonce_size": 20,
    "rollable_extranonce_size": 4,
}

_UPSTREAM_CHANNEL_3 = {
    "channel_id": 3,
    "user_identity": "baveetstudy.miner2",
    "shares_acknowledged": 5,
    "shares_submitted": 5,
    "shares_rejected": 0,
    "share_work_sum": "1000",
    "best_diff": "1.5",
    "blocks_found": 0,
    "hashrate": "0.0",
    "nominal_hashrate": "0.0",
    "target_hex": "00000a" + "00" * 29,
    "extranonce_prefix_hex": "00010003",
    "full_extranonce_size": 20,
    "rollable_extranonce_size": 4,
}

_DOWNSTREAM_CLIENT_2 = {
    "client_id": 1,
    "channel_id": 2,
    "authorized_worker_name": "Ben.Cust1",
    "user_identity": "Ben.Cust1",
    "target_hex": "ffff00" + "00" * 29,
    "extranonce1_hex": "00010002aabb",
    "extranonce2_len": 4,
    "version_rolling": True,
    "version_rolling_mask": "1fffe000",
    "version_rolling_min_bit": "00000000",
}


def test_miner_work_snapshot_unconfigured(monkeypatch) -> None:
    """No translator monitoring URL set -> unconfigured envelope, empty items."""
    client = _client(monkeypatch)
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unconfigured"
    assert body["configured"] is False
    assert body["snapshot_time"] is None
    assert body["source"] == "translator"
    assert body["data"] == {"total": 0, "items": []}
    assert body["detail"] is None


def test_miner_work_snapshot_requires_auth(monkeypatch) -> None:
    """No bearer token -> 401, same as every other ``/v1/translator/*`` route."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    r = client.get("/v1/translator/miner-work/snapshot")
    assert r.status_code == 401


def test_miner_work_snapshot_joins_by_channel_id(monkeypatch) -> None:
    """Happy path: one channel, one matching downstream -> join_status='joined'."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": [_UPSTREAM_CHANNEL_2]},
            clients_payload={"clients": [_DOWNSTREAM_CLIENT_2]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["configured"] is True
    assert isinstance(body["snapshot_time"], int)
    assert body["source"] == "translator"
    assert body["detail"] is None
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["channel_id"] == 2
    assert item["client_id"] == 1
    assert item["join_status"] == "joined"
    assert item["worker_identity"] == "Ben.Cust1"
    assert item["upstream_user_identity"] == "baveetstudy.miner1"
    assert item["downstream_user_identity"] == "Ben.Cust1"
    assert item["downstream_target_hex"] == "ffff00" + "00" * 29
    assert item["upstream_target_hex"] == "0000ff" + "00" * 29


def test_miner_work_snapshot_worker_identity_uses_authorized_not_upstream(
    monkeypatch,
) -> None:
    """worker_identity must come from downstream authorized_worker_name, NOT upstream."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={
                "channels": [
                    {
                        **_UPSTREAM_CHANNEL_2,
                        "user_identity": "POOL_VIEW_OF_USER",
                    }
                ]
            },
            clients_payload={
                "clients": [
                    {
                        **_DOWNSTREAM_CLIENT_2,
                        "authorized_worker_name": "Ben.Cust1",
                        "user_identity": "DownstreamUserField",
                    }
                ]
            },
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    body = r.json()
    item = body["data"]["items"][0]
    assert item["worker_identity"] == "Ben.Cust1"
    assert item["worker_identity"] != "POOL_VIEW_OF_USER"
    assert item["worker_identity"] != "DownstreamUserField"
    assert item["authorized_worker_name"] == "Ben.Cust1"
    assert item["upstream_user_identity"] == "POOL_VIEW_OF_USER"


def test_miner_work_snapshot_worker_identity_falls_back_to_user_identity(
    monkeypatch,
) -> None:
    """Missing authorized_worker_name -> fall back to downstream user_identity."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": [_UPSTREAM_CHANNEL_2]},
            clients_payload={
                "clients": [
                    {
                        "client_id": 1,
                        "channel_id": 2,
                        "user_identity": "FallbackIdentity",
                    }
                ]
            },
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    item = r.json()["data"]["items"][0]
    assert item["worker_identity"] == "FallbackIdentity"
    assert item["authorized_worker_name"] is None
    assert item["downstream_user_identity"] == "FallbackIdentity"


def test_miner_work_snapshot_worker_identity_null_when_neither_present(
    monkeypatch,
) -> None:
    """Neither authorized_worker_name nor user_identity -> worker_identity null."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": [_UPSTREAM_CHANNEL_2]},
            clients_payload={"clients": [{"client_id": 1, "channel_id": 2}]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    item = r.json()["data"]["items"][0]
    assert item["worker_identity"] is None
    assert item["authorized_worker_name"] is None
    assert item["downstream_user_identity"] is None


def test_miner_work_snapshot_share_work_sum_and_best_diff_are_strings(
    monkeypatch,
) -> None:
    """share_work_sum, best_diff, hashrate, nominal_hashrate must be JSON strings."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": [_UPSTREAM_CHANNEL_2]},
            clients_payload={"clients": [_DOWNSTREAM_CLIENT_2]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    item = r.json()["data"]["items"][0]
    assert isinstance(item["share_work_sum"], str)
    assert item["share_work_sum"] == "303120556"
    assert isinstance(item["best_diff"], str)
    assert item["best_diff"] == "9523103.34082162"
    assert isinstance(item["hashrate"], str)
    assert isinstance(item["nominal_hashrate"], str)


def test_miner_work_snapshot_share_work_sum_stringifies_int_input(monkeypatch) -> None:
    """If translator emits share_work_sum as a JSON int, we still surface a string."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={
                "channels": [
                    {**_UPSTREAM_CHANNEL_2, "share_work_sum": 303120556}
                ]
            },
            clients_payload={"clients": [_DOWNSTREAM_CLIENT_2]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    item = r.json()["data"]["items"][0]
    assert item["share_work_sum"] == "303120556"
    assert isinstance(item["share_work_sum"], str)


def test_miner_work_snapshot_integer_counters_stay_integers(monkeypatch) -> None:
    """shares_*, blocks_found, channel_id, client_id must stay JSON integers."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": [_UPSTREAM_CHANNEL_2]},
            clients_payload={"clients": [_DOWNSTREAM_CLIENT_2]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    item = r.json()["data"]["items"][0]
    for k in (
        "channel_id",
        "client_id",
        "shares_acknowledged",
        "shares_submitted",
        "shares_rejected",
        "blocks_found",
        "extranonce2_len",
        "full_extranonce_size",
        "rollable_extranonce_size",
    ):
        assert isinstance(item[k], int), f"{k} is {type(item[k]).__name__}"
    assert item["shares_acknowledged"] == 26248
    assert item["shares_submitted"] == 26249
    assert item["shares_rejected"] == 0
    assert item["blocks_found"] == 31


def test_miner_work_snapshot_includes_downstream_only_item(monkeypatch) -> None:
    """Downstream channel_id with no upstream match -> join_status='downstream_only'."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": []},
            clients_payload={"clients": [_DOWNSTREAM_CLIENT_2]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["channel_id"] == 2
    assert item["join_status"] == "downstream_only"
    assert item["worker_identity"] == "Ben.Cust1"
    assert item["upstream_user_identity"] is None
    assert item["share_work_sum"] is None
    assert item["shares_submitted"] is None
    assert item["upstream_target_hex"] is None


def test_miner_work_snapshot_includes_upstream_only_item(monkeypatch) -> None:
    """Upstream channel with no downstream match -> join_status='upstream_only'."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": [_UPSTREAM_CHANNEL_2]},
            clients_payload={"clients": []},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    body = r.json()
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["channel_id"] == 2
    assert item["join_status"] == "upstream_only"
    assert item["worker_identity"] is None
    assert item["client_id"] is None
    assert item["upstream_user_identity"] == "baveetstudy.miner1"
    assert item["share_work_sum"] == "303120556"


def test_miner_work_snapshot_sorts_by_channel_id_ascending(monkeypatch) -> None:
    """Mixed upstream/downstream/joined rows must come back sorted by channel_id."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    # Channels 3 (joined), 2 (joined), 7 (upstream-only) on the upstream side;
    # downstreams for 2 and 3 plus a stray channel 5 (downstream-only).
    upstream_ch = [
        {**_UPSTREAM_CHANNEL_3, "channel_id": 7},
        _UPSTREAM_CHANNEL_3,
        _UPSTREAM_CHANNEL_2,
    ]
    downstream_cl = [
        {**_DOWNSTREAM_CLIENT_2, "client_id": 99, "channel_id": 5},
        {**_DOWNSTREAM_CLIENT_2, "client_id": 11, "channel_id": 3},
        _DOWNSTREAM_CLIENT_2,
    ]
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": upstream_ch},
            clients_payload={"clients": downstream_cl},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    body = r.json()
    channel_ids = [it["channel_id"] for it in body["data"]["items"]]
    assert channel_ids == [2, 3, 5, 7]
    by_ch = {it["channel_id"]: it for it in body["data"]["items"]}
    assert by_ch[2]["join_status"] == "joined"
    assert by_ch[3]["join_status"] == "joined"
    assert by_ch[5]["join_status"] == "downstream_only"
    assert by_ch[7]["join_status"] == "upstream_only"


def test_miner_work_snapshot_accepts_extended_channels_key(monkeypatch) -> None:
    """The translator may wrap upstream rows under 'extended_channels'."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"extended_channels": [_UPSTREAM_CHANNEL_2]},
            clients_payload={"clients": [_DOWNSTREAM_CLIENT_2]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["join_status"] == "joined"


def test_miner_work_snapshot_fail_closed_when_one_side_fails(monkeypatch) -> None:
    """One raw call OK, the other times out -> degraded envelope, NO partial data."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload={"channels": [_UPSTREAM_CHANNEL_2]},
            clients_exc=httpx.TimeoutException("downstreams gone"),
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["configured"] is True
    assert body["snapshot_time"] is None
    assert body["source"] == "translator"
    assert body["data"] == {"total": 0, "items": []}
    assert body["detail"] is not None
    assert "downstreams" in body["detail"]


def test_miner_work_snapshot_fail_closed_on_non_200(monkeypatch) -> None:
    """Either side returning non-200 also collapses to degraded with empty items."""
    client = _client(monkeypatch, TRANSLATOR_MONITORING_BASE_URL="http://127.0.0.1:9")
    monkeypatch.setattr(
        "node_api.services.translator_monitoring._http_get",
        _miner_work_http_get_factory(
            channels_payload=None,
            channels_status=503,
            clients_payload={"clients": [_DOWNSTREAM_CLIENT_2]},
        ),
    )
    r = client.get(
        "/v1/translator/miner-work/snapshot",
        headers={"Authorization": "Bearer testtoken"},
    )
    body = r.json()
    assert body["status"] == "degraded"
    assert body["data"]["items"] == []
    assert "upstream_channels" in (body["detail"] or "")
