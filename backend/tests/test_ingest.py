import wave
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from iris.config import Settings
from iris.llm.errors import UserFacingError
from iris.models import UNREADABLE_UPLOAD, UPLOAD_WITHOUT_AUDIO, FrameIndex, SourceKind
from iris.pipeline.context import StageContext
from iris.sources.upload import UploadSource, extract_audio, extract_frames
from iris.store.layout import SessionPaths, read_model

W, H, FPS = 320, 240, 10
SECONDS = 6
COLOURS = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 0, 255), (255, 255, 0)]


def make_media(
    path: Path,
    *,
    video: bool = True,
    audio: bool = True,
    rotation: int | None = None,
    audio_start: float = 0.0,
    seconds: int = SECONDS,
) -> Path:
    with av.open(str(path), "w") as out:
        vs = as_ = None
        if video:
            vs = out.add_stream("mpeg4", rate=FPS)
            vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
            if rotation is not None:
                vs.set_display_rotation(rotation)
        if audio:
            codec = "pcm_s16le" if path.suffix == ".wav" else "aac"
            as_ = out.add_stream(codec, rate=48_000)
            as_.layout = "stereo"
        if vs is not None:
            for i in range(seconds * FPS):
                img = np.empty((H, W, 3), np.uint8)
                img[:] = COLOURS[(i // FPS) % len(COLOURS)]
                if rotation is not None:
                    img[H // 2 :] = 0
                frame = av.VideoFrame.from_ndarray(img, format="bgr24")
                frame.pts = i
                frame.time_base = Fraction(1, FPS)
                out.mux(vs.encode(frame))
            out.mux(vs.encode())
        if as_ is not None:
            write_sine(out, as_, seconds - audio_start, audio_start)
    return path


def write_sine(out, stream, seconds: float, start: float) -> None:
    rate, n = 48_000, 1024
    offset = round(start * rate)
    for k in range(round(seconds * rate) // n):
        t = (np.arange(n) + k * n) / rate
        s = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        fmt = "s16" if stream.codec_context.name == "pcm_s16le" else "fltp"
        data = np.stack([s, s])
        if fmt == "s16":
            data = (data.T * 32767).astype(np.int16).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(data, format=fmt, layout="stereo")
        frame.sample_rate = rate
        frame.pts = offset + k * n
        frame.time_base = Fraction(1, rate)
        out.mux(stream.encode(frame))
    out.mux(stream.encode())


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_media(tmp_path_factory.mktemp("media") / "talk.mp4")


def mean_bgr(path: Path) -> np.ndarray:
    return cv2.imread(str(path)).reshape(-1, 3).mean(axis=0)


def make_ctx(root: Path, src: Path, **settings) -> StageContext:
    paths = SessionPaths(root).ensure()
    paths.source(src.suffix).write_bytes(src.read_bytes())
    return StageContext(
        session_id="s1",
        source=SourceKind.UPLOAD,
        paths=paths,
        settings=Settings(data_dir=root, **settings),
    )


def test_frames_sampled_once_per_second(media: Path, tmp_path: Path):
    index = extract_frames(media, tmp_path / "frames", fps=1.0, max_width=1280)
    assert index.fps == 1.0
    assert [f.t for f in index.frames] == pytest.approx([0, 1, 2, 3, 4, 5])
    assert [f.path for f in index.frames] == [f"frames/{s * 1000:09d}.jpg" for s in range(SECONDS)]
    assert index.frames[1].path == "frames/000001000.jpg"
    for ref, colour in zip(index.frames, COLOURS, strict=True):
        assert mean_bgr(tmp_path / ref.path) == pytest.approx(np.array(colour), abs=12)


def test_frames_at_two_fps(media: Path, tmp_path: Path):
    index = extract_frames(media, tmp_path / "frames", fps=2.0, max_width=1280)
    assert [f.t for f in index.frames] == pytest.approx([i / 2 for i in range(2 * SECONDS)])
    assert len(list((tmp_path / "frames").glob("*.jpg"))) == 2 * SECONDS


def test_frames_downscaled_keeping_aspect(media: Path, tmp_path: Path):
    index = extract_frames(media, tmp_path / "frames", fps=1.0, max_width=160)
    assert (index.width, index.height) == (160, 120)
    img = cv2.imread(str(tmp_path / index.frames[0].path))
    assert img.shape == (120, 160, 3)


def test_frames_not_upscaled(media: Path, tmp_path: Path):
    index = extract_frames(media, tmp_path / "frames", fps=1.0, max_width=1280)
    assert (index.width, index.height) == (W, H)


def test_frames_rotated_upright(tmp_path: Path):
    src = make_media(tmp_path / "phone.mp4", rotation=90, seconds=1)
    index = extract_frames(src, tmp_path / "frames", fps=1.0, max_width=1280)
    assert (index.width, index.height) == (H, W)
    img = cv2.imread(str(tmp_path / index.frames[0].path))
    left, right = img[:, : H // 2 - 4].mean(), img[:, H // 2 + 4 :].mean()
    assert left > 50 > right


def test_audio_only_gives_empty_frame_index(tmp_path: Path):
    src = make_media(tmp_path / "voice.wav", video=False, seconds=1)
    index = extract_frames(src, tmp_path / "frames", fps=1.0, max_width=1280)
    assert index == FrameIndex(fps=1.0, width=0, height=0, frames=[])


def test_audio_is_16k_mono_s16(media: Path, tmp_path: Path):
    dest = tmp_path / "audio.wav"
    duration = extract_audio(media, dest, rate=16_000)
    with wave.open(str(dest)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        assert w.getnframes() / 16_000 == pytest.approx(SECONDS, abs=0.1)
        samples = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    assert duration == pytest.approx(SECONDS, abs=0.1)
    assert np.abs(samples[16_000:32_000]).max() > 5000
    assert not (tmp_path / "audio.wav.tmp").exists()


@pytest.mark.parametrize("rate", [7_999, 16_001, 44_100])
def test_audio_sample_rate_outside_what_the_speech_models_take_is_rejected(rate: int):
    with pytest.raises(ValidationError):
        Settings(audio_sample_rate=rate)


@pytest.mark.parametrize("rate", [8_000, 16_000])
def test_audio_sample_rate_within_bounds_is_accepted(rate: int):
    assert Settings(audio_sample_rate=rate).audio_sample_rate == rate


def test_audio_starting_late_is_padded_with_silence(tmp_path: Path):
    src = make_media(tmp_path / "late.mkv", audio_start=1.0, seconds=3)
    dest = tmp_path / "audio.wav"
    extract_audio(src, dest, rate=16_000)
    with wave.open(str(dest)) as w:
        samples = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    assert len(samples) / 16_000 == pytest.approx(3, abs=0.1)
    assert np.abs(samples[: 16_000 - 800]).max() == 0
    assert np.abs(samples[16_000 + 800 : 32_000]).max() > 5000


def test_no_audio_raises(tmp_path: Path):
    src = make_media(tmp_path / "silent.mp4", audio=False, seconds=1)
    with pytest.raises(UserFacingError, match="no audio") as raised:
        extract_audio(src, tmp_path / "audio.wav", rate=16_000)
    assert raised.value.message == UPLOAD_WITHOUT_AUDIO


def test_unreadable_file_raises(tmp_path: Path):
    src = tmp_path / "junk.mp4"
    src.write_bytes(b"not really a video")
    with pytest.raises(UserFacingError, match="not a readable") as raised:
        extract_audio(src, tmp_path / "audio.wav", rate=16_000)
    assert raised.value.message == UNREADABLE_UPLOAD


async def test_ingest_writes_audio_and_frames(media: Path, tmp_path: Path):
    ctx = make_ctx(tmp_path / "s1", media, frame_max_width=160)
    stale = ctx.paths.frames_dir / "999999999.jpg"
    stale.write_bytes(b"old")
    await UploadSource().ingest(ctx)

    index = read_model(ctx.paths.frame_index, FrameIndex)
    assert (index.fps, index.width, index.height) == (1.0, 160, 120)
    assert len(index.frames) == SECONDS
    assert all((ctx.paths.root / f.path).exists() for f in index.frames)
    assert not stale.exists()
    assert not ctx.paths.root.joinpath("frames.json.tmp").exists()
    with wave.open(str(ctx.paths.audio)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)


async def test_ingest_audio_only(tmp_path: Path):
    src = make_media(tmp_path / "voice.m4a", video=False, seconds=1)
    ctx = make_ctx(tmp_path / "s1", src)
    await UploadSource().ingest(ctx)
    assert read_model(ctx.paths.frame_index, FrameIndex) == FrameIndex(
        fps=1.0, width=0, height=0, frames=[]
    )
    assert ctx.paths.audio.exists()


async def test_ingest_video_without_audio_fails_before_decoding(tmp_path: Path):
    src = make_media(tmp_path / "silent.mp4", audio=False, seconds=1)
    ctx = make_ctx(tmp_path / "s1", src)
    with pytest.raises(UserFacingError, match="no audio track"):
        await UploadSource().ingest(ctx)
    assert not ctx.paths.frame_index.exists()
    assert not ctx.paths.audio.exists()


async def test_ingest_without_source(tmp_path: Path):
    ctx = StageContext(
        session_id="s1",
        source=SourceKind.UPLOAD,
        paths=SessionPaths(tmp_path / "s1").ensure(),
        settings=Settings(data_dir=tmp_path),
    )
    with pytest.raises(FileNotFoundError):
        await UploadSource().ingest(ctx)
