from __future__ import annotations

from fastapi.testclient import TestClient

from node_api.settings import get_settings


def test_protected_requires_auth(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    app = main_module.create_app()
    client = TestClient(app)

    r = client.get("/v1/az/node/info")
    assert r.status_code == 401


def test_protected_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    app = main_module.create_app()
    client = TestClient(app)

    r = client.get("/v1/az/node/info", headers={"Authorization": "Bearer wrong"})
    assert r.status_code in (401, 403)


def test_protected_allows_valid_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    app = main_module.create_app()
    client = TestClient(app)

    r = client.get("/v1/az/node/info", headers={"Authorization": "Bearer testtoken"})
    # Endpoint may return 503 if RPC isn't configured; auth is what we're validating here.
    assert r.status_code in (200, 503, 502)
