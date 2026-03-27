from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class SSEEnvelope:
    id: int
    event: str
    data: dict[str, Any]
    channels: tuple[str, ...]


class SSEEventBus:
    """
    In-memory SSE event bus (single-process scope).
    - publish from sync request handlers
    - stream side polls by last_event_id

    Note:
    This works for single-process deployments. If you run with multiple workers
    or multiple app instances, events will not be shared across processes.
    In that case, replace this with a shared event bus (for example Redis pub/sub
    + durable cursor store) to keep SSE streams consistent.
    """

    def __init__(self, max_events: int = 2000) -> None:
        self._max_events = max_events
        self._next_id = 1
        self._events: deque[SSEEnvelope] = deque()
        self._lock = threading.Lock()

    def publish(self, event: str, data: dict[str, Any], *, channels: list[str] | None = None) -> int:
        normalized_channels = tuple(channels or ["global"])
        with self._lock:
            event_id = self._next_id
            self._next_id += 1
            env = SSEEnvelope(id=event_id, event=event, data=data, channels=normalized_channels)
            self._events.append(env)
            while len(self._events) > self._max_events:
                self._events.popleft()
            return event_id

    def pull_since(self, last_event_id: int, *, channels: set[str]) -> list[SSEEnvelope]:
        with self._lock:
            return [e for e in self._events if e.id > last_event_id and (set(e.channels) & channels)]


_BUS = SSEEventBus()


def sse_publish(event: str, payload: dict[str, Any], *, channels: list[str] | None = None) -> int:
    data = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    return _BUS.publish(event, data, channels=channels)


def sse_pull_since(last_event_id: int, *, channels: set[str]) -> list[SSEEnvelope]:
    return _BUS.pull_since(last_event_id, channels=channels)


def format_sse(envelope: SSEEnvelope) -> str:
    body = {
        "id": envelope.id,
        "type": envelope.event,
        **envelope.data,
    }
    return f"id: {envelope.id}\nevent: {envelope.event}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
