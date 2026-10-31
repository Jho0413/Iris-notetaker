"""Incremental processing for live sessions (architecture section 1, "Life of a session: live").

The same stages run while the session is happening:
  - each audio chunk extends the transcript
  - a moment closes when the screen moves on, which triggers selection for it
  - the notes are kept as a running draft, updated section by section
On end, the last moment closes and the full PLAN runs over the whole recording, replacing the draft.
"""

from iris.pipeline.context import StageContext


class LiveProcessor:
    def __init__(self, ctx: StageContext):
        self.ctx = ctx

    async def on_audio_chunk(self, seq: int) -> None:
        # TODO: transcribe the new chunk, append segments, emit TRANSCRIPT_CHUNK
        pass

    async def on_frame(self, t: float, path: str) -> None:
        # TODO: feed an incremental version of detect; on a closed moment, run select for it
        pass

    async def refresh_draft(self) -> None:
        # TODO: periodically update the running notes draft, emit NOTES_UPDATED
        pass
