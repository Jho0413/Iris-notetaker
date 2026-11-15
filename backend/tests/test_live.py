import random
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.live import audio as audio_lane
from iris.live.replay import file_frames
from iris.llm.errors import UserFacingError
from iris.llm.usage import append_record
from iris.main import create_app
from iris.models import (
    LIVE_WITHOUT_AUDIO,
    UNREADABLE_LIVE_AUDIO,
    FrameIndex,
    UsageRecord,
)
from iris.pipeline.stages import render, select, transcribe, write
from iris.pipeline.stages.transcribe import read_wav
from iris.sources.browser_live import (
    CLUSTER_ID,
    BrowserLiveSource,
    CapturedFrame,
    ChunkConflict,
    audio_chunks,
    captured_frames,
    contiguous_runs,
    finalize_audio,
    finalize_frames,
    find_cluster,
    fit,
    missing_audio_chunks,
    split_shares,
    store_audio_chunk,
    store_frame,
)
from iris.store.layout import SessionPaths, read_model, session_paths
from tests.builders import stage_ctx
from tests.fakes import PipelineOpenAI, install_get_openai
from tests.live_api import post_audio, post_frame, start_recording, stop_recording
from tests.media import (
    COLOURS,
    TONE_SECONDS,
    WEBM_SECONDS,
    make_media,
    make_webm,
    mean_bgr,
    solid_jpeg,
    split,
)
from tests.waiting import wait_for_status, wait_until_idle


@pytest.fixture(scope="module")
def webm() -> bytes:
    return make_webm()


def read_16k_wav(path: Path) -> np.ndarray:
    samples, rate = read_wav(path)
    assert rate == 16_000
    return samples


