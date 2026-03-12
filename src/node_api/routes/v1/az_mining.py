"""
AZCoin mining template and status endpoints.

Provides GET /v1/az/mining/template/current (block template for pool consumption)
and GET /v1/az/mining/status (RPC connectivity and template fetch health).
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from node_api.services.azcoin_rpc import (
    AzcoinRpcClient,
    AzcoinRpcError,
    AzcoinRpcWrongChainError,
)
from node_api.settings import get_settings

router = APIRouter(prefix="/az/mining", tags=["az-mining"])


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


class MiningTemplateResponse(BaseModel):
    """Stable DTO for pool consumption. Maps getblocktemplate result to minimal fields."""

    job_id: str = Field(description="Deterministic job identifier (derived when not from RPC)")
    prev_hash: str = Field(description="Previous block hash (previousblockhash)")
    version: int = Field(description="Block version")
    nbits: str = Field(description="Compact difficulty target (bits)")
    ntime: str = Field(description="Current time as hex (curtime)")
    clean_jobs: bool = Field(description="True when serving current template; miners discard old work")
    height: int = Field(description="Block height")


def _derive_job_id(prev_hash: str, height: int, curtime: int) -> str:
    """
    Derive a deterministic job_id from stable template fields.

    getblocktemplate does not return job_id. We derive one from previousblockhash,
    height, and curtime so the same template yields the same job_id. Used for
    pool Stratum compatibility.
    """
    payload = f"{prev_hash}{height}{curtime}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _map_template(raw: dict[str, Any]) -> MiningTemplateResponse:
    """Map getblocktemplate result to MiningTemplateResponse. Raises on invalid payload."""
    prev_hash = raw.get("previousblockhash")
    version = raw.get("version")
    nbits = raw.get("bits")
    curtime = raw.get("curtime")
    height = raw.get("height")

    if not isinstance(prev_hash, str) or not prev_hash:
        raise ValueError("previousblockhash missing or invalid")
    if not isinstance(version, (int, float)):
        raise ValueError("version missing or invalid")
    if not isinstance(nbits, str) or not nbits:
        raise ValueError("bits missing or invalid")
    if not isinstance(curtime, (int, float)):
        raise ValueError("curtime missing or invalid")
    if not isinstance(height, (int, float)):
        raise ValueError("height missing or invalid")

    version_int = int(version)
    curtime_int = int(curtime)
    height_int = int(height)

    job_id = _derive_job_id(prev_hash, height_int, curtime_int)

    return MiningTemplateResponse(
        job_id=job_id,
        prev_hash=prev_hash,
        version=version_int,
        nbits=nbits,
        ntime=hex(curtime_int)[2:],
        clean_jobs=True,
        height=height_int,
    )


@router.get("/template/current", response_model=MiningTemplateResponse)
def template_current() -> MiningTemplateResponse:
    """Return current block template from AZCoin daemon for pool consumption."""
    rpc = _get_az_rpc()

    try:
        raw = rpc.call("getblocktemplate", [{"rules": ["segwit"]}])
    except AzcoinRpcWrongChainError as exc:
        _raise_wrong_chain(exc.expected_chain)
    except AzcoinRpcError:
        _raise_az_unavailable()

    if not isinstance(raw, dict):
        _raise_az_unavailable()

    try:
        return _map_template(raw)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "AZ_RPC_INVALID_PAYLOAD",
                "message": f"getblocktemplate returned invalid payload: {e}",
            },
        ) from e


@router.get("/status")
def mining_status() -> dict[str, Any]:
    """
    Return AZCoin mining status: RPC connectivity, chain info, and template fetch health.

    Lightweight read-only endpoint reusing getblockchaininfo and getblocktemplate.
    """
    settings = get_settings()
    if not settings.az_rpc_url or not settings.az_rpc_user or not settings.az_rpc_password:
        return {
            "rpc_healthy": False,
            "template_ok": False,
            "chain": None,
            "blocks": None,
            "headers": None,
            "error": "AZ_RPC_NOT_CONFIGURED",
        }

    rpc = AzcoinRpcClient(
        url=settings.az_rpc_url,
        user=settings.az_rpc_user,
        password=settings.az_rpc_password.get_secret_value(),
        timeout_seconds=settings.az_rpc_timeout_seconds,
        expected_chain=settings.az_expected_chain,
    )

    # Fetch blockchain info (chain, blocks, headers)
    try:
        blockchain = rpc.call("getblockchaininfo")
    except AzcoinRpcWrongChainError:
        return {
            "rpc_healthy": True,
            "template_ok": False,
            "chain": None,
            "blocks": None,
            "headers": None,
            "error": "AZ_WRONG_CHAIN",
        }
    except AzcoinRpcError:
        return {
            "rpc_healthy": False,
            "template_ok": False,
            "chain": None,
            "blocks": None,
            "headers": None,
            "error": "AZ_RPC_UNAVAILABLE",
        }

    if not isinstance(blockchain, dict):
        return {
            "rpc_healthy": False,
            "template_ok": False,
            "chain": None,
            "blocks": None,
            "headers": None,
            "error": "AZ_RPC_INVALID_PAYLOAD",
        }

    chain = blockchain.get("chain")
    blocks = blockchain.get("blocks")
    headers = blockchain.get("headers")

    # Check if template fetch works
    template_ok = False
    try:
        raw = rpc.call("getblocktemplate", [{"rules": ["segwit"]}])
        if isinstance(raw, dict) and raw.get("previousblockhash") and raw.get("height") is not None:
            template_ok = True
    except AzcoinRpcError:
        pass

    return {
        "rpc_healthy": True,
        "template_ok": template_ok,
        "chain": chain,
        "blocks": blocks,
        "headers": headers,
    }
