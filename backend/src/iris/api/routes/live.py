"""Live capture input. The browser posts audio chunks, screen frames and when the screen was
turned on or off while recording.

Chunks may arrive out of order or more than once (the browser retries failed uploads); they are
keyed by ``seq`` and ``t``, so neither loses nor duplicates content (``sources/browser_live.py``).
Each handler reads the whole body first, then checks the session is still recording and stores
the file with no ``await`` in between, so nothing is stored after ``end`` has started ingest.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from iris.api.deps import PathsDep, RunnerDep, SessionDep, SettingsDep, StoreDep
from iris.llm.usage import with_cost
from iris.models import Session, SessionStatus, SourceKind
from iris.sources.browser_live import (
    JPEG_MAGIC,
    MAX_AUDIO_SEQ,
    MAX_FRAME_T,
    MAX_UPLOAD_BYTES,
    WEBM_MAGIC,
    ChunkConflict,
    missing_audio_chunks,
    store_audio_chunk,
    store_frame,
    store_screen_change,
)
from iris.store.db import SessionStore
from iris.store.layout import session_paths

router = APIRouter(prefix="/live", tags=["live"])

# The 409 for missing chunks lists at most this many (the message gives the full count).
MAX_LISTED_MISSING = 100


class LiveStart(BaseModel):
    title: str = "Live session"
    screen: bool = Field(
        default=True,
        description="Whether the screen is shared from the start (as if it were turned on at "
        "0 s). False: audio only until the screen is turned on (POST /live/{id}/screen).",
    )


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


def _require_recording(store: SessionStore, session_id: str) -> Session:
    """The session's current state (not the one read before an ``await``), if recording."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session.source != SourceKind.BROWSER_LIVE or session.status != SessionStatus.RECORDING:
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
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    # Before the processor starts, so its screen lane never takes the start for screen off.
    if body.screen:
        store_screen_change(paths, 0.0, on=True)
    store.update(session.id, status=SessionStatus.RECORDING)
    runner.live.ensure(session.id)
    return store.get(session.id)  # type: ignore[return-value]


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
    _require_recording(store, session.id)
    # Small synchronous write, deliberately with no await after the status check (see above).
    try:
        store_audio_chunk(paths, seq, data)
    except ChunkConflict as e:
        raise HTTPException(409, str(e)) from e
    if processor := runner.live.ensure(session.id):
        processor.notify_audio()


@router.post("/{session_id}/frame", status_code=204)
async def post_frame(
    session: SessionDep,
    paths: PathsDep,
    store: StoreDep,
    runner: RunnerDep,
    request: Request,
    t: Annotated[float, Query(ge=0, le=MAX_FRAME_T, allow_inf_nan=False)],
) -> None:
    """Body: one JPEG screen frame. ``t`` is seconds since recording started (the audio's clock).

    Posting the same frame again is a no-op; a different body for a stored ``t`` is a 409.
    """
    data = await _read_body(request)
    if not data.startswith(JPEG_MAGIC):
        raise HTTPException(415, "frame must be a JPEG")
    _require_recording(store, session.id)
    try:
        store_frame(paths, t, data)
    except ChunkConflict as e:
        raise HTTPException(409, str(e)) from e
    if processor := runner.live.ensure(session.id):
        processor.notify_screen()


@router.post("/{session_id}/screen", status_code=204)
async def post_screen(
    session: SessionDep,
    paths: PathsDep,
    store: StoreDep,
    runner: RunnerDep,
    t: Annotated[float, Query(ge=0, le=MAX_FRAME_T, allow_inf_nan=False)],
    on: Annotated[bool, Query()],
) -> None:
    """The shared screen was turned on (``on=true``) or off ``t`` seconds after recording
    started (the audio's clock). Frames are posted only while it is on. What was shown before an
    off is not on screen after it, so the notes place it only while it was shared.

    Posting the same change again is a no-op.
    """
    _require_recording(store, session.id)
    store_screen_change(paths, t, on)
    if processor := runner.live.ensure(session.id):
        processor.notify_screen()


@router.post(
    "/{session_id}/end",
    response_model=Session,
    responses={409: {"description": "Not recording, or audio chunks are still missing"}},
)
async def end_live(
    session: SessionDep,
    paths: PathsDep,
    store: StoreDep,
    runner: RunnerDep,
    body: LiveEnd | None = None,
) -> Session:
    """Stop recording and finish the session: the live work is kept, the rest is processed.

    The browser flushes its uploads first and says how many audio chunks it recorded. If any are
    missing, this returns 409 with ``detail.missing_audio_chunks`` and the session keeps recording,
    so the browser can resend them (or end with ``allow_gaps``). Captured chunks and frames are
    kept, so a failed run can be retried with ``POST /sessions/{id}/run``.
    """
    body = body or LiveEnd()
    _require_recording(store, session.id)
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
    # Leave RECORDING before any await, so no chunk is accepted once ingest may have started.
    store.update(session.id, status=SessionStatus.PROCESSING)
    runner.start(session.id)
    # Live work has made model calls already, so the returned session carries their cost.
    return await asyncio.to_thread(with_cost, store.get(session.id), paths.usage_log)  # type: ignore[arg-type]
