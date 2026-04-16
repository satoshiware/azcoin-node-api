from __future__ import annotations

from fastapi.testclient import TestClient

from node_api.settings import get_settings


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


def test_metrics_shares_aggregate_current_single_point(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (
            [
                {
                    "miner_id": "m1",
                    "connected": True,
                    "accepted_shares": 10,
                    "rejected_shares": 2,
                },
                {
                    "miner_id": "m2",
                    "connected": True,
                    "accepted_shares": 5,
                    "rejected_shares": 1,
                },
                {
                    "miner_id": "m3",
                    "connected": False,
                    "accepted_shares": 7,
                    "rejected_shares": 3,
                },
            ],
            "ok",
        ),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/shares?window=1h&bucket=5m",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["window"] == "1h"
    assert body["data"]["bucket"] == "5m"
    assert body["data"]["miner_id"] is None
    assert body["data"]["series"] == {
        "submitted": [{"ts": body["data"]["series"]["submitted"][0]["ts"], "value": 18}],
        "accepted": [{"ts": body["data"]["series"]["accepted"][0]["ts"], "value": 15}],
        "rejected": [{"ts": body["data"]["series"]["rejected"][0]["ts"], "value": 3}],
    }


def test_metrics_shares_aggregate_includes_unknown_connection_rows(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (
            [
                {
                    "miner_id": "m1",
                    "connected": None,
                    "accepted_shares": 10,
                    "rejected_shares": 2,
                },
                {
                    "miner_id": "m2",
                    "connected": True,
                    "accepted_shares": 5,
                    "rejected_shares": 1,
                },
                {
                    "miner_id": "m3",
                    "connected": False,
                    "accepted_shares": 7,
                    "rejected_shares": 3,
                },
            ],
            "ok",
        ),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/shares?window=1h&bucket=5m",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["series"] == {
        "submitted": [{"ts": body["data"]["series"]["submitted"][0]["ts"], "value": 18}],
        "accepted": [{"ts": body["data"]["series"]["accepted"][0]["ts"], "value": 15}],
        "rejected": [{"ts": body["data"]["series"]["rejected"][0]["ts"], "value": 3}],
    }


def test_metrics_shares_miner_specific_current_single_point(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (
            [
                {
                    "miner_id": "m1",
                    "connected": True,
                    "accepted_shares": 4,
                    "rejected_shares": 1,
                },
                {
                    "miner_id": "m2",
                    "connected": True,
                    "accepted_shares": 8,
                    "rejected_shares": 0,
                },
            ],
            "ok",
        ),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/shares?window=15m&bucket=1m&miner_id=m1",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["miner_id"] == "m1"
    assert body["data"]["series"] == {
        "submitted": [{"ts": body["data"]["series"]["submitted"][0]["ts"], "value": 5}],
        "accepted": [{"ts": body["data"]["series"]["accepted"][0]["ts"], "value": 4}],
        "rejected": [{"ts": body["data"]["series"]["rejected"][0]["ts"], "value": 1}],
    }


def test_metrics_shares_no_current_source_degraded_empty_arrays(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (
            [
                {
                    "miner_id": "m1",
                    "connected": True,
                    "accepted_shares": None,
                    "rejected_shares": None,
                },
                {
                    "miner_id": "m2",
                    "connected": False,
                    "accepted_shares": None,
                    "rejected_shares": None,
                },
            ],
            "ok",
        ),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/shares?window=24h&bucket=1h",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {
        "code": "SHARE_COUNTERS_UNAVAILABLE",
        "message": "Current aggregate share counters unavailable",
    }
    assert body["data"]["series"] == {
        "submitted": [],
        "accepted": [],
        "rejected": [],
    }


def test_metrics_shares_invalid_window_bucket_controlled_client_error(monkeypatch):
    client = _make_client(monkeypatch)

    r = client.get(
        "/v1/metrics/shares?window=2h&bucket=30m",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 422


def test_metrics_shares_source_unavailable_error(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import metrics as metrics_module

    monkeypatch.setattr(
        metrics_module,
        "_normalize_items",
        lambda: (None, None),
        raising=True,
    )

    r = client.get(
        "/v1/metrics/shares?window=7d&bucket=1h",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "error"
    assert body["detail"] == {
        "code": "TRANSLATOR_UNAVAILABLE",
        "message": "Translator share source unavailable",
    }
    assert body["data"]["series"] == {
        "submitted": [],
        "accepted": [],
        "rejected": [],
    }