def loud(samples: np.ndarray, second: int, rate: int = 16_000) -> bool:
    window = samples[second * rate + rate // 5 : (second + 1) * rate - rate // 5]
    return bool(np.abs(window).max() > 3000)


def test_chunks_out_of_order_and_repeated_are_stored_once(paths: SessionPaths):
    assert store_audio_chunk(paths, 2, b"c")
    assert store_audio_chunk(paths, 0, b"a")
    assert not store_audio_chunk(paths, 2, b"c")
    assert [seq for seq, _ in audio_chunks(paths)] == [0, 2]
    assert missing_audio_chunks(paths, 4) == [1, 3]
    assert missing_audio_chunks(paths, 0) == []
    assert not list(paths.live_audio_dir.glob("*.tmp"))


def test_conflicting_repeat_is_rejected_not_overwritten(paths: SessionPaths):
    store_audio_chunk(paths, 0, b"first")
    with pytest.raises(ChunkConflict):
        store_audio_chunk(paths, 0, b"other")
    assert (paths.live_audio_dir / "000000.webm").read_bytes() == b"first"
    store_frame(paths, 1.5, 1, b"x")
    assert not store_frame(paths, 1.5, 1, b"x")
    with pytest.raises(ChunkConflict):
        store_frame(paths, 1.5, 1, b"y")


def write_chunks(paths: SessionPaths, chunks: list[bytes], skip: set[int] = frozenset()) -> None:
    order = list(range(len(chunks)))
    random.Random(0).shuffle(order)
    for seq in order:
        if seq not in skip:
            store_audio_chunk(paths, seq, chunks[seq])


def test_audio_from_shuffled_chunks_matches_the_recording(paths: SessionPaths, webm: bytes):
    write_chunks(paths, split(webm))
    duration = finalize_audio(audio_chunks(paths), paths.audio, 16_000)
    samples = read_16k_wav(paths.audio)
    assert duration == pytest.approx(WEBM_SECONDS, abs=0.1)
    assert [loud(samples, s) for s in range(WEBM_SECONDS)] == [
        s in TONE_SECONDS for s in range(WEBM_SECONDS)
    ]
    assert not paths.root.joinpath("audio.wav.tmp").exists()


@pytest.mark.parametrize("lost", [{5}, {12}, {12, 13, 20}])
def test_missing_chunks_become_silence_and_keep_later_audio_in_place(
    tmp_path: Path, webm: bytes, lost: set[int]
):
    chunks = split(webm, 2000)
    full_paths = SessionPaths(tmp_path / "full").ensure()
    write_chunks(full_paths, chunks)
    finalize_audio(audio_chunks(full_paths), full_paths.audio, 16_000)
    full = read_16k_wav(full_paths.audio)

    lossy_paths = SessionPaths(tmp_path / "lossy").ensure()
    write_chunks(lossy_paths, chunks, skip=lost)
    duration = finalize_audio(audio_chunks(lossy_paths), lossy_paths.audio, 16_000)
    lossy = read_16k_wav(lossy_paths.audio)

    assert duration == pytest.approx(WEBM_SECONDS, abs=0.1)
    differs = np.abs(envelope(full) - envelope(lossy)) > 3000
    assert np.count_nonzero(differs) * 0.01 < 1.2 * len(lost)
    assert loud(lossy, 0) and loud(lossy, 4) and not loud(lossy, 5)
    assert abs(onset(lossy, 3.8) - onset(full, 3.8)) <= 160


def envelope(samples: np.ndarray, window: int = 160) -> np.ndarray:
    n = len(samples) // window * window
    return np.abs(samples[:n].astype(np.int32)).reshape(-1, window).max(axis=1)


def onset(samples: np.ndarray, after_s: float) -> int:
    start = round(after_s * 16_000)
    return start + int(np.argmax(np.abs(samples[start:]) > 3000))


@pytest.mark.parametrize("partial_id", [0, 2])
def test_header_only_first_chunk_then_a_gap(paths: SessionPaths, webm: bytes, partial_id: int):
    header_end = webm.find(CLUSTER_ID) + partial_id
    chunks = [webm[:header_end], *split(webm[header_end:], 2000)]
    write_chunks(paths, chunks, skip={1})
    duration = finalize_audio(audio_chunks(paths), paths.audio, 16_000)
    samples = read_16k_wav(paths.audio)
    assert duration == pytest.approx(WEBM_SECONDS, abs=0.1)
    assert loud(samples, 2) and loud(samples, 4) and not loud(samples, 5)


def test_runs_split_at_missing_chunks(tmp_path: Path):
    p = [tmp_path / str(i) for i in range(6)]
    chunks = [(0, p[0]), (1, p[1]), (3, p[3]), (4, p[4]), (5, p[5])]
    assert contiguous_runs(chunks) == [[p[0], p[1]], [p[3], p[4], p[5]]]
    assert contiguous_runs([]) == []


@pytest.mark.parametrize("cut", range(len(CLUSTER_ID) + 1))
def test_cluster_found_across_file_boundaries(tmp_path: Path, cut: int):
    stream = b"header-bytes" + CLUSTER_ID + b"cluster"
    at = len(b"header-bytes") + cut
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
    with pytest.raises(UserFacingError, match="not a readable") as raised:
        finalize_audio(audio_chunks(paths), paths.audio, 16_000)
    assert raised.value.message == UNREADABLE_LIVE_AUDIO
    assert not paths.audio.exists()


async def test_ingest_requires_audio_and_its_first_chunk(tmp_path: Path, webm: bytes):
    ctx = stage_ctx(tmp_path, live=True)
    paths = ctx.paths
    with pytest.raises(UserFacingError, match="no audio was recorded") as raised:
        await BrowserLiveSource().ingest(ctx)
    assert raised.value.message == LIVE_WITHOUT_AUDIO
    store_audio_chunk(paths, 1, split(webm)[1])
    with pytest.raises(UserFacingError, match="first audio chunk") as raised:
        await BrowserLiveSource().ingest(ctx)
    assert raised.value.message == UNREADABLE_LIVE_AUDIO


def test_frames_sampled_on_the_grid_by_timestamp(paths: SessionPaths):
    captured = {0.0: 0, 1.02: 1, 1.6: 1, 2.05: 2, 4.3: 4, 5.0: 5}
    for t in reversed(list(captured)):
        store_frame(paths, t, 1, solid_jpeg(COLOURS[captured[t]]))
    index = finalize_frames(paths, fps=1.0, max_width=1280)
    assert [f.t for f in index.frames] == [0.0, 1.02, 2.05, 4.3, 5.0]
    assert [f.path for f in index.frames][:2] == ["frames/000000000.jpg", "frames/000001020.jpg"]
    for ref in index.frames:
        expected = COLOURS[captured[ref.t]]
        assert mean_bgr(paths.root / ref.path) == pytest.approx(np.array(expected), abs=12)
    assert read_model(paths.frame_index, FrameIndex) == index
    assert (index.width, index.height) == (320, 240)


def test_frames_downscaled_and_letterboxed_to_one_size(paths: SessionPaths):
    store_frame(paths, 0.0, 1, solid_jpeg(COLOURS[0], (640, 480)))
    store_frame(paths, 1.0, 1, solid_jpeg(COLOURS[1], (640, 240)))
    store_frame(paths, 2.0, 1, b"\xff\xd8\xff truncated")
    index = finalize_frames(paths, fps=1.0, max_width=320)
    assert (index.width, index.height) == (320, 240)
    assert [f.t for f in index.frames] == [0.0, 1.0]
    wide = cv2.imread(str(paths.root / index.frames[1].path))
    assert wide.shape == (240, 320, 3)
    assert wide[:50].mean() < 10 and wide[100:140].mean() > 50


def test_each_share_is_sized_from_its_own_first_frame(paths: SessionPaths):
    store_frame(paths, 0.0, 1, solid_jpeg(COLOURS[0], (160, 120)))
    store_frame(paths, 1.0, 1, solid_jpeg(COLOURS[1], (320, 120)))
    store_frame(paths, 9.2, 2, b"\xff\xd8\xff truncated")
    store_frame(paths, 10.0, 2, solid_jpeg(COLOURS[2], (1280, 800)))
    store_frame(paths, 11.0, 2, solid_jpeg(COLOURS[3], (640, 400)))
    index = finalize_frames(paths, fps=1.0, max_width=640)
    assert index.share_starts == [10.0]
    assert (index.width, index.height) == (160, 120)
    sizes = [cv2.imread(str(paths.root / f.path)).shape[:2] for f in index.frames]
    assert [f.t for f in index.frames] == [0.0, 1.0, 10.0, 11.0]
    assert sizes == [(120, 160), (120, 160), (400, 640), (400, 640)]


def test_shares_come_from_the_frames_share_numbers(paths: SessionPaths):
    for t, share, data in [
        (0.0, 1, solid_jpeg(COLOURS[0])),
        (1.0, 1, solid_jpeg(COLOURS[0])),
        (5.0, 2, b"\xff\xd8\xff truncated"),
        (8.0, 3, solid_jpeg(COLOURS[1])),
        (9.0, 3, solid_jpeg(COLOURS[1])),
        (9.5, 4, solid_jpeg(COLOURS[2])),
        (10.0, 4, solid_jpeg(COLOURS[2])),
    ]:
        store_frame(paths, t, share, data)
    index = finalize_frames(paths, fps=1.0, max_width=320)
    assert [f.t for f in index.frames] == [0.0, 1.0, 8.0, 9.0, 10.0]
    assert index.share_starts == [8.0, 10.0]


def test_frames_split_into_shares_where_the_share_number_changes():
    def frames(*items: tuple[float, int]) -> list[CapturedFrame]:
        return [CapturedFrame(t, share, Path(f"{t}.jpg")) for t, share in items]

    captured = frames((0.0, 1), (1.0, 1), (5.0, 2), (6.0, 3), (7.0, 3))
    assert [[f.t for f in share] for share in split_shares(captured)] == [
        [0.0, 1.0],
        [5.0],
        [6.0, 7.0],
    ]
    assert split_shares(frames((0.0, 1), (1.0, 1))) == [frames((0.0, 1), (1.0, 1))]
    assert split_shares([]) == []


def test_frames_of_two_shares_at_the_same_time_are_both_kept(paths: SessionPaths):
    assert store_frame(paths, 10.0, 1, b"a")
    assert store_frame(paths, 10.0, 2, b"b")
    assert [(f.t, f.share) for f in captured_frames(paths)] == [(10.0, 1), (10.0, 2)]


def test_frames_captured_before_share_numbers_are_one_share(paths: SessionPaths):
    (paths.live_frames_dir).mkdir(parents=True, exist_ok=True)
    (paths.live_frames_dir / "000001500.jpg").write_bytes(solid_jpeg(COLOURS[0]))
    store_frame(paths, 2.5, 1, solid_jpeg(COLOURS[0]))
    assert [(f.t, f.share) for f in captured_frames(paths)] == [(1.5, 1), (2.5, 1)]
    assert finalize_frames(paths, fps=1.0, max_width=320).share_starts == []


def test_finalize_frames_is_repeatable_and_removes_stale_frames(paths: SessionPaths):
    stale = paths.frames_dir / "999999999.jpg"
    stale.write_bytes(b"old")
    store_frame(paths, 0.5, 1, solid_jpeg(COLOURS[0]))
    first = finalize_frames(paths, fps=1.0, max_width=1280)
    assert not stale.exists()
    assert finalize_frames(paths, fps=1.0, max_width=1280) == first
    assert len(list(paths.frames_dir.glob("*.jpg"))) == 1
    assert (paths.live_frames_dir / "000000500-0001.jpg").exists()


def test_no_frames_gives_an_empty_index(paths: SessionPaths):
    assert finalize_frames(paths, fps=1.0, max_width=1280) == FrameIndex(
        fps=1.0, width=0, height=0, frames=[]
    )


def test_fit_keeps_aspect():
    tall = np.full((200, 50, 3), 255, np.uint8)
    out = fit(tall, (100, 100))
    assert out.shape == (100, 100, 3)
    assert out[:, :30].max() == 0 and out[:, 40:60].min() == 255


def test_live_input_is_validated(client: TestClient):
    sid = start_recording(client)
    assert post_audio(client, sid, 0, b"not webm") == 415
    assert post_audio(client, sid, -1, b"x") == 422
    assert post_audio(client, sid, 1, b"") == 400
    assert post_frame(client, sid, 1.0, b"not a jpeg") == 415
    for t in ("nan", "inf", "-1", "86401"):
        assert post_frame(client, sid, t, solid_jpeg(COLOURS[0])) == 422  # type: ignore[arg-type]
    big = b"\xff\xd8\xff" + bytes(16 * 1024 * 1024)
    assert post_frame(client, sid, 1.0, big) == 413
    assert post_audio(client, "nope", 0, b"x") == 404
    assert client.post("/api/live/nope/end").status_code == 404


def test_frames_need_their_share(client: TestClient, settings: Settings):
    sid = start_recording(client)
    for share in ("0", "-1", "10000", "x"):
        assert post_frame(client, sid, 1.0, solid_jpeg(COLOURS[0]), share) == 422  # type: ignore[arg-type]
    url = f"/api/live/{sid}/frame?t=1"
    assert client.post(url, content=solid_jpeg(COLOURS[0])).status_code == 422
    assert post_frame(client, sid, 1.0, solid_jpeg(COLOURS[0]), share=2) == 204
    paths = session_paths(settings.sessions_dir, sid)
    assert [(f.t, f.share) for f in captured_frames(paths)] == [(1.0, 2)]


def test_repeated_chunk_with_other_content_conflicts(client: TestClient, webm: bytes):
    sid = start_recording(client)
    chunks = split(webm)
    assert post_audio(client, sid, 1, chunks[1]) == 204
    assert post_audio(client, sid, 1, chunks[1]) == 204
    assert post_audio(client, sid, 1, chunks[2]) == 409


def test_end_waits_for_missing_chunks_then_processes(client: TestClient, webm: bytes):
    sid = start_recording(client)
    chunks = split(webm)
    for seq in range(1, len(chunks)):
        assert post_audio(client, sid, seq, chunks[seq]) == 204
    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(chunks)})
    assert r.status_code == 409
    assert r.json()["detail"]["missing_audio_chunks"] == [0]
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "recording"

    assert post_audio(client, sid, 0, chunks[0]) == 204
    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(chunks)})
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
    assert post_audio(client, sid, len(chunks), b"x") == 409
    assert client.post(f"/api/live/{sid}/end").status_code == 409
    wait_for_status(client, sid, {"done", "failed"})


