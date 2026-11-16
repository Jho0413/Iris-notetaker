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
    DRAFT = "draft"


UPSTREAM: dict[Lane, frozenset[Lane]] = {
    Lane.AUDIO: frozenset(),
    Lane.SCREEN: frozenset(),
    Lane.SELECT: frozenset({Lane.AUDIO, Lane.SCREEN}),
    Lane.DRAFT: frozenset({Lane.AUDIO, Lane.SCREEN, Lane.SELECT}),
}
PROMOTED: frozenset[Lane] = frozenset({Lane.AUDIO, Lane.SCREEN, Lane.SELECT})


@dataclass
class LiveTimeline:
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None
    transcribed_to: float = 0.0
    moments: list[Moment] = field(default_factory=list)
    closed_to: float = 0.0
    contents: list[MomentContent] = field(default_factory=list)
    visuals: list[Visual] = field(default_factory=list)
    rejected_moment_ids: list[str] = field(default_factory=list)
    selected_to: float = 0.0
    ending: bool = False
    finished: set[Lane] = field(default_factory=set)
    failed: dict[Lane, str] = field(default_factory=dict)
    absent: set[Lane] = field(default_factory=set)
    skipped_input: set[Lane] = field(default_factory=set)
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    def done(self, lane: Lane) -> bool:
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
        a = math.inf if self.done(Lane.AUDIO) else self.transcribed_to
        s = math.inf if self.done(Lane.SELECT) else self.selected_to
        return min(a, s)

    def upstream_failed(self, lane: Lane) -> bool:
        return any(up in self.failed for up in UPSTREAM[lane])

    @property
    def complete(self) -> bool:
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

    async def wait_until(self, predicate: Callable[[], bool], timeout: float | None = None) -> bool:
        try:
            async with asyncio.timeout(timeout), self._changed:
                await self._changed.wait_for(predicate)
        except TimeoutError:
            return False
        return True

    async def _notify(self) -> None:
        async with self._changed:
            self._changed.notify_all()

    async def publish_transcript(
        self,
        segments: Sequence[TranscriptSegment],
        transcribed_to: float,
        language: str | None = None,
    ) -> None:
        if self.segments and segments and segments[0].start < self.segments[-1].start:
            raise ValueError("transcript segments must be published in time order")
        self.segments.extend(segments)
        if language and not self.language:
            self.language = language
        self.transcribed_to = max(self.transcribed_to, transcribed_to)
        await self._notify()

    async def publish_moment(self, moment: Moment) -> None:
        if self.moments and moment.start < self.moments[-1].start:
            raise ValueError("moments must be published in time order")
        self.moments.append(moment)
        self.closed_to = max(self.closed_to, moment.end)
        await self._notify()

    async def advance_closed_to(self, t: float) -> None:
        self.closed_to = max(self.closed_to, t)
        await self._notify()

    async def publish_selection(
        self,
        contents: Sequence[MomentContent],
        visuals: Sequence[Visual],
        rejected_moment_ids: Sequence[str],
        selected_to: float,
    ) -> None:
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
        self.selected_to = max(self.selected_to, min(t, self.closed_to))
        await self._notify()

    async def mark_skipped_input(self, lane: Lane) -> None:
        self.skipped_input.add(lane)
        await self._notify()

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
