"""Draft lane: writes the notes sections that are final while recording, ahead of stop.

Reads:  the timeline (transcript, selection, horizon)
Writes: notes_sections/ (the write stage's cache) only. No draft is shown while recording.
Emits:  NOTES_UPDATED ({"final": false, "completed_parts", "live": true}), for the Live page's
        progress line: the sections written so far.

Architecture section 1, "Life of a session: live":

  1. The evidence published before ``timeline.horizon`` is packed into parts exactly as the write
     stage packs a whole session (``write.split_parts``): a part closes when the next record
     would pass the budget, so its boundary depends only on earlier evidence (``plan``).
  2. A part that closed before the horizon is final (the timeline never adds evidence before the
     horizon: see its docstring). It is *frozen*: written once through ``write.write_section``,
     with the payload, and so the cache key in ``notes_sections/``, that the write stage computes
     for the same part at stop. Stop then reuses it without a call, so the time from stop to
     notes depends on what was still open, not on how long the session was. Nothing past the
     horizon is written live: the open parts are the write stage's at stop.
  3. A payload is tried at most ``MAX_ATTEMPTS`` times, ``RETRY_DELAY_S`` apart (each try
     retries temporary API errors itself, in ``write.write_section``); after that, or at once on
     a blocking error, the lane gives up on it until it changes (the write stage retries it at
     stop). A part that can't
     be written blocks nothing, except that while the first part waits for its retry the later
     parts wait too (their payloads carry its title). Once it is given up, they are written
     without it, as continuations.
  4. ``timeline.upstream_failed``: no more calls (nothing made can be promoted); wait for stop.
  5. On ``timeline.ending``: return. A call in flight is finished, since the write stage would
     make that very call. No call starts after stop, not even a retry of a temporary error.

After a backend restart a fresh lane re-plans from the timeline and its frozen parts hit the
cache.
"""

import asyncio
import contextlib
import logging
import math

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm import usage
from iris.llm.errors import ErrorKind, classify
from iris.models import EventType, Selection, Stage, Transcript
from iris.pipeline.context import StageContext
from iris.pipeline.stages import write
from iris.pipeline.stages.write import WritingPart, WrittenSection

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # calls per payload before the lane gives up on it until it changes
RETRY_DELAY_S = 60.0  # a failed payload is tried again no sooner than this


def plan(
    transcript: Transcript, selection: Selection, budget: int, horizon: float
) -> list[WritingPart]:
    """The parts that closed before ``horizon``: final, so they can be written now."""
    frozen, _ = write.split_parts(transcript, selection, budget, horizon)
    return frozen


class DraftLane(LiveLane):
    lane = Lane.DRAFT

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        super().__init__(ctx, timeline)
        self._sections: dict[str, WrittenSection] = {}  # by payload, written (or cached) this run
        self._failures: dict[str, int] = {}  # by payload
        self._retry_at: dict[str, float] = {}  # by payload: no retry before this
        self._due: float | None = None  # when to look again without a timeline change
        self._announced = 0  # the completed_parts NOTES_UPDATED last announced

    def _halted(self) -> bool:
        return self.timeline.upstream_failed(self.lane)

    def _stopping(self) -> bool:
        return self.timeline.ending or self._halted()

    def _evidence(self) -> tuple:
        """Changes whenever the plan may change."""
        tl = self.timeline
        return (tl.horizon, len(tl.segments), len(tl.contents), len(tl.visuals), tl.language)

    async def run(self) -> None:
        tl = self.timeline
        loop = asyncio.get_running_loop()
        while True:
            if tl.ending:
                return
            if self._halted():
                await tl.wait_until(lambda: tl.ending)
                return
            seen = self._evidence()
            transcript, selection = tl.transcript(), tl.selection()
            budget, horizon = self.ctx.settings.notes_context_chars, seen[0]
            closed = await asyncio.to_thread(plan, transcript, selection, budget, horizon)
            language = transcript.language
            self._due = None

            # Freeze the closed parts, in order. A part is written again only if its payload
            # changed (e.g. the language became known). The first part goes first: the others'
            # payloads carry its title.
            first: WrittenSection | None = None
            blocked = False
            if closed:
                payload = self._payload(closed[0], 0, language, None)
                if self._ready(payload, loop.time()) and not self._stopping():
                    await self._write(closed[0], payload, 0, language, None)
                first = self._sections.get(payload)
                blocked = first is None and not self._given_up(payload)
            frozen = [self._payload(part, i, language, first) for i, part in enumerate(closed)]
            self._announce(frozen)
            for i, part in enumerate(closed):
                if blocked or self._stopping():
                    break
                if self._ready(frozen[i], loop.time()):
                    await self._write(part, frozen[i], i, language, first)
                    self._announce(frozen)
            if self._stopping():
                continue
            await self._wait(seen, loop.time())

    def _payload(
        self, part: WritingPart, index: int, language: str | None, first: WrittenSection | None
    ) -> str:
        # A closed part always has a part after it (``later``); ``earlier`` keeps a later part a
        # continuation even when the first part could not be written.
        return write.section_payload(
            part, language=language, first=first if index else None, later=True, earlier=index > 0
        )

    def _given_up(self, payload: str) -> bool:
        return self._failures.get(payload, 0) >= MAX_ATTEMPTS

    def _due_at(self, t: float) -> None:
        self._due = t if self._due is None else min(self._due, t)

    def _ready(self, payload: str, now: float) -> bool:
        """The payload should be written now."""
        if payload in self._sections or self._given_up(payload):
            return False
        retry_at = self._retry_at.get(payload, -math.inf)
        if now < retry_at:
            self._due_at(retry_at)
            return False
        return True

    async def _wait(self, seen: tuple, now: float) -> None:
        """Until the evidence changes, stop, an upstream failure, or ``self._due``."""
        tl = self.timeline
        waiting = tl.wait_until(lambda: tl.ending or self._halted() or self._evidence() != seen)
        due = self._due
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(waiting, None if due is None else max(due - now, 0))

    async def _write(
        self,
        part: WritingPart,
        payload: str,
        index: int,
        language: str | None,
        first: WrittenSection | None,
    ) -> None:
        """One section through the write stage's cache; on failure, schedule its retry."""
        try:
            with usage.recording(self.ctx, Stage.WRITE):
                section = await write.write_section(
                    self.ctx,
                    part,
                    language=language,
                    first=first if index else None,
                    later=True,
                    earlier=index > 0,
                )
        except Exception as exc:
            failures = self._failures.get(payload, 0) + 1
            if classify(exc) is ErrorKind.BLOCKING:
                failures = MAX_ATTEMPTS  # never retried: give the payload up for this recording
            self._failures[payload] = failures
            retry_at = self._retry_at[payload] = asyncio.get_running_loop().time() + RETRY_DELAY_S
            if failures < MAX_ATTEMPTS:
                self._due_at(retry_at)
            log.warning(
                "session %s: live notes section failed (attempt %d of %d)",
                self.ctx.session_id,
                failures,
                MAX_ATTEMPTS,
                exc_info=True,
            )
            return
        self._sections[payload] = section

    def _announce(self, frozen: list[str]) -> None:
        """Emit the number of sections written so far, when it grew. (Payloads can change, e.g.
        when the language becomes known, so the count can fall back while parts are rewritten;
        the progress line keeps the most it has said.)"""
        done = sum(p in self._sections for p in frozen)
        if done <= self._announced:
            return
        self._announced = done
        self.ctx.emit(
            EventType.NOTES_UPDATED, {"final": False, "completed_parts": done, "live": True}
        )
