import re
from pathlib import Path

from iris.models import (
    EventType,
    Notes,
    NotesDraft,
    NotesVisual,
    Renders,
    Selection,
    SynthesizedDraft,
    Visual,
)
from iris.notes.format import FORMAT_VERSION
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, read_optional, write_model

MARKER = re.compile(
    r"^[ \t]*\[visual:([A-Za-z0-9_\-]+)\]|\[visual:([A-Za-z0-9_\-]+)\]", re.MULTILINE
)


def resolve_markers(markdown: str, visuals: list[Visual]) -> str:
    known = {v.id for v in visuals}
    placed: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        vid = match.group(1) or match.group(2)
        if vid not in known or vid in placed:
            return ""
        placed.add(vid)
        return f"::figure{{#{vid}}}" if match.group(1) else f"\n\n::figure{{#{vid}}}\n\n"

    return MARKER.sub(repl, markdown)


def drop_title_heading(markdown: str, title: str) -> str:
    first, _, rest = markdown.lstrip().partition("\n")
    if (
        re.fullmatch(r"#{1,6}\s+(.*?)\s*#*\s*", first)
        and first.lstrip("# ").strip(" #").casefold() == title.strip().casefold()
    ):
        return rest.lstrip("\n")
    return markdown


def with_renders(visuals: list[Visual], renders: Path) -> list[NotesVisual]:
    rendered = {r.id: r for r in read_model(renders, Renders).visuals} if renders.exists() else {}
    out = []
    for v in visuals:
        visual = NotesVisual(**v.model_dump())
        if r := rendered.get(v.id):
            visual = visual.model_copy(
                update={"path": r.path, "render_as": r.render_as, "verified": r.verified}
            )
        out.append(visual)
    return out


def current_draft(ctx: StageContext, selection: Selection) -> NotesDraft:
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    synthesized = read_optional(ctx.paths.notes_synthesis, SynthesizedDraft)
    if (
        synthesized
        and synthesized.based_on == draft
        and synthesized.visual_ids == sorted(v.id for v in selection.visuals)
    ):
        return synthesized.draft
    return draft


async def run(ctx: StageContext) -> None:
    selection = read_model(ctx.paths.selection, Selection)
    draft = current_draft(ctx, selection)
    visuals = with_renders(selection.visuals, ctx.paths.renders)
    markdown = resolve_markers(drop_title_heading(draft.markdown, draft.title), visuals)
    notes = Notes(
        session_id=ctx.session_id,
        session_type=draft.session_type,
        title=draft.title,
        markdown=markdown,
        visuals=visuals,
        format_version=FORMAT_VERSION,
    )
    write_model(ctx.paths.notes, notes)
    ctx.emit(EventType.NOTES_UPDATED, {"final": True})
