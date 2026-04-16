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


def test_services_status_ok(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import services as services_module

    def fake_inspect(service_name: str, last_updated_ts: str):
        if service_name == "aztranslator.service":
            return (
                {
                    "service_name": service_name,
                    "status": "active",
                    "uptime_secs": 300,
                    "pid": 111,
                    "last_updated_ts": last_updated_ts,
                },
                True,
            )
        if service_name == "azcoin-node-api.service":
            return (
                {
                    "service_name": service_name,
                    "status": "active",
                    "uptime_secs": 120,
                    "pid": 222,
                    "last_updated_ts": last_updated_ts,
                },
                True,
            )
        raise AssertionError(f"unexpected service: {service_name}")

    monkeypatch.setattr(services_module, "_inspect_service", fake_inspect, raising=True)

    r = client.get("/v1/services/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"]["aztranslator"] == {
        "service_name": "aztranslator.service",
        "status": "active",
        "uptime_secs": 300,
        "pid": 111,
        "last_updated_ts": body["data"]["aztranslator"]["last_updated_ts"],
    }
    assert body["data"]["azcoin_node_api"] == {
        "service_name": "azcoin-node-api.service",
        "status": "active",
        "uptime_secs": 120,
        "pid": 222,
        "last_updated_ts": body["data"]["azcoin_node_api"]["last_updated_ts"],
    }
    assert body["data"]["aztranslator"]["last_updated_ts"] == body["data"]["azcoin_node_api"][
        "last_updated_ts"
    ]
    assert datetime.fromisoformat(
        body["data"]["aztranslator"]["last_updated_ts"].replace("Z", "+00:00")
    )


def test_services_status_degraded_when_one_service_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import services as services_module

    def fake_inspect(service_name: str, last_updated_ts: str):
        if service_name == "aztranslator.service":
            return (
                {
                    "service_name": service_name,
                    "status": "active",
                    "uptime_secs": 300,
                    "pid": 111,
                    "last_updated_ts": last_updated_ts,
                },
                True,
            )
        if service_name == "azcoin-node-api.service":
            return (
                {
                    "service_name": service_name,
                    "status": "unknown",
                    "uptime_secs": None,
                    "pid": None,
                    "last_updated_ts": last_updated_ts,
                },
                False,
            )
        raise AssertionError(f"unexpected service: {service_name}")

    monkeypatch.setattr(services_module, "_inspect_service", fake_inspect, raising=True)

    r = client.get("/v1/services/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "degraded"
    assert body["detail"] == {"unavailable_services": ["azcoin_node_api"]}
    assert body["data"]["aztranslator"]["status"] == "active"
    assert body["data"]["azcoin_node_api"] == {
        "service_name": "azcoin-node-api.service",
        "status": "unknown",
        "uptime_secs": None,
        "pid": None,
        "last_updated_ts": body["data"]["azcoin_node_api"]["last_updated_ts"],
    }
    assert datetime.fromisoformat(
        body["data"]["azcoin_node_api"]["last_updated_ts"].replace("Z", "+00:00")
    )


def test_services_status_error_when_inspection_backend_fails(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import services as services_module

    def boom(service_name: str, last_updated_ts: str):
        raise services_module.ServiceInspectionError()

    monkeypatch.setattr(services_module, "_inspect_service", boom, raising=True)

    r = client.get("/v1/services/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "error"
    assert body["detail"] == {
        "code": "SERVICE_INSPECTION_UNAVAILABLE",
        "message": "Local service inspection unavailable",
    }
    assert body["data"]["aztranslator"] == {
        "service_name": "aztranslator.service",
        "status": "unknown",
        "uptime_secs": None,
        "pid": None,
        "last_updated_ts": body["data"]["aztranslator"]["last_updated_ts"],
    }
    assert body["data"]["azcoin_node_api"] == {
        "service_name": "azcoin-node-api.service",
        "status": "unknown",
        "uptime_secs": None,
        "pid": None,
        "last_updated_ts": body["data"]["azcoin_node_api"]["last_updated_ts"],
    }
    assert body["data"]["aztranslator"]["last_updated_ts"] == body["data"]["azcoin_node_api"][
        "last_updated_ts"
    ]
    assert datetime.fromisoformat(
        body["data"]["aztranslator"]["last_updated_ts"].replace("Z", "+00:00")
    )
