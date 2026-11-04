"""The pipeline: stages and the order they run in.

Each stage is ``async def run(ctx: StageContext) -> None`` that reads files from the session and
writes its own output file. ``PLAN`` lists the stages in groups; stages within a group run
concurrently, groups run in order.

Finishing is two steps: assemble publishes the notes as written, so they can be read at once, then
synthesize consolidates them across the session and assemble runs again inside the synthesize
stage, so the polished notes replace the assembled ones. Each stage appears once in ``PLAN``:
``plan_from``, ``Session.current_stage`` and the UI's progress list need no special cases, and a
re-run from ``synthesize`` re-polishes without rewriting anything else. Assemble uses the
synthesized draft only while it matches the current notes draft and selection.
"""

from collections.abc import Awaitable, Callable

from iris.models import Stage
from iris.pipeline.context import StageContext
from iris.pipeline.stages import (
    align,
    assemble,
    detect,
    render,
    select,
    synthesize,
    transcribe,
    write,
)

StageFn = Callable[[StageContext], Awaitable[None]]


async def _ingest(ctx: StageContext) -> None:
    # Imported here to avoid a cycle (sources depend on pipeline.context).
    from iris.sources import SOURCES

    await SOURCES[ctx.source].ingest(ctx)


async def _synthesize(ctx: StageContext) -> None:
    # synthesize.run never raises for a failed synthesis (it records it and keeps the assembled
    # notes), so assembling again either publishes the synthesized notes or the same notes.
    await synthesize.run(ctx)
    await assemble.run(ctx)


STAGES: dict[Stage, StageFn] = {
    Stage.INGEST: _ingest,
    Stage.TRANSCRIBE: transcribe.run,
    Stage.DETECT: detect.run,
    Stage.ALIGN: align.run,
    Stage.SELECT: select.run,
    Stage.WRITE: write.run,
    Stage.RENDER: render.run,
    Stage.ASSEMBLE: assemble.run,
    Stage.SYNTHESIZE: _synthesize,
}

PLAN: list[list[Stage]] = [
    [Stage.INGEST],
    [Stage.TRANSCRIBE, Stage.DETECT],
    [Stage.ALIGN],
    [Stage.SELECT],
    [Stage.WRITE, Stage.RENDER],  # notes only need figure descriptions, not the images
    [Stage.ASSEMBLE],  # the notes as written: shown while synthesis runs
    [Stage.SYNTHESIZE],  # consolidated notes replace them; on failure the assembled notes stay
]


def plan_from(stage: Stage | None) -> list[list[Stage]]:
    """The plan starting at the group containing ``stage`` (for re-running from a stage)."""
    if stage is None:
        return PLAN
    for i, group in enumerate(PLAN):
        if stage in group:
            return PLAN[i:]
    raise ValueError(f"unknown stage {stage}")


__all__ = ["PLAN", "STAGES", "StageContext", "StageFn", "plan_from"]
