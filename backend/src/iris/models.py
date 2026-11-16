from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class SourceKind(StrEnum):
    UPLOAD = "upload"
    BROWSER_LIVE = "browser_live"


class SessionStatus(StrEnum):
    CREATED = "created"
    RECORDING = "recording"
    STOPPED = "stopped"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Stage(StrEnum):
    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    DETECT = "detect"
    ALIGN = "align"
    SELECT = "select"
    WRITE = "write"
    RENDER = "render"
    ASSEMBLE = "assemble"
    SYNTHESIZE = "synthesize"


class SessionType(StrEnum):
    MEETING = "meeting"
    LECTURE = "lecture"
    DEMO = "demo"


GENERIC_FAILURE = "Iris couldn't finish making the notes. Try again; finished steps are kept."
UNREADABLE_UPLOAD = "Iris couldn't read this file. Upload an audio or video recording."
UPLOAD_WITHOUT_AUDIO = "This recording has no sound. Upload a recording with speech."
LIVE_WITHOUT_AUDIO = "No audio was recorded. Check that the microphone works, then record again."
UNREADABLE_LIVE_AUDIO = "The recorded audio couldn't be read. Record the session again."
NOTHING_TO_WRITE = "Iris found no speech or screen content in this recording to make notes from."
INTERRUPTED_RUN = (
    "Iris was closed before these notes were finished. Try again; finished steps are kept."
)

FailureMessage = Literal[
    "Iris couldn't finish making the notes. Try again; finished steps are kept.",
    "Iris couldn't read this file. Upload an audio or video recording.",
    "This recording has no sound. Upload a recording with speech.",
    "No audio was recorded. Check that the microphone works, then record again.",
    "The recorded audio couldn't be read. Record the session again.",
    "Iris found no speech or screen content in this recording to make notes from.",
    "Iris was closed before these notes were finished. Try again; finished steps are kept.",
]


class FailureKind(StrEnum):
    INPUT = "input"
    GENERAL = "general"


class Session(BaseModel):
    """A session as the API shows it. The raw error of a failed run is not part of it: it is
    stored for developers only (``SessionStore.failure_detail``, ``iris show``)."""

    id: str
    title: str
    source: SourceKind
    status: SessionStatus
    current_stage: Stage | None = None
    failure_message: FailureMessage | None = None
    failure_kind: FailureKind | None = None
    duration_s: float | None = None
    created_at: datetime
    updated_at: datetime
    cost_usd: float | None = None
    cost_partial: bool = False


class FrameRef(BaseModel):
    """One sampled frame on disk (``frames/<t_ms>.jpg``)."""

    t: float
    path: str


class FrameIndex(BaseModel):
    fps: float
    width: int
    height: int
    frames: list[FrameRef]
    share_starts: list[float] = []


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None


class Transcript(BaseModel):
    language: str | None = None
    segments: list[TranscriptSegment]


class Box(BaseModel):
    """Pixel rectangle within a frame."""

    x: int
    y: int
    w: int
    h: int


class Build(BaseModel):
    t: float
    frame: FrameRef
    region: Box | None = None


class Moment(BaseModel):
    id: str
    start: float
    end: float
    frame: FrameRef
    changed_region: Box | None = None
    builds: list[Build] = Field(default_factory=list)


class Moments(BaseModel):
    moments: list[Moment]


class AlignedMoment(BaseModel):
    moment: Moment
    transcript: list[TranscriptSegment]
    ocr_text: str | None = None


class Alignment(BaseModel):
    items: list[AlignedMoment]


ElementKind = Literal["math", "text", "table", "code", "figure"]
RenderAs = Literal["svg", "image", "screenshot"]


class BoardElement(BaseModel):
    kind: ElementKind
    content: str
    t: float | None = None
    visual_id: str | None = None


class MomentContent(BaseModel):
    moment_id: str
    start: float
    end: float
    elements: list[BoardElement]


class Visual(BaseModel):
    id: str
    moment_id: str
    frame: FrameRef
    crop: Box | None = None
    caption: str
    path: str
    description: str = ""
    render_as: RenderAs = "screenshot"
    source_path: str | None = None


class Selection(BaseModel):
    visuals: list[Visual]
    rejected_moment_ids: list[str] = Field(default_factory=list)
    contents: list[MomentContent] = Field(default_factory=list)


class RenderedVisual(BaseModel):
    id: str
    path: str
    render_as: RenderAs
    verified: bool
    attempts: int
    problems: list[str] = Field(default_factory=list)


