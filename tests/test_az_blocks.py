from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from node_api.services.azcoin_rpc import AzcoinRpcTransportError
from node_api.settings import get_settings

AUTH_HEADER = {"Authorization": "Bearer testtoken"}


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_RPC_URL", "http://127.0.0.1:19332")
    monkeypatch.setenv("AZ_RPC_USER", "user")
    monkeypatch.setenv("AZ_RPC_PASSWORD", "pass")
    monkeypatch.setenv("AZ_EXPECTED_CHAIN", "main")
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


def _make_block(
    *,
    height: int,
    confirmations: int,
    vout: list[dict[str, Any]],
    time: int = 1_700_000_000,
    mediantime: int | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "hash": f"{height:064x}",
        "confirmations": confirmations,
        "time": time,
        "tx": [
            {
                "txid": f"cb{height:062x}",
                "vout": vout,
            }
        ],
    }
    if mediantime is not None:
        block["mediantime"] = mediantime
    return block


def _install_single_block_mock(monkeypatch, block: dict[str, Any], tip_height: int) -> None:
    """
    Common single-block RPC mock for tests that only care about how one block is
    normalized. Wires up getblockchaininfo / getblockhash / getblock against the
    given block at the given tip height.
    """
    from node_api.routes.v1 import az_blocks as az_blocks_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": tip_height,
                "bestblockhash": block["hash"],
            }
        if method == "getblockhash":
            assert params == [tip_height]
            return block["hash"]
        if method == "getblock":
            assert params == [block["hash"], 2]
            return block
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", fake_call, raising=True)


def test_az_blocks_rewards_requires_auth(monkeypatch):
    client = _make_client(monkeypatch)
    r = client.get("/v1/az/blocks/rewards")
    assert r.status_code == 401


def test_az_blocks_rewards_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    tip_height = 150
    fake_blocks = {
        150: _make_block(
            height=150,
            confirmations=1,
            mediantime=1_700_000_500,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "address": "AZaddr1",
                        "hex": "76a91400112233445566778899aabbccddeeff0011223388ac",
                    },
                }
            ],
        ),
        149: _make_block(
            height=149,
            confirmations=2,
            mediantime=1_700_000_400,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "address": "AZaddr2",
                        "hex": "76a914aabbccddeeff00112233445566778899aabbccdd88ac",
                    },
                }
            ],
        ),
    }
    tip_hash_value = fake_blocks[tip_height]["hash"]

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": tip_height,
                "bestblockhash": tip_hash_value,
            }
        if method == "getblockhash":
            height = params[0]
            return fake_blocks[height]["hash"]
        if method == "getblock":
            blockhash, verbosity = params
            assert verbosity == 2
            for block in fake_blocks.values():
                if block["hash"] == blockhash:
                    return block
            raise AssertionError(f"unknown blockhash: {blockhash}")
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/blocks/rewards?limit=2", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()

    assert body["tip_height"] == tip_height
    assert body["tip_hash"] == tip_hash_value
    assert body["chain"] == "main"
    assert body["maturity_confirmations"] == 100
    assert [b["height"] for b in body["blocks"]] == [150, 149]

    first = body["blocks"][0]
    assert first["blockhash"] == fake_blocks[150]["hash"]
    assert first["confirmations"] == 1
    assert first["mediantime"] == 1_700_000_500
    assert first["is_on_main_chain"] is True
    assert first["is_mature"] is False
    assert first["blocks_until_mature"] == 99
    assert first["maturity_status"] == "immature"
    assert first["coinbase_txid"] == fake_blocks[150]["tx"][0]["txid"]
    assert first["coinbase_total_sats"] == 5_000_000_000
    assert first["outputs"] == [
        {
            "index": 0,
            "value_sats": 5_000_000_000,
            "address": "AZaddr1",
            "script_type": "pubkeyhash",
            "script_pub_key_hex": "76a91400112233445566778899aabbccddeeff0011223388ac",
        }
    ]

    second = body["blocks"][1]
    assert second["confirmations"] == 2
    assert second["blocks_until_mature"] == 98
    assert second["is_mature"] is False


