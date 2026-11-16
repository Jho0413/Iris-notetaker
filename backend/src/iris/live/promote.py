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

EVERYTHING: tuple[Stage, ...] = (Stage.TRANSCRIBE, Stage.DETECT, Stage.ALIGN, Stage.SELECT)
TRANSCRIPT_ONLY: tuple[Stage, ...] = (Stage.TRANSCRIBE,)


def _screen_problems(ctx: StageContext, tl: LiveTimeline) -> list[str]:
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
    if tl is None or not tl.usable(Lane.AUDIO):
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
    groups = [
        [s for s in group if s not in promoted] for group in PLAN if Stage.INGEST not in group
    ]
    return [group for group in groups if group]


async def promote(ctx: StageContext, tl: LiveTimeline, stage: Stage) -> dict:
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
