from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from node_api.services.azcoin_rpc import (
    AzcoinRpcClient,
    AzcoinRpcError,
    AzcoinRpcResponseError,
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

# For `time_field=time` we no longer walk by header time itself because block
# time is not monotonic enough to support safe early termination. Instead we
# anchor the scan to monotonic `mediantime` and still filter inclusion by
# `block["time"]`. The 2-hour slack mirrors Bitcoin/AZCoin header future-time
# tolerance, which is large enough to cover normal `time` vs `mediantime` skew
# while still keeping narrow operator windows bounded in practice.
_TIME_FIELD_TIME_ANCHOR_SLACK_SECS = 2 * 60 * 60

# String describing the time interval semantics; echoed in `time_filter` so
# clients (and ledger code) don't have to encode the rule separately.
_TIME_INTERVAL_RULE = "start_time <= selected_time < end_time"

# Hard cap on how many block hashes a single direct-lookup request may
# resolve. Each hash drives one getblock RPC call; without a cap a caller
# could pin the node by passing thousands of hashes. 500 covers realistic
# ledger reconciliation batches (a day of blocks fits well below this).
_MAX_BLOCKHASH_LOOKUP = 500

# Bitcoin/AZCoin block hashes are 32 bytes => 64 lowercase hex characters.
# We accept upper or mixed case on input and normalize to lowercase before
# dedupe + RPC dispatch so callers can paste hashes from any source.
_BLOCKHASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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


def _raise_blockhash_lookup_too_large() -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "code": "AZ_REWARD_BLOCKHASH_LOOKUP_TOO_LARGE",
            "message": (
                "Too many blockhashes requested. Per-request limit is "
                f"{_MAX_BLOCKHASH_LOOKUP}; split the lookup client-side."
            ),
        },
    )


