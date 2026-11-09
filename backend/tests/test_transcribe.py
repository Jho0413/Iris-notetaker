"""Transcribe stage tests. No real API calls: a fake client stands in for OpenAI."""

import asyncio
import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from iris.config import Settings
from iris.models import EventType, SourceKind, Transcript
from iris.pipeline.context import StageContext
from iris.pipeline.stages import transcribe
from iris.pipeline.stages.transcribe import encode_wav, plan_chunks, transcribe_chunk
from iris.store.layout import SessionPaths, read_model

RATE = 16_000
SMALL = {"target_s": 3.0, "max_s": 5.0, "search_s": 1.0}  # small chunks so tests run fast


# --- Synthetic audio ---------------------------------------------------------------------------


def make_audio(pattern: list[tuple[str, float]], seed: int = 0) -> np.ndarray:
    """Join ("tone"|"silence", seconds) pieces into int16 samples. Silence has faint noise."""
    rng = np.random.default_rng(seed)
    parts = []
    for kind, dur in pattern:
        n = int(dur * RATE)
        noise = rng.normal(0, 20, n)
        if kind == "tone":
            t = np.arange(n) / RATE
            parts.append(8000 * np.sin(2 * np.pi * 440 * t) + noise)
        else:
            parts.append(noise)
    return np.concatenate(parts).astype(np.int16)


def bursts_pattern(n: int = 16) -> list[tuple[str, float]]:
    """Tone bursts of varying length separated by 0.4 s silences."""
    pattern = []
    for i in range(n):
        pattern.append(("tone", 0.6 + 0.15 * (i % 4)))
        pattern.append(("silence", 0.4))
    return pattern


def burst_times(pattern: list[tuple[str, float]]) -> list[tuple[float, float]]:
    times, t = [], 0.0
    for kind, dur in pattern:
        if kind == "tone":
            times.append((t, t + dur))
        t += dur
    return times


def is_quiet(samples: np.ndarray, i: int, margin_s: float = 0.05) -> bool:
    m = int(margin_s * RATE)
    return bool(np.abs(samples[max(0, i - m) : i + m].astype(np.int32)).max() < 200)


def write_wav(path: Path, samples: np.ndarray) -> None:
    path.write_bytes(encode_wav(samples, RATE))


def find_bursts(samples: np.ndarray, rate: int) -> list[tuple[float, float]]:
    """Tone runs in a chunk, in seconds from its start (what a perfect transcriber would hear)."""
    frame = rate // 100
    n = len(samples) // frame
    loud = np.abs(samples[: n * frame].astype(np.int32)).reshape(n, frame).max(axis=1) > 1000
    edges = np.flatnonzero(np.diff(np.concatenate(([0], loud.astype(np.int8), [0]))))
    return [
        (a * frame / rate, b * frame / rate) for a, b in zip(edges[::2], edges[1::2], strict=True)
    ]


# --- Fake OpenAI client ------------------------------------------------------------------------


class FakeTranscriptions:
    def __init__(self, fail_on_call: int | None = None, delay: float = 0.01):
        self.calls: list[dict] = []
        self.active = 0
        self.max_active = 0
        self.fail_on_call = fail_on_call
        self.delay = delay

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        call_no = len(self.calls)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if call_no == self.fail_on_call:
                raise RuntimeError("boom")
            _name, data, _mime = kwargs["file"]
            with wave.open(io.BytesIO(data)) as w:
                rate = w.getframerate()
                samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            segments = [
                SimpleNamespace(start=a, end=b, text=f" burst {a:.2f}")
                for a, b in find_bursts(samples, rate)
            ]
            segments.append(SimpleNamespace(start=0.0, end=0.1, text="   "))  # empty: stripped
            return SimpleNamespace(language="english", text="...", segments=segments)
        finally:
            self.active -= 1


def fake_client(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions(**kwargs)))


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    events: list[tuple[EventType, dict]] = []
    c = StageContext(
        session_id="s1",
        source=SourceKind.UPLOAD,
        paths=SessionPaths(tmp_path / "s1").ensure(),
        settings=Settings(data_dir=tmp_path, openai_api_key="test-key"),
        emit=lambda t, d: events.append((t, d)),
    )
    c.events = events  # type: ignore[attr-defined]
    return c


