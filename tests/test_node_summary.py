from __future__ import annotations

from datetime import datetime

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


def test_node_status_ok(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import node as node_module

    def az_call(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": 100,
                "headers": 100,
                "bestblockhash": "ab" * 32,
                "difficulty": 2.5,
                "initialblockdownload": False,
                "verificationprogress": 1.0,
            }
        if method == "getnetworkinfo":
            return {"connections": 8, "warnings": ""}
        if method == "getmempoolinfo":
            return {"size": 3}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(node_module.AzcoinRpcClient, "call", az_call, raising=True)

    r = client.get("/v1/node/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["data"] == {
        "chain": "main",
        "network": None,
        "blocks": 100,
        "headers": 100,
        "best_block_hash": "ab" * 32,
        "difficulty": 2.5,
        "initial_block_download": False,
        "synced": True,
        "verification_progress": 1.0,
        "peer_count": 8,
        "mempool_tx_count": 3,
        "warnings": [],
        "last_updated_ts": body["data"]["last_updated_ts"],
    }
    assert datetime.fromisoformat(body["data"]["last_updated_ts"].replace("Z", "+00:00"))


def test_node_status_dependency_failure(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import node as node_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblockchaininfo"
        raise AzcoinRpcTransportError("network down")

    monkeypatch.setattr(node_module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get("/v1/node/status", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "error"
    assert body["detail"] == {
        "code": "AZ_RPC_UNAVAILABLE",
        "message": "AZCoin RPC unavailable",
    }
    assert body["data"] == {
        "chain": None,
        "network": None,
        "blocks": None,
        "headers": None,
        "best_block_hash": None,
        "difficulty": None,
        "initial_block_download": None,
        "synced": None,
        "verification_progress": None,
        "peer_count": None,
        "mempool_tx_count": None,
        "warnings": None,
        "last_updated_ts": body["data"]["last_updated_ts"],
    }
    assert datetime.fromisoformat(body["data"]["last_updated_ts"].replace("Z", "+00:00"))
