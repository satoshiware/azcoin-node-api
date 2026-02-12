from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from node_api.services.azcoin_rpc import (
    AzcoinRpcClient,
    AzcoinRpcError,
    AzcoinRpcResponseError,
    AzcoinRpcWrongChainError,
)
from node_api.settings import get_settings

router = APIRouter(prefix="/az/wallet", tags=["az-wallet"])


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


def _raise_wallet_unavailable() -> None:
    raise HTTPException(
        status_code=503,
        detail={"code": "AZ_WALLET_UNAVAILABLE", "message": "AZCoin wallet unavailable"},
    )


def _raise_invalid_since() -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "code": "AZ_INVALID_SINCE",
            "message": "Invalid 'since' blockhash; expected 64 hex characters.",
        },
    )


def _raise_since_not_found() -> None:
    raise HTTPException(
        status_code=404,
        detail={
            "code": "AZ_SINCE_NOT_FOUND",
            "message": "Blockhash provided in 'since' was not found.",
        },
    )


def _raise_wrong_chain(expected_chain: str) -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "code": "AZ_WRONG_CHAIN",
            "message": f"AZCoin RPC is on the wrong chain (expected '{expected_chain}').",
        },
    )


def _is_wallet_unavailable_error(exc: AzcoinRpcResponseError) -> bool:
    if exc.code in {-19, -18}:
        return True
    message = exc.message.lower()
    if "wallet" in message and (
        "disabled" in message or "not loaded" in message or "not found" in message
    ):
        return True
    return "wallet" in message and "does not exist" in message


def _is_since_not_found_error(exc: AzcoinRpcResponseError) -> bool:
    if exc.code in {-5}:
        message = exc.message.lower()
        return (
            "block not found" in message
            or "non-existent block hash" in message
            or "nonexistent block hash" in message
            or "invalid or non-existent block hash" in message
            or "invalid or nonexistent block hash" in message
        )

    message = exc.message.lower()
    return (
        "block not found" in message
        or "non-existent block hash" in message
        or "nonexistent block hash" in message
        or "invalid or non-existent block hash" in message
        or "invalid or nonexistent block hash" in message
    )


def _num_or_none(value: Any) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    return None


def _compute_total(trusted: Any, untrusted_pending: Any, immature: Any) -> int | float | None:
    trusted_num = _num_or_none(trusted)
    untrusted_num = _num_or_none(untrusted_pending)
    immature_num = _num_or_none(immature)
    if trusted_num is None or untrusted_num is None or immature_num is None:
        return None
    return trusted_num + untrusted_num + immature_num


def _normalize_tx_time(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@router.get("/summary")
def wallet_summary() -> dict:
    rpc = _get_az_rpc()

    try:
        wallet_info = rpc.call("getwalletinfo")
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcResponseError as exc:
        if exc.code == -32601 or _is_wallet_unavailable_error(exc):
            _raise_wallet_unavailable()
        _raise_az_unavailable()
    except AzcoinRpcError:
        _raise_az_unavailable()

    if not isinstance(wallet_info, dict):
        _raise_az_unavailable()

    balances_payload: dict[str, Any] | None = None
    try:
        balances_result = rpc.call("getbalances")
        if isinstance(balances_result, dict):
            balances_payload = balances_result
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcResponseError as exc:
        if exc.code == -32601:
            balances_payload = None
        elif _is_wallet_unavailable_error(exc):
            _raise_wallet_unavailable()
    except AzcoinRpcError:
        _raise_az_unavailable()

    trusted = wallet_info.get("balance")
    untrusted_pending = wallet_info.get("unconfirmed_balance")
    immature = wallet_info.get("immature_balance")

    if balances_payload:
        mine = balances_payload.get("mine")
        if isinstance(mine, dict):
            trusted = mine.get("trusted", trusted)
            untrusted_pending = mine.get("untrusted_pending", untrusted_pending)
            immature = mine.get("immature", immature)

    balances = {
        "trusted": trusted,
        "untrusted_pending": untrusted_pending,
        "immature": immature,
        "total": _compute_total(trusted, untrusted_pending, immature),
    }

    response = {
        "txcount": wallet_info.get("txcount"),
        "keypoolsize": wallet_info.get("keypoolsize"),
        "balances": balances,
    }
    if "walletname" in wallet_info:
        response["walletname"] = wallet_info.get("walletname")
    if "unlocked_until" in wallet_info:
        response["unlocked_until"] = wallet_info.get("unlocked_until")
    return response


def _normalize_tx(tx: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "txid": tx.get("txid"),
        "time": _normalize_tx_time(tx.get("time")),
        "confirmations": tx.get("confirmations"),
        "amount": tx.get("amount"),
        "category": tx.get("category"),
    }
    if "fee" in tx:
        normalized["fee"] = tx.get("fee")
    if "address" in tx:
        normalized["address"] = tx.get("address")
    if "blockhash" in tx:
        normalized["blockhash"] = tx.get("blockhash")
    return normalized


@router.get("/transactions")
def wallet_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    since: str | None = Query(
        default=None,
        description=(
            "Optional 64-hex blockhash. When provided, transactions are fetched "
            "via listsinceblock."
        ),
    ),
) -> list[dict[str, Any]]:
    rpc = _get_az_rpc()

    if since and not re.fullmatch(r"[0-9a-fA-F]{64}", since):
        _raise_invalid_since()

    try:
        if since:
            payload = rpc.call("listsinceblock", [since])
            if not isinstance(payload, dict):
                _raise_az_unavailable()
            transactions = payload.get("transactions")
            if not isinstance(transactions, list):
                transactions = []
        else:
            # Pull the endpoint max and apply the requested limit after
            # normalization + sorting to keep response behavior deterministic.
            transactions = rpc.call("listtransactions", ["*", 200, 0])
            if not isinstance(transactions, list):
                _raise_az_unavailable()
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcResponseError as exc:
        if since and _is_since_not_found_error(exc):
            _raise_since_not_found()
        if _is_wallet_unavailable_error(exc):
            _raise_wallet_unavailable()
        _raise_az_unavailable()
    except AzcoinRpcError:
        _raise_az_unavailable()

    normalized_txs = [_normalize_tx(tx) for tx in transactions if isinstance(tx, dict)]
    normalized_txs.sort(key=lambda tx: tx["time"], reverse=True)
    return normalized_txs[:limit]
