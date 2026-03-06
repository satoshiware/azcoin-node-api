from __future__ import annotations

from fastapi import APIRouter, HTTPException

from node_api.services.bitcoin_rpc import BitcoinRpcError
from node_api.services.btc_route_helpers import (
    get_btc_rpc,
    normalize_peer,
    raise_btc_unavailable,
)

router = APIRouter(prefix="/btc/node", tags=["btc-node"])


@router.get("/info")
def node_info() -> dict:
    rpc = get_btc_rpc()
    try:
        blockchain = rpc.call_dict("getblockchaininfo")
        network = rpc.call_dict("getnetworkinfo")
        mempool = rpc.call_dict("getmempoolinfo")
    except BitcoinRpcError:
        raise_btc_unavailable()

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
    rpc = get_btc_rpc()
    try:
        return rpc.call_dict("getblockchaininfo")
    except BitcoinRpcError:
        raise_btc_unavailable()


@router.get("/peers")
def node_peers() -> list[dict]:
    rpc = get_btc_rpc()
    try:
        peers = rpc.call("getpeerinfo")
    except BitcoinRpcError:
        raise_btc_unavailable()

    if not isinstance(peers, list):
        raise_btc_unavailable()

    return [normalize_peer(peer) for peer in peers if isinstance(peer, dict)]
