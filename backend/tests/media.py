import io
import wave
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np

from iris.live.replay import encode_webm
from iris.models import FrameRef, Moment
from iris.pipeline.stages.detect import DetectParams, MomentDetector
from iris.pipeline.stages.transcribe import encode_wav

COLOURS = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 0, 255), (255, 255, 0)]

VIDEO_W, VIDEO_H, VIDEO_FPS = 320, 240, 10
MEDIA_SECONDS = 6

WEBM_RATE = 48_000
WEBM_SECONDS = 6
TONE_SECONDS = {0, 2, 4}

SPEECH_RATE = 16_000

SLIDE_W, SLIDE_H = 640, 360
BULLET_Y = 280


def tone(t: np.ndarray, amplitude: float = 0.3) -> np.ndarray:
    return amplitude * np.sin(2 * np.pi * 440 * t)


def image_bytes(img: np.ndarray, ext: str = ".jpg", quality: int | None = None) -> bytes:
    params = [] if quality is None else [cv2.IMWRITE_JPEG_QUALITY, quality]
    ok, buf = cv2.imencode(ext, img, params)
    assert ok
    return buf.tobytes()


def solid_jpeg(colour: tuple[int, int, int], size: tuple[int, int] = (320, 240)) -> bytes:
    img = np.empty((size[1], size[0], 3), np.uint8)
    img[:] = colour
    return image_bytes(img)


def mean_bgr(path: Path) -> np.ndarray:
    return cv2.imread(str(path)).reshape(-1, 3).mean(axis=0)


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def make_media(
    path: Path,
    *,
    video: bool = True,
    audio: bool = True,
    rotation: int | None = None,
    audio_start: float = 0.0,
    seconds: int = MEDIA_SECONDS,
) -> Path:
    with av.open(str(path), "w") as out:
        vs = as_ = None
        if video:
            vs = out.add_stream("mpeg4", rate=VIDEO_FPS)
            vs.width, vs.height, vs.pix_fmt = VIDEO_W, VIDEO_H, "yuv420p"
            if rotation is not None:
                vs.set_display_rotation(rotation)
        if audio:
            codec = "pcm_s16le" if path.suffix == ".wav" else "aac"
            as_ = out.add_stream(codec, rate=48_000)
            as_.layout = "stereo"
        if vs is not None:
            for i in range(seconds * VIDEO_FPS):
                img = np.empty((VIDEO_H, VIDEO_W, 3), np.uint8)
                img[:] = COLOURS[(i // VIDEO_FPS) % len(COLOURS)]
                if rotation is not None:
                    img[VIDEO_H // 2 :] = 0
                frame = av.VideoFrame.from_ndarray(img, format="bgr24")
                frame.pts = i
                frame.time_base = Fraction(1, VIDEO_FPS)
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
        s = tone(t).astype(np.float32)
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


def make_webm(seconds: int = WEBM_SECONDS) -> bytes:
    t = np.arange(seconds * WEBM_RATE) / WEBM_RATE
    loud = np.isin(t.astype(int), list(TONE_SECONDS))
    return encode_webm([(loud * tone(t)).astype(np.float32)]).data


def split(data: bytes, size: int = 4000) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def make_audio(pattern: list[tuple[str, float]], seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    parts = []
    for kind, dur in pattern:
        n = int(dur * SPEECH_RATE)
        noise = rng.normal(0, 20, n)
        if kind == "tone":
            t = np.arange(n) / SPEECH_RATE
            parts.append(tone(t, 8000) + noise)
        else:
            parts.append(noise)
    return np.concatenate(parts).astype(np.int16)


def write_wav(path: Path, samples: np.ndarray) -> None:
    path.write_bytes(encode_wav(samples, SPEECH_RATE))


def slide(title: str, bullets: int = 2, shape: str = "rect") -> np.ndarray:
    img = np.full((SLIDE_H, SLIDE_W, 3), 255, np.uint8)
    cv2.putText(img, title, (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3)
    for i in range(bullets):
        y = 140 + i * 55 if i < 2 else BULLET_Y
        cv2.circle(img, (75, y - 10), 7, (40, 40, 40), -1)
        cv2.putText(
            img, f"{title} point {i + 1}", (95, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2
        )
    if shape == "rect":
        cv2.rectangle(img, (470, 130), (600, 300), (200, 80, 30), -1)
    elif shape == "circle":
        cv2.circle(img, (530, 220), 70, (30, 160, 30), -1)
    else:
        pts = np.array([[530, 130], [610, 300], [450, 300]], np.int32)
        cv2.fillPoly(img, [pts], (30, 30, 200))
    return img


SLIDE_A = slide("Agenda", shape="rect")
SLIDE_B = slide("Results", shape="circle")
SLIDE_C = slide("Next steps", shape="tri")


class Deck:
    def __init__(self, seed: int = 0):
        self.frames: list[np.ndarray] = []
        self.transitions: set[float] = set()
        self.rng = np.random.default_rng(seed)

    @property
    def t(self) -> float:
        return float(len(self.frames))

    def _add(self, img: np.ndarray) -> None:
        noise = self.rng.normal(0, 4, img.shape)
        self.frames.append(np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8))

    def hold(self, img: np.ndarray, seconds: int, overlay=None) -> "Deck":
        for i in range(seconds):
            frame = img.copy()
            if overlay is not None:
                overlay(frame, i, self.rng)
            self._add(frame)
        return self

    def fade(self, a: np.ndarray, b: np.ndarray) -> "Deck":
        self.transitions.add(self.t)
        self._add(cv2.addWeighted(a, 0.5, b, 0.5, 0))
        return self

    def refs(self) -> list[FrameRef]:
        return [
            FrameRef(t=float(i), path=f"frames/{i * 1000:09d}.jpg") for i in range(len(self.frames))
        ]

    def detect(self, params: DetectParams | None = None) -> list[Moment]:
        det = MomentDetector(params)
        out = [det.feed(r.t, r, f) for r, f in zip(self.refs(), self.frames, strict=True)]
        out.append(det.finish(self.t))
        return [m for m in out if m is not None]


def three_slides(overlay_b=None, overlay_all=None) -> Deck:
    d = Deck()
    d.hold(SLIDE_A, 10, overlay_all).fade(SLIDE_A, SLIDE_B)
    d.hold(SLIDE_B, 10, overlay_b or overlay_all).fade(SLIDE_B, SLIDE_C)
    return d.hold(SLIDE_C, 10, overlay_all)