def use_client(monkeypatch, client) -> None:
    monkeypatch.setattr(transcribe, "get_openai", lambda _settings: client)


# --- plan_chunks -------------------------------------------------------------------------------


def test_plan_chunks_cuts_in_silences_and_respects_max():
    samples = make_audio(bursts_pattern(30))
    chunks = plan_chunks(samples, RATE, **SMALL)
    assert len(chunks) > 3
    assert chunks[0][0] == 0 and chunks[-1][1] == len(samples)
    for (_, e1), (s2, _) in zip(chunks, chunks[1:], strict=False):
        assert e1 == s2  # contiguous
    for s, e in chunks:
        assert 0 < e - s <= SMALL["max_s"] * RATE
    for _, cut in chunks[:-1]:
        assert is_quiet(samples, cut), cut / RATE


def test_plan_chunks_finds_the_one_pause_in_the_window():
    # Continuous tone except one short pause 0.8 s after the 3 s target.
    samples = make_audio([("tone", 3.6), ("silence", 0.4), ("tone", 2.0)])
    chunks = plan_chunks(samples, RATE, **SMALL)
    assert len(chunks) == 2
    cut = chunks[0][1] / RATE
    assert 3.6 < cut < 4.0


def test_plan_chunks_prefers_the_pause_closest_to_target():
    samples = make_audio(
        [("tone", 2.0), ("silence", 0.4), ("tone", 0.7), ("silence", 0.4), ("tone", 4.0)]
    )
    # Both pauses (2.0-2.4 s and 3.1-3.5 s) are in the 2-4 s window and equally quiet; the second
    # is closer to the 3 s target.
    cut = plan_chunks(samples, RATE, **SMALL)[0][1] / RATE
    assert 3.1 < cut < 3.5


def test_plan_chunks_without_silence_still_respects_max():
    samples = make_audio([("tone", 20.0)])
    chunks = plan_chunks(samples, RATE, target_s=3.0, max_s=3.5, search_s=1.0)
    assert sum(e - s for s, e in chunks) == len(samples)
    assert all(e - s <= 3.5 * RATE for s, e in chunks)


def test_plan_chunks_short_audio_is_one_chunk():
    samples = make_audio([("tone", 2.0), ("silence", 1.0)])
    assert plan_chunks(samples, RATE, **SMALL) == [(0, len(samples))]
    assert plan_chunks(samples, RATE) == [(0, len(samples))]  # default 5 min target


def test_plan_chunks_empty_and_bad_args():
    assert plan_chunks(np.zeros(0, dtype=np.int16), RATE) == []
    with pytest.raises(ValueError):
        plan_chunks(np.zeros(10, dtype=np.int16), RATE, target_s=5, max_s=4)


# --- transcribe_chunk parsing ------------------------------------------------------------------


class Recorder:
    def __init__(self, response):
        self.response = response
        self.kwargs: dict = {}
        self.audio = SimpleNamespace(transcriptions=self)

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


async def test_transcribe_chunk_whisper_verbose_json():
    client = Recorder(
        {
            "language": "english",
            "segments": [
                {"start": 0.5, "end": 2.0, "text": " Hello there. "},
                {"start": 2.0, "end": 99.0, "text": " runs past the end"},
                {"start": 3.0, "end": 3.5, "text": ""},
            ],
        }
    )
    result = await transcribe_chunk(client, "whisper-1", b"wav", 10.0)
    assert client.kwargs["response_format"] == "verbose_json"
    assert client.kwargs["timestamp_granularities"] == ["segment"]
    assert client.kwargs["file"] == ("chunk.wav", b"wav", "audio/wav")
    assert result.language == "english"
    assert [(s.start, s.end, s.text) for s in result.segments] == [
        (0.5, 2.0, "Hello there."),
        (2.0, 10.0, "runs past the end"),  # clamped to the chunk
    ]


