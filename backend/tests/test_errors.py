"""Error handling: classifying errors, retrying temporary ones, figures falling back to the source
crop, failing fast on blocking errors, and what the user is told (only our own messages).

No network: SDK exceptions are built locally (``tests/api_errors.py``) and model calls are faked.
"""

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cv2
import numpy as np
import openai
import pytest
from fastapi.testclient import TestClient

from iris import cli
from iris.api.routes import events as events_route
from iris.config import Settings
from iris.events import EventBus
from iris.jobs.runner import JobRunner
from iris.llm import client as llm_client
from iris.llm.client import MissingAPIKey
from iris.llm.errors import (
    ErrorKind,
    StageError,
    UserFacingError,
    classify,
    gather_units,
    retry_temporary,
)
from iris.main import create_app
from iris.models import (
    GENERIC_FAILURE,
    INTERRUPTED_RUN,
    LIVE_WITHOUT_AUDIO,
    NOTHING_TO_WRITE,
    UNREADABLE_LIVE_AUDIO,
    UNREADABLE_UPLOAD,
    UPLOAD_WITHOUT_AUDIO,
    AlignedMoment,
    Alignment,
    EventType,
    FailureKind,
    FailureMessage,
    FrameRef,
    Moment,
    Notes,
    Renders,
    Selection,
    SessionStatus,
    SourceKind,
    Stage,
    Transcript,
    TranscriptSegment,
    Visual,
)
from iris.pipeline import STAGES
from iris.pipeline.stages import render, select, write
from iris.store.db import SessionStore
from iris.store.layout import read_model, write_model
from tests.api_errors import connection_error, status_error, timeout_error
from tests.test_api import wait_for_status
from tests.test_ingest import make_media
from tests.test_write import section

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"><rect width="20"/></svg>'

# --- classify ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (connection_error(), ErrorKind.TEMPORARY),
        (timeout_error(), ErrorKind.TEMPORARY),
        (status_error(408), ErrorKind.TEMPORARY),
        (status_error(409), ErrorKind.TEMPORARY),
        (status_error(429, code="rate_limit_exceeded"), ErrorKind.TEMPORARY),
        (status_error(429), ErrorKind.TEMPORARY),
        (status_error(500), ErrorKind.TEMPORARY),
        (status_error(502), ErrorKind.TEMPORARY),
        (status_error(503), ErrorKind.TEMPORARY),
        (status_error(401), ErrorKind.BLOCKING),
        (status_error(403), ErrorKind.BLOCKING),
        (status_error(404, code="model_not_found"), ErrorKind.BLOCKING),
        (status_error(429, code="insufficient_quota"), ErrorKind.BLOCKING),
        (MissingAPIKey("no key"), ErrorKind.BLOCKING),
        (status_error(400), ErrorKind.REJECTED),
        (status_error(422), ErrorKind.REJECTED),
        (ValueError("vision model refused: no"), ErrorKind.BAD_OUTPUT),
        (json.JSONDecodeError("bad", "{", 0), ErrorKind.BAD_OUTPUT),
        (
            openai.LengthFinishReasonError(completion=SimpleNamespace(usage=None)),
            ErrorKind.BAD_OUTPUT,
        ),
        (openai.ContentFilterFinishReasonError(), ErrorKind.BAD_OUTPUT),
        (status_error(418), ErrorKind.INTERNAL),
        (RuntimeError("bug"), ErrorKind.INTERNAL),
        (OSError("disk full"), ErrorKind.INTERNAL),
        (UserFacingError(UNREADABLE_UPLOAD), ErrorKind.INTERNAL),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else str(v),
)
def test_classify(exc: BaseException, kind: ErrorKind):
    assert classify(exc) is kind


def test_classify_looks_through_stage_errors():
    assert classify(StageError(Stage.WRITE, status_error(401))) is ErrorKind.BLOCKING
    assert classify(StageError(Stage.WRITE, connection_error())) is ErrorKind.TEMPORARY


