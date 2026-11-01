"""Literal text inserted into generated Markdown."""

import string


def escape_text(text: str) -> str:
    # CommonMark permits escaping every ASCII punctuation character.
    return "".join("\\" + c if c in string.punctuation else c for c in text)
