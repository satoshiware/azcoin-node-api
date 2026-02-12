from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query

from node_api.services.events_bus import events_bus

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/recent")
def recent_events(
    limit: int = Query(default=100, ge=1, le=2000),
    event_type: Literal["hashtx", "hashblock"] | None = Query(default=None, alias="type"),
) -> list[dict[str, Any]]:
    return events_bus.list_recent(limit=limit, event_type=event_type)
