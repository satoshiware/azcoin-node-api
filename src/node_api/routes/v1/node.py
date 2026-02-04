"""
Deprecated route module.

The v0.1 skeleton uses `routes/v1/health.py` and `routes/v1/az_node.py`.
This file remains only to avoid import errors for anyone referencing it; it
exports an empty router.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
