from __future__ import annotations

from fastapi import APIRouter, HTTPException

from node_api.services.azcoin_rpc import (
    AzcoinRpcClient,
    AzcoinRpcError,
    AzcoinRpcWrongChainError,
)
from node_api.settings import get_settings

router = APIRouter(prefix="/az/mempool", tags=["az-mempool"])


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


@router.get("/info")
def mempool_info() -> dict:
    rpc = _get_az_rpc()
    try:
        mempool = rpc.call("getmempoolinfo")
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    if not isinstance(mempool, dict):
        _raise_az_unavailable()

    return {
        "size": mempool.get("size"),
        "bytes": mempool.get("bytes"),
        "usage": mempool.get("usage"),
        "maxmempool": mempool.get("maxmempool"),
        "mempoolminfee": mempool.get("mempoolminfee"),
        "minrelaytxfee": mempool.get("minrelaytxfee"),
    }
