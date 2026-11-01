from types import SimpleNamespace
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from iris.config import Settings
from iris.models import (
    AlignedMoment,
    Alignment,
    Box,
    EventType,
    FrameRef,
    Moment,
    Selection,
    SourceKind,
    TranscriptSegment,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import select
from iris.store.layout import SessionPaths, read_model, write_model


@pytest.fixture
def setup(tmp_path, monkeypatch):
    events = []
    ctx = StageContext(
        session_id="s1",
        source=SourceKind.UPLOAD,
        paths=SessionPaths(tmp_path).ensure(),
        settings=Settings(data_dir=tmp_path, selection_batch_size=2),
        emit=lambda kind, data: events.append((kind, data)),
    )
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    image[:, 50:] = 255
    items = []
    for i in range(3):
        path = f"frames/{i}.jpg"
        cv2.imwrite(str(tmp_path / path), image)
        items.append(
            AlignedMoment(
                moment=Moment(
                    id=f"m{i}", start=i * 10, end=(i + 1) * 10, frame=FrameRef(t=i * 10, path=path)
                ),
                transcript=[TranscriptSegment(start=i * 10, end=i * 10 + 1, text="Revenue chart")],
            )
        )
    write_model(ctx.paths.alignment, Alignment(items=items))
    parse = AsyncMock()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(select, "get_openai", lambda _: client)
    return ctx, items, events, parse


def response(*decisions, refusal=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    parsed=select.Decisions(decisions=list(decisions)) if refusal is None else None,
                    refusal=refusal,
                )
            )
        ]
    )


def decision(mid, keep=True, crop=None):
    return select.Decision(
        moment_id=mid, keep=keep, caption="Revenue chart" if keep else "", crop=crop
    )


async def test_batches_render_real_pixels_and_emit(setup):
    ctx, _, events, parse = setup
    parse.side_effect = [
        response(decision("m1", False), decision("m0")),
        response(decision("m2", crop=Box(x=50, y=0, w=50, h=60))),
    ]
    await select.run(ctx)
    output = read_model(ctx.paths.selection, Selection)
    assert [v.moment_id for v in output.visuals] == ["m0", "m2"]
    assert output.rejected_moment_ids == ["m1"]
    assert (ctx.paths.root / output.visuals[0].path).read_bytes() == (
        ctx.paths.frames_dir / "0.jpg"
    ).read_bytes()
    crop = cv2.imread(str(ctx.paths.root / output.visuals[1].path))
    assert crop.shape == (60, 50, 3)
    assert crop.mean() > 250
    assert [e[0] for e in events] == [EventType.VISUAL_SELECTED] * 2
    assert parse.await_count == 2
    request = parse.call_args_list[0].kwargs
    assert request["model"] == ctx.settings.vision_model
    assert request["response_format"] is select.Decisions
    content = request["messages"][1]["content"]
    assert len(content) == 4
    assert "Revenue chart" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize(
    "crop",
    [
        Box(x=-1, y=0, w=50, h=60),
        Box(x=0, y=0, w=101, h=60),
        Box(x=0, y=0, w=0, h=10),
        Box(x=0, y=59, w=50, h=2),
    ],
)
async def test_bad_crops_preserve_full_frame(setup, crop):
    ctx, items, _, parse = setup
    write_model(ctx.paths.alignment, Alignment(items=items[:1]))
    parse.return_value = response(decision("m0", crop=crop))
    await select.run(ctx)
    v = read_model(ctx.paths.selection, Selection).visuals[0]
    assert v.crop is None
    assert (ctx.paths.root / v.path).read_bytes() == (ctx.paths.frames_dir / "0.jpg").read_bytes()


@pytest.mark.parametrize("ids", [["m0"], ["m0", "m0"], ["m0", "invented"]])
async def test_invalid_decision_ids_do_not_publish_a_batch(setup, ids):
    ctx, _, events, parse = setup
    parse.return_value = response(*(decision(mid) for mid in ids))
    with pytest.raises(ValueError, match="exactly once"):
        await select.run(ctx)
    assert not ctx.paths.selection.exists()
    assert events == []
    assert list((ctx.paths.root / "selection_batches").glob("*.json")) == []