async def test_transcribe_chunk_text_only_model_spans_chunk():
    client = Recorder(SimpleNamespace(text=" Just text. ", usage=None))
    result = await transcribe_chunk(client, "gpt-4o-mini-transcribe", b"wav", 42.0)
    assert client.kwargs["response_format"] == "json"
    assert "timestamp_granularities" not in client.kwargs
    assert [(s.start, s.end, s.text) for s in result.segments] == [(0.0, 42.0, "Just text.")]


async def test_transcribe_chunk_diarized_keeps_speakers():
    client = Recorder(
        {"segments": [{"start": 0, "end": 1, "text": "Hi", "speaker": "A", "id": "x"}]}
    )
    result = await transcribe_chunk(client, "gpt-4o-transcribe-diarize", b"wav", 5.0)
    assert client.kwargs["response_format"] == "diarized_json"
    assert client.kwargs["chunking_strategy"] == "auto"
    assert result.segments[0].speaker == "A"


async def test_transcribe_chunk_sends_the_language_only_when_given():
    client = Recorder({"text": "hi"})
    await transcribe_chunk(client, "whisper-1", b"wav", 1.0, language="en")
    assert client.kwargs["language"] == "en"
    await transcribe_chunk(client, "whisper-1", b"wav", 1.0)
    assert "language" not in client.kwargs  # the model detects it


@pytest.mark.parametrize(
    ("value", "expected"), [("en", "en"), (" FR ", "fr"), ("auto", None), ("", None), (None, None)]
)
def test_transcribe_language_setting(tmp_path: Path, value, expected):
    s = Settings(data_dir=tmp_path, transcribe_language=value)
    assert s.transcribe_language == expected


@pytest.mark.parametrize("value", ["english", "e", "en-GB", "jp", "zz"])
def test_transcribe_language_setting_rejects_unknown_codes(tmp_path: Path, value):
    with pytest.raises(ValueError, match="not a language Whisper knows"):
        Settings(data_dir=tmp_path, transcribe_language=value)


def test_transcribe_language_defaults_to_english(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("IRIS_TRANSCRIBE_LANGUAGE", raising=False)
    assert Settings(data_dir=tmp_path, _env_file=None).transcribe_language == "en"


async def test_transcribe_chunk_plain_string_response():
    result = await transcribe_chunk(Recorder("hello"), "some-model", b"wav", 3.0)
    assert result.segments[0].text == "hello"


# --- The stage ---------------------------------------------------------------------------------


async def test_stage_offsets_and_merges_timestamps(ctx, monkeypatch):
    pattern = bursts_pattern(20)
    write_wav(ctx.paths.audio, make_audio(pattern))
    client = fake_client()
    use_client(monkeypatch, client)

    await transcribe.run(ctx, **SMALL)

    calls = client.audio.transcriptions.calls
    assert len(calls) > 3
    assert all(c["model"] == ctx.settings.transcribe_model for c in calls)
    transcript = read_model(ctx.paths.transcript, Transcript)
    assert transcript.language == "english"
    starts = [s.start for s in transcript.segments]
    assert starts == sorted(starts)
    assert all(s.text.strip() for s in transcript.segments)  # empty segments stripped
    expected = burst_times(pattern)
    assert len(transcript.segments) == len(expected)
    for seg, (a, b) in zip(transcript.segments, expected, strict=True):
        assert seg.start == pytest.approx(a, abs=0.02)
        assert seg.end == pytest.approx(b, abs=0.02)


async def test_stage_emits_transcript_chunk_events(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(20)))
    use_client(monkeypatch, fake_client())
    await transcribe.run(ctx, **SMALL)

    events = [d for t, d in ctx.events if t == EventType.TRANSCRIPT_CHUNK]
    n_chunks = len(list(ctx.paths.transcript_chunks_dir.glob("*.json")))
    assert sorted(e["chunk"] for e in events) == list(range(n_chunks))
    transcript = read_model(ctx.paths.transcript, Transcript)
    emitted = sorted((s["start"], s["text"]) for e in events for s in e["segments"])
    assert emitted == [(s.start, s.text) for s in transcript.segments]


