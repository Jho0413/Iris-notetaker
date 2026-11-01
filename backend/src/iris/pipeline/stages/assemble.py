"""Assemble: resolve ``[visual:<id>]`` markers into images and store the final notes.

Reads:  notes_draft.json, selection.json
Writes: notes.json (Notes), notes.md
"""

import re

from iris.markdown import escape_text
from iris.models import EventType, Notes, NotesDraft, Selection, Visual
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, write_model

MARKER = re.compile(r"\[visual:([A-Za-z0-9_\-]+)\]")


def resolve_markers(markdown: str, visuals: list[Visual]) -> str:
    """Replace each marker with a markdown image. Unknown ids are dropped, never invented."""
    by_id = {v.id: v for v in visuals}

    def repl(match: re.Match[str]) -> str:
        v = by_id.get(match.group(1))
        if v is None:
            return ""
        caption = escape_text(" ".join(v.caption.splitlines()))
        return f"![{caption}]({v.path})\n*{caption}*"

    return MARKER.sub(repl, markdown)


async def run(ctx: StageContext) -> None:
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    selection = read_model(ctx.paths.selection, Selection)
    markdown = resolve_markers(draft.markdown, selection.visuals)
    notes = Notes(
        session_id=ctx.session_id,
        session_type=draft.session_type,
        title=draft.title,
        markdown=markdown,
        visuals=selection.visuals,
    )
    write_model(ctx.paths.notes, notes)
    ctx.paths.notes_md.write_text(markdown)
    ctx.emit(EventType.NOTES_UPDATED, {"final": True})