def test_failure_messages_are_the_fixed_list():
    """The constants are exactly the messages the API may serve (``FailureMessage``)."""
    constants = {
        GENERIC_FAILURE,
        INTERRUPTED_RUN,
        UNREADABLE_UPLOAD,
        UPLOAD_WITHOUT_AUDIO,
        LIVE_WITHOUT_AUDIO,
        UNREADABLE_LIVE_AUDIO,
        NOTHING_TO_WRITE,
    }
    from typing import get_args

    assert set(get_args(FailureMessage)) == constants


# --- retry_temporary, gather_units ---------------------------------------------------------------


def flaky(*errors: BaseException, result: str = "ok") -> AsyncMock:
    """A call that raises each of ``errors`` in turn, then returns ``result``."""
    return AsyncMock(side_effect=[*errors, result])


async def test_retry_temporary_retries_until_it_succeeds():
    call = flaky(connection_error(), status_error(503), status_error(429))
    assert await retry_temporary(call, [0, 0, 0], what="test") == "ok"
    assert call.await_count == 4


async def test_retry_temporary_raises_the_last_temporary_error_after_the_delays():
    errors = [connection_error(f"down {i}") for i in range(3)]
    call = AsyncMock(side_effect=errors)
    with pytest.raises(openai.APIConnectionError, match="down 2"):
        await retry_temporary(call, [0, 0], what="test")
    assert call.await_count == 3


async def test_retry_temporary_waits_the_given_delays(monkeypatch):
    waited = []

    async def sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    call = flaky(connection_error(), connection_error())
    assert await retry_temporary(call, [15, 60, 120], what="test") == "ok"
    assert waited == [15, 60]


@pytest.mark.parametrize(
    "error",
    [status_error(401), status_error(429, code="insufficient_quota"), MissingAPIKey("x")]
    + [status_error(400), status_error(422), ValueError("refused"), RuntimeError("bug")],
    ids=lambda e: f"{type(e).__name__}",
)
async def test_retry_temporary_never_retries_other_kinds(error):
    call = flaky(error)
    with pytest.raises(type(error)):
        await retry_temporary(call, [0, 0, 0], what="test")
    assert call.await_count == 1


def test_default_retry_delays(monkeypatch):
    monkeypatch.delenv("IRIS_API_RETRY_DELAYS_S")
    assert Settings().api_retry_delays_s == [15, 60, 120]
    with pytest.raises(ValueError):
        Settings(api_retry_delays_s=[-1])


async def test_gather_units_lets_others_finish_after_an_ordinary_failure():
    finished = []

    async def unit(i: int) -> int:
        await asyncio.sleep(0.01 * i)
        if i == 0:
            raise ValueError("bad output")
        finished.append(i)
        return i

    outcomes = await gather_units(unit(i) for i in range(3))
    assert isinstance(outcomes[0], ValueError) and outcomes[1:] == [1, 2]
    assert finished == [1, 2]


async def test_gather_units_cancels_the_rest_on_a_blocking_error():
    cancelled = []

    async def slow() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def blocked() -> None:
        await asyncio.sleep(0)
        raise status_error(401)

    with pytest.raises(openai.AuthenticationError):
        await asyncio.wait_for(gather_units([slow(), slow(), blocked()]), 5)
    assert cancelled == [True, True]


# --- Sessions in a runner ------------------------------------------------------------------------


def runner_session(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, openai_api_key="test", synthesis=False, **overrides)
    store = SessionStore(settings.db_path)
    bus = EventBus()
    runner = JobRunner(store, bus, settings)
    session = store.create("Review", SourceKind.UPLOAD)
    return runner, session.id, runner.context(session.id)


def error_events(runner: JobRunner, sid: str) -> list[dict]:
    return [e.data for e in runner.bus.history(sid) if e.type == EventType.ERROR]


# --- Render: API errors give the source crop -----------------------------------------------------


def figure_visual(ctx, vid: str) -> Visual:
    source = f"visuals/{vid}.source.jpg"
    cv2.imwrite(str(ctx.paths.root / source), np.full((20, 40, 3), 200, np.uint8))
    return Visual(
        id=vid,
        moment_id="m1",
        frame=FrameRef(t=0, path="frames/0.jpg"),
        caption=f"Figure {vid}",
        path=source,
        source_path=source,
        description="Two bodies orbiting",
        render_as="svg",
    )


