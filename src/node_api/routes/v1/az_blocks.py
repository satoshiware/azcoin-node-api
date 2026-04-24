from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from node_api.services.azcoin_rpc import (
    AzcoinRpcClient,
    AzcoinRpcError,
    AzcoinRpcWrongChainError,
)
from node_api.settings import get_settings

router = APIRouter(prefix="/az/blocks", tags=["az-blocks"])

# 1 AZC = 100_000_000 sats. Kept local to avoid leaking a protocol constant
# into shared modules; this route is the only place we convert coin->sats.
_COIN = Decimal("100000000")
_MATURITY_CONFIRMATIONS = 100


def _get_az_rpc() -> AzcoinRpcClient:
    settings = get_settings()
    if not settings.az_rpc_url or not settings.az_rpc_user or not settings.az_rpc_password:
        raise HTTPException(
            status_code=503,
            detail={"code": "AZ_RPC_NOT_CONFIGURED", "message": "AZCoin RPC is not configured"},
        )

    return AzcoinRpcClient(
        url=settings.az_rpc_url,
        user=settings.az_rpc_user,
        password=settings.az_rpc_password.get_secret_value(),
        timeout_seconds=settings.az_rpc_timeout_seconds,
        expected_chain=settings.az_expected_chain,
    )


def _raise_az_unavailable() -> None:
    raise HTTPException(
        status_code=502,
        detail={"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"},
    )


def _raise_wrong_chain(expected_chain: str) -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "code": "AZ_WRONG_CHAIN",
            "message": f"AZCoin RPC is on the wrong chain (expected '{expected_chain}').",
        },
    )


def _raise_invalid_payload(message: str) -> None:
    raise HTTPException(
        status_code=502,
        detail={
            "code": "AZ_RPC_INVALID_PAYLOAD",
            "message": f"AZCoin RPC payload invalid: {message}",
        },
    )


def _coin_to_sats_strict(value: Any) -> int:
    """
    Convert a coin amount to integer sats with no rounding tolerance.

    Going through Decimal(str(value)) avoids binary float artifacts
    (e.g. 0.1 -> 0.10000000000000000555...) so values that look exact in
    JSON-RPC output land on the exact sat boundary.

    Raises ValueError when the value is missing, null, non-numeric,
    non-finite, negative, or carries sub-satoshi precision.
    """
    if value is None or isinstance(value, bool):
        raise ValueError("missing or null value")
    if not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("non-numeric value")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("non-numeric value") from exc
    if not amount.is_finite():
        raise ValueError("non-finite value")
    if amount < 0:
        raise ValueError("negative value")
    sats = amount * _COIN
    if sats != sats.to_integral_value():
        raise ValueError("sub-satoshi precision")
    return int(sats)


def _maturity_status(confirmations: Any) -> str:
    if not isinstance(confirmations, int) or isinstance(confirmations, bool):
        return "unknown"
    return "mature" if confirmations >= _MATURITY_CONFIRMATIONS else "immature"


def _extract_script_type(vout: dict[str, Any]) -> str | None:
    script_pub_key = vout.get("scriptPubKey")
    if not isinstance(script_pub_key, dict):
        return None
    script_type = script_pub_key.get("type")
    return script_type if isinstance(script_type, str) else None


def _extract_address(vout: dict[str, Any]) -> str | None:
    script_pub_key = vout.get("scriptPubKey")
    if not isinstance(script_pub_key, dict):
        return None
    address = script_pub_key.get("address")
    if isinstance(address, str):
        return address
    # Older Core versions expose a list under `addresses`; take the first if singular.
    addresses = script_pub_key.get("addresses")
    if isinstance(addresses, list) and len(addresses) == 1 and isinstance(addresses[0], str):
        return addresses[0]
    return None


def _extract_script_pub_key_hex(vout: dict[str, Any]) -> str | None:
    script_pub_key = vout.get("scriptPubKey")
    if not isinstance(script_pub_key, dict):
        return None
    hex_value = script_pub_key.get("hex")
    return hex_value if isinstance(hex_value, str) else None


