from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from node_api.settings import get_settings


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


def test_miners_healthy_list_path(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import miners as miners_module

    monkeypatch.setattr(
        miners_module,
        "_fetch_translator_miners_envelope",
        lambda: {
            "status": "ok",
            "data": {
                "clients": [
                    {
                        "miner_id": "miner-1",
                        "worker_name": "worker-a",
                        "user_identity": "user-a",
                        "channel_id": "ch-1",
                        "connected": True,
                        "hashrate": 123.4,
                        "target_hex": "ffff",
                        "extranonce1_hex": "abcd",
                        "extranonce2_len": 8,
                        "version_rolling_mask": "1fffe000",
                        "version_rolling_min_bit": 2,
                        "accepted_shares": 10,
                        "rejected_shares": 1,
                        "best_diff": 999.5,
                        "last_share_ts": "2026-04-15T18:00:00Z",
                        "connected_since_ts": "2026-04-15T17:00:00Z",
                    }
                ]
            },
            "detail": None,
        },
        raising=True,
    )

    r = client.get("/v1/miners", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["total"] == 1
    assert body["data"]["offset"] == 0
    assert body["data"]["limit"] == 50
    assert body["data"]["sort"] == "connected_since_ts"
    assert body["data"]["order"] == "desc"
    assert body["data"]["status_filter"] == "all"

    item = body["data"]["items"][0]
    assert item == {
        "miner_id": "miner-1",
        "worker_name": "worker-a",
        "user_identity": "user-a",
        "client_ip": None,
        "channel_id": "ch-1",
        "connected": True,
        "hashrate": 123.4,
        "target_hex": "ffff",
        "extranonce1_hex": "abcd",
        "extranonce2_len": 8,
        "version_rolling_mask": "1fffe000",
        "version_rolling_min_bit": 2,
        "accepted_shares": 10,
        "rejected_shares": 1,
        "best_diff": 999.5,
        "last_share_ts": "2026-04-15T18:00:00Z",
        "connected_since_ts": "2026-04-15T17:00:00Z",
    }
    assert datetime.fromisoformat(body["data"]["last_updated_ts"].replace("Z", "+00:00"))


def test_miners_empty_list_path(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import miners as miners_module

    monkeypatch.setattr(
        miners_module,
        "_fetch_translator_miners_envelope",
        lambda: {"status": "ok", "data": {"clients": []}, "detail": None},
        raising=True,
    )

    r = client.get("/v1/miners", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "data": {
            "items": [],
            "total": 0,
            "offset": 0,
            "limit": 50,
            "sort": "connected_since_ts",
            "order": "desc",
            "status_filter": "all",
            "last_updated_ts": r.json()["data"]["last_updated_ts"],
        },
        "detail": None,
    }


def test_miners_translator_unavailable_error_envelope(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import miners as miners_module

    monkeypatch.setattr(
        miners_module,
        "_fetch_translator_miners_envelope",
        lambda: {"status": "degraded", "data": None, "detail": "TimeoutException"},
        raising=True,
    )

    r = client.get("/v1/miners", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    assert r.json()["detail"] == {
        "code": "TRANSLATOR_UNAVAILABLE",
        "message": "Translator miner data unavailable",
    }


def test_miners_partial_record_normalization_degraded(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import miners as miners_module

    monkeypatch.setattr(
        miners_module,
        "_fetch_translator_miners_envelope",
        lambda: {
            "status": "ok",
            "data": {
                "clients": [
                    {
                        "client_id": "miner-1",
                        "worker_name": "worker-a",
                        "connected_since_ts": "2026-04-15T17:00:00Z",
                    },
                    {
                        "worker_name": "missing-id",
                        "connected": True,
                    },
                ]
            },
            "detail": None,
        },
        raising=True,
    )

    r = client.get("/v1/miners", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {"partial_records": 2}
    assert body["data"]["total"] == 1
    assert body["data"]["items"] == [
        {
            "miner_id": "miner-1",
            "worker_name": "worker-a",
            "user_identity": None,
            "client_ip": None,
            "channel_id": None,
            "connected": True,
            "hashrate": None,
            "target_hex": None,
            "extranonce1_hex": None,
            "extranonce2_len": None,
            "version_rolling_mask": None,
            "version_rolling_min_bit": None,
            "accepted_shares": None,
            "rejected_shares": None,
            "best_diff": None,
            "last_share_ts": None,
            "connected_since_ts": "2026-04-15T17:00:00Z",
        }
    ]


def test_miners_missing_connection_evidence_normalizes_to_none_and_partial(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import miners as miners_module

    monkeypatch.setattr(
        miners_module,
        "_fetch_translator_miners_envelope",
        lambda: {
            "status": "ok",
            "data": {
                "clients": [
                    {
                        "miner_id": "miner-unknown",
                        "worker_name": "worker-a",
                        "hashrate": 42.0,
                    }
                ]
            },
            "detail": None,
        },
        raising=True,
    )

    r = client.get("/v1/miners", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {"partial_records": 1}
    assert body["data"]["items"] == [
        {
            "miner_id": "miner-unknown",
            "worker_name": "worker-a",
            "user_identity": None,
            "client_ip": None,
            "channel_id": None,
            "connected": None,
            "hashrate": 42.0,
            "target_hex": None,
            "extranonce1_hex": None,
            "extranonce2_len": None,
            "version_rolling_mask": None,
            "version_rolling_min_bit": None,
            "accepted_shares": None,
            "rejected_shares": None,
            "best_diff": None,
            "last_share_ts": None,
            "connected_since_ts": None,
        }
    ]


def test_miners_pagination_sort_and_status_filter(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import miners as miners_module

    monkeypatch.setattr(
        miners_module,
        "_fetch_translator_miners_envelope",
        lambda: {
            "status": "ok",
            "data": {
                "clients": [
                    {
                        "miner_id": "m1",
                        "worker_name": "charlie",
                        "connected": True,
                        "connected_since_ts": "2026-04-15T17:00:00Z",
                    },
                    {
                        "miner_id": "m2",
                        "worker_name": "alpha",
                        "connected": False,
                        "connected_since_ts": "2026-04-15T16:00:00Z",
                    },
                    {
                        "miner_id": "m3",
                        "worker_name": "bravo",
                        "connected": True,
                        "connected_since_ts": "2026-04-15T15:00:00Z",
                    },
                ]
            },
            "detail": None,
        },
        raising=True,
    )

    r = client.get(
        "/v1/miners?status=connected&sort=worker_name&order=asc&offset=1&limit=1",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["total"] == 2
    assert body["data"]["offset"] == 1
    assert body["data"]["limit"] == 1
    assert body["data"]["sort"] == "worker_name"
    assert body["data"]["order"] == "asc"
    assert body["data"]["status_filter"] == "connected"
    assert len(body["data"]["items"]) == 1
    assert body["data"]["items"][0]["miner_id"] == "m1"
    assert body["data"]["items"][0]["worker_name"] == "charlie"


def test_miners_explicit_connection_state_mappings_unchanged() -> None:
    from node_api.routes.v1 import miners as miners_module

    assert miners_module._connected_from_record({"connected": True}) == (True, True)
    assert miners_module._connected_from_record({"connected": False}) == (False, True)
    assert miners_module._connected_from_record({"status": "active"}) == (True, True)
    assert miners_module._connected_from_record({"status": "offline"}) == (False, True)
