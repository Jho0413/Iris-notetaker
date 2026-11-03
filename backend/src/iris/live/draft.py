"""Draft lane: the timeline -> a running notes draft, while recording.

Reads:  the timeline (transcript, selection, horizon)
Writes: notes_sections/ (shared cache), notes_draft.json
Emits:  NOTES_UPDATED ({"final": false, ...}).

To build (track C, architecture section 1, "Life of a session: live"):
  1. Pack the evidence before ``timeline.horizon`` into parts as ``write.plan_parts`` does.
  2. Freeze a part once the horizon has passed a moment boundary beyond its budget; write it once.
     Its cache key must be the one the write stage computes for the same part at stop (so the
     key cannot depend on the total number of parts), so stop reuses it.
  3. Rewrite the open tail at most every ``live_draft_refresh_s``, and only if it has new
     evidence. Publish frozen sections plus the tail as ``notes_draft.json``.
  4. On ``timeline.ending``: return (stop writes the final tail through the write stage).
"""

from iris.live.lane import LiveLane
from iris.live.timeline import Lane


class DraftLane(LiveLane):
    lane = Lane.DRAFT

    async def run(self) -> None:
        # Placeholder until track C.
        await self.timeline.wait_until(lambda: self.timeline.ending)
