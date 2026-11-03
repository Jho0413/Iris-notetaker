"""Screen lane: captured frames -> moments, while recording.

Reads:  live/frames/<t_ms>.jpg
Writes: frames/<t_ms>.jpg (the same names and bytes ingest writes), live/frames.json,
        live/moments.json
Publishes: closed moments and ``closed_to``; emits MOMENT_FOUND per moment.

To build (track B, architecture section 1, "Life of a session: live"):
  1. Hold frames back ``live_frame_holdback_s`` so they are fed in time order; a frame arriving
     later than that is skipped live (``timeline.mark_skipped_input()``).
  2. Sample onto the ``frame_fps`` grid and normalise exactly as ``finalize_frames`` does (size
     fixed by the first readable frame), so ingest at stop produces identical frames.
  3. Feed ``MomentDetector``; publish each moment it returns, and advance ``closed_to``.
  4. On ``timeline.ending``: feed the rest, ``detector.finish(end)``, publish, return.
"""

from iris.live.lane import LiveLane
from iris.live.timeline import Lane


class ScreenLane(LiveLane):
    lane = Lane.SCREEN

    async def run(self) -> None:
        # Placeholder until track B.
        await self.timeline.mark_skipped_input()
        await self.timeline.wait_until(lambda: self.timeline.ending)
