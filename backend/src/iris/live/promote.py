"""Promotion: at stop, the live lanes' outputs become the pipeline's own.

After the lanes have finished and ingest
has written ``audio.wav`` and ``frames/`` + ``frames.json``, the job runner asks ``promotable``
which stages the live work replaces, writes their outputs with ``promote`` and runs the rest of
the plan (``remaining_plan``):

- **Everything** (``timeline.complete``: the audio, screen and select lanes finished, none
  failed, none skipped captured input): the transcript, moments, alignment and selection are
  written as ``transcript.json``, ``moments.json``, ``alignment.json`` and ``selection.json``, and
  the run continues from write. The draft lane froze its sections with the cache keys write
  computes over exactly this transcript and selection, and the select lane's renders are the
  render stage's cache hits, so only the sections still open at stop (and figures not rendered
  live) are new work.
- **The transcript only** (the audio lane finished cleanly, but the screen or select lane
  failed or skipped input): ``transcript.json`` is written and the run continues from detect
  without transcribe, so transcription isn't paid twice.
- **Nothing** (the audio lane failed, skipped input or didn't run): the full plan runs, as for an
  upload.

What is written is exactly what the timeline holds: segments in its order with its ``language``
(that of the first window with speech, not the transcribe stage's most-common rule), moments in
the order they closed, and contents and visuals in the order they were read. The frozen notes
sections' cache keys depend on it. Promotion also drops to a smaller set if the timeline doesn't
hold together on disk (duplicate moment ids, a selection naming an unknown moment, a frame or
source crop ingest didn't leave in place), since the stages it skips would have caught that.

Promoted stages make no model calls. The runner reports each as a stage that started and finished
with ``promoted: true``; ``promote`` replays the events the stage itself would emit for the UI's
counts (``moment_found`` per moment, ``visual_selected`` per figure). The transcript is fetched
whole once transcribe finishes, so no ``transcript_chunk`` is replayed; instead the transcribe
step's ``stage_finished`` carries its ``segments`` count (``promote``'s return value), since the UI
reset its per-run counts when processing started and the live windows' events came before that.
"""

import asyncio
import logging

from iris.live.timeline import Lane, LiveTimeline
from iris.models import Alignment, EventType, Moments, Stage
from iris.pipeline import PLAN
from iris.pipeline.context import StageContext
from iris.pipeline.stages import align
from iris.pipeline.stages.detect import moment_event
from iris.store.layout import write_model

log = logging.getLogger(__name__)

# What each promotion replaces, in plan order.
EVERYTHING: tuple[Stage, ...] = (Stage.TRANSCRIBE, Stage.DETECT, Stage.ALIGN, Stage.SELECT)
TRANSCRIPT_ONLY: tuple[Stage, ...] = (Stage.TRANSCRIBE,)


def _audio_usable(tl: LiveTimeline) -> bool:
    return (
        Lane.AUDIO in tl.finished
        and Lane.AUDIO not in tl.failed
        and Lane.AUDIO not in tl.skipped_input
    )


def _screen_problems(ctx: StageContext, tl: LiveTimeline) -> list[str]:
    """Why the live moments and selection can't stand in for detect and select, if anything."""
    root = ctx.paths.root
    problems = []
    ids = [m.id for m in tl.moments]
    if len(set(ids)) != len(ids):
        problems.append("duplicate moment ids")
    known = set(ids)
    named = {c.moment_id for c in tl.contents} | {v.moment_id for v in tl.visuals}
    named |= set(tl.rejected_moment_ids)
    if named - known:
        problems.append(f"selection names unknown moments {sorted(named - known)}")
    if len({v.id for v in tl.visuals}) != len(tl.visuals):
        problems.append("duplicate visual ids")
    missing = [m.frame.path for m in tl.moments if not (root / m.frame.path).is_file()]
    missing += [
        v.source_path or v.path
        for v in tl.visuals
        if not (root / (v.source_path or v.path)).is_file()
    ]
    if missing:
        problems.append(f"missing files {missing[:3]}")
    return problems


def promotable(ctx: StageContext, tl: LiveTimeline | None) -> tuple[Stage, ...]:
    """The stages whose outputs the live work replaces, in plan order (none: run everything).

    Call after ingest: it checks that the frames and source crops the timeline refers to exist.
    """
    if tl is None or not _audio_usable(tl):
        return ()
    if not tl.complete:
        return TRANSCRIPT_ONLY
    problems = _screen_problems(ctx, tl)
    if problems:
        log.warning(
            "session %s: live moments and selection not promoted: %s",
            ctx.session_id,
            "; ".join(problems),
        )
        return TRANSCRIPT_ONLY
    return EVERYTHING


def remaining_plan(promoted: tuple[Stage, ...]) -> list[list[Stage]]:
    """The plan after ingest, without the promoted stages."""
    groups = [
        [s for s in group if s not in promoted] for group in PLAN if Stage.INGEST not in group
    ]
    return [group for group in groups if group]


async def promote(ctx: StageContext, tl: LiveTimeline, stage: Stage) -> dict:
    """Write ``stage``'s output from the timeline (atomically) and emit the events it would.

    Returns extra data for the stage's ``stage_finished`` event (empty for most stages).
    """
    paths = ctx.paths
    if stage == Stage.TRANSCRIBE:
        transcript = tl.transcript()
        await asyncio.to_thread(write_model, paths.transcript, transcript)
        return {"segments": len(transcript.segments)}
    if stage == Stage.DETECT:
        moments = Moments(moments=list(tl.moments))
        await asyncio.to_thread(write_model, paths.moments, moments)
        for m in moments.moments:
            ctx.emit(EventType.MOMENT_FOUND, moment_event(m))
    elif stage == Stage.ALIGN:
        # Over the promoted transcript and moments, as the align stage reads them from disk.
        items = await asyncio.to_thread(align.align, list(tl.moments), list(tl.segments))
        await asyncio.to_thread(write_model, paths.alignment, Alignment(items=items))
    elif stage == Stage.SELECT:
        selection = tl.selection()
        await asyncio.to_thread(write_model, paths.selection, selection)
        for visual in selection.visuals:
            ctx.emit(EventType.VISUAL_SELECTED, visual.model_dump(mode="json"))
    else:
        raise ValueError(f"{stage} has no live output to promote")
    return {}
