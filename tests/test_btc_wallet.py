from __future__ import annotations

from fastapi.testclient import TestClient

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


def test_btc_node_peers_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.services import bitcoin_rpc as btc_rpc_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getpeerinfo":
            return [
                {
                    "id": 1,
                    "addr": "1.2.3.4:8333",
                    "inbound": False,
                    "synced_headers": 100,
                    "synced_blocks": 100,
                    "bytesrecv": 1000,
                    "bytessent": 2000,
                    "subver": "/Satoshi:28.0.0/",
                    "version": 70016,
                    "startingheight": 0,
                },
            ]
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(btc_rpc_module.BitcoinRPC, "call", fake_call, raising=True)

    r = client.get("/v1/btc/node/peers", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    peers = r.json()
    assert len(peers) == 1
    assert peers[0]["id"] == 1
    assert peers[0]["addr"] == "1.2.3.4:8333"
    assert peers[0]["inbound"] is False
    assert peers[0]["synced_headers"] == 100
    assert peers[0]["bytesrecv"] == 1000
