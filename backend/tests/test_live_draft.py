"""The live draft lane (sections written ahead of stop) and the write stage's stable parts and keys.

The notes model is a fake (``write.generate_section`` patched); nothing calls a real API.
"""

import asyncio
import json
import math
import random
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iris import pipeline
from iris.config import Settings
from iris.live import Lane, LiveProcessor, LiveTimeline, draft
from iris.live.draft import DraftLane
from iris.models import (
    BoardElement,
    EventType,
    FrameRef,
    MomentContent,
    NotesDraft,
    Selection,
    SessionType,
    SourceKind,
    Stage,
    Transcript,
    TranscriptSegment,
    Visual,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import write
from iris.store.layout import SessionPaths, read_model, session_paths, write_model
from tests.api_errors import connection_error, status_error

BUDGET = 2000
WORDS = "the derivative measures how fast the function changes at a point "


def seg(i: int, length: int = 300) -> TranscriptSegment:
    """Segment ``i``: [10 i, 10 i + 9], about ``length`` characters of text."""
    text = f"[{i}] " + (WORDS * (length // len(WORDS) + 1))[:length]
    return TranscriptSegment(start=10 * i, end=10 * i + 9, text=text)


def content(i: int, start: float, end: float, n: int = 2) -> MomentContent:
    elements = [
        BoardElement(kind="math", content=f"x_{i}^{k} = {k}", t=start + k) for k in range(n)
    ]
    return MomentContent(moment_id=f"m{i:03d}", start=start, end=end, elements=elements)


def visual(i: int, t: float) -> Visual:
    return Visual(
        id=f"v{i}",
        moment_id=f"m{i:03d}",
        frame=FrameRef(t=t, path=f"frames/{int(t * 1000):09d}.jpg"),
        caption=f"Figure {i}",
        path=f"visuals/v{i}.source.jpg",
        source_path=f"visuals/v{i}.source.jpg",
    )


class FakeNotes:
    """Stands in for the notes model: records each payload and places every supplied visual."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.horizons: list[float] = []
        self.timeline: LiveTimeline | None = None
        self.fail_next = 0
        self.error: Exception = RuntimeError("model unavailable")  # what fail_next raises
        self.invalid = lambda data: False  # payloads answered with a section that fails validation
        self.gate: asyncio.Event | None = None  # when set, calls wait for it
        self.cancelled = 0

    async def __call__(self, ctx: StageContext, payload: str) -> write.WrittenSection:
        data = json.loads(payload)
        self.payloads.append(data)
        if self.timeline is not None:
            self.horizons.append(self.timeline.horizon)
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        if self.fail_next:
            self.fail_next -= 1
            raise self.error
        ids = [r["id"] for r in data["records"] if r["kind"] == "visual"]
        first = data["records"][0]["start"]
        if self.invalid(data):
            return write.WrittenSection(session_type=SessionType.LECTURE, title="T", blocks=[])
        return write.WrittenSection(
            session_type=data["session_type"] or SessionType.LECTURE,
            title=data["title"] or "Derivatives",
            blocks=[write.NoteBlock(markdown=f"## From {first:g} s\n\nNotes.", visual_ids=ids)],
        )

    @property
    def calls(self) -> int:
        return len(self.payloads)


@pytest.fixture
def notes(monkeypatch) -> FakeNotes:
    fake = FakeNotes()
    monkeypatch.setattr(write, "generate_section", fake)
    return fake


def make_ctx(tmp_path: Path) -> tuple[StageContext, list]:
    events: list = []
    ctx = StageContext(
        session_id="s1",
        source=SourceKind.BROWSER_LIVE,
        paths=SessionPaths(tmp_path / "s1").ensure(),
        settings=Settings(data_dir=tmp_path, notes_context_chars=BUDGET),
        emit=lambda kind, data: events.append((kind, data)),
    )
    return ctx, events


def audio_only() -> LiveTimeline:
    """A timeline whose horizon is the transcript's (no screen or select lane)."""
    tl = LiveTimeline()
    tl.absent.update({Lane.SCREEN, Lane.SELECT})
    return tl


async def eventually(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def idle(seconds: float = 0.1) -> None:
    await asyncio.sleep(seconds)


async def stop(tl: LiveTimeline, task: asyncio.Task) -> None:
    await tl.request_end()
    await asyncio.wait_for(task, 2)


def start_lane(ctx: StageContext, tl: LiveTimeline, notes: FakeNotes) -> asyncio.Task:
    notes.timeline = tl
    return asyncio.create_task(DraftLane(ctx, tl).run())


async def publish_speech(tl: LiveTimeline, upto: int, start: int = 0) -> None:
    segments = [seg(i) for i in range(start, upto)]
    await tl.publish_transcript(segments, transcribed_to=10 * upto, language="en")


# --- Planning: boundaries and keys don't depend on later evidence -----------------------------


def random_session(rng: random.Random, n: int) -> tuple[Transcript, Selection]:
    segments = [
        TranscriptSegment(start=10 * i, end=10 * i + 9, text="w " * rng.randint(20, 400))
        for i in range(n)
    ]
    contents, visuals, t = [], [], 0.0
    while t < 10 * n:
        length = rng.uniform(15, 120)
        i = len(contents)
        contents.append(content(i, t, t + length, n=rng.randint(0, 4)))
        if rng.random() < 0.4:
            visuals.append(visual(i, t + 1))
        t += length
    return Transcript(language="en", segments=segments), Selection(
        visuals=visuals, contents=contents
    )


@pytest.mark.parametrize("seed", range(8))
def test_part_boundaries_before_a_point_never_change_when_evidence_is_appended(seed):
    rng = random.Random(seed)
    transcript, selection = random_session(rng, 120)
    full = write.plan_parts(transcript, selection, BUDGET)
    assert len(full) > 5
    for before in sorted(rng.uniform(0, 1200) for _ in range(30)):
        closed, last = write.split_parts(transcript, selection, BUDGET, before)
        assert [p.records for p in closed] == [p.records for p in full[: len(closed)]]
        # Nothing at or past `before` is packed, and nothing before it is lost.
        packed = [r for p in [*closed, *([last] if last else [])] for r in p.records]
        assert all(r["start"] < before for r in packed)
        everything = [r for p in full for r in p.records]
        assert packed == [r for r in everything if r["start"] < before]


def live_prefix(
    rng: random.Random, transcript: Transcript, selection: Selection, cut: float
) -> tuple[Transcript, Selection, float]:
    """What the live timeline holds at ``cut`` s: the speech transcribed so far, and the moments
    the select lane has read, in order (it reads a moment once its speech, plus a pad, is in, and
    may still be a few moments behind). Returns them with the horizon."""
    pad = 5.0
    segments = [s for s in transcript.segments if s.start < cut]
    gated = 0
    while gated < len(selection.contents) and selection.contents[gated].end + pad <= cut:
        gated += 1
    read = selection.contents[: rng.randint(max(gated - 3, 0), gated)]
    ids = {c.moment_id for c in read}
    selected_to = read[-1].end if read else 0.0
    prefix = Selection(
        visuals=[v for v in selection.visuals if v.moment_id in ids], contents=list(read)
    )
    return Transcript(language="en", segments=segments), prefix, min(cut, selected_to)


@pytest.mark.parametrize("seed", range(8))
def test_parts_frozen_on_the_live_timeline_are_the_parts_of_the_whole_session(seed):
    # Unlike the test above, the selection here only holds the moments read by each point, as
    # on the live timeline, so moment boundaries after the horizon are not known yet.
    rng = random.Random(seed)
    transcript, selection = random_session(rng, 120)
    full = write.plan_parts(transcript, selection, BUDGET)
    frozen_seen = 0
    for cut in sorted(rng.uniform(0, 1300) for _ in range(30)):
        prefix_transcript, prefix_selection, horizon = live_prefix(rng, transcript, selection, cut)
        frozen = draft.plan(prefix_transcript, prefix_selection, BUDGET, horizon)
        assert [p.records for p in frozen] == [p.records for p in full[: len(frozen)]]
        assert all(r["start"] < horizon for p in frozen for r in p.records)
        frozen_seen = max(frozen_seen, len(frozen))
    assert frozen_seen > 3


def test_selection_that_reaches_back_before_selected_to_is_rejected():
    async def scenario() -> None:
        tl = LiveTimeline()
        await tl.publish_selection([content(0, 0, 50)], [], [], selected_to=50)
        await tl.publish_selection([content(1, 50, 80)], [], [], selected_to=80)  # in order
        with pytest.raises(ValueError, match="time order"):
            await tl.publish_selection([content(2, 70, 90)], [], [], selected_to=90)
        assert [c.moment_id for c in tl.contents] == ["m000", "m001"] and tl.selected_to == 80

    asyncio.run(scenario())


def test_parts_close_at_a_moment_boundary_and_stay_within_budget():
    # Two moments, each with its own speech; the budget fits about one and a half of them.
    segments = [seg(i, 250) for i in range(10)]
    contents = [content(0, 0, 49), content(1, 49, 100)]
    transcript, selection = Transcript(segments=segments), Selection(visuals=[], contents=contents)
    parts = write.plan_parts(transcript, selection, BUDGET)
    assert all(len(write._json(p.records)) <= BUDGET for p in parts)
    # The first part ends with the first moment's evidence, not in the middle of the second.
    assert max(r["start"] for r in parts[0].records) < 49
    assert min(r["start"] for r in parts[1].records) == 49
    # Greedy packing would have filled the first part past the boundary.
    assert len(write._json([*parts[0].records, parts[1].records[0]])) <= BUDGET


def test_a_moment_longer_than_the_budget_is_still_split():
    segments = [seg(i, 250) for i in range(20)]
    selection = Selection(visuals=[], contents=[content(0, 0, 200)])
    parts = write.plan_parts(Transcript(segments=segments), selection, BUDGET)
    assert len(parts) > 2
    assert [r for p in parts for r in p.records if r["kind"] == "speech"] == [
        {"kind": "speech", **s.model_dump()} for s in segments
    ]


def test_the_section_key_ignores_the_number_of_parts(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    part = write.WritingPart([{"kind": "speech", "start": 0, "end": 1, "text": "hi"}])
    first = write.WrittenSection(session_type=SessionType.LECTURE, title="T", blocks=[])
    payload = write.section_payload(part, language="en", first=first, later=True)
    assert set(json.loads(payload)) == {
        "records",
        "earlier_parts",
        "later_parts",
        "language",
        "title",
        "session_type",
    }
    assert json.loads(payload)["earlier_parts"] is True
    assert write.section_key(ctx, payload) == write.section_key(ctx, payload)
    last = write.section_payload(part, language="en", first=first, later=False)
    assert write.section_key(ctx, payload) != write.section_key(ctx, last)


# --- The lane ----------------------------------------------------------------------------------


def live_counts(events: list) -> list[int]:
    """The ``completed_parts`` of each live NOTES_UPDATED, in order."""
    out = []
    for kind, data in events:
        if kind == EventType.NOTES_UPDATED:
            assert data == {
                "final": False,
                "completed_parts": data["completed_parts"],
                "live": True,
            }
            out.append(data["completed_parts"])
    return out


async def stop_and_write(ctx: StageContext, tl: LiveTimeline, task: asyncio.Task) -> list[dict]:
    """Stop, promote the timeline's outputs and run the write stage over the whole session, as
    the run at stop does. Returns the payloads of the calls the write stage made."""
    await stop(tl, task)
    write_model(ctx.paths.transcript, tl.transcript())
    write_model(ctx.paths.selection, tl.selection())
    notes: FakeNotes = write.generate_section  # type: ignore[assignment]
    before = notes.calls
    await write.run(ctx)
    return notes.payloads[before:]


async def test_parts_frozen_live_are_reused_by_the_write_stage_at_stop(tmp_path, notes):
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    for upto in range(5, 45, 5):  # the transcript arrives in windows
        await publish_speech(tl, upto, upto - 5)
        await idle(0.05)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(closed) >= 4
    await eventually(lambda: notes.calls == len(closed))
    await idle(0.1)
    assert [p["records"] for p in notes.payloads] == [p.records for p in closed]  # each once
    assert all(p["later_parts"] for p in notes.payloads)
    # Only the cache is written: no draft is shown while recording.
    assert not ctx.paths.notes_draft.exists()
    assert len(list(ctx.paths.notes_sections_dir.glob("*.json"))) == len(closed)
    assert live_counts(events) == list(range(1, len(closed) + 1))

    stop_payloads = await stop_and_write(ctx, tl, task)
    # Only the part still open at stop is new work.
    assert len(stop_payloads) == 1 and stop_payloads[0]["later_parts"] is False
    notes_draft = read_model(ctx.paths.notes_draft, NotesDraft)
    assert notes_draft.markdown.count("## From") == len(closed) + 1


async def test_only_parts_closed_before_the_horizon_are_written(tmp_path, notes):
    ctx, events = make_ctx(tmp_path)
    tl = LiveTimeline()
    tl.absent.add(Lane.SCREEN)
    task = start_lane(ctx, tl, notes)
    # Speech is transcribed far ahead; the screen has only been read to 95 s.
    await publish_speech(tl, 30)
    contents = [content(0, 0, 50), content(1, 50, 95)]
    await tl.publish_selection(contents, [visual(1, 51)], [], selected_to=95)
    assert tl.horizon == 95
    frozen = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    everything, _ = write.split_parts(tl.transcript(), tl.selection(), BUDGET)
    assert frozen and len(everything) > len(frozen)
    await eventually(lambda: notes.calls == len(frozen))
    await idle(0.2)
    # Exactly the parts that closed before the horizon; nothing past it reached the model.
    assert [p["records"] for p in notes.payloads] == [p.records for p in frozen]
    assert all(
        r["start"] < horizon
        for p, horizon in zip(notes.payloads, notes.horizons, strict=True)
        for r in p["records"]
    )
    assert any(r["kind"] == "visual" for p in frozen for r in p.records)
    assert live_counts(events)[-1] == len(frozen)
    assert not ctx.paths.notes_draft.exists()
    await stop(tl, task)


async def test_a_long_open_moment_makes_no_calls_until_it_closes(tmp_path, notes):
    # A slide stays up for 20 minutes: the screen moment is open, so selected_to stays at 0 and
    # the horizon never moves. Nothing is final, so nothing is written.
    ctx, events = make_ctx(tmp_path)
    tl = LiveTimeline()
    task = start_lane(ctx, tl, notes)
    for upto in range(10, 130, 10):  # 20 minutes of speech, in windows
        await publish_speech(tl, upto, upto - 10)
        await idle(0.02)
    await idle(0.1)
    assert tl.horizon == 0 and notes.calls == 0 and events == []

    # The moment closes with nothing to keep: the horizon jumps and the closed parts are written.
    await tl.publish_selection([], [], ["m001"], selected_to=1200)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(closed) > 4
    await eventually(lambda: notes.calls == len(closed))
    assert live_counts(events)[-1] == len(closed)
    # At stop the write stage writes only the last part.
    stop_payloads = await stop_and_write(ctx, tl, task)
    assert [p["later_parts"] for p in stop_payloads] == [False]


async def test_an_upstream_failure_stops_model_calls(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 12)
    await eventually(lambda: notes.calls >= 1)
    await idle(0.05)
    await tl.fail_lane(Lane.AUDIO, "decoder crashed")
    await idle(0.05)
    calls = notes.calls
    await tl.publish_transcript([seg(i) for i in range(12, 40)], transcribed_to=400)
    await idle(0.3)
    assert notes.calls == calls
    await stop(tl, task)  # it still returns cleanly at stop


async def test_stop_lets_a_call_in_flight_finish(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path)
    tl = audio_only()
    notes.gate = asyncio.Event()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 20)  # several parts close at once
    await eventually(lambda: notes.calls == 1)
    await tl.request_end()
    await idle(0.05)
    assert not task.done()  # the write stage would make this very call
    notes.gate.set()
    await asyncio.wait_for(task, 2)
    assert notes.calls == 1 and notes.cancelled == 0  # and no new call is started after stop
    assert len(list(ctx.paths.notes_sections_dir.glob("*.json"))) == 1


async def test_a_failed_call_is_retried_after_the_retry_delay(tmp_path, notes, monkeypatch):
    monkeypatch.setattr(draft, "RETRY_DELAY_S", 0.3)
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    notes.fail_next = 1
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 12)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(closed) >= 2
    await eventually(lambda: notes.calls == 1)
    await idle(0.1)
    # The first part failed; the later parts carry its title, so they wait for its retry.
    assert notes.calls == 1 and events == []
    await eventually(lambda: notes.calls == len(closed) + 1, timeout=2)
    assert notes.payloads[1] == notes.payloads[0]  # the same payload, tried again
    assert live_counts(events)[-1] == len(closed)
    assert not task.done()  # the failure didn't end the lane
    await stop(tl, task)


async def test_a_temporary_error_is_retried_within_the_call(tmp_path, notes, monkeypatch):
    monkeypatch.setattr(draft, "RETRY_DELAY_S", 60.0)  # the lane's own retry never comes
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    notes.fail_next, notes.error = 2, connection_error()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 12)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    await eventually(lambda: notes.calls == len(closed) + 2)
    assert notes.payloads[0] == notes.payloads[1] == notes.payloads[2]
    assert live_counts(events)[-1] == len(closed)
    await stop(tl, task)


async def test_stop_ends_a_retry_wait_and_starts_no_new_call(tmp_path, notes):
    """Stop never waits for a retry of a temporary error: the processor ends the wait."""
    ctx, _ = make_ctx(tmp_path)
    ctx.settings.api_retry_delays_s = [30.0]
    notes.fail_next, notes.error = 1000, connection_error()
    processor = LiveProcessor(ctx, lambda c, t: [DraftLane(c, t)], finish_timeout_s=10)
    notes.timeline = processor.timeline
    processor.start()
    await publish_speech(processor.timeline, 12)
    await eventually(lambda: notes.calls >= 1)
    await idle(0.05)
    assert notes.calls == 1  # waiting 30 s to retry
    started = time.monotonic()
    timeline = await processor.finish()
    assert time.monotonic() - started < 2
    assert notes.calls == 1  # no retry after stop, and no new call
    assert Lane.DRAFT in timeline.finished


async def test_an_upstream_failure_ends_a_retry_wait(tmp_path, notes):
    """Nothing the lane writes can be used once an upstream lane failed: no more retries."""
    ctx, _ = make_ctx(tmp_path)
    ctx.settings.api_retry_delays_s = [30.0]
    notes.fail_next, notes.error = 1000, connection_error()
    processor = LiveProcessor(ctx, lambda c, t: [DraftLane(c, t)], finish_timeout_s=10)
    notes.timeline = processor.timeline
    processor.start()
    await publish_speech(processor.timeline, 12)
    await eventually(lambda: notes.calls >= 1)
    await idle(0.05)
    started = time.monotonic()
    await processor.timeline.fail_lane(Lane.AUDIO, "decoder crashed")
    lane = processor.lanes[Lane.DRAFT]
    # The wait ended (the call is given up) well before the 30 s delay, with no new attempt.
    await eventually(lambda: bool(lane._failures), timeout=1)
    assert time.monotonic() - started < 1
    assert notes.calls == 1
    await processor.finish()


async def test_a_blocking_error_gives_the_payload_up_at_once(tmp_path, notes, monkeypatch):
    monkeypatch.setattr(draft, "RETRY_DELAY_S", 0.02)
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    notes.fail_next, notes.error = 1, status_error(401)
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 12)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(closed) >= 2
    # The first part is given up after one call; the later ones are written as continuations.
    await eventually(lambda: notes.calls == len(closed))
    await idle(0.3)
    assert notes.calls == len(closed)
    assert sum(p == notes.payloads[0] for p in notes.payloads) == 1
    await stop(tl, task)


async def test_a_model_that_always_fails_validation_is_called_at_most_twice_per_payload(
    tmp_path, notes, monkeypatch
):
    monkeypatch.setattr(draft, "RETRY_DELAY_S", 0.02)
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    notes.invalid = lambda data: True
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 20)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(closed) >= 2
    await eventually(lambda: notes.calls == draft.MAX_ATTEMPTS * len(closed))
    await idle(1.0)  # about 50 retry delays
    # Every closed part was tried twice, then given up.
    assert notes.calls == draft.MAX_ATTEMPTS * len(closed)
    payloads = [json.dumps(p, sort_keys=True) for p in notes.payloads]
    assert len(set(payloads)) == len(closed)
    assert events == [] and not list(ctx.paths.notes_sections_dir.glob("*.json"))

    # The lane keeps working: a part that closes later is a new payload, and the model is back.
    notes.invalid = lambda data: False
    await publish_speech(tl, 40, 20)
    more = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(more) > len(closed)
    await eventually(
        lambda: notes.calls == draft.MAX_ATTEMPTS * len(closed) + len(more) - len(closed)
    )
    await idle(0.2)
    assert [p["records"] for p in notes.payloads[draft.MAX_ATTEMPTS * len(closed) :]] == [
        p.records for p in more[len(closed) :]
    ]
    assert live_counts(events)[-1] == len(more) - len(closed)
    assert not task.done()
    await stop(tl, task)


