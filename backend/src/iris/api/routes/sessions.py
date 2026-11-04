import asyncio
import shutil
import unicodedata
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from iris.api.deps import PathsDep, RunnerDep, SessionDep, SettingsDep, StoreDep
from iris.config import Settings
from iris.export import export_markdown_embedded
from iris.llm.usage import forget_total, with_cost
from iris.models import Notes, Session, SourceKind, Stage, Transcript
from iris.sources.upload import save_upload
from iris.store.layout import read_model, session_paths

router = APIRouter(prefix="/sessions", tags=["sessions"])

SERVABLE_DIRS = ("frames", "visuals")


def _with_cost(session: Session, settings: Settings) -> Session:
    return with_cost(session, session_paths(settings.sessions_dir, session.id).usage_log)


@router.get("", response_model=list[Session])
def list_sessions(store: StoreDep, settings: SettingsDep) -> list[Session]:
    """Every session, newest first, each with its model cost so far.

    Costs come from running totals that read only what each call log gained since the last
    list, so polling the list while sessions process stays cheap (``iris.llm.usage``).
    """
    return [_with_cost(s, settings) for s in store.list()]


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
def get_session(session: SessionDep, settings: SettingsDep) -> Session:
    return _with_cost(session, settings)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session: SessionDep, paths: PathsDep, store: StoreDep, runner: RunnerDep):
    if runner.is_running(session.id):
        raise HTTPException(409, "session is processing")
    # Remove the row before any await, so live input or an end request arriving meanwhile is
    # refused (404) instead of restarting processing on a session being deleted.
    store.delete(session.id)
    await runner.live.cancel(session.id)  # stop live processing before its files go
    await asyncio.to_thread(shutil.rmtree, paths.root, ignore_errors=True)
    forget_total(paths.usage_log)


@router.post("/{session_id}/run", response_model=Session)
async def rerun(
    session: SessionDep,
    store: StoreDep,
    runner: RunnerDep,
    settings: SettingsDep,
    from_stage: Stage | None = None,
) -> Session:
    """Re-run the pipeline, optionally from a given stage (never lose a session: principle 4)."""
    if runner.is_running(session.id):
        raise HTTPException(409, "session is already processing")
    runner.start(session.id, from_stage)
    session = store.get(session.id)  # type: ignore[assignment]
    return await asyncio.to_thread(_with_cost, session, settings)


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
    return PlainTextResponse(
        body,
        media_type="text/markdown",
        headers={"Content-Disposition": _attachment_header(session.title, session.id)},
    )


def _attachment_header(title: str, fallback: str) -> str:
    """``Content-Disposition`` for a download named after ``title`` (RFC 6266 / RFC 5987).

    Header values must be ASCII, so ``filename`` carries an ASCII approximation (accents
    folded, quotes, backslashes and other unsafe characters replaced) and ``filename*`` the
    full UTF-8 name, which browsers prefer. ``fallback`` names the file if nothing is left.
    """
    stem = _clean_filename(title)
    if all(c.isspace() or unicodedata.category(c) == "Cf" for c in stem):  # blank/invisible
        stem = fallback
    # NFKD folds accents off letters, but also turns full-width forms into ASCII "/", "\\"
    # and '"', so the folded name is cleaned again.
    folded = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    ascii_stem = _clean_filename(folded).replace('"', "_")
    if not any(c.isalnum() for c in ascii_stem):
        ascii_stem = fallback
    return (
        f"attachment; filename=\"{ascii_stem}.md\"; filename*=UTF-8''{quote(stem + '.md', safe='')}"
    )


_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def _clean_filename(name: str) -> str:
    """Replace path separators, drop bidi controls, turn control characters into spaces."""
    out = []
    for c in name:
        if c in "/\\":
            out.append("_")
        elif unicodedata.category(c) == "Cc":
            out.append(" ")
        elif c not in _BIDI_CONTROLS:
            out.append(c)
    return " ".join("".join(out).split())


@router.get("/{session_id}/files/{subdir}/{name}")
def get_file(subdir: str, name: str, paths: PathsDep) -> FileResponse:
    """Serve frames and visuals referenced by notes (e.g. ``visuals/v1.jpg``)."""
    if subdir not in SERVABLE_DIRS or "/" in name or name.startswith("."):
        raise HTTPException(404)
    path = paths.root / subdir / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)
