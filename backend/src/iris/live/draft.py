"""Draft lane: the timeline -> a running notes draft, while recording.

Reads:  the timeline (transcript, selection, horizon)
Writes: notes_sections/ (shared cache), notes_draft.json
Emits:  NOTES_UPDATED ({"final": false, "completed_parts", "total_parts", "live": true}).

Architecture section 1, "Life of a session: live":

  1. All the evidence published so far is packed into parts exactly as the write stage packs a
     whole session (``write.split_parts``): a part closes when the next record would pass the
     budget, so its boundary depends only on earlier evidence (``plan``).
  2. A part that closed before ``timeline.horizon`` is final (the timeline never adds evidence
     before the horizon: see its docstring). It is *frozen*: written at once through
     ``write.write_section``, with the payload, and so the cache key in ``notes_sections/``, that
     the write stage computes for the same part at stop. Stop then reuses it without a call.
  3. The parts after them are the *tail*. It covers everything published so far, speech the
     select lane has not caught up with included, so a long open moment (a slide shown for 20
     minutes) doesn't leave the draft empty or stale. It is rewritten at most every
     ``live_draft_refresh_s``, and only when its evidence changed; a tail part whose payload did
     not change is not written again. Its last part is written as the session's last, so if
     nothing follows it, stop reuses it too.
  4. ``notes_draft.json`` holds the frozen sections plus the tail. When parts freeze, the tail
     written earlier stays only if those parts froze unchanged; otherwise (it overlaps them) it
     is dropped until the next tail rewrite.
  5. A payload is tried at most ``MAX_ATTEMPTS`` times; after that the lane gives up on it until
     it changes (the write stage retries it at stop). Retries come no sooner than
     ``live_draft_refresh_s`` later. A part that can't be written blocks nothing, except that
     while the first part waits for its retry the later parts wait too (their payloads carry its
     title). Once it is given up, they are written without it, as continuations.
  6. ``timeline.upstream_failed``: no more calls (nothing made can be promoted); wait for stop.
  7. On ``timeline.ending``: return. A tail call in flight is cancelled (stop rewrites the tail
     anyway); a frozen part's call in flight is finished, since the write stage would make it.
     No call starts after stop.

After a backend restart a fresh lane re-plans from the timeline and its frozen parts hit the
cache.
"""

import asyncio
import contextlib
import logging
import math
from collections.abc import Awaitable
from dataclasses import dataclass

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm import usage
from iris.models import EventType, Selection, Stage, Transcript
from iris.pipeline.context import StageContext
from iris.pipeline.stages import write
from iris.pipeline.stages.write import WritingPart, WrittenSection
from iris.store.layout import write_model

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # calls per payload before the lane gives up on it until it changes


def plan(
    transcript: Transcript, selection: Selection, budget: int, horizon: float
) -> tuple[list[WritingPart], list[WritingPart]]:
    """The parts that closed before ``horizon`` (final) and the tail: the parts after them, over
    all the evidence published so far."""
    frozen, _ = write.split_parts(transcript, selection, budget, horizon)
    closed, last = write.split_parts(transcript, selection, budget)
    # Packing is online, so the parts that closed before the horizon are the first parts here.
    return frozen, [*closed[len(frozen) :], *([last] if last else [])]


@dataclass(frozen=True)
class _Tail:
    start: int  # the index of its first part (the parts frozen when it was written)
    parts: list[tuple[str, WrittenSection | None]]  # (payload, section if written), in order


