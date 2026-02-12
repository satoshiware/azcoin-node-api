from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@lru_cache(maxsize=1)
def _get_project_version() -> str | None:
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                return None
            return data.get("project", {}).get("version")
    return None


def _get_api_version() -> str:
    return os.environ.get("VERSION") or _get_project_version() or "unknown"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
