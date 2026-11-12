"""Finishing a live session at stop: promoting the live work and running only what is left.

The end-to-end tests drive a live session through the API with all the real lanes. The OpenAI
client is a fake behind the real metered client, so ``usage_calls.jsonl`` records every call with
its stage, as in a real session, and the calls made after stop can be counted.
"""

import json
import time
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.live import Lane, LiveTimeline, promote
from iris.live.audio import AudioLane
from iris.live.replay import chunk_webm, encode_webm
from iris.llm import client as llm_client
from iris.llm.usage import read_usage
from iris.main import create_app
from iris.models import (
    UNREADABLE_LIVE_AUDIO,
    Alignment,
    BoardElement,
    FrameIndex,
    FrameRef,
    Moment,
    MomentContent,
    Moments,
    NotesDraft,
    Selection,
    SourceKind,
    Stage,
    Transcript,
    TranscriptSegment,
    UsageRecord,
    Visual,
)
from iris.pipeline import PLAN, STAGES
from iris.pipeline.context import StageContext
from iris.pipeline.stages import render, select, synthesize
from iris.store.layout import SessionPaths, read_model, session_paths
from tests.test_detect import slide
from tests.test_live import post_audio, post_frame, start, stop
from tests.test_write import section

RATE = 48_000
SECONDS = 120
# Four slides, shown 30 s each.
SCREENS = [
    (0, "Agenda", "rect"),
    (30, "Results", "circle"),
    (60, "Method", "tri"),
    (90, "Next", "rect"),
]


