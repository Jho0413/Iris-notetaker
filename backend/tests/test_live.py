"""Browser live capture: storing chunks, finalizing them, and ending a session through the API.

Recordings are synthetic: a WebM/Opus stream encoded with PyAV and cut into byte ranges like
MediaRecorder timeslice chunks, and solid-colour JPEG frames.
"""

import io
import json
import random
import time
import wave
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.models import FrameIndex, SourceKind
from iris.pipeline.context import StageContext
from iris.sources.browser_live import (
    CLUSTER_ID,
    BrowserLiveSource,
    ChunkConflict,
    audio_chunks,
    contiguous_runs,
    finalize_audio,
    finalize_frames,
    find_cluster,
    fit,
    missing_audio_chunks,
    store_audio_chunk,
    store_frame,
)
from iris.store.layout import SessionPaths, read_model
from tests.test_api import wait_for_status

RATE = 48_000
SECONDS = 6
# Tone only in even seconds, so where a second's audio lands in audio.wav is checkable.
TONE_SECONDS = {0, 2, 4}
COLOURS = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 0, 255), (255, 255, 0)]


def make_webm(seconds: int = SECONDS) -> bytes:
    """A mono Opus WebM: a 440 Hz tone during ``TONE_SECONDS``, silence otherwise.

    Clusters are short, as in MediaRecorder output (FFmpeg's default is a few seconds).
    """
    buf = io.BytesIO()
    n = 960  # 20 ms Opus frames
    with av.open(buf, "w", format="webm", options={"cluster_time_limit": "500"}) as out:
        stream = out.add_stream("libopus", rate=RATE)
        stream.layout = "mono"
        for k in range(seconds * RATE // n):
            t = (np.arange(n) + k * n) / RATE
            loud = int(k * n / RATE) in TONE_SECONDS
            s = (0.3 * loud * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(s, format="flt", layout="mono")
            frame.sample_rate = RATE
            frame.pts = k * n
            frame.time_base = Fraction(1, RATE)
            out.mux(stream.encode(frame))
        out.mux(stream.encode())
    return buf.getvalue()


def split(data: bytes, size: int = 4000) -> list[bytes]:
    """Byte ranges like MediaRecorder timeslice chunks: only the first has the header."""
    return [data[i : i + size] for i in range(0, len(data), size)]


def jpeg(colour: tuple[int, int, int], size: tuple[int, int] = (320, 240)) -> bytes:
    img = np.empty((size[1], size[0], 3), np.uint8)
    img[:] = colour
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


@pytest.fixture(scope="module")
def webm() -> bytes:
    return make_webm()


@pytest.fixture
def paths(tmp_path: Path) -> SessionPaths:
    return SessionPaths(tmp_path / "s1").ensure()


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def loud(samples: np.ndarray, second: int, rate: int = 16_000) -> bool:
    """Whether the middle of ``second`` has the tone (edges skipped for codec smearing)."""
    window = samples[second * rate + rate // 5 : (second + 1) * rate - rate // 5]
    return bool(np.abs(window).max() > 3000)


# --- Storing chunks ---------------------------------------------------------------------------


def test_chunks_out_of_order_and_repeated_are_stored_once(paths: SessionPaths):
    assert store_audio_chunk(paths, 2, b"c")
    assert store_audio_chunk(paths, 0, b"a")
    assert not store_audio_chunk(paths, 2, b"c")  # a retry of the same chunk
    assert [seq for seq, _ in audio_chunks(paths)] == [0, 2]
    assert missing_audio_chunks(paths, 4) == [1, 3]
    assert missing_audio_chunks(paths, 0) == []
    assert not list(paths.live_audio_dir.glob("*.tmp"))


def test_conflicting_repeat_is_rejected_not_overwritten(paths: SessionPaths):
    store_audio_chunk(paths, 0, b"first")
    with pytest.raises(ChunkConflict):
        store_audio_chunk(paths, 0, b"other")
    assert (paths.live_audio_dir / "000000.webm").read_bytes() == b"first"
    store_frame(paths, 1.5, b"x")
    assert not store_frame(paths, 1.5, b"x")
    with pytest.raises(ChunkConflict):
        store_frame(paths, 1.5, b"y")


# --- Audio ------------------------------------------------------------------------------------


def write_chunks(paths: SessionPaths, chunks: list[bytes], skip: set[int] = frozenset()) -> None:
    order = list(range(len(chunks)))
    random.Random(0).shuffle(order)
    for seq in order:
        if seq not in skip:
            store_audio_chunk(paths, seq, chunks[seq])


def test_audio_from_shuffled_chunks_matches_the_recording(paths: SessionPaths, webm: bytes):
    write_chunks(paths, split(webm))
    duration = finalize_audio(audio_chunks(paths), paths.audio, 16_000)
    samples = read_wav(paths.audio)
    assert duration == pytest.approx(SECONDS, abs=0.1)
    assert [loud(samples, s) for s in range(SECONDS)] == [s in TONE_SECONDS for s in range(SECONDS)]
    assert not paths.root.joinpath("audio.wav.tmp").exists()


@pytest.mark.parametrize("lost", [{5}, {12}, {12, 13, 20}])
def test_missing_chunks_become_silence_and_keep_later_audio_in_place(
    tmp_path: Path, webm: bytes, lost: set[int]
):
    chunks = split(webm, 2000)
    full_paths = SessionPaths(tmp_path / "full").ensure()
    write_chunks(full_paths, chunks)
    finalize_audio(audio_chunks(full_paths), full_paths.audio, 16_000)
    full = read_wav(full_paths.audio)

    lossy_paths = SessionPaths(tmp_path / "lossy").ensure()
    write_chunks(lossy_paths, chunks, skip=lost)
    duration = finalize_audio(audio_chunks(lossy_paths), lossy_paths.audio, 16_000)
    lossy = read_wav(lossy_paths.audio)

    # Same length, and audio is lost only around each missing chunk: elsewhere the loudness
    # envelope matches the full recording (nothing is pulled earlier by the missing bytes).
    assert duration == pytest.approx(SECONDS, abs=0.1)
    differs = np.abs(envelope(full) - envelope(lossy)) > 3000
    assert np.count_nonzero(differs) * 0.01 < 1.2 * len(lost)  # the chunk, rest of its cluster
    assert loud(lossy, 0) and loud(lossy, 4) and not loud(lossy, 5)
    # The tone starting at 4 s, after the gaps, is within a few milliseconds of where it was.
    assert abs(onset(lossy, 3.8) - onset(full, 3.8)) <= 160


def envelope(samples: np.ndarray, window: int = 160) -> np.ndarray:
    """Peak level per 10 ms."""
    n = len(samples) // window * window
    return np.abs(samples[:n].astype(np.int32)).reshape(-1, window).max(axis=1)


def onset(samples: np.ndarray, after_s: float) -> int:
    """Index of the first loud sample after ``after_s`` seconds."""
    start = round(after_s * 16_000)
    return start + int(np.argmax(np.abs(samples[start:]) > 3000))


def test_runs_split_at_missing_chunks(tmp_path: Path):
    p = [tmp_path / str(i) for i in range(6)]
    chunks = [(0, p[0]), (1, p[1]), (3, p[3]), (4, p[4]), (5, p[5])]
    assert contiguous_runs(chunks) == [[p[0], p[1]], [p[3], p[4], p[5]]]
    assert contiguous_runs([]) == []


@pytest.mark.parametrize("cut", range(len(CLUSTER_ID) + 1))
def test_cluster_found_across_file_boundaries(tmp_path: Path, cut: int):
    stream = b"header-bytes" + CLUSTER_ID + b"cluster"
    at = len(b"header-bytes") + cut  # cut the stream inside (or next to) the cluster ID
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(stream[:at])
    b.write_bytes(stream[at:])
    found = find_cluster([a, b])
    assert found is not None
    index, offset = found
    assert (len(a.read_bytes()) if index == 1 else 0) + offset == len(b"header-bytes")
    assert find_cluster([tmp_path / "a"] if cut < len(CLUSTER_ID) else [b]) is None


def test_unreadable_audio_raises(paths: SessionPaths):
    store_audio_chunk(paths, 0, b"\x1a\x45\xdf\xa3 but not really webm")
    with pytest.raises(ValueError, match="not a readable"):
        finalize_audio(audio_chunks(paths), paths.audio, 16_000)
    assert not paths.audio.exists()


async def test_ingest_requires_audio_and_its_first_chunk(tmp_path: Path, webm: bytes):
    paths = SessionPaths(tmp_path / "s1").ensure()
    ctx = StageContext("s1", SourceKind.BROWSER_LIVE, paths, Settings(data_dir=tmp_path))
    with pytest.raises(ValueError, match="no audio was recorded"):
        await BrowserLiveSource().ingest(ctx)
    store_audio_chunk(paths, 1, split(webm)[1])
    with pytest.raises(ValueError, match="first audio chunk"):
        await BrowserLiveSource().ingest(ctx)


# --- Frames -----------------------------------------------------------------------------------


def mean_bgr(path: Path) -> np.ndarray:
    return cv2.imread(str(path)).reshape(-1, 3).mean(axis=0)


def test_frames_sampled_on_the_grid_by_timestamp(paths: SessionPaths):
    # Uneven browser timing: two frames in second 1, none in second 3, out of posting order.
    captured = {0.0: 0, 1.02: 1, 1.6: 1, 2.05: 2, 4.3: 4, 5.0: 5}
    for t in reversed(list(captured)):
        store_frame(paths, t, jpeg(COLOURS[captured[t]]))
    index = finalize_frames(paths, fps=1.0, max_width=1280)
    assert [f.t for f in index.frames] == [0.0, 1.02, 2.05, 4.3, 5.0]
    assert [f.path for f in index.frames][:2] == ["frames/000000000.jpg", "frames/000001020.jpg"]
    for ref in index.frames:
        expected = COLOURS[captured[ref.t]]
        assert mean_bgr(paths.root / ref.path) == pytest.approx(np.array(expected), abs=12)
    assert read_model(paths.frame_index, FrameIndex) == index
    assert (index.width, index.height) == (320, 240)


def test_frames_downscaled_and_letterboxed_to_one_size(paths: SessionPaths):
    store_frame(paths, 0.0, jpeg(COLOURS[0], (640, 480)))
    store_frame(paths, 1.0, jpeg(COLOURS[1], (640, 240)))  # the shared window got wider
    store_frame(paths, 2.0, b"\xff\xd8\xff truncated")  # unreadable: skipped
    index = finalize_frames(paths, fps=1.0, max_width=320)
    assert (index.width, index.height) == (320, 240)
    assert [f.t for f in index.frames] == [0.0, 1.0]
    wide = cv2.imread(str(paths.root / index.frames[1].path))
    assert wide.shape == (240, 320, 3)
    assert wide[:50].mean() < 10 and wide[100:140].mean() > 50  # black bars above and below


def test_finalize_frames_is_repeatable_and_removes_stale_frames(paths: SessionPaths):
    stale = paths.frames_dir / "999999999.jpg"
    stale.write_bytes(b"old")
    store_frame(paths, 0.5, jpeg(COLOURS[0]))
    first = finalize_frames(paths, fps=1.0, max_width=1280)
    assert not stale.exists()
    assert finalize_frames(paths, fps=1.0, max_width=1280) == first
    assert len(list(paths.frames_dir.glob("*.jpg"))) == 1
    assert (paths.live_frames_dir / "000000500.jpg").exists()  # captured data is kept


def test_no_frames_gives_an_empty_index(paths: SessionPaths):
    assert finalize_frames(paths, fps=1.0, max_width=1280) == FrameIndex(
        fps=1.0, width=0, height=0, frames=[]
    )


def test_fit_keeps_aspect():
    tall = np.full((200, 50, 3), 255, np.uint8)
    out = fit(tall, (100, 100))
    assert out.shape == (100, 100, 3)
    assert out[:, :30].max() == 0 and out[:, 40:60].min() == 255


# --- API --------------------------------------------------------------------------------------


def start(client: TestClient) -> str:
    return client.post("/api/live", json={"title": "standup"}).json()["id"]


def post_audio(client: TestClient, sid: str, seq: int, data: bytes) -> int:
    return client.post(f"/api/live/{sid}/audio?seq={seq}", content=data).status_code


def post_frame(client: TestClient, sid: str, t: float, data: bytes) -> int:
    return client.post(f"/api/live/{sid}/frame?t={t}", content=data).status_code


def test_live_input_is_validated(client: TestClient):
    sid = start(client)
    assert post_audio(client, sid, 0, b"not webm") == 415
    assert post_audio(client, sid, -1, b"x") == 422
    assert post_audio(client, sid, 1, b"") == 400
    assert post_frame(client, sid, 1.0, b"not a jpeg") == 415
    for t in ("nan", "inf", "-1", "86401"):
        assert post_frame(client, sid, t, jpeg(COLOURS[0])) == 422  # type: ignore[arg-type]
    big = b"\xff\xd8\xff" + bytes(16 * 1024 * 1024)
    assert post_frame(client, sid, 1.0, big) == 413
    assert post_audio(client, "nope", 0, b"x") == 404
    assert client.post("/api/live/nope/end").status_code == 404


def test_repeated_chunk_with_other_content_conflicts(client: TestClient, webm: bytes):
    sid = start(client)
    chunks = split(webm)
    assert post_audio(client, sid, 1, chunks[1]) == 204
    assert post_audio(client, sid, 1, chunks[1]) == 204  # retry: no-op
    assert post_audio(client, sid, 1, chunks[2]) == 409


def test_end_waits_for_missing_chunks_then_processes(client: TestClient, webm: bytes):
    sid = start(client)
    chunks = split(webm)
    for seq in range(1, len(chunks)):
        assert post_audio(client, sid, seq, chunks[seq]) == 204
    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(chunks)})
    assert r.status_code == 409
    assert r.json()["detail"]["missing_audio_chunks"] == [0]
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "recording"

    assert post_audio(client, sid, 0, chunks[0]) == 204  # the browser resends it
    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(chunks)})
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
    # Recording has ended: no more input, and ending again is refused.
    assert post_audio(client, sid, len(chunks), b"x") == 409
    assert client.post(f"/api/live/{sid}/end").status_code == 409
    wait_for_status(client, sid, {"done", "failed"})


