from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.models import User
from app.services.sse_service import format_sse, sse_pull_since

router = APIRouter()


@router.get("/api/sse/stream")
async def sse_stream(
    request: Request,
    last_event_id: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
):
    async def event_generator() -> AsyncGenerator[str, None]:
        cursor = int(last_event_id or 0)
        idle_ticks = 0
        channels = {"global", f"user:{current_user.id}"}
        while True:
            if await request.is_disconnected():
                break

            events = sse_pull_since(cursor, channels=channels)
            if events:
                idle_ticks = 0
                for env in events:
                    cursor = env.id
                    yield format_sse(env)
            else:
                idle_ticks += 1
                # heartbeat: keep proxies/connections alive.
                if idle_ticks >= 10:
                    idle_ticks = 0
                    yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(0.8)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            # no-cache: do not cache event stream responses
            "Cache-Control": "no-cache",
            # keep-alive: hint proxies/clients to keep the TCP connection open
            "Connection": "keep-alive",
            # nginx: disable proxy buffering so events are flushed in real time
            "X-Accel-Buffering": "no",
        },
    )
