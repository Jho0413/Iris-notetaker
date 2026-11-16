import re
import unicodedata
from dataclasses import dataclass

from iris.models import Notes, NotesVisual
from iris.notes.format import heading, scan, split_sections

SNIPPET_CHARS = 300
MAX_PASSAGES_PER_SESSION = 3
MAX_QUERY_WORDS = 20


def figure_line(visual: NotesVisual | None) -> str:
    if visual is None:
        return "[Figure]"
    caption = " ".join(visual.caption.split())
    return f"[Figure {visual.id}: {caption}]" if caption else f"[Figure {visual.id}]"


def figure_origin(visual: NotesVisual) -> str:
    if visual.render_as == "screenshot" and visual.verified is not False:
        return "A crop of the screen, as it was shown."
    if visual.verified is False:
        return "A crop of the screen: no redraw of this figure passed Iris's check."
    if visual.path == visual.source_path:
        return "A crop of the screen: Iris didn't redraw this figure."
    return "Iris's redraw, checked against the screen."


def with_figures_as_text(notes: Notes) -> str:
    layout = scan(notes.markdown)
    visuals = {v.id: v for v in notes.visuals}
    lines = list(layout.lines)
    for i, visual_id in layout.figures:
        lines[i] = figure_line(visuals.get(visual_id))
    return "\n".join(lines)


def as_seen(markdown: str) -> str:
    layout = scan(markdown)
    lines = list(layout.lines)
    for block in layout.blocks:
        lines[block.line] = block.title or ""
        if block.end is not None:
            lines[block.end] = ""
    return "\n".join(lines)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


@dataclass(frozen=True)
class Passage:
    heading: str | None
    text: str


def passages(markdown: str) -> list[Passage]:
    return [Passage(heading(s) or None, as_seen(s)) for s in split_sections(markdown)]


def query_words(query: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\w+", _fold(query))))


def score(text: str, words: list[str]) -> int:
    folded = _fold(text)
    counts = [folded.count(word) for word in words]
    return sum(counts) if counts and all(counts) else 0


def snippet(text: str, words: list[str]) -> str:
    flat = " ".join(unicodedata.normalize("NFC", text).split())
    pieces, origin = [], []
    for i, char in enumerate(flat):
        pieces.append(char.casefold())
        origin += [i] * len(pieces[-1])
    folded = "".join(pieces)
    found = [origin[at] for word in words if (at := folded.find(word)) >= 0]
    start = max(0, min(found, default=0) - SNIPPET_CHARS // 3)
    end = start + SNIPPET_CHARS
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


@dataclass(frozen=True)
class Found:
    score: int
    heading: str | None
    snippet: str


def find_passages(titles: str, text: str, words: list[str]) -> list[Found]:
    found = []
    for passage in passages(text):
        if points := score(f"{titles}\n{passage.text}", words):
            found.append(Found(points, passage.heading, snippet(passage.text, words)))
    found.sort(key=lambda f: f.score, reverse=True)
    return found[:MAX_PASSAGES_PER_SESSION]
