from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from node_api.services.bitcoin_rpc import BitcoinRPC

router = APIRouter(prefix="/v1/tx", tags=["tx"])


class TxSendRequest(BaseModel):
    hex: str = Field(min_length=2, description="Raw transaction hex")


class TxSendResponse(BaseModel):
    txid: str


@router.post("/send", response_model=TxSendResponse)
def send_tx(
    payload: TxSendRequest, rpc: BitcoinRPC = Depends(BitcoinRPC.from_settings)
) -> TxSendResponse:
    try:
        txid = rpc.call("sendrawtransaction", [payload.hex])
        if not isinstance(txid, str) or not txid:
            raise RuntimeError("Unexpected RPC response for sendrawtransaction")
        return TxSendResponse(txid=txid)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"sendrawtransaction failed: {e}") from e
