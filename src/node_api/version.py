from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _version_file() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def get_version() -> str:
    version_file = _version_file()
    if version_file is None:
        return "unknown"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return version or "unknown"


__version__ = get_version()
