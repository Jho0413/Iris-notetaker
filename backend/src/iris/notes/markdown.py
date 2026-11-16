"""Literal text inserted into generated Markdown, and protection for LaTeX math."""

import re
import string

# $$display$$ (may span lines) or $inline$ (one line, no space inside the delimiters).
MATH = re.compile(r"\$\$.+?\$\$|(?<![\\$])\$(?=\S)[^$\n]*?(?<=[^\s\\])\$(?!\d)", re.DOTALL)


def escape_text(text: str) -> str:
    # CommonMark permits escaping every ASCII punctuation character.
    return "".join("\\" + c if c in string.punctuation else c for c in text)


def protect_math(markdown: str) -> tuple[str, list[str]]:
    """Swap math spans for inert placeholders, so Markdown parsing cannot alter or misread LaTeX.

    Restoration is exact, so text that only looks like math (prices) is unaffected.
    """
    spans: list[str] = []

    def repl(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"IRISMATH{len(spans) - 1:06d}X"

    return MATH.sub(repl, markdown), spans


def restore_math(text: str, spans: list[str]) -> str:
    return re.sub(r"IRISMATH(\d{6})X", lambda m: spans[int(m.group(1))], text)


def mask_math(markdown: str) -> str:
    """Math spans as runs of ``x`` with their line breaks kept, so a parse of the result reads
    the structure (and line numbers) of the original without misreading LaTeX as Markdown."""
    return MATH.sub(lambda m: re.sub(r"[^\n]", "x", m.group(0)), markdown)
