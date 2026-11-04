"""Progress events over Server-Sent Events.

SSE is the working choice for server -> UI progress; live input goes over plain HTTP POSTs.
Revisit if live needs WebSockets (docs/architecture/overview.md, open questions).
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from iris.api.deps import BusDep, SessionDep
from iris.models import Event

router = APIRouter(prefix="/sessions", tags=["events"])

KEEPALIVE_S = 15


def _format(event: Event) -> str:
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"


@router.get(
    "/{session_id}/events",
    response_class=StreamingResponse,
    responses={200: {"model": Event, "description": "text/event-stream of Event objects"}},
)
async def stream_events(session: SessionDep, bus: BusDep, request: Request) -> StreamingResponse:
    """Replays this session's recent events, then streams new ones."""

    async def gen() -> AsyncIterator[str]:
        q = bus.subscribe(session.id)
        try:
            for event in bus.history(session.id):
                yield _format(event)
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _format(event)
        finally:
            bus.unsubscribe(session.id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
