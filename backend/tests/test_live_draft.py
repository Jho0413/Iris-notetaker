"""The live draft lane, the write stage's stable parts and keys, and the draft notes endpoint.

The notes model is a fake (``write.generate_section`` patched); nothing calls a real API.
"""

import asyncio
import json
import math
import random
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.live import Lane, LiveTimeline
from iris.live.draft import DraftLane
from iris.models import (
    BoardElement,
    EventType,
    FrameRef,
    MomentContent,
    NotesDraft,
    RenderedVisual,
    Renders,
    Selection,
    SessionType,
    SourceKind,
    Transcript,
    TranscriptSegment,
    Visual,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import write
from iris.store.layout import SessionPaths, read_model, session_paths, write_model

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
            raise RuntimeError("model unavailable")
        ids = [r["id"] for r in data["records"] if r["kind"] == "visual"]
        first = data["records"][0]["start"]
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


def make_ctx(tmp_path: Path, refresh: float = 1000.0) -> tuple[StageContext, list]:
    events: list = []
    ctx = StageContext(
        session_id="s1",
        source=SourceKind.BROWSER_LIVE,
        paths=SessionPaths(tmp_path / "s1").ensure(),
        settings=Settings(
            data_dir=tmp_path, notes_context_chars=BUDGET, live_draft_refresh_s=refresh
        ),
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


async def test_parts_frozen_live_are_reused_by_the_write_stage_at_stop(tmp_path, notes):
    ctx, events = make_ctx(tmp_path)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    for upto in range(5, 45, 5):  # the transcript arrives in windows
        await publish_speech(tl, upto, upto - 5)
        await idle(0.05)
    closed, _ = write.split_parts(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    assert len(closed) >= 4
    await eventually(lambda: sum(p["later_parts"] for p in notes.payloads) == len(closed))
    await stop(tl, task)
    frozen = [p for p in notes.payloads if p["later_parts"]]
    assert len(frozen) == len(closed)  # each closed part written once
    assert [p["records"] for p in frozen] == [p.records for p in closed]

    # Stop: the write stage runs over the whole session (the timeline's outputs, promoted).
    write_model(ctx.paths.transcript, tl.transcript())
    write_model(ctx.paths.selection, tl.selection())
    live_calls = notes.calls
    await write.run(ctx)
    stop_payloads = notes.payloads[live_calls:]
    # Only the tail is new work: it was written once, early, and the refresh never came round.
    assert len(stop_payloads) == 1 and stop_payloads[0]["later_parts"] is False
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    assert draft.markdown.count("## From") == len(closed) + 1

    live = [d for k, d in events if k == EventType.NOTES_UPDATED and d.get("live")]
    assert live and all(d["final"] is False for d in live)
    assert live[-1]["completed_parts"] == len(closed)


async def test_the_tail_written_live_is_reused_when_nothing_follows_it(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path, refresh=0.05)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 12)
    await eventually(lambda: any(not p["later_parts"] for p in notes.payloads))
    await idle(0.2)
    await stop(tl, task)
    write_model(ctx.paths.transcript, tl.transcript())
    write_model(ctx.paths.selection, tl.selection())
    calls = notes.calls
    await write.run(ctx)
    assert notes.calls == calls  # every section, the tail included, came from the cache


async def test_the_tail_is_rewritten_at_most_once_per_refresh_and_only_with_new_evidence(
    tmp_path, notes
):
    ctx, events = make_ctx(tmp_path, refresh=0.5)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 1)
    await eventually(lambda: notes.calls == 1)  # the first tail is written at once
    for i in range(1, 4):  # new evidence keeps arriving within the interval
        await publish_speech(tl, i + 1, i)
        await idle(0.05)
    assert notes.calls == 1
    await eventually(lambda: notes.calls == 2, timeout=2)
    assert [r["start"] for r in notes.payloads[1]["records"]] == [0, 10, 20, 30]
    await idle(1.2)  # two more intervals, but nothing new
    assert notes.calls == 2
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    assert draft.title == "Derivatives" and "## From 0 s" in draft.markdown
    assert [d["total_parts"] for k, d in events if k == EventType.NOTES_UPDATED] == [1, 1]
    await stop(tl, task)
    assert notes.calls == 2


async def test_nothing_past_the_horizon_is_written(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path, refresh=0.05)
    tl = LiveTimeline()
    tl.absent.add(Lane.SCREEN)
    task = start_lane(ctx, tl, notes)
    # Speech is transcribed far ahead; the screen has only been read to 95 s.
    await publish_speech(tl, 30)
    contents = [content(0, 0, 50), content(1, 50, 95)]
    await tl.publish_selection(contents, [visual(1, 51)], [], selected_to=95)
    assert tl.horizon == 95
    await eventually(lambda: notes.calls >= 1)
    await idle(0.2)
    records = [r for p in notes.payloads for r in p["records"]]
    assert records and all(r["start"] < 95 for r in records)
    assert all(
        r["start"] < h
        for p, h in zip(notes.payloads, notes.horizons, strict=True)
        for r in p["records"]
    )
    assert any(r["kind"] == "visual" for r in records)
    await stop(tl, task)


async def test_an_upstream_failure_stops_model_calls(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path, refresh=0.05)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 3)
    await eventually(lambda: notes.calls == 1)
    await tl.fail_lane(Lane.AUDIO, "decoder crashed")
    await idle(0.05)
    calls = notes.calls
    await tl.publish_transcript([seg(i) for i in range(3, 40)], transcribed_to=400)
    await idle(0.3)
    assert notes.calls == calls
    await stop(tl, task)  # it still returns cleanly at stop


