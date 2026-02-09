from __future__ import annotations

from fastapi import APIRouter, HTTPException

from node_api.services.bitcoin_rpc import BitcoinRpcClient, BitcoinRpcError
from node_api.settings import get_settings

router = APIRouter(prefix="/btc/node", tags=["btc-node"])


@router.get("/info")
def node_info() -> dict:
    settings = get_settings()
    if not settings.btc_rpc_url or not settings.btc_rpc_user or not settings.btc_rpc_password:
        raise HTTPException(
            status_code=503,
            detail={"code": "BTC_RPC_NOT_CONFIGURED", "message": "Bitcoin RPC is not configured"},
        )

    rpc = BitcoinRpcClient(
        url=settings.btc_rpc_url,
        user=settings.btc_rpc_user,
        password=settings.btc_rpc_password.get_secret_value(),
        timeout_seconds=settings.btc_rpc_timeout_seconds,
    )

    try:
        blockchain = rpc.call("getblockchaininfo")
        network = rpc.call("getnetworkinfo")
        mempool = rpc.call("getmempoolinfo")
    except BitcoinRpcError:
        raise HTTPException(
            status_code=502,
            detail={"code": "BTC_RPC_UNAVAILABLE", "message": "Bitcoin RPC unavailable"},
        ) from None

    return {
        "chain": blockchain.get("chain"),
        "blocks": blockchain.get("blocks"),
        "headers": blockchain.get("headers"),
        "verificationprogress": blockchain.get("verificationprogress"),
        "difficulty": blockchain.get("difficulty"),
        "connections": network.get("connections"),
        "subversion": network.get("subversion"),
        "protocolversion": network.get("protocolversion"),
        "mempool": {"size": mempool.get("size"), "bytes": mempool.get("bytes")},
    }


@router.get("/blockchain-info")
def blockchain_info() -> dict:
    settings = get_settings()
    if not settings.btc_rpc_url or not settings.btc_rpc_user or not settings.btc_rpc_password:
        raise HTTPException(
            status_code=503,
            detail={"code": "BTC_RPC_NOT_CONFIGURED", "message": "Bitcoin RPC is not configured"},
        )

    rpc = BitcoinRpcClient(
        url=settings.btc_rpc_url,
        user=settings.btc_rpc_user,
        password=settings.btc_rpc_password.get_secret_value(),
        timeout_seconds=settings.btc_rpc_timeout_seconds,
    )

    try:
        return rpc.call("getblockchaininfo")
    except BitcoinRpcError:
        raise HTTPException(
            status_code=502,
            detail={"code": "BTC_RPC_UNAVAILABLE", "message": "Bitcoin RPC unavailable"},
        ) from None
