"""Transcribe: audio -> timestamped transcript.

Reads:  audio.wav (16-bit PCM mono)
Writes: transcript_chunks/<i>_<start_ms>_<end_ms>.json (one per chunk, as each finishes)
        transcript.json (Transcript)
Emits:  TRANSCRIPT_CHUNK per finished chunk, so the UI can show text as it arrives.

Steps:
  1. ``plan_chunks`` splits the audio into chunks, cutting at the quietest point near each target
     boundary so words are not cut in half. Chunk length depends on the model
     (``chunk_lengths_for``): ~5 min for whisper-1, which returns segment timestamps, and ~15 s
     for text-only models, where each chunk becomes one segment.
  2. Only each chunk's speech is sent (``iris.speech``): Whisper makes up text over silence, noise
     and clicks. Each speech stretch is sent on its own; a chunk without speech is not sent and
     has no segments.
  3. Stretches are transcribed concurrently, each retried after a temporary API error
     (``retry_temporary``). Each finished chunk is cached on disk, so a crash or a re-run never
     re-pays for it. A cached chunk is reused only if its boundaries, audio and
     model match. A chunk with a stretch that still fails fails the stage once the others have
     finished; a blocking error (``classify``) cancels them at once.
  4. Each segment's times are offset by its stretch's start, then everything is merged and sorted.

Model-specific request and response handling lives in ``transcribe_chunk`` and
``chunk_lengths_for``.
"""

import asyncio
import hashlib
import io
import logging
import wave
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from iris import speech
from iris.llm.client import get_openai
from iris.llm.errors import gather_units, retry_temporary
from iris.models import EventType, Transcript, TranscriptSegment
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, write_model

log = logging.getLogger(__name__)

# Chunk lengths. With segment timestamps (whisper-1), long chunks give the model more context.
# Models that return text only (gpt-transcribe, gpt-4o-*-transcribe) get one segment per chunk, so
# the chunk length *is* the timestamp resolution: keep those chunks short.
TIMESTAMPED_TARGET_CHUNK_S = 300.0  # ~5 min
TIMESTAMPED_MAX_CHUNK_S = 600.0  # 10 min of 16 kHz s16 mono is ~19 MB, under the 25 MB limit
TEXT_ONLY_TARGET_CHUNK_S = 15.0
TEXT_ONLY_MAX_CHUNK_S = 30.0
SEARCH_S = 30.0  # look this far either side of a target boundary for a pause (capped at target/2)
CONCURRENCY = 8
QUIET_RATIO = 2.0  # within 3 dB of the quietest point counts as equally quiet
API_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


# --- Chunk planning (pure numpy) ---------------------------------------------------------------


