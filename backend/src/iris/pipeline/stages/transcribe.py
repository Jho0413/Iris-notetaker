"""Transcribe: audio -> timestamped transcript.

Reads:  audio.wav
Writes: transcript.json (Transcript)
Emits:  TRANSCRIPT_CHUNK per finished chunk, so the UI can show text as it arrives.

Plan (architecture section 1, step 3):
  1. Split audio at silences into chunks (~1-5 min each, under the API's file size limit).
  2. Transcribe chunks concurrently with the OpenAI API, requesting segment timestamps.
  3. Offset each chunk's timestamps by its start time and merge into one Transcript.
"""

from iris.pipeline.context import StageContext


async def run(ctx: StageContext) -> None:
    # TODO: silence-based chunking (energy threshold over numpy samples read from audio.wav)
    # TODO: concurrent transcription via iris.llm.client.get_openai(ctx.settings)
    # TODO: merge, write_model(ctx.paths.transcript, Transcript(...))
    raise NotImplementedError("transcribe stage")
