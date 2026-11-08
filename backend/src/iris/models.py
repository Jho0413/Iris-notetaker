"""Shared data types: API models and the files that pipeline stages read and write.

Every stage's output is one of these models serialised to JSON in the session folder, so stages
can be run and tested on their own (docs/architecture/overview.md, principle 5).
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
    # Live capture ended without notes (its page went away, or the app quit or crashed): nothing
    # more is accepted, and notes are made only when asked (``POST /live/{id}/end``).
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
    SYNTHESIZE = "synthesize"  # consolidates the assembled notes, then assembles them again


class SessionType(StrEnum):
    MEETING = "meeting"
    LECTURE = "lecture"
    DEMO = "demo"


# What the user is told when a session fails: only these messages, which we wrote
# (docs/architecture/errors.md). Exception text, provider and model names, status codes and
# model output never reach the API; the raw error is kept for developers (``iris show``).
GENERIC_FAILURE = "Iris couldn't finish making the notes. Try again; finished steps are kept."
UNREADABLE_UPLOAD = "Iris couldn't read this file. Upload an audio or video recording."
UPLOAD_WITHOUT_AUDIO = "This recording has no sound. Upload a recording with speech."
LIVE_WITHOUT_AUDIO = "No audio was recorded. Check that the microphone works, then record again."
UNREADABLE_LIVE_AUDIO = "The recorded audio couldn't be read. Record the session again."
NOTHING_TO_WRITE = "Iris found no speech or screen content in this recording to make notes from."
# A run cut off by Iris stopping (quit, restart, a crash), found when Iris next starts.
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
    INPUT = "input"  # a known problem with the recording: upload or record again
    GENERAL = "general"  # anything else: try again


class Session(BaseModel):
    """A session as the API shows it. The raw error of a failed run is not part of it: it is
    stored for developers only (``SessionStore.failure_detail``, ``iris show``)."""

    id: str
    title: str
    source: SourceKind
    status: SessionStatus
    current_stage: Stage | None = None  # while failed: the stage it stopped at
    failure_message: FailureMessage | None = None  # while failed: what to tell the user
    failure_kind: FailureKind | None = None
    duration_s: float | None = None
    created_at: datetime
    updated_at: datetime
    # Model cost so far, across all runs, from the call log (``iris.llm.usage.running_total``).
    # None: no model calls recorded yet. Partial: some calls are unpriced (missing from the price
    # table), so ``cost_usd`` is the priced part only, a lower bound.
    cost_usd: float | None = None
    cost_partial: bool = False


# --- Ingest output -----------------------------------------------------------------------------


class FrameRef(BaseModel):
    """One sampled frame on disk (``frames/<t_ms>.jpg``)."""

    t: float  # seconds from session start
    path: str  # relative to the session folder


class FrameIndex(BaseModel):
    fps: float
    # The first frames' size. A live recording can share the screen several times, each share
    # with its own source and size (``share_starts``).
    width: int
    height: int
    frames: list[FrameRef]
    # Live recordings: where each share after the first starts (its first frame's ``t``, in time
    # order). A moment ends where its share does, and each share's frames are compared only with
    # each other. Uploads are one share.
    share_starts: list[float] = []


# --- Transcribe output -------------------------------------------------------------------------


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None  # open question: speaker labels (docs/architecture/overview.md)


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
    source_path: str | None = None  # the source crop; notes show it only as the render fallback


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
    # For debugging, never served: the last failed check's problems, or the API error that made
    # it fall back to the source crop.
    problems: list[str] = Field(default_factory=list)


class Renders(BaseModel):
    visuals: list[RenderedVisual]


# --- Write / assemble output -------------------------------------------------------------------


class NotesDraft(BaseModel):
    """The notes as far as they are written, before assembly.

    Stored in notes_draft.json with ``[visual:<id>]`` markers, written by the write stage. It is
    never shown: assemble resolves the markers into ``notes.json``.
    """

    session_type: SessionType
    title: str
    markdown: str
    # Writing parts the notes were planned in (None: unknown, e.g. a draft from an older version).
    # Synthesis makes no edit-plan call for one-part notes: the writer saw everything at once.
    parts: int | None = None


class NotesVisual(Visual):
    """A visual as the finished notes show it: ``path`` is its render, and ``verified`` says how
    it was made (``RenderedVisual.verified``): False for the source crop shown because no redraw
    passed (or the redraw failed); None if it was never rendered."""

    verified: bool | None = None


class NotesEdited(BaseModel):
    """Set by the API when the user's edits replace the generated notes in a response."""

    saved_at: datetime
    # True when Iris made the notes again after the edits started (a re-run): the generated
    # notes they were based on are no longer the current ones.
    stale: bool


