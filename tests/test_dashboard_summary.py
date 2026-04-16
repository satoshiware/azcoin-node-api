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


def _healthy_translator_snapshot() -> dict:
    return {
        "monitoring_status": "ok",
        "upstream_channels": 2,
        "downstream_clients": 3,
        "detail": None,
    }


def _healthy_node_envelope() -> dict:
    return {
        "status": "ok",
        "data": {
            "synced": True,
            "blocks": 100,
            "headers": 100,
            "peer_count": 8,
            "verification_progress": 1.0,
        },
        "detail": None,
    }


def _healthy_services_envelope() -> dict:
    return {
        "status": "ok",
        "data": {
            "aztranslator": {
                "status": "active",
                "uptime_secs": 3600,
                "pid": 111,
            },
            "azcoin_node_api": {
                "status": "active",
                "uptime_secs": 7200,
                "pid": 222,
            },
        },
        "detail": None,
    }


def _healthy_alerts_envelope() -> dict:
    return {
        "status": "ok",
        "data": {
            "items": [],
            "count": 0,
        },
        "detail": None,
    }


def _healthy_translator_miners_envelope() -> dict:
    return {
        "status": "ok",
        "data": {
            "clients": [
                {
                    "miner_id": "miner-1",
                    "connected": True,
                    "hashrate": 10.5,
                    "accepted_shares": 4,
                    "rejected_shares": 1,
                    "best_diff": 100.0,
                },
                {
                    "miner_id": "miner-2",
                    "connected": True,
                    "hashrate": 20.0,
                    "accepted_shares": 6,
                    "rejected_shares": 2,
                    "best_diff": 250.5,
                },
                {
                    "miner_id": "miner-3",
                    "connected": True,
                    "hashrate": 5.0,
                    "accepted_shares": 1,
                    "rejected_shares": 0,
                    "best_diff": 180.0,
                },
            ]
        },
        "detail": None,
    }


