import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

FRAMES_DIR = "frames"
VISUALS_DIR = "visuals"
SERVED_DIRS = (FRAMES_DIR, VISUALS_DIR)
SOURCE_CROP_SUFFIX = ".source.jpg"
REDRAW_SUFFIXES = frozenset({".svg", ".png", ".jpg"})

MAX_AUDIO_SEQ = 999_999
MAX_FRAME_T = 24 * 3600.0
MAX_SHARE = 9999
FIRST_SHARE = 1

_AUDIO_CHUNK_NAME = re.compile(r"^(\d{6})\.webm$")
_CAPTURED_FRAME_NAME = re.compile(r"^(\d{9})(?:-(\d{4}))?\.jpg$")


def frame_name(t: float) -> str:
    return f"{round(t * 1000):09d}.jpg"


def frame_path(t: float) -> str:
    return f"{FRAMES_DIR}/{frame_name(t)}"


def captured_frame_name(t: float, share: int) -> str:
    return f"{round(t * 1000):09d}-{share:04d}.jpg"


def read_captured_frame_name(name: str) -> tuple[float, int] | None:
    if m := _CAPTURED_FRAME_NAME.match(name):
        return int(m.group(1)) / 1000, int(m.group(2) or FIRST_SHARE)
    return None


def audio_chunk_name(seq: int) -> str:
    return f"{seq:06d}.webm"


def read_audio_chunk_name(name: str) -> int | None:
    if m := _AUDIO_CHUNK_NAME.match(name):
        return int(m.group(1))
    return None


def transcript_cache_name(index: int, start: int, end: int, rate: int) -> str:
    return f"{index}_{round(start * 1000 / rate)}_{round(end * 1000 / rate)}.json"


def visual_source_path(visual_id: str) -> str:
    return f"{VISUALS_DIR}/{visual_id}{SOURCE_CROP_SUFFIX}"


def visual_path(visual_id: str, ext: str) -> str:
    return f"{VISUALS_DIR}/{visual_id}.{ext}"


def inside(root: Path, relative: str, within: Path | None = None) -> Path | None:
    path = (root / relative).resolve()
    return path if path.is_relative_to((within or root).resolve()) else None


@dataclass(frozen=True)
class SessionPaths:
    root: Path

    def source(self, ext: str) -> Path:
        return self.root / f"source{ext}"

    def find_source(self) -> Path | None:
        matches = sorted(self.root.glob("source.*"))
        return matches[0] if matches else None

    @property
    def live_audio_dir(self) -> Path:
        return self.root / "live" / "audio"

    def live_audio_chunk(self, seq: int) -> Path:
        return self.live_audio_dir / audio_chunk_name(seq)

    def live_frame(self, t: float, share: int) -> Path:
        return self.live_frames_dir / captured_frame_name(t, share)

    @property
    def live_frames_dir(self) -> Path:
        return self.root / "live" / "frames"

    @property
    def live_dir(self) -> Path:
        return self.root / "live"

    @property
    def live_pcm(self) -> Path:
        return self.live_dir / "audio.pcm"

    @property
    def live_transcript_windows_dir(self) -> Path:
        return self.live_dir / "transcript_windows"

    def live_transcript_window(self, index: int, start: int, end: int, rate: int) -> Path:
        return self.live_transcript_windows_dir / transcript_cache_name(index, start, end, rate)

    @property
    def live_select_batches(self) -> Path:
        return self.live_dir / "select_batches.json"

    @property
    def audio(self) -> Path:
        return self.root / "audio.wav"

    @property
    def frames_dir(self) -> Path:
        return self.root / FRAMES_DIR

    @property
    def frame_index(self) -> Path:
        return self.root / "frames.json"

    @property
    def transcript_chunks_dir(self) -> Path:
        return self.root / "transcript_chunks"

    def transcript_chunk(self, index: int, start: int, end: int, rate: int) -> Path:
        return self.transcript_chunks_dir / transcript_cache_name(index, start, end, rate)

    @property
    def transcript(self) -> Path:
        return self.root / "transcript.json"

    @property
    def moments(self) -> Path:
        return self.root / "moments.json"

    @property
    def alignment(self) -> Path:
        return self.root / "alignment.json"

    @property
    def selection_batches_dir(self) -> Path:
        return self.root / "selection_batches"

    @property
    def selection(self) -> Path:
        return self.root / "selection.json"

    @property
    def visuals_dir(self) -> Path:
        return self.root / VISUALS_DIR

    @property
    def render_cache_dir(self) -> Path:
        return self.root / "render_cache"

    @property
    def renders(self) -> Path:
        return self.root / "renders.json"

    @property
    def notes_sections_dir(self) -> Path:
        return self.root / "notes_sections"

    @property
    def notes_draft(self) -> Path:
        return self.root / "notes_draft.json"

    @property
    def synthesis_cache_dir(self) -> Path:
        return self.root / "synthesis_cache"

    @property
    def notes_synthesis(self) -> Path:
        return self.root / "notes_synthesis.json"

    @property
    def synthesis_report(self) -> Path:
        return self.root / "synthesis.json"

    @property
    def notes(self) -> Path:
        return self.root / "notes.json"

    @property
    def notes_edits(self) -> Path:
        return self.root / "notes_edits.json"

    @property
    def usage_log(self) -> Path:
        return self.root / "usage_calls.jsonl"

    @property
    def usage(self) -> Path:
        return self.root / "usage.json"

    def remove_source_crops_except(self, keep: set[str]) -> None:
        for path in self.visuals_dir.glob(f"v*{SOURCE_CROP_SUFFIX}"):
            if f"{VISUALS_DIR}/{path.name}" not in keep:
                path.unlink(missing_ok=True)

    def remove_redraws_except(self, keep: set[str]) -> None:
        for path in self.visuals_dir.glob("v*.*"):
            is_redraw = SOURCE_CROP_SUFFIX not in path.name and path.suffix in REDRAW_SUFFIXES
            if is_redraw and f"{VISUALS_DIR}/{path.name}" not in keep:
                path.unlink(missing_ok=True)

    def ensure(self) -> "SessionPaths":
        for d in (self.root, self.frames_dir, self.visuals_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def session_paths(sessions_dir: Path, session_id: str) -> SessionPaths:
    return SessionPaths(sessions_dir / session_id)


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_model(path: Path, model: BaseModel) -> None:
    write_bytes(path, model.model_dump_json(indent=2).encode())


def read_model[M: BaseModel](path: Path, model: type[M]) -> M:
    return model.model_validate_json(path.read_bytes())
