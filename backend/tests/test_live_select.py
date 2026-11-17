import asyncio
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from iris.live import LiveProcessor
from iris.live.screen import ScreenLane
from iris.live.select import SelectBatches, SelectLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm import structured
from iris.models import (
    Alignment,
    EventType,
    FrameRef,
    Moment,
    RenderedVisual,
    Renders,
    Selection,
    TranscriptSegment,
    Visual,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import align, render, select
from iris.sources.browser_live import store_frame
from iris.store.layout import read_model, write_model
from tests.api_errors import connection_error
from tests.builders import stage_ctx
from tests.fakes import Drawer, fake_openai, parsed_response
from tests.media import image_bytes, three_slides
from tests.waiting import eventually, settle


class FakeVision:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.release = asyncio.Event()
        self.release.set()
        self.started = asyncio.Event()

    async def parse(self, **kwargs):
        content = kwargs["messages"][1]["content"]
        ids = [json.loads(part["text"])["moment_id"] for part in content[1::2]]
        self.calls.append(ids)
        self.started.set()
        await self.release.wait()
        decisions = [
            select.Decision(
                moment_id=mid,
                keep=True,
                elements=[
                    select.ElementDecision(
                        kind="figure",
                        content=f"Diagram on {mid}",
                        build=None,
                        region=None,
                        caption=f"Figure {mid}",
                        render_as="svg",
                    )
                ],
            )
            for mid in ids
        ]
        return parsed_response(select.Decisions(decisions=decisions))


def rendered_visual(visual_id: str) -> RenderedVisual:
    path = f"visuals/{visual_id}.svg"
    return RenderedVisual(id=visual_id, path=path, render_as="svg", verified=True, attempts=1)


class FakeRenderer:
    def __init__(self):
        self.started: list[str] = []
        self.hold: dict[str, asyncio.Event] = {}
        self.fail: set[str] = set()
        self.running = 0
        self.max_running = 0

    async def __call__(self, _ctx, visual: Visual) -> RenderedVisual:
        self.started.append(visual.id)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if visual.id in self.hold:
                await self.hold[visual.id].wait()
            if visual.id in self.fail:
                raise RuntimeError("image model unavailable")
            return rendered_visual(visual.id)
        finally:
            self.running -= 1


@pytest.fixture
def ctx(tmp_path: Path, events: list) -> StageContext:
    return stage_ctx(
        tmp_path,
        live=True,
        events=events,
        selection_batch_size=2,
        live_speech_pad_s=5.0,
        live_render_concurrency=2,
    )


@pytest.fixture
def vision(monkeypatch) -> FakeVision:
    fake = FakeVision()
    monkeypatch.setattr(structured, "get_openai", lambda _: fake_openai(parse=fake.parse))
    return fake


@pytest.fixture
def renderer(monkeypatch) -> FakeRenderer:
    fake = FakeRenderer()
    monkeypatch.setattr(render, "render_visual", fake)
    return fake


def moment(ctx: StageContext, i: int) -> Moment:
    start = 10.0 * i
    name = f"{int(start * 1000):09d}.jpg"
    image = np.full((60, 100, 3), 40 * i % 255, np.uint8)
    cv2.putText(image, str(i), (30, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.imwrite(str(ctx.paths.frames_dir / name), image)
    frame = FrameRef(t=start, path=f"frames/{name}")
    return Moment(id=f"m{i + 1:03d}", start=start, end=start + 10, frame=frame)


def speech(i: int) -> TranscriptSegment:
    return TranscriptSegment(start=10.0 * i + 1, end=10.0 * i + 9, text=f"About figure {i}")


class Driver:
    def __init__(self, ctx: StageContext):
        self.ctx = ctx
        self.timeline = LiveTimeline()
        self.lane = SelectLane(ctx, self.timeline)
        self.task: asyncio.Task[None] | None = None

    def start(self) -> Driver:
        self.task = asyncio.create_task(self.lane.run())
        return self

    async def close(self, *indexes: int) -> list[Moment]:
        out = []
        for i in indexes:
            m = moment(self.ctx, i)
            await self.timeline.publish_moment(m)
            out.append(m)
        return out

    async def transcribe(self, *indexes: int, to: float) -> None:
        await self.timeline.publish_transcript([speech(i) for i in indexes], transcribed_to=to)

    async def selected(self, t: float) -> None:
        await asyncio.wait_for(self.timeline.wait_until(lambda: self.timeline.selected_to >= t), 5)

    async def stop(self, audio: bool = True) -> LiveTimeline:
        await self.timeline.request_end()
        await self.timeline.finish_lane(Lane.SCREEN)
        if audio:
            await self.timeline.finish_lane(Lane.AUDIO)
        assert self.task is not None
        await asyncio.wait_for(self.task, 5)
        return self.timeline


async def test_a_moment_waits_for_the_transcript_past_its_end_plus_pad(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=14.9)
    await settle(20)
    assert vision.calls == []
    await driver.transcribe(to=15.0)
    await driver.selected(10)
    assert vision.calls == [["m001"]]
    await driver.stop()


async def test_moments_closing_during_a_call_are_batched_and_ids_continue(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer, events: list
):
    driver = Driver(ctx).start()
    vision.release.clear()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    await asyncio.wait_for(vision.started.wait(), 5)
    await driver.close(1, 2, 3)
    await driver.transcribe(1, 2, 3, to=100)
    assert driver.timeline.selected_to == 0
    vision.release.set()
    await driver.selected(40)
    timeline = await driver.stop()

    assert vision.calls == [["m001"], ["m002", "m003"], ["m004"]]
    assert [v.id for v in timeline.visuals] == ["v000001", "v000002", "v000003", "v000004"]
    assert [v.moment_id for v in timeline.visuals] == ["m001", "m002", "m003", "m004"]
    assert [c.moment_id for c in timeline.contents] == ["m001", "m002", "m003", "m004"]
    assert all((ctx.paths.root / v.source_path).is_file() for v in timeline.visuals)
    selected = [data["id"] for kind, data in events if kind == EventType.VISUAL_SELECTED]
    assert selected == ["v000001", "v000002", "v000003", "v000004"]


async def test_selected_to_never_passes_closed_to(ctx: StageContext, vision, renderer):
    driver = Driver(ctx).start()
    await driver.timeline.advance_closed_to(5)
    driver.timeline.moments.append(moment(ctx, 0))
    await driver.transcribe(to=100)
    await driver.selected(5)
    assert driver.timeline.selected_to == 5
    await driver.stop()


async def test_with_every_moment_read_selected_to_follows_closed_to(
    ctx: StageContext, vision, renderer
):
    driver = Driver(ctx).start()
    await driver.timeline.advance_closed_to(30)
    await driver.selected(30)
    assert vision.calls == []
    await driver.close(3)
    await driver.timeline.advance_closed_to(60)
    await settle(20)
    assert driver.timeline.selected_to == 30
    await driver.transcribe(3, to=60)
    await driver.selected(60)
    assert vision.calls == [["m004"]]
    await driver.stop()


async def test_selection_matches_the_select_stage_when_batches_coincide(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    driver = Driver(ctx)
    moments = await driver.close(0, 1, 2)
    await driver.transcribe(0, 1, 2, to=100)
    driver.start()
    await driver.selected(30)
    timeline = await driver.stop()
    assert vision.calls == [["m001", "m002"], ["m003"]]

    items = align.align(moments, timeline.segments)
    write_model(ctx.paths.alignment, Alignment(items=items))
    await select.run(ctx)
    assert read_model(ctx.paths.selection, Selection) == timeline.selection()
    assert len(vision.calls) == 2


async def test_figures_render_in_the_background_and_a_failure_is_not_fatal(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer, events: list
):
    renderer.hold = {"v000001": asyncio.Event(), "v000002": asyncio.Event()}
    renderer.fail = {"v000002"}
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    await eventually(lambda: renderer.started == ["v000001"])
    await driver.close(1, 2)
    await driver.transcribe(1, 2, to=100)
    await driver.selected(30)
    await eventually(lambda: renderer.started == ["v000001", "v000002"])
    assert renderer.max_running == 2
    renderer.hold["v000002"].set()
    await eventually(lambda: renderer.started == ["v000001", "v000002", "v000003"])
    renderer.hold["v000001"].set()
    await eventually(lambda: renderer.running == 0)
    timeline = await driver.stop()

    assert Lane.SELECT not in timeline.failed
    rendered = [data["id"] for kind, data in events if kind == EventType.VISUAL_RENDERED]
    assert sorted(rendered) == ["v000001", "v000003"]
    assert events[-1][1] == render.rendered_event(rendered_visual(rendered[-1]))
    assert "problems" not in events[-1][1]


async def test_live_renders_hit_the_render_cache(
    ctx: StageContext, vision: FakeVision, monkeypatch, events: list
):
    drawer = Drawer(monkeypatch)
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    await driver.selected(10)
    await eventually(lambda: any(kind == EventType.VISUAL_RENDERED for kind, _ in events))
    timeline = await driver.stop()
    assert drawer.calls == ["v000001"]
    assert (ctx.paths.visuals_dir / "v000001.svg").is_file()

    write_model(ctx.paths.selection, timeline.selection())
    await render.run(ctx)
    assert drawer.calls == ["v000001"]


async def test_cancelling_the_lane_cancels_a_render_in_flight_and_caches_nothing(
    ctx: StageContext, vision: FakeVision, monkeypatch, events: list
):
    drawer = Drawer(monkeypatch)
    drawer.hold["v000001"] = asyncio.Event()
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    await eventually(lambda: drawer.calls == ["v000001"])
    assert driver.task is not None
    driver.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(driver.task, 5)
    assert drawer.cancelled == ["v000001"]
    drawer.hold["v000001"].set()
    await settle(20)
    assert not list(ctx.paths.render_cache_dir.glob("*.json"))
    assert not [kind for kind, _ in events if kind == EventType.VISUAL_RENDERED]


async def test_an_api_error_in_a_live_render_gives_the_source_crop_uncached(
    ctx: StageContext, vision: FakeVision, monkeypatch, events: list
):
    drawer = Drawer(monkeypatch)
    drawer.errors["v000001"] = connection_error()
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    await driver.selected(10)
    await eventually(lambda: any(kind == EventType.VISUAL_RENDERED for kind, _ in events))
    timeline = await driver.stop()
    [data] = [d for kind, d in events if kind == EventType.VISUAL_RENDERED]
    assert data == {
        "id": "v000001",
        "path": "visuals/v000001.jpg",
        "render_as": "screenshot",
        "verified": False,
    }
    assert not list(ctx.paths.render_cache_dir.glob("*.json"))

    del drawer.errors["v000001"]
    write_model(ctx.paths.selection, timeline.selection())
    await render.run(ctx)
    assert drawer.calls == ["v000001", "v000001"]
    [rendered] = read_model(ctx.paths.renders, Renders).visuals
    assert rendered.verified and rendered.render_as == "svg"


async def test_upstream_failure_stops_model_calls(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    driver = Driver(ctx).start()
    await driver.timeline.fail_lane(Lane.AUDIO, "decoder crashed")
    await driver.close(0, 1)
    await settle(20)
    timeline = await driver.stop(audio=False)
    assert vision.calls == [] and renderer.started == []
    assert timeline.contents == [] and timeline.selected_to == 0


async def test_upstream_failure_mid_recording_cancels_renders(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer, events: list
):
    ctx.settings.live_render_concurrency = 1
    driver = Driver(ctx).start()
    renderer.hold = {"v000001": asyncio.Event()}
    await driver.close(0, 1)
    await driver.transcribe(0, 1, to=100)
    await eventually(lambda: renderer.started == ["v000001"])
    await driver.timeline.fail_lane(Lane.SCREEN, "boom")
    await driver.close(2)
    await eventually(lambda: renderer.running == 0)
    await driver.timeline.request_end()
    await asyncio.wait_for(driver.task, 5)
    assert vision.calls == [["m001", "m002"]]
    assert renderer.started == ["v000001"]
    assert not [kind for kind, _ in events if kind == EventType.VISUAL_RENDERED]


async def test_a_fresh_lane_reuses_the_batches_already_paid_for(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    driver = Driver(ctx).start()
    for i in range(4):
        await driver.close(i)
        await driver.transcribe(i, to=10.0 * i + 15)
        await driver.selected(10.0 * i + 10)
    before = await driver.stop()
    assert vision.calls == [["m001"], ["m002"], ["m003"], ["m004"]]

    fresh = Driver(ctx)
    await fresh.close(0, 1, 2, 3)
    await fresh.transcribe(0, 1, 2, 3, to=100)
    fresh.start()
    after = await fresh.stop()
    assert len(vision.calls) == 4
    assert after.selection() == before.selection()

    third = Driver(ctx)
    await third.close(0, 1, 2, 3, 4)
    await third.transcribe(0, 1, 2, 3, 4, to=100)
    third.start()
    await third.stop()
    assert vision.calls[4:] == [["m005"]]


async def test_a_fresh_lane_waits_to_rebuild_a_batch_split_by_the_gate(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    driver = Driver(ctx).start()
    vision.release.clear()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    await asyncio.wait_for(vision.started.wait(), 5)
    await driver.close(1, 2)
    await driver.transcribe(1, 2, to=100)
    vision.release.set()
    before = await driver.stop()
    assert vision.calls == [["m001"], ["m002", "m003"]]

    fresh = Driver(ctx)
    await fresh.close(0, 1, 2)
    await fresh.transcribe(0, 1, to=25)
    fresh.start()
    await fresh.selected(10)
    await settle(20)
    assert fresh.timeline.selected_to == 10
    await fresh.transcribe(2, to=100)
    after = await fresh.stop()
    assert len(vision.calls) == 2
    assert after.selection() == before.selection()


async def test_a_second_restart_during_a_replay_keeps_the_rest_of_it(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    first = Driver(ctx).start()
    vision.release.clear()
    await first.close(0)
    await first.transcribe(0, to=100)
    await asyncio.wait_for(vision.started.wait(), 5)
    await first.close(1, 2)
    await first.transcribe(1, 2, to=100)
    vision.release.set()
    await first.stop()
    assert vision.calls == [["m001"], ["m002", "m003"]]

    second = Driver(ctx)
    await second.close(0, 1, 2)
    await second.transcribe(0, to=15)
    second.start()
    await second.selected(10)
    assert second.task is not None
    second.task.cancel()
    await asyncio.gather(second.task, return_exceptions=True)
    record = read_model(ctx.paths.live_select_batches, SelectBatches)
    assert record.batches == [["m001"], ["m002", "m003"]]

    third = Driver(ctx)
    await third.close(0, 1, 2)
    await third.transcribe(0, 1, 2, to=100)
    third.start()
    await third.stop()
    assert len(vision.calls) == 2


async def test_a_replay_that_no_longer_matches_is_dropped(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    ctx.paths.live_dir.mkdir(parents=True, exist_ok=True)
    write_model(
        ctx.paths.live_select_batches,
        SelectBatches(batches=[["m001", "m002"], ["m003"]]),
    )
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    timeline = await driver.stop()
    assert vision.calls == [["m001"]]
    assert [c.moment_id for c in timeline.contents] == ["m001"]


async def test_an_unreadable_batch_record_is_ignored(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    ctx.paths.live_dir.mkdir(parents=True, exist_ok=True)
    ctx.paths.live_select_batches.write_text("{not json")
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    await driver.selected(10)
    await driver.stop()
    assert read_model(ctx.paths.live_select_batches, SelectBatches).batches == [["m001"]]


async def test_stop_reads_the_rest_once_the_transcript_is_done_and_waits_for_renders(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    renderer.hold = {"v000001": asyncio.Event()}
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=15)
    await eventually(lambda: renderer.started == ["v000001"])
    await driver.close(1, 2)
    await driver.transcribe(1, to=24)
    await driver.selected(10)
    await driver.timeline.request_end()
    await driver.timeline.finish_lane(Lane.SCREEN)
    await settle(20)
    assert vision.calls == [["m001"]] and not driver.task.done()
    await driver.timeline.finish_lane(Lane.AUDIO)
    await eventually(lambda: len(vision.calls) == 2)
    await settle(20)
    assert not driver.task.done()
    renderer.hold["v000001"].set()
    await asyncio.wait_for(driver.task, 5)
    timeline = driver.timeline
    assert vision.calls == [["m001"], ["m002", "m003"]]
    assert timeline.selected_to == 30
    assert [c.moment_id for c in timeline.contents] == ["m001", "m002", "m003"]
    assert renderer.started == ["v000001"]


async def test_no_moments_finishes_at_stop(ctx: StageContext, vision, renderer):
    timeline = await Driver(ctx).start().stop()
    assert vision.calls == [] and timeline.selection() == Selection(visuals=[])
    assert not ctx.paths.live_select_batches.exists()


async def test_a_failed_select_call_fails_the_lane(ctx: StageContext, monkeypatch):
    async def parse(**_kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(structured, "get_openai", lambda _: fake_openai(parse=parse))
    driver = Driver(ctx).start()
    await driver.close(0)
    await driver.transcribe(0, to=100)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(driver.task, 5)


async def test_screen_and_select_lanes_through_the_processor(
    ctx: StageContext, vision: FakeVision, renderer: FakeRenderer
):
    processor = LiveProcessor(ctx, lanes=lambda c, tl: [ScreenLane(c, tl), SelectLane(c, tl)])
    processor.start()
    for i, frame in enumerate(three_slides().frames):
        store_frame(ctx.paths, float(i), 1, image_bytes(frame))
        processor.notify(Lane.SCREEN)
        await asyncio.sleep(0)
    timeline = await processor.finish()
    assert timeline.finished == {Lane.SCREEN, Lane.SELECT} and not timeline.failed
    assert [m.id for m in timeline.moments] == ["m001", "m002", "m003"]
    assert [c.moment_id for c in timeline.contents] == ["m001", "m002", "m003"]
    assert timeline.selected_to == timeline.closed_to == timeline.moments[-1].end
