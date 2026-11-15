from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient
from markdown_it import MarkdownIt

from iris.config import Settings
from iris.models import (
    AlignedMoment,
    Alignment,
    EventType,
    FrameRef,
    Moment,
    Notes,
    NotesVisual,
    SessionType,
    SourceKind,
    TranscriptSegment,
    Visual,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import write
from iris.store.db import SessionStore
from iris.store.layout import SessionPaths, session_paths, write_model


def stage_ctx(
    tmp_path: Path,
    *,
    live: bool = False,
    events: list[tuple[EventType, dict]] | None = None,
    root: str = "s1",
    settings: Settings | None = None,
    **overrides,
) -> StageContext:
    ctx = StageContext(
        session_id=root,
        source=SourceKind.BROWSER_LIVE if live else SourceKind.UPLOAD,
        paths=SessionPaths(tmp_path / root).ensure(),
        settings=settings or Settings(data_dir=tmp_path, **overrides),
    )
    if events is not None:
        ctx.emit = lambda kind, data: events.append((kind, data))
    return ctx


def section(ids=(), text="## Decisions\n\nShip the prototype. Alex will test it."):
    return write.WrittenSection(
        session_type=SessionType.MEETING,
        title="Prototype review",
        blocks=[write.NoteBlock(markdown=text, visual_ids=list(ids))],
    )


def figure_to_render(ctx: StageContext, vid: str = "v1", caption: str = "Orbit") -> Visual:
    source = f"visuals/{vid}.source.jpg"
    cv2.imwrite(str(ctx.paths.root / source), np.full((20, 40, 3), 255, np.uint8))
    return Visual(
        id=vid,
        moment_id="m1",
        frame=FrameRef(t=0, path="frames/0.jpg"),
        caption=caption,
        path=source,
        source_path=source,
        description="Two bodies orbiting",
        render_as="svg",
    )


def aligned_moments(ctx: StageContext, n: int, text: str) -> list[AlignedMoment]:
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    image[:, 50:] = 255
    items = []
    for i in range(n):
        path = f"frames/{i}.jpg"
        cv2.imwrite(str(ctx.paths.root / path), image)
        frame = FrameRef(t=i * 10, path=path)
        moment = Moment(id=f"m{i}", start=i * 10, end=(i + 1) * 10, frame=frame)
        segment = TranscriptSegment(start=i * 10, end=i * 10 + 1, text=text)
        items.append(AlignedMoment(moment=moment, transcript=[segment]))
    write_model(ctx.paths.alignment, Alignment(items=items))
    return items


def notes_visual(
    vid: str = "v1", path: str = "visuals/v1.svg", caption: str = "Forces on a loop"
) -> NotesVisual:
    return NotesVisual(
        id=vid, moment_id="m1", frame=FrameRef(t=0, path="frames/0.jpg"), caption=caption, path=path
    )


def notes(
    markdown: str = "",
    visuals: Sequence[Visual] = (),
    *,
    session_id: str = "s1",
    session_type: SessionType = SessionType.MEETING,
    title: str = "Review",
    format_version: int = 2,
) -> Notes:
    return Notes(
        session_id=session_id,
        session_type=session_type,
        title=title,
        markdown=markdown,
        visuals=[NotesVisual(**v.model_dump()) for v in visuals],
        format_version=format_version,
    )


def session_with_notes(
    store: SessionStore,
    settings: Settings,
    title: str,
    *,
    source: SourceKind = SourceKind.UPLOAD,
    notes_title: str | None = None,
    **fields,
) -> tuple[str, Notes]:
    session = store.create(title, source)
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    written = notes(
        session_id=session.id, title=title if notes_title is None else notes_title, **fields
    )
    write_model(paths.notes, written)
    return session.id, written


def markdown_images(markdown: str) -> list:
    return [
        child
        for block in MarkdownIt().parse(markdown)
        for child in (block.children or [])
        if child.type == "image"
    ]


def start(client: TestClient) -> str:
    return client.post("/api/live", json={"title": "standup"}).json()["id"]


def post_audio(client: TestClient, sid: str, seq: int, data: bytes) -> int:
    return client.post(f"/api/live/{sid}/audio?seq={seq}", content=data).status_code


def post_frame(client: TestClient, sid: str, t: float, data: bytes, share: int = 1) -> int:
    return client.post(f"/api/live/{sid}/frame?t={t}&share={share}", content=data).status_code


def stop(client: TestClient, sid: str) -> int:
    return client.post(f"/api/live/{sid}/stop").status_code
