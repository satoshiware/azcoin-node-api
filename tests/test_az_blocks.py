from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from node_api.services.azcoin_rpc import AzcoinRpcResponseError, AzcoinRpcTransportError
from node_api.settings import get_settings

AUTH_HEADER = {"Authorization": "Bearer testtoken"}


def _make_client(monkeypatch, **extra_env: str) -> TestClient:
    """
    Build a TestClient with the standard dev auth + AZ RPC env wired up.

    `extra_env` is forwarded to monkeypatch.setenv so individual tests can
    layer in extra config (e.g. AZ_REWARD_OWNERSHIP_ADDRESSES) without each
    test having to re-set the baseline env.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_RPC_URL", "http://127.0.0.1:19332")
    monkeypatch.setenv("AZ_RPC_USER", "user")
    monkeypatch.setenv("AZ_RPC_PASSWORD", "pass")
    monkeypatch.setenv("AZ_EXPECTED_CHAIN", "main")
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
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
    # Real getblock(verbosity=2) responses always include `height`; we mirror
    # that here so blockhash-lookup tests (which read block["height"] directly)
    # can reuse this fixture. Scan-mode tests pass height through the loop and
    # ignore the dict field, so this is harmless to existing tests.
    block: dict[str, Any] = {
        "hash": f"{height:064x}",
        "height": height,
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


def _install_multi_block_mock(
    monkeypatch, blocks_by_height: dict[int, dict[str, Any]], tip_height: int
) -> None:
    """RPC mock for tests that walk more than one block from tip downward."""
    from node_api.routes.v1 import az_blocks as az_blocks_module

    tip_hash = blocks_by_height[tip_height]["hash"]

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": tip_height,
                "bestblockhash": tip_hash,
            }
        if method == "getblockhash":
            return blocks_by_height[params[0]]["hash"]
        if method == "getblock":
            blockhash, verbosity = params
            assert verbosity == 2
            for block in blocks_by_height.values():
                if block["hash"] == blockhash:
                    return block
            raise AssertionError(f"unknown blockhash: {blockhash}")
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", fake_call, raising=True)


def test_az_blocks_rewards_requires_auth(monkeypatch):
    client = _make_client(monkeypatch)
    r = client.get("/v1/az/blocks/rewards")
    assert r.status_code == 401


def test_az_blocks_rewards_success(monkeypatch):
    client = _make_client(monkeypatch)

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

    _install_multi_block_mock(monkeypatch, fake_blocks, tip_height=tip_height)

    r = client.get("/v1/az/blocks/rewards?limit=2&owned_only=false", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()

    assert body["tip_height"] == tip_height
    assert body["tip_hash"] == fake_blocks[tip_height]["hash"]
    assert body["chain"] == "main"
    assert body["maturity_confirmations"] == 100
    assert body["owned_only"] is False
    assert body["ownership_configured"] is False
    assert [b["height"] for b in body["blocks"]] == [150, 149]

    first = body["blocks"][0]
    assert first["blockhash"] == fake_blocks[150]["hash"]
    assert first["confirmations"] == 1
    assert first["mediantime"] == 1_700_000_500
    assert first["is_on_main_chain"] is True
    assert first["is_mature"] is False
    assert first["blocks_until_mature"] == 99
    assert first["maturity_status"] == "immature"
    # maturity_height = height + maturity_confirmations - 1
    # 150 + 100 - 1 = 249.
    assert first["maturity_height"] == 249
    assert first["is_owned_reward"] is False
    assert first["matched_output_indexes"] == []
    assert first["ownership_match"] is None
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
    assert second["maturity_height"] == 248
    assert second["is_owned_reward"] is False


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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["confirmations"] is None
    assert only_block["maturity_status"] == "unknown"
    assert only_block["is_mature"] is False
    assert only_block["blocks_until_mature"] is None
    assert only_block["mediantime"] is None
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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["confirmations"] == -1
    assert only_block["is_on_main_chain"] is False
    assert only_block["is_mature"] is False
    assert only_block["blocks_until_mature"] is None


@pytest.mark.parametrize(
    ("height", "confirmations", "expected_maturity_height"),
    [
        # Spec example: height=1000, maturity_confirmations=100 -> 1099.
        (1000, 1, 1099),
        # Block already mature (confirmations >> 100) still reports the same
        # `maturity_height` because it's derived from height alone, not from
        # confirmations.
        (5000, 500, 5099),
        # Low-height edge: maturity_height can be lower than tip; the field is
        # purely arithmetic and must still be returned regardless of whether
        # the block is currently mature/immature.
        (50, 10, 149),
        # Genesis edge.
        (0, 1, 99),
    ],
)
def test_az_blocks_rewards_includes_maturity_height(
    monkeypatch, height, confirmations, expected_maturity_height
):
    """
    `maturity_height = height + maturity_confirmations - 1` must always be
    populated, independently of confirmations / is_mature, so the ledger can
    schedule deferred reward ingestion without re-deriving it.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=height,
        confirmations=confirmations,
        vout=[
            {"n": 0, "value": 50.0, "scriptPubKey": {"type": "pubkeyhash", "address": "X"}}
        ],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=height)

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()

    only_block = body["blocks"][0]
    assert only_block["height"] == height
    assert only_block["maturity_height"] == expected_maturity_height
    # The relationship to maturity_confirmations must be self-consistent.
    assert (
        only_block["maturity_height"]
        == only_block["height"] + body["maturity_confirmations"] - 1
    )


def test_az_blocks_rewards_maturity_height_present_when_confirmations_unknown(monkeypatch):
    """
    `maturity_height` is derived from `height` and the protocol constant; it
    must remain present even when confirmations is missing/null and
    `blocks_until_mature` collapses to None.
    """
    client = _make_client(monkeypatch)

    block = {
        "hash": "f" * 64,
        "time": 1_700_000_999,
        "tx": [{"txid": "cb", "vout": [{"n": 0, "value": 1.0}]}],
    }

    _install_single_block_mock(monkeypatch, block, tip_height=750)

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["confirmations"] is None
    assert only_block["blocks_until_mature"] is None
    assert only_block["is_mature"] is False
    assert only_block["maturity_height"] == 849


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

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?limit=50&owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?owned_only=false", headers=AUTH_HEADER)
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

    r = client.get("/v1/az/blocks/rewards?owned_only=false", headers=AUTH_HEADER)
    assert r.status_code == 503
    assert r.json() == {
        "detail": {
            "code": "AZ_WRONG_CHAIN",
            "message": "AZCoin RPC is on the wrong chain (expected 'main').",
        }
    }
    assert calls == ["getblockchaininfo"]


