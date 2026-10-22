from __future__ import annotations

from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.sse_service import format_sse, sse_pull_since, sse_wait_for_events

router = APIRouter()

# 每隔多少个空闲 tick 做一次 session 校验（每 tick ≈ 1s，60 tick ≈ 1 分钟）
_SESSION_CHECK_INTERVAL = 60


def _read_session_token(bind, user_id: int) -> str | None:
    # A session check must release its connection before the stream waits again.
    with Session(bind=bind) as check_db:
        return check_db.query(User.session_token).filter(User.id == user_id).scalar()


@router.get("/api/sse/stream")
async def sse_stream(
    request: Request,
    last_event_id: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Snapshot authentication before closing the request Session. Depending on
    # FastAPI's version, yield dependencies close before or after streaming;
    # neither lifecycle should leave a connection reserved for an idle client.
    user_id = current_user.id
    saved_session_token = current_user.session_token
    bind = db.get_bind()
    await run_in_threadpool(db.close)

    async def event_generator() -> AsyncGenerator[str, None]:
        cursor = int(last_event_id or 0)
        idle_ticks = 0
        session_check_ticks = 0
        channels = {"global", f"user:{user_id}"}
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
                if idle_ticks >= 15:
                    idle_ticks = 0
                    yield "event: ping\ndata: {}\n\n"
                # Wait until a new event is published (or at most 1s), then re-poll.
                # This replaces the fixed 0.8s sleep and wakes up immediately on publish.
                await sse_wait_for_events(timeout=1.0)

            # 定期校验会话；短会话在查询后立即归还连接，查询也不阻塞事件循环。
            session_check_ticks += 1
            if session_check_ticks >= _SESSION_CHECK_INTERVAL:
                session_check_ticks = 0
                try:
                    current_token = await run_in_threadpool(_read_session_token, bind, user_id)
                except Exception:
                    break
                if current_token != saved_session_token:
                    break

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
