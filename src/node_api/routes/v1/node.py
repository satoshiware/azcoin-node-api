from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from node_api.services.azcoin_rpc import (
    AzcoinRpcClient,
    AzcoinRpcError,
    AzcoinRpcWrongChainError,
)
from node_api.services.bitcoin_rpc import BitcoinRPC, BitcoinRpcError
from node_api.settings import get_settings

router = APIRouter(prefix="/node", tags=["node"])


def _status_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_envelope(status: str, data: dict[str, Any], detail: Any) -> dict[str, Any]:
    return {"status": status, "data": data, "detail": detail}


def _empty_node_status_data(*, last_updated_ts: str | None = None) -> dict[str, Any]:
    return {
        "chain": None,
        "network": None,
        "blocks": None,
        "headers": None,
        "best_block_hash": None,
        "difficulty": None,
        "initial_block_download": None,
        "synced": None,
        "verification_progress": None,
        "peer_count": None,
        "mempool_tx_count": None,
        "warnings": None,
        "last_updated_ts": last_updated_ts or _status_timestamp(),
    }


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _warnings_or_none(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        warning = value.strip()
        return [warning] if warning else []
    if isinstance(value, list):
        warnings = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return warnings
    return None


def _derive_synced(blockchain: dict[str, Any]) -> bool | None:
    initial_block_download = _bool_or_none(blockchain.get("initialblockdownload"))
    blocks = _number_or_none(blockchain.get("blocks"))
    headers = _number_or_none(blockchain.get("headers"))
    verification_progress = _number_or_none(blockchain.get("verificationprogress"))

    if initial_block_download is True:
        return False

    if blocks is not None and headers is not None and blocks < headers:
        return False

    if verification_progress is not None and verification_progress < 0.999:
        return False

    if initial_block_download is False:
        if verification_progress is None and (blocks is None or headers is None):
            return None
        return True

    if verification_progress is not None and verification_progress >= 0.999:
        if blocks is None or headers is None or blocks >= headers:
            return True

    return None


def _fetch_az_status_payload() -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    settings = get_settings()
    if not settings.az_rpc_url or not settings.az_rpc_user or not settings.az_rpc_password:
        return None, {
            "code": "AZ_RPC_NOT_CONFIGURED",
            "message": "AZCoin RPC is not configured",
        }

    rpc = AzcoinRpcClient(
        url=settings.az_rpc_url,
        user=settings.az_rpc_user,
        password=settings.az_rpc_password.get_secret_value(),
        timeout_seconds=settings.az_rpc_timeout_seconds,
        expected_chain=settings.az_expected_chain,
    )

    try:
        blockchain = rpc.call("getblockchaininfo")
        network = rpc.call("getnetworkinfo")
        mempool = rpc.call("getmempoolinfo")
    except AzcoinRpcWrongChainError as exc:
        return None, {
            "code": "AZ_WRONG_CHAIN",
            "message": f"AZCoin RPC is on the wrong chain (expected '{exc.expected_chain}').",
        }
    except AzcoinRpcError:
        return None, {"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"}

    if (
        not isinstance(blockchain, dict)
        or not isinstance(network, dict)
        or not isinstance(mempool, dict)
    ):
        return None, {"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"}

    data = _empty_node_status_data()
    data.update(
        {
            "chain": _str_or_none(blockchain.get("chain")),
            "network": _str_or_none(network.get("network")),
            "blocks": _number_or_none(blockchain.get("blocks")),
            "headers": _number_or_none(blockchain.get("headers")),
            "best_block_hash": _str_or_none(blockchain.get("bestblockhash")),
            "difficulty": _number_or_none(blockchain.get("difficulty")),
            "initial_block_download": _bool_or_none(blockchain.get("initialblockdownload")),
            "synced": _derive_synced(blockchain),
            "verification_progress": _number_or_none(blockchain.get("verificationprogress")),
            "peer_count": _number_or_none(network.get("connections")),
            "mempool_tx_count": _number_or_none(mempool.get("size")),
            "warnings": _warnings_or_none(network.get("warnings")),
        }
    )
    return data, None


def _trim_blockchain_info(blockchain: dict) -> dict:
    return {
        "chain": blockchain.get("chain"),
        "blocks": blockchain.get("blocks"),
        "headers": blockchain.get("headers"),
        "verificationprogress": blockchain.get("verificationprogress"),
        "difficulty": blockchain.get("difficulty"),
    }


def _fetch_az_blockchain_info() -> tuple[dict | None, dict | None]:
    settings = get_settings()
    if not settings.az_rpc_url or not settings.az_rpc_user or not settings.az_rpc_password:
        return None, {
            "code": "AZ_RPC_NOT_CONFIGURED",
            "message": "AZCoin RPC is not configured",
        }

    rpc = AzcoinRpcClient(
        url=settings.az_rpc_url,
        user=settings.az_rpc_user,
        password=settings.az_rpc_password.get_secret_value(),
        timeout_seconds=settings.az_rpc_timeout_seconds,
        expected_chain=settings.az_expected_chain,
    )

    try:
        return _trim_blockchain_info(rpc.call("getblockchaininfo")), None
    except AzcoinRpcWrongChainError as exc:
        return None, {
            "code": "AZ_WRONG_CHAIN",
            "message": f"AZCoin RPC is on the wrong chain (expected '{exc.expected_chain}').",
        }
    except AzcoinRpcError:
        return None, {"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"}


def _fetch_btc_blockchain_info() -> tuple[dict | None, dict | None]:
    settings = get_settings()
    if not settings.btc_rpc_url or not settings.btc_rpc_user or not settings.btc_rpc_password:
        return None, {
            "code": "BTC_RPC_NOT_CONFIGURED",
            "message": "Bitcoin RPC is not configured",
        }

    rpc = BitcoinRPC(
        url=settings.btc_rpc_url,
        user=settings.btc_rpc_user,
        password=settings.btc_rpc_password.get_secret_value(),
        timeout_seconds=settings.btc_rpc_timeout_seconds,
    )

    try:
        return _trim_blockchain_info(rpc.call_dict("getblockchaininfo")), None
    except BitcoinRpcError:
        return None, {"code": "BTC_RPC_UNAVAILABLE", "message": "Bitcoin RPC unavailable"}


@router.get("/summary")
def node_summary() -> dict:
    az_data, az_error = _fetch_az_blockchain_info()
    btc_data, btc_error = _fetch_btc_blockchain_info()

    status = "ok" if not az_error and not btc_error else "degraded"
    return {
        "status": status,
        "az": az_data if az_error is None else {"error": az_error},
        "btc": btc_data if btc_error is None else {"error": btc_error},
    }


@router.get("/status")
def node_status() -> dict:
    data, detail = _fetch_az_status_payload()
    if detail is not None:
        return _status_envelope("error", _empty_node_status_data(), detail)

    assert data is not None

    status = "ok"
    if data["synced"] is False:
        status = "degraded"
    elif isinstance(data["warnings"], list) and data["warnings"]:
        status = "degraded"

    return _status_envelope(status, data, None)
