"""Shared data types: API models and the files that pipeline stages read and write.

Every stage's output is one of these models serialised to JSON in the session folder, so stages
can be run and tested on their own (architecture principle 5).
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# --- Sessions ----------------------------------------------------------------------------------


class SourceKind(StrEnum):
    UPLOAD = "upload"
    BROWSER_LIVE = "browser_live"
    # Later: DESKTOP = "desktop", MEETING_BOT = "meeting_bot"


class SessionStatus(StrEnum):
    CREATED = "created"
    RECORDING = "recording"  # live capture in progress
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
    ASSEMBLE = "assemble"


class SessionType(StrEnum):
    MEETING = "meeting"
    LECTURE = "lecture"
    DEMO = "demo"


class Session(BaseModel):
    id: str
    title: str
    source: SourceKind
    status: SessionStatus
    current_stage: Stage | None = None
    error: str | None = None
    duration_s: float | None = None
    created_at: datetime
    updated_at: datetime


# --- Ingest output -----------------------------------------------------------------------------


class FrameRef(BaseModel):
    """One sampled frame on disk (``frames/<t_ms>.jpg``)."""

    t: float  # seconds from session start
    path: str  # relative to the session folder


class FrameIndex(BaseModel):
    fps: float
    width: int
    height: int
    frames: list[FrameRef]


# --- Transcribe output -------------------------------------------------------------------------


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None  # open question: speaker labels (architecture section 5)


class Transcript(BaseModel):
    language: str | None = None
    segments: list[TranscriptSegment]


# --- Detect output -----------------------------------------------------------------------------


class Box(BaseModel):
    """Pixel rectangle within a frame."""

    x: int
    y: int
    w: int
    h: int


class Moment(BaseModel):
    """A stretch of time where the screen settled on one thing."""

    id: str
    start: float
    end: float
    frame: FrameRef  # representative "settled" frame
    changed_region: Box | None = None


class Moments(BaseModel):
    moments: list[Moment]


# --- Align output ------------------------------------------------------------------------------


class AlignedMoment(BaseModel):
    moment: Moment
    transcript: list[TranscriptSegment]
    ocr_text: str | None = None


class Alignment(BaseModel):
    items: list[AlignedMoment]


# --- Select output -----------------------------------------------------------------------------


class Visual(BaseModel):
    """A kept visual: a real frame, or a crop of one. Never generated (principle 1)."""

    id: str
    moment_id: str
    frame: FrameRef
    crop: Box | None = None
    caption: str
    path: str  # rendered image, relative to the session folder (``visuals/<id>.jpg``)


class Selection(BaseModel):
    visuals: list[Visual]
    rejected_moment_ids: list[str] = Field(default_factory=list)


# --- Write / assemble output -------------------------------------------------------------------


class NotesDraft(BaseModel):
    """Markdown containing ``[visual:<id>]`` markers, before images are resolved."""

    session_type: SessionType
    title: str
    markdown: str


class Notes(BaseModel):
    session_id: str
    session_type: SessionType
    title: str
    markdown: str  # markers resolved to image links
    visuals: list[Visual]


# --- Progress events ---------------------------------------------------------------------------


class EventType(StrEnum):
    STATUS = "status"
    STAGE_STARTED = "stage_started"
    STAGE_FINISHED = "stage_finished"
    TRANSCRIPT_CHUNK = "transcript_chunk"
    MOMENT_FOUND = "moment_found"
    VISUAL_SELECTED = "visual_selected"
    NOTES_UPDATED = "notes_updated"
    ERROR = "error"


class Event(BaseModel):
    type: EventType
    session_id: str
    data: dict = Field(default_factory=dict)
    at: datetime