def test_dashboard_summary_healthy(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_miners_envelope",
        _healthy_translator_miners_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_alerts_envelope",
        _healthy_alerts_envelope,
        raising=True,
    )

    r = client.get("/v1/dashboard/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["api"]["healthy"] is True
    assert isinstance(body["data"]["api"]["uptime_secs"], int)
    assert body["data"]["translator"]["reachable"] is True
    assert body["data"]["translator"]["monitoring_status"] == "ok"
    assert body["data"]["translator"]["downstream_client_count"] == 3
    assert body["data"]["translator"]["upstream_channel_count"] == 2
    assert body["data"]["translator"]["total_hashrate"] == 35.5
    assert body["data"]["shares"] == {
        "submitted": 14,
        "acknowledged": 11,
        "rejected": 3,
        "best_diff": 250.5,
    }
    assert body["data"]["node"] == {
        "synced": True,
        "blocks": 100,
        "headers": 100,
        "peer_count": 8,
        "verification_progress": 1.0,
    }
    assert body["data"]["services"] == {
        "aztranslator": {
            "status": "active",
            "uptime_secs": 3600,
            "pid": 111,
        },
        "azcoin_node_api": {
            "status": "active",
            "uptime_secs": 7200,
            "pid": 222,
        },
    }
    assert body["data"]["alerts"] == {"active_count": 0, "items": []}
    assert datetime.fromisoformat(body["data"]["last_updated_ts"].replace("Z", "+00:00"))
    assert datetime.fromisoformat(
        body["data"]["translator"]["last_updated_ts"].replace("Z", "+00:00")
    )


def test_dashboard_summary_partial_dependency_failure_degraded(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_monitoring_snapshot",
        lambda: {
            "monitoring_status": "degraded",
            "upstream_channels": 2,
            "downstream_clients": None,
            "detail": "partial_fetch",
        },
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_miners_envelope",
        _healthy_translator_miners_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_services_status_envelope",
        lambda: {
            "status": "degraded",
            "data": {
                "aztranslator": {
                    "status": "active",
                    "uptime_secs": 3600,
                    "pid": 111,
                },
                "azcoin_node_api": {
                    "status": "unknown",
                    "uptime_secs": None,
                    "pid": None,
                },
            },
            "detail": {"unavailable_services": ["azcoin_node_api"]},
        },
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_alerts_envelope",
        _healthy_alerts_envelope,
        raising=True,
    )

    r = client.get("/v1/dashboard/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {"unavailable_dependencies": ["translator", "services"]}
    assert body["data"]["translator"] == {
        "reachable": True,
        "monitoring_status": "degraded",
        "downstream_client_count": None,
        "upstream_channel_count": 2,
        "total_hashrate": 35.5,
        "last_updated_ts": body["data"]["translator"]["last_updated_ts"],
    }
    assert body["data"]["shares"] == {
        "submitted": 14,
        "acknowledged": 11,
        "rejected": 3,
        "best_diff": 250.5,
    }
    assert body["data"]["services"]["azcoin_node_api"] == {
        "status": "unknown",
        "uptime_secs": None,
        "pid": None,
    }
    assert body["data"]["node"]["blocks"] == 100


def test_dashboard_summary_major_dependency_failure_error(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_monitoring_snapshot",
        lambda: None,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_miners_envelope",
        lambda: None,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_node_status_envelope",
        lambda: {"status": "error", "data": {}, "detail": {"code": "AZ_RPC_UNAVAILABLE"}},
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_services_status_envelope",
        lambda: {"status": "error", "detail": {"code": "SERVICE_INSPECTION_UNAVAILABLE"}},
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_alerts_envelope",
        lambda: {
            "status": "error",
            "data": {"items": [], "count": 0},
            "detail": {"code": "ALERT_EVALUATION_UNAVAILABLE"},
        },
        raising=True,
    )

    r = client.get("/v1/dashboard/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "error"
    assert body["detail"] == {
        "code": "DASHBOARD_COMPOSITION_UNAVAILABLE",
        "message": "Dashboard composition unavailable",
    }
    assert body["data"]["translator"]["monitoring_status"] is None
    assert body["data"]["node"] == {
        "synced": None,
        "blocks": None,
        "headers": None,
        "peer_count": None,
        "verification_progress": None,
    }
    assert body["data"]["services"]["aztranslator"]["status"] == "unknown"
    assert body["data"]["alerts"] == {"active_count": 0, "items": []}


def test_dashboard_summary_disconnected_miners_excluded(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_miners_envelope",
        lambda: {
            "status": "ok",
            "data": {
                "clients": [
                    {
                        "miner_id": "connected-1",
                        "connected": True,
                        "hashrate": 11.0,
                        "accepted_shares": 3,
                        "rejected_shares": 1,
                        "best_diff": 90.0,
                    },
                    {
                        "miner_id": "connected-2",
                        "connected": True,
                        "hashrate": 9.0,
                        "accepted_shares": 2,
                        "rejected_shares": 0,
                        "best_diff": 120.0,
                    },
                    {
                        "miner_id": "disconnected-1",
                        "connected": False,
                        "hashrate": 9999.0,
                        "accepted_shares": 999,
                        "rejected_shares": 999,
                        "best_diff": 99999.0,
                    },
                ]
            },
            "detail": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_alerts_envelope",
        _healthy_alerts_envelope,
        raising=True,
    )

    r = client.get("/v1/dashboard/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["translator"]["total_hashrate"] == 20.0
    assert body["data"]["shares"] == {
        "submitted": 6,
        "acknowledged": 5,
        "rejected": 1,
        "best_diff": 120.0,
    }


def test_dashboard_summary_partial_aggregate_availability(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_miners_envelope",
        lambda: {
            "status": "ok",
            "data": {
                "clients": [
                    {
                        "miner_id": "miner-1",
                        "connected": True,
                        "hashrate": 12.5,
                        "accepted_shares": 4,
                        "rejected_shares": None,
                        "best_diff": None,
                    }
                ]
            },
            "detail": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_alerts_envelope",
        _healthy_alerts_envelope,
        raising=True,
    )

    r = client.get("/v1/dashboard/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["translator"]["total_hashrate"] == 12.5
    assert body["data"]["shares"] == {
        "submitted": None,
        "acknowledged": 4,
        "rejected": None,
        "best_diff": None,
    }


def test_dashboard_summary_unavailable_aggregate_source_stable_degraded(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_translator_miners_envelope",
        lambda: {"status": "degraded", "data": None, "detail": "TimeoutException"},
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        dashboard_module,
        "_fetch_alerts_envelope",
        _healthy_alerts_envelope,
        raising=True,
    )

    r = client.get("/v1/dashboard/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {"unavailable_dependencies": ["translator"]}
    assert body["data"]["translator"] == {
        "reachable": True,
        "monitoring_status": "ok",
        "downstream_client_count": 3,
        "upstream_channel_count": 2,
        "total_hashrate": None,
        "last_updated_ts": body["data"]["translator"]["last_updated_ts"],
    }
    assert body["data"]["shares"] == {
        "submitted": None,
        "acknowledged": None,
        "rejected": None,
        "best_diff": None,
    }
