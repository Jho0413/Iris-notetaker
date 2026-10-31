import asyncio
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from iris.api.deps import PathsDep, RunnerDep, SessionDep, SettingsDep, StoreDep
from iris.export import export_markdown_embedded
from iris.models import Notes, Session, SourceKind, Stage, Transcript
from iris.sources.upload import save_upload
from iris.store.layout import read_model, session_paths

router = APIRouter(prefix="/sessions", tags=["sessions"])

SERVABLE_DIRS = ("frames", "visuals")


@router.get("", response_model=list[Session])
def list_sessions(store: StoreDep) -> list[Session]:
    return store.list()


@router.post("", response_model=Session, status_code=201)
async def create_upload_session(
    store: StoreDep,
    runner: RunnerDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
) -> Session:
    """Upload a recording and start processing it."""
    name = file.filename or "recording"
    session = store.create(title or Path(name).stem, SourceKind.UPLOAD)
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    await asyncio.to_thread(save_upload, paths, name, file.file)
    runner.start(session.id)
    return store.get(session.id)  # type: ignore[return-value]


@router.get("/{session_id}", response_model=Session)
def get_session(session: SessionDep) -> Session:
    return session


@router.delete("/{session_id}", status_code=204)
def delete_session(session: SessionDep, paths: PathsDep, store: StoreDep, runner: RunnerDep):
    if runner.is_running(session.id):
        raise HTTPException(409, "session is processing")
    store.delete(session.id)
    shutil.rmtree(paths.root, ignore_errors=True)


@router.post("/{session_id}/run", response_model=Session)
async def rerun(
    session: SessionDep, store: StoreDep, runner: RunnerDep, from_stage: Stage | None = None
) -> Session:
    """Re-run the pipeline, optionally from a given stage (never lose a session: principle 4)."""
    if runner.is_running(session.id):
        raise HTTPException(409, "session is already processing")
    runner.start(session.id, from_stage)
    return store.get(session.id)  # type: ignore[return-value]


@router.get("/{session_id}/transcript", response_model=Transcript)
def get_transcript(paths: PathsDep) -> Transcript:
    if not paths.transcript.exists():
        raise HTTPException(404, "transcript not ready")
    return read_model(paths.transcript, Transcript)


@router.get("/{session_id}/notes", response_model=Notes)
def get_notes(paths: PathsDep) -> Notes:
    if not paths.notes.exists():
        raise HTTPException(404, "notes not ready")
    return read_model(paths.notes, Notes)


@router.get("/{session_id}/export", response_class=PlainTextResponse)
def export_notes(session: SessionDep, paths: PathsDep) -> PlainTextResponse:
    """Markdown with visuals embedded as data URIs, so the file stands alone."""
    if not paths.notes.exists():
        raise HTTPException(404, "notes not ready")
    notes = read_model(paths.notes, Notes)
    body = export_markdown_embedded(notes, paths.root)
    filename = f"{session.title or session.id}.md"
    return PlainTextResponse(
        body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{session_id}/files/{subdir}/{name}")
def get_file(subdir: str, name: str, paths: PathsDep) -> FileResponse:
    """Serve frames and visuals referenced by notes (e.g. ``visuals/v1.jpg``)."""
    if subdir not in SERVABLE_DIRS or "/" in name or name.startswith("."):
        raise HTTPException(404)
    path = paths.root / subdir / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)
