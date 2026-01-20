from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status

from app.auth import Authenticator, Role
from app.config import load_settings
from app.rpc import RPCClient, RPCError

settings = load_settings()
client = RPCClient(settings)
auth = Authenticator(settings)

app = FastAPI(title="AZCoin REST API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/node/info")
def node_info(_: Role = Depends(auth.require_role(Role.reader))) -> dict[str, Any]:
    try:
        blockchain = client.call("getblockchaininfo")
        network = client.call("getnetworkinfo")
    except RPCError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {"blockchain": blockchain, "network": network}


@app.get("/node/peers")
def node_peers(_: Role = Depends(auth.require_role(Role.reader))) -> dict[str, Any]:
    try:
        peers = client.call("getpeerinfo")
    except RPCError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {"count": len(peers), "peers": peers}


@app.get("/network/summary")
def network_summary(_: Role = Depends(auth.require_role(Role.reader))) -> dict[str, Any]:
    try:
        peers = client.call("getpeerinfo")
        mining = client.call("getmininginfo")
    except RPCError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {
        "peer_count": len(peers),
        "mining": mining,
    }


@app.post("/wallets")
def create_wallet(
    name: str,
    _: Role = Depends(auth.require_role(Role.owner)),
) -> dict[str, Any]:
    try:
        result = client.call("createwallet", [name])
    except RPCError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {"wallet": result}


@app.post("/rpc/{method}")
def rpc_passthrough(
    method: str,
    params: list[Any] | None = None,
    _: Role = Depends(auth.require_role(Role.owner)),
) -> dict[str, Any]:
    try:
        result = client.call(method, params)
    except RPCError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {"result": result}
