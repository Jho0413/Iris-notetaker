import threading
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from iris.live.audio import LiveDecoder
from iris.media.audio import (
    TRANSCRIPTION_UPLOAD_LIMIT_BYTES,
    WAV_HEADER_ALLOWANCE_BYTES,
    encode_wav,
    fits_transcription_upload,
    open_mono_wav,
    quiet_profile,
    read_wav,
)
from iris.media.webm import (
    CLUSTER_ID,
    MAX_HEADER_BYTES,
    ChunkStream,
    PlacedWav,
    StoredChunks,
    find_cluster,
    stream_header,
)
from iris.sources.browser_live import store_audio_chunk
from iris.store.layout import SessionPaths, transcript_cache_name
from tests.media import SPEECH_RATE
from tests.waiting import decoder_waiting_for

HEADER = b"stream-header"


def stored(tmp_path: Path, parts: dict[int, bytes]) -> StoredChunks:
    chunks = []
    for seq, data in sorted(parts.items()):
        path = tmp_path / f"{seq}.webm"
        path.write_bytes(data)
        chunks.append((seq, path))
    return StoredChunks(chunks)


def read_all(stream: ChunkStream) -> bytes:
    with stream:
        return stream.read()


def test_contiguous_stored_chunks_read_as_one_stream_until_the_end(tmp_path: Path):
    feed = stored(tmp_path, {0: b"aa", 1: b"bbb", 2: b"c"})
    stream = ChunkStream(feed, b"", 0, tmp_path / "0.webm", 0)
    assert read_all(stream) == b"aabbbc"
    assert (stream.seq, stream.ended) == (2, "end")


def test_a_stored_stream_stops_at_a_gap(tmp_path: Path):
    feed = stored(tmp_path, {0: b"aa", 1: b"bb", 3: b"dd"})
    stream = ChunkStream(feed, b"", 0, tmp_path / "0.webm", 0)
    assert read_all(stream) == b"aabb"
    assert (stream.seq, stream.ended) == (1, "gap")


def test_after_a_gap_the_stream_restarts_at_the_next_cluster_with_the_header(tmp_path: Path):
    feed = stored(tmp_path, {0: HEADER + CLUSTER_ID + b"one", 2: b"junk" + CLUSTER_ID + b"two"})
    header = stream_header([tmp_path / "0.webm"])
    assert header == HEADER
    found = find_cluster([tmp_path / "2.webm"])
    assert found == (0, len(b"junk"))
    stream = ChunkStream(feed, header, 2, tmp_path / "2.webm", found[1])
    assert read_all(stream) == HEADER + CLUSTER_ID + b"two"
    assert stream.ended == "end"


def test_a_missing_first_chunk_is_a_gap_and_no_chunks_is_the_end(tmp_path: Path):
    assert stored(tmp_path, {1: b"b"}).next_chunk(0) == "gap"
    assert StoredChunks([]).next_chunk(0) == "end"


def test_the_stream_reads_the_first_chunk_from_its_offset(tmp_path: Path):
    feed = stored(tmp_path, {4: b"skipkeep", 5: b"more"})
    stream = ChunkStream(feed, b"", 4, tmp_path / "4.webm", len(b"skip"))
    assert read_all(stream) == b"keepmore"


def waiting_decoder(paths: SessionPaths) -> LiveDecoder:
    return LiveDecoder(paths, SPEECH_RATE, gap_wait_s=30.0, notify=lambda: None)


def read_in_thread(stream: ChunkStream) -> tuple[threading.Thread, list[bytes]]:
    out: list[bytes] = []
    thread = threading.Thread(target=lambda: out.append(read_all(stream)))
    thread.start()
    return thread, out


def test_a_waiting_feed_hands_on_a_chunk_that_arrives_later(paths: SessionPaths, monkeypatch):
    waiting = decoder_waiting_for(monkeypatch, 1)
    decoder = waiting_decoder(paths)
    store_audio_chunk(paths, 0, b"first")
    stream = ChunkStream(decoder, b"", 0, paths.live_audio_chunk(0), 0)
    thread, out = read_in_thread(stream)
    assert waiting.wait(5), "the feed never waited for chunk 1"
    store_audio_chunk(paths, 1, b"second")
    decoder.poke()
    decoder.end()
    thread.join(5)
    assert out == [b"firstsecond"]
    assert (stream.seq, stream.ended) == (1, "end")


def test_a_waiting_feed_stops_when_cancelled(paths: SessionPaths, monkeypatch):
    waiting = decoder_waiting_for(monkeypatch, 1)
    decoder = waiting_decoder(paths)
    store_audio_chunk(paths, 0, b"first")
    stream = ChunkStream(decoder, b"", 0, paths.live_audio_chunk(0), 0)
    thread, out = read_in_thread(stream)
    assert waiting.wait(5), "the feed never waited for chunk 1"
    decoder.cancel()
    thread.join(5)
    assert out == [b"first"]
    assert stream.ended == "stop"


@pytest.mark.parametrize("cut", range(len(CLUSTER_ID) + 1))
def test_a_cluster_is_found_across_a_file_boundary(tmp_path: Path, cut: int):
    stream = HEADER + CLUSTER_ID + b"cluster"
    at = len(HEADER) + cut
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(stream[:at])
    b.write_bytes(stream[at:])
    found = find_cluster([a, b])
    assert found is not None
    index, offset = found
    assert (len(a.read_bytes()) if index == 1 else 0) + offset == len(HEADER)
    assert find_cluster([a] if cut < len(CLUSTER_ID) else [b]) is None


