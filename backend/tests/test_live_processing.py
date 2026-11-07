"""Live processing foundation: the timeline, the processor's lane lifecycle and its API wiring.

Lanes are fakes here; each lane's own behaviour is tested with that lane.
"""

import asyncio
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from iris.config import Settings
from iris.live import Lane, LiveProcessor, LiveRegistry, LiveTimeline
from iris.live.lane import LiveLane
from iris.main import create_app
from iris.models import FrameRef, Moment, MomentContent, SourceKind, TranscriptSegment
from iris.pipeline.context import StageContext
from iris.store.layout import session_paths
from tests.test_api import wait_for_status
from tests.test_live import jpeg, make_webm, post_audio, post_frame, split, start


def seg(start: float, end: float, text: str = "x") -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def moment(i: int, start: float, end: float) -> Moment:
    frame = FrameRef(t=start, path=f"frames/{int(start * 1000):09d}.jpg")
    return Moment(id=f"m{i:03d}", start=start, end=end, frame=frame)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# --- Timeline ---------------------------------------------------------------------------------


async def test_waiters_wake_when_a_watermark_passes():
    tl = LiveTimeline()
    waiter = asyncio.create_task(tl.wait_until(lambda: tl.transcribed_through(20)))
    await tl.publish_transcript([seg(0, 9)], transcribed_to=10)
    await settle()
    assert not waiter.done()
    await tl.publish_transcript([seg(10, 19)], transcribed_to=20, language="en")
    await asyncio.wait_for(waiter, 1)
    assert [s.start for s in tl.transcript().segments] == [0, 10]
    assert tl.transcript().language == "en"


async def test_watermarks_never_move_back():
    tl = LiveTimeline()
    await tl.publish_transcript([], transcribed_to=30)
    await tl.publish_transcript([], transcribed_to=12)
    await tl.advance_closed_to(40)
    await tl.publish_moment(moment(1, 0, 20))
    assert (tl.transcribed_to, tl.closed_to) == (30, 40)


async def test_selected_to_follows_closed_to_but_never_passes_it():
    tl = LiveTimeline()
    await tl.advance_closed_to(25)
    await tl.advance_selected_to(40)
    assert tl.selected_to == 25
    await tl.advance_selected_to(10)
    assert tl.selected_to == 25


async def test_out_of_order_results_are_rejected():
    tl = LiveTimeline()
    await tl.publish_transcript([seg(10, 12)], transcribed_to=12)
    with pytest.raises(ValueError):
        await tl.publish_transcript([seg(5, 6)], transcribed_to=13)
    await tl.publish_moment(moment(1, 10, 20))
    with pytest.raises(ValueError):
        await tl.publish_moment(moment(2, 0, 5))


async def test_horizon_is_the_slower_of_transcript_and_selection():
    tl = LiveTimeline()
    await tl.publish_transcript([], transcribed_to=30.6)
    content = MomentContent(moment_id="m001", start=0, end=21, elements=[])
    await tl.publish_selection([content], [], [], selected_to=21)
    assert tl.horizon == 21
    await tl.finish_lane(Lane.SELECT)
    assert tl.horizon == 30.6
    await tl.fail_lane(Lane.AUDIO, "boom")
    assert tl.horizon == math.inf


async def test_a_done_lane_releases_everything_waiting_on_it():
    tl = LiveTimeline()
    waiters = [
        asyncio.create_task(tl.wait_until(lambda: tl.transcribed_through(1e9))),
        asyncio.create_task(tl.wait_until(lambda: tl.closed_through(1e9))),
    ]
    await tl.fail_lane(Lane.AUDIO, "decoder crashed")
    await tl.finish_lane(Lane.SCREEN)
    await asyncio.wait_for(asyncio.gather(*waiters), 1)


async def finished_timeline() -> LiveTimeline:
    tl = LiveTimeline()
    for lane in Lane:
        await tl.finish_lane(lane)
    return tl


async def test_complete_needs_the_promoted_lanes_finished_with_nothing_skipped():
    tl = await finished_timeline()
    assert tl.complete
    await tl.mark_skipped_input(Lane.SCREEN)
    assert not tl.complete
    assert tl.skipped_input == {Lane.SCREEN}

    tl = await finished_timeline()
    await tl.fail_lane(Lane.SELECT, "x")
    assert not tl.complete
    assert not tl.done(Lane.SELECT) or Lane.SELECT not in tl.finished


