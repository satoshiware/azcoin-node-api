from __future__ import annotations

from fastapi.testclient import TestClient

from node_api.settings import get_settings
from node_api.version import get_version


def test_get_version_returns_version_file_value() -> None:
    assert get_version() == "0.1.4"


def test_version_endpoint_returns_service_version(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    app = main_module.create_app()
    client = TestClient(app)

    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": "0.1.4", "service": "azcoin-api"}
