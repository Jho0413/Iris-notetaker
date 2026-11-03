"""Draft lane: the timeline -> a running notes draft, while recording.

Reads:  the timeline (transcript, selection, horizon)
Writes: notes_sections/ (shared cache), notes_draft.json
Emits:  NOTES_UPDATED ({"final": false, "completed_parts", "total_parts", "live": true}).

Architecture section 1, "Life of a session: live":

  1. Evidence before ``timeline.horizon`` is final. It is packed into parts exactly as the write
     stage packs a whole session (``write.split_parts``): a part closes when the next record would
     pass the budget, so its boundary depends only on earlier evidence and never moves.
  2. A closed part is *frozen*: written once through ``write.write_section``, with the payload,
     and so the cache key in ``notes_sections/``, that the write stage computes for the same part
     at stop. Stop then reuses it without a call.
  3. The open *tail* (evidence after the last closed part) is rewritten at most every
     ``live_draft_refresh_s``, and only when its evidence changed. It is written as the session's
     last part, so if nothing arrives after it, stop reuses it too.
  4. ``notes_draft.json`` holds the frozen sections plus the tail. When a part freezes, the old
     tail (which covered it) is dropped until the next tail rewrite.
  5. ``timeline.upstream_failed``: no more calls (nothing made can be promoted); wait for stop.
  6. On ``timeline.ending``: return. A tail call in flight is cancelled (stop rewrites the tail
     anyway); a frozen part's call in flight is finished, since the write stage would make it.

A call that fails is logged and retried no sooner than ``live_draft_refresh_s`` later; the lane
keeps going. After a backend restart a fresh lane re-plans from the timeline and its frozen parts
hit the cache.
"""

import asyncio
import contextlib
import logging
import math
from collections.abc import Awaitable
from dataclasses import dataclass

from iris.live.lane import LiveLane
from iris.live.timeline import Lane
from iris.llm import usage
from iris.models import EventType, Stage
from iris.pipeline.stages import write
from iris.pipeline.stages.write import WritingPart, WrittenSection
from iris.store.layout import write_model

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Written:
    payload: str
    section: WrittenSection


class DraftLane(LiveLane):
    lane = Lane.DRAFT

    async def run(self) -> None:
        tl = self.timeline
        refresh = self.ctx.settings.live_draft_refresh_s
        loop = asyncio.get_running_loop()
        frozen: list[_Written] = []
        tail: _Written | None = None
        tail_payload: str | None = None  # the tail evidence last written (or tried)
        next_tail_at = -math.inf  # the tail may not be rewritten before this
        retry_at = -math.inf  # after a failed call, no call before this
        while True:
            if tl.ending:
                return
            if tl.upstream_failed(self.lane):
                await tl.wait_until(lambda: tl.ending)
                return
            horizon = tl.horizon
            transcript, selection = tl.transcript(), tl.selection()
            closed, open_part = await asyncio.to_thread(
                write.split_parts,
                transcript,
                selection,
                self.ctx.settings.notes_context_chars,
                horizon,
            )
            language = transcript.language
            total = len(closed) + (open_part is not None)
            due: float | None = None  # when to look again without a timeline change

            # Freeze the parts that closed. Earlier frozen parts stay as they are unless their
            # payload changed (e.g. the language became known), when they are written again.
            del frozen[len(closed) :]
            for i, part in enumerate(closed):
                first = frozen[0].section if i else None
                payload = write.section_payload(part, language=language, first=first, later=True)
                if i < len(frozen) and frozen[i].payload == payload:
                    continue
                del frozen[i:]  # this part and every later one need writing
                if loop.time() < retry_at:
                    due = retry_at
                    break
                section = await self._write(part, language, first, later=True)
                if section is None:
                    retry_at = due = loop.time() + refresh
                    break
                frozen.append(_Written(payload, section))
                tail = None  # it covered this part's evidence
                self._publish(frozen, tail, total)
                if tl.ending or tl.upstream_failed(self.lane):
                    break
            if tl.ending or tl.upstream_failed(self.lane):
                continue

            # Rewrite the tail once every closed part is frozen, at most every `refresh`.
            if len(frozen) == len(closed) and open_part is not None:
                first = frozen[0].section if frozen else None
                payload = write.section_payload(
                    open_part, language=language, first=first, later=False
                )
                not_before = max(next_tail_at, retry_at)
                if payload != tail_payload and loop.time() < not_before:
                    due = not_before
                elif payload != tail_payload:
                    tail_payload, next_tail_at = payload, loop.time() + refresh
                    section = await self._until_ending(
                        self._write(open_part, language, first, later=False)
                    )
                    if section is None:
                        tail_payload = None  # failed or stopped: try again at the next refresh
                    else:
                        tail = _Written(payload, section)
                        self._publish(frozen, tail, total)
                    continue  # re-plan: the timeline may have moved during the call

            await self._wait(horizon, due, loop.time())

    async def _wait(self, horizon: float, due: float | None, now: float) -> None:
        """Until the horizon moves, stop, an upstream failure, or ``due`` (loop time)."""
        tl = self.timeline
        waiting = tl.wait_until(
            lambda: tl.ending or tl.upstream_failed(self.lane) or tl.horizon != horizon
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(waiting, None if due is None else max(due - now, 0))

    async def _write(
        self, part: WritingPart, language: str | None, first: WrittenSection | None, later: bool
    ) -> WrittenSection | None:
        """One section through the write stage's cache; ``None`` if the call failed."""
        try:
            with usage.recording(self.ctx, Stage.WRITE):
                return await write.write_section(
                    self.ctx, part, language=language, first=first, later=later
                )
        except Exception:
            log.warning("session %s: live draft section failed", self.ctx.session_id, exc_info=True)
            return None

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

    def _publish(self, frozen: list[_Written], tail: _Written | None, total: int) -> None:
        sections = [w.section for w in frozen] + ([tail.section] if tail else [])
        if not sections:
            return
        write_model(self.ctx.paths.notes_draft, write.draft_of(sections))
        self.ctx.emit(
            EventType.NOTES_UPDATED,
            {"final": False, "completed_parts": len(frozen), "total_parts": total, "live": True},
        )
