from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from node_api.services.azcoin_rpc import (
    AzcoinRpcClient,
    AzcoinRpcError,
    AzcoinRpcWrongChainError,
)
from node_api.settings import get_settings

router = APIRouter(prefix="/az/node", tags=["az-node"])


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
def node_info() -> dict:
    rpc = _get_az_rpc()

    try:
        blockchain = rpc.call("getblockchaininfo")
        network = rpc.call("getnetworkinfo")
        mempool = rpc.call("getmempoolinfo")
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    if (
        not isinstance(blockchain, dict)
        or not isinstance(network, dict)
        or not isinstance(mempool, dict)
    ):
        _raise_az_unavailable()

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
    rpc = _get_az_rpc()

    try:
        blockchain = rpc.call("getblockchaininfo")
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    if not isinstance(blockchain, dict):
        _raise_az_unavailable()
    return blockchain


def _normalize_peer(peer: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "addr": peer.get("addr"),
        "inbound": peer.get("inbound"),
        "subver": peer.get("subver"),
        "pingtime": peer.get("pingtime"),
        "bytesrecv": peer.get("bytesrecv"),
        "bytessent": peer.get("bytessent"),
        "lastsend": peer.get("lastsend"),
        "lastrecv": peer.get("lastrecv"),
        "version": peer.get("version"),
    }
    if "connection_type" in peer:
        normalized["connection_type"] = peer.get("connection_type")
    return normalized


@router.get("/peers")
def node_peers() -> list[dict[str, Any]]:
    rpc = _get_az_rpc()

    try:
        peers = rpc.call("getpeerinfo")
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    if not isinstance(peers, list):
        _raise_az_unavailable()

    return [_normalize_peer(peer) for peer in peers if isinstance(peer, dict)]