def test_end_with_allowed_gaps(client: TestClient, webm: bytes, settings: Settings):
    sid = start_recording(client)
    chunks = split(webm, 2000)
    for seq in range(len(chunks)):
        if seq != 3:
            post_audio(client, sid, seq, chunks[seq])
    log = settings.sessions_dir / sid / "usage_calls.jsonl"
    record = UsageRecord(
        at=datetime.now(UTC), purpose="x", operation="op", model="m", latency_s=0, cost_usd=0.01
    )
    append_record(log, record)
    body = {"audio_chunks": len(chunks), "allow_gaps": True}
    r = client.post(f"/api/live/{sid}/end", json=body)
    assert r.status_code == 200
    assert r.json()["cost_usd"] == pytest.approx(0.01)
    wait_for_status(client, sid, {"done", "failed"})
    audio = settings.sessions_dir / sid / "audio.wav"
    assert len(read_16k_wav(audio)) / 16_000 == pytest.approx(WEBM_SECONDS, abs=0.1)


def test_stop_keeps_the_recording_without_making_notes(client: TestClient, webm: bytes):
    sid = start_recording(client)
    chunks = split(webm)
    assert post_audio(client, sid, 0, chunks[0]) == 204
    r = client.post(f"/api/live/{sid}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"
    runner = client.app.state.runner  # type: ignore[attr-defined]
    assert not runner.is_running(sid)
    assert post_audio(client, sid, 1, chunks[1]) == 409
    assert post_frame(client, sid, 1.0, solid_jpeg(COLOURS[0])) == 409
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "stopped"


def test_stopping_again_is_a_no_op(client: TestClient):
    sid = start_recording(client)
    assert stop_recording(client, sid) == 200
    r = client.post(f"/api/live/{sid}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


def test_stop_is_announced_to_open_pages(client: TestClient):
    sid = start_recording(client)
    stop_recording(client, sid)
    bus = client.app.state.bus  # type: ignore[attr-defined]
    statuses = [e.data["status"] for e in bus.history(sid) if e.type == "status"]
    assert statuses == ["stopped"]


def test_make_notes_from_a_stopped_recording(client: TestClient, webm: bytes, settings: Settings):
    sid = start_recording(client)
    chunks = split(webm)
    for seq, chunk in enumerate(chunks):
        post_audio(client, sid, seq, chunk)
    stop_recording(client, sid)
    r = client.post(f"/api/live/{sid}/end", json={"allow_gaps": True})
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
    assert stop_recording(client, sid) == 409
    wait_for_status(client, sid, {"done", "failed"})
    audio = settings.sessions_dir / sid / "audio.wav"
    assert len(read_16k_wav(audio)) / 16_000 == pytest.approx(WEBM_SECONDS, abs=0.1)


def test_discard_a_stopped_recording(client: TestClient, settings: Settings):
    sid = start_recording(client)
    stop_recording(client, sid)
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert not (settings.sessions_dir / sid).exists()
    assert stop_recording(client, sid) == 404


def test_only_a_live_recording_can_be_stopped(client: TestClient):
    assert stop_recording(client, "nope") == 404
    upload = client.post("/api/sessions", files={"file": ("talk.mp4", b"not really a video")})
    sid = upload.json()["id"]
    assert stop_recording(client, sid) == 409
    wait_for_status(client, sid, {"failed"})
    assert stop_recording(client, sid) == 409
    live = start_recording(client)
    client.post(f"/api/live/{live}/end")
    assert stop_recording(client, live) == 409
    wait_for_status(client, live, {"done", "failed"})
    assert stop_recording(client, live) == 409


def test_stop_keeps_live_processing_for_making_notes(client: TestClient):
    sid = start_recording(client)
    live = client.app.state.runner.live  # type: ignore[attr-defined]
    processor = live.get(sid)
    assert processor is not None
    stop_recording(client, sid)
    assert live.get(sid) is processor


def test_make_notes_after_an_engine_restart(settings: Settings, webm: bytes):
    with TestClient(create_app(settings)) as client:
        sid = start_recording(client)
        for seq, chunk in enumerate(split(webm)):
            post_audio(client, sid, seq, chunk)
        stop_recording(client, sid)
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/sessions/{sid}").json()["status"] == "stopped"
        assert client.app.state.runner.live.get(sid) is None  # type: ignore[attr-defined]
        r = client.post(f"/api/live/{sid}/end", json={"allow_gaps": True})
        assert r.status_code == 200
        assert r.json()["status"] == "processing"
        wait_for_status(client, sid, {"done", "failed"})
    audio = settings.sessions_dir / sid / "audio.wav"
    assert len(read_16k_wav(audio)) / 16_000 == pytest.approx(WEBM_SECONDS, abs=0.1)


def test_live_session_produces_notes_like_an_upload(
    client: TestClient, webm: bytes, settings: Settings, monkeypatch
):
    install_get_openai(monkeypatch, PipelineOpenAI(), write, select, transcribe, render, audio_lane)
    sid = start_recording(client)
    chunks = split(webm)
    order = list(range(len(chunks)))
    random.Random(1).shuffle(order)
    for seq in order + order[:3]:
        assert post_audio(client, sid, seq, chunks[seq]) == 204
    frame_times = [0.0, 1.01, 2.0, 3.02, 4.0, 5.0]
    for i in [3, 0, 5, 1, 4, 2, 0, 5]:
        assert post_frame(client, sid, frame_times[i], solid_jpeg(COLOURS[i // 3 * 3])) == 204

    r = client.post(f"/api/live/{sid}/end", json={"audio_chunks": len(chunks)})
    assert r.status_code == 200
    session = wait_for_status(client, sid, {"done", "failed"})
    assert session["status"] == "done", session.get("error")

    root = settings.sessions_dir / sid
    samples = read_16k_wav(root / "audio.wav")
    assert len(samples) / 16_000 == pytest.approx(WEBM_SECONDS, abs=0.1)
    index = read_model(root / "frames.json", FrameIndex)
    assert [f.t for f in index.frames] == frame_times
    assert all((root / f.path).exists() for f in index.frames)
    notes = client.get(f"/api/sessions/{sid}/notes").json()
    assert "Ship the prototype" in notes["markdown"]
    assert notes["visuals"]
    assert len(list((root / "live" / "audio").glob("*.webm"))) == len(chunks)

    assert client.post(f"/api/sessions/{sid}/run").status_code == 200
    wait_until_idle(client, sid)
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "done"
    assert read_model(root / "frames.json", FrameIndex) == index


def test_replay_records_a_file_without_video_as_audio_only(tmp_path: Path):
    assert list(file_frames(make_media(tmp_path / "talk.mp4", video=True, seconds=2)))
    assert list(file_frames(make_media(tmp_path / "call.wav", video=False, seconds=2))) == []


def make_audio_with_cover_art(path: Path) -> Path:
    rate = 48_000
    with av.open(str(path), "w", format="mp4") as out:
        audio = out.add_stream("aac", rate=rate)
        audio.layout = "mono"
        cover = out.add_stream("mjpeg", rate=1)
        cover.width, cover.height, cover.pix_fmt = 64, 64, "yuvj420p"
        cover.disposition = av.stream.Disposition.attached_pic
        art = av.VideoFrame.from_ndarray(np.full((64, 64, 3), 200, np.uint8), format="bgr24")
        art = art.reformat(format="yuvj420p")
        art.pts, art.time_base = 0, Fraction(1, 1)
        out.mux(cover.encode(art))
        out.mux(cover.encode())
        tone = np.zeros((1, rate * 2), np.float32)
        frame = av.AudioFrame.from_ndarray(tone, format="fltp", layout="mono")
        frame.sample_rate, frame.pts = rate, 0
        out.mux(audio.encode(frame))
        out.mux(audio.encode())
    return path


def test_replay_records_an_audio_file_with_cover_art_as_audio_only(tmp_path: Path):
    assert list(file_frames(make_audio_with_cover_art(tmp_path / "podcast.m4a"))) == []
