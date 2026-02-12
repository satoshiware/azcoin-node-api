from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from node_api.services.azcoin_rpc import AzcoinRpcResponseError, AzcoinRpcTransportError
from node_api.settings import get_settings

AUTH_HEADER = {"Authorization": "Bearer testtoken"}
VALID_SINCE_BLOCKHASH = "00000000000000000000000000000000000000000000000000000000000000aa"
BASE_PEER_KEYS = {
    "addr",
    "inbound",
    "subver",
    "pingtime",
    "bytesrecv",
    "bytessent",
    "lastsend",
    "lastrecv",
    "version",
}
NORMALIZED_MEMPOOL_KEYS = {
    "size",
    "bytes",
    "usage",
    "maxmempool",
    "mempoolminfee",
    "minrelaytxfee",
}


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_RPC_URL", "http://127.0.0.1:19332")
    monkeypatch.setenv("AZ_RPC_USER", "user")
    monkeypatch.setenv("AZ_RPC_PASSWORD", "pass")
    monkeypatch.setenv("AZ_EXPECTED_CHAIN", "micro")
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


@pytest.mark.parametrize(
    "path",
    [
        "/v1/az/node/peers",
        "/v1/az/mempool/info",
        "/v1/az/wallet/summary",
        "/v1/az/wallet/transactions",
    ],
)
def test_az_readonly_endpoints_require_auth(monkeypatch, path: str):
    client = _make_client(monkeypatch)
    r = client.get(path)
    assert r.status_code == 401