def with_figures(ctx, monkeypatch, *ids: str) -> list[Visual]:
    """Figures to render, and a transcript and notes writer for write, which runs beside it."""
    visuals = [figure_visual(ctx, vid) for vid in ids]
    write_model(ctx.paths.selection, Selection(visuals=visuals))
    segment = TranscriptSegment(start=0, end=10, text="Two bodies orbit each other.")
    write_model(ctx.paths.transcript, Transcript(segments=[segment]))
    monkeypatch.setattr(write, "generate_section", AsyncMock(return_value=section(ids)))
    return visuals


class Drawer:
    """The svg renderer: draws ``SVG`` unless ``errors[vid]`` says otherwise."""

    def __init__(self, monkeypatch):
        self.errors: dict[str, BaseException] = {}
        self.calls: list[str] = []
        self.hold: dict[str, asyncio.Event] = {}
        self.cancelled: list[str] = []
        monkeypatch.setitem(render.DRAWERS, "svg", (self.draw, "svg"))
        monkeypatch.setitem(render.DRAWERS, "image", (self.draw, "png"))
        monkeypatch.setattr(render, "check", self.check)

    async def draw(self, _ctx, visual, _source, _feedback):
        self.calls.append(visual.id)
        try:
            if visual.id in self.hold:
                await self.hold[visual.id].wait()
        except asyncio.CancelledError:
            self.cancelled.append(visual.id)
            raise
        if visual.id in self.errors:
            raise self.errors[visual.id]
        return SVG.encode(), b"preview"

    async def check(self, *_args):
        return render.Check(matches=True, problems=[])


@pytest.mark.parametrize(
    "error",
    [connection_error("SECRET down"), status_error(400), status_error(418)],
    ids=["temporary", "rejected", "internal"],
)
async def test_an_api_error_gives_the_source_crop_unverified_and_uncached(
    tmp_path, monkeypatch, error
):
    runner, sid, ctx = runner_session(tmp_path)
    with_figures(ctx, monkeypatch, "v1", "v2")
    drawer = Drawer(monkeypatch)
    drawer.errors["v1"] = error

    await runner.run(sid, Stage.RENDER)

    assert runner.store.get(sid).status == SessionStatus.DONE
    assert drawer.calls.count("v1") == 1  # no retries of our own
    renders = {r.id: r for r in read_model(ctx.paths.renders, Renders).visuals}
    v1 = renders["v1"]
    assert (v1.verified, v1.render_as, v1.path) == (False, "screenshot", "visuals/v1.jpg")
    assert v1.problems and v1.problems[0].startswith("Redraw failed: ")
    source = (ctx.paths.root / "visuals/v1.source.jpg").read_bytes()
    assert (ctx.paths.root / v1.path).read_bytes() == source
    assert renders["v2"].verified and renders["v2"].render_as == "svg"
    cached = [json.loads(p.read_text())["id"] for p in ctx.paths.render_cache_dir.glob("*.json")]
    assert cached == ["v2"]  # the fallback is not cached
    notes = read_model(ctx.paths.notes, Notes)
    assert {v.id: v.verified for v in notes.visuals} == {"v1": False, "v2": True}
    assert "::figure{#v1}" in notes.markdown
    assert {v.id: v.path for v in notes.visuals}["v1"] == "visuals/v1.jpg"
    rendered = [e.data for e in runner.bus.history(sid) if e.type == EventType.VISUAL_RENDERED]
    assert {d["id"]: d["verified"] for d in rendered} == {"v1": False, "v2": True}
    assert all("problems" not in d for d in rendered)

    # A later run tries the redraw again, and it is used once it passes.
    del drawer.errors["v1"]
    await runner.run(sid, Stage.RENDER)
    assert drawer.calls.count("v1") == 2 and drawer.calls.count("v2") == 1  # v2 from the cache
    renders = {r.id: r for r in read_model(ctx.paths.renders, Renders).visuals}
    assert renders["v1"].verified and renders["v1"].path == "visuals/v1.svg"
    assert not (ctx.paths.root / "visuals/v1.jpg").exists()  # the stale fallback is removed
    runner.store.close()


