"""The notes as plain text for an AI assistant, and a plain word search over them.

Pure functions only. Figures become one line naming their id and caption, so no image reaches the
assistant's context unless it asks for one by id. Search splits the notes into passages (the text
under each top-level heading), reads each as the user sees it (a box or step shows its title,
never its command), and matches words, not meaning: a passage matches when every word of the
query is in it or in the session's titles. Case and Unicode forms are folded, so "CAFÉ" finds
"café" however either was typed. Notes are a few thousand characters per session, so reading them
all on each search is fast; anything smarter waits until this falls short.
"""

import re
import unicodedata
from dataclasses import dataclass

from iris.models import Notes, NotesVisual
from iris.notes.format import heading, scan, split_sections

# Characters of a passage shown around its first match.
SNIPPET_CHARS = 300
# Passages one session may contribute to one search, so results span sessions.
MAX_PASSAGES_PER_SESSION = 3
# Words one query may have. A passage must hold every word, so a longer query can't usefully
# match, and each word is a pass over every session's notes.
MAX_QUERY_WORDS = 20


def figure_line(visual: NotesVisual | None) -> str:
    """``[Figure <id>: caption]``, or ``[Figure]`` for a figure that isn't among the visuals
    (removed in an edit), which has no image to ask for."""
    if visual is None:
        return "[Figure]"
    caption = " ".join(visual.caption.split())
    return f"[Figure {visual.id}: {caption}]" if caption else f"[Figure {visual.id}]"


def figure_origin(visual: NotesVisual) -> str:
    """What a figure's image is, so the assistant knows how far to trust its exact wording. Notes
    from before ``verified`` was recorded are read as the app reads them: a redraw only reached
    them after passing its check."""
    if visual.render_as == "screenshot" and visual.verified is not False:
        return "A crop of the screen, as it was shown."
    if visual.verified is False:
        return "A crop of the screen: no redraw of this figure passed Iris's check."
    if visual.path == visual.source_path:
        return "A crop of the screen: Iris didn't redraw this figure."
    return "Iris's redraw, checked against the screen."


def with_figures_as_text(notes: Notes) -> str:
    """The notes' Markdown with each ``::figure{#<id>}`` line replaced by its ``figure_line``."""
    layout = scan(notes.markdown)
    visuals = {v.id: v for v in notes.visuals}
    lines = list(layout.lines)
    for i, visual_id in layout.figures:
        lines[i] = figure_line(visuals.get(visual_id))
    return "\n".join(lines)


def as_seen(markdown: str) -> str:
    """The notes with each box or step command line replaced by its title (or nothing), and each
    closing ``:::`` by an empty line, keeping the line count. Search then never matches words the
    user doesn't see ("color", "indigo"), and an empty line keeps a closing ``:::`` from reading
    as part of a heading."""
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
    heading: str | None  # the heading it sits under; None for text before the first heading
    text: str  # the heading and what follows it, up to the next top-level heading


def passages(markdown: str) -> list[Passage]:
    """The notes split at their top-level headings, never inside a box or step (the rule
    synthesize splits by), each as the user sees it."""
    return [Passage(heading(s) or None, as_seen(s)) for s in split_sections(markdown)]


def query_words(query: str) -> list[str]:
    """The distinct words of a query, case-folded; punctuation is ignored."""
    return list(dict.fromkeys(re.findall(r"\w+", _fold(query))))


def score(text: str, words: list[str]) -> int:
    """How often the words occur in the text (as parts of words too, so "eigen" finds
    "eigenvalues"), or 0 unless every word is there."""
    folded = _fold(text)
    counts = [folded.count(word) for word in words]
    return sum(counts) if counts and all(counts) else 0


def snippet(text: str, words: list[str]) -> str:
    """About ``SNIPPET_CHARS`` characters of the text, on one line, starting a little before the
    first word found; "…" marks text cut at either end."""
    flat = " ".join(unicodedata.normalize("NFC", text).split())
    # Words are found in a case-folded copy, which can be longer than ``flat`` ("ß" folds to
    # "ss"), so each folded character remembers the position it came from.
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
    """A session's best matching passages, best first, at most ``MAX_PASSAGES_PER_SESSION``.

    The titles count towards every passage, so "standup pricing" finds pricing in a session
    called "Standup"."""
    found = []
    for passage in passages(text):
        if points := score(f"{titles}\n{passage.text}", words):
            found.append(Found(points, passage.heading, snippet(passage.text, words)))
    found.sort(key=lambda f: f.score, reverse=True)  # stable: equal scores keep note order
    return found[:MAX_PASSAGES_PER_SESSION]