def test_az_node_peers_enforces_allowlist_and_optional_connection_type(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_node as az_node_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        assert method == "getpeerinfo"
        return [
            {
                "addr": "127.0.0.1:19332",
                "inbound": False,
                "subver": "/AZCoin:0.1.0/",
                "pingtime": 0.1,
                "bytesrecv": 123,
                "bytessent": 456,
                "lastsend": 1700000000,
                "lastrecv": 1700000010,
                "version": 70015,
                "connection_type": "outbound-full-relay",
                "services": "0000000000000409",
                "synced_headers": 77,
                "whitelisted": False,
                "addrlocal": "10.0.0.2:19332",
                "junk": {"nested": ["should", "not", "leak"]},
            },
            {
                "addr": "127.0.0.1:19333",
                "inbound": True,
                "subver": "/AZCoin:0.1.1/",
                "pingtime": 0.2,
                "bytesrecv": 999,
                "bytessent": 111,
                "lastsend": 1700000100,
                "lastrecv": 1700000200,
                "version": 70016,
                "services": "0000000000000409",
                "synced_headers": 88,
                "whitelisted": True,
                "nested": {"too": {"much": "data"}},
            }
        ]

    monkeypatch.setattr(az_node_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/node/peers", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2

    first_peer = body[0]
    assert set(first_peer.keys()) == BASE_PEER_KEYS | {"connection_type"}
    assert first_peer["connection_type"] == "outbound-full-relay"

    second_peer = body[1]
    assert set(second_peer.keys()) == BASE_PEER_KEYS
    assert "connection_type" not in second_peer

    for peer in body:
        assert set(peer.keys()).issubset(BASE_PEER_KEYS | {"connection_type"})


def test_az_mempool_info_normalizes_and_drops_extra_rpc_keys(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mempool as az_mempool_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        assert method == "getmempoolinfo"
        return {
            "size": 2,
            "bytes": 2048,
            "usage": 4096,
            "maxmempool": 300000000,
            "mempoolminfee": 0.00001,
            "minrelaytxfee": 0.00001,
            "loaded": True,
            "unbroadcastcount": 7,
            "completely_extra": "ignored",
        }

    monkeypatch.setattr(az_mempool_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mempool/info", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == NORMALIZED_MEMPOOL_KEYS
    assert body == {
        "size": 2,
        "bytes": 2048,
        "usage": 4096,
        "maxmempool": 300000000,
        "mempoolminfee": 0.00001,
        "minrelaytxfee": 0.00001,
    }


def test_az_chain_guardrail_returns_503_on_wrong_chain(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mempool as az_mempool_module

    calls: list[str] = []

    def fake_raw(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        calls.append(method)
        if method == "getblockchaininfo":
            return {"chain": "main"}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_mempool_module.AzcoinRpcClient, "_call_raw", fake_raw, raising=True)

    r = client.get("/v1/az/mempool/info", headers=AUTH_HEADER)
    assert r.status_code == 503
    assert r.json() == {
        "detail": {
            "code": "AZ_WRONG_CHAIN",
            "message": "AZCoin RPC is on the wrong chain (expected 'micro').",
        }
    }
    assert calls == ["getblockchaininfo"]


def test_az_chain_guardrail_passes_on_expected_chain(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mempool as az_mempool_module

    calls: list[str] = []

    def fake_raw(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        calls.append(method)
        if method == "getblockchaininfo":
            return {"chain": "micro"}
        if method == "getmempoolinfo":
            return {
                "size": 4,
                "bytes": 8192,
                "usage": 16384,
                "maxmempool": 300000000,
                "mempoolminfee": 0.00001,
                "minrelaytxfee": 0.00001,
            }
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_mempool_module.AzcoinRpcClient, "_call_raw", fake_raw, raising=True)

    r = client.get("/v1/az/mempool/info", headers=AUTH_HEADER)
    assert r.status_code == 200
    assert r.json() == {
        "size": 4,
        "bytes": 8192,
        "usage": 16384,
        "maxmempool": 300000000,
        "mempoolminfee": 0.00001,
        "minrelaytxfee": 0.00001,
    }
    assert calls == ["getblockchaininfo", "getmempoolinfo"]


def test_az_wallet_summary_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        if method == "getwalletinfo":
            return {
                "walletname": "main",
                "txcount": 11,
                "keypoolsize": 1000,
                "unlocked_until": 0,
                "balance": 1.0,
                "unconfirmed_balance": 0.2,
                "immature_balance": 0.3,
            }
        if method == "getbalances":
            return {"mine": {"trusted": 1.0, "untrusted_pending": 0.2, "immature": 0.3}}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/wallet/summary", headers=AUTH_HEADER)
    assert r.status_code == 200
    assert r.json() == {
        "walletname": "main",
        "txcount": 11,
        "keypoolsize": 1000,
        "unlocked_until": 0,
        "balances": {
            "trusted": 1.0,
            "untrusted_pending": 0.2,
            "immature": 0.3,
            "total": 1.5,
        },
    }


def test_az_wallet_summary_allows_missing_optional_fields(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        if method == "getwalletinfo":
            return {
                "txcount": 22,
                "keypoolsize": 500,
                "balance": 3.0,
                "unconfirmed_balance": 0.5,
                "immature_balance": 0.25,
            }
        if method == "getbalances":
            return {"mine": {"trusted": 3.0, "untrusted_pending": 0.5, "immature": 0.25}}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/wallet/summary", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"txcount", "keypoolsize", "balances"}
    assert "walletname" not in body
    assert "unlocked_until" not in body
    assert set(body["balances"].keys()) == {"trusted", "untrusted_pending", "immature", "total"}
    assert body == {
        "txcount": 22,
        "keypoolsize": 500,
        "balances": {
            "trusted": 3.0,
            "untrusted_pending": 0.5,
            "immature": 0.25,
            "total": 3.75,
        },
    }


def test_az_wallet_summary_falls_back_when_getbalances_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        if method == "getwalletinfo":
            return {
                "walletname": "main",
                "txcount": 12,
                "keypoolsize": 900,
                "balance": 2.0,
                "unconfirmed_balance": 0.1,
                "immature_balance": 0.4,
            }
        if method == "getbalances":
            raise AzcoinRpcResponseError(code=-32601, message="Method not found")
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/wallet/summary", headers=AUTH_HEADER)
    assert r.status_code == 200
    assert r.json() == {
        "walletname": "main",
        "txcount": 12,
        "keypoolsize": 900,
        "balances": {
            "trusted": 2.0,
            "untrusted_pending": 0.1,
            "immature": 0.4,
            "total": 2.5,
        },
    }


def test_az_wallet_transactions_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "listsinceblock":
            assert params == [VALID_SINCE_BLOCKHASH]
            return {
                "transactions": [
                    {
                        "txid": "aa" * 32,
                        "time": 1700001000,
                        "confirmations": 3,
                        "amount": 1.25,
                        "fee": -0.0001,
                        "category": "receive",
                        "address": "AZabc",
                        "blockhash": "bb" * 32,
                    },
                    {
                        "txid": "cc" * 32,
                        "time": 1700002000,
                        "confirmations": 0,
                        "amount": -0.5,
                        "category": "send",
                    },
                ]
            }
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get(
        f"/v1/az/wallet/transactions?since={VALID_SINCE_BLOCKHASH}&limit=1",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    assert r.json() == [
        {
            "txid": "cc" * 32,
            "time": 1700002000,
            "confirmations": 0,
            "amount": -0.5,
            "category": "send",
        }
    ]


def test_az_wallet_transactions_ordering_no_since_newest_first(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "listtransactions"
        assert params == ["*", 200, 0]
        return [
            {
                "txid": "a" * 64,
                "time": 300,
                "confirmations": 1,
                "amount": 1.0,
                "category": "receive",
            },
            {
                "txid": "b" * 64,
                "time": 100,
                "confirmations": 1,
                "amount": 2.0,
                "category": "receive",
            },
            {
                "txid": "c" * 64,
                "time": 200,
                "confirmations": 1,
                "amount": 3.0,
                "category": "receive",
            },
        ]

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/wallet/transactions?limit=3", headers=AUTH_HEADER)
    assert r.status_code == 200
    assert [tx["time"] for tx in r.json()] == [300, 200, 100]


def test_az_wallet_transactions_ordering_since_newest_first(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "listsinceblock"
        assert params == [VALID_SINCE_BLOCKHASH]
        return {
            "transactions": [
                {
                    "txid": "a" * 64,
                    "time": 300,
                    "confirmations": 1,
                    "amount": 1.0,
                    "category": "receive",
                },
                {
                    "txid": "b" * 64,
                    "time": 100,
                    "confirmations": 1,
                    "amount": 2.0,
                    "category": "receive",
                },
                {
                    "txid": "c" * 64,
                    "time": 200,
                    "confirmations": 1,
                    "amount": 3.0,
                    "category": "receive",
                },
            ]
        }

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get(
        f"/v1/az/wallet/transactions?since={VALID_SINCE_BLOCKHASH}&limit=3",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    assert [tx["time"] for tx in r.json()] == [300, 200, 100]


def test_az_wallet_transactions_applies_limit_after_sort(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "listtransactions"
        assert params == ["*", 200, 0]
        return [
            {
                "txid": "t1" * 32,
                "time": 1,
                "confirmations": 1,
                "amount": 1.0,
                "category": "receive",
            },
            {
                "txid": "t5" * 32,
                "time": 5,
                "confirmations": 1,
                "amount": 5.0,
                "category": "receive",
            },
            {
                "txid": "t3" * 32,
                "time": 3,
                "confirmations": 1,
                "amount": 3.0,
                "category": "receive",
            },
            {
                "txid": "t4" * 32,
                "time": 4,
                "confirmations": 1,
                "amount": 4.0,
                "category": "receive",
            },
            {
                "txid": "t2" * 32,
                "time": 2,
                "confirmations": 1,
                "amount": 2.0,
                "category": "receive",
            },
        ]

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/wallet/transactions?limit=2", headers=AUTH_HEADER)
    assert r.status_code == 200
    assert [tx["time"] for tx in r.json()] == [5, 4]


def test_az_wallet_transactions_missing_time_is_zero_and_sorts_last(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "listtransactions"
        assert params == ["*", 200, 0]
        return [
            {
                "txid": "missing" * 10 + "abcd",
                "confirmations": 1,
                "amount": 1.0,
                "category": "receive",
            },
            {
                "txid": "float" * 12 + "abcd",
                "time": 1.9,
                "confirmations": 1,
                "amount": 2.0,
                "category": "receive",
            },
            {
                "txid": "normal" * 10 + "abcd",
                "time": 5,
                "confirmations": 1,
                "amount": 3.0,
                "category": "receive",
            },
        ]

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/wallet/transactions?limit=10", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert [tx["time"] for tx in body] == [5, 1, 0]
    assert body[-1]["txid"] == "missing" * 10 + "abcd"


@pytest.mark.parametrize("bad_since", ["abc", "123"])
def test_az_wallet_transactions_returns_422_for_invalid_since_format(monkeypatch, bad_since: str):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC should not be called for invalid since: {method} {params}")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get(f"/v1/az/wallet/transactions?since={bad_since}", headers=AUTH_HEADER)
    assert r.status_code == 422
    assert r.json() == {
        "detail": {
            "code": "AZ_INVALID_SINCE",
            "message": "Invalid 'since' blockhash; expected 64 hex characters.",
        }
    }


def test_az_wallet_transactions_returns_404_for_unknown_since_blockhash(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "listsinceblock"
        assert params == [VALID_SINCE_BLOCKHASH]
        raise AzcoinRpcResponseError(code=-5, message="Block not found")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get(f"/v1/az/wallet/transactions?since={VALID_SINCE_BLOCKHASH}", headers=AUTH_HEADER)
    assert r.status_code == 404
    assert r.json() == {
        "detail": {
            "code": "AZ_SINCE_NOT_FOUND",
            "message": "Blockhash provided in 'since' was not found.",
        }
    }


def test_az_wallet_transactions_since_returns_502_on_rpc_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        assert method == "listsinceblock"
        assert params == [VALID_SINCE_BLOCKHASH]
        raise AzcoinRpcTransportError("network down")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get(f"/v1/az/wallet/transactions?since={VALID_SINCE_BLOCKHASH}", headers=AUTH_HEADER)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_UNAVAILABLE"


@pytest.mark.parametrize(
    ("path", "module_name"),
    [
        ("/v1/az/node/peers", "node_api.routes.v1.az_node"),
        ("/v1/az/mempool/info", "node_api.routes.v1.az_mempool"),
        ("/v1/az/wallet/summary", "node_api.routes.v1.az_wallet"),
        ("/v1/az/wallet/transactions", "node_api.routes.v1.az_wallet"),
    ],
)
def test_az_readonly_endpoints_return_502_on_rpc_unavailable(
    monkeypatch, path: str, module_name: str
):
    client = _make_client(monkeypatch)
    module = importlib.import_module(module_name)

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcTransportError("network down")

    monkeypatch.setattr(module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get(path, headers=AUTH_HEADER)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_UNAVAILABLE"


def test_az_wallet_summary_returns_503_when_wallet_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_wallet as az_wallet_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert params in (None, [])
        if method == "getwalletinfo":
            raise AzcoinRpcResponseError(
                code=-18, message="Requested wallet does not exist or is not loaded"
            )
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_wallet_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/wallet/summary", headers=AUTH_HEADER)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "AZ_WALLET_UNAVAILABLE"
