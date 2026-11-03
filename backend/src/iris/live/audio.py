"""Audio lane: captured WebM chunks -> transcript, while recording.

Reads:  live/audio/<seq>.webm
Writes: live/audio.pcm, live/transcript_windows/, live/transcript.json
Publishes: transcript segments and ``transcribed_to``; emits TRANSCRIPT_CHUNK per window.

To build (track A, architecture section 1, "Life of a session: live"):
  1. Decode only the contiguous run of ``seq`` into one PCM stream (PyAV in a thread, reading a
     stream that grows as chunks arrive). Wait ``live_audio_gap_wait_s`` for a missing chunk,
     then resume at the next WebM cluster with the gap as silence (as ``finalize_audio`` does) and
     ``timeline.mark_skipped_input(Lane.AUDIO)``.
  2. Cut windows at the quietest point ``live_window_min_s``..``live_window_max_s`` after the last
     cut (reuse the quiet-point logic of ``transcribe.plan_chunks``); never cut within the last
     moments of decoded audio, where a pause can't be confirmed yet.
  3. Transcribe up to ``live_transcribe_concurrency`` windows at once with ``transcribe_chunk``,
     prompting each with the previous window's text; cache each window; commit windows in order.
  4. On ``timeline.ending``: decode what is left, cut the last window where the audio ends,
     transcribe it, publish, return.
"""

from iris.live.lane import LiveLane
from iris.live.timeline import Lane


class AudioLane(LiveLane):
    lane = Lane.AUDIO

    async def run(self) -> None:
        # Placeholder until track A: consume nothing, finish at stop. Stop then runs the full
        # pipeline because the timeline is marked incomplete.
        await self.timeline.mark_skipped_input(self.lane)
        await self.timeline.wait_until(lambda: self.timeline.ending)
