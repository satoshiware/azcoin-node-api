from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from node_api.services.bitcoin_rpc import BitcoinRPC, BitcoinRpcResponseError
from node_api.settings import get_settings


def get_btc_rpc() -> BitcoinRPC:
    """Return configured Bitcoin RPC client or raise BTC_RPC_NOT_CONFIGURED."""
    settings = get_settings()
    if not settings.btc_rpc_url or not settings.btc_rpc_user or not settings.btc_rpc_password:
        raise HTTPException(
            status_code=503,
            detail={"code": "BTC_RPC_NOT_CONFIGURED", "message": "Bitcoin RPC is not configured"},
        )
    return BitcoinRPC(
        url=settings.btc_rpc_url,
        user=settings.btc_rpc_user,
        password=settings.btc_rpc_password.get_secret_value(),
        timeout_seconds=settings.btc_rpc_timeout_seconds,
    )


def raise_btc_not_configured() -> None:
    raise HTTPException(
        status_code=503,
        detail={"code": "BTC_RPC_NOT_CONFIGURED", "message": "Bitcoin RPC is not configured"},
    )


def raise_btc_unavailable() -> None:
    raise HTTPException(
        status_code=502,
        detail={"code": "BTC_RPC_UNAVAILABLE", "message": "Bitcoin RPC unavailable"},
    )


def raise_wallet_unavailable() -> None:
    raise HTTPException(
        status_code=503,
        detail={"code": "BTC_WALLET_UNAVAILABLE", "message": "Bitcoin wallet unavailable"},
    )


def raise_invalid_since() -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "code": "BTC_INVALID_SINCE",
            "message": "Invalid 'since' blockhash; expected 64 hex characters.",
        },
    )


def raise_since_not_found() -> None:
    raise HTTPException(
        status_code=404,
        detail={
            "code": "BTC_SINCE_NOT_FOUND",
            "message": "Blockhash provided in 'since' was not found.",
        },
    )


def is_wallet_unavailable_error(exc: BitcoinRpcResponseError) -> bool:
    """Detect wallet disabled/not loaded/not found from RPC error."""
    if exc.code in {-19, -18}:
        return True
    message = (exc.message or "").lower()
    if "wallet" in message and (
        "disabled" in message or "not loaded" in message or "not found" in message
    ):
        return True
    return "wallet" in message and "does not exist" in message


def is_since_not_found_error(exc: BitcoinRpcResponseError) -> bool:
    """Detect block-not-found from RPC error."""
    if exc.code in {-5}:
        message = (exc.message or "").lower()
        return (
            "block not found" in message
            or "non-existent block hash" in message
            or "nonexistent block hash" in message
            or "invalid or non-existent block hash" in message
            or "invalid or nonexistent block hash" in message
        )
    message = (exc.message or "").lower()
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


def compute_balance_total(
    trusted: Any, untrusted_pending: Any, immature: Any
) -> int | float | None:
    trusted_num = _num_or_none(trusted)
    untrusted_num = _num_or_none(untrusted_pending)
    immature_num = _num_or_none(immature)
    if trusted_num is None or untrusted_num is None or immature_num is None:
        return None
    return trusted_num + untrusted_num + immature_num


def normalize_tx_time(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_tx(tx: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "txid": tx.get("txid"),
        "time": normalize_tx_time(tx.get("time")),
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


def normalize_peer(peer: dict[str, Any]) -> dict[str, Any]:
    """Normalize getpeerinfo item to stable subset."""
    normalized = {
        "id": peer.get("id"),
        "addr": peer.get("addr"),
        "inbound": peer.get("inbound"),
        "synced_headers": peer.get("synced_headers"),
        "synced_blocks": peer.get("synced_blocks"),
        "bytesrecv": peer.get("bytesrecv"),
        "bytessent": peer.get("bytessent"),
        "subver": peer.get("subver"),
        "version": peer.get("version"),
        "startingheight": peer.get("startingheight"),
    }
    if "addrlocal" in peer:
        normalized["addrlocal"] = peer.get("addrlocal")
    if "connection_type" in peer:
        normalized["connection_type"] = peer.get("connection_type")
    if "presynced_headers" in peer:
        normalized["presynced_headers"] = peer.get("presynced_headers")
    return normalized
