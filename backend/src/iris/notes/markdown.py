import re
import string

MATH = re.compile(r"\$\$.+?\$\$|(?<![\\$])\$(?=\S)[^$\n]*?(?<=[^\s\\])\$(?!\d)", re.DOTALL)


def escape_text(text: str) -> str:
    return "".join("\\" + c if c in string.punctuation else c for c in text)


def protect_math(markdown: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def repl(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"IRISMATH{len(spans) - 1:06d}X"

    return MATH.sub(repl, markdown), spans


def restore_math(text: str, spans: list[str]) -> str:
    return re.sub(r"IRISMATH(\d{6})X", lambda m: spans[int(m.group(1))], text)


def mask_math(markdown: str) -> str:
    return MATH.sub(lambda m: re.sub(r"[^\n]", "x", m.group(0)), markdown)