async def test_a_quality_failure_still_tries_the_other_renderer(tmp_path, monkeypatch):
    """Bad output (a refusal) is not an API error: the quality retries are unchanged."""
    runner, sid, ctx = runner_session(tmp_path, render_attempts=1)
    with_figures(ctx, monkeypatch, "v1")
    drawer = Drawer(monkeypatch)
    drawer.errors["v1"] = ValueError("diagram model: refused")
    await runner.run(sid, Stage.RENDER)
    assert drawer.calls == ["v1", "v1"]  # svg, then image
    [v1] = read_model(ctx.paths.renders, Renders).visuals
    assert (v1.verified, v1.attempts) == (False, 2)
    assert list(ctx.paths.render_cache_dir.glob("*.json"))  # a quality fallback is cached
    runner.store.close()


async def test_a_blocking_error_fails_the_run_and_cancels_other_figures(tmp_path, monkeypatch):
    runner, sid, ctx = runner_session(tmp_path)
    with_figures(ctx, monkeypatch, "v1", "v2", "v3")
    drawer = Drawer(monkeypatch)
    drawer.hold = {"v1": asyncio.Event(), "v3": asyncio.Event()}  # never released
    drawer.errors["v2"] = status_error(401, message="SECRET-sk-abc Incorrect API key")

    await asyncio.wait_for(runner.run(sid, Stage.RENDER), 5)

    session = runner.store.get(sid)
    assert session.status == SessionStatus.FAILED and session.current_stage == Stage.RENDER
    assert session.failure_message == GENERIC_FAILURE
    assert session.failure_kind == FailureKind.GENERAL
    assert sorted(drawer.cancelled) == ["v1", "v3"]
    assert "AuthenticationError" in runner.store.failure_detail(sid).error
    assert error_events(runner, sid) == [
        {"message": GENERIC_FAILURE, "kind": "general", "stage": "render"}
    ]
    runner.store.close()


async def test_a_blocking_error_cancels_the_stage_running_beside_it(tmp_path, monkeypatch):
    """Write and render run together; render's blocking error stops write at once."""
    runner, sid, ctx = runner_session(tmp_path)
    cancelled = []

    async def slow_write(_ctx):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append("write")
            raise

    async def failing_render(_ctx):
        await asyncio.sleep(0)
        raise status_error(429, code="insufficient_quota")

    monkeypatch.setitem(STAGES, Stage.WRITE, slow_write)
    monkeypatch.setitem(STAGES, Stage.RENDER, failing_render)
    await asyncio.wait_for(runner.run(sid, Stage.WRITE), 5)
    session = runner.store.get(sid)
    assert session.status == SessionStatus.FAILED and session.current_stage == Stage.RENDER
    assert cancelled == ["write"]
    runner.store.close()


# --- Transcribe: blocking errors don't wait for the other chunks ---------------------------------


async def test_a_blocking_error_in_one_chunk_cancels_the_others(tmp_path, monkeypatch):
    from iris.pipeline.stages import transcribe
    from tests.test_transcribe import make_audio, write_wav

    runner, sid, ctx = runner_session(tmp_path)
    write_wav(ctx.paths.audio, make_audio([("tone", 60.0)]))  # speech in every chunk
    started, cancelled = [], []

    async def fake_chunk(_client, _model, _wav, _duration, prompt=None, language=None):
        n = len(started)
        started.append(n)
        if n == 0:
            await asyncio.sleep(0)
            raise status_error(403, message="SECRET-sk-abc")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(n)
            raise

    monkeypatch.setattr(transcribe, "transcribe_chunk", fake_chunk)
    monkeypatch.setattr(transcribe, "get_openai", lambda _s: object())
    with pytest.raises(openai.PermissionDeniedError):
        await asyncio.wait_for(transcribe.run(ctx, target_s=10, max_s=12, search_s=1), 5)
    assert len(started) > 1 and sorted(cancelled) == started[1:]
    runner.store.close()


# --- Select and write: temporary errors are retried per unit -------------------------------------


def with_moments(ctx, n: int = 2) -> None:
    items = []
    for i in range(n):
        path = f"frames/{i}.jpg"
        cv2.imwrite(str(ctx.paths.root / path), np.zeros((60, 100, 3), np.uint8))
        moment = Moment(id=f"m{i}", start=i * 10, end=i * 10 + 10, frame=FrameRef(t=i, path=path))
        segment = TranscriptSegment(start=i * 10, end=i * 10 + 1, text="Revenue")
        items.append(AlignedMoment(moment=moment, transcript=[segment]))
    write_model(ctx.paths.alignment, Alignment(items=items))


