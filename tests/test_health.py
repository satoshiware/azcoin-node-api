from __future__ import annotations

from fastapi.testclient import TestClient

from node_api.settings import get_settings


def test_health_ok(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    app = main_module.create_app()
    client = TestClient(app)

    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
