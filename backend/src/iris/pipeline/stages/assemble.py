"""Assemble: resolve ``[visual:<id>]`` markers into images and store the final notes.

Reads:  notes_draft.json, selection.json, renders.json and notes_synthesis.json (when present)
Writes: notes.json (Notes), notes.md

The synthesized draft replaces the notes draft only while it was made from exactly that draft and
selection; otherwise (absent, stale after a re-run, or unreadable) the notes draft is used.
"""

import logging
import re
from pathlib import Path

from iris.markdown import escape_text
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
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, write_model

log = logging.getLogger(__name__)

MARKER = re.compile(r"\[visual:([A-Za-z0-9_\-]+)\]")


def resolve_markers(markdown: str, visuals: list[Visual]) -> str:
    """Replace each marker with a markdown image.

    Unknown ids are dropped, never invented. Each visual appears once: repeated markers for an
    already placed visual are dropped.
    """
    by_id = {v.id: v for v in visuals}
    placed: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        v = by_id.get(match.group(1))
        if v is None or v.id in placed:
            return ""
        placed.add(v.id)
        caption = escape_text(" ".join(v.caption.splitlines()))
        return f"![{caption}]({v.path})\n*{caption}*"

    return MARKER.sub(repl, markdown)


def drop_title_heading(markdown: str, title: str) -> str:
    """The title is shown above the notes; a first heading repeating it is redundant."""
    first, _, rest = markdown.lstrip().partition("\n")
    if (
        re.fullmatch(r"#{1,6}\s+(.*?)\s*#*\s*", first)
        and first.lstrip("# ").strip(" #").casefold() == title.strip().casefold()
    ):
        return rest.lstrip("\n")
    return markdown


def with_renders(visuals: list[Visual], renders: Path) -> list[NotesVisual]:
    """Point each visual at its render where ``renders.json`` has one, with how it was made; the
    others keep their selected path (the source crop)."""
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
    """The synthesized draft if it is current, else the notes draft as written."""
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    try:
        synthesized = read_model(ctx.paths.notes_synthesis, SynthesizedDraft)
    except FileNotFoundError:
        return draft
    except ValueError:
        log.warning("ignoring unreadable %s", ctx.paths.notes_synthesis)
        return draft
    if synthesized.based_on == draft and synthesized.visual_ids == sorted(
        v.id for v in selection.visuals
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
    )
    write_model(ctx.paths.notes, notes)
    ctx.paths.notes_md.write_text(markdown)
    ctx.emit(EventType.NOTES_UPDATED, {"final": True})
