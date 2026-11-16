import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from iris.api import deps
from iris.api.deps import BusDep, PathsDep, RunnerDep, SessionDep, SettingsDep, StoreDep
from iris.live import Lane
from iris.models import EventType, Session, SessionStatus, SourceKind
from iris.sources.browser_live import (
    JPEG_MAGIC,
    MAX_UPLOAD_BYTES,
    WEBM_MAGIC,
    ChunkConflict,
    missing_audio_chunks,
    store_audio_chunk,
    store_frame,
)
from iris.store.db import SessionStore
from iris.store.layout import FIRST_SHARE, MAX_AUDIO_SEQ, MAX_FRAME_T, MAX_SHARE, session_paths

router = APIRouter(prefix="/live", tags=["live"])

MAX_LISTED_MISSING = 100


class LiveStart(BaseModel):
    title: str = "Live session"


class LiveEnd(BaseModel):
    audio_chunks: int | None = Field(
        default=None,
        ge=0,
        le=MAX_AUDIO_SEQ + 1,
        description="How many audio chunks the browser recorded (seq 0 to n-1). If set, the "
        "session only ends once all of them have been received.",
    )
    allow_gaps: bool = Field(
        default=False,
        description="End even if some audio chunks are missing; they become silence.",
    )


RECORDING = frozenset({SessionStatus.RECORDING})
RECORDING_OR_STOPPED = frozenset({SessionStatus.RECORDING, SessionStatus.STOPPED})


def _require_live_now(
    store: SessionStore, session_id: str, statuses: frozenset[SessionStatus] = RECORDING
) -> Session:
    session = deps.find_session(session_id, store)
    if session.source != SourceKind.BROWSER_LIVE or session.status not in statuses:
        raise HTTPException(409, "session is not recording")
    return session


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "chunk too large")
    body = bytearray()
    async for part in request.stream():
        body += part
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "chunk too large")
    if not body:
        raise HTTPException(400, "empty body")
    return bytes(body)


@router.post("", response_model=Session, status_code=201)
async def start_live(
    body: LiveStart, store: StoreDep, settings: SettingsDep, runner: RunnerDep
) -> Session:
    session = store.create(body.title, SourceKind.BROWSER_LIVE)
    session_paths(settings.sessions_dir, session.id).ensure()
    store.update(session.id, status=SessionStatus.RECORDING)
    runner.live.ensure(session.id)
    return deps.find_session(session.id, store)


@router.post("/{session_id}/audio", status_code=204)
async def post_audio(
    session: SessionDep,
    paths: PathsDep,
    store: StoreDep,
    runner: RunnerDep,
    request: Request,
    seq: Annotated[int, Query(ge=0, le=MAX_AUDIO_SEQ)],
) -> None:
    """Body: one MediaRecorder chunk (audio/webm). ``seq`` orders chunks, from 0.

    Posting the same chunk again is a no-op; a different body for a stored ``seq`` is a 409.
    """
    data = await _read_body(request)
    if seq == 0 and not data.startswith(WEBM_MAGIC):
        raise HTTPException(415, "audio must be WebM")
    _require_live_now(store, session.id)
    try:
        store_audio_chunk(paths, seq, data)
    except ChunkConflict as e:
        raise HTTPException(409, str(e)) from e
    if processor := runner.live.ensure(session.id):
        processor.notify(Lane.AUDIO)


@router.post("/{session_id}/frame", status_code=204)
async def post_frame(
    session: SessionDep,
    paths: PathsDep,
    store: StoreDep,
    runner: RunnerDep,
    request: Request,
    t: Annotated[float, Query(ge=0, le=MAX_FRAME_T, allow_inf_nan=False)],
    share: Annotated[int, Query(ge=FIRST_SHARE, le=MAX_SHARE)],
) -> None:
    """Body: one JPEG screen frame. ``t`` is seconds since recording started (the audio's clock).
    ``share`` numbers the screen share it belongs to: 1 for the first, one more for each share
    after it (Share screen, or Continue after Stop). A share ends one sampling step
    (``1 / frame_fps``) after its last frame, or where the next share's frames start if sooner,
    so what was shown is placed only while it was shared.

    Posting the same frame again is a no-op; a different body for a stored ``t`` and ``share``
    is a 409.
    """
    data = await _read_body(request)
    if not data.startswith(JPEG_MAGIC):
        raise HTTPException(415, "frame must be a JPEG")
    _require_live_now(store, session.id)
    try:
        store_frame(paths, t, share, data)
    except ChunkConflict as e:
        raise HTTPException(409, str(e)) from e
    if processor := runner.live.ensure(session.id):
        processor.notify(Lane.SCREEN)


@router.post(
    "/{session_id}/end",
    response_model=Session,
    responses={
        409: {"description": "Neither recording nor stopped, or audio chunks are still missing"}
    },
)
async def end_live(
    session: SessionDep,
    paths: PathsDep,
    store: StoreDep,
    runner: RunnerDep,
    settings: SettingsDep,
    body: LiveEnd | None = None,
) -> Session:
    """Make notes: stop recording (if it hasn't stopped already, ``/stop``) and finish the
    session. The live work is kept, the rest is processed.

    The browser flushes its uploads first and says how many audio chunks it recorded. If any are
    missing, this returns 409 with ``detail.missing_audio_chunks`` and the session stays as it was
    (recording or stopped), so the browser can resend them (or end with ``allow_gaps``). Captured
    chunks and frames are kept, so a failed run can be retried with ``POST /sessions/{id}/run``.
    """
    body = body or LiveEnd()
    _require_live_now(store, session.id, RECORDING_OR_STOPPED)
    if body.audio_chunks is not None and not body.allow_gaps:
        missing = missing_audio_chunks(paths, body.audio_chunks)
        if missing:
            raise HTTPException(
                409,
                {
                    "message": f"{len(missing)} audio chunks are missing",
                    "missing_audio_chunks": missing[:MAX_LISTED_MISSING],
                },
            )
    runner.live.ensure(session.id)
    store.update(session.id, status=SessionStatus.PROCESSING)
    runner.start(session.id)
    return await asyncio.to_thread(deps.current_session, session.id, store, settings)


@router.post(
    "/{session_id}/stop",
    response_model=Session,
    responses={409: {"description": "Not recording: making notes, finished or failed"}},
)
async def stop_live(
    session: SessionDep, store: StoreDep, settings: SettingsDep, bus: BusDep
) -> Session:
    """Stop recording without making notes: for a recording whose page went away (closed or
    reloaded, or the desktop app quit or crashed), so leaving never makes notes nobody asked for.

    Nothing more is accepted; what has arrived is kept, and live processing carries on with it,
    so making notes later (``/end``) reuses that work. Stopping a stopped session again is a no-op:
    the page and the desktop app may both send it. Delete it with ``DELETE /sessions/{id}``.
    """
    current = _require_live_now(store, session.id, RECORDING_OR_STOPPED)
    if current.status == SessionStatus.RECORDING:
        store.update(session.id, status=SessionStatus.STOPPED)
        bus.publish(session.id, EventType.STATUS, {"status": SessionStatus.STOPPED})
    return await asyncio.to_thread(deps.current_session, session.id, store, settings)
