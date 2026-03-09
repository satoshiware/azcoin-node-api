from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from node_api.services.bitcoin_rpc import BitcoinRpcResponseError
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


def test_btc_rpc_not_configured_returns_503(monkeypatch):
    monkeypatch.delenv("BTC_RPC_URL", raising=False)
    monkeypatch.delenv("BTC_RPC_COOKIE_FILE", raising=False)
    monkeypatch.delenv("BTC_RPC_USER", raising=False)
    monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    get_settings.cache_clear()

    from node_api import main as main_module

    client = TestClient(main_module.create_app())

    for path in ["/v1/btc/wallet/summary", "/v1/btc/wallet/transactions"]:
        r = client.get(path, headers={"Authorization": "Bearer testtoken"})
        assert r.status_code == 503, f"{path}: {r.text}"
        assert r.json()["detail"]["code"] == "BTC_RPC_NOT_CONFIGURED"


def test_btc_wallet_summary_success(monkeypatch):
    """Verify summary returns expected shape when RPC returns valid data."""
    class FakeRPC:
        def call_dict(self, method: str, params=None):  # noqa: ANN001
            if method == "getwalletinfo":
                return {
                    "txcount": 5,
                    "keypoolsize": 100,
                    "balance": 1.5,
                    "unconfirmed_balance": 0.1,
                    "immature_balance": 0.0,
                    "walletname": "default",
                    "unlocked_until": 0,
                }
            raise AssertionError(f"unexpected method: {method}")

        def call(self, method: str, params=None):  # noqa: ANN001
            if method == "getbalances":
                raise BitcoinRpcResponseError(code=-32601, message="Method not found")
            raise AssertionError(f"unexpected method: {method}")

    fake = FakeRPC()
    with patch(
        "node_api.routes.v1.btc_wallet.get_btc_rpc",
        return_value=fake,
    ):
        client = _make_client(monkeypatch)
        r = client.get("/v1/btc/wallet/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200
    body = r.json()
    assert body["txcount"] == 5
    assert body["keypoolsize"] == 100
    assert "balances" in body
    assert body["balances"]["trusted"] == 1.5
    assert body["balances"]["untrusted_pending"] == 0.1
    assert body["balances"]["immature"] == 0.0
    assert body["walletname"] == "default"


def test_btc_wallet_transactions_invalid_since_returns_422(monkeypatch):
    client = _make_client(monkeypatch)

    r = client.get(
        "/v1/btc/wallet/transactions?since=bad",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "BTC_INVALID_SINCE"


def test_btc_wallet_transactions_since_not_found_returns_404(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.services import bitcoin_rpc as btc_rpc_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        if method == "listsinceblock" and params and params[0] == "a" * 64:
            raise BitcoinRpcResponseError(code=-5, message="Block not found")
        raise AssertionError(f"unexpected: {method} {params}")

    monkeypatch.setattr(btc_rpc_module.BitcoinRPC, "call", boom, raising=True)

    r = client.get(
        "/v1/btc/wallet/transactions?since=" + "a" * 64,
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "BTC_SINCE_NOT_FOUND"


def test_btc_wallet_transactions_success_sorted_descending(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.services import bitcoin_rpc as btc_rpc_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "listtransactions":
            return [
                {"txid": "aa", "time": 100, "confirmations": 1, "amount": 0.5, "category": "receive"},
                {"txid": "bb", "time": 200, "confirmations": 2, "amount": -0.1, "category": "send"},
            ]
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(btc_rpc_module.BitcoinRPC, "call", fake_call, raising=True)

    r = client.get(
        "/v1/btc/wallet/transactions?limit=10",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200
    txs = r.json()
    assert len(txs) == 2
    assert txs[0]["time"] == 200
    assert txs[0]["txid"] == "bb"
    assert txs[1]["time"] == 100
    assert txs[1]["txid"] == "aa"


def test_btc_wallet_unavailable_returns_503(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.services import bitcoin_rpc as btc_rpc_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise BitcoinRpcResponseError(code=-19, message="Wallet is not loaded")

    monkeypatch.setattr(btc_rpc_module.BitcoinRPC, "call_dict", boom, raising=True)

    r = client.get("/v1/btc/wallet/summary", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "BTC_WALLET_UNAVAILABLE"


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
