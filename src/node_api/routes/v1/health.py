from __future__ import annotations

import os

from fastapi import APIRouter

from node_api.version import get_version

router = APIRouter(tags=["health"])
public_router = APIRouter(tags=["health"])


def _get_api_version() -> str:
    return get_version()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@public_router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": _get_api_version()}


@public_router.get("/version")
def version() -> dict[str, str]:
    return {"version": _get_api_version(), "service": "azcoin-api"}


@router.get("/health/version")
def version_info() -> dict[str, str]:
    api_version = _get_api_version()
    payload = {
        "api_version": api_version,
        "azcoin_core_target": api_version,
    }
    git_sha = os.environ.get("GIT_SHA")
    if git_sha:
        payload["git_sha"] = git_sha
    return payload