def test_end_with_allowed_gaps(client: TestClient, webm: bytes, settings: Settings):
    sid = start(client)
    chunks = split(webm, 2000)
    for seq in range(len(chunks)):
        if seq != 3:
            post_audio(client, sid, seq, chunks[seq])
    body = {"audio_chunks": len(chunks), "allow_gaps": True}
    assert client.post(f"/api/live/{sid}/end", json=body).status_code == 200
    wait_for_status(client, sid, {"done", "failed"})
    audio = settings.sessions_dir / sid / "audio.wav"
    assert len(read_wav(audio)) / 16_000 == pytest.approx(SECONDS, abs=0.1)


def wait_until_idle(client: TestClient, sid: str, timeout: float = 10.0) -> None:
    runner = client.app.state.runner  # type: ignore[attr-defined]
    deadline = time.monotonic() + timeout
    while runner.is_running(sid):
        assert time.monotonic() < deadline, "pipeline did not finish"
        time.sleep(0.05)


def fake_openai(monkeypatch) -> None:
    """Fake every model call the pipeline makes (no network, no cost)."""
    from iris.pipeline.stages import render, select, transcribe, write
    from tests.test_write import section

    async def parse(**kwargs):
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
        else:
            payload = json.loads(kwargs["messages"][1]["content"])
            result = section([r["id"] for r in payload["records"] if r["kind"] == "visual"])
        message = SimpleNamespace(parsed=result, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    async def transcribe_audio(**kwargs):
        segments = [{"start": 0, "end": 2, "text": "Ship the prototype. Alex will test it."}]
        return {"language": "english", "segments": segments}

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)),
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe_audio)),
    )
    for module in (write, select, transcribe, render):
        monkeypatch.setattr(module, "get_openai", lambda _: fake)


