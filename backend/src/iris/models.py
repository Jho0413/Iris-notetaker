"""Shared data types: API models and the files that pipeline stages read and write.

Every stage's output is one of these models serialised to JSON in the session folder, so stages
can be run and tested on their own (architecture principle 5).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

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
    RENDER = "render"
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


class Build(BaseModel):
    """One settled addition within a moment: a line written, a stroke drawn, a bullet shown."""

    t: float  # when the addition settled
    frame: FrameRef  # the screen just after the addition
    region: Box | None = None  # what was added (None: most of the screen, or the first state)


class Moment(BaseModel):
    """A stretch of time where the screen settled on one thing."""

    id: str
    start: float
    end: float
    frame: FrameRef  # representative "settled" frame
    changed_region: Box | None = None
    builds: list[Build] = Field(default_factory=list)  # the moment's content, in the order added


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


ElementKind = Literal["math", "text", "table", "code", "figure"]
RenderAs = Literal["svg", "image", "screenshot"]


class BoardElement(BaseModel):
    """One piece of on-screen content, read by the vision model.

    ``content`` is LaTeX for math, Markdown for text and tables, source for code, and for figures a
    complete description of what is drawn (every label, value and relationship).
    """

    kind: ElementKind
    content: str
    t: float | None = None  # when it was written or shown, if known
    visual_id: str | None = None  # figures only: the visual rebuilt from it


class MomentContent(BaseModel):
    moment_id: str
    start: float
    end: float
    elements: list[BoardElement]


class Visual(BaseModel):
    """A figure from the session, rebuilt as a professional visual (principle 1).

    Select crops the source region (``source_path``); render produces ``path``.
    """

    id: str
    moment_id: str
    frame: FrameRef
    crop: Box | None = None
    caption: str
    path: str  # final visual, relative to the session folder
    description: str = ""  # what the figure shows; the brief for rendering and verification
    render_as: RenderAs = "screenshot"
    source_path: str | None = None  # the source crop (kept on disk, never shown in notes)


class Selection(BaseModel):
    visuals: list[Visual]
    rejected_moment_ids: list[str] = Field(default_factory=list)
    contents: list[MomentContent] = Field(default_factory=list)


# --- Render output -----------------------------------------------------------------------------


class RenderedVisual(BaseModel):
    id: str
    path: str  # final visual, relative to the session folder
    render_as: RenderAs  # the renderer that produced it (may differ from the requested one)
    verified: bool  # passed the check against the source; False only for screenshot fallbacks
    attempts: int
    problems: list[str] = Field(default_factory=list)  # from the last failed check, if any


class Renders(BaseModel):
    visuals: list[RenderedVisual]


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
    VISUAL_RENDERED = "visual_rendered"
    NOTES_UPDATED = "notes_updated"
    ERROR = "error"


class Event(BaseModel):
    type: EventType
    session_id: str
    data: dict = Field(default_factory=dict)
    at: datetime