async def test_failed_batch_resumes_from_content_cache(setup):
    ctx, items, _, parse = setup
    parse.side_effect = [response(decision("m0"), decision("m1", False)), RuntimeError("offline")]
    with pytest.raises(RuntimeError, match="offline"):
        await select.run(ctx)
    assert len(read_model(ctx.paths.selection, Selection).visuals) == 1
    parse.reset_mock(side_effect=True)
    parse.return_value = response(decision("m2"))
    await select.run(ctx)
    assert parse.await_count == 1
    await select.run(ctx)
    assert parse.await_count == 1
    # A changed transcript invalidates only its batch.
    items[2].transcript[0].text = "Updated chart"
    write_model(ctx.paths.alignment, Alignment(items=items))
    await select.run(ctx)
    assert parse.await_count == 2
    # Changed pixels also invalidate that batch.
    cv2.imwrite(str(ctx.paths.frames_dir / "2.jpg"), np.zeros((60, 100, 3), dtype=np.uint8))
    await select.run(ctx)
    assert parse.await_count == 3
    ctx.settings.vision_model = "another-model"
    parse.side_effect = [response(decision("m0"), decision("m1", False)), response(decision("m2"))]
    await select.run(ctx)
    assert parse.await_count == 5


async def test_empty_alignment_needs_no_api(setup):
    ctx, _, _, parse = setup
    write_model(ctx.paths.alignment, Alignment(items=[]))
    await select.run(ctx)
    assert read_model(ctx.paths.selection, Selection) == Selection(visuals=[])
    parse.assert_not_awaited()


async def test_refusal_is_an_explicit_failure(setup):
    ctx, _, _, parse = setup
    parse.return_value = response(refusal="Cannot process")
    with pytest.raises(ValueError, match="refused selection"):
        await select.run(ctx)
    assert not ctx.paths.selection.exists()


async def test_frame_must_be_readable_and_inside_frames(setup):
    ctx, items, _, parse = setup
    (ctx.paths.frames_dir / "0.jpg").write_bytes(b"broken")
    with pytest.raises(ValueError, match="unreadable selection frame"):
        await select.run(ctx)
    items[0].moment.frame.path = "../outside.jpg"
    write_model(ctx.paths.alignment, Alignment(items=items))
    with pytest.raises(ValueError, match="inside the session"):
        await select.run(ctx)
    parse.assert_not_awaited()


@pytest.mark.parametrize(
    "damaged",
    [
        b"{broken",
        b"\xff",
        b'{"wrong_schema": []}',
        select.Decisions(decisions=[decision("invented")]).model_dump_json().encode(),
        select.Decisions(
            decisions=[
                select.Decision(
                    moment_id="m0",
                    keep=True,
                    caption="  ",
                    crop=None,
                )
            ]
        )
        .model_dump_json()
        .encode(),
    ],
)
async def test_damaged_cache_is_replaced_and_reused(setup, damaged):
    ctx, items, _, parse = setup
    write_model(ctx.paths.alignment, Alignment(items=items[:1]))
    parse.return_value = response(decision("m0"))
    await select.run(ctx)
    cache = next((ctx.paths.root / "selection_batches").glob("*.json"))
    cache.write_bytes(damaged)
    await select.run(ctx)
    assert parse.await_count == 2
    assert read_model(cache, select.Decisions).decisions == [decision("m0")]
    await select.run(ctx)
    assert parse.await_count == 2


async def test_failed_replacement_does_not_cache_invalid_model_output(setup):
    ctx, items, _, parse = setup
    write_model(ctx.paths.alignment, Alignment(items=items[:1]))
    parse.return_value = response(decision("m0"))
    await select.run(ctx)
    cache = next((ctx.paths.root / "selection_batches").glob("*.json"))
    cache.write_text("{broken")
    parse.return_value = response(decision("invented"))
    with pytest.raises(ValueError, match="exactly once"):
        await select.run(ctx)
    assert cache.read_text() == "{broken"
    parse.return_value = response(decision("m0"))
    await select.run(ctx)
    assert read_model(cache, select.Decisions).decisions == [decision("m0")]
