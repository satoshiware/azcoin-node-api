from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from node_api.settings import get_settings


def _make_client(monkeypatch, db_path: Path, token: str = "testtoken-123") -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_SHARE_DB_PATH", str(db_path))
    monkeypatch.setenv("AZ_NODE_API_TOKEN", token)
    get_settings.cache_clear()

    from node_api import main as main_module

    app = main_module.create_app()
    return TestClient(app)


def _payload(worker: str = "BenC") -> dict[str, object]:
    return {
        "ts": 1700000000,
        "ts_ms": 1700000000123,
        "remote": "127.0.0.1",
        "worker": worker,
        "job_id": "job-42",
        "difficulty": 1,
        "accepted": True,
        "reason": None,
        "extranonce2": "0a0b0c0d",
        "ntime": "65a1bc2f",
        "nonce": "deadbeef",
        "version_bits": "20000000",
        "accepted_unvalidated": True,
    }


def test_post_share_ok_with_token(monkeypatch, tmp_path: Path) -> None:
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    response = client.post(
        "/v1/mining/share",
        json=_payload(),
        headers={"Authorization": "Bearer testtoken-123"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_post_share_unauthorized_without_token_header(monkeypatch, tmp_path: Path) -> None:
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    response = client.post("/v1/mining/share", json=_payload())
    assert response.status_code == 401


def test_workers_endpoints_after_post(monkeypatch, tmp_path: Path) -> None:
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    post_resp = client.post(
        "/v1/mining/share",
        json=_payload(),
        headers={"Authorization": "Bearer testtoken-123"},
    )
    assert post_resp.status_code == 200

    workers_resp = client.get(
        "/v1/mining/workers",
        headers={"Authorization": "Bearer testtoken-123"},
    )
    assert workers_resp.status_code == 200
    workers = workers_resp.json()
    assert any(w["name"] == "BenC" and w["accepted"] == 1 for w in workers)

    worker_resp = client.get(
        "/v1/mining/workers/BenC",
        headers={"Authorization": "Bearer testtoken-123"},
    )
    assert worker_resp.status_code == 200
    worker = worker_resp.json()
    assert worker["name"] == "BenC"
    assert worker["accepted"] == 1
    assert worker["last_seen"] > 0