@pytest.mark.parametrize("partial_id", range(len(CLUSTER_ID)))
def test_a_header_only_first_chunk_is_the_whole_header(tmp_path: Path, partial_id: int):
    first = tmp_path / "0.webm"
    first.write_bytes(HEADER + CLUSTER_ID[:partial_id])
    assert stream_header([first]) == HEADER


def test_a_header_with_no_cluster_in_reach_is_unknown(tmp_path: Path):
    first = tmp_path / "0.webm"
    first.write_bytes(b"x" * (MAX_HEADER_BYTES + 1))
    assert stream_header([first]) is None


def mono_frame(samples: np.ndarray, at_s: float, rate: int = SPEECH_RATE) -> av.AudioFrame:
    frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = rate
    frame.pts = round(at_s * rate)
    frame.time_base = Fraction(1, rate)
    return frame


def placed(frames: list[av.AudioFrame]) -> np.ndarray:
    written: list[bytes] = []
    wav = PlacedWav(written.append, SPEECH_RATE)
    for frame in frames:
        wav.write(frame)
    wav.flush()
    pcm = np.frombuffer(b"".join(written), "<i2")
    assert len(pcm) == wav.samples
    return pcm


def test_sound_after_a_jump_in_time_is_placed_at_its_timestamp():
    tone = np.full(1600, 1000, "<i2")
    pcm = placed([mono_frame(tone, 0.0), mono_frame(tone, 1.0)])
    assert len(pcm) == SPEECH_RATE + 1600
    assert not pcm[1600:SPEECH_RATE].any()
    assert (pcm[SPEECH_RATE:] == 1000).all()


def test_a_jump_within_codec_jitter_is_not_filled():
    tone = np.full(1600, 1000, "<i2")
    pcm = placed([mono_frame(tone, 0.0), mono_frame(tone, 0.1 + 0.04)])
    assert len(pcm) == 3200 and (pcm == 1000).all()


def test_sound_that_starts_late_is_led_in_with_silence():
    pcm = placed([mono_frame(np.full(800, 500, "<i2"), 0.5)])
    assert len(pcm) == SPEECH_RATE // 2 + 800
    assert not pcm[: SPEECH_RATE // 2].any()


def test_the_upload_limit_holds_exactly_up_to_its_last_sample():
    last = (TRANSCRIPTION_UPLOAD_LIMIT_BYTES - WAV_HEADER_ALLOWANCE_BYTES) // 2
    assert fits_transcription_upload(last)
    assert not fits_transcription_upload(last + 1)
    assert fits_transcription_upload(600.0 * 16_000)


def test_a_mono_wav_round_trips_its_samples_and_rate(tmp_path: Path):
    samples = np.array([0, 1, -1, 32767, -32768], "<i2")
    path = tmp_path / "a.wav"
    path.write_bytes(encode_wav(samples, 22_050))
    read, rate = read_wav(path)
    assert rate == 22_050 and np.array_equal(read, samples)
    with open_mono_wav(str(tmp_path / "b.wav"), 8_000) as out:
        out.writeframes(samples.tobytes())
    assert (tmp_path / "b.wav").read_bytes() == encode_wav(samples, 8_000)


def test_a_wav_that_is_not_16_bit_mono_is_refused(tmp_path: Path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SPEECH_RATE)
        out.writeframes(bytes(8))
    with pytest.raises(ValueError, match="expected 16-bit mono"):
        read_wav(path)


def test_the_quietest_point_is_the_centre_of_the_pause_nearest_the_target():
    loud = np.full(SPEECH_RATE, 8000, "<i2")
    pause = np.zeros(SPEECH_RATE // 2, "<i2")
    samples = np.concatenate([loud, pause, loud, pause, loud])
    profile = quiet_profile(samples, SPEECH_RATE)
    first, second = 1.25 * SPEECH_RATE, 2.75 * SPEECH_RATE
    assert abs(profile.quietest(0, len(samples), target=0) - first) < SPEECH_RATE * 0.15
    assert abs(profile.quietest(0, len(samples), target=len(samples)) - second) < SPEECH_RATE * 0.15
    assert quiet_profile(samples[:100], SPEECH_RATE).quietest(0, 100, target=50) is None


@pytest.mark.parametrize(
    ("index", "start", "end", "rate", "name"),
    [
        (0, 0, 16_000, 16_000, "0_0_1000.json"),
        (3, 24_000, 40_008, 16_000, "3_1500_2500.json"),
        (7, 1, 44_101, 44_100, "7_0_1000.json"),
    ],
)
def test_a_transcript_cache_name_holds_its_index_and_bounds_in_ms(
    paths: SessionPaths, index: int, start: int, end: int, rate: int, name: str
):
    assert transcript_cache_name(index, start, end, rate) == name
    i, start_ms, end_ms = (int(part) for part in Path(name).stem.split("_"))
    assert (i, start_ms, end_ms) == (index, round(start * 1000 / rate), round(end * 1000 / rate))
    assert paths.transcript_chunk(index, start, end, rate) == paths.transcript_chunks_dir / name
    assert paths.live_transcript_window(index, start, end, rate) == (
        paths.live_transcript_windows_dir / name
    )