class Renders(BaseModel):
    visuals: list[RenderedVisual]


class NotesDraft(BaseModel):
    session_type: SessionType
    title: str
    markdown: str
    parts: int | None = None


class NotesVisual(Visual):
    """A visual as the finished notes show it: ``path`` is its render, and ``verified`` says how
    it was made (``RenderedVisual.verified``): False for the source crop shown because no redraw
    passed (or the redraw failed); None if it was never rendered."""

    verified: bool | None = None


class NotesEdited(BaseModel):
    """Set by the API when the user's edits replace the generated notes in a response."""

    saved_at: datetime
    stale: bool


class Notes(BaseModel):
    session_id: str
    session_type: SessionType
    title: str
    markdown: str
    visuals: list[NotesVisual]
    format_version: int = 1
    generated_hash: str | None = None
    edited: NotesEdited | None = None


class NotesEdits(BaseModel):
    markdown: str
    based_on: str
    saved_at: datetime


RepeatKind = Literal["slide", "figure"]


class Repeat(BaseModel):
    kind: RepeatKind
    ids: list[str]


EditKind = Literal["merge", "correct", "drop_repeat", "dedupe_figure"]


class SynthesisEdit(BaseModel):
    """One consolidation of the notes, planned across the whole session.

    Sections are indexes into the notes split at their top-level headings. ``sections`` are
    rewritten; ``context_sections`` are only read. Only ``dedupe_figure`` removes visual markers
    (``drop_visual_ids``, pointing readers at ``keep_visual_id`` instead).
    """

    kind: EditKind
    sections: list[int]
    context_sections: list[int]
    instruction: str
    keep_visual_id: str | None
    drop_visual_ids: list[str]


SynthesisStatus = Literal["applied", "unchanged", "skipped", "failed"]


class SynthesisReport(BaseModel):
    status: SynthesisStatus
    reason: str | None = None
    repeats: list[Repeat] = Field(default_factory=list)
    edits: list[SynthesisEdit] = Field(default_factory=list)
    sections: int = 0
    rewritten_sections: list[int] = Field(default_factory=list)
    failed_sections: list[int] = Field(default_factory=list)


class SynthesizedDraft(BaseModel):
    based_on: NotesDraft
    visual_ids: list[str]
    draft: NotesDraft


class EventType(StrEnum):
    STATUS = "status"
    STAGE_STARTED = "stage_started"
    STAGE_FINISHED = "stage_finished"
    TRANSCRIPT_CHUNK = "transcript_chunk"
    MOMENT_FOUND = "moment_found"
    VISUAL_SELECTED = "visual_selected"
    VISUAL_RENDERED = "visual_rendered"
    NOTES_UPDATED = "notes_updated"
    ERROR = "error"


class Event(BaseModel):
    type: EventType
    session_id: str
    data: dict = Field(default_factory=dict)
    at: datetime


class UsageCounts(BaseModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    input_image_tokens: int = 0
    input_audio_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    output_image_tokens: int = 0
    images: int = 0
    audio_seconds: float = 0


class UsageRecordBase(UsageCounts):
    at: datetime
    stage: Stage | None = None
    purpose: str
    operation: str
    model: str
    latency_s: float
    cost_usd: float | None = None
    unpriced: str | None = None


class UsageRecord(UsageRecordBase):
    error: str | None = None


class UsageRecordView(UsageRecordBase):
    """A model call as the API serves it: whether it failed, never the error text."""

    failed: bool = False


class UsageTotals(UsageCounts):
    calls: int = 0
    unpriced_calls: int = 0
    failed_calls: int = 0
    cost_usd: float = 0
    latency_s: float = 0


class SessionUsageTotals(BaseModel):
    prices_checked: str
    total: UsageTotals = UsageTotals()
    by_stage: dict[str, UsageTotals] = {}
    by_model: dict[str, UsageTotals] = {}
    by_purpose: dict[str, UsageTotals] = {}


class SessionUsage(SessionUsageTotals):
    records: list[UsageRecord] = []

    def view(self) -> "SessionUsageView":
        return SessionUsageView(
            **self.model_dump(exclude={"records"}),
            records=[
                UsageRecordView(**r.model_dump(exclude={"error"}), failed=r.error is not None)
                for r in self.records
            ],
        )


class SessionUsageView(SessionUsageTotals):
    """``SessionUsage`` as the API serves it (``GET /sessions/{id}/usage``)."""

    records: list[UsageRecordView] = []