async def test_a_failed_draft_does_not_block_promotion():
    tl = await finished_timeline()
    await tl.fail_lane(Lane.DRAFT, "rate limited")
    await tl.mark_skipped_input(Lane.DRAFT)
    assert tl.complete


async def test_downstream_lanes_learn_their_evidence_is_incomplete():
    tl = LiveTimeline()
    await tl.fail_lane(Lane.AUDIO, "decoder crashed")
    assert tl.upstream_failed(Lane.SELECT)
    assert tl.upstream_failed(Lane.DRAFT)
    assert not tl.upstream_failed(Lane.SCREEN)
    assert not tl.upstream_failed(Lane.AUDIO)


async def test_a_failed_lane_stays_failed():
    tl = LiveTimeline()
    await tl.fail_lane(Lane.AUDIO, "x")
    await tl.finish_lane(Lane.AUDIO)
    assert Lane.AUDIO in tl.failed and Lane.AUDIO not in tl.finished


# --- Processor --------------------------------------------------------------------------------


class FakeLane(LiveLane):
    """Counts wakes; flushes (records it) when stop is requested; can fail on demand."""

    def __init__(self, ctx, timeline, lane: Lane, fail: bool = False):
        super().__init__(ctx, timeline)
        self.lane = lane  # type: ignore[misc]
        self.fail = fail
        self.wakes = 0
        self.flushed = False
        self.flushed_at: datetime | None = None

    async def run(self) -> None:
        while True:
            await self.wait_for_input()
            if self.fail:
                raise RuntimeError("lane broke")
            if self.timeline.ending:
                self.flushed = True
                self.flushed_at = datetime.now(UTC)
                return
            self.wakes += 1


def fake_lanes(failing: set[Lane] = frozenset(), only: set[Lane] | None = None):
    made: dict[Lane, FakeLane] = {}

    def factory(ctx, timeline):
        for lane in only or set(Lane):
            made[lane] = FakeLane(ctx, timeline, lane, fail=lane in failing)
        return list(made.values())

    return factory, made


def ctx(tmp_path: Path) -> StageContext:
    settings = Settings(data_dir=tmp_path, openai_api_key=None)
    return StageContext(
        session_id="s1",
        source=SourceKind.BROWSER_LIVE,
        paths=session_paths(settings.sessions_dir, "s1").ensure(),
        settings=settings,
    )


async def test_input_wakes_only_its_lane_and_finish_flushes_every_lane(tmp_path: Path):
    factory, lanes = fake_lanes()
    processor = LiveProcessor(ctx(tmp_path), factory)
    processor.start()
    processor.start()  # idempotent
    processor.notify_audio()
    await settle()
    processor.notify_screen()
    await settle()
    assert lanes[Lane.AUDIO].wakes == 1
    assert lanes[Lane.SCREEN].wakes == 1
    assert lanes[Lane.SELECT].wakes == 0

    timeline = await asyncio.wait_for(processor.finish(), 1)
    assert all(lane.flushed for lane in lanes.values())
    assert timeline.finished == set(Lane)
    assert timeline.complete


async def test_a_failing_lane_does_not_stop_the_others(tmp_path: Path):
    factory, lanes = fake_lanes(failing={Lane.SCREEN})
    processor = LiveProcessor(ctx(tmp_path), factory)
    processor.start()
    processor.notify_screen()
    await settle()
    processor.notify_audio()
    await settle()
    assert "lane broke" in processor.timeline.failed[Lane.SCREEN]
    assert lanes[Lane.AUDIO].wakes == 1

    timeline = await asyncio.wait_for(processor.finish(), 1)
    assert lanes[Lane.AUDIO].flushed
    assert not timeline.complete


async def test_missing_lanes_are_done_but_never_complete(tmp_path: Path):
    factory, _ = fake_lanes(only={Lane.SELECT})
    processor = LiveProcessor(ctx(tmp_path), factory)
    processor.start()
    processor.notify_screen()  # no screen lane: ignored
    assert processor.timeline.transcribed_through(1e9)  # nothing waits on an absent lane
    timeline = await asyncio.wait_for(processor.finish(), 1)
    assert Lane.SELECT in timeline.finished
    assert not timeline.complete  # no transcript or moments were made to promote