async def test_stop_cancels_a_tail_call_in_flight(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path)
    tl = audio_only()
    notes.gate = asyncio.Event()  # never set: the call would hang
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 2)
    await eventually(lambda: notes.calls == 1)
    await stop(tl, task)
    assert notes.cancelled == 1
    assert not list((ctx.paths.root / "notes_sections").glob("*.json"))


async def test_stop_lets_a_frozen_part_call_finish(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path)
    tl = audio_only()
    notes.gate = asyncio.Event()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 20)  # several parts close at once
    await eventually(lambda: notes.calls == 1)
    assert notes.payloads[0]["later_parts"] is True
    await tl.request_end()
    await idle(0.05)
    assert not task.done()  # the write stage would make this very call
    notes.gate.set()
    await asyncio.wait_for(task, 2)
    assert notes.calls == 1  # and no new call is started after stop
    assert len(list((ctx.paths.root / "notes_sections").glob("*.json"))) == 1


async def test_a_failed_call_is_retried_after_the_refresh_interval(tmp_path, notes):
    ctx, events = make_ctx(tmp_path, refresh=0.3)
    tl = audio_only()
    notes.fail_next = 1
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 2)
    await eventually(lambda: notes.calls == 1)
    await idle(0.1)
    assert notes.calls == 1 and not ctx.paths.notes_draft.exists()
    await eventually(lambda: notes.calls == 2, timeout=2)
    await eventually(ctx.paths.notes_draft.exists)
    assert not task.done()  # the failure didn't end the lane
    await stop(tl, task)


async def test_a_restarted_lane_reuses_its_frozen_parts(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await publish_speech(tl, 30)
    closed, _ = write.split_parts(tl.transcript(), tl.selection(), BUDGET, tl.horizon)
    await eventually(lambda: notes.calls == len(closed) + 1)
    await stop(tl, task)
    # The backend restarts: a fresh lane over the same evidence finds every section cached.
    ctx.paths.notes_draft.unlink()
    tl2 = audio_only()
    await tl2.publish_transcript(tl.segments, transcribed_to=tl.transcribed_to, language="en")
    calls = notes.calls
    task = start_lane(ctx, tl2, notes)
    await eventually(ctx.paths.notes_draft.exists)
    await idle(0.1)
    await stop(tl2, task)
    assert notes.calls == calls
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    assert draft.markdown.count("## From") == len(closed) + 1


async def test_no_evidence_means_no_calls_and_a_prompt_stop(tmp_path, notes):
    ctx, _ = make_ctx(tmp_path, refresh=0.05)
    tl = audio_only()
    task = start_lane(ctx, tl, notes)
    await tl.publish_transcript([], transcribed_to=60)  # silence
    await idle(0.1)
    await stop(tl, task)
    assert notes.calls == 0 and not ctx.paths.notes_draft.exists()


async def test_a_lane_without_upstream_lanes_drafts_everything(tmp_path, notes):
    # Absent audio and select lanes publish nothing more: their evidence is final (inf horizon).
    ctx, _ = make_ctx(tmp_path)
    tl = LiveTimeline()
    tl.segments.extend(seg(i) for i in range(3))
    tl.absent.update({Lane.AUDIO, Lane.SCREEN, Lane.SELECT})
    assert tl.horizon == math.inf
    task = start_lane(ctx, tl, notes)
    await eventually(lambda: notes.calls == 1)
    await stop(tl, task)


# --- The endpoint ------------------------------------------------------------------------------


def test_draft_endpoint_serves_the_draft_with_figures_resolved(
    client: TestClient, settings: Settings
):
    session = client.app.state.store.create("Algebra", SourceKind.BROWSER_LIVE)
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    url = f"/api/sessions/{session.id}/notes/draft"
    assert client.get(url).status_code == 404
    assert client.get("/api/sessions/missing/notes/draft").status_code == 404

    markdown = "# Algebra\n\n## Groups\n\nA group.\n\n[visual:v1]\n\n[visual:v9]"
    write_model(
        paths.notes_draft, NotesDraft(session_type="lecture", title="Algebra", markdown=markdown)
    )
    paths.live_dir.mkdir(exist_ok=True)
    write_model(paths.live_selection, Selection(visuals=[visual(1, 3)]))
    body = client.get(url).json()
    assert body["title"] == "Algebra" and body["session_type"] == "lecture"
    # Recording: the source crop; an unknown figure is dropped; the repeated title goes.
    assert "](visuals/v1.source.jpg)" in body["markdown"]
    assert "[visual:" not in body["markdown"]
    assert not body["markdown"].startswith("# Algebra")

    # Processing: selection.json takes over, and a finished render replaces the crop.
    write_model(paths.selection, Selection(visuals=[visual(1, 3)]))
    rendered = RenderedVisual(
        id="v1", path="visuals/v1.svg", render_as="svg", verified=True, attempts=1
    )
    write_model(paths.renders, Renders(visuals=[rendered]))
    assert "](visuals/v1.svg)" in client.get(url).json()["markdown"]