def test_az_blocks_rewards_decimal_precision_exact_for_0_1_and_6_15(monkeypatch):
    """
    Strict Decimal(str(value)) * 100_000_000 must land exactly on integer sats:
    0.1 -> 10_000_000 and 6.15 -> 615_000_000, with no FP noise leaking through.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=10,
        confirmations=500,
        vout=[
            {"n": 0, "value": 6.15, "scriptPubKey": {"type": "pubkeyhash", "address": "A"}},
            {
                "n": 1,
                "value": 0.1,
                "scriptPubKey": {"type": "witness_v0_keyhash", "hex": "0014deadbeef"},
            },
        ],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=10)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]

    assert only_block["maturity_status"] == "mature"
    assert only_block["is_mature"] is True
    assert only_block["blocks_until_mature"] == 0
    assert only_block["outputs"][0]["value_sats"] == 615_000_000
    assert only_block["outputs"][1]["value_sats"] == 10_000_000
    assert only_block["coinbase_total_sats"] == 625_000_000
    assert only_block["outputs"][0]["address"] == "A"
    assert only_block["outputs"][0]["script_pub_key_hex"] is None
    assert only_block["outputs"][1]["address"] is None
    assert only_block["outputs"][1]["script_type"] == "witness_v0_keyhash"
    assert only_block["outputs"][1]["script_pub_key_hex"] == "0014deadbeef"


def test_az_blocks_rewards_sums_multiple_valid_outputs_exactly(monkeypatch):
    """
    coinbase_total_sats must be the exact integer sum of every output's
    value_sats — proves we're summing post-Decimal conversion, not the raw
    floats the RPC returns.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=42,
        confirmations=120,
        mediantime=1_700_000_900,
        vout=[
            {"n": 0, "value": 50.0, "scriptPubKey": {"type": "pubkeyhash", "address": "A"}},
            {"n": 1, "value": 0.5, "scriptPubKey": {"type": "pubkeyhash", "address": "B"}},
            {"n": 2, "value": "0.00012345", "scriptPubKey": {"type": "pubkeyhash"}},
            {"n": 3, "value": 0, "scriptPubKey": {"type": "nulldata", "hex": "6a24aa21a9ed"}},
        ],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=42)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]

    expected = [5_000_000_000, 50_000_000, 12_345, 0]
    assert [o["value_sats"] for o in only_block["outputs"]] == expected
    assert only_block["coinbase_total_sats"] == sum(expected) == 5_050_012_345
    assert only_block["is_mature"] is True
    assert only_block["blocks_until_mature"] == 0
    assert only_block["mediantime"] == 1_700_000_900


def test_az_blocks_rewards_missing_address_with_script_fields_ok(monkeypatch):
    """
    A coinbase output whose scriptPubKey has only `type` and `hex` (e.g. the
    segwit witness commitment OP_RETURN) is a valid coinbase output even though
    it has no address. The endpoint must return it successfully with
    address=null, surfacing script_type and script_pub_key_hex.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=200,
        confirmations=10,
        vout=[
            {
                "n": 0,
                "value": 50.0,
                "scriptPubKey": {
                    "type": "pubkeyhash",
                    "address": "AZminer",
                    "hex": "76a914cafebabecafebabecafebabecafebabecafebabe88ac",
                },
            },
            {
                "n": 1,
                "value": 0,
                "scriptPubKey": {
                    "type": "nulldata",
                    "hex": "6a24aa21a9eddeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                },
            },
        ],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=200)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]

    commitment_output = only_block["outputs"][1]
    assert commitment_output["address"] is None
    assert commitment_output["script_type"] == "nulldata"
    assert commitment_output["script_pub_key_hex"].startswith("6a24aa21a9ed")
    assert commitment_output["value_sats"] == 0
    assert only_block["coinbase_total_sats"] == 5_000_000_000


@pytest.mark.parametrize(
    ("bad_value", "label"),
    [
        (None, "null value"),
        ("MISSING", "missing value"),
        ("not-a-number", "non-numeric string"),
        (-0.5, "negative value"),
        (0.000_000_001, "sub-satoshi precision"),
        (True, "boolean value"),
        ([], "wrong-type value"),
    ],
)
def test_az_blocks_rewards_invalid_coinbase_value_returns_invalid_payload(
    monkeypatch, bad_value, label
):
    """
    Any missing/null/non-numeric/negative/sub-satoshi/non-scalar coinbase
    value must fail the whole request as AZ_RPC_INVALID_PAYLOAD / 502 rather
    than silently returning value_sats=null.
    """
    client = _make_client(monkeypatch)

    if bad_value == "MISSING":
        bad_vout: dict[str, Any] = {"n": 0, "scriptPubKey": {"type": "pubkeyhash"}}
    else:
        bad_vout = {"n": 0, "value": bad_value, "scriptPubKey": {"type": "pubkeyhash"}}
    block = _make_block(height=7, confirmations=1, vout=[bad_vout])

    _install_single_block_mock(monkeypatch, block, tip_height=7)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 502, f"{label}: expected 502, got {r.status_code}"
    assert r.json()["detail"]["code"] == "AZ_RPC_INVALID_PAYLOAD"


def test_az_blocks_rewards_sub_satoshi_precision_fails(monkeypatch):
    """
    Explicit single-case proof that 0.000000001 (1e-9 AZC) — a value that
    cannot be represented as an integer number of sats — fails the request.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=3,
        confirmations=1,
        vout=[{"n": 0, "value": 0.000_000_001, "scriptPubKey": {"type": "pubkeyhash"}}],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=3)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["code"] == "AZ_RPC_INVALID_PAYLOAD"
    assert "sub-satoshi precision" in detail["message"]


