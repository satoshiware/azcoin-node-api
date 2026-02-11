from __future__ import annotations

from fastapi import APIRouter, HTTPException

from node_api.services.azcoin_rpc import AzcoinRpcClient, AzcoinRpcError
from node_api.settings import get_settings

router = APIRouter(prefix="/az/node", tags=["az-node"])

@router.get("/_debug/settings")
def debug_settings() -> dict:
    s = get_settings()
    return {
        "az_rpc_url": s.az_rpc_url,
        "az_rpc_user_set": bool(s.az_rpc_user),
        "az_rpc_password_set": bool(s.az_rpc_password),
        "az_rpc_password_len": len(s.az_rpc_password.get_secret_value()) if s.az_rpc_password else 0,
        "auth_mode": s.auth_mode,
    }


@router.get("/info")
def node_info() -> dict:
    settings = get_settings()
    if not settings.az_rpc_url or not settings.az_rpc_user or not settings.az_rpc_password:
        raise HTTPException(
            status_code=503,
            detail={"code": "AZ_RPC_NOT_CONFIGURED", "message": "AZCoin RPC is not configured"},
        )

    rpc = AzcoinRpcClient(
        url=settings.az_rpc_url,
        user=settings.az_rpc_user,
        password=settings.az_rpc_password.get_secret_value(),
        timeout_seconds=settings.az_rpc_timeout_seconds,
    )

    try:
        blockchain = rpc.call("getblockchaininfo")
        network = rpc.call("getnetworkinfo")
        mempool = rpc.call("getmempoolinfo")
    except AzcoinRpcError:
        raise HTTPException(
            status_code=502,
            detail={"code": "AZ_RPC_UNAVAILABLE", "message": "AZCoin RPC unavailable"},
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
    if not settings.az_rpc_url or not settings.az_rpc_user or not settings.az_rpc_password:
        raise HTTPException(
            status_code=503,
            detail={"code": "AZ_RPC_NOT_CONFIGURED", "message": "AZCoin RPC is not configured"},
        )

    rpc = AzcoinRpcClient(
        url=settings.az_rpc_url,
        user=settings.az_rpc_user,
        password=settings.az_rpc_password.get_secret_value(),
        timeout_seconds=settings.az_rpc_timeout_seconds,
    )

    try:
        return rpc.call("getblockchaininfo")
    except AzcoinRpcError as e:
        raise HTTPException(
            status_code=502,
            detail={"code": "AZ_RPC_UNAVAILABLE", "message": f"AZCoin RPC unavailable: {e}"},
        ) from None

