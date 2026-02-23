from typing import Optional

from fastapi import APIRouter, Query

# Import the module-scope store created in main.py.
from node_api.main import store

router = APIRouter(prefix="/v1/events", tags=["events"])


@router.get("/recent")
def recent(
    type: Optional[str] = Query(
        default=None,
        description="Filter by event type: rawtx|rawblock|hashtx|hashblock",
    ),
    limit: int = Query(default=50, ge=1, le=1000),
):
    return store.recent(ev_type=type, limit=limit)
