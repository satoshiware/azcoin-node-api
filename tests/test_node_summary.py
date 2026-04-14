from __future__ import annotations

from fastapi.testclient import TestClient

from node_api.services.azcoin_rpc import AzcoinRpcTransportError
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


def test_node_summary_ok(monkeypatch):
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
    from node_api.services import bitcoin_rpc as btc_rpc_module

    monkeypatch.setattr(btc_rpc_module.BitcoinRPC, "call_dict", btc_call, raising=True)

    r = client.get("/v1/node/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "az": {
            "chain": "main",
            "blocks": 10,
            "headers": 11,
            "verificationprogress": 0.9,
            "difficulty": 2.5,
        },
        "btc": {
            "chain": "main",
            "blocks": 20,
            "headers": 21,
            "verificationprogress": 0.8,
            "difficulty": 1.5,
        },
    }


def test_node_summary_degraded(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import node as node_module

    def az_call(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcTransportError("network down")

    def btc_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblockchaininfo"
        return {"chain": "main", "blocks": 30, "headers": 31}

    monkeypatch.setattr(node_module.AzcoinRpcClient, "call", az_call, raising=True)
    from node_api.services import bitcoin_rpc as btc_rpc_module

    monkeypatch.setattr(btc_rpc_module.BitcoinRPC, "call_dict", btc_call, raising=True)

    r = client.get("/v1/node/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json() == {
        "status": "degraded",
        "az": {"error": {"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"}},
        "btc": {
            "chain": "main",
            "blocks": 30,
            "headers": 31,
            "verificationprogress": None,
            "difficulty": None,
        },
    }
