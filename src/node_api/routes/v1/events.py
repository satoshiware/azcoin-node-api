from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from node_api.services.events_bus import events_bus

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/recent-legacy")
def recent_events(
    limit: int = Query(default=100, ge=1, le=2000),
    event_type: Literal["hashtx", "hashblock", "rawblock", "rawtx"] | None = Query(
        default=None, alias="type"
    ),
) -> list[dict[str, Any]]:
    return events_bus.list_recent(limit=limit, event_type=event_type)


def _sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


@router.get("/stream")
async def stream_events(
    request: Request,
    event_type: Literal["hashtx", "hashblock", "rawblock", "rawtx"] | None = Query(
        default=None, alias="type"
    ),
) -> StreamingResponse:
    queue = events_bus.subscribe()

    async def event_generator():
        try:
            yield _sse_data({"type": "hello", "message": "connected"})
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                if event_type is not None and event.get("type") != event_type:
                    continue
                yield _sse_data(event)
        finally:
            events_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