async def test_two_lanes_of_one_kind_are_refused(tmp_path: Path):
    def factory(c, tl):
        return [FakeLane(c, tl, Lane.AUDIO), FakeLane(c, tl, Lane.AUDIO)]

    with pytest.raises(ValueError):
        LiveProcessor(ctx(tmp_path), factory)


class StubbornLane(LiveLane):
    """Ignores stop entirely."""

    lane = Lane.SELECT

    async def run(self) -> None:
        await asyncio.Event().wait()


async def test_a_lane_that_ignores_stop_is_cut_off(tmp_path: Path):
    processor = LiveProcessor(
        ctx(tmp_path), lambda c, tl: [StubbornLane(c, tl)], finish_timeout_s=0.05
    )
    processor.start()
    timeline = await asyncio.wait_for(processor.finish(), 1)
    assert Lane.SELECT in timeline.failed
    assert not timeline.complete


class EarlyLane(LiveLane):
    lane = Lane.AUDIO

    async def run(self) -> None:
        return  # gave up before stop


async def test_a_lane_that_returns_before_stop_has_failed(tmp_path: Path):
    processor = LiveProcessor(ctx(tmp_path), lambda c, tl: [EarlyLane(c, tl)])
    processor.start()
    await settle()
    assert "before stop" in processor.timeline.failed[Lane.AUDIO]
    timeline = await asyncio.wait_for(processor.finish(), 1)
    assert not timeline.complete


class AbandonedLane(LiveLane):
    """Awaits a future someone else cancels."""

    lane = Lane.AUDIO

    def __init__(self, ctx, timeline):
        super().__init__(ctx, timeline)
        self.future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def run(self) -> None:
        await self.future


async def test_a_cancelled_lane_releases_its_waiters(tmp_path: Path):
    made: list[AbandonedLane] = []

    def factory(c, tl):
        made.append(AbandonedLane(c, tl))
        return made

    processor = LiveProcessor(ctx(tmp_path), factory)
    processor.start()
    tl = processor.timeline
    waiter = asyncio.create_task(tl.wait_until(lambda: tl.transcribed_through(1e9)))
    await settle()
    made[0].future.cancel()
    await asyncio.wait_for(waiter, 1)
    assert Lane.AUDIO in tl.failed


class WaitsForScreen(LiveLane):
    """Follows the select lane's stop contract: waits for the screen lane to finish."""

    lane = Lane.SELECT

    async def run(self) -> None:
        await self.timeline.wait_until(lambda: self.timeline.done(Lane.SCREEN))
        await self.timeline.wait_until(lambda: self.timeline.ending)


async def test_a_lane_waiting_on_an_absent_lane_completes(tmp_path: Path):
    processor = LiveProcessor(ctx(tmp_path), lambda c, tl: [WaitsForScreen(c, tl)])
    processor.start()
    timeline = await asyncio.wait_for(processor.finish(), 1)
    assert Lane.SELECT in timeline.finished


class SlowFlushLane(LiveLane):
    lane = Lane.AUDIO

    async def run(self) -> None:
        await self.timeline.wait_until(lambda: self.timeline.ending)
        await asyncio.sleep(30)  # still flushing when the backend shuts down


async def test_cancelling_finish_cancels_the_lanes(tmp_path: Path):
    processor = LiveProcessor(ctx(tmp_path), lambda c, tl: [SlowFlushLane(c, tl)])
    processor.start()
    job = asyncio.create_task(processor.finish())
    await settle()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert all(task.done() for task in processor._tasks.values())
    assert Lane.AUDIO in processor.timeline.failed


async def test_registry_starts_once_finishes_and_forgets(tmp_path: Path):
    factory, lanes = fake_lanes()
    registry = LiveRegistry(lambda sid: ctx(tmp_path), lanes=factory)
    first = registry.ensure("s1")
    assert first is not None and registry.ensure("s1") is first
    timeline = await registry.finish("s1")
    assert timeline is not None and timeline.complete
    assert registry.get("s1") is None
    assert await registry.finish("s1") is None  # e.g. a re-run after the backend restarted


