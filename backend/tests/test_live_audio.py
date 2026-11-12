"""The live audio lane: decoding chunks as they arrive, cutting windows at pauses, transcribing
them and committing them in order, with gaps, stop and restart.

Recordings are synthetic WebM/Opus streams cut into byte ranges like MediaRecorder chunks; the
transcription client is a fake that records each call.
"""

import asyncio
import hashlib
import io
import json
import random
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from iris.config import Settings
from iris.live import Lane, LiveProcessor, LiveTimeline
from iris.live import audio as audio_lane
from iris.live.audio import (
    CONFIRM_S,
    PROMPT_CHARS,
    AudioLane,
    WindowFailed,
    window_cut,
)
from iris.live.replay import encode_webm
from iris.llm import client as llm_client
from iris.llm import usage
from iris.models import EventType, SourceKind, Stage, Transcript
from iris.pipeline.context import StageContext
from iris.pipeline.stages.transcribe import encode_wav, transcribe_chunk
from iris.sources.browser_live import (
    CLUSTER_ID,
    audio_chunks,
    finalize_audio,
    store_audio_chunk,
)
from iris.store.layout import SessionPaths, read_model, write_model
from tests.api_errors import connection_error, status_error
from tests.test_live import split

RATE = 16_000
SECONDS = 12
# "Words": tone bursts with 0.5 s pauses between them, so every 1..2.5 s window has a pause.
WORDS = [
    (0.2, 0.9),
    (1.4, 2.3),
    (2.8, 3.4),
    (3.9, 5.0),
    (5.5, 6.1),
    (6.6, 7.7),
    (8.2, 8.8),
    (9.3, 10.4),
    (10.9, 11.6),
]


def speech_webm(words=WORDS, seconds: int = SECONDS) -> bytes:
    """Mono Opus WebM at 48 kHz: a 440 Hz tone during each word, silence between."""
    src_rate, n = 48_000, 960  # 20 ms frames
    t = np.arange(seconds * src_rate) / src_rate
    starts = (np.arange(len(t)) // n * n) / src_rate  # each 20 ms frame is loud or quiet whole
    loud = np.zeros(len(t), bool)
    for a, b in words:
        loud |= (starts >= a) & (starts < b)
    return encode_webm([(0.3 * loud * np.sin(2 * np.pi * 440 * t)).astype(np.float32)]).data


@pytest.fixture(scope="module")
def webm() -> bytes:
    return speech_webm()


@pytest.fixture
def paths(tmp_path: Path) -> SessionPaths:
    return SessionPaths(tmp_path / "s1").ensure()


def settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path / "data",
        "openai_api_key": "test",
        "live_window_min_s": 1.0,
        "live_window_max_s": 2.5,
        "live_transcribe_concurrency": 1,
        "live_audio_gap_wait_s": 30.0,
    }
    return Settings(**{**values, **overrides})


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


class FakeTranscriber:
    """Records every call; each returns one segment, by default naming the call (``text(n, sha1)``
    can make it depend on the audio instead). ``gate(n)`` may delay or fail call ``n``."""

    def __init__(self, gate=None, words_per_call: int = 2, text=None):
        self.calls: list[dict] = []
        self.finished: list[int] = []
        self.gate = gate
        self.text = text or (
            lambda n, _sha1: " ".join(f"call{n}word{k}" for k in range(words_per_call))
        )
        self.client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=self.create))
        )

    async def create(self, **kwargs):
        n = len(self.calls)
        data = kwargs["file"][1]
        self.calls.append(
            {
                "prompt": kwargs.get("prompt"),
                "language": kwargs.get("language"),
                "sha1": hashlib.sha1(data).hexdigest(),
                "seconds": wav_seconds(data),
            }
        )
        if self.gate is not None:
            await self.gate(n)
        self.finished.append(n)
        text = self.text(n, self.calls[n]["sha1"])
        return {
            "language": "english",
            "segments": [{"start": 0.0, "end": wav_seconds(data), "text": text}],
        }