# ----------------------------------------------------------------------------
# Ownership classification tests
# ----------------------------------------------------------------------------


def test_az_blocks_rewards_owned_only_without_config_returns_503(monkeypatch):
    """
    owned_only=true with neither AZ_REWARD_OWNERSHIP_ADDRESSES nor
    AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS set must fail closed and not call RPC.
    """
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC should not be called when ownership unconfigured: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get("/v1/az/blocks/rewards?owned_only=true", headers=AUTH_HEADER)
    assert r.status_code == 503
    assert r.json() == {
        "detail": {
            "code": "AZ_REWARD_OWNERSHIP_NOT_CONFIGURED",
            "message": "Reward ownership matching is not configured.",
        }
    }


def test_az_blocks_rewards_owned_only_default_is_true(monkeypatch):
    """
    Sanity check that owned_only defaults to true: hitting /rewards with no
    query string and no ownership configured produces the same 503 as an
    explicit owned_only=true.
    """
    client = _make_client(monkeypatch)

    r = client.get("/v1/az/blocks/rewards", headers=AUTH_HEADER)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "AZ_REWARD_OWNERSHIP_NOT_CONFIGURED"


def test_az_blocks_rewards_owned_only_filters_by_address(monkeypatch):
    """
    With only an ownership address configured, owned_only=true must drop blocks
    whose coinbase doesn't pay that address and label the surviving block as
    matched by coinbase_output_address.
    """
    client = _make_client(
        monkeypatch,
        AZ_REWARD_OWNERSHIP_ADDRESSES="  AZmine_us  ,  ,  AZmine_other  ",
    )

    tip_height = 5
    blocks_by_height = {
        5: _make_block(
            height=5,
            confirmations=10,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "address": "AZmine_us",
                        "hex": "76a91400112233445566778899aabbccddeeff0011223388ac",
                    },
                }
            ],
        ),
        4: _make_block(
            height=4,
            confirmations=11,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "address": "AZmine_them",
                        "hex": "76a914aabbccddeeff00112233445566778899aabbccdd88ac",
                    },
                }
            ],
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get("/v1/az/blocks/rewards?limit=2&owned_only=true", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()

    assert body["owned_only"] is True
    assert body["ownership_configured"] is True
    assert [b["height"] for b in body["blocks"]] == [5]

    only_block = body["blocks"][0]
    assert only_block["is_owned_reward"] is True
    assert only_block["matched_output_indexes"] == [0]
    assert only_block["ownership_match"] == "coinbase_output_address"


def test_az_blocks_rewards_owned_only_filters_by_script_pubkey_case_insensitive(monkeypatch):
    """
    Configured scriptPubKey hex matches case-insensitively against the actual
    coinbase output's hex. Demonstrates matching when configured value is
    upper-case and the on-chain hex is lower-case.
    """
    on_chain_hex = "76a914aabbccddeeff00112233445566778899aabbccdd88ac"
    client = _make_client(
        monkeypatch,
        AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS=on_chain_hex.upper(),
    )

    tip_height = 9
    blocks_by_height = {
        9: _make_block(
            height=9,
            confirmations=2,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "hex": on_chain_hex,
                        # No address; only the hex should drive the match.
                    },
                }
            ],
        ),
        8: _make_block(
            height=8,
            confirmations=3,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "address": "someone_else",
                        "hex": "76a914000000000000000000000000000000000000000088ac",
                    },
                }
            ],
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get("/v1/az/blocks/rewards?limit=2&owned_only=true", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()

    assert [b["height"] for b in body["blocks"]] == [9]
    only_block = body["blocks"][0]
    assert only_block["is_owned_reward"] is True
    assert only_block["matched_output_indexes"] == [0]
    assert only_block["ownership_match"] == "coinbase_script_pub_key"


def test_az_blocks_rewards_owned_only_false_returns_all_with_classification(monkeypatch):
    """
    With owned_only=false, every walked block is returned, but each carries
    the classification fields so callers can audit ownership without losing
    the chain context.
    """
    client = _make_client(
        monkeypatch,
        AZ_REWARD_OWNERSHIP_ADDRESSES="AZmine_us",
    )

    tip_height = 21
    blocks_by_height = {
        21: _make_block(
            height=21,
            confirmations=1,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {"type": "pubkeyhash", "address": "AZmine_us"},
                }
            ],
        ),
        20: _make_block(
            height=20,
            confirmations=2,
            vout=[
                {
                    "n": 0,
                    "value": 50.0,
                    "scriptPubKey": {"type": "pubkeyhash", "address": "someone_else"},
                }
            ],
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get("/v1/az/blocks/rewards?limit=2&owned_only=false", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()

    assert body["owned_only"] is False
    assert body["ownership_configured"] is True
    assert [b["height"] for b in body["blocks"]] == [21, 20]

    owned, unowned = body["blocks"]
    assert owned["is_owned_reward"] is True
    assert owned["matched_output_indexes"] == [0]
    assert owned["ownership_match"] == "coinbase_output_address"

    assert unowned["is_owned_reward"] is False
    assert unowned["matched_output_indexes"] == []
    assert unowned["ownership_match"] is None


def test_az_blocks_rewards_multiple_matching_outputs_returns_all_indexes(monkeypatch):
    """
    matched_output_indexes lists every coinbase output that matched, in the
    order they appear in the coinbase. Output index 1 (a non-matching
    address) is correctly excluded.
    """
    client = _make_client(
        monkeypatch,
        AZ_REWARD_OWNERSHIP_ADDRESSES="AZmine_us",
    )

    block = _make_block(
        height=77,
        confirmations=5,
        vout=[
            {
                "n": 0,
                "value": 25.0,
                "scriptPubKey": {"type": "pubkeyhash", "address": "AZmine_us"},
            },
            {
                "n": 1,
                "value": 5.0,
                "scriptPubKey": {"type": "pubkeyhash", "address": "AZsomeone"},
            },
            {
                "n": 2,
                "value": 20.0,
                "scriptPubKey": {"type": "pubkeyhash", "address": "AZmine_us"},
            },
        ],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=77)

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=true", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["is_owned_reward"] is True
    assert only_block["matched_output_indexes"] == [0, 2]
    assert only_block["ownership_match"] == "coinbase_output_address"


def test_az_blocks_rewards_address_and_script_match_combines_label(monkeypatch):
    """
    When at least one output matches by address AND at least one (possibly
    different) output matches by scriptPubKey, ownership_match must collapse
    to the combined label and matched_output_indexes must include both.
    """
    other_script_hex = "0014cafef00d0000000000000000000000000000beef"
    client = _make_client(
        monkeypatch,
        AZ_REWARD_OWNERSHIP_ADDRESSES="AZmine_us",
        AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS=other_script_hex,
    )

    block = _make_block(
        height=88,
        confirmations=5,
        vout=[
            {
                "n": 0,
                "value": 30.0,
                "scriptPubKey": {"type": "pubkeyhash", "address": "AZmine_us"},
            },
            {
                "n": 1,
                "value": 20.0,
                "scriptPubKey": {
                    "type": "witness_v0_keyhash",
                    "hex": other_script_hex,
                },
            },
        ],
    )

    _install_single_block_mock(monkeypatch, block, tip_height=88)

    r = client.get("/v1/az/blocks/rewards?limit=1&owned_only=true", headers=AUTH_HEADER)
    assert r.status_code == 200
    only_block = r.json()["blocks"][0]
    assert only_block["is_owned_reward"] is True
    assert only_block["matched_output_indexes"] == [0, 1]
    assert only_block["ownership_match"] == "coinbase_output_address_and_script_pub_key"


# ----------------------------------------------------------------------------
# Time-window filtering tests
# ----------------------------------------------------------------------------
#
# The following tests exercise the start_time / end_time / time_field params
# and the AZ_REWARD_TIME_RANGE_* error envelopes. They are deliberately built
# from small mocked chains (typically tip <= ~20) so each test is fully
# deterministic and the half-open interval and early-termination semantics
# can be asserted exactly.


def _coinbase_vout(address: str = "AZanyone") -> list[dict[str, Any]]:
    """One-output coinbase used as a default when a test doesn't care about value."""
    return [
        {"n": 0, "value": 50.0, "scriptPubKey": {"type": "pubkeyhash", "address": address}}
    ]


def test_az_blocks_rewards_time_window_filters_by_block_time(monkeypatch):
    """
    Only blocks whose block.time falls inside [start_time, end_time) are
    returned. Blocks before start_time and at-or-after end_time are dropped.
    """
    client = _make_client(monkeypatch)

    tip_height = 4
    blocks_by_height = {
        4: _make_block(height=4, confirmations=1, time=210, vout=_coinbase_vout()),
        3: _make_block(height=3, confirmations=2, time=170, vout=_coinbase_vout()),
        2: _make_block(height=2, confirmations=3, time=150, vout=_coinbase_vout()),
        1: _make_block(height=1, confirmations=4, time=100, vout=_coinbase_vout()),
        0: _make_block(height=0, confirmations=5, time=50, vout=_coinbase_vout()),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    # Window [150, 200): height 4 (time=210) excluded above, height 3 (time=170)
    # in, height 2 (time=150) in (boundary include), heights 1 and 0 below.
    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&start_time=150&end_time=200",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    assert [b["height"] for b in body["blocks"]] == [3, 2]
    assert body["time_filter"] == {
        "start_time": 150,
        "end_time": 200,
        "time_field": "time",
        "interval_rule": "start_time <= selected_time < end_time",
    }


def test_az_blocks_rewards_time_window_includes_block_at_start_time(monkeypatch):
    """
    A block whose selected time equals start_time is INCLUDED in the result
    (the half-open interval is closed on the lower bound).
    """
    client = _make_client(monkeypatch)

    tip_height = 2
    blocks_by_height = {
        2: _make_block(height=2, confirmations=1, time=180, vout=_coinbase_vout()),
        1: _make_block(height=1, confirmations=2, time=140, vout=_coinbase_vout()),
        0: _make_block(height=0, confirmations=3, time=100, vout=_coinbase_vout()),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&start_time=140&end_time=200",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    # Heights 2 (time=180) and 1 (time=140 == start_time) are in window.
    assert [b["height"] for b in body["blocks"]] == [2, 1]
    boundary_block = next(b for b in body["blocks"] if b["height"] == 1)
    assert boundary_block["time"] == 140


def test_az_blocks_rewards_time_window_excludes_block_at_end_time(monkeypatch):
    """
    A block whose selected time equals end_time is EXCLUDED (the half-open
    interval is open on the upper bound). This is what avoids double-counting
    the boundary block when two payout intervals abut.
    """
    client = _make_client(monkeypatch)

    tip_height = 2
    blocks_by_height = {
        2: _make_block(height=2, confirmations=1, time=200, vout=_coinbase_vout()),
        1: _make_block(height=1, confirmations=2, time=170, vout=_coinbase_vout()),
        0: _make_block(height=0, confirmations=3, time=150, vout=_coinbase_vout()),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&start_time=150&end_time=200",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    # Height 2 (time=200 == end_time) is excluded; heights 1 and 0 are inside.
    assert [b["height"] for b in body["blocks"]] == [1, 0]


def test_az_blocks_rewards_time_window_with_owned_only_true_returns_only_owned_in_window(
    monkeypatch,
):
    """
    Both filters must apply: blocks must be inside the time window AND owned.
    A block that is owned but outside the window is excluded; a block that is
    inside the window but unowned is also excluded.
    """
    client = _make_client(monkeypatch, AZ_REWARD_OWNERSHIP_ADDRESSES="AZmine_us")

    tip_height = 4
    blocks_by_height = {
        # Owned but ABOVE the window -> excluded by time filter.
        4: _make_block(
            height=4, confirmations=1, time=300, vout=_coinbase_vout("AZmine_us")
        ),
        # Owned and INSIDE the window -> included.
        3: _make_block(
            height=3, confirmations=2, time=180, vout=_coinbase_vout("AZmine_us")
        ),
        # Inside the window but UNOWNED -> excluded by ownership filter.
        2: _make_block(
            height=2, confirmations=3, time=160, vout=_coinbase_vout("someone_else")
        ),
        # Owned and INSIDE the window (boundary include at start_time=150).
        1: _make_block(
            height=1, confirmations=4, time=150, vout=_coinbase_vout("AZmine_us")
        ),
        # Below window.
        0: _make_block(
            height=0, confirmations=5, time=50, vout=_coinbase_vout("AZmine_us")
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=true&start_time=150&end_time=200",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    assert [b["height"] for b in body["blocks"]] == [3, 1]
    for block in body["blocks"]:
        assert block["is_owned_reward"] is True
        assert 150 <= block["time"] < 200


def test_az_blocks_rewards_time_window_with_owned_only_false_returns_owned_and_unowned(
    monkeypatch,
):
    """
    With owned_only=false, all blocks inside the time window are returned --
    owned and unowned alike -- but each carries the full ownership
    classification fields so callers can audit separately.
    """
    client = _make_client(monkeypatch, AZ_REWARD_OWNERSHIP_ADDRESSES="AZmine_us")

    tip_height = 3
    blocks_by_height = {
        3: _make_block(
            height=3, confirmations=1, time=180, vout=_coinbase_vout("AZmine_us")
        ),
        2: _make_block(
            height=2, confirmations=2, time=160, vout=_coinbase_vout("someone_else")
        ),
        1: _make_block(
            height=1, confirmations=3, time=140, vout=_coinbase_vout()
        ),  # below window
        0: _make_block(
            height=0, confirmations=4, time=100, vout=_coinbase_vout()
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&start_time=150&end_time=200",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    assert [b["height"] for b in body["blocks"]] == [3, 2]
    owned, unowned = body["blocks"]
    assert owned["is_owned_reward"] is True
    assert owned["ownership_match"] == "coinbase_output_address"
    assert unowned["is_owned_reward"] is False
    assert unowned["ownership_match"] is None


def test_az_blocks_rewards_time_window_filters_by_mediantime(monkeypatch):
    """
    time_field=mediantime drives the filter from block.mediantime, not
    block.time. The two timestamps can disagree (each block here has a
    deliberately different mediantime), and only mediantime should matter.
    """
    client = _make_client(monkeypatch)

    tip_height = 3
    blocks_by_height = {
        3: _make_block(
            height=3, confirmations=1, time=999, mediantime=210, vout=_coinbase_vout()
        ),
        2: _make_block(
            height=2, confirmations=2, time=1, mediantime=170, vout=_coinbase_vout()
        ),
        1: _make_block(
            height=1, confirmations=3, time=999, mediantime=150, vout=_coinbase_vout()
        ),
        0: _make_block(
            height=0, confirmations=4, time=1, mediantime=80, vout=_coinbase_vout()
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false"
        "&start_time=150&end_time=200&time_field=mediantime",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    # mediantimes: 210 (above), 170 (in), 150 (in, boundary), 80 (below).
    assert [b["height"] for b in body["blocks"]] == [2, 1]
    assert body["time_filter"]["time_field"] == "mediantime"


def test_az_blocks_rewards_time_window_mediantime_early_terminates_below_start(monkeypatch):
    """
    For time_field=mediantime, the scan must early-terminate as soon as it
    sees a block whose mediantime is below start_time. Heights deeper than
    that block must NEVER be requested (no extra getblockhash/getblock calls).
    BIP113 mediantime is non-decreasing on the active chain so this is safe.
    """
    client = _make_client(monkeypatch)

    tip_height = 100

    blocks_by_height = {
        100: _make_block(
            height=100, confirmations=1, time=1, mediantime=300, vout=_coinbase_vout()
        ),
        99: _make_block(
            height=99, confirmations=2, time=1, mediantime=220, vout=_coinbase_vout()
        ),
        # First block strictly below start_time=200 -- triggers early termination.
        98: _make_block(
            height=98, confirmations=3, time=1, mediantime=180, vout=_coinbase_vout()
        ),
    }
    # Heights 97..0 are intentionally NOT in blocks_by_height so any attempt
    # to fetch them blows up the test (KeyError) -- a stronger guarantee than
    # an inequality assertion on a fetched-heights list.

    fetched_heights: list[int] = []
    from node_api.routes.v1 import az_blocks as az_blocks_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": tip_height,
                "bestblockhash": blocks_by_height[100]["hash"],
            }
        if method == "getblockhash":
            height = params[0]
            fetched_heights.append(height)
            return blocks_by_height[height]["hash"]
        if method == "getblock":
            blockhash, verbosity = params
            assert verbosity == 2
            for block in blocks_by_height.values():
                if block["hash"] == blockhash:
                    return block
            raise AssertionError(f"unknown blockhash {blockhash}")
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false"
        "&start_time=200&end_time=400&time_field=mediantime",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    # Heights 100 and 99 inside [200, 400). Height 98 fetched (its mediantime
    # is what tells us to stop), excluded from result, then loop breaks.
    assert [b["height"] for b in body["blocks"]] == [100, 99]
    assert fetched_heights == [100, 99, 98]


@pytest.mark.parametrize("time_field", ["time", "mediantime"])
def test_az_blocks_rewards_time_window_excludes_block_with_missing_selected_time(
    monkeypatch, time_field
):
    """
    A block whose selected time field is missing or non-int is silently
    excluded from time-window results. The endpoint must NOT crash and must
    NOT short-circuit (other blocks past the gap can still be in window).
    """
    client = _make_client(monkeypatch)

    tip_height = 3

    if time_field == "time":
        # Use a block dict that simply omits the `time` field at the source.
        broken_block: dict[str, Any] = {
            "hash": f"{2:064x}",
            "confirmations": 2,
            "tx": [{"txid": f"cb{2:062x}", "vout": _coinbase_vout()}],
        }
    else:
        # `mediantime` is omitted by default in _make_block when not passed.
        broken_block = _make_block(
            height=2, confirmations=2, time=170, vout=_coinbase_vout()
        )

    blocks_by_height = {
        3: _make_block(
            height=3, confirmations=1, time=180, mediantime=180, vout=_coinbase_vout()
        ),
        2: broken_block,
        1: _make_block(
            height=1, confirmations=3, time=160, mediantime=160, vout=_coinbase_vout()
        ),
        0: _make_block(
            height=0, confirmations=4, time=10, mediantime=10, vout=_coinbase_vout()
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        f"/v1/az/blocks/rewards?owned_only=false"
        f"&start_time=150&end_time=200&time_field={time_field}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    # Height 2 (selected time missing/None) is excluded; heights 3 and 1 in.
    assert [b["height"] for b in body["blocks"]] == [3, 1]
    assert all(b["height"] != 2 for b in body["blocks"])


def test_az_blocks_rewards_time_window_only_start_time_returns_422(monkeypatch):
    """
    Only one of start_time/end_time provided -> 422 AZ_REWARD_TIME_RANGE_INCOMPLETE.
    RPC must NOT be called.
    """
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&start_time=100",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "AZ_REWARD_TIME_RANGE_INCOMPLETE"


def test_az_blocks_rewards_time_window_only_end_time_returns_422(monkeypatch):
    """Symmetric to the previous test: only end_time provided -> 422."""
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&end_time=200",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "AZ_REWARD_TIME_RANGE_INCOMPLETE"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (200, 200),  # equal -> rejected (open upper bound, equal would be empty)
        (300, 200),  # end < start
        (1, 0),
    ],
)
def test_az_blocks_rewards_time_window_end_le_start_returns_422(monkeypatch, start, end):
    """end_time must be strictly greater than start_time."""
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get(
        f"/v1/az/blocks/rewards?owned_only=false&start_time={start}&end_time={end}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "AZ_REWARD_TIME_RANGE_INVALID"


def test_az_blocks_rewards_time_window_invalid_time_field_returns_422(monkeypatch):
    """
    Anything other than `time` / `mediantime` is rejected by FastAPI's Literal
    validator before the route runs -> native FastAPI 422 envelope.
    """
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false"
        "&start_time=100&end_time=200&time_field=blocktime",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422


def test_az_blocks_rewards_time_window_negative_start_time_returns_422(monkeypatch):
    """`Query(ge=0)` rejects negative start_time before the route runs."""
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&start_time=-1&end_time=10",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422


def test_az_blocks_rewards_no_time_params_preserves_limit_based_behavior(monkeypatch):
    """
    No time params -> legacy `limit`-bounded scan returns exactly `limit`
    blocks (or fewer if capped by tip), and the time_filter top-level field
    reports start_time=null/end_time=null/time_field='time'.
    """
    client = _make_client(monkeypatch)

    tip_height = 9
    blocks_by_height = {
        h: _make_block(
            height=h, confirmations=tip_height - h + 1, time=h * 10, vout=_coinbase_vout()
        )
        for h in range(0, tip_height + 1)
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?limit=3&owned_only=false",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    assert [b["height"] for b in body["blocks"]] == [9, 8, 7]
    assert body["time_filter"] == {
        "start_time": None,
        "end_time": None,
        "time_field": "time",
        "interval_rule": "start_time <= selected_time < end_time",
    }


def test_az_blocks_rewards_time_window_max_scan_guard_returns_too_large(monkeypatch):
    """
    A time window that would force more than _MAX_TIME_RANGE_SCAN_BLOCKS
    block fetches must fail closed with 422 AZ_REWARD_TIME_RANGE_TOO_LARGE
    rather than performing an unbounded chain scan.

    We patch the constant to a small value (2) so the guard fires after two
    blocks have been processed -- equivalent in behavior to the production
    5000-block guard but cheap to exercise from a unit test.
    """
    from node_api.routes.v1 import az_blocks as az_blocks_module

    monkeypatch.setattr(az_blocks_module, "_MAX_TIME_RANGE_SCAN_BLOCKS", 2)

    client = _make_client(monkeypatch)

    tip_height = 10
    # All blocks have time far above the window so none match. With time_field
    # =time the scan does NOT early-terminate, so the guard is the only thing
    # that can stop the walk.
    blocks_by_height = {
        h: _make_block(
            height=h,
            confirmations=tip_height - h + 1,
            time=999_999_999,
            vout=_coinbase_vout(),
        )
        for h in (10, 9)
    }
    # Heights 8..0 deliberately absent: if the route ever tries to fetch them,
    # the mock will KeyError and the test will fail loudly.

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false&start_time=0&end_time=1000",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "AZ_REWARD_TIME_RANGE_TOO_LARGE"
    # The error message should reference the (patched) limit so operators see
    # exactly which guard fired.
    assert "2 blocks" in detail["message"]


def test_az_blocks_rewards_time_window_results_include_maturity_height(monkeypatch):
    """
    maturity_height must be populated on every per-block entry returned by
    the time-window path, identically to limit-based mode. We use mediantime
    here so the scan can early-terminate after observing the below-window
    block at height 999, keeping the test mock small while still exercising
    the spec example (height=1000 -> maturity_height=1099).
    """
    client = _make_client(monkeypatch)

    tip_height = 1000
    blocks_by_height = {
        1000: _make_block(
            height=1000,
            confirmations=1,
            time=180,
            mediantime=180,
            vout=_coinbase_vout(),
        ),
        # mediantime=140 < start_time=150 -> triggers early termination after
        # this block is fetched. Heights below 999 are intentionally absent so
        # any over-scan would KeyError.
        999: _make_block(
            height=999,
            confirmations=2,
            time=140,
            mediantime=140,
            vout=_coinbase_vout(),
        ),
    }

    _install_multi_block_mock(monkeypatch, blocks_by_height, tip_height=tip_height)

    r = client.get(
        "/v1/az/blocks/rewards?owned_only=false"
        "&start_time=150&end_time=200&time_field=mediantime",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    assert [b["height"] for b in body["blocks"]] == [1000]
    only_block = body["blocks"][0]
    # maturity_height = 1000 + 100 - 1 = 1099 (matches the spec example).
    assert only_block["maturity_height"] == 1099
    assert (
        only_block["maturity_height"]
        == only_block["height"] + body["maturity_confirmations"] - 1
    )


def test_az_blocks_rewards_time_window_owned_only_default_true_without_config_returns_503(
    monkeypatch,
):
    """
    Time-window queries do not bypass the ownership precheck: with
    owned_only=true (default) and ownership unconfigured, the endpoint still
    returns 503 AZ_REWARD_OWNERSHIP_NOT_CONFIGURED *before* any RPC call.
    """
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    r = client.get(
        "/v1/az/blocks/rewards?start_time=100&end_time=200",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "AZ_REWARD_OWNERSHIP_NOT_CONFIGURED"


# ----------------------------------------------------------------------------
# Scan mode now emits lookup-mode metadata fields on every response.
# ----------------------------------------------------------------------------


def test_az_blocks_rewards_scan_mode_emits_lookup_metadata(monkeypatch):
    """
    Even in pure scan mode (no blockhashes), the response must carry the new
    top-level lookup metadata so ledger code can branch on `lookup_mode`
    without reading the request URL back.
    """
    client = _make_client(monkeypatch)

    block = _make_block(
        height=200,
        confirmations=10,
        vout=[
            {
                "n": 0,
                "value": 1.0,
                "scriptPubKey": {
                    "type": "pubkeyhash",
                    "address": "AZaddrZ",
                    "hex": "76a91400000000000000000000000000000000000000ee88ac",
                },
            }
        ],
    )
    _install_single_block_mock(monkeypatch, block, tip_height=200)

    r = client.get(
        "/v1/az/blocks/rewards?limit=1&owned_only=false",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    assert body["lookup_mode"] == "scan"
    assert body["requested_blockhash_count"] == 0
    assert body["resolved_blockhash_count"] == 1
    assert body["unresolved_blockhashes"] == []
    assert body["stale_blockhashes"] == []
    assert body["filtered_out_blockhashes"] == []


# ----------------------------------------------------------------------------
# Blockhash lookup mode tests
# ----------------------------------------------------------------------------


def _install_lookup_blockhash_mock(
    monkeypatch,
    blocks_by_hash: dict[str, dict[str, Any] | None],
    tip_height: int,
    tip_hash: str,
) -> list[tuple[str, Any]]:
    """
    Install an RPC mock for blockhash-lookup tests.

    `blocks_by_hash` keys are lowercase-canonical hex hashes; values are:
      * a block dict to return from getblock(hash, 2), or
      * None to simulate an RPC application error (treated as "not found"
        / "unresolved" in lookup mode).

    Returns a list of (method, params) tuples capturing every RPC call so
    individual tests can assert that `getblockhash` is never invoked in
    lookup mode.
    """
    from node_api.routes.v1 import az_blocks as az_blocks_module

    calls: list[tuple[str, Any]] = []

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        calls.append((method, params))
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": tip_height,
                "bestblockhash": tip_hash,
            }
        if method == "getblockhash":
            raise AssertionError(
                "lookup mode must not call getblockhash; "
                f"unexpected params={params}"
            )
        if method == "getblock":
            assert isinstance(params, list) and len(params) == 2
            requested_hash, verbosity = params
            assert verbosity == 2
            key = requested_hash.lower() if isinstance(requested_hash, str) else None
            if key is None or key not in blocks_by_hash:
                raise AzcoinRpcResponseError(code=-5, message="Block not found")
            block = blocks_by_hash[key]
            if block is None:
                raise AzcoinRpcResponseError(code=-5, message="Block not found")
            return block
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", fake_call, raising=True)
    return calls


def _lookup_block(
    height: int,
    confirmations: int = 1,
    *,
    time: int = 1_700_000_000,
    mediantime: int | None = None,
    address: str = "AZaddrL",
    value: float = 50.0,
) -> dict[str, Any]:
    """Build a single getblock-verbosity-2 payload for blockhash-lookup tests."""
    return _make_block(
        height=height,
        confirmations=confirmations,
        time=time,
        mediantime=mediantime,
        vout=[
            {
                "n": 0,
                "value": value,
                "scriptPubKey": {
                    "type": "pubkeyhash",
                    "address": address,
                    "hex": "76a91400000000000000000000000000000000000000aa88ac",
                },
            }
        ],
    )


def test_az_blocks_rewards_lookup_mode_stale_orphan_excluded_from_blocks(monkeypatch):
    """
    Core-style stale block (confirmations=-1, not on main chain) resolves
    via getblock but must not appear in blocks[]; hash is only in
    stale_blockhashes. resolved_blockhash_count counts blocks[] only.
    """
    client = _make_client(monkeypatch)

    block_stale = _lookup_block(height=5, confirmations=-1)
    hash_stale = block_stale["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_stale: block_stale},
        tip_height=500,
        tip_hash="a" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_stale}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["blocks"] == []
    assert body["stale_blockhashes"] == [hash_stale]
    assert body["resolved_blockhash_count"] == 0
    assert body["requested_blockhash_count"] == 1
    assert body["unresolved_blockhashes"] == []
    assert body["filtered_out_blockhashes"] == []


def test_az_blocks_rewards_lookup_mode_mixed_stale_valid_and_missing(monkeypatch):
    """One stale orphan, one payable main-chain block, one not-found RPC."""
    client = _make_client(monkeypatch)

    block_stale = _lookup_block(height=1, confirmations=-1)
    block_ok = _lookup_block(height=2, confirmations=50, address="AZok")
    hash_stale = block_stale["hash"]
    hash_ok = block_ok["hash"]
    hash_missing = "2" * 64

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={
            hash_stale: block_stale,
            hash_ok: block_ok,
            hash_missing: None,
        },
        tip_height=500,
        tip_hash="b" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_stale}&blockhash={hash_ok}"
        f"&blockhash={hash_missing}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert [b["blockhash"] for b in body["blocks"]] == [hash_ok]
    assert body["stale_blockhashes"] == [hash_stale]
    assert body["unresolved_blockhashes"] == [hash_missing]
    assert body["requested_blockhash_count"] == 3
    assert body["resolved_blockhash_count"] == 1


def test_az_blocks_rewards_lookup_mode_stale_not_in_filtered_out_with_time_window(
    monkeypatch,
):
    """
    Optional time window must not list stale hashes in filtered_out_*:
    they are non-payable and classified only as stale_blockhashes.
    """
    client = _make_client(monkeypatch)

    block_stale = _lookup_block(height=3, confirmations=-1, time=1_700_000_550)
    hash_stale = block_stale["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_stale: block_stale},
        tip_height=500,
        tip_hash="c" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_stale}"
        "&start_time=1700000400&end_time=1700000500",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["blocks"] == []
    assert body["stale_blockhashes"] == [hash_stale]
    assert body["filtered_out_blockhashes"] == []
    assert body["unresolved_blockhashes"] == []


def test_az_blocks_rewards_lookup_mode_does_not_call_getblockhash(monkeypatch):
    """
    Repeated `?blockhash=` activates direct lookup: no height-based scan
    is performed and `getblockhash` must never be invoked.
    """
    client = _make_client(monkeypatch)

    block_a = _lookup_block(height=10, confirmations=200)
    block_b = _lookup_block(height=11, confirmations=199, address="AZaddrL2")
    hash_a = block_a["hash"]
    hash_b = block_b["hash"]

    calls = _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_a: block_a, hash_b: block_b},
        tip_height=500,
        tip_hash="f" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_a}&blockhash={hash_b}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    assert body["lookup_mode"] == "blockhashes"
    assert body["requested_blockhash_count"] == 2
    assert body["resolved_blockhash_count"] == 2
    assert body["unresolved_blockhashes"] == []
    assert body["stale_blockhashes"] == []
    assert body["filtered_out_blockhashes"] == []
    assert [b["blockhash"] for b in body["blocks"]] == [hash_a, hash_b]

    methods = [c[0] for c in calls]
    assert "getblockhash" not in methods
    assert methods.count("getblock") == 2


def test_az_blocks_rewards_lookup_mode_csv_blockhashes_param(monkeypatch):
    """
    The CSV `?blockhashes=h1,h2` form is equivalent to two repeated
    `?blockhash=` parameters and produces the same lookup results.
    """
    client = _make_client(monkeypatch)

    block_a = _lookup_block(height=20)
    block_b = _lookup_block(height=21, address="AZaddrM")
    hash_a = block_a["hash"]
    hash_b = block_b["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_a: block_a, hash_b: block_b},
        tip_height=500,
        tip_hash="e" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhashes={hash_a},{hash_b}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["lookup_mode"] == "blockhashes"
    assert body["stale_blockhashes"] == []
    assert [b["blockhash"] for b in body["blocks"]] == [hash_a, hash_b]


def test_az_blocks_rewards_lookup_mode_dedupe_preserves_request_order(monkeypatch):
    """
    Duplicate hashes are dropped (first occurrence wins) while the
    request-order of *unique* hashes is preserved across the joined input
    of repeated `blockhash` and CSV `blockhashes`.
    """
    client = _make_client(monkeypatch)

    block_a = _lookup_block(height=30)
    block_b = _lookup_block(height=31, address="AZB")
    block_c = _lookup_block(height=32, address="AZC")
    hash_a = block_a["hash"]
    hash_b = block_b["hash"]
    hash_c = block_c["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_a: block_a, hash_b: block_b, hash_c: block_c},
        tip_height=500,
        tip_hash="d" * 64,
    )

    r = client.get(
        # Order: a, b, a (dup), c, b (dup) -> unique a, b, c.
        f"/v1/az/blocks/rewards"
        f"?blockhash={hash_a}&blockhash={hash_b}&blockhash={hash_a}"
        f"&blockhashes={hash_c},{hash_b}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["requested_blockhash_count"] == 3
    assert body["resolved_blockhash_count"] == 3
    assert body["stale_blockhashes"] == []
    assert [b["blockhash"] for b in body["blocks"]] == [hash_a, hash_b, hash_c]


def test_az_blocks_rewards_lookup_mode_normalizes_case(monkeypatch):
    """
    Mixed-case hex hashes are accepted and normalized to lowercase before
    deduplication and RPC dispatch.
    """
    client = _make_client(monkeypatch)

    block_a = _lookup_block(height=40)
    hash_a = block_a["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_a: block_a},
        tip_height=500,
        tip_hash="c" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_a.upper()}&blockhash={hash_a.lower()}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["requested_blockhash_count"] == 1
    assert body["resolved_blockhash_count"] == 1
    assert body["stale_blockhashes"] == []
    assert body["blocks"][0]["blockhash"] == hash_a


def test_az_blocks_rewards_lookup_mode_invalid_blockhash_returns_422(monkeypatch):
    """
    A malformed hash (wrong length, non-hex chars) returns 422 with the
    AZ_REWARD_BLOCKHASH_INVALID code; no RPC call is made.
    """
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    # Wrong length:
    r = client.get(
        "/v1/az/blocks/rewards?blockhash=" + "a" * 63,
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "AZ_REWARD_BLOCKHASH_INVALID"

    # Non-hex character:
    r = client.get(
        "/v1/az/blocks/rewards?blockhash=" + "g" * 64,
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "AZ_REWARD_BLOCKHASH_INVALID"

    # Empty CSV is *not* an error (silently skipped) -> falls back to scan
    # mode and would 503 on missing ownership; just sanity-check the
    # invalid-character case via the CSV form:
    r = client.get(
        "/v1/az/blocks/rewards?blockhashes=" + "z" * 64,
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "AZ_REWARD_BLOCKHASH_INVALID"


def test_az_blocks_rewards_lookup_mode_too_many_returns_422(monkeypatch):
    """
    Requesting more than _MAX_BLOCKHASH_LOOKUP unique hashes returns 422
    AZ_REWARD_BLOCKHASH_LOOKUP_TOO_LARGE before any RPC call is made.
    """
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def should_not_call(self, method: str, params=None):  # noqa: ANN001
        raise AssertionError(f"RPC unexpectedly called: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", should_not_call, raising=True)

    # Build _MAX_BLOCKHASH_LOOKUP + 1 distinct valid hashes via the CSV form.
    too_many = ",".join(f"{i:064x}" for i in range(az_blocks_module._MAX_BLOCKHASH_LOOKUP + 1))
    r = client.get(
        f"/v1/az/blocks/rewards?blockhashes={too_many}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "AZ_REWARD_BLOCKHASH_LOOKUP_TOO_LARGE"


def test_az_blocks_rewards_lookup_mode_unresolved_blockhash_does_not_crash(monkeypatch):
    """
    A specific hash returning a JSON-RPC application error (e.g. -5 Block
    not found) is recorded in `unresolved_blockhashes` while remaining
    hashes still resolve normally; whole response is 200.
    """
    client = _make_client(monkeypatch)

    block_good = _lookup_block(height=50)
    hash_good = block_good["hash"]
    hash_missing = "1" * 64

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_good: block_good, hash_missing: None},
        tip_height=500,
        tip_hash="b" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_good}&blockhash={hash_missing}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["lookup_mode"] == "blockhashes"
    assert body["requested_blockhash_count"] == 2
    assert body["resolved_blockhash_count"] == 1
    assert body["unresolved_blockhashes"] == [hash_missing]
    assert body["stale_blockhashes"] == []
    assert [b["blockhash"] for b in body["blocks"]] == [hash_good]


def test_az_blocks_rewards_lookup_mode_time_window_includes_block_at_start_time(monkeypatch):
    """
    Time-window filter is half-open: a block whose selected time equals
    `start_time` is INCLUDED.
    """
    client = _make_client(monkeypatch)

    block = _lookup_block(height=60, time=1_700_000_500)
    hash_at_start = block["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_at_start: block},
        tip_height=500,
        tip_hash="a" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_at_start}"
        "&start_time=1700000500&end_time=1700000600",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert [b["blockhash"] for b in body["blocks"]] == [hash_at_start]
    assert body["filtered_out_blockhashes"] == []


def test_az_blocks_rewards_lookup_mode_time_window_excludes_block_at_end_time(monkeypatch):
    """
    Time-window filter is half-open: a block whose selected time equals
    `end_time` is EXCLUDED and listed in `filtered_out_blockhashes`.
    """
    client = _make_client(monkeypatch)

    block = _lookup_block(height=70, time=1_700_000_600)
    hash_at_end = block["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_at_end: block},
        tip_height=500,
        tip_hash="9" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_at_end}"
        "&start_time=1700000500&end_time=1700000600",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["blocks"] == []
    assert body["filtered_out_blockhashes"] == [hash_at_end]
    assert body["resolved_blockhash_count"] == 0
    assert body["requested_blockhash_count"] == 1
    assert body["stale_blockhashes"] == []


def test_az_blocks_rewards_lookup_mode_time_window_filters_by_mediantime(monkeypatch):
    """
    `time_field=mediantime` makes the half-open interval apply to the
    block's BIP113 mediantime, not the header `time`.
    """
    client = _make_client(monkeypatch)

    # Header time inside the window, but mediantime BELOW start_time:
    # mediantime mode must filter this block out, proving the selector
    # actually drives the comparison.
    block = _lookup_block(
        height=80,
        time=1_700_000_550,
        mediantime=1_700_000_400,
    )
    hash_under = block["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_under: block},
        tip_height=500,
        tip_hash="8" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_under}"
        "&start_time=1700000500&end_time=1700000600&time_field=mediantime",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["blocks"] == []
    assert body["filtered_out_blockhashes"] == [hash_under]
    assert body["time_filter"]["time_field"] == "mediantime"
    assert body["stale_blockhashes"] == []


def test_az_blocks_rewards_lookup_mode_strict_coinbase_validation_still_applies(monkeypatch):
    """
    A resolved block with a malformed coinbase (here: negative value)
    must produce 502 AZ_RPC_INVALID_PAYLOAD just like in scan mode; we
    do NOT silently demote it to "unresolved".
    """
    client = _make_client(monkeypatch)

    bad_block = _make_block(
        height=90,
        confirmations=10,
        vout=[
            {
                "n": 0,
                "value": -1.0,
                "scriptPubKey": {"type": "pubkeyhash", "address": "AZbad"},
            }
        ],
    )
    bad_hash = bad_block["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={bad_hash: bad_block},
        tip_height=500,
        tip_hash="7" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={bad_hash}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_INVALID_PAYLOAD"


def test_az_blocks_rewards_lookup_mode_includes_maturity_fields(monkeypatch):
    """
    Lookup-mode entries carry the same maturity-related fields as scan
    mode: `maturity_height`, `is_mature`, `blocks_until_mature`.
    """
    client = _make_client(monkeypatch)

    immature = _lookup_block(height=100, confirmations=10)
    mature = _lookup_block(height=200, confirmations=200, address="AZmature")
    hash_immature = immature["hash"]
    hash_mature = mature["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_immature: immature, hash_mature: mature},
        tip_height=500,
        tip_hash="6" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_immature}&blockhash={hash_mature}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()

    by_hash = {b["blockhash"]: b for b in body["blocks"]}
    assert body["stale_blockhashes"] == []

    im = by_hash[hash_immature]
    assert im["is_mature"] is False
    assert im["blocks_until_mature"] == 90
    # height + maturity_confirmations - 1 = 100 + 100 - 1 = 199
    assert im["maturity_height"] == 199
    assert im["coinbase_total_sats"] == 5_000_000_000

    mt = by_hash[hash_mature]
    assert mt["is_mature"] is True
    assert mt["blocks_until_mature"] == 0
    # 200 + 100 - 1 = 299
    assert mt["maturity_height"] == 299


def test_az_blocks_rewards_lookup_mode_works_without_ownership_configured(monkeypatch):
    """
    Lookup mode bypasses the AZ_REWARD_OWNERSHIP_NOT_CONFIGURED 503: the
    caller has explicitly named the blocks they want, so the absence of
    pool/reward-wallet configuration is irrelevant.
    """
    client = _make_client(monkeypatch)

    block = _lookup_block(height=110)
    hash_only = block["hash"]

    _install_lookup_blockhash_mock(
        monkeypatch,
        blocks_by_hash={hash_only: block},
        tip_height=500,
        tip_hash="5" * 64,
    )

    r = client.get(
        f"/v1/az/blocks/rewards?blockhash={hash_only}",
        headers=AUTH_HEADER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ownership_configured"] is False
    assert body["lookup_mode"] == "blockhashes"
    assert body["stale_blockhashes"] == []
    assert [b["blockhash"] for b in body["blocks"]] == [hash_only]
    # Classification fields are still present, just unmatched.
    assert body["blocks"][0]["is_owned_reward"] is False
    assert body["blocks"][0]["matched_output_indexes"] == []
    assert body["blocks"][0]["ownership_match"] is None


def test_az_blocks_rewards_lookup_mode_rpc_transport_failure_still_502(monkeypatch):
    """
    Transport-level RPC failure during a per-hash getblock surfaces as
    the standard 502 AZ_RPC_UNAVAILABLE; this is NOT silently converted
    into an unresolved entry.
    """
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_blocks as az_blocks_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {"chain": "main", "blocks": 500, "bestblockhash": "4" * 64}
        if method == "getblock":
            raise AzcoinRpcTransportError("network down")
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_blocks_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get(
        "/v1/az/blocks/rewards?blockhash=" + "a" * 64,
        headers=AUTH_HEADER,
    )
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_UNAVAILABLE"
