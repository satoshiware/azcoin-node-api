from __future__ import annotations

from fastapi.testclient import TestClient

from node_api.settings import get_settings


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_RPC_URL", "http://127.0.0.1:19332")
    monkeypatch.setenv("AZ_RPC_USER", "user")
    monkeypatch.setenv("AZ_RPC_PASSWORD", "pass")
    monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
    monkeypatch.setenv("BTC_RPC_USER", "user")
    monkeypatch.setenv("BTC_RPC_PASSWORD", "pass")
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


def test_auth_smoke_for_blockchain_info_endpoints(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_node as az_node_module
    from node_api.routes.v1 import btc_node as btc_node_module

    def az_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblockchaininfo"
        return {
            "chain": "main",
            "blocks": 10,
            "headers": 11,
            "verificationprogress": 0.9,
            "difficulty": 2.5,
        }

    def btc_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblockchaininfo"
        return {
            "chain": "main",
            "blocks": 20,
            "headers": 21,
            "verificationprogress": 0.8,
            "difficulty": 1.5,
        }

    monkeypatch.setattr(az_node_module.AzcoinRpcClient, "call", az_call, raising=True)
    monkeypatch.setattr(btc_node_module.BitcoinRpcClient, "call", btc_call, raising=True)

    az_resp = client.get(
        "/v1/az/node/blockchain-info",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert az_resp.status_code == 200
    for key in ("chain", "blocks", "headers"):
        assert key in az_resp.json()

    btc_resp = client.get(
        "/v1/btc/node/blockchain-info",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert btc_resp.status_code == 200
    for key in ("chain", "blocks", "headers"):
        assert key in btc_resp.json()


def test_auth_smoke_for_node_summary(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import node as node_module

    def az_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblockchaininfo"
        return {
            "chain": "main",
            "blocks": 10,
            "headers": 11,
            "verificationprogress": 0.9,
            "difficulty": 2.5,
        }

    def btc_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblockchaininfo"
        return {
            "chain": "main",
            "blocks": 20,
            "headers": 21,
            "verificationprogress": 0.8,
            "difficulty": 1.5,
        }

    monkeypatch.setattr(node_module.AzcoinRpcClient, "call", az_call, raising=True)
    monkeypatch.setattr(node_module.BitcoinRpcClient, "call", btc_call, raising=True)

    resp = client.get("/v1/node/summary", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 200
    body = resp.json()
    for key in ("status", "az", "btc"):
        assert key in body
    for key in ("chain", "blocks", "headers"):
        assert key in body["az"]
        assert key in body["btc"]