def _frame_power(samples: np.ndarray, frame: int) -> np.ndarray:
    """Mean square amplitude of each whole ``frame``-sample window, computed in blocks so a 90 min
    session never needs a full float copy of the audio."""
    n_frames = len(samples) // frame
    power = np.empty(n_frames, dtype=np.float64)
    block = max(1, 4_000_000 // frame)
    for i in range(0, n_frames, block):
        j = min(i + block, n_frames)
        x = samples[i * frame : j * frame].astype(np.float32).reshape(j - i, frame)
        power[i:j] = np.mean(np.square(x), axis=1)
    return power


@dataclass(frozen=True)
class QuietProfile:
    """How quiet the audio is around each point: the mean power of the ``pause_s`` stretch
    centred there, sampled every ``frame`` samples. Shared by upload chunking and live windows."""

    quiet: np.ndarray  # quiet[j]: mean power of frames j..j+k-1, centred on sample j*frame+offset
    frame: int
    offset: int

    def quietest(self, lo: int, hi: int, target: int) -> int | None:
        """The sample in ``[lo, hi]`` at the centre of the quietest stretch; among stretches within
        ``QUIET_RATIO`` of the quietest, the one closest to ``target``. None if no stretch is
        centred in the range (too little audio)."""
        j_lo = max(0, -(-(lo - self.offset) // self.frame))  # ceil division
        j_hi = min(len(self.quiet) - 1, (hi - self.offset) // self.frame)
        if j_lo > j_hi:
            return None
        window = self.quiet[j_lo : j_hi + 1]
        floor = window.min()
        candidates = np.flatnonzero(window <= floor * QUIET_RATIO + 1e-12) + j_lo
        centres = candidates * self.frame + self.offset
        return int(centres[np.argmin(np.abs(centres - target))])


def quiet_profile(
    samples: np.ndarray, rate: int, window_s: float = 0.05, pause_s: float = 0.3
) -> QuietProfile:
    """RMS energy over ``window_s`` frames, smoothed over ``pause_s`` (see ``QuietProfile``)."""
    frame = max(1, int(window_s * rate))
    k = max(1, round(pause_s / window_s))  # smoothing width, in frames
    power = _frame_power(samples, frame)
    if len(power) >= k:
        csum = np.concatenate(([0.0], np.cumsum(power)))
        quiet = (csum[k:] - csum[:-k]) / k
    else:
        quiet = np.empty(0)
    return QuietProfile(quiet, frame, k * frame // 2)


def plan_chunks(
    samples: np.ndarray,
    rate: int,
    target_s: float = TIMESTAMPED_TARGET_CHUNK_S,
    max_s: float = TIMESTAMPED_MAX_CHUNK_S,
    search_s: float = SEARCH_S,
    window_s: float = 0.05,
    pause_s: float = 0.3,
) -> list[tuple[int, int]]:
    """Split ``samples`` into contiguous ``(start, end)`` sample ranges covering the whole audio.

    Each cut lands near ``target_s`` after the previous one, at the quietest ``pause_s`` stretch
    (by RMS energy over ``window_s`` frames) within ``search_s`` either side. No chunk is longer
    than ``max_s``. Remaining audio that fits within that maximum is kept as the final chunk
    without searching for another cut. Among equally quiet candidates, the one closest to
    the target wins (within ``QUIET_RATIO`` of the quietest). ``search_s`` is capped at
    ``target_s / 2``.
    """
    if target_s <= 0 or max_s < target_s:
        raise ValueError("need 0 < target_s <= max_s")
    n = len(samples)
    if n == 0:
        return []

    target = int(target_s * rate)
    max_len = int(max_s * rate)
    search = int(min(search_s, target_s / 2) * rate)

    if n <= max_len:
        return [(0, n)]

    profile = quiet_profile(samples, rate, window_s, pause_s)
    chunks: list[tuple[int, int]] = []
    start = 0
    while n - start > max_len:
        lo = start + max(target - search, 1)
        hi = min(start + target + search, start + max_len, n - search)
        cut = profile.quietest(lo, hi, start + target)
        if cut is None:  # no energy data to search
            cut = min(start + target, hi)
        chunks.append((start, cut))
        start = cut
    chunks.append((start, n))
    return chunks


# --- Audio helpers -----------------------------------------------------------------------------


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM mono wav into int16 samples."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(
                f"{path}: expected 16-bit mono PCM, got {w.getsampwidth() * 8}-bit "
                f"{w.getnchannels()}-channel"
            )
        rate = w.getframerate()
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2"), rate


def encode_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2", copy=False).tobytes())
    return buf.getvalue()


# --- The API call (all model-specific handling is here) ----------------------------------------


class ChunkTranscript(BaseModel):
    language: str | None = None
    segments: list[TranscriptSegment]  # seconds from the chunk start


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from an SDK response object or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def returns_segments(model: str) -> bool:
    """Whether the model returns segment timestamps (``verbose_json``). Only whisper-1 does."""
    return model.startswith("whisper")


def chunk_lengths_for(model: str) -> tuple[float, float]:
    """``(target_s, max_s)`` chunk lengths suited to the model's timestamp support."""
    if returns_segments(model):
        return TIMESTAMPED_TARGET_CHUNK_S, TIMESTAMPED_MAX_CHUNK_S
    return TEXT_ONLY_TARGET_CHUNK_S, TEXT_ONLY_MAX_CHUNK_S


def supports_prompt(model: str) -> bool:
    """Whether ``transcribe_chunk`` passes a prompt to the model. Only whisper-1, which reads it as
    the text that came before the audio. The gpt-4o-* models read a prompt as instructions, and
    session text is never sent as instructions; diarize models take no prompt."""
    return returns_segments(model)


async def transcribe_chunk(
    client: Any,
    model: str,
    wav_bytes: bytes,
    duration_s: float,
    prompt: str | None = None,
    language: str | None = None,
) -> ChunkTranscript:
    """Transcribe one chunk. Timestamps in the result are relative to the chunk start.

    Request: ``whisper-1`` gets ``verbose_json`` with segment timestamps; ``*-diarize`` models get
    ``diarized_json`` (segments with per-request speaker labels ``A``, ``B``..., so per speech
    stretch); every other model (``gpt-transcribe``, ``gpt-4o-transcribe``,
    ``gpt-4o-mini-transcribe``...) gets ``json``.

    Response, parsed by what comes back rather than by model name: if it has ``segments`` they are
    used; otherwise its ``text`` becomes one segment spanning the whole chunk. A plain string
    (``response_format="text"``) is treated as text. Language comes from ``language``
    (verbose_json) or the first of ``languages`` (gpt-transcribe).

    ``prompt`` (the transcript just before this audio, for continuity across live windows) is
    sent only where ``supports_prompt``; uploads send none. ``language`` (ISO-639-1) is sent if
    given (``Settings.transcribe_language``); otherwise the model detects it.
    """
    if "diarize" in model:
        params: dict[str, Any] = {"response_format": "diarized_json", "chunking_strategy": "auto"}
    elif returns_segments(model):
        params = {"response_format": "verbose_json", "timestamp_granularities": ["segment"]}
    else:
        params = {"response_format": "json"}
    if prompt and supports_prompt(model):
        params["prompt"] = prompt
    if language:
        params["language"] = language

    resp = await client.audio.transcriptions.create(
        model=model, file=("chunk.wav", wav_bytes, "audio/wav"), **params
    )

    if isinstance(resp, str):
        resp = {"text": resp}
    language = _get(resp, "language")
    if language is None and (languages := _get(resp, "languages")):
        language = _get(languages[0], "code")
    raw_segments = _get(resp, "segments")
    if raw_segments is None:
        raw_segments = [{"start": 0.0, "end": duration_s, "text": _get(resp, "text") or ""}]

    segments = []
    for seg in raw_segments:
        text = (_get(seg, "text") or "").strip()
        if not text:
            continue
        start = min(max(float(_get(seg, "start", 0.0)), 0.0), duration_s)
        end = min(max(float(_get(seg, "end", start)), start), duration_s)
        segments.append(
            TranscriptSegment(start=start, end=end, text=text, speaker=_get(seg, "speaker"))
        )
    return ChunkTranscript(language=language, segments=segments)


# --- The stage ---------------------------------------------------------------------------------


class CachedChunk(BaseModel):
    """One finished chunk on disk, with session-absolute timestamps."""

    index: int
    start: float
    end: float
    audio_sha1: str
    model: str
    requested_language: str | None = None  # ``transcribe_language`` when sent (None: detected)
    language: str | None = None
    segments: list[TranscriptSegment]


class TranscribeError(RuntimeError):
    pass


def _fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


async def run(
    ctx: StageContext,
    *,
    target_s: float | None = None,
    max_s: float | None = None,
    search_s: float = SEARCH_S,
    concurrency: int = CONCURRENCY,
) -> None:
    """Chunk lengths come from the arguments, else ``chunk_lengths_for(model)``."""
    model = ctx.settings.transcribe_model
    requested = ctx.settings.transcribe_language
    default_target, default_max = chunk_lengths_for(model)
    if target_s is None:
        target_s = default_target
    if max_s is None:
        max_s = max(default_max, target_s)

    samples, rate = read_wav(ctx.paths.audio)
    if rate != ctx.settings.audio_sample_rate:
        log.warning("audio.wav is %d Hz, expected %d", rate, ctx.settings.audio_sample_rate)
    if max_s * rate * 2 + 1024 > API_MAX_UPLOAD_BYTES:
        raise ValueError(f"max chunk of {max_s}s at {rate} Hz exceeds the API's upload limit")

    chunks = plan_chunks(samples, rate, target_s, max_s, search_s)
    cache_dir = ctx.paths.transcript_chunks_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, CachedChunk] = {}

    def cache_path(i: int, s: int, e: int) -> Path:
        return cache_dir / f"{i}_{round(s * 1000 / rate)}_{round(e * 1000 / rate)}.json"

    def finish(chunk: CachedChunk) -> None:
        results[chunk.index] = chunk
        ctx.emit(
            EventType.TRANSCRIPT_CHUNK,
            {"chunk": chunk.index, "segments": [s.model_dump() for s in chunk.segments]},
        )

    todo: list[tuple[int, int, int, str]] = []
    for i, (s, e) in enumerate(chunks):
        digest = hashlib.sha1(samples[s:e]).hexdigest()
        path = cache_path(i, s, e)
        if path.exists():
            try:
                cached = read_model(path, CachedChunk)
            except ValueError:
                log.warning("ignoring unreadable chunk cache %s", path)
            else:
                if (
                    cached.audio_sha1 == digest
                    and cached.model == model
                    and cached.requested_language == requested
                ):
                    finish(cached)
                    continue
        todo.append((i, s, e, digest))

    if not todo:
        log.info("transcribe: all %d chunks cached", len(chunks))
    else:
        client = get_openai(ctx.settings)
        sem = asyncio.Semaphore(concurrency)
        # The detector doesn't run in parallel: one chunk at a time lets the first chunk's calls
        # start while later chunks are still being checked.
        detecting = asyncio.Semaphore(1)

        async def stretch(i: int, a: int, b: int) -> ChunkTranscript:
            """Transcribe the speech stretch ``samples[a:b]`` of chunk ``i`` (times from ``a``)."""
            async with sem:
                wav = encode_wav(samples[a:b], rate)
                result = await retry_temporary(
                    lambda: transcribe_chunk(
                        client, model, wav, (b - a) / rate, language=requested
                    ),
                    ctx.settings.api_retry_delays_s,
                    what=f"session {ctx.session_id}: transcription chunk {i} at {a / rate:.1f}s",
                )
            return result

        async def one(i: int, s: int, e: int, digest: str) -> None:
            async with detecting:
                spoken = await asyncio.to_thread(speech.find_speech, samples[s:e], rate)
            outcomes = await gather_units(stretch(i, s + a, s + b) for a, b in spoken)
            if failed := next((o for o in outcomes if isinstance(o, Exception)), None):
                raise failed
            segments = [
                seg.model_copy(
                    update={"start": seg.start + (s + a) / rate, "end": seg.end + (s + a) / rate}
                )
                for (a, _), result in zip(spoken, outcomes, strict=True)
                for seg in result.segments
            ]
            chunk = CachedChunk(
                index=i,
                start=s / rate,
                end=e / rate,
                audio_sha1=digest,
                model=model,
                requested_language=requested,
                language=next((r.language for r in outcomes if r.language), None),
                segments=segments,
            )
            write_model(cache_path(i, s, e), chunk)
            finish(chunk)

        # Let every chunk run to completion (no cancelling paid-for work), then report failures;
        # a blocking error (e.g. a rejected key) cancels the rest at once and is raised as is.
        outcomes = await gather_units(one(*t) for t in todo)
        failures = [(t, o) for t, o in zip(todo, outcomes, strict=True) if isinstance(o, Exception)]
        if failures:
            details = "; ".join(
                f"chunk {i} [{_fmt(s / rate)}-{_fmt(e / rate)}]: {type(exc).__name__}: {exc}"
                for (i, s, e, _), exc in failures[:3]
            ) + ("; ..." if len(failures) > 3 else "")
            raise TranscribeError(
                f"{len(failures)} of {len(chunks)} transcription chunks failed "
                f"(finished chunks are cached; re-run to retry the rest): {details}"
            ) from failures[0][1]

    segments = sorted(
        (seg for i in sorted(results) for seg in results[i].segments),
        key=lambda seg: (seg.start, seg.end),
    )
    languages = Counter(c.language for c in results.values() if c.language)
    language = languages.most_common(1)[0][0] if languages else None
    write_model(ctx.paths.transcript, Transcript(language=language, segments=segments))