@pytest.mark.parametrize(
    ("malformed_block", "label"),
    [
        (
            {"hash": "a" * 64, "confirmations": 1, "time": 0, "tx": [{"txid": "cb", "vout": []}]},
            "empty vout list",
        ),
        (
            {
                "hash": "b" * 64,
                "confirmations": 1,
                "time": 0,
                "tx": [
                    {
                        "txid": "cb",
                        "vout": [
                            "garbage-not-a-dict",
                        ],
                    }
                ],
            },
            "non-object vout entry",
        ),
        (
            {"hash": "c" * 64, "confirmations": 1, "time": 0, "tx": []},
            "empty tx list (no coinbase)",
        ),
        (
            {"hash": "d" * 64, "confirmations": 1, "time": 0},
            "tx field missing entirely",
        ),
    ],
)
def test_az_blocks_rewards_malformed_coinbase_returns_invalid_payload(
    monkeypatch, malformed_block, label
):
    client = _make_client(monkeypatch)

    _install_single_block_mock(monkeypatch, malformed_block, tip_height=1)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 502, f"{label}: expected 502, got {r.status_code}"
    assert r.json()["detail"]["code"] == "AZ_RPC_INVALID_PAYLOAD"


def test_az_blocks_rewards_missing_confirmations_is_unknown(monkeypatch):
    client = _make_client(monkeypatch)

    block = {
        "hash": "c" * 64,
        "time": 1_700_000_002,
        "tx": [{"txid": "cb", "vout": [{"n": 0, "value": 1.0}]}],
    }

    _install_single_block_mock(monkeypatch, block, tip_height=5)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["confirmations"] is None
    assert only_block["maturity_status"] == "unknown"
    assert only_block["is_mature"] is False
    assert only_block["blocks_until_mature"] is None
    assert only_block["mediantime"] is None
    # Missing confirmations is indeterminate state; we fail closed and report
    # the block as not on the active chain so callers never assume ledger truth.
    assert only_block["is_on_main_chain"] is False
    assert only_block["coinbase_total_sats"] == 100_000_000


def test_az_blocks_rewards_orphan_confirmations_is_not_on_main_chain(monkeypatch):
    """
    Bitcoin Core returns confirmations == -1 for blocks that are stored but no
    longer on the active chain (stale/orphan). is_on_main_chain must be false.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=99,
        confirmations=-1,
        vout=[{"n": 0, "value": 50.0, "scriptPubKey": {"type": "pubkeyhash", "address": "X"}}],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=99)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["confirmations"] == -1
    assert only_block["is_on_main_chain"] is False
    # confirmations < 0 is also not "mature" and has no countdown to maturity.
    assert only_block["is_mature"] is False
    assert only_block["blocks_until_mature"] is None


@pytest.mark.parametrize("confirmations", [0, 1, 99, 100, 12_345])
def test_az_blocks_rewards_active_chain_confirmations_is_on_main_chain(monkeypatch, confirmations):
    """
    Any non-negative integer confirmations value means the block is on the
    active chain, including the genesis-edge case of confirmations == 0.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=10,
        confirmations=confirmations,
        vout=[{"n": 0, "value": 50.0, "scriptPubKey": {"type": "pubkeyhash", "address": "Y"}}],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=10)

    r = client.get("/v1/az/blocks/rewards?limit=1", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["confirmations"] == confirmations
    assert only_block["is_on_main_chain"] is True


def test_az_blocks_rewards_limit_is_capped_by_tip(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    tip_height = 1

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": tip_height,
                "bestblockhash": f"{tip_height:064x}",
            }
        if method == "getblockhash":
            height = params[0]
            assert 0 <= height <= tip_height
            return f"{height:064x}"
        if method == "getblock":
            blockhash, verbosity = params
            assert verbosity == 2
            return {
                "hash": blockhash,
                "confirmations": 1,
                "time": 0,
                "tx": [{"txid": "cb", "vout": [{"n": 0, "value": 1.0}]}],
            }
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/blocks/rewards?limit=50", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    # tip=1 means heights [1, 0]; limit cannot synthesize extra blocks.
    assert [b["height"] for b in body["blocks"]] == [1, 0]
    assert body["maturity_confirmations"] == 100


def test_az_blocks_rewards_rejects_out_of_range_limit(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC should not be called for bad limit: {method} {params}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    for bad in ("0", "201", "-1", "abc"):
        r = client.get(f"/v1/az/blocks/rewards?limit={bad}", headers=AUTH_HEADER)
        assert r.status_code == 422, f"expected 422 for limit={bad}, got {r.status_code}"


def test_az_blocks_rewards_returns_502_on_rpc_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcTransportError("network down")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get("/v1/az/blocks/rewards", headers=AUTH_HEADER)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_UNAVAILABLE"


def test_az_blocks_rewards_returns_503_on_wrong_chain(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    calls: list[str] = []

    def fake_raw(self, method: str, params=None):  # noqa: ANN001
        calls.append(method)
        if method == "getblockchaininfo":
            return {"chain": "regtest"}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "_call_raw", fake_raw, raising=True)

    r = client.get("/v1/az/blocks/rewards", headers=AUTH_HEADER)
    assert r.status_code == 503
    assert r.json() == {
        "detail": {
            "code": "AZ_WRONG_CHAIN",
            "message": "AZCoin RPC is on the wrong chain (expected 'main').",
        }
    }
    assert calls == ["getblockchaininfo"]
