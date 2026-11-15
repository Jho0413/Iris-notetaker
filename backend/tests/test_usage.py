import asyncio
import base64
import json
import logging
import sqlite3
import sys
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai.types.audio import Transcription, TranscriptionVerbose
from openai.types.chat import ChatCompletion
from openai.types.images_response import ImagesResponse

from iris import cli
from iris.config import PRICES_CHECKED, ModelPrice, Settings, get_settings
from iris.llm import client as llm_client
from iris.llm import usage
from iris.llm.client import get_openai
from iris.main import create_app
from iris.models import (
    Renders,
    Selection,
    SessionStatus,
    SessionUsage,
    SourceKind,
    Stage,
    UsageRecord,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import render, select
from iris.store.db import SessionStore
from iris.store.layout import SessionPaths, read_model, session_paths, write_model
from tests.builders import figure_to_render, section, stage_ctx
from tests.media import make_media
from tests.waiting import wait_until_idle

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><rect width="9"/></svg>'
IMAGE = b"\x89PNG generated"

CHAT_USAGE = {"prompt": 1000, "cached": 200, "completion": 500, "reasoning": 300}
CHAT_COST = (800 * 5.00 + 200 * 0.50 + 500 * 30.00) / 1e6
IMAGE_COST = (100 * 5.00 + 1000 * 30.00) / 1e6
WHISPER_SECONDS = 1.2
WHISPER_COST = 2 * 0.0001


def chat_response(parsed, model="gpt-5.5"):
    u = CHAT_USAGE
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
        usage=SimpleNamespace(
            prompt_tokens=u["prompt"],
            completion_tokens=u["completion"],
            prompt_tokens_details=SimpleNamespace(cached_tokens=u["cached"]),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=u["reasoning"]),
        ),
    )


def image_response():
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(IMAGE).decode())],
        usage=SimpleNamespace(
            input_tokens=100,
            input_tokens_details=SimpleNamespace(text_tokens=100, image_tokens=0),
            output_tokens=1000,
            output_tokens_details=SimpleNamespace(image_tokens=1000, text_tokens=0),
        ),
    )


class FakeOpenAI:
    def __init__(self):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))
        self.images = SimpleNamespace(generate=self.generate)
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.transcribe))

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        schema = kwargs.get("response_format")
        if schema is None:
            result = None
        elif schema is select.Decisions:
            content = kwargs["messages"][1]["content"]
            ids = [json.loads(p["text"])["moment_id"] for p in content[1:] if p["type"] == "text"]
            figure = select.ElementDecision(
                kind="figure",
                content="A coloured slide",
                build=0,
                region=None,
                caption="Colored slide",
                render_as="svg",
            )
            result = select.Decisions(
                decisions=[select.Decision(moment_id=i, keep=True, elements=[figure]) for i in ids]
            )
        elif schema is render.SvgDrawing:
            result = render.SvgDrawing(svg=SVG)
        elif schema is render.Check:
            candidate = kwargs["messages"][1]["content"][2]["image_url"]["url"]
            passed = candidate.endswith(base64.b64encode(IMAGE).decode())
            result = render.Check(matches=passed, problems=[] if passed else ["wrong shape"])
        else:
            payload = json.loads(kwargs["messages"][1]["content"])
            ids = [r["id"] for r in payload["records"] if r["kind"] == "visual"]
            result = section(ids)
        return chat_response(result, model=kwargs["model"])

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return image_response()

    async def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "language": "english",
            "segments": [{"start": 0, "end": 2, "text": "Ship the prototype. Alex will test it."}],
            "usage": {"type": "duration", "seconds": WHISPER_SECONDS},
        }


@pytest.fixture
def fake(monkeypatch) -> FakeOpenAI:
    fake = FakeOpenAI()
    monkeypatch.setattr(llm_client, "_client", lambda _key: fake)
    return fake


@pytest.fixture
def ctx(tmp_path) -> StageContext:
    return stage_ctx(tmp_path, openai_api_key="test", render_attempts=1)


