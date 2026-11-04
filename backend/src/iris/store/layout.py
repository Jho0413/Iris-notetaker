"""The on-disk layout of one session. Every stage reads and writes files named here.

sessions/<id>/
    source.<ext>          uploaded recording (upload source only)
    live/audio/<n>.webm   live audio chunks, by sequence number (browser live source only)
    live/frames/<t_ms>.jpg
                          live screen frames as captured (browser live source only)
    live/audio.pcm        live: the contiguous audio decoded so far, 16-bit mono at
                          audio_sample_rate (sample i is at i / rate seconds)
    live/transcript_windows/<i>_<start_ms>_<end_ms>.json
                          live: one transcribed window each (cache, like transcript_chunks/)
    live/transcript.json  live: Transcript committed so far (complete up to transcribed_to)
    live/frames.json      live: FrameIndex of the normalised frames fed to detection so far
    live/moments.json     live: Moments closed so far
    live/selection.json   live: Selection of the moments read so far
    live/select_batches.json
                          live: the moment ids of each batch the select lane read, so a lane
                          restarted mid-recording reads the same batches (and hits their cache)
    audio.wav             16 kHz mono
    frames/<t_ms>.jpg     sampled frames
    frames.json           FrameIndex
    transcript_chunks/<i>_<start_ms>_<end_ms>.json
                          per-chunk transcription cache (so re-runs don't re-pay)
    transcript.json       Transcript
    moments.json          Moments
    alignment.json        Alignment
    selection_batches/<hash>.json
                          cached vision decisions, keyed by model, prompt and inputs
    selection.json        Selection
    visuals/<id>.source.jpg
                          source crop of each kept figure (for rendering and verification)
    visuals/<id>.<ext>    rendered visual (svg, png or jpg)
    render_cache/<hash>.json
                          completed renders, keyed by models, prompts and inputs
    renders.json          Renders
    notes_sections/<hash>.json
                          validated writing sections, keyed by model, prompt and inputs
    notes_draft.json      NotesDraft (written by write, and by the live draft lane while recording)
    synthesis_cache/<hash>.json
                          synthesis edit plans and section rewrites, keyed by model, prompt
                          and inputs
    notes_synthesis.json  SynthesizedDraft: the notes draft after the synthesis pass
    synthesis.json        SynthesisReport: what synthesis did, or why it skipped or failed
    notes.json            Notes
    notes.md              final markdown (for export)
    usage_calls.jsonl     one UsageRecord per model call, appended by iris.llm.usage
    usage.json            SessionUsage: those records with totals, refreshed after each stage
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


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

    @property
    def live_frames_dir(self) -> Path:
        return self.root / "live" / "frames"

    # Live processing outputs (iris/live/). Each lane writes only its own; see the docstring above.
    @property
    def live_dir(self) -> Path:
        return self.root / "live"

    @property
    def live_pcm(self) -> Path:
        return self.live_dir / "audio.pcm"

    @property
    def live_transcript_windows_dir(self) -> Path:
        return self.live_dir / "transcript_windows"

    @property
    def live_transcript(self) -> Path:
        return self.live_dir / "transcript.json"

    @property
    def live_frame_index(self) -> Path:
        return self.live_dir / "frames.json"

    @property
    def live_moments(self) -> Path:
        return self.live_dir / "moments.json"

    @property
    def live_selection(self) -> Path:
        return self.live_dir / "selection.json"

    @property
    def live_select_batches(self) -> Path:
        return self.live_dir / "select_batches.json"

    @property
    def audio(self) -> Path:
        return self.root / "audio.wav"

    @property
    def frames_dir(self) -> Path:
        return self.root / "frames"

    @property
    def frame_index(self) -> Path:
        return self.root / "frames.json"

    @property
    def transcript_chunks_dir(self) -> Path:
        return self.root / "transcript_chunks"

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
    def selection(self) -> Path:
        return self.root / "selection.json"

    @property
    def visuals_dir(self) -> Path:
        return self.root / "visuals"

    @property
    def renders(self) -> Path:
        return self.root / "renders.json"

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
    def notes_md(self) -> Path:
        return self.root / "notes.md"

    # Cost tracking: written by iris.llm.usage around every model call, not by one stage.
    @property
    def usage_log(self) -> Path:
        return self.root / "usage_calls.jsonl"

    @property
    def usage(self) -> Path:
        return self.root / "usage.json"

    def ensure(self) -> "SessionPaths":
        for d in (self.root, self.frames_dir, self.visuals_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def session_paths(sessions_dir: Path, session_id: str) -> SessionPaths:
    return SessionPaths(sessions_dir / session_id)


def write_model(path: Path, model: BaseModel) -> None:
    """Write atomically, so a crash never leaves a half-written stage output."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(model.model_dump_json(indent=2))
    tmp.replace(path)


def read_model[M: BaseModel](path: Path, model: type[M]) -> M:
    return model.model_validate_json(path.read_text())
