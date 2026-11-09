"""The live timeline: what each lane of a live session has finished, and up to when.

Lanes never call each other. Each publishes its results here and advances its watermark; lanes
downstream wait on the watermarks they depend on:

- ``transcribed_to`` (T_a): the transcript is complete up to here. Audio lane.
- ``closed_to`` (T_m): every moment that ends by here is closed. Screen lane. With no screen
  shared (an audio-only recording) no moment can open, so it follows ``transcribed_to``.
- ``selected_to`` (T_s): every closed moment that ends by here has been read. Select lane. Once
  every closed moment is read, it follows ``closed_to``.
- ``horizon``: ``min(T_a, T_s)``. Evidence before it is final, so the draft lane may write its notes
  sections ahead of making notes. With no screen it is ``transcribed_to``: notes are written from
  the speech as it is transcribed.

Selection is published in time order and never reaches back: every moment read after
``selected_to`` advanced starts at or after it, and so does every record made from it (its
elements and figures are timed within the moment). ``publish_selection`` rejects content that
breaks this. It is what makes evidence before the horizon final: a moment read later can neither
add a record before the horizon nor span a time before it.

A lane that has finished (flushed everything once notes were asked for) or failed counts as through
any time (``through``), so a lane waiting on it never waits forever. Watermarks only move forward.

A failed lane is not a finished one, though: everything downstream of it is working on
incomplete evidence. ``upstream_failed(lane)`` tells a lane so; it then stops making paid model
calls and only waits for notes to be asked for (making notes redoes that work in the pipeline
anyway).

``complete`` decides whether making notes may promote the live outputs. It covers the lanes whose
outputs are promoted (audio, screen, select); the draft lane only fills caches, so its failure
costs nothing when notes are made. Input skipped live is recorded per lane, so making notes can
re-run only what it affects.

Every method runs on the event loop; none blocks. Waiters are woken by ``asyncio.Condition``.
"""

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from iris.models import Moment, MomentContent, Selection, Transcript, TranscriptSegment, Visual


class Lane(StrEnum):
    AUDIO = "audio"
    SCREEN = "screen"
    SELECT = "select"
    DRAFT = "draft"  # writes the final notes sections ahead of making notes (live/draft.py)


# The lanes whose results each lane uses, directly or through another lane.
UPSTREAM: dict[Lane, frozenset[Lane]] = {
    Lane.AUDIO: frozenset(),
    Lane.SCREEN: frozenset(),
    Lane.SELECT: frozenset({Lane.AUDIO, Lane.SCREEN}),
    Lane.DRAFT: frozenset({Lane.AUDIO, Lane.SCREEN, Lane.SELECT}),
}
# The lanes whose outputs making notes promotes (transcript, moments, alignment, selection).
PROMOTED: frozenset[Lane] = frozenset({Lane.AUDIO, Lane.SCREEN, Lane.SELECT})