async def test_registry_cancel_and_shutdown_stop_lanes(tmp_path: Path):
    factory, lanes = fake_lanes()
    registry = LiveRegistry(lambda sid: ctx(tmp_path), lanes=factory)
    processor = registry.ensure("s1")
    assert processor is not None
    await settle()
    await registry.cancel("s1")
    assert registry.get("s1") is None
    assert all(task.done() for task in processor._tasks.values())
    assert not any(lane.flushed for lane in lanes.values())

    registry.ensure("s2")
    await registry.shutdown()
    assert registry.get("s2") is None


def test_disabled_registry_starts_nothing(tmp_path: Path):
    registry = LiveRegistry(lambda sid: ctx(tmp_path), enabled=False)
    assert registry.ensure("s1") is None


def test_live_window_bounds_are_validated():
    with pytest.raises(ValidationError):
        Settings(live_window_min_s=30, live_window_max_s=30)


# --- API wiring --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def recording() -> bytes:
    return make_webm()


@pytest.fixture
def lanes(client: TestClient) -> dict[Lane, FakeLane]:
    factory, made = fake_lanes()
    client.app.state.runner.live._lanes = factory  # type: ignore[attr-defined]
    return made


def registry(client: TestClient) -> LiveRegistry:
    return client.app.state.runner.live  # type: ignore[attr-defined]


def test_recording_wakes_lanes_and_stop_flushes_them_before_processing(
    client: TestClient, lanes: dict[Lane, FakeLane], recording: bytes
):
    sid = start(client)
    processor = registry(client).get(sid)
    assert processor is not None
    chunks = split(recording)
    for seq, chunk in enumerate(chunks):
        assert post_audio(client, sid, seq, chunk) == 204
    assert lanes[Lane.AUDIO].wakes >= 1
    assert lanes[Lane.SCREEN].wakes == 0

    assert client.post(f"/api/live/{sid}/end").status_code == 200
    wait_for_status(client, sid, {"done", "failed"})
    assert all(lane.flushed for lane in lanes.values())
    assert processor.timeline.finished == set(Lane)
    assert registry(client).get(sid) is None
    bus = client.app.state.bus  # type: ignore[attr-defined]
    ingest = next(
        e for e in bus.history(sid) if e.type == "stage_started" and e.data["stage"] == "ingest"
    )
    assert all(lane.flushed_at and lane.flushed_at <= ingest.at for lane in lanes.values())


def test_first_chunk_after_a_restart_starts_a_processor(
    client: TestClient, lanes: dict[Lane, FakeLane]
):
    sid = start(client)
    old = registry(client).get(sid)
    assert old is not None
    # A backend restart loses the processor; the session is still recording.
    registry(client)._processors.clear()
    assert post_frame(client, sid, 1.0, jpeg((0, 0, 255))) == 204
    new = registry(client).get(sid)
    assert new is not None and new is not old
    assert lanes[Lane.SCREEN].wakes >= 1


def test_deleting_a_recording_session_stops_its_processor(
    client: TestClient, lanes: dict[Lane, FakeLane]
):
    sid = start(client)
    processor = registry(client).get(sid)
    assert processor is not None
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert registry(client).get(sid) is None
    assert all(task.done() for task in processor._tasks.values())


def test_delete_refuses_live_input_before_stopping_processing(
    client: TestClient, lanes: dict[Lane, FakeLane], monkeypatch
):
    sid = start(client)
    live = registry(client)
    store = client.app.state.store  # type: ignore[attr-defined]
    rows_while_cancelling: list[object] = []
    real_cancel = live.cancel

    async def cancel(session_id: str) -> None:
        # Live input is refused (404) once the row is gone, so posts arriving while the
        # processor stops can't start a new one.
        rows_while_cancelling.append(store.get(session_id))
        await real_cancel(session_id)

    monkeypatch.setattr(live, "cancel", cancel)
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert rows_while_cancelling == [None]
    assert live.get(sid) is None
    assert post_frame(client, sid, 2.0, jpeg((0, 255, 0))) == 404
    assert live.get(sid) is None


def test_live_processing_can_be_turned_off(tmp_path: Path, recording: bytes):
    settings = Settings(data_dir=tmp_path / "data", openai_api_key=None, live_processing=False)
    with TestClient(create_app(settings)) as client:
        sid = start(client)
        assert registry(client).get(sid) is None
        assert post_audio(client, sid, 0, split(recording)[0]) == 204
        assert registry(client).get(sid) is None
