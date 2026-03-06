from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from node_api.services.bitcoin_rpc import (
    BitcoinRpcError,
    BitcoinRpcResponseError,
)
from node_api.services.btc_route_helpers import (
    compute_balance_total,
    get_btc_rpc,
    is_since_not_found_error,
    is_wallet_unavailable_error,
    normalize_tx,
    raise_btc_unavailable,
    raise_invalid_since,
    raise_since_not_found,
    raise_wallet_unavailable,
)

router = APIRouter(prefix="/btc/wallet", tags=["btc-wallet"])


@router.get("/summary")
def wallet_summary() -> dict[str, Any]:
    rpc = get_btc_rpc()
    try:
        wallet_info = rpc.call_dict("getwalletinfo")
    except BitcoinRpcResponseError as exc:
        if exc.code == -32601 or is_wallet_unavailable_error(exc):
            raise_wallet_unavailable()
        raise_btc_unavailable()
    except BitcoinRpcError:
        raise_btc_unavailable()

    balances_payload: dict[str, Any] | None = None
    try:
        balances_result = rpc.call("getbalances")
        if isinstance(balances_result, dict):
            balances_payload = balances_result
    except BitcoinRpcResponseError as exc:
        if exc.code == -32601:
            balances_payload = None
        elif is_wallet_unavailable_error(exc):
            raise_wallet_unavailable()
        else:
            raise_btc_unavailable()
    except BitcoinRpcError:
        raise_btc_unavailable()

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
        "total": compute_balance_total(trusted, untrusted_pending, immature),
    }

    response: dict[str, Any] = {
        "txcount": wallet_info.get("txcount"),
        "keypoolsize": wallet_info.get("keypoolsize"),
        "balances": balances,
    }
    if "walletname" in wallet_info:
        response["walletname"] = wallet_info.get("walletname")
    if "unlocked_until" in wallet_info:
        response["unlocked_until"] = wallet_info.get("unlocked_until")
    return response


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
    if since and not re.fullmatch(r"[0-9a-fA-F]{64}", since):
        raise_invalid_since()

    rpc = get_btc_rpc()
    try:
        if since:
            payload = rpc.call("listsinceblock", [since])
            if not isinstance(payload, dict):
                raise_btc_unavailable()
            transactions = payload.get("transactions")
            if not isinstance(transactions, list):
                transactions = []
        else:
            transactions = rpc.call("listtransactions", ["*", 200, 0])
            if not isinstance(transactions, list):
                raise_btc_unavailable()
    except BitcoinRpcResponseError as exc:
        if since and is_since_not_found_error(exc):
            raise_since_not_found()
        if is_wallet_unavailable_error(exc):
            raise_wallet_unavailable()
        raise_btc_unavailable()
    except BitcoinRpcError:
        raise_btc_unavailable()

    normalized_txs = [normalize_tx(tx) for tx in transactions if isinstance(tx, dict)]
    normalized_txs.sort(key=lambda tx: tx["time"], reverse=True)
    return normalized_txs[:limit]
