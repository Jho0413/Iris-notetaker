"""The notes format: Markdown with boxes, steps and figures written as directive commands.

    :::box[Title]{color=green}     a box; no [ ] means no title, no colour means grey
    ...
    :::
    :::step[Title]{color=indigo}   a step on a numbered path (steps in a row form one path)
    ...
    :::
    ::figure{#v000001}             a figure from this session, by id

The word after the colons is a command and is never shown; everything a reader sees is the
writer's own text. Each closing ``:::`` closes the most recent block still open, so a box opened
inside a step is closed before the step. Blocks go one level deep (a box inside a step or box).
Lines inside fenced code are text.

Pure functions only: ``scan`` reads the structure, ``validate`` lists what is wrong in words for
the user, ``repair`` fixes what can be fixed without guessing (for model output), and
``upgrade_legacy`` reads notes written before this format. It is the only place that matches words
in the notes, and only to read old notes (``Notes.format_version`` 1).

The UI reads the same format with its own parser (``frontend/src/notes/format.ts``); the two
follow the rules above.
"""

import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markdown_it.token import Token

from iris.markdown import mask_math
from iris.models import Notes, NotesVisual

FORMAT_VERSION = 2  # notes.json written with boxes, steps and figures by id

PALETTE = ("grey", "indigo", "blue", "teal", "green", "amber", "orange", "red", "pink", "violet")
COMMANDS = ("box", "step")
MAX_DEPTH = 2  # blocks open at once: a box inside a step or box, never deeper

# A title may hold brackets in pairs, one level deep ("[Interval [a, b]]", "[$x[i]$]"); an
# unpaired ] is written \].
OPEN = re.compile(
    r"^(?P<colons>:{3,})(?P<command>[A-Za-z][\w-]*)"
    r"(?:\[(?P<title>(?:\\.|[^\[\]\\]|\[(?:\\.|[^\[\]\\])*\])*)\])?"
    r"(?:\{(?P<attrs>[^}]*)\})?[ \t]*$"
)
CLOSE = re.compile(r"^:{3,}[ \t]*$")
FIGURE = re.compile(r"^::figure\{#(?P<id>[A-Za-z0-9_-]+)\}[ \t]*$")
DIRECTIVE = re.compile(r"^::+[A-Za-z]")  # anything else that looks like a command
FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
COLOR = re.compile(r"(?:^|\s)color=(\"?)(?P<color>[^\s\"}]*)\1(?=\s|$)")


@dataclass(frozen=True)
class Block:
    command: str
    title: str | None  # as written between [ ] (inline Markdown), None without [ ]
    color: str | None  # as written; None when no colour was given
    line: int  # 0-based line of the opening fence
    depth: int  # 0 at the top level
    end: int | None = None  # line of the closing ``:::``; None if never closed


@dataclass(frozen=True)
class Problem:
    line: int  # 1-based, for people
    message: str


@dataclass
class Scan:
    lines: list[str]
    blocks: list[Block] = field(default_factory=list)
    figures: list[tuple[int, str]] = field(default_factory=list)  # (line, visual id)
    stray_closes: list[int] = field(default_factory=list)
    unreadable: list[int] = field(default_factory=list)  # lines that look like a command
    open_at: list[int] = field(default_factory=list)  # per line: blocks open on that line


def color_of(attrs: str | None) -> str | None:
    match = COLOR.search(attrs or "")
    return match.group("color") if match else None


def scan(markdown: str) -> Scan:
    """The block structure of the notes, line by line, following the stack rule."""
    out = Scan(lines=markdown.split("\n"))
    stack: list[int] = []  # indexes into out.blocks
    fence: str | None = None
    for i, line in enumerate(out.lines):
        out.open_at.append(len(stack))
        if fence is not None:
            if line.strip().startswith(fence) and not line.strip().strip(fence[0]):
                fence = None
            continue
        if m := FENCE.match(line):
            fence = m.group("fence")
            continue
        if m := FIGURE.match(line):
            out.figures.append((i, m.group("id")))
        elif m := OPEN.match(line):
            stack.append(len(out.blocks))
            out.blocks.append(
                Block(
                    command=m.group("command"),
                    title=m.group("title"),
                    color=color_of(m.group("attrs")),
                    line=i,
                    depth=len(stack) - 1,
                )
            )
        elif CLOSE.match(line):
            if stack:
                index = stack.pop()
                block = out.blocks[index]
                out.blocks[index] = Block(**{**block.__dict__, "end": i})
            else:
                out.stray_closes.append(i)
        elif DIRECTIVE.match(line):
            out.unreadable.append(i)
    return out


def _check_tokens(tokens: list[Token], problems: list[Problem]) -> None:
    for token in tokens:
        line = token.map[0] + 1 if token.map else 0
        if token.type in {"html_inline", "html_block"}:
            problems.append(Problem(line, "HTML isn't supported in notes. Remove it."))
        elif token.type == "image":
            problems.append(
                Problem(
                    line,
                    "Images from outside the session aren't supported. Use a figure from this "
                    "session instead.",
                )
            )
        if token.children:
            _check_tokens(token.children, problems)


