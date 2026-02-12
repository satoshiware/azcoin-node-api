from __future__ import annotations

from fastapi import APIRouter

from node_api.services.azcoin_rpc import AzcoinRpcClient, AzcoinRpcError, AzcoinRpcWrongChainError
from node_api.services.bitcoin_rpc import BitcoinRpcClient, BitcoinRpcError
from node_api.settings import get_settings

router = APIRouter(prefix="/node", tags=["node"])


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

    rpc = BitcoinRpcClient(
        url=settings.btc_rpc_url,
        user=settings.btc_rpc_user,
        password=settings.btc_rpc_password.get_secret_value(),
        timeout_seconds=settings.btc_rpc_timeout_seconds,
    )

    try:
        return _trim_blockchain_info(rpc.call("getblockchaininfo")), None
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
