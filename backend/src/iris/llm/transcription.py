from collections.abc import Sequence
from typing import Any

import numpy as np
from pydantic import BaseModel

from iris.llm.errors import retry_temporary
from iris.media.audio import encode_wav
from iris.models import TranscriptSegment


class ChunkTranscript(BaseModel):
    language: str | None = None
    segments: list[TranscriptSegment]


class CachedChunk(BaseModel):
    index: int
    start: float
    end: float
    audio_sha1: str
    model: str
    requested_language: str | None = None
    language: str | None = None
    segments: list[TranscriptSegment]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def returns_segments(model: str) -> bool:
    return model.startswith("whisper")


def supports_prompt(model: str) -> bool:
    return returns_segments(model)


async def transcribe_chunk(
    client: Any,
    model: str,
    wav_bytes: bytes,
    duration_s: float,
    prompt: str | None = None,
    language: str | None = None,
) -> ChunkTranscript:
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
    language = _field(resp, "language")
    if language is None and (languages := _field(resp, "languages")):
        language = _field(languages[0], "code")
    raw_segments = _field(resp, "segments")
    if raw_segments is None:
        raw_segments = [{"start": 0.0, "end": duration_s, "text": _field(resp, "text") or ""}]

    segments = []
    for seg in raw_segments:
        text = (_field(seg, "text") or "").strip()
        if not text:
            continue
        start = min(max(float(_field(seg, "start", 0.0)), 0.0), duration_s)
        end = min(max(float(_field(seg, "end", start)), start), duration_s)
        segments.append(
            TranscriptSegment(start=start, end=end, text=text, speaker=_field(seg, "speaker"))
        )
    return ChunkTranscript(language=language, segments=segments)


async def transcribe_stretch_at(
    client: Any,
    model: str,
    samples: np.ndarray,
    rate: int,
    start: int,
    *,
    prompt: str | None = None,
    language: str | None,
    retry_delays_s: Sequence[float],
    what: str,
) -> ChunkTranscript:
    wav = encode_wav(samples, rate)
    result = await retry_temporary(
        lambda: transcribe_chunk(
            client, model, wav, len(samples) / rate, prompt, language=language
        ),
        retry_delays_s,
        what=what,
    )
    at = start / rate
    placed = [
        seg.model_copy(update={"start": seg.start + at, "end": seg.end + at})
        for seg in result.segments
    ]
    return ChunkTranscript(language=result.language, segments=placed)