def test_pipeline_records_every_call_and_cached_rerun_costs_nothing(tmp_path, fake):

    settings = Settings(data_dir=tmp_path / "data", openai_api_key="test", render_attempts=1)
    with TestClient(create_app(settings)) as client:
        recording = make_media(tmp_path / "talk.mp4", seconds=2)
        sid = client.post(
            "/api/sessions", files={"file": ("talk.mp4", recording.read_bytes())}
        ).json()["id"]
        session = wait_until_idle(client, sid)
        assert session["status"] == "done", session.get("error")

        body = client.get(f"/api/sessions/{sid}/usage").json()
        result = SessionUsage.model_validate(body)
        assert len(result.records) == len(fake.calls) > 0
        summary = settings.sessions_dir / sid / "usage.json"
        assert read_model(summary, SessionUsage) == result

        expected = {
            ("transcription", Stage.TRANSCRIBE, "whisper-1", WHISPER_COST),
            ("screen_reading", Stage.SELECT, "gpt-5.5", CHAT_COST),
            ("notes", Stage.WRITE, "gpt-5.5", CHAT_COST),
            ("svg", Stage.RENDER, "gpt-5.5", CHAT_COST),
            ("figure_check", Stage.RENDER, "gpt-5.5", CHAT_COST),
            ("image", Stage.RENDER, "gpt-image-2", IMAGE_COST),
        }
        seen = {(r.purpose, r.stage, r.model, r.cost_usd) for r in result.records}
        assert {(p, s, m) for p, s, m, _ in seen} == {(p, s, m) for p, s, m, _ in expected}
        for p, s, m, cost in seen:
            assert cost == pytest.approx(next(c for p2, *_, c in expected if p2 == p)), (p, s, m)

        transcription = next(r for r in result.records if r.purpose == "transcription")
        assert transcription.audio_seconds == 2
        check = next(r for r in result.records if r.purpose == "figure_check")
        assert (check.input_tokens, check.cached_input_tokens) == (1000, 200)
        assert (check.output_tokens, check.reasoning_tokens) == (500, 300)
        image = next(r for r in result.records if r.purpose == "image")
        assert (image.images, image.output_image_tokens) == (1, 1000)
        assert all(r.latency_s >= 0 and r.unpriced is None for r in result.records)

        total = sum(r.cost_usd for r in result.records)
        assert result.total.cost_usd == pytest.approx(total)
        assert list(result.by_stage) == ["transcribe", "select", "write", "render"]
        for key, group in (("stage", result.by_stage), ("model", result.by_model)):
            assert sum(t.calls for t in group.values()) == len(result.records), key
            assert sum(t.cost_usd for t in group.values()) == pytest.approx(total), key
        image_calls = [r for r in result.records if r.model == "gpt-image-2"]
        assert result.by_model["gpt-image-2"].cost_usd == pytest.approx(
            IMAGE_COST * len(image_calls)
        )
        assert result.total.input_tokens == sum(r.input_tokens for r in result.records)

        calls_before = len(fake.calls)
        assert client.post(f"/api/sessions/{sid}/run").status_code == 200
        assert wait_until_idle(client, sid)["status"] == "done"
        assert len(fake.calls) == calls_before
        again = SessionUsage.model_validate(client.get(f"/api/sessions/{sid}/usage").json())
        assert again.records == result.records
        assert read_model(summary, SessionUsage) == result
        assert again.total.cost_usd == pytest.approx(total)
        listed = next(s for s in client.get("/api/sessions").json() if s["id"] == sid)
        assert listed["cost_usd"] == pytest.approx(total)
        assert listed["cost_partial"] is False


def call(cost: float | None = 0.25, stage: Stage = Stage.RENDER) -> UsageRecord:
    return UsageRecord(
        at=datetime.now(UTC),
        stage=stage,
        purpose="x",
        operation="op",
        model="gpt-5.5" if cost is not None else "mystery-1",
        latency_s=1,
        cost_usd=cost,
        unpriced=None if cost is not None else "no price for model 'mystery-1'",
    )