async def test_stage_honours_concurrency_limit(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(30)))
    client = fake_client(delay=0.05)
    use_client(monkeypatch, client)
    await transcribe.run(ctx, **SMALL, concurrency=2)
    fake = client.audio.transcriptions
    assert len(fake.calls) > 4
    assert fake.max_active == 2


async def test_stage_reuses_cached_chunks(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(20)))
    use_client(monkeypatch, fake_client())
    await transcribe.run(ctx, **SMALL)
    first = ctx.paths.transcript.read_text()

    def no_client(_settings):
        raise AssertionError("no API call expected when every chunk is cached")

    monkeypatch.setattr(transcribe, "get_openai", no_client)
    ctx.paths.transcript.unlink()
    await transcribe.run(ctx, **SMALL)
    assert ctx.paths.transcript.read_text() == first


async def test_stage_sends_the_language_and_a_changed_language_resends_every_chunk(
    ctx, monkeypatch
):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(20)))
    client = fake_client()
    use_client(monkeypatch, client)
    ctx.settings = ctx.settings.model_copy(update={"transcribe_language": "en"})
    await transcribe.run(ctx, **SMALL)
    first = client.audio.transcriptions.calls
    assert first and all(c["language"] == "en" for c in first)

    # Chunks cached before the setting existed (or with another language) are paid for again.
    client = fake_client()
    use_client(monkeypatch, client)
    ctx.settings = ctx.settings.model_copy(update={"transcribe_language": None})
    await transcribe.run(ctx, **SMALL)
    again = client.audio.transcriptions.calls
    assert len(again) == len(first)
    assert all("language" not in c for c in again)


def strip_requested_language(cache_dir: Path) -> None:
    """Make every cache entry look as it did before ``requested_language`` existed."""
    for path in cache_dir.glob("*.json"):
        data = json.loads(path.read_text())
        del data["requested_language"]
        path.write_text(json.dumps(data))


async def test_chunks_cached_before_the_language_setting_are_resent_in_english(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(20)))
    use_client(monkeypatch, fake_client())
    ctx.settings = ctx.settings.model_copy(update={"transcribe_language": None})
    await transcribe.run(ctx, **SMALL)
    strip_requested_language(ctx.paths.transcript_chunks_dir)

    # Detected then, detected now: still cached.
    def no_client(_settings):
        raise AssertionError("no API call expected: these chunks were detected too")

    monkeypatch.setattr(transcribe, "get_openai", no_client)
    await transcribe.run(ctx, **SMALL)

    # Under the default ("en") they may hold a misdetected language: every chunk is sent again.
    client = fake_client()
    use_client(monkeypatch, client)
    ctx.settings = ctx.settings.model_copy(update={"transcribe_language": "en"})
    await transcribe.run(ctx, **SMALL)
    calls = client.audio.transcriptions.calls
    n_chunks = len(list(ctx.paths.transcript_chunks_dir.glob("*.json")))
    assert len(calls) == n_chunks and all(c["language"] == "en" for c in calls)


async def test_stage_resends_chunks_whose_boundaries_changed(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(20)))
    use_client(monkeypatch, fake_client())
    await transcribe.run(ctx, **SMALL)

    client = fake_client()
    use_client(monkeypatch, client)
    await transcribe.run(ctx, target_s=6.0, max_s=8.0, search_s=1.0)
    assert len(client.audio.transcriptions.calls) > 0


async def test_stage_resends_chunk_if_audio_changed(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(4), seed=1))
    use_client(monkeypatch, fake_client())
    await transcribe.run(ctx, **SMALL)

    write_wav(ctx.paths.audio, make_audio(bursts_pattern(4), seed=2))  # same length, new audio
    client = fake_client()
    use_client(monkeypatch, client)
    await transcribe.run(ctx, **SMALL)
    assert len(client.audio.transcriptions.calls) > 0


