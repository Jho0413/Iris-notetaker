import asyncio
import logging
import math

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm import usage
from iris.llm.errors import ErrorKind, classify
from iris.models import EventType, Stage
from iris.pipeline.context import StageContext
from iris.pipeline.stages import write
from iris.pipeline.stages.write import WritingPart, WrittenSection

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
RETRY_DELAY_S = 60.0


def _section_args(index: int, language: str | None, first: WrittenSection | None) -> dict:
    return dict(language=language, first=first if index else None, later=True, earlier=index > 0)


class DraftLane(LiveLane):
    lane = Lane.DRAFT
    retries_end_at_notes = True

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        super().__init__(ctx, timeline)
        self._sections: dict[str, WrittenSection] = {}
        self._failures: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._due: float | None = None
        self._announced = 0

    def _stopping(self) -> bool:
        return self.timeline.ending or self.halted

    def _evidence(self) -> tuple:
        tl = self.timeline
        return (tl.horizon, len(tl.segments), len(tl.contents), len(tl.visuals), tl.language)

    async def run(self) -> None:
        tl = self.timeline
        loop = asyncio.get_running_loop()
        while True:
            if tl.ending:
                return
            if self.halted:
                await tl.wait_until(lambda: tl.ending)
                return
            seen = self._evidence()
            transcript, selection = tl.transcript(), tl.selection()
            budget, horizon = self.ctx.settings.notes_context_chars, seen[0]
            closed, _ = await asyncio.to_thread(
                write.split_parts, transcript, selection, budget, horizon
            )
            language = transcript.language
            self._due = None

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
        return write.section_payload(part, **_section_args(index, language, first))

    def _given_up(self, payload: str) -> bool:
        return self._failures.get(payload, 0) >= MAX_ATTEMPTS

    def _due_at(self, t: float) -> None:
        self._due = t if self._due is None else min(self._due, t)

    def _ready(self, payload: str, now: float) -> bool:
        if payload in self._sections or self._given_up(payload):
            return False
        retry_at = self._retry_at.get(payload, -math.inf)
        if now < retry_at:
            self._due_at(retry_at)
            return False
        return True

    async def _wait(self, seen: tuple, now: float) -> None:
        tl = self.timeline
        due = self._due
        await tl.wait_until(
            lambda: tl.ending or self.halted or self._evidence() != seen,
            None if due is None else max(due - now, 0),
        )

    async def _write(
        self,
        part: WritingPart,
        payload: str,
        index: int,
        language: str | None,
        first: WrittenSection | None,
    ) -> None:
        try:
            with usage.recording(self.ctx, Stage.WRITE):
                section = await write.write_section(
                    self.ctx, part, **_section_args(index, language, first)
                )
        except Exception as exc:
            failures = self._failures.get(payload, 0) + 1
            if classify(exc) is ErrorKind.BLOCKING:
                failures = MAX_ATTEMPTS
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
        done = sum(p in self._sections for p in frozen)
        if done <= self._announced:
            return
        self._announced = done
        self.ctx.emit(
            EventType.NOTES_UPDATED, {"final": False, "completed_parts": done, "live": True}
        )
