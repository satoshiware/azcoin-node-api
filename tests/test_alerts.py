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


def _healthy_node_envelope() -> dict:
    return {
        "status": "ok",
        "data": {
            "synced": True,
            "initial_block_download": False,
        },
        "detail": None,
    }


def _healthy_services_envelope() -> dict:
    return {
        "status": "ok",
        "data": {
            "aztranslator": {
                "service_name": "aztranslator.service",
                "status": "active",
                "uptime_secs": 3600,
                "pid": 111,
                "last_updated_ts": "2026-01-01T00:00:00Z",
            },
            "azcoin_node_api": {
                "service_name": "azcoin-node-api.service",
                "status": "active",
                "uptime_secs": 3600,
                "pid": 222,
                "last_updated_ts": "2026-01-01T00:00:00Z",
            },
        },
        "detail": None,
    }


def _healthy_translator_snapshot() -> dict:
    return {
        "monitoring_status": "ok",
        "downstream_clients": 1,
        "detail": None,
    }


def test_alerts_no_active_alerts(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import alerts as alerts_module

    monkeypatch.setattr(
        alerts_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )

    r = client.get("/v1/alerts", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0
    assert datetime.fromisoformat(body["data"]["last_updated_ts"].replace("Z", "+00:00"))


def test_alerts_translator_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import alerts as alerts_module

    monkeypatch.setattr(
        alerts_module,
        "_fetch_translator_monitoring_snapshot",
        lambda: {
            "monitoring_status": "degraded",
            "downstream_clients": None,
            "detail": "TimeoutException",
        },
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )

    r = client.get("/v1/alerts", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {"unavailable_dependencies": ["translator"]}
    assert body["data"]["count"] == 1

    alert = body["data"]["items"][0]
    assert alert["id"] == "translator_unavailable"
    assert alert["source"] == "translator"
    assert alert["severity"] == "critical"
    assert alert["active"] is True
    assert alert["since_ts"] is None
    assert alert["detail"] == {"reason": "TimeoutException"}
    assert datetime.fromisoformat(alert["last_checked_ts"].replace("Z", "+00:00"))


def test_alerts_no_downstream_miners(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import alerts as alerts_module

    monkeypatch.setattr(
        alerts_module,
        "_fetch_translator_monitoring_snapshot",
        lambda: {
            "monitoring_status": "ok",
            "downstream_clients": 0,
            "detail": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )

    r = client.get("/v1/alerts", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["count"] == 1

    alert = body["data"]["items"][0]
    assert alert["id"] == "no_downstream_miners"
    assert alert["source"] == "translator"
    assert alert["severity"] == "warning"
    assert alert["active"] is True
    assert alert["since_ts"] is None
    assert alert["detail"] == {"downstream_clients": 0}
    assert datetime.fromisoformat(alert["last_checked_ts"].replace("Z", "+00:00"))


def test_alerts_node_not_synced(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import alerts as alerts_module

    monkeypatch.setattr(
        alerts_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_node_status_envelope",
        lambda: {
            "status": "degraded",
            "data": {
                "synced": False,
                "initial_block_download": True,
            },
            "detail": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )

    r = client.get("/v1/alerts", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["count"] == 1

    alert = body["data"]["items"][0]
    assert alert["id"] == "node_not_synced"
    assert alert["source"] == "node"
    assert alert["severity"] == "warning"
    assert alert["active"] is True
    assert alert["detail"] == {"synced": False, "initial_block_download": True}


def test_alerts_recent_translator_restart(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import alerts as alerts_module

    monkeypatch.setattr(
        alerts_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_node_status_envelope",
        _healthy_node_envelope,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_services_status_envelope",
        lambda: {
            "status": "ok",
            "data": {
                "aztranslator": {
                    "service_name": "aztranslator.service",
                    "status": "active",
                    "uptime_secs": 600,
                    "pid": 111,
                    "last_updated_ts": "2026-01-01T00:00:00Z",
                },
                "azcoin_node_api": {
                    "service_name": "azcoin-node-api.service",
                    "status": "active",
                    "uptime_secs": 3600,
                    "pid": 222,
                    "last_updated_ts": "2026-01-01T00:00:00Z",
                },
            },
            "detail": None,
        },
        raising=True,
    )

    r = client.get("/v1/alerts", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["count"] == 1

    alert = body["data"]["items"][0]
    assert alert["id"] == "recent_service_restart_aztranslator"
    assert alert["source"] == "service"
    assert alert["severity"] == "warning"
    assert alert["active"] is True
    assert alert["detail"] == {
        "service_name": "aztranslator.service",
        "uptime_secs": 600,
    }


def test_alerts_partial_dependency_failure_degraded_envelope(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import alerts as alerts_module

    monkeypatch.setattr(
        alerts_module,
        "_fetch_translator_monitoring_snapshot",
        _healthy_translator_snapshot,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_node_status_envelope",
        lambda: {
            "status": "error",
            "data": {
                "synced": None,
                "initial_block_download": None,
            },
            "detail": {"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"},
        },
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_services_status_envelope",
        _healthy_services_envelope,
        raising=True,
    )

    r = client.get("/v1/alerts", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {"unavailable_dependencies": ["node"]}
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0


def test_alerts_evaluation_unavailable_error(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import alerts as alerts_module

    monkeypatch.setattr(
        alerts_module,
        "_fetch_translator_monitoring_snapshot",
        lambda: None,
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_node_status_envelope",
        lambda: {
            "status": "error",
            "data": {},
            "detail": {"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"},
        },
        raising=True,
    )
    monkeypatch.setattr(
        alerts_module,
        "_fetch_services_status_envelope",
        lambda: {
            "status": "error",
            "data": {},
            "detail": {
                "code": "SERVICE_INSPECTION_UNAVAILABLE",
                "message": "Local service inspection unavailable",
            },
        },
        raising=True,
    )

    r = client.get("/v1/alerts", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "error"
    assert body["detail"] == {
        "code": "ALERT_EVALUATION_UNAVAILABLE",
        "message": "Alert evaluation unavailable",
    }
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0