def speech(seconds: int = SECONDS) -> np.ndarray:
    """Tone "words" of 0.7 s with 0.5 s pauses: every window has pauses to cut at."""
    t = np.arange(seconds * RATE) / RATE
    loud = (t % 1.2) < 0.7
    return (0.3 * loud * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


@pytest.fixture(scope="module")
def recording() -> list[tuple[float, bytes]]:
    """Two minutes of speech as ~5 s MediaRecorder-like chunks, as (recorded by t, bytes)."""
    return chunk_webm(encode_webm([speech()]))


@cache
def screen_at(t: float) -> bytes:
    _, title, shape = [screen for screen in SCREENS if screen[0] <= t][-1]
    ok, buf = cv2.imencode(".jpg", slide(title, shape=shape))
    assert ok
    return buf.tobytes()


class FakeOpenAI:
    """Every model call the pipeline makes, answered locally (no network, no cost)."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.transcribe))

    async def transcribe(self, **kwargs):
        data = kwargs["file"][1]
        seconds = (len(data) - 44) / 2 / 16_000
        # Different text per window (from its audio), so sections differ as speech arrives.
        text = f"Point {len(data) % 9973} about the slide on screen, with its working explained."
        return {"language": "english", "segments": [{"start": 0, "end": seconds, "text": text}]}

    async def parse(self, **kwargs):
        schema = kwargs["response_format"]
        if schema is select.Decisions:
            content = kwargs["messages"][1]["content"]
            ids = [json.loads(p["text"])["moment_id"] for p in content[1:] if p["type"] == "text"]
            figure = select.ElementDecision(
                kind="figure",
                content="A coloured slide",
                build=0,
                region=None,
                caption="Coloured slide",
                render_as="svg",
            )
            result = select.Decisions(
                decisions=[select.Decision(moment_id=m, keep=True, elements=[figure]) for m in ids]
            )
        elif schema is render.SvgDrawing:
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><rect width="9"/></svg>'
            )
            result = render.SvgDrawing(svg=svg)
        elif schema is render.Check:
            result = render.Check(matches=True, problems=[])
        elif schema is synthesize.EditPlan:
            result = synthesize.EditPlan(edits=[])
        else:
            payload = json.loads(kwargs["messages"][1]["content"])
            ids = [r["id"] for r in payload["records"] if r["kind"] == "visual"]
            result = section(ids)
        message = SimpleNamespace(parsed=result, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


@pytest.fixture
def fake(monkeypatch) -> FakeOpenAI:
    client = FakeOpenAI()
    monkeypatch.setattr(llm_client, "_client", lambda _key: client)
    return client


def live_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path / "data",
        "openai_api_key": "test",
        "live_window_min_s": 4.0,
        "live_window_max_s": 8.0,
        "live_transcribe_concurrency": 1,
        "live_speech_pad_s": 1.0,
        "notes_context_chars": 2000,
    }
    return Settings(**{**values, **overrides})


@pytest.fixture
def app_client(tmp_path: Path, fake: FakeOpenAI):
    settings = live_settings(tmp_path)
    with TestClient(create_app(settings)) as c:
        yield c


def calls(client: TestClient, sid: str) -> list[UsageRecord]:
    settings: Settings = client.app.state.settings  # type: ignore[attr-defined]
    return read_usage(session_paths(settings.sessions_dir, sid).usage_log).records


def by_stage(records: list[UsageRecord]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        out[str(r.stage)] = out.get(str(r.stage), 0) + 1
    return out


def events(client: TestClient, sid: str) -> list:
    return client.app.state.bus.history(sid)  # type: ignore[attr-defined]


def record_session(
    client: TestClient,
    sid: str,
    recording: list[tuple[float, bytes]],
    hold_frame: float | None = None,
) -> None:
    """Post audio and frames in session-time order, as the browser does (not paced: session time
    comes from the data). ``hold_frame`` is posted last, too late for the screen lane."""
    queue = [(t, "audio", seq, data) for seq, (t, data) in enumerate(recording)]
    queue += [(float(s), "frame", s, b"") for s in range(SECONDS)]
    for t, kind, key, data in sorted(queue, key=lambda item: (item[0], item[1])):
        if kind == "audio":
            assert post_audio(client, sid, key, data) == 204
        elif t != hold_frame:
            assert post_frame(client, sid, t, screen_at(t)) == 204
    if hold_frame is not None:
        assert post_frame(client, sid, hold_frame, screen_at(hold_frame)) == 204


def wait_until_caught_up(client: TestClient, sid: str, kinds: set[str], timeout: float = 30.0):
    """Until the live lanes have caught up: ``kinds`` of event have arrived and no model call
    has been made for a while.

    A quiet second is a heuristic: the lanes have no "idle" signal to wait on, and this runs
    them for real (threads, decoding, OpenCV), so a fake clock can't reach them. A second is far
    longer than any pause between their calls with the fake client."""
    deadline = time.monotonic() + timeout
    last, quiet_since = -1, time.monotonic()
    while True:
        n = len(calls(client, sid))
        seen = {e.type for e in events(client, sid)}
        assert time.monotonic() < deadline, f"the live lanes did not catch up: {seen}, {n} calls"
        if n != last:
            last, quiet_since = n, time.monotonic()
        if kinds <= seen and time.monotonic() - quiet_since > 1.0:
            return
        time.sleep(0.05)


def wait_for_done(client: TestClient, sid: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        session = client.get(f"/api/sessions/{sid}").json()
        runner = client.app.state.runner  # type: ignore[attr-defined]
        if session["status"] in {"done", "failed"} and not runner.is_running(sid):
            return session
        assert time.monotonic() < deadline, "the session did not finish"
        time.sleep(0.05)


def stage_events(client: TestClient, sid: str, since: int) -> list[tuple[str, str, bool]]:
    return [
        (e.type, e.data["stage"], e.data.get("promoted", False))
        for e in events(client, sid)[since:]
        if e.type in {"stage_started", "stage_finished"}
    ]


def windows(root: Path) -> int:
    return len(list((root / "live" / "transcript_windows").glob("*.json")))


LIVE_EVENTS = {"transcript_chunk", "moment_found", "visual_selected", "notes_updated"}


def draft_seen_at(monkeypatch, at: Stage) -> list[bool]:
    """Whether notes_draft.json existed when ``at`` started (promoted or run)."""
    seen: list[bool] = []
    real_promote, real_stage = promote.promote, STAGES[at]

    async def promoting(ctx, tl, stage):
        if stage == at:
            seen.append(ctx.paths.notes_draft.exists())
        return await real_promote(ctx, tl, stage)

    async def running(ctx):
        seen.append(ctx.paths.notes_draft.exists())
        await real_stage(ctx)

    monkeypatch.setattr(promote, "promote", promoting)
    monkeypatch.setitem(STAGES, at, running)
    return seen


def test_a_live_session_is_finished_at_stop_with_only_the_open_work_left(
    app_client: TestClient, recording, capsys, monkeypatch
):
    client = app_client
    draft_at_promotion = draft_seen_at(monkeypatch, Stage.TRANSCRIBE)
    sid = start(client)
    processor = client.app.state.runner.live.get(sid)  # type: ignore[attr-defined]
    timeline: LiveTimeline = processor.timeline
    root = client.app.state.settings.sessions_dir / sid  # type: ignore[attr-defined]

    record_session(client, sid, recording)
    wait_until_caught_up(client, sid, LIVE_EVENTS)

    # Everything the UI shows while recording arrived before stop.
    live = events(client, sid)
    assert {e.type for e in live} >= LIVE_EVENTS
    assert any(e.type == "notes_updated" and e.data.get("live") for e in live)
    # Sections are written ahead of stop into the write stage's cache; no draft is made.
    assert not (root / "notes_draft.json").exists()
    frozen = len(list(SessionPaths(root).notes_sections_dir.glob("*.json")))
    assert frozen >= 1
    before = calls(client, sid)
    windows_before, batches_before = windows(root), len(timeline.contents)
    live_figures = {v.id for v in timeline.visuals}

    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(recording)})
    assert r.status_code == 200
    session = wait_for_done(client, sid)
    assert session["status"] == "done", session.get("error")
    assert (root / "notes.json").exists()
    # The live lanes left no notes draft for the run to keep or drop.
    assert draft_at_promotion == [False]
    notes = client.get(f"/api/sessions/{sid}/notes").json()
    assert len(notes["visuals"]) == 4 and "::figure{#v000004}" in notes["markdown"]

    # Promoted: the stages the live lanes did report as promoted, the rest ran.
    assert timeline.complete
    stages = stage_events(client, sid, len(live))
    promoted = [s for kind, s, p in stages if p and kind == "stage_finished"]
    ran = [s for kind, s, p in stages if not p and kind == "stage_finished"]
    assert promoted == ["transcribe", "detect", "align", "select"]
    assert ran == ["ingest", "write", "render", "assemble", "synthesize"] or ran == [
        "ingest",
        "render",
        "write",
        "assemble",
        "synthesize",
    ]

    # The promoted files are exactly the timeline's.
    assert read_model(root / "transcript.json", Transcript) == timeline.transcript()
    assert read_model(root / "transcript.json", Transcript).language == "english"
    assert read_model(root / "moments.json", Moments).moments == timeline.moments
    assert read_model(root / "selection.json", Selection) == timeline.selection()
    items = read_model(root / "alignment.json", Alignment).items
    assert [i.moment for i in items] == timeline.moments
    assert not SessionPaths(root).transcript_chunks_dir.exists()  # the transcribe stage never ran
    # The UI resets its counts when processing starts, after the live windows' events: the
    # promoted transcribe step reports the whole transcript's size itself.
    finished = [
        e.data
        for e in events(client, sid)[len(live) :]
        if e.type == "stage_finished" and e.data["stage"] == "transcribe"
    ]
    assert len(timeline.segments) > 1
    assert finished == [
        {"stage": "transcribe", "promoted": True, "segments": len(timeline.segments)}
    ]

    # After stop: the last window(s), the last select batch, the sections still open at stop,
    # the last figure's render (live renders don't start after stop) and synthesis. Nothing live
    # is redone.
    after = calls(client, sid)[len(before) :]
    counts = by_stage(after)
    new_windows = windows(root) - windows_before
    new_batches = 1 if len(timeline.contents) > batches_before else 0
    new_figures = len(timeline.visuals) - len(live_figures)
    parts = read_model(root / "notes_draft.json", NotesDraft).parts
    assert parts is not None and parts > frozen
    with capsys.disabled():
        print(f"\nmodel calls after stop: {counts} (live before stop: {by_stage(before)})")
    assert new_windows == 1 and new_batches == 1 and new_figures == 1
    assert counts == {
        "transcribe": new_windows,  # the window cut at stop
        "select": new_batches,  # the moment closed at stop
        "render": 2 * new_figures,  # its figure: one drawing and one check
        "write": parts - frozen,  # the sections still open at stop, the last one included
        "synthesize": 1,  # the plan over several parts (no edits, so no rewrites)
    }
    # No repeats of live work: one call per window and per batch over the whole session.
    total = by_stage(calls(client, sid))
    assert total["transcribe"] == windows(root)
    assert total["select"] == len(
        json.loads((root / "live/select_batches.json").read_text())["batches"]
    )
    assert total["render"] == 2 * len(timeline.visuals)

    # A re-run from the API has no processor: it runs as before (here from select, all cached).
    n = len(events(client, sid))
    assert client.post(f"/api/sessions/{sid}/run?from_stage=select").status_code == 200
    assert wait_for_done(client, sid)["status"] == "done"
    assert not any(p for _, _, p in stage_events(client, sid, n))


def test_an_audio_only_session_writes_its_notes_while_recording(app_client: TestClient, recording):
    """With no screen, the horizon follows the transcript: sections are written while recording
    (not all at stop), and stop still promotes everything."""
    client = app_client
    sid = client.post("/api/live", json={"title": "call"}).json()["id"]
    timeline: LiveTimeline = client.app.state.runner.live.get(sid).timeline  # type: ignore[attr-defined]
    root = client.app.state.settings.sessions_dir / sid  # type: ignore[attr-defined]
    for seq, (_, data) in enumerate(recording):
        assert post_audio(client, sid, seq, data) == 204
    wait_until_caught_up(client, sid, {"transcript_chunk", "notes_updated"})

    assert timeline.horizon == timeline.transcribed_to > SECONDS / 2
    assert timeline.closed_to == timeline.selected_to == timeline.transcribed_to
    frozen = len(list(SessionPaths(root).notes_sections_dir.glob("*.json")))
    assert frozen >= 1
    before = calls(client, sid)
    assert set(by_stage(before)) == {"transcribe", "write"}
    live = events(client, sid)

    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(recording)})
    assert r.status_code == 200
    session = wait_for_done(client, sid)
    assert session["status"] == "done", session.get("error")
    assert timeline.complete and timeline.moments == []
    stages = stage_events(client, sid, len(live))
    promoted = [s for kind, s, p in stages if p and kind == "stage_finished"]
    assert promoted == ["transcribe", "detect", "align", "select"]
    notes = client.get(f"/api/sessions/{sid}/notes").json()
    assert notes["visuals"] == [] and "::figure" not in notes["markdown"]
    # After stop: the last window, the sections still open, and synthesis. Nothing is redone.
    parts = read_model(root / "notes_draft.json", NotesDraft).parts
    assert parts is not None and parts > frozen
    counts = by_stage(calls(client, sid)[len(before) :])
    assert counts == {"transcribe": 1, "write": parts - frozen, "synthesize": 1}


def test_a_screen_shared_for_part_of_a_recording_is_placed_only_there(
    app_client: TestClient, recording
):
    """An audio recording whose screen is shared from 30 s to 60 s, then again from 90 s: each
    share is one moment ending one frame after its last, the first closes while recording once
    the second share's frames come, the speech between them isn't aligned with either, and stop
    still promotes everything."""
    client = app_client
    sid = start(client)
    timeline: LiveTimeline = client.app.state.runner.live.get(sid).timeline  # type: ignore[attr-defined]
    root = client.app.state.settings.sessions_dir / sid  # type: ignore[attr-defined]
    queue = [(t, 1, "audio", seq, data) for seq, (t, data) in enumerate(recording)]
    queue += [(float(s), 0, "frame", 1, b"") for s in range(30, 60)]
    queue += [(float(s), 0, "frame", 2, b"") for s in range(90, SECONDS)]
    for t, _, kind, key, data in sorted(queue, key=lambda item: item[:2]):
        if kind == "audio":
            assert post_audio(client, sid, key, data) == 204
        else:
            assert post_frame(client, sid, t, screen_at(t), share=key) == 204
    wait_until_caught_up(client, sid, {"transcript_chunk", "moment_found", "visual_selected"})

    assert [(m.start, m.end) for m in timeline.moments] == [(30.0, 60.0)]
    # The second share's moment is open until stop, so nothing after the first's end is final
    # yet: the notes from 60 s on are written at stop.
    assert timeline.horizon == 60.0
    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(recording)})
    assert r.status_code == 200
    session = wait_for_done(client, sid)
    assert session["status"] == "done", session.get("error")
    assert timeline.complete
    assert read_model(root / "frames.json", FrameIndex).share_starts == [90.0]
    moments = read_model(root / "moments.json", Moments).moments
    assert [(m.start, m.end) for m in moments] == [(30.0, 60.0), (90.0, 120.0)]
    # Speech while nothing was shared isn't taken as said about what was shown.
    alignment = read_model(root / "alignment.json", Alignment)
    for item in alignment.items:
        assert not any(s.start > 60.0 and s.end < 90.0 for s in item.transcript)
    notes = client.get(f"/api/sessions/{sid}/notes").json()
    assert len(notes["visuals"]) == 2


def test_an_audio_failure_runs_the_full_plan(app_client: TestClient, recording, monkeypatch):
    client = app_client

    async def broken(self):
        raise RuntimeError("decoder crashed")

    monkeypatch.setattr(AudioLane, "run", broken)
    sid = start(client)
    record_session(client, sid, recording[:6])  # half a minute is enough here
    wait_until_caught_up(client, sid, {"moment_found"})
    # Nothing downstream of the failed lane made a call while recording.
    assert calls(client, sid) == []
    n = len(events(client, sid))
    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": 6})
    assert r.status_code == 200
    session = wait_for_done(client, sid)
    assert session["status"] == "done", session.get("error")
    stages = stage_events(client, sid, n)
    assert not any(p for _, _, p in stages)
    assert [s for kind, s, _ in stages if kind == "stage_finished"][:4] in (
        ["ingest", "transcribe", "detect", "align"],
        ["ingest", "detect", "transcribe", "align"],
    )


def test_a_skipped_frame_promotes_the_transcript_and_runs_from_detect(
    app_client: TestClient, recording, monkeypatch
):
    client = app_client
    draft_at_detect = draft_seen_at(monkeypatch, Stage.DETECT)
    sid = start(client)
    processor = client.app.state.runner.live.get(sid)  # type: ignore[attr-defined]
    timeline: LiveTimeline = processor.timeline
    root = client.app.state.settings.sessions_dir / sid  # type: ignore[attr-defined]
    # The frame at 7 s arrives after the screen lane has moved on; ingest samples it.
    record_session(client, sid, recording[:8], hold_frame=7.0)
    wait_until_caught_up(client, sid, {"transcript_chunk", "moment_found"})
    assert not (root / "notes_draft.json").exists()
    n = len(events(client, sid))
    windows_before = windows(root)
    before = len(calls(client, sid))

    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": 8})
    assert r.status_code == 200
    session = wait_for_done(client, sid)
    assert session["status"] == "done", session.get("error")
    assert timeline.skipped_input == {Lane.SCREEN} and not timeline.complete

    stages = stage_events(client, sid, n)
    promoted = [s for kind, s, p in stages if p and kind == "stage_finished"]
    ran = [s for kind, s, p in stages if not p and kind == "stage_finished"]
    assert promoted == ["transcribe"]
    # Select runs again: no notes draft is left for the run to assemble by mistake.
    assert draft_at_detect == [False]
    assert ran[:4] == ["ingest", "detect", "align", "select"]
    assert read_model(root / "transcript.json", Transcript) == timeline.transcript()
    # Transcription is paid once: after stop, only the window cut at stop.
    after = by_stage(calls(client, sid)[before:])
    assert after.get("transcribe", 0) == windows(root) - windows_before
    assert not SessionPaths(root).transcript_chunks_dir.exists()
    # Detect saw the late frame that the screen lane skipped.
    assert any(m.start <= 7.0 for m in read_model(root / "moments.json", Moments).moments)


# --- Making notes after an engine restart -----------------------------------------------------


def test_notes_made_after_a_restart_keep_the_live_work(tmp_path: Path, fake: FakeOpenAI, recording):
    """The desktop app stops a recording at quit; the next engine has no processor for it. Making
    notes starts fresh lanes that catch up from the captured files and their caches, so nothing
    done before the restart is paid for again, and their work is promoted as at a normal stop."""
    settings = live_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        sid = start(client)
        before_restart: LiveTimeline = client.app.state.runner.live.get(sid).timeline  # type: ignore[attr-defined]
        record_session(client, sid, recording)
        wait_until_caught_up(client, sid, LIVE_EVENTS)
        assert stop(client, sid) == 200
        before = calls(client, sid)
    root = settings.sessions_dir / sid
    windows_before = windows(root)
    batches_before = len(json.loads((root / "live/select_batches.json").read_text())["batches"])
    frozen = len(list(SessionPaths(root).notes_sections_dir.glob("*.json")))
    assert frozen >= 1

    with TestClient(create_app(settings)) as client:
        live = client.app.state.runner.live  # type: ignore[attr-defined]
        assert live.get(sid) is None
        r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(recording)})
        assert r.status_code == 200
        session = wait_for_done(client, sid)
        assert session["status"] == "done", session.get("error")
        assert live.get(sid) is None  # finished and forgotten, as at a normal stop

        stages = stage_events(client, sid, 0)
        promoted = [s for kind, s, p in stages if p and kind == "stage_finished"]
        assert promoted == ["transcribe", "detect", "align", "select"]
        # The transcribe stage never ran.
        assert not SessionPaths(root).transcript_chunks_dir.exists()
        transcript = read_model(root / "transcript.json", Transcript)
        assert transcript == read_model(root / "live/transcript.json", Transcript)
        assert transcript.segments[: len(before_restart.segments)] == before_restart.segments
        moments = read_model(root / "moments.json", Moments).moments
        assert len(moments) == len(SCREENS)
        notes = client.get(f"/api/sessions/{sid}/notes").json()
        assert len(notes["visuals"]) == 4

        # Only what was still open when recording stopped is new work, as at a normal stop.
        after = calls(client, sid)[len(before) :]
        visuals = read_model(root / "selection.json", Selection).visuals
        new_figures = len(visuals) - len(before_restart.visuals)
        batches = json.loads((root / "live/select_batches.json").read_text())["batches"]
        parts = read_model(root / "notes_draft.json", NotesDraft).parts
        assert parts is not None and parts > frozen
        assert windows(root) - windows_before == 1 and new_figures == 1
        assert by_stage(after) == {
            "transcribe": 1,  # the window cut at stop
            "select": len(batches) - batches_before,  # the moment closed at stop
            "render": 2 * new_figures,
            "write": parts - frozen,
            "synthesize": 1,
        }
        total = by_stage(calls(client, sid))
        assert total["transcribe"] == windows(root)
        assert total["select"] == len(batches)

        # A re-run with a chosen stage starts no lanes: it runs that stage and the rest.
        n = len(events(client, sid))
        r = client.post(f"/api/sessions/{sid}/run?from_stage=transcribe")
        assert r.status_code == 200
        assert wait_for_done(client, sid)["status"] == "done"
        assert live.get(sid) is None
        stages = stage_events(client, sid, n)
        assert not any(p for _, _, p in stages)
        assert stages[0] == ("stage_started", "transcribe", False)
        assert SessionPaths(root).transcript_chunks_dir.exists()


def test_notes_made_after_a_restart_with_live_processing_off_run_the_full_plan(
    tmp_path: Path, fake: FakeOpenAI, recording
):
    settings = live_settings(tmp_path, live_processing=False)
    with TestClient(create_app(settings)) as client:
        sid = start(client)
        record_session(client, sid, recording[:4])
        assert stop(client, sid) == 200
        assert calls(client, sid) == []
    with TestClient(create_app(settings)) as client:
        r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": 4})
        assert r.status_code == 200
        assert client.app.state.runner.live.get(sid) is None  # type: ignore[attr-defined]
        session = wait_for_done(client, sid)
        assert session["status"] == "done", session.get("error")
        stages = stage_events(client, sid, 0)
        assert not any(p for _, _, p in stages)
        ran = [s for kind, s, _ in stages if kind == "stage_finished"]
        assert ran[0] == "ingest" and "transcribe" in ran
        assert not (settings.sessions_dir / sid / "live/transcript_windows").exists()


def test_notes_made_after_a_restart_without_the_first_chunk_fail_as_before(
    tmp_path: Path, fake: FakeOpenAI, recording
):
    """Fresh lanes can't decode audio without its first chunk: they finish at once (no waiting
    for it), promote nothing, and ingest fails with the message it gives without live work."""
    settings = live_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        sid = start(client)
        for seq, (_, data) in enumerate(recording[:4]):
            if seq:
                assert post_audio(client, sid, seq, data) == 204
        assert stop(client, sid) == 200
    with TestClient(create_app(settings)) as client:
        r = client.post(f"/api/live/{sid}/end", json={"allow_gaps": True})
        assert r.status_code == 200
        session = wait_for_done(client, sid, timeout=10.0)
        assert session["status"] == "failed"
        assert session["failure_message"] == UNREADABLE_LIVE_AUDIO
        assert session["failure_kind"] == "input"
        stages = stage_events(client, sid, 0)
        assert not any(p for _, _, p in stages)
        assert calls(client, sid) == []


@pytest.mark.parametrize("status", ["recording", "stopped"])
def test_notes_asked_for_when_the_lanes_cannot_start_leave_the_session_as_it_was(
    tmp_path: Path, fake: FakeOpenAI, recording, monkeypatch, status: str
):
    """Starting the lanes is the one step of making notes that can fail before the status
    changes, so the recording can still be kept, or its notes asked for again."""
    settings = live_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        sid = start(client)
        for seq, (_, data) in enumerate(recording[:2]):
            assert post_audio(client, sid, seq, data) == 204
        if status == "stopped":
            assert stop(client, sid) == 200
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        runner = client.app.state.runner  # type: ignore[attr-defined]

        def broken(session_id: str):
            raise RuntimeError("the lanes could not start")

        monkeypatch.setattr(runner.live, "ensure", broken)
        r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": 2})
        assert r.status_code == 500
        assert client.get(f"/api/sessions/{sid}").json()["status"] == status
        assert not runner.is_running(sid)
        assert stage_events(client, sid, 0) == []


# --- Deciding what to promote -----------------------------------------------------------------


def ctx(tmp_path: Path) -> StageContext:
    settings = Settings(data_dir=tmp_path, openai_api_key=None)
    return StageContext(
        session_id="s1",
        source=SourceKind.BROWSER_LIVE,
        paths=session_paths(settings.sessions_dir, "s1").ensure(),
        settings=settings,
    )


async def timeline_with_work(c: StageContext) -> LiveTimeline:
    tl = LiveTimeline()
    frame = FrameRef(t=0, path="frames/000000000.jpg")
    (c.paths.root / "frames").mkdir(exist_ok=True)
    (c.paths.root / frame.path).write_bytes(b"jpeg")
    (c.paths.root / "visuals").mkdir(exist_ok=True)
    (c.paths.root / "visuals/v000001.source.jpg").write_bytes(b"crop")
    await tl.publish_transcript(
        [TranscriptSegment(start=0, end=4, text="b"), TranscriptSegment(start=0, end=2, text="a")],
        transcribed_to=5,
        language="english",
    )
    moment = Moment(id="m001", start=0, end=5, frame=frame)
    await tl.publish_moment(moment)
    visual = Visual(
        id="v000001",
        moment_id="m001",
        frame=frame,
        caption="c",
        path="visuals/v000001.source.jpg",
        description="d",
        render_as="svg",
        source_path="visuals/v000001.source.jpg",
    )
    element = BoardElement(kind="figure", content="d", visual_id="v000001")
    content = MomentContent(moment_id="m001", start=0, end=5, elements=[element])
    await tl.publish_selection([content], [visual], [], selected_to=5)
    for lane in Lane:
        await tl.finish_lane(lane)
    return tl


async def test_a_complete_timeline_promotes_everything(tmp_path: Path):
    c = ctx(tmp_path)
    tl = await timeline_with_work(c)
    assert promote.promotable(c, tl) == promote.EVERYTHING
    assert promote.remaining_plan(promote.EVERYTHING) == PLAN[4:]


@pytest.mark.parametrize(
    "spoil",
    [
        lambda tl, c: tl.failed.update({Lane.SCREEN: "x"}),
        lambda tl, c: tl.skipped_input.add(Lane.SELECT),
        lambda tl, c: tl.finished.discard(Lane.SELECT),  # absent: never finished
        lambda tl, c: (c.paths.root / "frames/000000000.jpg").unlink(),
        lambda tl, c: (c.paths.root / "visuals/v000001.source.jpg").unlink(),
        lambda tl, c: tl.rejected_moment_ids.append("m999"),
        lambda tl, c: tl.moments.append(tl.moments[0]),
    ],
    ids=[
        "screen failed",
        "select skipped input",
        "select absent",
        "frame missing",
        "crop missing",
        "unknown moment",
        "duplicate moment",
    ],
)
async def test_unusable_screen_work_promotes_only_the_transcript(tmp_path: Path, spoil):
    c = ctx(tmp_path)
    tl = await timeline_with_work(c)
    spoil(tl, c)
    assert promote.promotable(c, tl) == promote.TRANSCRIPT_ONLY
    plan = promote.remaining_plan(promote.TRANSCRIPT_ONLY)
    assert plan[0] == [Stage.DETECT] and Stage.TRANSCRIBE not in sum(plan, [])
    assert plan[1:] == PLAN[2:]


@pytest.mark.parametrize(
    "spoil",
    [
        lambda tl: tl.fail_lane(Lane.AUDIO, "x"),
        lambda tl: tl.mark_skipped_input(Lane.AUDIO),
    ],
    ids=["audio failed", "audio skipped input"],
)
async def test_unusable_audio_promotes_nothing(tmp_path: Path, spoil):
    c = ctx(tmp_path)
    tl = await timeline_with_work(c)
    await spoil(tl)
    assert promote.promotable(c, tl) == ()
    assert promote.promotable(c, None) == ()
    assert promote.remaining_plan(()) == PLAN[1:]


async def test_promotion_writes_the_timeline_as_it_is(tmp_path: Path):
    c = ctx(tmp_path)
    emitted: list[tuple[str, dict]] = []
    c.emit = lambda t, d: emitted.append((t, d))
    tl = await timeline_with_work(c)
    extras = [await promote.promote(c, tl, stage) for stage in promote.EVERYTHING]
    # Only transcribe reports extra data: its size, as the UI can't count it from events.
    assert extras == [{"segments": len(tl.segments)}, {}, {}, {}]
    transcript = read_model(c.paths.transcript, Transcript)
    # Order and language as the timeline holds them (not re-sorted, not re-voted).
    assert [s.text for s in transcript.segments] == ["b", "a"]
    assert transcript.language == "english"
    assert read_model(c.paths.moments, Moments).moments == tl.moments
    assert read_model(c.paths.selection, Selection) == tl.selection()
    (item,) = read_model(c.paths.alignment, Alignment).items
    assert [s.text for s in item.transcript] == ["b", "a"]
    assert [t for t, _ in emitted] == ["moment_found", "visual_selected"]
    with pytest.raises(ValueError):
        await promote.promote(c, tl, Stage.WRITE)