class DraftLane(LiveLane):
    lane = Lane.DRAFT

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        super().__init__(ctx, timeline)
        self._sections: dict[str, WrittenSection] = {}  # by payload, written (or cached) this run
        self._failures: dict[str, int] = {}  # by payload
        self._retry_at: dict[str, float] = {}  # by payload: no frozen-part retry before this
        self._due: float | None = None  # when to look again without a timeline change
        # What NOTES_UPDATED last announced: the payloads in notes_draft.json and the counts.
        self._shown: tuple[list[str], int, int] | None = None

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
        refresh = self.ctx.settings.live_draft_refresh_s
        loop = asyncio.get_running_loop()
        tail: _Tail | None = None
        tail_key: tuple[str, ...] | None = None  # the tail evidence last written
        next_tail_at = -math.inf  # the tail may not be rewritten before this
        while True:
            if tl.ending:
                return
            if self._halted():
                await tl.wait_until(lambda: tl.ending)
                return
            seen = self._evidence()
            transcript, selection = tl.transcript(), tl.selection()
            budget, horizon = self.ctx.settings.notes_context_chars, seen[0]
            closed, rest = await asyncio.to_thread(plan, transcript, selection, budget, horizon)
            language = transcript.language
            total = len(closed) + len(rest)
            self._due = None

            # Freeze the closed parts, at once and in order. A part is written again only if its
            # payload changed (e.g. the language became known). The first part goes first: the
            # others' payloads carry its title.
            first: WrittenSection | None = None
            blocked = False
            if closed:
                payload = self._payload(closed[0], 0, language, None, later=True)
                if self._ready(payload, loop.time()) and not self._stopping():
                    await self._write(closed[0], payload, 0, language, None, later=True)
                first = self._sections.get(payload)
                blocked = first is None and not self._given_up(payload)
            frozen = [
                self._payload(part, i, language, first, later=True) for i, part in enumerate(closed)
            ]
            self._publish(frozen, tail, total)
            for i, part in enumerate(closed):
                if blocked or self._stopping():
                    break
                if self._ready(frozen[i], loop.time()):
                    await self._write(part, frozen[i], i, language, first, later=True)
                    self._publish(frozen, tail, total)
            if self._stopping():
                continue

            # Rewrite the tail when its evidence changed, at most every `refresh`.
            if not blocked and rest:
                key = tuple(
                    self._payload(part, len(closed) + j, language, first, j < len(rest) - 1)
                    for j, part in enumerate(rest)
                )
                if key != tail_key and loop.time() < next_tail_at:
                    self._due_at(next_tail_at)
                elif key != tail_key:
                    tail_key, next_tail_at = key, loop.time() + refresh
                    written, settled = await self._write_tail(len(closed), rest, language, first)
                    if written is not None:
                        tail = _Tail(len(closed), written)
                        self._publish(frozen, tail, total)
                    if not settled:
                        tail_key = None  # a part failed: try it again at the next refresh
                    continue  # re-plan: the timeline may have moved during the calls

            await self._wait(seen, loop.time())

    def _payload(
        self,
        part: WritingPart,
        index: int,
        language: str | None,
        first: WrittenSection | None,
        later: bool,
    ) -> str:
        return write.section_payload(
            part, language=language, first=first if index else None, later=later, earlier=index > 0
        )

    def _given_up(self, payload: str) -> bool:
        return self._failures.get(payload, 0) >= MAX_ATTEMPTS

    def _due_at(self, t: float) -> None:
        self._due = t if self._due is None else min(self._due, t)

    def _ready(self, payload: str, now: float) -> bool:
        """A frozen part's payload should be written now."""
        if payload in self._sections or self._given_up(payload):
            return False
        retry_at = self._retry_at.get(payload, -math.inf)
        if now < retry_at:
            self._due_at(retry_at)
            return False
        return True

    async def _write_tail(
        self,
        start: int,
        parts: list[WritingPart],
        language: str | None,
        first: WrittenSection | None,
    ) -> tuple[list[tuple[str, WrittenSection | None]] | None, bool]:
        """Write the tail's parts whose payload has no section yet. Returns the parts (``None``
        if stopped meanwhile) and whether no part is left to retry."""
        written: list[tuple[str, WrittenSection | None]] = []
        settled = True
        for j, part in enumerate(parts):
            index, later = start + j, j < len(parts) - 1
            payload = self._payload(part, index, language, first, later)
            section = self._sections.get(payload)
            if section is None and not self._given_up(payload):
                if self._stopping():
                    return None, True
                call = self._write(part, payload, index, language, first, later)
                section = await self._until_ending(call)
                if self._stopping():
                    return None, True
                settled = settled and (section is not None or self._given_up(payload))
            written.append((payload, section))
            if index == 0:
                first = section
                if first is None and not self._given_up(payload):
                    break  # the later parts carry its title: wait for its retry
        return written, settled

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
        later: bool,
    ) -> WrittenSection | None:
        """One section through the write stage's cache; ``None`` if the call failed."""
        try:
            with usage.recording(self.ctx, Stage.WRITE):
                section = await write.write_section(
                    self.ctx,
                    part,
                    language=language,
                    first=first if index else None,
                    later=later,
                    earlier=index > 0,
                )
        except Exception:
            failures = self._failures[payload] = self._failures.get(payload, 0) + 1
            retry_at = self._retry_at[payload] = (
                asyncio.get_running_loop().time() + self.ctx.settings.live_draft_refresh_s
            )
            if failures < MAX_ATTEMPTS:
                self._due_at(retry_at)
            log.warning(
                "session %s: live draft section failed (attempt %d of %d)",
                self.ctx.session_id,
                failures,
                MAX_ATTEMPTS,
                exc_info=True,
            )
            return None
        self._sections[payload] = section
        return section

    async def _until_ending[T](self, call: Awaitable[T | None]) -> T | None:
        """``call``'s result, or ``None`` if stop is requested first (the call is cancelled)."""
        tl = self.timeline
        task = asyncio.ensure_future(call)
        ending = asyncio.ensure_future(tl.wait_until(lambda: tl.ending))
        try:
            await asyncio.wait({task, ending}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (task, ending):
                if not t.done():
                    t.cancel()
            await asyncio.gather(task, ending, return_exceptions=True)
        if task.cancelled() or task.exception() is not None:
            return None
        return task.result()

    def _publish(self, frozen: list[str], tail: _Tail | None, total: int) -> None:
        """Write the frozen sections plus the tail, if that changes the draft or its counts."""
        done = [p for p in frozen if p in self._sections]
        shown = done + [p for p, s in _visible(tail, frozen) if s is not None]
        state = (shown, len(done), total)
        if not shown or state == self._shown:
            return
        self._shown = state
        sections = [self._sections[p] for p in done] + [
            s for _, s in _visible(tail, frozen) if s is not None
        ]
        write_model(self.ctx.paths.notes_draft, write.draft_of(sections))
        self.ctx.emit(
            EventType.NOTES_UPDATED,
            {"final": False, "completed_parts": len(done), "total_parts": total, "live": True},
        )


def _visible(tail: _Tail | None, frozen: list[str]) -> list[tuple[str, WrittenSection | None]]:
    """The tail parts not covered by frozen parts. If parts froze since the tail was written and
    they are not its first parts unchanged, the tail overlaps them: none of it is shown."""
    if tail is None:
        return []
    covered = len(frozen) - tail.start
    if covered <= 0:
        return tail.parts
    if [p for p, _ in tail.parts[:covered]] != frozen[tail.start :]:
        return []
    return tail.parts[covered:]