def decisions(*ids: str):
    parsed = select.Decisions(
        decisions=[select.Decision(moment_id=i, keep=False, elements=[]) for i in ids]
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))]
    )


def fake_vision(monkeypatch, *responses) -> AsyncMock:
    parse = AsyncMock(side_effect=list(responses))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(select, "get_openai", lambda _: client)
    return parse


async def test_a_temporary_error_in_a_select_batch_succeeds_after_a_retry(tmp_path, monkeypatch):
    runner, sid, ctx = runner_session(tmp_path, selection_batch_size=1)
    with_moments(ctx)
    parse = fake_vision(
        monkeypatch, decisions("m0"), status_error(503), connection_error(), decisions("m1")
    )
    await select.run(ctx)
    assert parse.await_count == 4
    assert read_model(ctx.paths.selection, Selection).rejected_moment_ids == ["m0", "m1"]
    runner.store.close()


async def test_a_persistent_temporary_error_fails_select_with_the_generic_message(
    tmp_path, monkeypatch
):
    runner, sid, ctx = runner_session(tmp_path, selection_batch_size=1)
    with_moments(ctx)
    parse = fake_vision(monkeypatch, decisions("m0"), *[status_error(500)] * 4)
    await runner.run(sid, Stage.SELECT)
    assert parse.await_count == 1 + 4  # the first batch, then the second tried four times
    session = runner.store.get(sid)
    assert session.status == SessionStatus.FAILED and session.current_stage == Stage.SELECT
    assert (session.failure_message, session.failure_kind) == (GENERIC_FAILURE, "general")
    assert len(list(ctx.paths.selection_batches_dir.glob("*.json"))) == 1  # kept
    runner.store.close()


def with_transcript(ctx) -> None:
    segment = TranscriptSegment(start=0, end=10, text="We agreed to ship the prototype.")
    write_model(ctx.paths.transcript, Transcript(segments=[segment]))
    write_model(ctx.paths.selection, Selection(visuals=[]))


async def test_a_temporary_error_in_a_write_section_succeeds_after_a_retry(tmp_path, monkeypatch):
    runner, sid, ctx = runner_session(tmp_path)
    with_transcript(ctx)
    generate = AsyncMock(side_effect=[status_error(429), timeout_error(), section()])
    monkeypatch.setattr(write, "generate_section", generate)
    await runner.run(sid, Stage.WRITE)
    assert runner.store.get(sid).status == SessionStatus.DONE
    assert generate.await_count == 3
    runner.store.close()


async def test_a_persistent_temporary_error_fails_write_with_the_generic_message(
    tmp_path, monkeypatch
):
    runner, sid, ctx = runner_session(tmp_path)
    with_transcript(ctx)
    generate = AsyncMock(side_effect=connection_error("SECRET-sk-abc openai"))
    monkeypatch.setattr(write, "generate_section", generate)
    await runner.run(sid, Stage.WRITE)
    assert generate.await_count == 4
    session = runner.store.get(sid)
    assert session.status == SessionStatus.FAILED and session.current_stage == Stage.WRITE
    assert session.failure_message == GENERIC_FAILURE
    assert "SECRET-sk-abc" in runner.store.failure_detail(sid).error
    runner.store.close()


async def test_a_blocking_error_in_a_write_section_is_not_retried(tmp_path, monkeypatch):
    runner, sid, ctx = runner_session(tmp_path)
    with_transcript(ctx)
    generate = AsyncMock(side_effect=status_error(404, code="model_not_found"))
    monkeypatch.setattr(write, "generate_section", generate)
    await runner.run(sid, Stage.WRITE)
    assert generate.await_count == 1
    assert runner.store.get(sid).failure_message == GENERIC_FAILURE
    runner.store.close()


# --- Input errors show their own message ---------------------------------------------------------