class Notes(BaseModel):
    session_id: str
    session_type: SessionType
    title: str
    markdown: str  # the notes format (iris.notes_format); figures as ::figure{#<id>}
    visuals: list[NotesVisual]
    # 2: boxes, steps and figures by id. Missing means 1, notes written before that format with
    # bold labels and image links, which readers upgrade (iris.notes_format.upgraded).
    format_version: int = 1
    # Set by the API only, never stored by a stage. ``generated_hash`` names the generated notes
    # (send it back as ``based_on`` when saving edits); ``edited`` is set when ``markdown`` is the
    # user's edited version rather than the generated one.
    generated_hash: str | None = None
    edited: NotesEdited | None = None


class NotesEdits(BaseModel):
    """The user's edited notes (notes_edits.json): the latest save only. Written by the API,
    never by a stage, so a re-run keeps them."""

    markdown: str
    based_on: str  # generated_hash of the generated notes the edits started from
    saved_at: datetime


# --- Synthesize output -------------------------------------------------------------------------

RepeatKind = Literal["slide", "figure"]


class Repeat(BaseModel):
    """Items the local sweep found to look the same: a slide returned to, or a figure picked twice.

    ``ids`` are moment ids (slides) or visual ids (figures), in chronological order.
    """

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
    """What the synthesis pass did (``synthesis.json``). A failure here never fails the session."""

    status: SynthesisStatus
    reason: str | None = None  # why it was skipped, changed nothing or failed
    repeats: list[Repeat] = Field(default_factory=list)
    edits: list[SynthesisEdit] = Field(default_factory=list)  # validated edits that were applied
    sections: int = 0  # sections the notes were split into
    rewritten_sections: list[int] = Field(default_factory=list)
    failed_sections: list[int] = Field(default_factory=list)  # rewrites rejected; left as written


class SynthesizedDraft(BaseModel):
    """The notes after synthesis (``notes_synthesis.json``) and what they were made from.

    Assemble uses ``draft`` only while ``based_on`` equals the current notes draft and
    ``visual_ids`` the current selection, so a re-run never shows a stale synthesis.
    """

    based_on: NotesDraft
    visual_ids: list[str]  # sorted ids of the selected visuals
    draft: NotesDraft


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


# --- Usage (cost tracking) ---------------------------------------------------------------------


class UsageRecordBase(BaseModel):
    """One model call. Token counts are as billed; breakdowns are subsets of their totals."""

    at: datetime
    stage: Stage | None = None
    purpose: str  # transcription, screen_reading, notes, svg, image, figure_check, ...
    operation: str  # SDK method, e.g. "chat.completions.parse"
    model: str
    latency_s: float
    input_tokens: int = 0  # all input, including cached, image and audio tokens
    cached_input_tokens: int = 0
    input_image_tokens: int = 0
    input_audio_tokens: int = 0
    output_tokens: int = 0  # all output, including reasoning and image tokens
    reasoning_tokens: int = 0
    output_image_tokens: int = 0
    images: int = 0  # images returned
    audio_seconds: float = 0  # billed seconds of audio (duration-billed models)
    cost_usd: float | None = None  # None: unpriced (see ``unpriced``)
    unpriced: str | None = None  # why no cost could be computed


class UsageRecord(UsageRecordBase):
    """A model call as recorded in usage.json, for developers (``iris cost``)."""

    error: str | None = None  # the call failed (no usage reported): what it failed on


class UsageRecordView(UsageRecordBase):
    """A model call as the API serves it: whether it failed, never the error text."""

    failed: bool = False


class UsageTotals(BaseModel):
    calls: int = 0
    unpriced_calls: int = 0
    failed_calls: int = 0
    cost_usd: float = 0  # priced calls only; unpriced calls are counted in ``unpriced_calls``
    latency_s: float = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    input_image_tokens: int = 0
    input_audio_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    output_image_tokens: int = 0
    images: int = 0
    audio_seconds: float = 0


class SessionUsageTotals(BaseModel):
    prices_checked: str  # date the price table was checked against OpenAI's pricing page
    total: UsageTotals = UsageTotals()
    by_stage: dict[str, UsageTotals] = {}
    by_model: dict[str, UsageTotals] = {}
    by_purpose: dict[str, UsageTotals] = {}


class SessionUsage(SessionUsageTotals):
    """Every model call made for a session, across all runs, with totals (``usage.json``).

    Cache hits make no call, so a re-run that reuses cached work adds nothing here.
    """

    records: list[UsageRecord] = []

    def view(self) -> "SessionUsageView":
        """What the API serves: the same, without failed calls' error text."""
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