async def test_a_part_that_cannot_freeze_blocks_nothing_and_the_write_stage_retries_it(
    tmp_path, notes, monkeypatch
):
    monkeypatch.setattr(draft, "RETRY_DELAY_S", 0.02)
    ctx, events = make_ctx(tmp_path)
    speech = Transcript(language="en", segments=[seg(i) for i in range(30)])
    closed, last = write.split_parts(speech, Selection(visuals=[]), BUDGET, 300)
    assert len(closed) >= 3 and last is not None
    bad = closed[1].records[0]["start"]  # the model never gets part 1 right
    notes.invalid = lambda data: data["records"][0]["start"] == bad
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 30)
    await eventually(lambda: notes.calls == len(closed) + 1)
    await idle(0.3)
    tries = [p for p in notes.payloads if p["records"][0]["start"] == bad]
    assert len(tries) == draft.MAX_ATTEMPTS
    assert notes.calls == len(closed) + 1  # every other part once
    assert live_counts(events)[-1] == len(closed) - 1

    # At stop the write stage writes the part that failed live and the open last part, and
    # reuses everything else.
    notes.invalid = lambda data: False
    stop_payloads = await stop_and_write(ctx, tl, task)
    assert [p["records"][0]["start"] for p in stop_payloads] == [bad, last.records[0]["start"]]