async def test_an_input_error_shows_its_own_message(tmp_path):
    runner, sid, ctx = runner_session(tmp_path)
    make_media(ctx.paths.source(".mp4"), audio=False, seconds=1)
    await runner.run(sid)
    session = runner.store.get(sid)
    assert session.current_stage == Stage.INGEST
    assert (session.failure_message, session.failure_kind) == (UPLOAD_WITHOUT_AUDIO, "input")
    assert error_events(runner, sid) == [
        {"message": UPLOAD_WITHOUT_AUDIO, "kind": "input", "stage": "ingest"}
    ]
    runner.store.close()


async def test_a_silent_recording_has_nothing_to_write(tmp_path, monkeypatch):
    runner, sid, ctx = runner_session(tmp_path)
    write_model(ctx.paths.transcript, Transcript(segments=[]))
    write_model(ctx.paths.selection, Selection(visuals=[]))
    await runner.run(sid, Stage.WRITE)
    session = runner.store.get(sid)
    assert (session.failure_message, session.failure_kind) == (NOTHING_TO_WRITE, "input")
    runner.store.close()


async def test_a_new_run_clears_the_previous_failure(tmp_path, monkeypatch):
    runner, sid, ctx = runner_session(tmp_path)
    with_transcript(ctx)
    monkeypatch.setattr(write, "generate_section", AsyncMock(side_effect=status_error(401)))
    await runner.run(sid, Stage.WRITE)
    assert runner.store.failure_detail(sid) is not None
    monkeypatch.setattr(write, "generate_section", AsyncMock(return_value=section()))
    await runner.run(sid, Stage.WRITE)
    session = runner.store.get(sid)
    assert session.status == SessionStatus.DONE
    assert (session.failure_message, session.failure_kind) == (None, None)
    assert runner.store.failure_detail(sid) is None
    runner.store.close()


# --- No leaks: raw error text never reaches the API ----------------------------------------------

SENTINELS = ("SECRET-sk-proj-42", "openai", "APIConnectionError", "api.openai.com")


class RaisingTranscriptions:
    """The SDK's ``audio.transcriptions``, failing as a connection error would."""

    def __init__(self):
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        raise connection_error("SECRET-sk-proj-42: openai at api.openai.com is unreachable")


