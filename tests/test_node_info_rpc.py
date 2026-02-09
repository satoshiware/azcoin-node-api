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
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


def test_node_info_normalized_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_node as az_node_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": 123,
                "headers": 124,
                "verificationprogress": 0.99,
                "difficulty": 1.23,
            }
        if method == "getnetworkinfo":
            return {"connections": 8, "subversion": "/AZCoin:0.1.0/", "protocolversion": 70015}
        if method == "getmempoolinfo":
            return {"size": 2, "bytes": 2048}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_node_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/node/info", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json() == {
        "chain": "main",
        "blocks": 123,
        "headers": 124,
        "verificationprogress": 0.99,
        "difficulty": 1.23,
        "connections": 8,
        "subversion": "/AZCoin:0.1.0/",
        "protocolversion": 70015,
        "mempool": {"size": 2, "bytes": 2048},
    }


def test_node_info_returns_502_on_rpc_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_node as az_node_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcTransportError("network down")

    monkeypatch.setattr(az_node_module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get("/v1/az/node/info", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code in (502, 503)
    body = r.json()
    assert body["detail"]["code"] in ("AZ_RPC_UNAVAILABLE", "AZ_RPC_NOT_CONFIGURED")


def test_node_blockchain_info_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_node as az_node_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblockchaininfo"
        return {"chain": "main", "blocks": 77, "headers": 80}

    monkeypatch.setattr(az_node_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/node/blockchain-info", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    assert r.json() == {"chain": "main", "blocks": 77, "headers": 80}


def test_node_blockchain_info_returns_502_on_rpc_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_node as az_node_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcTransportError("network down")

    monkeypatch.setattr(az_node_module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get("/v1/az/node/blockchain-info", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code in (502, 503)
    body = r.json()
    assert body["detail"]["code"] in ("AZ_RPC_UNAVAILABLE", "AZ_RPC_NOT_CONFIGURED")