async def test_no_call_starts_once_stop_is_requested_while_planning(tmp_path, notes, monkeypatch):
    ctx, _ = make_ctx(tmp_path)
    tl = audio_only()
    real_plan = draft.plan

    def plan_then_stop(*args):
        tl.ending = True  # stop arrives while the lane plans, before it writes the closed parts
        return real_plan(*args)

    monkeypatch.setattr(draft, "plan", plan_then_stop)
    await publish_speech(tl, 30)
    task = start_lane(ctx, tl, notes)
    await asyncio.wait_for(task, 2)
    assert notes.calls == 0


async def test_a_restarted_lane_reuses_its_frozen_parts(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 30)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    await eventually(lambda: notes.calls == len(closed))
    await stop(tl, task)
    # The backend restarts: a fresh lane over the same evidence finds every section cached.
    ctx2, events = make_ctx(tmp_path)
    tl2 = audio_only()
    await tl2.publish_transcript(tl.segments, transcribed_to=tl.transcribed_to, language="en")
    calls = notes.calls
    task = start_lane(ctx2, tl2, notes)
    await eventually(lambda: live_counts(events)[-1:] == [len(closed)])
    await idle(0.1)
    await stop(tl2, task)
    assert notes.calls == calls


async def test_parts_rewritten_once_the_language_is_known_never_lower_the_count(tmp_path, notes):
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    # Parts close before the language is known (silent first windows, say)...
    await tl.publish_transcript([seg(i) for i in range(20)], transcribed_to=200)
    closed = draft.plan(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(closed) >= 2
    await eventually(lambda: notes.calls == len(closed))
    # ...then it is: every payload changes, so every part is written again for stop to reuse.
    await tl.publish_transcript([seg(20)], transcribed_to=210, language="en")
    await eventually(lambda: notes.calls >= 2 * len(closed))
    await idle(0.1)
    assert {p["language"] for p in notes.payloads[len(closed) :]} == {"en"}
    counts = live_counts(events)
    assert counts == sorted(set(counts))  # it only grows
    await stop(tl, task)


async def test_no_evidence_means_no_calls_and_a_prompt_stop(tmp_path, notes):
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await tl.publish_transcript([], transcribed_to=60)  # silence
    await idle(0.1)
    await stop(tl, task)
    assert notes.calls == 0 and events == []


async def test_a_lane_without_upstream_lanes_writes_every_closed_part(tmp_path, notes):
    # Absent audio and select lanes publish nothing more: their evidence is final (inf horizon).
    ctx, _ = make_ctx(tmp_path)
    tl = LiveTimeline()
    tl.segments.extend(seg(i) for i in range(20))
    tl.absent.update({Lane.AUDIO, Lane.SCREEN, Lane.SELECT})
    assert tl.horizon == math.inf
    closed, last = write.split_parts(tl.transcript(), tl.selection(), BUDGET)
    assert closed and last is not None
    task = start_lane(ctx, tl, notes)
    await eventually(lambda: notes.calls == len(closed))
    await idle(0.1)
    await stop(tl, task)
    assert notes.calls == len(closed)  # the open last part is left to the write stage


# --- The runner --------------------------------------------------------------------------------


def test_a_run_that_selects_again_never_assembles_a_draft_of_the_old_selection(
    client: TestClient, settings: Settings, monkeypatch
):
    session = client.app.state.store.create("Algebra", SourceKind.UPLOAD)
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    old = NotesDraft(session_type="lecture", title="Algebra", markdown="Groups.\n\n[visual:v1]")
    write_model(paths.notes_draft, old)
    write_model(paths.selection, Selection(visuals=[visual(1, 3)]))

    selecting, release = threading.Event(), threading.Event()
    drafts_seen_by_select: list[bool] = []
    drafts_seen_by_write: list[bool] = []

    async def nothing(ctx: StageContext) -> None:
        pass

    async def select(ctx: StageContext) -> None:
        drafts_seen_by_select.append(ctx.paths.notes_draft.exists())
        selecting.set()
        await asyncio.to_thread(release.wait, 5)
        # Selected again: the figure that was v1 now has another id.
        write_model(ctx.paths.selection, Selection(visuals=[visual(2, 3)]))

    async def write_stage(ctx: StageContext) -> None:
        drafts_seen_by_write.append(ctx.paths.notes_draft.exists())

    for stage in list(pipeline.STAGES):
        monkeypatch.setitem(pipeline.STAGES, stage, nothing)
    monkeypatch.setitem(pipeline.STAGES, Stage.SELECT, select)
    monkeypatch.setitem(pipeline.STAGES, Stage.WRITE, write_stage)
    runner = client.app.state.runner

    def finish() -> None:
        deadline = time.monotonic() + 5
        while runner.is_running(session.id):
            assert time.monotonic() < deadline, "the run never finished"
            time.sleep(0.02)

    # A full run selects again: the old draft is gone before select starts.
    assert client.post(f"/api/sessions/{session.id}/run").status_code == 200
    try:
        assert selecting.wait(5)
    finally:
        release.set()
    finish()
    assert drafts_seen_by_select == [False] and drafts_seen_by_write == [False]

    # A run that starts at write (as one promoted from live does) keeps it: write replaces it.
    write_model(paths.notes_draft, old)
    assert client.post(f"/api/sessions/{session.id}/run?from_stage=write").status_code == 200
    finish()
    assert drafts_seen_by_write == [False, True]


def test_the_draft_notes_endpoint_is_gone(client: TestClient):
    session = client.app.state.store.create("Algebra", SourceKind.BROWSER_LIVE)
    assert client.get(f"/api/sessions/{session.id}/notes/draft").status_code == 404
    assert (
        "/api/sessions/{session_id}/notes/draft" not in client.get("/openapi.json").json()["paths"]
    )