@pytest.fixture
def api(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", openai_api_key="test", render_attempts=1)
    with TestClient(create_app(settings)) as client:
        yield client, settings


def new_session(client: TestClient, settings: Settings) -> tuple[str, SessionPaths]:
    sid = client.app.state.store.create("Talk", SourceKind.UPLOAD).id
    return sid, session_paths(settings.sessions_dir, sid).ensure()


def listed(client: TestClient, sid: str) -> dict:
    return next(s for s in client.get("/api/sessions").json() if s["id"] == sid)


def test_session_list_cost_counts_priced_calls_and_flags_unpriced(api):
    client, settings = api
    sid, paths = new_session(client, settings)
    for record in (call(0.25), call(None), call(0.5, Stage.WRITE)):
        usage.append_record(paths.usage_log, record)
    row = listed(client, sid)
    assert row["cost_usd"] == pytest.approx(0.75)
    assert row["cost_partial"] is True
    one = client.get(f"/api/sessions/{sid}").json()
    assert (one["cost_usd"], one["cost_partial"]) == (row["cost_usd"], True)


def test_session_list_cost_is_none_without_model_calls(api):
    client, settings = api
    sid, paths = new_session(client, settings)
    assert (listed(client, sid)["cost_usd"], listed(client, sid)["cost_partial"]) == (None, False)
    paths.usage_log.touch()
    assert listed(client, sid)["cost_usd"] is None
    usage.append_record(paths.usage_log, call(0.0))
    assert listed(client, sid)["cost_usd"] == 0


def test_session_list_cost_adds_a_reruns_calls_reading_only_new_lines(api, monkeypatch):
    client, settings = api
    sid, paths = new_session(client, settings)
    for _ in range(3):
        usage.append_record(paths.usage_log, call(0.25))
    assert listed(client, sid)["cost_usd"] == pytest.approx(0.75)

    parsed = []
    validate = UsageRecord.model_validate_json
    monkeypatch.setattr(
        UsageRecord, "model_validate_json", lambda data: parsed.append(data) or validate(data)
    )
    usage.append_record(paths.usage_log, call(0.5))
    assert listed(client, sid)["cost_usd"] == pytest.approx(1.25)
    assert len(parsed) == 1
    assert listed(client, sid)["cost_usd"] == pytest.approx(1.25)
    assert len(parsed) == 1


async def test_session_list_cost_is_current_while_processing(api, fake):
    client, settings = api
    sid, paths = new_session(client, settings)
    client.app.state.store.update(sid, status=SessionStatus.PROCESSING, current_stage=Stage.SELECT)
    ctx = StageContext(session_id=sid, source=SourceKind.UPLOAD, paths=paths, settings=settings)
    with usage.recording(ctx, Stage.SELECT):
        await get_openai(settings).chat.completions.parse(model="gpt-5.5", messages=[])
        assert not paths.usage.exists()
        row = listed(client, sid)
        assert row["status"] == "processing"
        assert row["cost_usd"] == pytest.approx(CHAT_COST)

        line = call(0.5).model_dump_json()
        with paths.usage_log.open("a") as f:
            f.write(line[:20])
        assert listed(client, sid)["cost_usd"] == pytest.approx(CHAT_COST)
        with paths.usage_log.open("a") as f:
            f.write(line[20:] + "\n")
        assert listed(client, sid)["cost_usd"] == pytest.approx(CHAT_COST + 0.5)


def test_session_list_cost_skips_damage_and_recounts_a_replaced_log(api, caplog):
    client, settings = api
    sid, paths = new_session(client, settings)
    usage.append_record(paths.usage_log, call(0.25))
    with paths.usage_log.open("a") as f:
        f.write('{"at": "2026-11-03T00:00:00Z", "purp')
    assert listed(client, sid)["cost_usd"] == pytest.approx(0.25)
    usage.append_record(paths.usage_log, call(0.5))
    with caplog.at_level(logging.WARNING, "iris.llm.usage"):
        assert listed(client, sid)["cost_usd"] == pytest.approx(0.75)
    assert "skipping unreadable line" in caplog.text
    assert listed(client, sid)["cost_usd"] == pytest.approx(0.75)

    paths.usage_log.unlink()
    usage.append_record(paths.usage_log, call(0.1))
    assert listed(client, sid)["cost_usd"] == pytest.approx(0.1)


def test_deleting_a_session_forgets_its_running_total(api):
    client, settings = api
    sid, paths = new_session(client, settings)
    for _ in range(3):
        usage.append_record(paths.usage_log, call(0.25))
    assert listed(client, sid)["cost_usd"] == pytest.approx(0.75)
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    paths.ensure()
    for _ in range(3):
        usage.append_record(paths.usage_log, call(0.1))
    assert usage.running_total(paths.usage_log).cost_usd == pytest.approx(0.3)


def test_one_unreadable_log_does_not_break_the_session_list(api, caplog):
    client, settings = api
    broken, broken_paths = new_session(client, settings)
    broken_paths.usage_log.mkdir()
    fine, fine_paths = new_session(client, settings)
    usage.append_record(fine_paths.usage_log, call(0.25))
    with caplog.at_level(logging.WARNING, "iris.llm.usage"):
        response = client.get("/api/sessions")
    assert response.status_code == 200
    rows = {s["id"]: s for s in response.json()}
    assert rows[broken]["cost_usd"] is None
    assert rows[fine]["cost_usd"] == pytest.approx(0.25)
    assert "could not read" in caplog.text


def test_a_read_error_after_opening_the_log_does_not_break_the_session_list(api, monkeypatch):
    client, settings = api
    sid, paths = new_session(client, settings)
    usage.append_record(paths.usage_log, call(0.25))

    def failing_read(*_args):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(usage.os, "fstat", failing_read)
    assert listed(client, sid)["cost_usd"] is None
    monkeypatch.undo()
    assert listed(client, sid)["cost_usd"] == pytest.approx(0.25)


def test_list_and_usage_agree_on_records_with_unicode_line_separators(api):
    client, settings = api
    sid, paths = new_session(client, settings)
    for cost, error in ((0.25, "model said: a\u2028b"), (0.5, "c\x85d\u2029e"), (0.125, None)):
        usage.append_record(paths.usage_log, call(cost).model_copy(update={"error": error}))
    rail = client.get(f"/api/sessions/{sid}/usage").json()["total"]
    assert rail["calls"] == 3 and rail["cost_usd"] == pytest.approx(0.875)
    assert listed(client, sid)["cost_usd"] == pytest.approx(rail["cost_usd"])


def test_rerun_returns_the_sessions_cost(api, monkeypatch):
    client, settings = api
    sid, paths = new_session(client, settings)
    usage.append_record(paths.usage_log, call(0.25))
    monkeypatch.setattr(client.app.state.runner, "start", lambda *_args, **_kwargs: None)
    body = client.post(f"/api/sessions/{sid}/run").json()
    assert (body["cost_usd"], body["cost_partial"]) == (0.25, False)


async def test_unpriced_model_is_flagged_not_guessed(ctx, fake):
    with usage.recording(ctx, Stage.SELECT):
        await get_openai(ctx.settings).chat.completions.parse(model="mystery-1", messages=[])
        await get_openai(ctx.settings).chat.completions.parse(model="gpt-5.5", messages=[])
    result = read_model(ctx.paths.usage, SessionUsage)
    unpriced, priced = result.records
    assert unpriced.cost_usd is None
    assert unpriced.unpriced == "no price for model 'mystery-1'"
    assert result.by_model["mystery-1"].unpriced_calls == 1
    assert (result.total.calls, result.total.unpriced_calls) == (2, 1)
    assert result.total.cost_usd == pytest.approx(priced.cost_usd)


async def test_usage_recording_failure_never_fails_the_stage(ctx, fake, monkeypatch, caplog):
    write_model(ctx.paths.selection, Selection(visuals=[figure_to_render(ctx)]))

    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(usage, "append_record", broken)
    with caplog.at_level(logging.WARNING, "iris.llm.usage"), usage.recording(ctx, Stage.RENDER):
        await render.run(ctx)
    [rendered] = read_model(ctx.paths.renders, Renders).visuals
    assert rendered.verified and rendered.render_as == "image"
    assert not ctx.paths.usage.exists() and not ctx.paths.usage_log.exists()
    assert "could not record usage" in caplog.text


async def test_unreadable_usage_in_a_response_is_logged_and_the_result_returned(ctx, monkeypatch):
    class Weird:
        model = "gpt-5.5"

        @property
        def usage(self):
            raise RuntimeError("bad usage")

    weird = Weird()

    async def parse(**_kwargs):
        return weird

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(llm_client, "_client", lambda _key: fake)
    with usage.recording(ctx, Stage.WRITE):
        assert await get_openai(ctx.settings).chat.completions.parse(model="gpt-5.5") is weird
    assert not ctx.paths.usage.exists()


async def test_failed_call_is_recorded_and_still_raises(ctx, monkeypatch):
    async def parse(**_kwargs):
        raise TimeoutError("took too long")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(llm_client, "_client", lambda _key: fake)
    with usage.recording(ctx, Stage.WRITE), pytest.raises(TimeoutError):
        await get_openai(ctx.settings).chat.completions.parse(model="gpt-5.5")
    [record] = read_model(ctx.paths.usage, SessionUsage).records
    assert record.error == "TimeoutError: took too long"
    assert (record.cost_usd, record.input_tokens, record.purpose) == (0, 0, "notes")
    assert read_model(ctx.paths.usage, SessionUsage).total.failed_calls == 1


async def test_calls_outside_a_stage_are_not_recorded(ctx, fake):
    await get_openai(ctx.settings).chat.completions.parse(model="gpt-5.5", messages=[])
    assert len(fake.calls) == 1
    assert not ctx.paths.usage.exists()


async def test_with_options_client_is_metered(ctx, fake):
    fake.with_options = lambda **_kwargs: fake
    with usage.recording(ctx, Stage.WRITE):
        client = get_openai(ctx.settings).with_options(max_retries=5)
        await client.chat.completions.parse(model="gpt-5.5", messages=[])
    assert read_model(ctx.paths.usage, SessionUsage).total.calls == 1


async def test_other_client_attributes_pass_through(ctx, monkeypatch):
    fake = SimpleNamespace(api_key="k", chat=SimpleNamespace(other="x"))
    monkeypatch.setattr(llm_client, "_client", lambda _key: fake)
    proxied = get_openai(ctx.settings)
    assert proxied.api_key == "k"
    assert proxied.chat.other == "x"


async def test_concurrent_stages_and_threads_keep_every_record(ctx, fake, tmp_path):
    other = stage_ctx(tmp_path, root="s2", settings=ctx.settings)

    async def stage(c: StageContext, s: Stage, n: int) -> None:
        with usage.recording(c, s):
            await asyncio.gather(
                *(
                    get_openai(c.settings).chat.completions.parse(model="gpt-5.5", messages=[])
                    for _ in range(n)
                )
            )

    await asyncio.gather(
        stage(ctx, Stage.WRITE, 20), stage(ctx, Stage.RENDER, 15), stage(other, Stage.SELECT, 5)
    )
    record = UsageRecord(
        at=datetime.now(UTC), purpose="x", operation="op", model="gpt-5.5", latency_s=0
    )
    threads = [
        threading.Thread(
            target=lambda: [usage.append_record(ctx.paths.usage_log, record) for _ in range(10)]
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = usage.read_usage(ctx.paths.usage_log)
    assert result.by_stage["write"].calls == 20
    assert result.by_stage["render"].calls == 15
    assert result.by_stage["none"].calls == 40
    assert result.total.calls == 75
    assert read_model(other.paths.usage, SessionUsage).total.calls == 5


def test_damaged_log_lines_are_skipped(ctx, caplog):
    record = UsageRecord(
        at=datetime.now(UTC), purpose="x", operation="op", model="gpt-5.5", latency_s=0
    )
    usage.append_record(ctx.paths.usage_log, record)
    with ctx.paths.usage_log.open("a") as f:
        f.write('{"at": "2026-11-03T00:00:00Z", "purp')
    with caplog.at_level(logging.WARNING, "iris.llm.usage"):
        assert usage.read_usage(ctx.paths.usage_log).records == [record]
    assert "skipping unreadable line 2" in caplog.text
    usage.append_record(ctx.paths.usage_log, record)
    assert usage.read_usage(ctx.paths.usage_log).total.calls == 2


def test_appending_does_not_reread_the_log(ctx, monkeypatch):
    record = UsageRecord(
        at=datetime.now(UTC), purpose="x", operation="op", model="gpt-5.5", latency_s=0
    )
    for _ in range(3):
        usage.append_record(ctx.paths.usage_log, record)

    def no_read(*_args, **_kwargs):
        raise AssertionError("append re-read the log")

    monkeypatch.setattr(type(ctx.paths.usage_log), "read_text", no_read)
    usage.append_record(ctx.paths.usage_log, record)
    monkeypatch.undo()
    assert len(ctx.paths.usage_log.read_text().splitlines()) == 4


async def test_stage_with_only_cache_hits_leaves_usage_untouched(ctx, fake):
    with usage.recording(ctx, Stage.WRITE):
        pass
    assert not ctx.paths.usage.exists() and not ctx.paths.usage_log.exists()


def test_measure_real_sdk_responses():
    completion = ChatCompletion.model_validate(
        {
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-5.5-2026-01-01",
            "choices": [],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 400,
                "total_tokens": 1600,
                "prompt_tokens_details": {"cached_tokens": 1024, "image_tokens": 765},
                "completion_tokens_details": {"reasoning_tokens": 256},
            },
        }
    )
    assert usage.measure("chat.completions.parse", {}, completion) == {
        "input_tokens": 1200,
        "cached_input_tokens": 1024,
        "input_image_tokens": 765,
        "input_audio_tokens": 0,
        "output_tokens": 400,
        "reasoning_tokens": 256,
    }

    images = ImagesResponse.model_validate(
        {
            "created": 0,
            "data": [{"b64_json": "eA=="}],
            "usage": {
                "input_tokens": 50,
                "input_tokens_details": {"text_tokens": 40, "image_tokens": 10},
                "output_tokens": 4160,
                "total_tokens": 4210,
            },
        }
    )
    m = usage.measure("images.generate", {}, images)
    assert (m["input_tokens"], m["input_image_tokens"], m["output_image_tokens"]) == (50, 10, 4160)
    assert m["images"] == 1

    verbose = TranscriptionVerbose.model_validate(
        {
            "duration": 299.5,
            "language": "english",
            "text": "hi",
            "usage": {"type": "duration", "seconds": 300.01},
        }
    )
    assert usage.measure("audio.transcriptions.create", {}, verbose) == {"audio_seconds": 301}

    tokens = Transcription.model_validate(
        {
            "text": "hi",
            "usage": {
                "type": "tokens",
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "input_token_details": {"audio_tokens": 110, "text_tokens": 10},
            },
        }
    )
    m = usage.measure("audio.transcriptions.create", {}, tokens)
    assert (m["input_tokens"], m["input_audio_tokens"], m["output_tokens"]) == (120, 110, 30)
    assert "audio_seconds" not in m


def test_audio_without_usage_is_billed_from_the_wav_sent():
    from iris.pipeline.stages.transcribe import encode_wav

    wav = encode_wav(__import__("numpy").zeros(16_000 * 3 + 100, dtype="<i2"), 16_000)
    request = {"file": ("chunk.wav", wav, "audio/wav")}
    assert usage.measure("audio.transcriptions.create", request, "plain text") == {
        "audio_seconds": 4
    }


PRICES = Settings().model_prices


@pytest.mark.parametrize(
    ("model", "m", "cost"),
    [
        ("gpt-5.5", {"input_tokens": 100_000}, 0.50),
        ("gpt-5.5", {"input_tokens": 1000, "cached_input_tokens": 1000}, 0.0005),
        ("gpt-5.5", {"input_tokens": 1000, "input_image_tokens": 765}, 0.005),
        ("gpt-5.5", {"output_tokens": 1000, "reasoning_tokens": 900}, 0.03),
        ("gpt-5.5-2026-04-23", {"output_tokens": 1000}, 0.03),
        ("gpt-5.5", {"input_tokens": 300_000, "output_tokens": 1000}, 3.0 + 0.045),
        (
            "gpt-image-2",
            {
                "input_tokens": 1000,
                "input_image_tokens": 400,
                "output_image_tokens": 2000,
                "output_tokens": 2000,
            },
            (600 * 5 + 400 * 8 + 2000 * 30) / 1e6,
        ),
        ("whisper-1", {"audio_seconds": 61}, 0.0061),
        (
            "gpt-4o-transcribe",
            {"input_tokens": 1000, "input_audio_tokens": 900, "output_tokens": 100},
            (100 * 2.5 + 900 * 2.5 + 100 * 10) / 1e6,
        ),
        ("gpt-5.5", {}, 0.0),
    ],
)
def test_price_call(model, m, cost):
    priced, reason = usage.price_call(model, m, PRICES)
    assert reason is None
    assert priced == pytest.approx(cost)


@pytest.mark.parametrize(
    ("model", "m", "reason"),
    [
        ("gpt-transcribe", {"audio_seconds": 10}, "no price for model 'gpt-transcribe'"),
        ("gpt-5.5-turbo", {"input_tokens": 1}, "no price for model 'gpt-5.5-turbo'"),
        ("gpt-4o-transcribe", {"audio_seconds": 10}, "gpt-4o-transcribe: no per-second audio"),
        ("gpt-image-2", {"output_tokens": 50, "output_image_tokens": 0}, "gpt-image-2: no output"),
        ("whisper-1", {"input_tokens": 10}, "whisper-1: no input"),
    ],
)
def test_unpriced_calls_say_why(model, m, reason):
    priced, why = usage.price_call(model, m, PRICES)
    assert priced is None
    assert why.startswith(reason)


def test_price_table_overrides_merge_with_defaults(monkeypatch):
    monkeypatch.setenv("IRIS_MODEL_PRICES", json.dumps({"my-model": {"input": 1.0}}))
    prices = Settings().model_prices
    assert prices["my-model"] == ModelPrice(input=1.0)
    assert prices["gpt-5.5"].output == 30.00


async def test_reasoning_effort_per_call_type(ctx, fake):
    ctx.settings = ctx.settings.model_copy(
        update={"check_reasoning_effort": "low", "notes_reasoning_effort": "high"}
    )
    v = figure_to_render(ctx)
    await render.check(ctx, v, b"src", b"candidate")
    await render.draw_svg(ctx, v, b"src", [])
    from iris.pipeline.stages import write

    await write.generate_section(ctx, json.dumps({"records": []}))
    assert [c["reasoning_effort"] for c in fake.calls] == ["low", "medium", "high"]


def test_render_cache_key_changes_only_when_an_effort_override_is_set(ctx):
    v = figure_to_render(ctx)
    base = render._cache_key(ctx, v, b"src")
    ctx.settings = ctx.settings.model_copy(update={"select_reasoning_effort": "low"})
    assert render._cache_key(ctx, v, b"src") == base
    ctx.settings = ctx.settings.model_copy(update={"check_reasoning_effort": "medium"})
    assert render._cache_key(ctx, v, b"src") == base
    ctx.settings = ctx.settings.model_copy(update={"check_reasoning_effort": "low"})
    assert render._cache_key(ctx, v, b"src") != base


def test_usage_route(client: TestClient, settings):
    assert client.get("/api/sessions/nope/usage").status_code == 404
    sid = client.post("/api/live", json={}).json()["id"]
    empty = client.get(f"/api/sessions/{sid}/usage").json()
    assert empty["records"] == [] and empty["total"]["calls"] == 0
    assert empty["prices_checked"] == PRICES_CHECKED

    log = SessionPaths(settings.sessions_dir / sid).usage_log
    for record in fixture_usage().records:
        usage.append_record(log, record)
    body = SessionUsage.model_validate(client.get(f"/api/sessions/{sid}/usage").json())
    assert body == fixture_usage()


def fixture_usage() -> SessionUsage:
    def rec(stage, purpose, model, cost, **kw):
        return UsageRecord(
            at=datetime(2026, 11, 3, tzinfo=UTC),
            stage=stage,
            purpose=purpose,
            operation="chat.completions.parse",
            model=model,
            latency_s=2.5,
            cost_usd=cost,
            **kw,
        )

    return usage.summarize(
        [
            rec(Stage.TRANSCRIBE, "transcription", "whisper-1", 0.36, audio_seconds=3600),
            rec(Stage.SELECT, "screen_reading", "gpt-5.5", 0.25, input_tokens=40_000),
            rec(Stage.RENDER, "svg", "gpt-5.5", 0.1, output_tokens=3000, reasoning_tokens=2000),
            rec(Stage.RENDER, "image", "gpt-image-2", 0.2, images=1),
            rec(Stage.RENDER, "figure_check", "mystery-1", None, unpriced="no price for model 'x'"),
        ]
    )


def test_iris_cost_prints_a_table_per_stage(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRIS_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        paths = SessionPaths(tmp_path / "sessions" / "abc").ensure()
        for record in fixture_usage().records:
            usage.append_record(paths.usage_log, record)
        monkeypatch.setattr(sys, "argv", ["iris", "cost", "abc"])
        with pytest.raises(SystemExit) as exit:
            cli.main()
        assert exit.value.code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert lines[0] == "session abc: $0.9100 + 1 unpriced over 5 model calls"
        stage_table = lines[lines.index("") + 1 : lines.index("") + 6]
        assert [line.split()[0] for line in stage_table] == [
            "stage",
            "transcribe",
            "select",
            "render",
            "total",
        ]
        render_row = stage_table[3].split()
        assert render_row[1] == "3"
        assert render_row[-4:] == ["$0.3000", "+", "1", "unpriced"]
        assert "  no price for model 'x'" in lines
        assert f"Prices checked against OpenAI's pricing page on {PRICES_CHECKED}." in lines

        monkeypatch.setattr(sys, "argv", ["iris", "cost", "missing"])
        with pytest.raises(SystemExit) as exit:
            cli.main()
        assert exit.value.code == 1
    finally:
        get_settings.cache_clear()


def test_iris_cost_defaults_to_the_most_recent_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRIS_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        monkeypatch.setattr(sys, "argv", ["iris", "cost"])
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert excinfo.value.code == 1
        assert "no sessions yet" in capsys.readouterr().err

        store = SessionStore(get_settings().db_path)
        older = store.create("older", SourceKind.UPLOAD)
        newer = store.create("newer", SourceKind.UPLOAD)
        store.close()
        with sqlite3.connect(get_settings().db_path) as conn:
            conn.execute(
                "UPDATE sessions SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                (older.id,),
            )
        for session in (older, newer):
            SessionPaths(tmp_path / "sessions" / session.id).ensure()
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert excinfo.value.code == 0
        assert capsys.readouterr().out.startswith(f"session {newer.id}: ")
    finally:
        get_settings.cache_clear()


def test_iris_cost_with_no_calls():
    assert cli.format_usage("abc", usage.summarize([])) == (
        "session abc: $0.0000 over 0 model calls (none recorded)"
    )
