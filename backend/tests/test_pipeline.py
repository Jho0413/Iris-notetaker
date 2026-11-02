from pathlib import Path

import pytest

from iris.config import Settings
from iris.models import (
    Box,
    FrameRef,
    Moment,
    Moments,
    Notes,
    NotesDraft,
    Selection,
    SessionType,
    SourceKind,
    Stage,
    Transcript,
    TranscriptSegment,
    Visual,
)
from iris.pipeline import PLAN, plan_from
from iris.pipeline.context import StageContext
from iris.pipeline.stages import align, assemble
from iris.store.layout import SessionPaths, read_model, write_model


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    return StageContext(
        session_id="s1",
        source=SourceKind.UPLOAD,
        paths=SessionPaths(tmp_path / "s1").ensure(),
        settings=Settings(data_dir=tmp_path),
    )


def moment(mid: str, start: float, end: float) -> Moment:
    return Moment(id=mid, start=start, end=end, frame=FrameRef(t=start, path="frames/x.jpg"))


def seg(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def test_plan_from():
    assert plan_from(None) == PLAN
    assert plan_from(Stage.DETECT)[0] == [Stage.TRANSCRIBE, Stage.DETECT]
    assert plan_from(Stage.ASSEMBLE) == [[Stage.ASSEMBLE]]


def test_align_assigns_overlapping_segments():
    items = align.align(
        [moment("m1", 0, 10), moment("m2", 10, 20)],
        [seg(0, 4, "a"), seg(8, 12, "b"), seg(15, 19, "c")],
    )
    assert [s.text for s in items[0].transcript] == ["a", "b"]
    assert [s.text for s in items[1].transcript] == ["b", "c"]


async def test_align_stage_reads_and_writes_files(ctx: StageContext):
    write_model(ctx.paths.moments, Moments(moments=[moment("m1", 0, 5)]))
    write_model(ctx.paths.transcript, Transcript(segments=[seg(1, 2, "hello")]))
    await align.run(ctx)
    assert ctx.paths.alignment.exists()


async def test_assemble_resolves_markers_and_drops_unknown(ctx: StageContext):
    visual = Visual(
        id="v1",
        moment_id="m1",
        frame=FrameRef(t=3, path="frames/000003000.jpg"),
        crop=Box(x=0, y=0, w=10, h=10),
        caption="Q3 revenue chart",
        path="visuals/v1.jpg",
    )
    write_model(ctx.paths.selection, Selection(visuals=[visual]))
    write_model(
        ctx.paths.notes_draft,
        NotesDraft(
            session_type=SessionType.MEETING,
            title="Review",
            markdown="Intro\n\n[visual:v1]\n\n[visual:nope]\n",
        ),
    )
    await assemble.run(ctx)
    notes = read_model(ctx.paths.notes, Notes)
    assert "![Q3 revenue chart](visuals/v1.jpg)" in notes.markdown
    assert "nope" not in notes.markdown
    assert ctx.paths.notes_md.read_text() == notes.markdown


def test_leading_heading_repeating_the_title_is_dropped():
    md = "## Completing the Square\n\n**Problem:** x\n\n## Completing the Square\n"
    assert assemble.drop_title_heading(md, "Completing the square") == (
        "**Problem:** x\n\n## Completing the Square\n"
    )
    assert assemble.drop_title_heading("## Other\n\nText", "Title") == "## Other\n\nText"
