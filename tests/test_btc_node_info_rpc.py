from __future__ import annotations

from fastapi.testclient import TestClient

from node_api.services.bitcoin_rpc import BitcoinRpcTransportError
from node_api.settings import get_settings


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
    monkeypatch.setenv("BTC_RPC_USER", "user")
    monkeypatch.setenv("BTC_RPC_PASSWORD", "pass")
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


def test_btc_protected_requires_auth(monkeypatch):
    client = _make_client(monkeypatch)

    r = client.get("/v1/btc/node/info")
    assert r.status_code == 401


def test_btc_node_info_normalized_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import btc_node as btc_node_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": 1,
                "headers": 2,
                "verificationprogress": 0.5,
                "difficulty": 10.0,
            }
        if method == "getnetworkinfo":
            return {"connections": 12, "subversion": "/Satoshi:28.0.0/", "protocolversion": 70016}
        if method == "getmempoolinfo":
            return {"size": 3, "bytes": 4096}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(btc_node_module.BitcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/btc/node/info", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json() == {
        "chain": "main",
        "blocks": 1,
        "headers": 2,
        "verificationprogress": 0.5,
        "difficulty": 10.0,
        "connections": 12,
        "subversion": "/Satoshi:28.0.0/",
        "protocolversion": 70016,
        "mempool": {"size": 3, "bytes": 4096},
    }


def test_btc_node_info_returns_502_on_rpc_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import btc_node as btc_node_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise BitcoinRpcTransportError("network down")

    monkeypatch.setattr(btc_node_module.BitcoinRpcClient, "call", boom, raising=True)

    r = client.get("/v1/btc/node/info", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 502
    body = r.json()
    assert body["detail"]["code"] == "BTC_RPC_UNAVAILABLE"
