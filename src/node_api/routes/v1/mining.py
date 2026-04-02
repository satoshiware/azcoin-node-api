from __future__ import annotations

import re
from typing import Any, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from node_api.services.share_ledger import (
    get_user, get_worker, ingest_block, ingest_share, list_blocks, list_users, list_workers,
)
from node_api.settings import get_settings

router = APIRouter(prefix="/mining", tags=["mining"])
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _is_hex(value: str) -> bool:
    return bool(_HEX_RE.fullmatch(value))


def _require_mining_token(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    token = get_settings().az_node_api_token
    if not token:
        return

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    scheme, _, supplied_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or supplied_token != token:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


class BlockEvent(BaseModel):
    height: int = Field(gt=0)
    block_hash: str = Field(min_length=1)
    reward: float = Field(ge=0)
    worker: str = Field(min_length=1, max_length=64)
    ts: int = Field(gt=0)
    confirmed: bool = False

    @field_validator("block_hash")
    @classmethod
    def _validate_hex_block_hash(cls, value: str) -> str:
        if not _HEX_RE.fullmatch(value):
            raise ValueError("must be a hex string")
        return value


class ShareEvent(BaseModel):
    ts: int = Field(gt=0)
    worker: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=255)
    extranonce2: str = Field(min_length=1)
    ntime: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    accepted: bool
    duplicate: bool
    share_diff: float
    reason: str = ""

    @field_validator("extranonce2", "ntime", "nonce")
    @classmethod
    def _validate_hex_fields(cls, value: str) -> str:
        if not _is_hex(value):
            raise ValueError("must be a hex string")
        return value


@router.post("/share")
def post_share(
    payload: ShareEvent,
    _: None = Depends(_require_mining_token),
) -> dict[str, bool]:
    ingest_share(payload.model_dump())
    return {"ok": True}


@router.post("/block")
def post_block(
    payload: BlockEvent,
    _: None = Depends(_require_mining_token),
) -> dict[str, bool]:
    ingest_block(payload.model_dump())
    return {"ok": True}


@router.get("/blocks")
def blocks(
    limit: int = Query(default=50, ge=1, le=500),
    _: None = Depends(_require_mining_token),
) -> list[dict[str, Any]]:
    return list_blocks(limit=limit)


@router.get("/workers")
def workers(
    _: None = Depends(_require_mining_token),
) -> list[dict[str, Any]]:
    return list_workers()


@router.get("/workers/{worker}")
def worker(
    worker: str,
    include_recent: bool = Query(default=True),
    _: None = Depends(_require_mining_token),
) -> dict[str, Any]:
    item = get_worker(worker, include_recent=include_recent)
    if item is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return item


@router.get("/users")
def users(
    _: None = Depends(_require_mining_token),
) -> list[dict[str, Any]]:
    return list_users()


@router.get("/users/{username}")
def user(
    username: str,
    _: None = Depends(_require_mining_token),
) -> dict[str, Any]:
    item = get_user(username)
    if item is None:
        raise HTTPException(status_code=404, detail="User not found")
    return item