def test_live_session_produces_notes_like_an_upload(
    client: TestClient, webm: bytes, settings: Settings, monkeypatch
):
    fake_openai(monkeypatch)
    sid = start(client)
    chunks = split(webm)
    order = list(range(len(chunks)))
    random.Random(1).shuffle(order)
    for seq in order + order[:3]:  # out of order, and some sent twice
        assert post_audio(client, sid, seq, chunks[seq]) == 204
    frame_times = [0.0, 1.01, 2.0, 3.02, 4.0, 5.0]
    for i in [3, 0, 5, 1, 4, 2, 0, 5]:
        assert post_frame(client, sid, frame_times[i], jpeg(COLOURS[i // 3 * 3])) == 204

    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(chunks)})
    assert r.status_code == 200
    session = wait_for_status(client, sid, {"done", "failed"})
    assert session["status"] == "done", session.get("error")

    root = settings.sessions_dir / sid
    samples = read_wav(root / "audio.wav")
    assert len(samples) / 16_000 == pytest.approx(SECONDS, abs=0.1)
    index = read_model(root / "frames.json", FrameIndex)
    assert [f.t for f in index.frames] == frame_times
    assert all((root / f.path).exists() for f in index.frames)
    notes = client.get(f"/api/sessions/{sid}/notes").json()
    assert "Ship the prototype" in notes["markdown"]
    assert notes["visuals"]  # two screens (red, then yellow) gave moments and figures
    assert len(list((root / "live" / "audio").glob("*.webm"))) == len(chunks)  # kept for retries

    # A retry re-ingests from the kept capture and gives the same result.
    assert client.post(f"/api/sessions/{sid}/run").status_code == 200
    wait_until_idle(client, sid)
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "done"
    assert read_model(root / "frames.json", FrameIndex) == index
