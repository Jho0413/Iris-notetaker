import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markdown_it.token import Token

from iris.models import Notes, NotesVisual
from iris.notes.markdown import mask_math

FORMAT_VERSION = 2

PALETTE = ("grey", "indigo", "blue", "teal", "green", "amber", "orange", "red", "pink", "violet")
COMMANDS = ("box", "step")
MAX_DEPTH = 2

OPEN = re.compile(
    r"^(?P<colons>:{3,})(?P<command>[A-Za-z][\w-]*)"
    r"(?:\[(?P<title>(?:\\.|[^\[\]\\]|\[(?:\\.|[^\[\]\\])*\])*)\])?"
    r"(?:\{(?P<attrs>[^}]*)\})?[ \t]*$"
)
CLOSE = re.compile(r"^:{3,}[ \t]*$")
VISUAL_ID = r"[A-Za-z0-9_-]+"
FIGURE = re.compile(rf"^::figure\{{#(?P<id>{VISUAL_ID})\}}[ \t]*$")
MARKER = re.compile(rf"\[visual:({VISUAL_ID})\]")
LINE_START_OR_INLINE_MARKER = re.compile(rf"^[ \t]*{MARKER.pattern}|{MARKER.pattern}", re.MULTILINE)
DIRECTIVE = re.compile(r"^::+[A-Za-z]")
FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
COLOR = re.compile(r"(?:^|\s)color=(\"?)(?P<color>[^\s\"}]*)\1(?=\s|$)")
_PARSER = MarkdownIt()


@dataclass(frozen=True)
class Block:
    command: str
    title: str | None
    color: str | None
    line: int
    depth: int
    end: int | None = None


@dataclass(frozen=True)
class Problem:
    line: int
    message: str


@dataclass
class Scan:
    lines: list[str]
    blocks: list[Block] = field(default_factory=list)
    figures: list[tuple[int, str]] = field(default_factory=list)
    stray_closes: list[int] = field(default_factory=list)
    unreadable: list[int] = field(default_factory=list)
    open_at: list[int] = field(default_factory=list)


def color_of(attrs: str | None) -> str | None:
    match = COLOR.search(attrs or "")
    return match.group("color") if match else None


def scan(markdown: str) -> Scan:
    out = Scan(lines=markdown.split("\n"))
    stack: list[int] = []
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


def forbidden_tokens(tokens: list[Token]) -> Iterator[Token]:
    for token in tokens:
        if token.type in {"image", "html_inline", "html_block"}:
            yield token
        yield from forbidden_tokens(token.children or [])


def _inline_lines(tokens: list[Token]) -> None:
    for token in tokens:
        if token.children and token.map:
            for child in token.children:
                child.map = child.map or token.map


def structure_problems(s: Scan) -> list[Problem]:
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
    s = scan(markdown)
    problems = structure_problems(s)
    problems += [
        Problem(i + 1, f'There\'s no figure "{vid}" in this session.')
        for i, vid in s.figures
        if vid not in visual_ids
    ]
    tokens = MarkdownIt().parse(mask_math(markdown))
    _inline_lines(tokens)
    for token in forbidden_tokens(tokens):
        line = token.map[0] + 1 if token.map else 0
        if token.type == "image":
            message = (
                "Images from outside the session aren't supported. Use a figure from this "
                "session instead."
            )
        else:
            message = "HTML isn't supported in notes. Remove it."
        problems.append(Problem(line, message))
    return sorted(problems, key=lambda p: p.line)


def split_sections(markdown: str) -> list[str]:
    markdown = re.sub(r"\r\n?", "\n", markdown)
    lines = markdown.split("\n")
    open_at = scan(markdown).open_at
    starts = sorted(
        {
            t.map[0]
            for t in _PARSER.parse(markdown)
            if t.type == "heading_open"
            and t.tag in {"h1", "h2"}
            and t.level == 0
            and t.map
            and not open_at[t.map[0]]
        }
    )
    bounds = [0, *(s for s in starts if s > 0), len(lines)]
    sections = ["\n".join(lines[a:b]).strip() for a, b in zip(bounds, bounds[1:], strict=False)]
    return [s for s in sections if s]


def heading(section: str) -> str:
    first = section.lstrip().split("\n", 1)[0]
    return first.lstrip("#").strip() if first.startswith("#") else ""


def open_line(command: str, title: str | None, color: str) -> str:
    return f":::{command}{f'[{title}]' if title is not None else ''}{{color={color}}}"


def repair(markdown: str) -> str:
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
    s = scan(markdown.rstrip())
    last = len(s.lines) - 1
    return next((b.end for b in s.blocks if b.depth == 0 and b.end == last), None)


def signatures(markdown: str) -> set[tuple[str, str | None, str | None]]:
    return {(b.command, b.title, b.color) for b in scan(markdown).blocks}


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
    out: list[str] = []
    opened: list[int] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
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
    return escape_title(lead.strip().rstrip(":").strip())


def _without_lead(text: str) -> str:
    return LEAD.sub("", text, count=1).lstrip()


def _unquote(text: str) -> str:
    return "\n".join(re.sub(r"^ {0,3}> ?", "", line) for line in text.split("\n"))


def upgrade_legacy(markdown: str, visuals: list[NotesVisual]) -> str:
    by_path = {v.path: v.id for v in visuals}
    lines = markdown.split("\n")
    tokens = MarkdownIt().enable("table").parse(mask_math(markdown))
    out: list[str] = []
    card: str | None = None

    def add(text: str, *, tight: bool = False) -> None:
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
        if gap := "\n".join(lines[cursor:start]).strip("\n"):
            add(gap, tight=bool(card) and bool(OPEN.match(out[-1].split("\n")[-1])))
        cursor = end
        source = "\n".join(lines[start:end]).strip("\n")
        depth, j = token.nesting, i + 1
        while depth > 0 and j < len(tokens):
            depth += tokens[j].nesting
            j += 1
        lead = _lead(tokens[i + 1], source) if token.type == "paragraph_open" else ""
        kind = next(((k, cmd, col) for k, rx, cmd, col in LEGACY_CARDS if rx.search(lead)), None)
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
    if notes.format_version >= FORMAT_VERSION:
        return notes
    return notes.model_copy(
        update={
            "markdown": upgrade_legacy(notes.markdown, notes.visuals),
            "format_version": FORMAT_VERSION,
        }
    )