def _inline_lines(tokens: list[Token]) -> None:
    """Give inline children their block's line, so problems found in them can name it."""
    for token in tokens:
        if token.children and token.map:
            for child in token.children:
                child.map = child.map or token.map


def structure_problems(s: Scan) -> list[Problem]:
    """Everything wrong with the block structure itself, in words for the person writing."""
    problems: list[Problem] = []
    for b in s.blocks:
        n = b.line + 1
        if b.command not in COMMANDS:
            problems.append(
                Problem(
                    n,
                    f'":::{b.command}" isn\'t something Iris knows. A box is :::box[…] and a '
                    "step is :::step[…].",
                )
            )
        if b.depth >= MAX_DEPTH:
            problems.append(
                Problem(
                    n,
                    "Blocks go one level deep (a box inside a step or box), so this one can't "
                    "go here.",
                )
            )
        if b.color is not None and b.color not in PALETTE:
            problems.append(
                Problem(
                    n,
                    f"\"{b.color}\" isn't one of Iris's colours. Use one of: {', '.join(PALETTE)}.",
                )
            )
        if b.end is None:
            problems.append(
                Problem(n, "This block isn't closed. Add a line with ::: after its last line.")
            )
    problems += [Problem(i + 1, "This ::: doesn't close anything.") for i in s.stray_closes]
    problems += [
        Problem(i + 1, "Iris can't read this line as a box, step or figure.") for i in s.unreadable
    ]
    return problems


def validate(markdown: str, visual_ids: set[str]) -> list[Problem]:
    """Every problem in notes from outside (model output, a save), in the order they appear."""
    s = scan(markdown)
    problems = structure_problems(s)
    problems += [
        Problem(i + 1, f'There\'s no figure "{vid}" in this session.')
        for i, vid in s.figures
        if vid not in visual_ids
    ]
    tokens = MarkdownIt().parse(mask_math(markdown))
    _inline_lines(tokens)
    _check_tokens(tokens, problems)
    return sorted(problems, key=lambda p: p.line)


def open_line(command: str, title: str | None, color: str) -> str:
    return f":::{command}{f'[{title}]' if title is not None else ''}{{color={color}}}"


def repair(markdown: str) -> str:
    """Fix what needs no guessing: unknown or missing colours become grey, a block left open is
    closed at the end, and a ``:::`` that closes nothing is dropped. Other problems stay."""
    s = scan(markdown)
    lines = list(s.lines)
    for b in s.blocks:
        if b.command in COMMANDS and b.color not in PALETTE:
            lines[b.line] = open_line(b.command, b.title, "grey")
    for i in sorted(s.stray_closes, reverse=True):
        del lines[i]
    unclosed = sum(1 for b in s.blocks if b.end is None)
    text = "\n".join(lines).rstrip()
    return text + "\n:::" * unclosed if unclosed else text


def ends_inside_block(markdown: str) -> int | None:
    """The line closing a top-level block when the notes end with that close, else None."""
    s = scan(markdown.rstrip())
    last = len(s.lines) - 1
    return next((b.end for b in s.blocks if b.depth == 0 and b.end == last), None)


def signatures(markdown: str) -> set[tuple[str, str | None, str | None]]:
    """The boxes and steps by command, title and colour (what a rewrite must not lose)."""
    return {(b.command, b.title, b.color) for b in scan(markdown).blocks}


# --- Notes written before this format (format_version 1) ------------------------------------------

LEGACY_CARDS = (
    ("problem", re.compile(r"^problem\b", re.I), "box", "indigo"),
    ("step", re.compile(r"^step\s*\d+", re.I), "step", "indigo"),
    ("answer", re.compile(r"^(answer|result|summary)\b", re.I), "box", "green"),
)
LEGACY_CALLOUTS = (
    (re.compile(r"^key (idea|point)", re.I), "blue"),
    (re.compile(r"^(watch out|common mistake|warning|note)\b", re.I), "amber"),
)
LEAD = re.compile(r"^\s*(\*\*|__)(?P<lead>.+?)\1[ \t]*")
STEP_LABEL = re.compile(r"^step\s*\d+\s*[:.\-–—]?\s*", re.I)
LEGACY_FIGURE = re.compile(r"^!\[(?:\\.|[^\]\\])*\]\((?P<src>[^)\s]+)\)\s*(?:\n\s*\*.*\*\s*)?$")


def _lead(inline: Token | None, source: str) -> str:
    """The paragraph's leading bold as written, or '' when it doesn't start with one."""
    children = [
        c
        for c in (inline.children if inline else None) or []
        if c.content.strip() or c.type != "text"
    ]
    if not children or children[0].type != "strong_open":
        return ""
    m = LEAD.match(source)
    return m.group("lead").strip() if m else ""


