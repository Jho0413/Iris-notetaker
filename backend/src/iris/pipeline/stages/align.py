from iris.models import AlignedMoment, Alignment, Moment, Moments, Transcript, TranscriptSegment
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, write_model


def align(moments: list[Moment], segments: list[TranscriptSegment]) -> list[AlignedMoment]:
    items = []
    for m in moments:
        spoken = [s for s in segments if s.end > m.start and s.start < m.end]
        items.append(AlignedMoment(moment=m, transcript=spoken))
    return items


async def run(ctx: StageContext) -> None:
    moments = read_model(ctx.paths.moments, Moments)
    transcript = read_model(ctx.paths.transcript, Transcript)
    write_model(ctx.paths.alignment, Alignment(items=align(moments.moments, transcript.segments)))
