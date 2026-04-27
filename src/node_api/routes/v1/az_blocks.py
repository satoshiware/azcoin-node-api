from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

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

# Ownership match labels. The combined value is emitted when at least one
# coinbase output matched by address AND at least one matched by scriptPubKey
# (the two matches may be on the same output or on different outputs).
_OWNERSHIP_MATCH_ADDRESS = "coinbase_output_address"
_OWNERSHIP_MATCH_SCRIPT = "coinbase_script_pub_key"
_OWNERSHIP_MATCH_BOTH = "coinbase_output_address_and_script_pub_key"

# Hard cap on how many blocks a single time-window query may walk. Bitcoin/
# AZCoin block headers don't carry a back-index by time, so a time-windowed
# request must scan tip -> genesis (or until early termination). Without a cap,
# a tight window deep in the past would walk the whole chain. 5000 is roughly
# 7 weeks of 10-minute blocks; queries that need more must be split client-side.
_MAX_TIME_RANGE_SCAN_BLOCKS = 5000

# String describing the time interval semantics; echoed in `time_filter` so
# clients (and ledger code) don't have to encode the rule separately.
_TIME_INTERVAL_RULE = "start_time <= selected_time < end_time"


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


def _raise_ownership_not_configured() -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "code": "AZ_REWARD_OWNERSHIP_NOT_CONFIGURED",
            "message": "Reward ownership matching is not configured.",
        },
    )


def _raise_time_range_too_large() -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "code": "AZ_REWARD_TIME_RANGE_TOO_LARGE",
            "message": (
                "Time range scan exceeded the per-request limit of "
                f"{_MAX_TIME_RANGE_SCAN_BLOCKS} blocks. Narrow the interval, "
                "use time_field=mediantime to enable early termination, or "
                "split the request client-side."
            ),
        },
    )


def _raise_time_range_incomplete() -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "code": "AZ_REWARD_TIME_RANGE_INCOMPLETE",
            "message": "start_time and end_time must both be provided.",
        },
    )


def _raise_time_range_invalid() -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "code": "AZ_REWARD_TIME_RANGE_INVALID",
            "message": "end_time must be strictly greater than start_time.",
        },
    )


def _parse_ownership_addresses(raw: str | None) -> frozenset[str]:
    """Comma-separated addresses, whitespace-trimmed, empty entries dropped, exact match."""
    if not raw:
        return frozenset()
    return frozenset(piece.strip() for piece in raw.split(",") if piece.strip())


def _parse_ownership_scripts(raw: str | None) -> frozenset[str]:
    """Comma-separated scriptPubKey hex strings; case-insensitive match (lowercased)."""
    if not raw:
        return frozenset()
    return frozenset(piece.strip().lower() for piece in raw.split(",") if piece.strip())


def _classify_block_ownership(
    outputs: list[dict[str, Any]],
    owned_addresses: frozenset[str],
    owned_scripts: frozenset[str],
) -> tuple[bool, list[int], str | None]:
    """
    Inspect normalized coinbase outputs and report:
        (is_owned_reward, matched_output_indexes, ownership_match)

    An output matches if its `address` is in `owned_addresses` OR its
    `script_pub_key_hex` (compared lowercased) is in `owned_scripts`.
    """
    matched_indexes: list[int] = []
    had_address_match = False
    had_script_match = False

    for output in outputs:
        address = output.get("address")
        script_hex = output.get("script_pub_key_hex")
        addr_match = isinstance(address, str) and address in owned_addresses
        script_match = (
            isinstance(script_hex, str) and script_hex.lower() in owned_scripts
        )
        if addr_match or script_match:
            index = output.get("index")
            if isinstance(index, int) and not isinstance(index, bool):
                matched_indexes.append(index)
            if addr_match:
                had_address_match = True
            if script_match:
                had_script_match = True

    if had_address_match and had_script_match:
        ownership_match: str | None = _OWNERSHIP_MATCH_BOTH
    elif had_address_match:
        ownership_match = _OWNERSHIP_MATCH_ADDRESS
    elif had_script_match:
        ownership_match = _OWNERSHIP_MATCH_SCRIPT
    else:
        ownership_match = None

    return bool(matched_indexes), matched_indexes, ownership_match


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
        # The chain height at which this coinbase first becomes spendable
        # (i.e. when its confirmations reach _MATURITY_CONFIRMATIONS). Derived
        # purely from `height` so it is independent of `confirmations`: an
        # immature, mature, or even orphan block all report the same value.
        "maturity_height": height + _MATURITY_CONFIRMATIONS - 1,
        "coinbase_txid": coinbase_tx.get("txid"),
        "coinbase_total_sats": coinbase_total_sats,
        "outputs": outputs,
    }


def _fetch_classified_block_entry(
    rpc: AzcoinRpcClient,
    height: int,
    owned_addresses: frozenset[str],
    owned_scripts: frozenset[str],
) -> dict[str, Any]:
    """
    Fetch a single block by height, run strict coinbase validation, attach
    ownership classification fields, and return the full per-block entry.

    AzcoinRpcError / AzcoinRpcWrongChainError raised by the RPC client are
    intentionally propagated; the caller's outer try/except converts them to
    the route's standard 502/503 responses.
    """
    blockhash = rpc.call("getblockhash", [height])
    if not isinstance(blockhash, str):
        _raise_az_unavailable()
    block = rpc.call("getblock", [blockhash, 2])
    if not isinstance(block, dict):
        _raise_az_unavailable()
    try:
        entry = _build_block_entry(height, block)
    except ValueError as exc:
        _raise_invalid_payload(f"block {height}: {exc}")
    is_owned, matched_indexes, ownership_match = _classify_block_ownership(
        entry["outputs"], owned_addresses, owned_scripts
    )
    entry["is_owned_reward"] = is_owned
    entry["matched_output_indexes"] = matched_indexes
    entry["ownership_match"] = ownership_match
    return entry


