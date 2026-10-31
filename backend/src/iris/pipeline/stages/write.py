"""Write: produce notes in a style suited to the session, with visual markers.

Reads:  transcript.json, selection.json
Writes: notes_draft.json (NotesDraft)
Emits:  NOTES_UPDATED as the draft streams in.

Plan (architecture section 1, step 6):
  1. Classify the session type (meeting / lecture / demo) from the transcript.
  2. Write markdown notes in the matching style, placing ``[visual:<id>]`` markers where each kept
     visual belongs (the model gets each visual's id, time range, caption and nearby transcript).
  3. Open question (section 2): one call for long sessions, or per-section then merged.
"""

from iris.pipeline.context import StageContext


async def run(ctx: StageContext) -> None:
    # TODO: call ctx.settings.notes_model; write_model(ctx.paths.notes_draft, NotesDraft(...))
    raise NotImplementedError("write stage")