class Run:
    """One lane over a session folder, with its timeline and the events it emitted."""

    def __init__(
        self,
        paths: SessionPaths,
        s: Settings,
        fake: FakeTranscriber,
        monkeypatch,
        metered: bool = False,
    ):
        if metered:  # through the real get_openai, which records usage
            monkeypatch.setattr(llm_client, "_client", lambda _key: fake.client)
        else:
            monkeypatch.setattr(audio_lane, "get_openai", lambda _: fake.client)
        self.events: list[tuple[EventType, dict]] = []
        self.ctx = StageContext(
            "s1",
            SourceKind.BROWSER_LIVE,
            paths,
            s,
            emit=lambda t, d: self.events.append((t, d)),
        )
        self.timeline = LiveTimeline()
        self.lane = AudioLane(self.ctx, self.timeline)
        self.task = asyncio.create_task(self.lane.run())

    def store(self, chunks: list[bytes], seqs) -> None:
        for seq in seqs:
            store_audio_chunk(self.ctx.paths, seq, chunks[seq])
            self.lane.wake()

    async def until(self, predicate, timeout: float = 10.0) -> None:
        await asyncio.wait_for(self.timeline.wait_until(predicate), timeout)

    async def stop(self, timeout: float = 20.0) -> None:
        await self.timeline.request_end()
        self.lane.wake()
        await asyncio.wait_for(self.task, timeout)

    def windows(self) -> list[tuple[float, float]]:
        """Committed windows as (start, end) seconds, from the event stream and the cache."""
        names = sorted(
            (int(p.stem.split("_")[0]), p)
            for p in self.ctx.paths.live_transcript_windows_dir.iterdir()
        )
        return [
            (int(p.stem.split("_")[1]) / 1000, int(p.stem.split("_")[2]) / 1000) for _, p in names
        ]

    def chunk_events(self) -> list[int]:
        return [d["chunk"] for t, d in self.events if t == EventType.TRANSCRIPT_CHUNK]


def shuffled(n: int, skip: set[int] = frozenset(), seed: int = 0) -> list[int]:
    order = [i for i in range(n) if i not in skip]
    random.Random(seed).shuffle(order)
    return order


def decoder_waiting_for(monkeypatch, seq: int) -> threading.Event:
    """Set once the decoder has decoded everything before chunk ``seq`` and waits for it (it
    isn't stored yet), with what it decoded flushed to ``live/audio.pcm``. The helper flushes it
    itself, so the PCM is final when the event is seen, rather than racing the decoder's own
    flush just after."""
    waiting = threading.Event()
    wait_chunk = audio_lane.LiveDecoder.wait_chunk

    def watched(decoder, n):
        if n == seq:
            decoder._report()  # what wait_chunk reports before it blocks, so the PCM is final
            waiting.set()
        return wait_chunk(decoder, n)

    monkeypatch.setattr(audio_lane.LiveDecoder, "wait_chunk", watched)
    return waiting


async def reached(waiting: threading.Event) -> None:
    assert await asyncio.to_thread(waiting.wait, 10), "the decoder never got there"


def live_pcm(paths: SessionPaths) -> np.ndarray:
    return np.fromfile(paths.live_pcm, dtype="<i2")


