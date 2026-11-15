from pathlib import Path

import pytest

from iris.config import Settings
from iris.models import (
    AlignedMoment,
    FrameRef,
    Moment,
    SessionType,
    SourceKind,
    TranscriptSegment,
    Visual,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import render, select, synthesize, write
from iris.store.layout import SessionPaths

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x02\x08\x02\x00\x00\x00"
    b"\xfd\xd4\x9as\x00\x00\x00\x16IDAT\x08\x1dc\x8c\x8a\x8ab```\x8c\x8a\x8ab``\x00\x00\x12\x11"
    b"\x02\x1f\xa0\x95\xc8?\x00\x00\x00\x00IEND\xaeB`\x82"
)

WRITE_KEY = "0930ab10bc3f4333833097cd805bdfa6fd43e86fdbeef69c86974ccfbcca3a47"
SYNTHESIZE_KEY = "b7b34d054cc16038bcf62fa7e48a5158be24a421c8416f1c544663351b83e062"
SELECT_KEY = "0ba97be394795fbcbcb275bcc3ca5c24f9773dc1328d7b88c272776decea1410"
RENDER_KEY = "ce1e53622a0733c5d0bc08118a22d93865ca8d2ee7a8cf1bb1a8b7acaf1eebab"


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        openai_api_key=None,
        vision_model="vision-model",
        notes_model="notes-model",
        diagram_model="diagram-model",
        image_model="image-model",
        synthesis_model="synthesis-model",
        image_quality="high",
        reasoning_effort="medium",
        diagram_reasoning_effort=None,
        check_reasoning_effort=None,
        render_attempts=2,
    )
    paths = SessionPaths(tmp_path / "s1").ensure()
    return StageContext("s1", SourceKind.UPLOAD, paths, settings)


def test_the_write_cache_key_is_unchanged(ctx):
    part = write.WritingPart(
        records=[
            {"kind": "speech", "start": 0.0, "end": 4.0, "text": "Loops repeat a block — café."},
            {"kind": "visual", "id": "v1", "t": 2.0, "caption": "A for loop"},
        ]
    )
    first = write.WrittenSection(session_type=SessionType.LECTURE, title="Loops", blocks=[])
    payload = write.section_payload(part, language="en", first=first, later=True)
    assert write.section_key(ctx, payload) == WRITE_KEY


async def test_the_synthesize_cache_key_is_unchanged(ctx, monkeypatch):
    async def answer(*_args):
        return synthesize.EditPlan(edits=[])

    monkeypatch.setattr(synthesize, "generate", answer)
    payload = '{"sections": ["Loops repeat a block — café."]}'
    await synthesize.cached(
        ctx,
        "synthesis_plan",
        synthesize.PLAN_PROMPT,
        payload,
        synthesize.EditPlan,
        "low",
        lambda plan: plan,
    )
    assert [p.stem for p in ctx.paths.synthesis_cache_dir.iterdir()] == [SYNTHESIZE_KEY]


def test_the_select_cache_key_is_unchanged(ctx):
    (ctx.paths.frames_dir / "000002000.jpg").write_bytes(TINY_PNG)
    item = AlignedMoment(
        moment=Moment(
            id="m001", start=1.0, end=6.0, frame=FrameRef(t=2.0, path="frames/000002000.jpg")
        ),
        transcript=[TranscriptSegment(start=1.0, end=5.0, text="Loops repeat a block — café.")],
    )
    batch = select.prepare_batch(ctx, [item], earlier=[{"id": "v0", "caption": "Agenda"}])
    assert batch.cache_key == SELECT_KEY


def test_the_render_cache_key_is_unchanged(ctx):
    visual = Visual(
        id="v1",
        moment_id="m001",
        frame=FrameRef(t=2.0, path="frames/000002000.jpg"),
        caption="A for loop",
        path="visuals/v1.svg",
        description="A loop arrow returning to the start of a block — café",
        render_as="svg",
    )
    assert render._cache_key(ctx, visual, TINY_PNG) == RENDER_KEY
