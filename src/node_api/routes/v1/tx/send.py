from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from node_api.services.bitcoin_rpc import (
    BitcoinRPC,
    BitcoinRpcError,
    BitcoinRpcHttpError,
    BitcoinRpcResponseError,
    BitcoinRpcTransportError,
)

router = APIRouter(prefix="/tx", tags=["tx"])


class TxSendRequest(BaseModel):
    hex: str = Field(min_length=2, description="Raw transaction hex")


class TxSendResponse(BaseModel):
    txid: str


def get_bitcoin_rpc() -> BitcoinRPC:
    """
    Resolve the RPC dependency into explicit API errors.

    `BitcoinRPC.from_settings()` raises a domain exception when RPC config is
    incomplete; convert that to a stable 503 payload for clients.
    """
    try:
        return BitcoinRPC.from_settings()
    except BitcoinRpcResponseError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "BTC_RPC_NOT_CONFIGURED", "message": exc.message},
        ) from exc


@router.post("/send", response_model=TxSendResponse)
def send_tx(
    payload: TxSendRequest, rpc: BitcoinRPC = Depends(get_bitcoin_rpc)
) -> TxSendResponse:
    try:
        txid = rpc.call("sendrawtransaction", [payload.hex])
        if not isinstance(txid, str) or not txid:
            raise RuntimeError("Unexpected RPC response for sendrawtransaction")
        return TxSendResponse(txid=txid)
    except BitcoinRpcTransportError:
        raise HTTPException(
            status_code=502,
            detail={"code": "BTC_RPC_UNAVAILABLE", "message": "Bitcoin RPC transport failure"},
        ) from None
    except BitcoinRpcHttpError:
        raise HTTPException(
            status_code=502,
            detail={"code": "BTC_RPC_HTTP_ERROR", "message": "Bitcoin RPC returned non-200 status"},
        ) from None
    except BitcoinRpcResponseError as exc:
        # Upstream returned a JSON-RPC error (for example, malformed tx hex).
        raise HTTPException(
            status_code=400,
            detail={"code": "TX_REJECTED", "message": exc.message, "rpc_code": exc.code},
        ) from None
    except BitcoinRpcError:
        raise HTTPException(
            status_code=502,
            detail={"code": "BTC_RPC_ERROR", "message": "Bitcoin RPC call failed"},
        ) from None