def finalized(tmp_path: Path, chunks: list[bytes], skip: set[int] = frozenset()) -> np.ndarray:
    ref = SessionPaths(tmp_path / f"ref{len(skip)}{sorted(skip)}").ensure()
    for seq in range(len(chunks)):
        if seq not in skip:
            store_audio_chunk(ref, seq, chunks[seq])
    finalize_audio(audio_chunks(ref), ref.audio, RATE)
    with wave.open(str(ref.audio)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


# --- Decoding ------------------------------------------------------------------------------------


async def test_shuffled_chunks_decode_to_the_same_pcm_as_ingest(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    run = Run(paths, settings(tmp_path), FakeTranscriber(), monkeypatch)
    order = shuffled(len(chunks))
    run.store(chunks, order + order[:4])  # out of order, some twice
    await run.stop()
    assert np.array_equal(live_pcm(paths), finalized(tmp_path, chunks))
    assert Lane.AUDIO not in run.timeline.skipped_input


@pytest.mark.parametrize("lost", [{5}, {12, 13, 20}])
async def test_a_missing_chunk_is_waited_for_then_skipped_as_silence(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch, lost: set[int]
):
    chunks = split(webm, 2000)
    run = Run(paths, settings(tmp_path, live_audio_gap_wait_s=0.2), FakeTranscriber(), monkeypatch)
    run.store(chunks, shuffled(len(chunks), skip=lost))
    # Skipped while still recording (after the wait), not only at stop.
    await run.until(lambda: Lane.AUDIO in run.timeline.skipped_input)
    assert not run.timeline.ending
    await run.stop()
    assert np.array_equal(live_pcm(paths), finalized(tmp_path, chunks, skip=lost))
    assert len(live_pcm(paths)) / RATE == pytest.approx(SECONDS, abs=0.1)


async def test_a_chunk_arriving_within_the_wait_is_used(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    waiting = decoder_waiting_for(monkeypatch, 7)
    run = Run(paths, settings(tmp_path, live_audio_gap_wait_s=30), FakeTranscriber(), monkeypatch)
    run.store(chunks, shuffled(len(chunks), skip={7}))
    await reached(waiting)
    # Time passes while it waits, well inside the 30 s wait: one that gives up early skips it.
    await asyncio.sleep(0.3)
    run.store(chunks, [7])
    await run.stop()
    assert Lane.AUDIO not in run.timeline.skipped_input
    assert np.array_equal(live_pcm(paths), finalized(tmp_path, chunks))


async def test_stop_does_not_wait_for_a_missing_chunk(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    run = Run(paths, settings(tmp_path, live_audio_gap_wait_s=300), FakeTranscriber(), monkeypatch)
    run.store(chunks, shuffled(len(chunks), skip={9}))
    await run.stop(timeout=10)  # well inside the gap wait
    assert Lane.AUDIO in run.timeline.skipped_input
    assert np.array_equal(live_pcm(paths), finalized(tmp_path, chunks, skip={9}))


@pytest.mark.parametrize("partial_id", [0, 2])
async def test_header_only_first_chunk_then_a_gap(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch, partial_id: int
):
    header_end = webm.find(CLUSTER_ID) + partial_id
    chunks = [webm[:header_end], *split(webm[header_end:], 2000)]
    run = Run(paths, settings(tmp_path, live_audio_gap_wait_s=0.1), FakeTranscriber(), monkeypatch)
    run.store(chunks, shuffled(len(chunks), skip={1}))
    await run.until(lambda: Lane.AUDIO in run.timeline.skipped_input)
    await run.stop()
    assert np.array_equal(live_pcm(paths), finalized(tmp_path, chunks, skip={1}))


async def test_without_the_first_chunk_nothing_decodes_and_input_is_skipped(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    fake = FakeTranscriber()
    run = Run(paths, settings(tmp_path, live_audio_gap_wait_s=0.05), fake, monkeypatch)
    run.store(chunks, range(1, len(chunks)))
    await asyncio.sleep(0.2)
    assert Lane.AUDIO not in run.timeline.skipped_input  # chunk 0 is waited for until stop
    await run.stop()
    assert Lane.AUDIO in run.timeline.skipped_input
    assert not fake.calls and not run.timeline.segments


async def test_no_audio_at_all(tmp_path: Path, paths: SessionPaths, monkeypatch):
    fake = FakeTranscriber()
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    await run.stop()
    assert not fake.calls and Lane.AUDIO not in run.timeline.skipped_input
    assert read_model(paths.live_transcript, Transcript).segments == []


async def test_unreadable_audio_is_skipped_not_fatal(
    tmp_path: Path, paths: SessionPaths, monkeypatch
):
    run = Run(paths, settings(tmp_path), FakeTranscriber(), monkeypatch)
    run.store([b"\x1a\x45\xdf\xa3 but not really webm"], [0])
    await run.stop()
    assert Lane.AUDIO in run.timeline.skipped_input  # stop runs the full pipeline instead


# --- Windows -------------------------------------------------------------------------------------


def in_silence(t: float, pad: float = 0.08) -> bool:
    return all(not (a - pad < t < b + pad) for a, b in WORDS)


async def test_windows_are_cut_in_pauses_and_cover_the_audio(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    # 1000-byte chunks are ~0.1 s of audio each: chunk edges fall inside every word.
    chunks = split(webm, 1000)
    fake = FakeTranscriber()
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    run.store(chunks, shuffled(len(chunks)))
    await run.stop()

    windows = run.windows()
    assert windows[0][0] == 0
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:], strict=False))  # contiguous
    assert windows[-1][1] == pytest.approx(len(live_pcm(paths)) / RATE, abs=0.001)
    for start, end in windows[:-1]:
        assert 1.0 - 1e-3 <= end - start <= 2.5 + 1e-3
        assert in_silence(end), f"cut at {end:.3f}s is inside a word"
    pcm = live_pcm(paths)
    for _, end in windows[:-1]:
        i = round(end * RATE)
        assert np.abs(pcm[i - 800 : i + 800]).max() < 1000  # 50 ms either side is quiet
    # Each committed window was sent once, with its own audio.
    assert [c["seconds"] for c in fake.calls] == pytest.approx(
        [e - s for s, e in windows], abs=1e-3
    )


def test_window_cut_prefers_the_quietest_point_else_the_maximum():
    tone = (8000 * np.sin(np.arange(RATE * 4) * 0.2)).astype(np.int16)
    gap = tone.copy()
    gap[int(1.6 * RATE) : int(2.1 * RATE)] = 0
    cut = window_cut(gap, RATE, RATE, int(3 * RATE)) / RATE
    assert 1.6 < cut < 2.1
    assert window_cut(tone, RATE, RATE, int(3 * RATE)) == 3 * RATE  # no pause: cut at the max


async def test_transcript_commits_while_recording_and_stop_flushes_the_rest(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    half = len(chunks) // 2
    decoded_half = decoder_waiting_for(monkeypatch, half)
    decisions: list[tuple[int, int | None]] = []  # (decoded samples, the cut decided or None)
    next_cut = AudioLane._next_cut

    async def watched(lane, cut_at, decoded, final):
        cut = await next_cut(lane, cut_at, decoded, final)
        decisions.append((decoded, cut))
        return cut

    monkeypatch.setattr(AudioLane, "_next_cut", watched)
    run = Run(paths, settings(tmp_path), FakeTranscriber(), monkeypatch)
    run.store(chunks, range(half))
    await run.until(lambda: run.timeline.transcribed_to > 0)  # before stop
    await reached(decoded_half)
    samples = len(live_pcm(paths))
    decoded = samples / RATE
    # The lane has cut everything it can from these chunks, and committed it.
    async with asyncio.timeout(10):
        while (samples, None) not in decisions:
            await asyncio.sleep(0.01)
    last_cut = max(cut for _, cut in decisions if cut is not None)
    await run.until(lambda: run.timeline.transcribed_to >= last_cut / RATE - 1e-6)
    early = run.timeline.transcribed_to
    # The rest waits for more audio: a cut needs the whole range up to the maximum window, plus
    # CONFIRM_S, decoded.
    assert decoded - early < 2.5 + CONFIRM_S
    assert run.chunk_events()
    # Nothing is cut in the last moments of what has been decoded.
    assert early <= decoded - CONFIRM_S + 1e-3

    run.store(chunks, range(half, len(chunks)))
    await run.stop()
    duration = len(live_pcm(paths)) / RATE
    assert run.timeline.transcribed_to == pytest.approx(duration, abs=1e-3)
    assert run.timeline.transcribed_to > early
    n = len(run.windows())
    assert run.chunk_events() == list(range(n))
    transcript = read_model(paths.live_transcript, Transcript)
    assert transcript == run.timeline.transcript().model_copy(update={"language": "english"})
    assert len(transcript.segments) == n
    starts = [s.start for s in transcript.segments]
    assert starts == sorted(starts)
    event = next(d for t, d in run.events if t == EventType.TRANSCRIPT_CHUNK)
    assert set(event) == {"chunk", "segments"} and event["segments"][0]["text"]


async def test_windows_commit_in_order_when_transcription_finishes_out_of_order(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    s = settings(tmp_path, live_transcribe_concurrency=2)
    chunks = split(webm, 2000)
    # Cuts depend only on the audio, so a reference run says what window 0's audio is.
    ref_paths = SessionPaths(tmp_path / "ref").ensure()
    ref = Run(ref_paths, s, FakeTranscriber(), monkeypatch)
    ref.store(chunks, range(len(chunks)))
    await ref.stop()
    first_end = round(ref.windows()[0][1] * RATE)
    held = hashlib.sha1(encode_wav(live_pcm(ref_paths)[:first_end], RATE)).hexdigest()

    later_done = asyncio.Event()

    async def gate(n: int) -> None:
        if fake.calls[n]["sha1"] == held:  # window 0 waits until a later window has finished
            await asyncio.wait_for(later_done.wait(), 10)
        else:
            later_done.set()

    fake = FakeTranscriber(gate, text=lambda n, sha1: "first" if sha1 == held else f"later{n}")
    run = Run(paths, s, fake, monkeypatch)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    window0_call = next(n for n, c in enumerate(fake.calls) if c["sha1"] == held)
    assert fake.finished[0] != window0_call  # a later window finished first...
    assert run.chunk_events() == list(range(len(run.windows())))  # ...but window 0 committed first
    assert run.timeline.segments[0].text == "first"


async def test_each_window_is_prompted_with_the_previous_text(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    fake = FakeTranscriber(words_per_call=60)  # long texts: the prompt keeps only the tail
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    assert fake.calls[0]["prompt"] is None
    previous = run.timeline.segments[0].text
    prompt = fake.calls[1]["prompt"]
    assert len(prompt) <= PROMPT_CHARS and previous.endswith(prompt)
    assert prompt.startswith("call0word")  # starts at a word
    assert all(c["prompt"].startswith(f"call{i}") for i, c in enumerate(fake.calls[1:]))


async def test_prompts_are_only_sent_to_models_that_take_them():
    sent = []

    async def create(**kwargs):
        sent.append(kwargs.get("prompt"))
        return {"text": "hi"}

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    await transcribe_chunk(client, "whisper-1", b"", 1.0, prompt="before")
    await transcribe_chunk(client, "whisper-1", b"", 1.0)  # uploads pass none
    await transcribe_chunk(client, "gpt-4o-transcribe", b"", 1.0, prompt="before")
    assert sent == ["before", None, None]


# --- Failures, restart ---------------------------------------------------------------------------


async def test_a_window_that_keeps_failing_fails_the_lane(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    async def gate(n: int) -> None:
        raise connection_error("API down")

    fake = FakeTranscriber(gate)
    s = settings(tmp_path)
    run = Run(paths, s, fake, monkeypatch)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    with pytest.raises(WindowFailed, match="API down"):
        await asyncio.wait_for(run.task, 10)  # fails while recording; the thread is stopped
    assert len(fake.calls) == 1 + len(s.api_retry_delays_s)  # retried with the settings' delays


async def test_a_blocking_error_fails_the_lane_without_retries(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    async def gate(n: int) -> None:
        raise status_error(401, message="bad key")

    fake = FakeTranscriber(gate)
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    with pytest.raises(WindowFailed, match="blocking"):
        await asyncio.wait_for(run.task, 10)
    assert len(fake.calls) == 1


async def test_a_transient_failure_is_retried(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    async def gate(n: int) -> None:
        if n == 0:
            raise connection_error("timeout")

    fake = FakeTranscriber(gate)
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    assert len(fake.calls) == len(run.windows()) + 1
    assert fake.calls[0]["sha1"] == fake.calls[1]["sha1"]


async def test_failing_lane_is_marked_failed_by_the_processor(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    async def gate(n: int) -> None:
        raise RuntimeError("API down")

    fake = FakeTranscriber(gate)
    monkeypatch.setattr(audio_lane, "get_openai", lambda _: fake.client)
    ctx = StageContext("s1", SourceKind.BROWSER_LIVE, paths, settings(tmp_path))
    processor = LiveProcessor(ctx, lambda c, t: [AudioLane(c, t)], finish_timeout_s=10)
    processor.start()
    for seq, data in enumerate(split(webm, 2000)):
        store_audio_chunk(paths, seq, data)
        processor.notify_audio()
    timeline = processor.timeline
    await asyncio.wait_for(timeline.wait_until(lambda: Lane.AUDIO in timeline.failed), 10)
    timeline = await processor.finish()
    assert "WindowFailed" in timeline.failed[Lane.AUDIO]


async def test_a_window_transcribed_at_stop_is_still_retried(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    """The lane's flush at stop is what stop waits for: a temporary error there is retried, so
    the live transcript is kept rather than transcribed again by the pipeline."""
    failed_at_stop: list[int] = []
    processor: LiveProcessor | None = None

    async def gate(n: int) -> None:
        if processor is not None and processor.timeline.ending and not failed_at_stop:
            failed_at_stop.append(n)
            raise connection_error("blip")

    fake = FakeTranscriber(gate)
    monkeypatch.setattr(audio_lane, "get_openai", lambda _: fake.client)
    ctx = StageContext(
        "s1", SourceKind.BROWSER_LIVE, paths, settings(tmp_path, api_retry_delays_s=[0.01])
    )
    processor = LiveProcessor(ctx, lambda c, t: [AudioLane(c, t)], finish_timeout_s=10)
    processor.start()
    for seq, data in enumerate(split(webm, 2000)):
        store_audio_chunk(paths, seq, data)
        processor.notify_audio()
    timeline = await processor.finish()
    assert failed_at_stop  # a call after stop did fail once
    assert Lane.AUDIO in timeline.finished and Lane.AUDIO not in timeline.failed


async def test_a_restarted_lane_reuses_every_cached_window(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    s = settings(tmp_path, live_transcribe_concurrency=2)
    first = FakeTranscriber()
    run = Run(paths, s, first, monkeypatch)
    run.store(chunks, shuffled(len(chunks)))
    await run.stop()
    assert first.calls

    again = FakeTranscriber()
    rerun = Run(paths, s, again, monkeypatch)
    await rerun.stop()
    assert again.calls == []
    assert rerun.timeline.segments == run.timeline.segments
    assert rerun.chunk_events() == run.chunk_events()


async def test_a_lane_restarted_mid_recording_pays_only_for_new_windows(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    s = settings(tmp_path)
    first = FakeTranscriber()
    run = Run(paths, s, first, monkeypatch)
    run.store(chunks, range(len(chunks) * 2 // 3))
    await run.until(lambda: len(run.chunk_events()) >= 2)
    await run.until(lambda: len(first.finished) == len(run.chunk_events()))  # nothing in flight
    run.task.cancel()  # backend restart
    await asyncio.gather(run.task, return_exceptions=True)
    done = {c["sha1"] for c in first.calls}

    reference = FakeTranscriber()
    ref_paths = SessionPaths(tmp_path / "ref").ensure()
    ref = Run(ref_paths, s, reference, monkeypatch)
    ref.store(chunks, range(len(chunks)))
    await ref.stop()

    again = FakeTranscriber()
    rerun = Run(paths, s, again, monkeypatch)
    rerun.store(chunks, range(len(chunks)))
    await rerun.stop()
    # The same cuts as an uninterrupted lane, and no window paid for twice.
    assert rerun.windows() == ref.windows()
    assert not done & {c["sha1"] for c in again.calls}
    assert len(first.calls) + len(again.calls) == len(reference.calls)


async def test_a_damaged_cached_window_is_regenerated(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    s = settings(tmp_path)
    run = Run(paths, s, FakeTranscriber(), monkeypatch)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    files = sorted(
        paths.live_transcript_windows_dir.iterdir(), key=lambda p: int(p.name.split("_")[0])
    )
    files[-1].write_text("{not json")

    again = FakeTranscriber()
    rerun = Run(paths, s, again, monkeypatch)
    await rerun.stop()
    assert len(again.calls) == 1  # only the damaged (last) window; earlier prompts still match
    assert len(rerun.windows()) == len(run.windows())


async def test_a_changed_prompt_invalidates_the_cached_window(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    def by_audio(changed: str = ""):
        # Like a real model: the same audio gives the same text (except the ``changed`` audio).
        # Long texts, so each prompt is taken from the previous window alone.
        def text(_n: int, sha1: str) -> str:
            tag = "new" if sha1 == changed else ""
            return " ".join(f"{tag}{sha1[:8]}w{k}" for k in range(60))

        return text

    chunks = split(webm, 2000)
    s = settings(tmp_path)
    first = FakeTranscriber(text=by_audio())
    run = Run(paths, s, first, monkeypatch)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    window0 = min(
        paths.live_transcript_windows_dir.iterdir(), key=lambda p: int(p.name.split("_")[0])
    )
    window0.unlink()  # window 0 is transcribed again, and this time its text differs

    again = FakeTranscriber(text=by_audio(changed=first.calls[0]["sha1"]))
    await Run(paths, s, again, monkeypatch).stop()
    # Window 0, and window 1 whose prompt was window 0's text; window 2's prompt (window 1's
    # text, whose audio is unchanged) still matches.
    assert [c["sha1"] for c in again.calls] == [c["sha1"] for c in first.calls[:2]]


async def test_segments_past_the_window_end_stay_inside_it(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    # Whisper can return trailing text ("Thank you.") timed at or after the end of the audio;
    # transcribe_chunk clamps it to the duration, and offset + duration may exceed the window
    # end by a float ulp. Committing must not then fail as out of order.
    fake = FakeTranscriber()
    original = fake.create

    async def create(**kwargs):
        result = await original(**kwargs)
        d = result["segments"][0]["end"]
        result["segments"].append({"start": d + 0.02, "end": d + 0.5, "text": "Thank you."})
        return result

    fake.client.audio.transcriptions.create = create
    run = Run(
        paths, settings(tmp_path, live_window_min_s=0.7, live_window_max_s=1.9), fake, monkeypatch
    )
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    starts = [seg.start for seg in run.timeline.segments]
    assert starts == sorted(starts)
    bounds = run.windows()
    for i, (a, b) in enumerate(bounds):
        in_window = run.timeline.segments[2 * i : 2 * i + 2]
        assert all(a - 1e-9 <= seg.start <= seg.end <= b + 1e-9 for seg in in_window)


async def test_the_language_is_the_first_spoken_windows_in_timeline_and_file(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    fake = FakeTranscriber()
    original = fake.create

    async def create(**kwargs):
        result = await original(**kwargs)
        if len(fake.calls) == 1:  # window 0: nothing recognised, language misdetected
            return {"language": "welsh", "segments": [{"start": 0, "end": 1, "text": " "}]}
        return result

    fake.client.audio.transcriptions.create = create
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    assert run.timeline.language == "english"
    assert read_model(paths.live_transcript, Transcript).language == "english"


async def test_at_most_the_configured_number_of_windows_are_transcribed_at_once(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    in_flight, peak = 0, 0

    async def gate(n: int) -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1

    fake = FakeTranscriber(gate)
    run = Run(paths, settings(tmp_path, live_transcribe_concurrency=2), fake, monkeypatch)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))  # a backlog: every window is ready almost at once
    await run.stop()
    assert peak == 2
    assert run.chunk_events() == list(range(len(run.windows())))


async def test_calls_are_recorded_under_the_transcribe_stage(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    fake = FakeTranscriber()
    run = Run(paths, settings(tmp_path), fake, monkeypatch, metered=True)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    records = usage.read_usage(paths.usage_log).records
    assert len(records) == len(fake.calls) > 0
    assert {r.stage for r in records} == {Stage.TRANSCRIBE}
    assert {r.model for r in records} == {"whisper-1"}


async def test_another_model_invalidates_the_cache_and_stale_windows_are_removed(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    run = Run(paths, settings(tmp_path), FakeTranscriber(), monkeypatch)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    names = {p.name for p in paths.live_transcript_windows_dir.iterdir()}
    stale = paths.live_transcript_windows_dir / "99_0_1000.json"  # e.g. from other cuts
    stale.write_text("{}")

    again = FakeTranscriber()
    s = settings(tmp_path, transcribe_model="gpt-4o-transcribe")
    await Run(paths, s, again, monkeypatch).stop()
    assert len(again.calls) == len(names)  # every window paid for again with the new model
    assert all(c["prompt"] is None for c in again.calls)  # this model gets no prompt
    assert {p.name for p in paths.live_transcript_windows_dir.iterdir()} == names


async def test_every_window_is_sent_the_language_and_a_changed_one_invalidates_the_cache(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    first = FakeTranscriber()
    run = Run(paths, settings(tmp_path, transcribe_language="en"), first, monkeypatch)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    assert len(first.calls) > 1
    # Not just the first: a misdetected window can't pass its language on through the prompt.
    assert all(c["language"] == "en" for c in first.calls)

    again = FakeTranscriber()
    await Run(paths, settings(tmp_path, transcribe_language="auto"), again, monkeypatch).stop()
    assert len(again.calls) == len(first.calls)
    assert all(c["language"] is None for c in again.calls)


async def test_windows_cached_before_the_language_setting_are_resent_in_english(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    chunks = split(webm, 2000)
    first = FakeTranscriber()
    run = Run(paths, settings(tmp_path, transcribe_language="auto"), first, monkeypatch)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    for path in paths.live_transcript_windows_dir.glob("*.json"):  # as written before the field
        data = json.loads(path.read_text())
        del data["requested_language"]
        path.write_text(json.dumps(data))

    again = FakeTranscriber()
    await Run(paths, settings(tmp_path, transcribe_language="en"), again, monkeypatch).stop()
    assert len(again.calls) == len(first.calls)
    assert all(c["language"] == "en" for c in again.calls)


async def test_at_stop_a_very_short_tail_joins_the_last_window(
    tmp_path: Path, paths: SessionPaths, monkeypatch
):
    s = settings(tmp_path)
    lane = AudioLane(StageContext("s1", SourceKind.BROWSER_LIVE, paths, s), LiveTimeline())
    speech = (8000 * np.sin(np.arange(RATE * 4) * 0.2)).astype("<i2")  # no pause anywhere
    paths.live_pcm.parent.mkdir(parents=True, exist_ok=True)
    speech.tofile(paths.live_pcm)
    tail = int(2.5 * RATE) + RATE // 2  # 0.5 s past the maximum window
    assert await lane._next_cut(0, tail, final=False) is None  # can't confirm a cut yet
    assert await lane._next_cut(0, tail, final=True) == tail  # one window, not 2.5 s + 0.5 s
    longer = int(2.5 * RATE) + int(1.5 * RATE)
    assert await lane._next_cut(0, longer, final=False) == int(2.5 * RATE)
    assert await lane._next_cut(0, longer, final=True) == int(2.5 * RATE)


async def test_a_cached_window_just_past_its_bounds_still_commits(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    fake = FakeTranscriber()
    original = fake.create

    async def create(**kwargs):  # two segments, so each window has one at its start
        result = await original(**kwargs)
        d = result["segments"][0]["end"]
        result["segments"] = [
            {"start": 0.0, "end": d / 2, "text": "first half"},
            {"start": d / 2, "end": d, "text": "second half"},
        ]
        return result

    fake.client.audio.transcriptions.create = create
    chunks = split(webm, 2000)
    s = settings(tmp_path)
    run = Run(paths, s, fake, monkeypatch)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    # Each cached window's segment moved a hair past its end (within the reuse tolerance, same
    # text), as an older run without clamping could have written it.
    for path in paths.live_transcript_windows_dir.iterdir():
        window = read_model(path, audio_lane.CachedWindow)
        last = window.segments[-1]
        nudged = last.model_copy(update={"start": window.end + 1e-7, "end": window.end + 1e-7})
        write_model(path, window.model_copy(update={"segments": [*window.segments[:-1], nudged]}))

    again = FakeTranscriber()
    rerun = Run(paths, s, again, monkeypatch)
    await rerun.stop()
    assert again.calls == []
    starts = [seg.start for seg in rerun.timeline.segments]
    assert starts == sorted(starts)


# --- Only speech is sent (the stand-in detector in conftest hears the tones as speech) -----------

# Speech, then 5.9 s of quiet, then speech: the windows in between have no speech.
GAP_WORDS = [(0.2, 0.9), (1.4, 2.3), (8.2, 8.8), (9.3, 10.4), (10.9, 11.6)]


@pytest.fixture(scope="module")
def gap_webm() -> bytes:
    return speech_webm(GAP_WORDS)


def cached_windows(paths: SessionPaths) -> list[audio_lane.CachedWindow]:
    windows = (
        read_model(p, audio_lane.CachedWindow) for p in paths.live_transcript_windows_dir.iterdir()
    )
    return sorted(windows, key=lambda w: w.index)


def has_speech(window: audio_lane.CachedWindow) -> bool:
    return any(a < window.end and window.start < b for a, b in GAP_WORDS)


async def test_a_window_without_speech_is_not_sent_and_the_transcript_still_moves_on(
    tmp_path: Path, paths: SessionPaths, gap_webm: bytes, monkeypatch
):
    fake = FakeTranscriber()
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    chunks = split(gap_webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.until(lambda: run.timeline.transcribed_to >= 7.0)  # past the quiet, while recording
    assert not run.timeline.ending
    await run.stop()

    windows = cached_windows(paths)
    silent = [w for w in windows if not has_speech(w)]
    assert len(silent) >= 2
    assert all(w.segments == [] and w.language is None for w in silent)
    assert len(fake.calls) == len(windows) - len(silent)
    assert run.chunk_events() == list(range(len(windows)))  # every window commits, in order
    assert run.timeline.transcribed_to == pytest.approx(len(live_pcm(paths)) / RATE)


async def test_after_a_window_without_text_the_next_is_sent_without_a_prompt(
    tmp_path: Path, paths: SessionPaths, gap_webm: bytes, monkeypatch
):
    fake = FakeTranscriber()
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    chunks = split(gap_webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()

    windows = cached_windows(paths)
    after_silence = 0
    for w in windows[1:]:
        if not has_speech(w):
            continue
        before = windows[w.index - 1]
        assert w.prompt_through == w.index - 1  # one window at a time: the previous one
        assert (w.prompt == "") == (before.segments == [])
        after_silence += before.segments == []
    assert after_silence == 1
    assert [c["prompt"] is None for c in fake.calls].count(True) == 2  # the first, and after


async def test_after_a_window_whisper_found_no_text_in_the_next_has_no_prompt(
    tmp_path: Path, paths: SessionPaths, webm: bytes, monkeypatch
):
    fake = FakeTranscriber(text=lambda n, _sha1: "" if n == 1 else f"call{n}")
    run = Run(paths, settings(tmp_path), fake, monkeypatch)
    chunks = split(webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()
    # Only the window right after the empty one goes without; then the tail of all text resumes.
    assert [c["prompt"] for c in fake.calls[:4]] == [None, "call0", None, "call0 call2"]


async def test_a_restarted_lane_reuses_windows_without_speech_too(
    tmp_path: Path, paths: SessionPaths, gap_webm: bytes, monkeypatch
):
    s = settings(tmp_path)
    run = Run(paths, s, FakeTranscriber(), monkeypatch)
    chunks = split(gap_webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()

    again = FakeTranscriber()
    rerun = Run(paths, s, again, monkeypatch)
    await rerun.stop()
    assert again.calls == []
    assert rerun.timeline.segments == run.timeline.segments
    assert rerun.chunk_events() == run.chunk_events()


async def test_only_a_windows_speech_is_sent_and_billed(
    tmp_path: Path, paths: SessionPaths, gap_webm: bytes, monkeypatch
):
    fake = FakeTranscriber()
    s = settings(tmp_path, live_window_min_s=4.0, live_window_max_s=9.0)
    run = Run(paths, s, fake, monkeypatch, metered=True)
    chunks = split(gap_webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()

    first = cached_windows(paths)[0]
    assert first.end > 7.0  # the window runs well into the quiet...
    # ...but only its speech (0.2-2.3 s, padded by 0.4 s and up to a 32 ms slice) was sent, and
    # the one line the fake returns for the whole of it is placed where that speech was.
    assert fake.calls[0]["seconds"] == pytest.approx(2.7, abs=0.07)
    [seg] = first.segments
    assert (seg.start, seg.end) == pytest.approx((0.0, 2.7), abs=0.07)
    records = usage.read_usage(paths.usage_log).records
    assert [r.audio_seconds for r in records] == [
        usage.billed_seconds(c["seconds"]) for c in fake.calls
    ]


async def test_each_speech_stretch_in_a_window_is_sent_on_its_own_and_placed_at_its_start(
    tmp_path: Path, paths: SessionPaths, gap_webm: bytes, monkeypatch
):
    fake = FakeTranscriber()
    s = settings(tmp_path, live_window_min_s=4.0, live_window_max_s=14.0)  # one 12 s window
    run = Run(paths, s, fake, monkeypatch)
    chunks = split(gap_webm, 2000)
    run.store(chunks, range(len(chunks)))
    await run.stop()

    [window] = cached_windows(paths)
    # 0.2-2.3 s and 8.2-11.6 s, more than 2 s apart: two calls, both with the window's prompt.
    assert [c["seconds"] for c in fake.calls] == [
        pytest.approx(2.7, abs=0.07),
        pytest.approx(12.0 - 7.8, abs=0.07),
    ]
    assert [c["prompt"] for c in fake.calls] == [None, None]
    assert [(seg.start, seg.end) for seg in window.segments] == [
        pytest.approx((0.0, 2.7), abs=0.07),
        pytest.approx((7.8, 12.0), abs=0.07),
    ]