def assert_no_leak(text: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in text, sentinel


def test_raw_errors_never_reach_the_api(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path / "data", openai_api_key="test", synthesis=False)
    transcriptions = RaisingTranscriptions()
    fake = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    # Through the real metered client, so the failed calls are recorded in usage.json too.
    monkeypatch.setattr(llm_client, "_client", lambda _key: fake)
    media = make_media(tmp_path / "talk.mp4", seconds=2)
    with TestClient(create_app(settings)) as client:
        sid = client.post("/api/sessions", files={"file": ("talk.mp4", media.read_bytes())}).json()[
            "id"
        ]
        session = wait_for_status(client, sid, {"done", "failed"}, timeout=20)
        assert session["status"] == "failed"
        assert transcriptions.calls == 1 + len(settings.api_retry_delays_s)
        assert session["failure_message"] == GENERIC_FAILURE
        assert session["failure_kind"] == "general"
        assert session["current_stage"] == "transcribe"

        responses = [
            client.get(f"/api/sessions/{sid}"),
            client.get("/api/sessions"),
            client.get(f"/api/sessions/{sid}/usage"),
            client.get(f"/api/sessions/{sid}/transcript"),
            client.get(f"/api/sessions/{sid}/notes"),
            client.get(f"/api/sessions/{sid}/export"),
            client.get("/api/health"),
            client.post(f"/api/sessions/{sid}/run?from_stage=nope"),
        ]
        for r in responses:
            assert_no_leak(r.text)
        usage = client.get(f"/api/sessions/{sid}/usage").json()
        assert usage["records"] and all(r["failed"] for r in usage["records"])
        assert all("error" not in r for r in usage["records"])

        # The event stream sends exactly these, formatted, on (re)connect.
        bus = client.app.state.bus
        stream = "".join(events_route._format(e) for e in bus.history(sid))
        assert '"type":"error"' in stream
        assert_no_leak(stream)

        # The raw error is kept for developers: `iris show`.
        store = client.app.state.store
        out = cli.format_show(store.get(sid), store.failure_detail(sid))
        assert "SECRET-sk-proj-42" in out and "APIConnectionError" in out
        assert "transcribe" in out and GENERIC_FAILURE in out

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli._show(sid) == 0
    printed = capsys.readouterr().out
    assert "SECRET-sk-proj-42" in printed and "failed at" in printed
    assert cli._show("missing") == 1


def test_old_sessions_with_a_raw_error_are_not_leaked(tmp_path, capsys, monkeypatch):
    """A database from before this change: its ``error`` column holds the raw message."""
    settings = Settings(data_dir=tmp_path / "data", openai_api_key="test")
    settings.data_dir.mkdir(parents=True)
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
            status TEXT NOT NULL, current_stage TEXT, error TEXT, duration_s REAL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO sessions VALUES ('old1', 'Old', 'upload', 'failed', 'render',
            'render failed: APIConnectionError: SECRET-sk-proj-42 openai', NULL,
            '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        INSERT INTO sessions VALUES ('old2', 'Fine', 'upload', 'done', 'synthesize', NULL, NULL,
            '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00');
        """
    )
    conn.close()
    with TestClient(create_app(settings)) as client:
        old = client.get("/api/sessions/old1").json()
        assert old["failure_message"] == GENERIC_FAILURE and old["failure_kind"] == "general"
        assert old["current_stage"] == "render"
        assert_no_leak(client.get("/api/sessions/old1").text)
        assert_no_leak(client.get("/api/sessions").text)
        fine = client.get("/api/sessions/old2").json()
        assert fine["failure_message"] is None and fine["failure_kind"] is None
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli._show("old1") == 0
    printed = capsys.readouterr().out
    assert "SECRET-sk-proj-42" in printed and "unknown" in printed  # failure time not recorded
    # Opening again changes nothing (the migration is idempotent).
    store = SessionStore(settings.db_path)
    assert store.failure_detail("old1").error.startswith("render failed: APIConnectionError")
    store.close()


def test_a_stored_message_not_on_the_list_is_served_as_the_generic_one(tmp_path):
    """Say a later version rewords a message: older rows never break the library or leak."""
    settings = Settings(data_dir=tmp_path / "data", openai_api_key="test")
    store = SessionStore(settings.db_path)
    bad = store.create("Reworded", SourceKind.UPLOAD).id
    odd_kind = store.create("Odd kind", SourceKind.UPLOAD).id
    with store._conn:
        store._conn.execute(
            "UPDATE sessions SET status = 'failed', failure_message = ?, failure_kind = 'input'"
            " WHERE id = ?",
            ("SECRET-sk-proj-42 old wording", bad),
        )
        store._conn.execute(
            "UPDATE sessions SET status = 'failed', failure_message = ?, failure_kind = 'weird'"
            " WHERE id = ?",
            (UPLOAD_WITHOUT_AUDIO, odd_kind),
        )
    store.close()
    with TestClient(create_app(settings)) as client:
        listed = client.get("/api/sessions")
        assert listed.status_code == 200
        assert_no_leak(listed.text)
        one = client.get(f"/api/sessions/{bad}").json()
        assert (one["failure_message"], one["failure_kind"]) == (GENERIC_FAILURE, "general")
        other = client.get(f"/api/sessions/{odd_kind}").json()
        assert (other["failure_message"], other["failure_kind"]) == (
            UPLOAD_WITHOUT_AUDIO,
            "general",
        )


# --- Runs cut off by Iris stopping ---------------------------------------------------------------


def wait_for_stage(client: TestClient, sid: str, stage: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while client.get(f"/api/sessions/{sid}").json()["current_stage"] != stage:
        assert time.monotonic() < deadline, f"session {sid} never reached {stage}"
        time.sleep(0.05)


def test_a_run_cut_off_by_a_restart_can_be_tried_again_from_its_stage(tmp_path, monkeypatch):
    """Restart Engine or Quit mid-run: shutdown cancels the run, which records nothing (the
    cancellation is not an ``Exception``). The next start marks it failed at its stage."""
    settings = Settings(data_dir=tmp_path / "data", openai_api_key="test", synthesis=False)
    ran: list[Stage] = []

    def fake(stage: Stage, *, hang: bool = False):
        async def run(_ctx) -> None:
            ran.append(stage)
            if hang:
                await asyncio.sleep(3600)  # stands in for a stage waiting on the API

        return run

    for stage in STAGES:
        monkeypatch.setitem(STAGES, stage, fake(stage, hang=stage == Stage.ALIGN))
    with TestClient(create_app(settings)) as client:
        sid = client.app.state.store.create("Lecture", SourceKind.UPLOAD).id
        assert client.post(f"/api/sessions/{sid}/run?from_stage=transcribe").status_code == 200
        wait_for_stage(client, sid, "align")
    store = SessionStore(settings.db_path)
    assert store.get(sid).status == SessionStatus.PROCESSING  # what shutdown leaves behind
    store.close()

    for stage in STAGES:
        monkeypatch.setitem(STAGES, stage, fake(stage))
    ran.clear()
    with TestClient(create_app(settings)) as client:
        session = client.get(f"/api/sessions/{sid}").json()
        assert session["status"] == "failed"
        assert (session["failure_message"], session["failure_kind"]) == (INTERRUPTED_RUN, "general")
        assert session["current_stage"] == "align"
        assert "interrupted" in client.app.state.store.failure_detail(sid).error

        # Try again from the stage it stopped at; the earlier stages' outputs are reused.
        assert client.post(f"/api/sessions/{sid}/run?from_stage=align").status_code == 200
        session = wait_for_status(client, sid, {"done", "failed"})
        assert session["status"] == "done"
        assert (session["failure_message"], session["failure_kind"]) == (None, None)
        assert Stage.TRANSCRIBE not in ran and ran[0] == Stage.ALIGN


def test_startup_fails_only_the_runs_left_processing(tmp_path):
    """A crash, a forced quit or power loss: no shutdown ran, so only the next start finds out."""
    settings = Settings(data_dir=tmp_path / "data", openai_api_key="test")
    store = SessionStore(settings.db_path)

    def session(status: SessionStatus, stage: Stage | None = None) -> str:
        sid = store.create(status.value, SourceKind.UPLOAD).id
        store.update(sid, status=status, current_stage=stage)
        return sid

    cut_off = session(SessionStatus.PROCESSING, Stage.WRITE)
    cut_off_live = store.create("Live", SourceKind.BROWSER_LIVE).id
    store.update(cut_off_live, status=SessionStatus.PROCESSING)  # before its first stage
    untouched = {
        session(SessionStatus.CREATED): None,
        session(SessionStatus.RECORDING): None,
        session(SessionStatus.DONE, Stage.SYNTHESIZE): None,
        session(SessionStatus.FAILED, Stage.RENDER): None,
    }
    failed = list(untouched)[-1]
    store.fail(failed, message=GENERIC_FAILURE, kind=FailureKind.GENERAL, detail="x")
    before = {sid: store.get(sid) for sid in untouched}
    store.close()

    with TestClient(create_app(settings)) as client:
        for sid, stage in ((cut_off, "write"), (cut_off_live, None)):
            s = client.get(f"/api/sessions/{sid}").json()
            assert (s["status"], s["current_stage"]) == ("failed", stage)
            assert (s["failure_message"], s["failure_kind"]) == (INTERRUPTED_RUN, "general")
        for sid, old in before.items():
            assert client.app.state.store.get(sid) == old


async def test_a_run_that_finishes_after_being_marked_interrupted_is_clean(tmp_path, monkeypatch):
    """A CLI run on the same data while the server starts: marked, but it carries on and ends
    done with no failure left on it."""
    runner, sid, _ctx = runner_session(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def wait(_ctx) -> None:
        started.set()
        await release.wait()

    async def nothing(_ctx) -> None:
        pass

    for stage in STAGES:
        monkeypatch.setitem(STAGES, stage, wait if stage == Stage.INGEST else nothing)
    runner.start(sid)
    await started.wait()
    runner.fail_interrupted()  # as the server starting would
    assert runner.store.get(sid).failure_message == INTERRUPTED_RUN
    release.set()
    await runner._tasks[sid]
    session = runner.store.get(sid)
    assert session.status == SessionStatus.DONE
    assert (session.failure_message, session.failure_kind) == (None, None)
    assert runner.store.failure_detail(sid) is None
    runner.store.close()
