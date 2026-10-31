"""Export notes as a standalone file with visuals embedded."""

import base64
import mimetypes
import re
from pathlib import Path

from iris.models import Notes

IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def export_markdown_embedded(notes: Notes, session_root: Path) -> str:
    """Inline every local image as a data URI. Images outside the session folder are left as is."""
    root = session_root.resolve()

    def repl(match: re.Match[str]) -> str:
        alt, ref = match.group(1), match.group(2)
        path = (root / ref).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        data = base64.b64encode(path.read_bytes()).decode()
        return f"![{alt}](data:{mime};base64,{data})"

    return f"# {notes.title}\n\n" + IMAGE.sub(repl, notes.markdown)