def escape_title(text: str) -> str:
    """Text for between ``[ ]``: brackets in pairs stay (maths keeps its LaTeX); an unpaired
    bracket is escaped, so the title can't end early."""
    out: list[str] = []
    opened: list[int] = []  # where each unclosed [ is in out
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            # A lone backslash at the end would escape the closing ]: it is written \\.
            out.append(text[i : i + 2] if i + 1 < len(text) else "\\\\")
            i += 2
            continue
        if c == "[":
            opened.append(len(out))
        elif c == "]":
            if opened:
                opened.pop()
            else:
                c = "\\]"
        out.append(c)
        i += 1
    for at in opened:
        out[at] = "\\["
    return "".join(out)


def _label(lead: str) -> str:
    """A title for ``[ ]``: the label without its colon."""
    return escape_title(lead.strip().rstrip(":").strip())


def _without_lead(text: str) -> str:
    return LEAD.sub("", text, count=1).lstrip()


def _unquote(text: str) -> str:
    return "\n".join(re.sub(r"^ {0,3}> ?", "", line) for line in text.split("\n"))


def upgrade_legacy(markdown: str, visuals: list[NotesVisual]) -> str:
    """Old notes in this format, grouped exactly as the old reading view drew them.

    A ``**Problem:**``, ``**Step n: …**`` or ``**Answer:**`` (``Result:``, ``Summary:``) paragraph
    starts a card holding what follows until the next card, heading or divider; a quote right
    after an answer ends it. ``> **Key idea:**`` quotes become blue boxes and ``> **Watch out:**``
    ones amber; ``![caption](visuals/<id>.<ext>)`` with its caption becomes ``::figure{#<id>}``.
    """
    by_path = {v.path: v.id for v in visuals}
    lines = markdown.split("\n")
    tokens = MarkdownIt().enable("table").parse(mask_math(markdown))
    out: list[str] = []
    card: str | None = None

    def add(text: str, *, tight: bool = False) -> None:
        """Blocks are separated by blank lines; a fence hugs the text inside it."""
        if tight and out:
            out[-1] += "\n" + text
        else:
            out.append(text)

    def close() -> None:
        nonlocal card
        if card:
            add(":::", tight=True)
        card = None

    i = cursor = 0
    while i < len(tokens):
        token = tokens[i]
        if token.level != 0 or token.nesting == -1 or not token.map:
            i += 1
            continue
        start, end = token.map
        # Lines no block holds, such as link reference definitions, are kept as they are.
        if gap := "\n".join(lines[cursor:start]).strip("\n"):
            add(gap, tight=bool(card) and bool(OPEN.match(out[-1].split("\n")[-1])))
        cursor = end
        source = "\n".join(lines[start:end]).strip("\n")
        # Skip to the token after this block.
        depth, j = token.nesting, i + 1
        while depth > 0 and j < len(tokens):
            depth += tokens[j].nesting
            j += 1
        lead = _lead(tokens[i + 1], source) if token.type == "paragraph_open" else ""
        kind = next(((k, cmd, col) for k, rx, cmd, col in LEGACY_CARDS if rx.search(lead)), None)
        # The first block inside a card hugs its opening fence.
        opened = bool(card) and bool(out) and bool(OPEN.match(out[-1].split("\n")[-1]))
        if token.type in {"heading_open", "hr"}:
            close()
            add(source)
        elif kind:
            close()
            name, command, color = kind
            rest = _without_lead(source)
            title = _label(STEP_LABEL.sub("", lead) if name == "step" else lead)
            add(open_line(command, title or None, color))
            if rest:
                add(rest, tight=True)
            card = name
        elif token.type == "blockquote_open":
            if card == "answer":
                close()
            body = _unquote(source)
            first = next((t for t in tokens[i:j] if t.type == "inline"), None)
            quote_lead = _lead(first, body.lstrip())
            color = next((c for rx, c in LEGACY_CALLOUTS if rx.search(quote_lead)), None)
            if color:
                rest = _without_lead(body)
                add(open_line("box", _label(quote_lead) or None, color), tight=opened)
                if rest:
                    add(rest, tight=True)
                add(":::", tight=True)
            else:
                add(source, tight=opened)
        elif (m := LEGACY_FIGURE.match(source)) and m.group("src") in by_path:
            add(f"::figure{{#{by_path[m.group('src')]}}}", tight=opened)
        else:
            add(source, tight=opened)
        i = j
    if gap := "\n".join(lines[cursor:]).strip("\n"):
        add(gap)
    close()
    return "\n\n".join(out)


def upgraded(notes: Notes) -> Notes:
    """Notes in the current format; old notes are upgraded in memory, never on disk."""
    if notes.format_version >= FORMAT_VERSION:
        return notes
    return notes.model_copy(
        update={
            "markdown": upgrade_legacy(notes.markdown, notes.visuals),
            "format_version": FORMAT_VERSION,
        }
    )
