from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/btc/wallet", tags=["btc-wallet"])


def _raise_wallet_disabled() -> None:
    raise HTTPException(
        status_code=501,
        detail={
            "code": "BTC_WALLET_DISABLED",
            "message": "Bitcoin wallet support is disabled in this deployment",
        },
    )


@router.get("/summary")
def wallet_summary() -> dict[str, Any]:
    _raise_wallet_disabled()


@router.get("/transactions")
def wallet_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    since: str | None = Query(
        default=None,
        description=(
            "Optional 64-hex blockhash. When provided, transactions are fetched "
            "via listsinceblock."
        ),
    ),
) -> list[dict[str, Any]]:
    _raise_wallet_disabled()
