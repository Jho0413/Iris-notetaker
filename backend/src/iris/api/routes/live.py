"""Live capture input. The browser posts audio chunks and screen frames while recording."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from iris.api.deps import PathsDep, RunnerDep, SessionDep, SettingsDep, StoreDep
from iris.models import Session, SessionStatus, SourceKind
from iris.sources.browser_live import append_audio_chunk, append_frame
from iris.store.layout import session_paths

router = APIRouter(prefix="/live", tags=["live"])


class LiveStart(BaseModel):
    title: str = "Live session"


def _require_recording(session: Session) -> None:
    if session.source != SourceKind.BROWSER_LIVE or session.status != SessionStatus.RECORDING:
        raise HTTPException(409, "session is not recording")


@router.post("", response_model=Session, status_code=201)
def start_live(body: LiveStart, store: StoreDep, settings: SettingsDep) -> Session:
    session = store.create(body.title, SourceKind.BROWSER_LIVE)
    session_paths(settings.sessions_dir, session.id).ensure()
    store.update(session.id, status=SessionStatus.RECORDING)
    return store.get(session.id)  # type: ignore[return-value]


@router.post("/{session_id}/audio", status_code=204)
async def post_audio(session: SessionDep, paths: PathsDep, request: Request, seq: int) -> None:
    """Body: one MediaRecorder chunk (audio/webm). ``seq`` orders chunks."""
    _require_recording(session)
    append_audio_chunk(paths, seq, await request.body())
    # TODO: hand to LiveProcessor.on_audio_chunk


@router.post("/{session_id}/frame", status_code=204)
async def post_frame(session: SessionDep, paths: PathsDep, request: Request, t: float) -> None:
    """Body: one JPEG screen frame. ``t`` is seconds since recording started."""
    _require_recording(session)
    append_frame(paths, t, await request.body())
    # TODO: hand to LiveProcessor.on_frame


@router.post("/{session_id}/end", response_model=Session)
async def end_live(session: SessionDep, store: StoreDep, runner: RunnerDep) -> Session:
    """Stop recording and run the full pipeline over the whole session."""
    _require_recording(session)
    runner.start(session.id)
    return store.get(session.id)  # type: ignore[return-value]
