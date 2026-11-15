import json
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from iris.models import (
    Alignment,
    Box,
    Build,
    EventType,
    Selection,
)
from iris.pipeline.stages import select
from iris.store.layout import read_model, write_model
from tests.builders import aligned_moments, stage_ctx
from tests.fakes import (
    answer_message,
    chat_completion,
    fake_openai,
    parsed_response,
    sdk_client,
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    events = []
    ctx = stage_ctx(tmp_path, events=events, selection_batch_size=2)
    items = aligned_moments(ctx, 3, "Revenue chart")
    parse = AsyncMock()
    monkeypatch.setattr(select, "get_openai", lambda _: fake_openai(parse=parse))
    return ctx, items, events, parse


def response(*decisions, refusal=None):
    return parsed_response(
        select.Decisions(decisions=list(decisions)) if refusal is None else None, refusal
    )


def figure(crop=None, caption="Revenue chart", build=None):
    return select.ElementDecision(
        kind="figure",
        content="Bar chart of revenue by quarter",
        build=build,
        region=crop,
        caption=caption,
        render_as="svg",
    )


def decision(mid, keep=True, crop=None):
    return select.Decision(moment_id=mid, keep=keep, elements=[figure(crop)] if keep else [])


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
    assert len(content) == 5
    assert json.loads(content[0]["text"]) == {"earlier_content": []}
    assert "Revenue chart" in content[1]["text"]
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    later = json.loads(parse.call_args_list[1].kwargs["messages"][1]["content"][0]["text"])
    assert [m["moment_id"] for m in later["earlier_content"]] == ["m0"]


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
    assert list(ctx.paths.selection_batches_dir.glob("*.json")) == []


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
    items[2].transcript[0].text = "Updated chart"
    write_model(ctx.paths.alignment, Alignment(items=items))
    await select.run(ctx)
    assert parse.await_count == 2
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
        select.Decisions(decisions=[select.Decision(moment_id="m0", keep=True, elements=[])])
        .model_dump_json()
        .encode(),
        select.Decisions(
            decisions=[select.Decision(moment_id="m0", keep=True, elements=[figure(caption=" ")])]
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
    cache = next(ctx.paths.selection_batches_dir.glob("*.json"))
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
    cache = next(ctx.paths.selection_batches_dir.glob("*.json"))
    cache.write_text("{broken")
    parse.return_value = response(decision("invented"))
    with pytest.raises(ValueError, match="exactly once"):
        await select.run(ctx)
    assert cache.read_text() == "{broken"
    parse.return_value = response(decision("m0"))
    await select.run(ctx)
    assert read_model(cache, select.Decisions).decisions == [decision("m0")]


def test_long_context_is_bounded_and_changes_invalidate_cache(setup):
    import json

    ctx, items, _, _ = setup
    for item in items:
        item.transcript[0].text = "start " + "x" * 50000 + " end"
        item.ocr_text = "y" * 5000
    prepared = select.prepare_batch(ctx, items)
    metadata = [json.loads(p["text"]) for p in prepared.content[1:] if p["type"] == "text"]
    assert sum(len(e) for m in metadata for e in m["transcript_excerpts"]) <= (
        select.TRANSCRIPT_CHARS_PER_BATCH
    )
    assert all(m["transcript_excerpted"] and m["ocr_excerpted"] for m in metadata)
    assert all(
        m["transcript_excerpts"][0].startswith("[0") or "start " in m["transcript_excerpts"][0]
        for m in metadata
    )
    assert all(m["transcript_excerpts"][-1].endswith(" end") for m in metadata)
    assert all(sum(map(len, m["ocr_excerpts"])) <= select.OCR_CHARS_PER_MOMENT for m in metadata)
    items[0].transcript[0].text = (
        items[0].transcript[0].text[:10000] + "z" + (items[0].transcript[0].text[10001:])
    )
    changed = select.prepare_batch(ctx, items)
    assert changed.content == prepared.content
    assert changed.cache_key != prepared.cache_key


@pytest.mark.parametrize("valid", [True, False])
async def test_selection_through_sdk_parser(setup, monkeypatch, valid):
    from pydantic import ValidationError

    ctx, items, _, _ = setup
    write_model(ctx.paths.alignment, Alignment(items=items[:1]))
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        result = select.Decisions(decisions=[decision("m0")]).model_dump()
        if not valid:
            del result["decisions"][0]["keep"]
        message = answer_message(json.dumps(result))
        return chat_completion(message, model=ctx.settings.vision_model)

    async with sdk_client(handle) as client:
        monkeypatch.setattr(select, "get_openai", lambda _: client)
        if valid:
            await select.run(ctx)
            assert read_model(ctx.paths.selection, Selection).visuals[0].caption == "Revenue chart"
        else:
            with pytest.raises(ValidationError):
                await select.run(ctx)
            assert not ctx.paths.selection.exists()
            assert not list(ctx.paths.selection_batches_dir.glob("*.json"))

    assert len(requests) == 1
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert requests[0]["messages"][1]["content"][2]["type"] == "image_url"


async def test_screen_content_is_read_in_build_order(setup):
    ctx, items, events, parse = setup
    items[0].moment.builds = [
        Build(t=0, frame=items[0].moment.frame),
        Build(t=4.5, frame=items[0].moment.frame, region=Box(x=0, y=0, w=10, h=10)),
    ]
    write_model(ctx.paths.alignment, Alignment(items=items[:1]))
    math = select.ElementDecision(
        kind="math", content="3(x+3)^2 - 28", build=1, region=None, caption="", render_as=None
    )
    parse.return_value = response(
        select.Decision(moment_id="m0", keep=True, elements=[math, figure(build=0)])
    )
    await select.run(ctx)
    output = read_model(ctx.paths.selection, Selection)
    [content] = output.contents
    assert [(e.kind, e.t, e.visual_id) for e in content.elements] == [
        ("math", 4.5, None),
        ("figure", 0, "v000001"),
    ]
    [visual] = output.visuals
    assert visual.render_as == "svg"
    assert visual.description == "Bar chart of revenue by quarter"
    assert visual.source_path == visual.path == "visuals/v000001.source.jpg"
    builds = json.loads(parse.call_args.kwargs["messages"][1]["content"][1]["text"])["builds"]
    assert [b["t"] for b in builds] == [0, 4.5]