def _normalize_coinbase_outputs(
    coinbase_tx: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    """
    Walk the coinbase tx vout list and produce normalized outputs plus total sats.

    Strict mode: any missing/null/invalid/negative/sub-satoshi value, any
    non-object vout entry, or an empty/missing vout list raises ValueError so
    the caller can surface AZ_RPC_INVALID_PAYLOAD instead of returning a
    partial/zeroed reward total that downstream ledgers could mistake for truth.
    """
    vouts = coinbase_tx.get("vout")
    if not isinstance(vouts, list) or not vouts:
        raise ValueError("coinbase has no vout outputs")

    outputs: list[dict[str, Any]] = []
    total_sats = 0
    for idx, vout in enumerate(vouts):
        if not isinstance(vout, dict):
            raise ValueError(f"coinbase vout[{idx}] is not an object")
        try:
            value_sats = _coin_to_sats_strict(vout.get("value"))
        except ValueError as exc:
            raise ValueError(f"coinbase vout[{idx}]: {exc}") from exc
        # Prefer the explicit `n` field when present; fall back to list index.
        n = vout.get("n")
        index = n if isinstance(n, int) and not isinstance(n, bool) else idx
        outputs.append(
            {
                "index": index,
                "value_sats": value_sats,
                "address": _extract_address(vout),
                "script_type": _extract_script_type(vout),
                "script_pub_key_hex": _extract_script_pub_key_hex(vout),
            }
        )
        total_sats += value_sats
    return outputs, total_sats


def _normalize_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _build_block_entry(height: int, block: dict[str, Any]) -> dict[str, Any]:
    txs = block.get("tx")
    if not isinstance(txs, list) or not txs or not isinstance(txs[0], dict):
        raise ValueError("missing coinbase transaction")
    coinbase_tx = txs[0]

    outputs, coinbase_total_sats = _normalize_coinbase_outputs(coinbase_tx)

    confirmations = block.get("confirmations")
    confirmations_int = _normalize_int(confirmations)
    if confirmations_int is not None and confirmations_int >= 0:
        is_mature = confirmations_int >= _MATURITY_CONFIRMATIONS
        blocks_until_mature: int | None = max(0, _MATURITY_CONFIRMATIONS - confirmations_int)
    else:
        is_mature = False
        blocks_until_mature = None

    return {
        "height": height,
        "blockhash": block.get("hash"),
        "confirmations": confirmations_int,
        "time": _normalize_int(block.get("time")),
        "mediantime": _normalize_int(block.get("mediantime")),
        # Active-chain blocks report confirmations >= 0 (>=1 in practice).
        # Bitcoin Core uses -1 for stale/orphan blocks; missing/null/non-int is
        # treated as unknown and fails closed to false so callers never assume
        # ledger truth from indeterminate state.
        "is_on_main_chain": confirmations_int is not None and confirmations_int >= 0,
        "is_mature": is_mature,
        "blocks_until_mature": blocks_until_mature,
        "maturity_status": _maturity_status(confirmations),
        "coinbase_txid": coinbase_tx.get("txid"),
        "coinbase_total_sats": coinbase_total_sats,
        "outputs": outputs,
    }


@router.get("/rewards")
def block_rewards(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rpc = _get_az_rpc()

    try:
        blockchain = rpc.call("getblockchaininfo")
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    if not isinstance(blockchain, dict):
        _raise_az_unavailable()

    tip_height = blockchain.get("blocks")
    chain = blockchain.get("chain")
    tip_hash = blockchain.get("bestblockhash")
    if not isinstance(tip_height, int) or isinstance(tip_height, bool) or tip_height < 0:
        _raise_az_unavailable()

    # Walk tip -> genesis, capped by `limit` and bounded by height 0.
    lowest = max(0, tip_height - limit + 1)
    blocks: list[dict[str, Any]] = []
    try:
        for height in range(tip_height, lowest - 1, -1):
            blockhash = rpc.call("getblockhash", [height])
            if not isinstance(blockhash, str):
                _raise_az_unavailable()
            block = rpc.call("getblock", [blockhash, 2])
            if not isinstance(block, dict):
                _raise_az_unavailable()
            try:
                blocks.append(_build_block_entry(height, block))
            except ValueError as exc:
                _raise_invalid_payload(f"block {height}: {exc}")
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    return {
        "tip_height": tip_height,
        "tip_hash": tip_hash if isinstance(tip_hash, str) else None,
        "chain": chain if isinstance(chain, str) else None,
        "maturity_confirmations": _MATURITY_CONFIRMATIONS,
        "blocks": blocks,
    }