async def test_failing_chunk_fails_stage_but_keeps_others_cached(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio(bursts_pattern(20)))
    n_chunks = len(plan_chunks(make_audio(bursts_pattern(20)), RATE, **SMALL))
    use_client(monkeypatch, fake_client(fail_on_call=2))

    with pytest.raises(transcribe.TranscribeError, match=r"1 of \d+ transcription chunks failed"):
        await transcribe.run(ctx, **SMALL, concurrency=1)
    assert not ctx.paths.transcript.exists()
    assert len(list(ctx.paths.transcript_chunks_dir.glob("*.json"))) == n_chunks - 1

    client = fake_client()
    use_client(monkeypatch, client)
    await transcribe.run(ctx, **SMALL)
    assert len(client.audio.transcriptions.calls) == 1  # only the failed chunk is re-sent
    assert ctx.paths.transcript.exists()


async def test_stage_on_silent_short_audio(ctx, monkeypatch):
    write_wav(ctx.paths.audio, make_audio([("silence", 1.0)]))
    client = fake_client()
    use_client(monkeypatch, client)
    await transcribe.run(ctx)
    assert len(client.audio.transcriptions.calls) == 1
    assert read_model(ctx.paths.transcript, Transcript).segments == []


# --- Text-only models (no timestamps) ----------------------------------------------------------


class TextOnlyTranscriptions:
    """Like gpt-transcribe: ``json`` with ``text`` and ``languages``, no segments."""

    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        with wave.open(io.BytesIO(kwargs["file"][1])) as w:
            dur = w.getnframes() / w.getframerate()
        return SimpleNamespace(
            text=f" chunk of {dur:.2f}s ", languages=[SimpleNamespace(code="en")], usage=None
        )


def test_chunk_lengths_for_models():
    assert transcribe.chunk_lengths_for("whisper-1") == (300.0, 600.0)
    for model in ("gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"):
        target, max_s = transcribe.chunk_lengths_for(model)
        assert target <= 15 and max_s <= 30


async def test_text_only_model_gives_one_segment_per_short_chunk(ctx, monkeypatch):
    ctx.settings.transcribe_model = "gpt-transcribe"
    samples = make_audio(bursts_pattern(60))  # ~73 s
    write_wav(ctx.paths.audio, samples)
    fake = TextOnlyTranscriptions()
    use_client(monkeypatch, SimpleNamespace(audio=SimpleNamespace(transcriptions=fake)))

    await transcribe.run(ctx)  # chunk lengths from chunk_lengths_for("gpt-transcribe")

    target, max_s = transcribe.chunk_lengths_for("gpt-transcribe")
    chunks = plan_chunks(samples, RATE, target, max_s)
    assert len(chunks) >= 3 and all(e - s <= max_s * RATE for s, e in chunks)
    assert all(c["response_format"] == "json" for c in fake.calls)
    assert all("timestamp_granularities" not in c for c in fake.calls)
    assert len(fake.calls) == len(chunks)

    transcript = read_model(ctx.paths.transcript, Transcript)
    assert transcript.language == "en"
    assert [(s.start, s.end) for s in transcript.segments] == [
        pytest.approx((s / RATE, e / RATE)) for s, e in chunks
    ]
    for seg in transcript.segments:
        assert seg.text == f"chunk of {seg.end - seg.start:.2f}s"
    for _, cut in chunks[:-1]:
        assert is_quiet(samples, cut)  # still cut at silences


@pytest.mark.parametrize("seconds", [10, 12, 15])
def test_remaining_audio_within_max_is_one_chunk(seconds):
    samples = np.zeros(seconds * RATE, dtype=np.int16)
    assert plan_chunks(samples, RATE, target_s=10, max_s=15, search_s=2) == [(0, len(samples))]


@pytest.mark.parametrize("sample_count", [10 * RATE + 1, 12 * RATE, 22 * RATE])
def test_final_chunk_never_exceeds_max(sample_count):
    samples = np.zeros(sample_count, dtype=np.int16)
    chunks = plan_chunks(samples, RATE, target_s=10, max_s=10, search_s=5)
    assert chunks[0][0] == 0
    assert chunks[-1][1] == sample_count
    assert all(0 < end - start <= 10 * RATE for start, end in chunks)
    assert all(left[1] == right[0] for left, right in zip(chunks, chunks[1:], strict=False))
