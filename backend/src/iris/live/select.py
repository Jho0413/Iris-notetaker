"""Select lane: closed moments -> screen content and figures, while recording.

Reads:  the timeline (closed moments, transcript), frames/
Writes: selection_batches/ (shared cache), visuals/<id>.source.jpg, rendered visuals and the
        render cache, live/selection.json
Publishes: contents, visuals, rejected moment ids and ``selected_to``; emits VISUAL_SELECTED and
        VISUAL_RENDERED.

To build (track B, architecture section 1, "Life of a session: live"):
  1. Gate each closed moment until ``timeline.transcribed_through(moment.end + live_speech_pad_s)``.
  2. Align it (``align.align``) and read it with the select stage's batch logic: one call in
     flight; moments that pass the gate meanwhile go in the next batch, up to
     ``selection_batch_size``, with earlier moments' content as context. Visual ids continue the
     pipeline's numbering.
  3. Render each new figure in the background (``render.render_visual``), at most
     ``live_render_concurrency`` at once.
  4. ``selected_to`` advances to the end of the last moment read, but never past ``closed_to``.
  5. On ``timeline.ending``: wait for the screen lane to finish, read the remaining moments, wait
     for renders, publish, return.
"""

from iris.live.lane import LiveLane
from iris.live.timeline import Lane


class SelectLane(LiveLane):
    lane = Lane.SELECT

    async def run(self) -> None:
        # Placeholder until track B.
        await self.timeline.wait_until(lambda: self.timeline.ending)
