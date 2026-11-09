"""A source turns an input into timestamped audio and frames inside a session folder.

After ``ingest`` the session must contain ``audio.wav`` (16 kHz mono) and ``frames.json`` plus
``frames/*.jpg``. Nothing downstream knows which source produced them.
"""

from typing import Protocol

from iris.pipeline.context import StageContext


class Source(Protocol):
    async def ingest(self, ctx: StageContext) -> None: ...
