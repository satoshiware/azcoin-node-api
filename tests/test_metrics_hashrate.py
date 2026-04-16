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


def test_metrics_hashrate_aggregate_current_single_point(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (
            [
                {"miner_id": "m1", "connected": True, "hashrate": 10.5},
                {"miner_id": "m2", "connected": True, "hashrate": 20.0},
                {"miner_id": "m3", "connected": False, "hashrate": 30.0},
            ],
            "ok",
        ),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/hashrate?window=1h&bucket=5m",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["window"] == "1h"
    assert body["data"]["bucket"] == "5m"
    assert body["data"]["miner_id"] is None
    assert body["data"]["unit"] == "hps"
    assert body["data"]["series"] == [
        {
            "ts": body["data"]["series"][0]["ts"],
            "hashrate": 30.5,
        }
    ]
    assert datetime.fromisoformat(body["data"]["series"][0]["ts"].replace("Z", "+00:00"))


def test_metrics_hashrate_miner_specific_current_single_point(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (
            [
                {"miner_id": "m1", "connected": True, "hashrate": 15.25},
                {"miner_id": "m2", "connected": True, "hashrate": 8.0},
            ],
            "ok",
        ),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/hashrate?window=15m&bucket=1m&miner_id=m1",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["miner_id"] == "m1"
    assert body["data"]["series"] == [
        {
            "ts": body["data"]["series"][0]["ts"],
            "hashrate": 15.25,
        }
    ]


def test_metrics_hashrate_no_current_source_degraded_empty_series(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (
            [
                {"miner_id": "m1", "connected": True, "hashrate": None},
                {"miner_id": "m2", "connected": False, "hashrate": None},
            ],
            "ok",
        ),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/hashrate?window=24h&bucket=1h",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {
        "code": "HASHRATE_UNAVAILABLE",
        "message": "Current aggregate hashrate unavailable",
    }
    assert body["data"]["series"] == []


def test_metrics_hashrate_invalid_window_bucket_controlled_client_error(monkeypatch):
    client = _make_client(monkeypatch)

    r = client.get(
        "/v1/metrics/hashrate?window=2h&bucket=30m",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 422


def test_metrics_hashrate_source_unavailable_error(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (None, None),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/hashrate?window=7d&bucket=1h",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "error"
    assert body["detail"] == {
        "code": "TRANSLATOR_UNAVAILABLE",
        "message": "Translator hashrate source unavailable",
    }
    assert body["data"]["series"] == []
