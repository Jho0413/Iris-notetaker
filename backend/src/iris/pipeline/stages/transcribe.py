import asyncio
import hashlib
import logging
from collections import Counter
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from iris import speech
from iris.llm.client import get_openai
from iris.llm.errors import gather_units
from iris.llm.transcription import ChunkTranscript, returns_segments, transcribe_stretch_at
from iris.media.audio import fits_transcription_upload, quiet_profile, read_wav
from iris.models import EventType, Transcript, TranscriptSegment
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, write_model

log = logging.getLogger(__name__)

TIMESTAMPED_TARGET_CHUNK_S = 300.0
TIMESTAMPED_MAX_CHUNK_S = 600.0
TEXT_ONLY_TARGET_CHUNK_S = 15.0
TEXT_ONLY_MAX_CHUNK_S = 30.0
PAUSE_SEARCH_EITHER_SIDE_S = 30.0
CONCURRENCY = 8


def plan_chunks(
    samples: np.ndarray,
    rate: int,
    target_s: float = TIMESTAMPED_TARGET_CHUNK_S,
    max_s: float = TIMESTAMPED_MAX_CHUNK_S,
    search_s: float = PAUSE_SEARCH_EITHER_SIDE_S,
    window_s: float = 0.05,
    pause_s: float = 0.3,
) -> list[tuple[int, int]]:
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
        if cut is None:
            cut = min(start + target, hi)
        chunks.append((start, cut))
        start = cut
    chunks.append((start, n))
    return chunks


def chunk_lengths_for(model: str) -> tuple[float, float]:
    if returns_segments(model):
        return TIMESTAMPED_TARGET_CHUNK_S, TIMESTAMPED_MAX_CHUNK_S
    return TEXT_ONLY_TARGET_CHUNK_S, TEXT_ONLY_MAX_CHUNK_S


class CachedChunk(BaseModel):
    index: int
    start: float
    end: float
    audio_sha1: str
    model: str
    requested_language: str | None = None
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
    search_s: float = PAUSE_SEARCH_EITHER_SIDE_S,
    concurrency: int = CONCURRENCY,
) -> None:
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
    if not fits_transcription_upload(max_s * rate):
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
        detecting_one_chunk_at_a_time = asyncio.Semaphore(1)

        async def stretch(i: int, a: int, b: int) -> ChunkTranscript:
            async with sem:
                return await transcribe_stretch_at(
                    client,
                    model,
                    samples[a:b],
                    rate,
                    a,
                    language=requested,
                    retry_delays_s=ctx.settings.api_retry_delays_s,
                    what=f"session {ctx.session_id}: transcription chunk {i} at {a / rate:.1f}s",
                )

        async def one(i: int, s: int, e: int, digest: str) -> None:
            async with detecting_one_chunk_at_a_time:
                spoken = await asyncio.to_thread(speech.find_speech, samples[s:e], rate)
            outcomes = await gather_units(stretch(i, s + a, s + b) for a, b in spoken)
            if failed := next((o for o in outcomes if isinstance(o, Exception)), None):
                raise failed
            transcripts = [o for o in outcomes if isinstance(o, ChunkTranscript)]
            chunk = CachedChunk(
                index=i,
                start=s / rate,
                end=e / rate,
                audio_sha1=digest,
                model=model,
                requested_language=requested,
                language=next((r.language for r in transcripts if r.language), None),
                segments=[seg for r in transcripts for seg in r.segments],
            )
            write_model(cache_path(i, s, e), chunk)
            finish(chunk)

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