def _raise_invalid_blockhash(value: str) -> None:
    # Truncate the echoed value so a giant pasted token can't bloat the error
    # body. The 80-char ceiling is comfortably above the 64-hex valid form.
    safe = value if len(value) <= 80 else value[:77] + "..."
    raise HTTPException(
        status_code=422,
        detail={
            "code": "AZ_REWARD_BLOCKHASH_INVALID",
            "message": (
                "blockhash must be exactly 64 hexadecimal characters: "
                f"{safe!r}"
            ),
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


def _scan_anchor_time(
    entry: dict[str, Any], time_field: Literal["time", "mediantime"]
) -> int | None:
    """Return the monotonic-ish time used only to bound the scan walk.

    `time_field=mediantime` uses `mediantime` directly, preserving the existing
    early-termination behavior. `time_field=time` still filters result inclusion
    by `block["time"]`, but bounds the traversal by `mediantime` so tight
    windows do not degrade into tip-to-genesis walks.
    """
    return _selected_block_time(entry, "mediantime")


def _scan_anchor_lower_bound(
    start_time: int, time_field: Literal["time", "mediantime"]
) -> int:
    """Return the lower bound that safely ends a downward time-window scan."""
    if time_field == "mediantime":
        return start_time
    return max(0, start_time - _TIME_FIELD_TIME_ANCHOR_SLACK_SECS)


def _is_lookup_mode_payable_main_chain(entry: dict[str, Any]) -> bool:
    """
    Return True only when a blockhash-lookup result is safe for ledger
    ingestion as active-chain reward truth.

    Stale / non-main-chain blocks (Core reports ``confirmations == -1`` for
    orphans, or missing/non-int confirmations) must be excluded from
    ``blocks[]``: they are not payable and not eligible for maturity.

    Rule (matches operator contract): exclude when
    ``confirmations <= 0`` (after normalizing to int), or when
    ``is_on_main_chain`` is not true.
    """
    conf = entry.get("confirmations")
    if not isinstance(conf, int) or isinstance(conf, bool):
        return False
    if conf <= 0:
        return False
    return entry.get("is_on_main_chain") is True


def _parse_lookup_blockhashes(
    blockhash_list: list[str] | None,
    blockhashes_csv: str | None,
) -> list[str]:
    """
    Combine repeated ``?blockhash=`` and CSV ``?blockhashes=`` query
    parameters into a single ordered, deduplicated, lowercase-normalized
    list of 64-hex block hashes.

    * Each entry must match :data:`_BLOCKHASH_RE` (exactly 64 hex chars).
      The first invalid entry raises 422 ``AZ_REWARD_BLOCKHASH_INVALID``.
    * Duplicates (case-insensitive) are dropped; first occurrence wins so
      callers see results in their requested order.
    * Empty entries (``""`` or ``" "``) are silently skipped, which makes
      ``blockhashes=h1,,h2`` and trailing commas tolerant for paste-friendly
      use without becoming a covert validation bypass.
    * If the deduplicated list exceeds :data:`_MAX_BLOCKHASH_LOOKUP`, raises
      422 ``AZ_REWARD_BLOCKHASH_LOOKUP_TOO_LARGE``.
    """
    raw: list[str] = []
    if blockhash_list:
        raw.extend(blockhash_list)
    if blockhashes_csv:
        raw.extend(blockhashes_csv.split(","))

    seen: set[str] = set()
    ordered: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped:
            continue
        if not _BLOCKHASH_RE.fullmatch(stripped):
            _raise_invalid_blockhash(stripped)
        normalized = stripped.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)

    if len(ordered) > _MAX_BLOCKHASH_LOOKUP:
        _raise_blockhash_lookup_too_large()
    return ordered


def _fetch_classified_block_entry_by_hash(
    rpc: AzcoinRpcClient,
    blockhash: str,
    owned_addresses: frozenset[str],
    owned_scripts: frozenset[str],
) -> tuple[Literal["unresolved", "stale", "ok"], dict[str, Any] | None]:
    """
    Look up a single block by its hash and classify the result for
    blockhash-lookup mode.

    Returns one of three states:

    * ``("unresolved", None)`` — the JSON-RPC layer reported an
      application error for *this specific hash* (e.g. Bitcoin Core
      ``-5 Block not found`` / invalid hash format / unknown block) or
      the returned payload is structurally unusable (not an object,
      missing height). Caller appends to ``unresolved_blockhashes``.
    * ``("stale", None)`` — ``getblock`` returned a usable object with a
      valid height and strict coinbase validation **succeeded**, but the
      block is **not** active-chain reward truth: ``confirmations <= 0``
      and/or ``is_on_main_chain`` is false (e.g. Core ``confirmations:
      -1`` for stale/orphan blocks). Caller appends to
      ``stale_blockhashes``. These hashes must **not** appear in
      ``blocks[]``, ``unresolved_blockhashes``, or
      ``filtered_out_blockhashes``.
    * ``("ok", entry)`` — payable main-chain block. Strict coinbase
      validation has run, ownership classification fields are attached,
      and the entry is safe to surface in ``blocks[]`` for ledger use.

    ``AzcoinRpcTransportError`` / ``AzcoinRpcHttpError`` /
    ``AzcoinRpcWrongChainError`` are intentionally propagated; they
    signal that the *transport* or *chain* is wrong, in which case the
    entire response should fail closed with the standard 502/503
    envelope rather than silently swallowing the failure.

    Strict coinbase validation runs **before** stale vs. payable
    classification. A malformed coinbase never becomes ``stale`` or
    ``unresolved``: it raises ``AZ_RPC_INVALID_PAYLOAD`` (502), including
    for orphan payloads where the node still returned a block object.
    Only blocks that pass validation and satisfy
    :func:`_is_lookup_mode_payable_main_chain` become ``("ok", entry)``.
    """
    try:
        block = rpc.call("getblock", [blockhash, 2])
    except AzcoinRpcResponseError:
        return ("unresolved", None)
    if not isinstance(block, dict):
        return ("unresolved", None)
    height = block.get("height")
    if not isinstance(height, int) or isinstance(height, bool) or height < 0:
        return ("unresolved", None)

    try:
        entry = _build_block_entry(height, block)
    except ValueError as exc:
        _raise_invalid_payload(f"block {blockhash}: {exc}")

    if not _is_lookup_mode_payable_main_chain(entry):
        return ("stale", None)

    is_owned, matched_indexes, ownership_match = _classify_block_ownership(
        entry["outputs"], owned_addresses, owned_scripts
    )
    entry["is_owned_reward"] = is_owned
    entry["matched_output_indexes"] = matched_indexes
    entry["ownership_match"] = ownership_match
    return ("ok", entry)


@router.get("/rewards")
def block_rewards(
    limit: int = Query(default=50, ge=1, le=200),
    owned_only: bool = Query(
        default=True,
        description=(
            "Scan-mode only. When true (default), return only blocks whose "
            "coinbase paid a configured AZ_REWARD_OWNERSHIP_* address or "
            "scriptPubKey hex; this is *configured coinbase/reward-wallet "
            "filtering*, not SC-node ownership identification (a shared "
            "pool wallet does not distinguish per-node ownership). When "
            "false, return every recent chain block with ownership "
            "classification fields populated for inspection. Ignored when "
            "`blockhash` / `blockhashes` are supplied."
        ),
    ),
    start_time: int | None = Query(
        default=None,
        ge=0,
        description=(
            "Inclusive lower bound (Unix seconds) for the selected block time "
            "field. Must be supplied together with end_time. Applies in "
            "scan mode and in blockhash-lookup mode."
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
    blockhash: list[str] | None = Query(
        default=None,
        description=(
            "Repeated query parameter for direct blockhash lookup, e.g. "
            "`?blockhash=<h1>&blockhash=<h2>`. Each value must be exactly "
            "64 hexadecimal characters. Activates blockhash-lookup mode "
            "(no height scan, `limit` ignored). May be combined with "
            "`blockhashes` (CSV) and with the optional time-window filter."
        ),
    ),
    blockhashes: str | None = Query(
        default=None,
        description=(
            "Comma-separated fallback for direct blockhash lookup. Equivalent "
            "to repeated `blockhash` parameters; the two forms may be mixed "
            "and are deduplicated together (case-insensitive, request-order "
            "preserving)."
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

    # ----- Phase 1b: blockhash lookup mode parsing --------------------------
    # Direct lookup overrides the height-walk modes entirely. We validate
    # and dedupe up-front so 422s land before any RPC call is made.
    lookup_hashes = _parse_lookup_blockhashes(blockhash, blockhashes)
    blockhash_lookup_mode = bool(lookup_hashes)

    # ----- Phase 2: ownership config + 503 fail-closed ----------------------
    # In blockhash-lookup mode the caller has already decided exactly which
    # blocks they want (typically from translator block-found events), so
    # we don't gate the lookup on ownership configuration: classification
    # fields are still populated when config is present, but `owned_only`
    # is treated as "off" for filtering purposes. This avoids returning a
    # confusing 503 when a ledger asks for a specific hash on a node where
    # AZ_REWARD_OWNERSHIP_* simply hasn't been wired up.
    settings = get_settings()
    owned_addresses = _parse_ownership_addresses(settings.az_reward_ownership_addresses)
    owned_scripts = _parse_ownership_scripts(settings.az_reward_ownership_script_pubkeys)
    ownership_configured = bool(owned_addresses or owned_scripts)

    if owned_only and not ownership_configured and not blockhash_lookup_mode:
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
    unresolved_blockhashes: list[str] = []
    stale_blockhashes: list[str] = []
    filtered_out_blockhashes: list[str] = []

    # ----- Phase 4: walk blocks ---------------------------------------------
    # Three scan modes:
    #   * Blockhash lookup: resolve each user-supplied hash with getblock(2);
    #     no height walk, `limit` ignored. Each hash is classified into
    #     unresolved / stale / ok before any time-window filter runs, so
    #     stale (non-payable) blocks never pollute blocks[] or
    #     filtered_out_blockhashes. Ledger consumers must use only blocks[].
    #   * Time-window: walk tip -> older heights, capped by
    #     _MAX_TIME_RANGE_SCAN_BLOCKS. `time_field=mediantime` preserves the
    #     existing exact early-termination rule. `time_field=time` now uses
    #     monotonic `mediantime` as the traversal anchor, while still filtering
    #     returned blocks by `block["time"]`.
    #   * Limit-based (legacy): walk tip -> tip-limit+1.
    # Strict coinbase validation runs on every blockhash lookup payload
    # that has a valid height; stale/orphan blocks that still fail
    # validation fail the whole request with AZ_RPC_INVALID_PAYLOAD. Once
    # validation passes, non-payable-main-chain blocks are listed only in
    # stale_blockhashes, never in blocks[].
    try:
        if blockhash_lookup_mode:
            for input_hash in lookup_hashes:
                status, entry = _fetch_classified_block_entry_by_hash(
                    rpc, input_hash, owned_addresses, owned_scripts
                )
                if status == "unresolved":
                    unresolved_blockhashes.append(input_hash)
                    continue
                if status == "stale":
                    stale_blockhashes.append(input_hash)
                    continue
                # status == "ok": entry is guaranteed non-None.
                assert entry is not None
                if time_window_mode:
                    assert start_time is not None and end_time is not None
                    selected_time = _selected_block_time(entry, time_field)
                    in_window = (
                        selected_time is not None
                        and start_time <= selected_time < end_time
                    )
                    if not in_window:
                        filtered_out_blockhashes.append(input_hash)
                        continue
                blocks.append(entry)
        elif time_window_mode:
            assert start_time is not None and end_time is not None  # narrowed for type checkers
            scanned = 0
            anchor_lower_bound = _scan_anchor_lower_bound(start_time, time_field)
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
                # Early termination uses a scan anchor that is monotonic enough
                # for a downward walk. For mediantime requests the anchor is the
                # selected time itself. For block-time requests we keep the
                # half-open inclusion test on `block["time"]`, but stop once
                # `mediantime` falls below the lower bound expanded by the
                # future-time slack above.
                anchor_time = _scan_anchor_time(entry, time_field)
                if anchor_time is not None and anchor_time < anchor_lower_bound:
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
        "lookup_mode": "blockhashes" if blockhash_lookup_mode else "scan",
        "requested_blockhash_count": len(lookup_hashes),
        "resolved_blockhash_count": len(blocks),
        "unresolved_blockhashes": unresolved_blockhashes,
        "stale_blockhashes": stale_blockhashes,
        "filtered_out_blockhashes": filtered_out_blockhashes,
        "time_filter": {
            "start_time": start_time,
            "end_time": end_time,
            "time_field": time_field,
            "interval_rule": _TIME_INTERVAL_RULE,
        },
        "blocks": blocks,
    }
