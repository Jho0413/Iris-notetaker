"""The lane contract. Each lane is one long-running asyncio task of a live session's processor.

A lane:

- reads captured input and the timeline, and writes only its own files (named in
  ``store/layout.py``) and its own part of the timeline (``LiveTimeline.publish_*``);
- sleeps in ``wait_for_input`` until the API wakes it (new chunk or frame) or notes are asked for,
  or waits on watermarks with ``timeline.wait_until``;
- runs CPU-bound work (decoding, OpenCV) in ``asyncio.to_thread``, and wraps model calls in
  ``usage.recording(ctx, <stage>)`` so their cost is recorded under the matching stage;
- returns only once ``timeline.ending`` is set, after flushing everything it holds and publishing
  its final results; the processor then marks it finished. Returning earlier, raising, or still
  running ``live_finish_timeout_s`` after notes are asked for marks it failed instead: the
  recording carries on, and making notes falls back to the full pipeline;
- when ``halted`` (``timeline.upstream_failed(lane)``), stops making paid model calls (its
  evidence is incomplete and nothing it makes can be promoted) and just waits for notes to be
  asked for;
- retries temporary API errors like the stages (``retry_temporary``), but never once an upstream
  lane has failed (nothing it makes can be promoted), nor once notes are asked for if
  ``retries_end_at_notes``: the processor runs each lane in ``retries_until``, so such a retry's
  wait ends and no new attempt starts (a call already running finishes). Lanes whose final flush
  is model calls (audio, select) keep their retries then: making notes waits for that flush
  anyway, bounded by ``live_finish_timeout_s``, and losing it would discard the lane's work;
- must survive a backend restart mid-recording: a fresh lane starts from the captured files, and
  content-keyed caches (transcript windows, ``selection_batches/``, render and notes caches) mean
  work done before the restart is not paid for again.
"""

import asyncio
from typing import ClassVar

from iris.live.timeline import Lane, LiveTimeline
from iris.pipeline.context import StageContext


class LiveLane:
    lane: ClassVar[Lane]
    retries_end_at_notes: ClassVar[bool] = False

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        self.ctx = ctx
        self.timeline = timeline
        self._wake = asyncio.Event()

    @property
    def halted(self) -> bool:
        return self.timeline.upstream_failed(self.lane)

    def wake(self) -> None:
        self._wake.set()

    async def wait_for_input(self) -> None:
        if self.timeline.ending:
            return
        await self._wake.wait()
        self._wake.clear()

    async def run(self) -> None:
        raise NotImplementedError