def _selected_block_time(
    entry: dict[str, Any], time_field: Literal["time", "mediantime"]
) -> int | None:
    """Return the int time for the active filter mode, or None when absent/non-int."""
    value = entry.get(time_field)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


@router.get("/rewards")
def block_rewards(
    limit: int = Query(default=50, ge=1, le=200),
    owned_only: bool = Query(
        default=True,
        description=(
            "When true (default), return only blocks whose coinbase paid a "
            "configured ownership address or scriptPubKey hex. When false, "
            "return every recent chain block with ownership classification "
            "fields populated for inspection."
        ),
    ),
    start_time: int | None = Query(
        default=None,
        ge=0,
        description=(
            "Inclusive lower bound (Unix seconds) for the selected block time "
            "field. Must be supplied together with end_time; otherwise the "
            "endpoint falls back to limit-based scanning."
        ),
    ),
    end_time: int | None = Query(
        default=None,
        description=(
            "Exclusive upper bound (Unix seconds) for the selected block time "
            "field. Must be supplied together with start_time and must be "
            "strictly greater than start_time."
        ),
    ),
    time_field: Literal["time", "mediantime"] = Query(
        default="time",
        description=(
            "Which block timestamp drives interval filtering: the block "
            "header `time` (default) or BIP113 `mediantime` (monotonic on "
            "the active chain; enables early scan termination)."
        ),
    ),
) -> dict[str, Any]:
    # ----- Phase 1: cross-field validation of time-window params ------------
    # `Query(ge=0)` already covers start_time>=0 and Literal already covers
    # time_field. The remaining rules ("both or neither" and end>start) are
    # cross-field, which Query can't express, so we raise 422 here with our
    # standard {code, message} envelope used elsewhere in this module.
    time_window_mode = start_time is not None or end_time is not None
    if time_window_mode and (start_time is None or end_time is None):
        _raise_time_range_incomplete()
    if time_window_mode and end_time is not None and start_time is not None:
        if end_time <= start_time:
            _raise_time_range_invalid()

    # ----- Phase 2: ownership config + 503 fail-closed ----------------------
    settings = get_settings()
    owned_addresses = _parse_ownership_addresses(settings.az_reward_ownership_addresses)
    owned_scripts = _parse_ownership_scripts(settings.az_reward_ownership_script_pubkeys)
    ownership_configured = bool(owned_addresses or owned_scripts)

    if owned_only and not ownership_configured:
        _raise_ownership_not_configured()

    # ----- Phase 3: fetch tip metadata --------------------------------------
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

    blocks: list[dict[str, Any]] = []

    # ----- Phase 4: walk blocks ---------------------------------------------
    # Two scan modes:
    #   * Time-window: walk tip -> genesis, capped by _MAX_TIME_RANGE_SCAN_BLOCKS,
    #     with optional early termination for time_field=="mediantime".
    #   * Limit-based (legacy): walk tip -> tip-limit+1.
    # Strict coinbase validation runs unconditionally on every fetched block
    # so callers can never receive a partial/zeroed reward total.
    try:
        if time_window_mode:
            assert start_time is not None and end_time is not None  # narrowed for type checkers
            scanned = 0
            for height in range(tip_height, -1, -1):
                scanned += 1
                if scanned > _MAX_TIME_RANGE_SCAN_BLOCKS:
                    _raise_time_range_too_large()
                entry = _fetch_classified_block_entry(
                    rpc, height, owned_addresses, owned_scripts
                )
                selected_time = _selected_block_time(entry, time_field)
                in_window = (
                    selected_time is not None
                    and start_time <= selected_time < end_time
                )
                ownership_passes = entry["is_owned_reward"] or not owned_only
                if in_window and ownership_passes:
                    blocks.append(entry)
                # Early termination is only safe for `mediantime`: BIP113
                # mediantime is non-decreasing on the active chain, so once
                # we observe a block strictly below start_time no earlier
                # block can possibly fall back inside the window. Header
                # `time` carries up to ~2h drift per BIP113 and is therefore
                # not safe to short-circuit on.
                if (
                    time_field == "mediantime"
                    and selected_time is not None
                    and selected_time < start_time
                ):
                    break
        else:
            lowest = max(0, tip_height - limit + 1)
            # `limit` is a fetch cap, not a result cap; when `owned_only=true`
            # the response can contain fewer blocks than `limit` if some are
            # unowned.
            for height in range(tip_height, lowest - 1, -1):
                entry = _fetch_classified_block_entry(
                    rpc, height, owned_addresses, owned_scripts
                )
                if owned_only and not entry["is_owned_reward"]:
                    continue
                blocks.append(entry)
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    return {
        "tip_height": tip_height,
        "tip_hash": tip_hash if isinstance(tip_hash, str) else None,
        "chain": chain if isinstance(chain, str) else None,
        "maturity_confirmations": _MATURITY_CONFIRMATIONS,
        "owned_only": owned_only,
        "ownership_configured": ownership_configured,
        "time_filter": {
            "start_time": start_time,
            "end_time": end_time,
            "time_field": time_field,
            "interval_rule": _TIME_INTERVAL_RULE,
        },
        "blocks": blocks,
    }