@dataclass
class LiveTimeline:
    # Audio lane
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None
    transcribed_to: float = 0.0
    # Screen lane
    moments: list[Moment] = field(default_factory=list)
    closed_to: float = 0.0
    # Select lane
    contents: list[MomentContent] = field(default_factory=list)
    visuals: list[Visual] = field(default_factory=list)
    rejected_moment_ids: list[str] = field(default_factory=list)
    selected_to: float = 0.0
    # Lifecycle
    ending: bool = False  # notes asked for: lanes flush what they hold, then return
    finished: set[Lane] = field(default_factory=set)
    failed: dict[Lane, str] = field(default_factory=dict)
    absent: set[Lane] = field(
        default_factory=set
    )  # not run by this processor: done, never finished
    # Lanes that left some captured input unused (a late frame, an undecodable chunk); their
    # outputs miss it, so making notes re-runs from the stage they replace.
    skipped_input: set[Lane] = field(default_factory=set)
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    # --- Reading ---------------------------------------------------------------------------------

    def done(self, lane: Lane) -> bool:
        """The lane will publish nothing more (finished, failed or absent)."""
        return lane in self.finished or lane in self.failed or lane in self.absent

    def _through(self, lane: Lane, watermark: float, t: float) -> bool:
        return self.done(lane) or watermark >= t

    def transcribed_through(self, t: float) -> bool:
        return self._through(Lane.AUDIO, self.transcribed_to, t)

    def closed_through(self, t: float) -> bool:
        return self._through(Lane.SCREEN, self.closed_to, t)

    def selected_through(self, t: float) -> bool:
        return self._through(Lane.SELECT, self.selected_to, t)

    @property
    def horizon(self) -> float:
        """Evidence (speech, screen content) before this time is final. ``inf`` once both the
        audio and select lanes are done."""
        a = math.inf if self.done(Lane.AUDIO) else self.transcribed_to
        s = math.inf if self.done(Lane.SELECT) else self.selected_to
        return min(a, s)

    def upstream_failed(self, lane: Lane) -> bool:
        """A lane this one depends on failed: its evidence is incomplete from now on."""
        return any(up in self.failed for up in UPSTREAM[lane])

    @property
    def complete(self) -> bool:
        """The promoted lanes all finished, none failed, and none skipped captured input, so the
        live transcript, moments and selection can replace the pipeline's own when notes are
        made."""
        return (
            self.finished >= PROMOTED
            and not (PROMOTED & self.failed.keys())
            and not (PROMOTED & self.skipped_input)
        )

    def transcript(self) -> Transcript:
        return Transcript(language=self.language, segments=list(self.segments))

    def selection(self) -> Selection:
        return Selection(
            visuals=list(self.visuals),
            rejected_moment_ids=list(self.rejected_moment_ids),
            contents=list(self.contents),
        )

    async def wait_until(self, predicate: Callable[[], bool]) -> None:
        """Return once ``predicate()`` holds; it is re-checked whenever the timeline changes."""
        async with self._changed:
            await self._changed.wait_for(predicate)

    # --- Publishing (each lane calls only its own) ------------------------------------------------

    async def _notify(self) -> None:
        async with self._changed:
            self._changed.notify_all()

    async def publish_transcript(
        self,
        segments: Sequence[TranscriptSegment],
        transcribed_to: float,
        language: str | None = None,
    ) -> None:
        """Audio lane: append segments (in time order, after those already published)."""
        if self.segments and segments and segments[0].start < self.segments[-1].start:
            raise ValueError("transcript segments must be published in time order")
        self.segments.extend(segments)
        if language and not self.language:
            self.language = language
        self.transcribed_to = max(self.transcribed_to, transcribed_to)
        await self._notify()

    async def publish_moment(self, moment: Moment) -> None:
        """Screen lane: a moment closed. Moments close in order, so ``closed_to`` is its end."""
        if self.moments and moment.start < self.moments[-1].start:
            raise ValueError("moments must be published in time order")
        self.moments.append(moment)
        self.closed_to = max(self.closed_to, moment.end)
        await self._notify()

    async def advance_closed_to(self, t: float) -> None:
        """Screen lane: no moment is open and none can start before ``t`` (no screen is shared).
        Only then: a later moment starting before ``t`` would break the time order selection
        relies on (see the module docstring)."""
        self.closed_to = max(self.closed_to, t)
        await self._notify()

    async def publish_selection(
        self,
        contents: Sequence[MomentContent],
        visuals: Sequence[Visual],
        rejected_moment_ids: Sequence[str],
        selected_to: float,
    ) -> None:
        """Select lane: the result of reading one batch of moments, which all start at or after
        the previous ``selected_to`` (see the module docstring)."""
        early = [c.moment_id for c in contents if c.start < self.selected_to]
        if early:
            raise ValueError(
                f"selection must be published in time order: {', '.join(early)} start before "
                f"{self.selected_to:g} s, which is already selected"
            )
        self.contents.extend(contents)
        self.visuals.extend(visuals)
        self.rejected_moment_ids.extend(rejected_moment_ids)
        self.selected_to = max(self.selected_to, selected_to)
        await self._notify()

    async def advance_selected_to(self, t: float) -> None:
        """Select lane: every closed moment is read, so every one ending by ``t`` (at most
        ``closed_to``) is."""
        self.selected_to = max(self.selected_to, min(t, self.closed_to))
        await self._notify()

    async def mark_skipped_input(self, lane: Lane) -> None:
        """Any lane: some captured input was not used live, so its outputs are incomplete."""
        self.skipped_input.add(lane)
        await self._notify()

    # --- Lifecycle (the processor calls these) ----------------------------------------------------

    async def request_end(self) -> None:
        self.ending = True
        await self._notify()

    async def finish_lane(self, lane: Lane) -> None:
        if lane not in self.failed:
            self.finished.add(lane)
            await self._notify()

    async def fail_lane(self, lane: Lane, message: str) -> None:
        self.finished.discard(lane)
        self.failed[lane] = message
        await self._notify()
