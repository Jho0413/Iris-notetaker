"""In-process pub/sub for progress events. The API streams these to the UI.

Each session keeps a bounded history so a client that connects late (or reconnects) can replay
what it missed, then continue live.
"""

import asyncio
from collections import defaultdict, deque
from datetime import UTC, datetime

from iris.models import Event, EventType

HISTORY_LIMIT = 1000


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)
        self._history: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=HISTORY_LIMIT))

    def publish(self, session_id: str, type: EventType, data: dict | None = None) -> Event:
        event = Event(type=type, session_id=session_id, data=data or {}, at=datetime.now(UTC))
        self._history[session_id].append(event)
        for q in self._subscribers[session_id]:
            q.put_nowait(event)
        return event

    def history(self, session_id: str) -> list[Event]:
        return list(self._history[session_id])

    def subscribe(self, session_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[session_id].add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[Event]) -> None:
        self._subscribers[session_id].discard(q)
