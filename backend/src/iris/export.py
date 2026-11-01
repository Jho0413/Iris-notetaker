"""Export notes as standalone CommonMark with local Markdown images embedded."""

import base64
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from mdformat.renderer import MDRenderer

from iris.markdown import escape_text
from iris.models import Notes


def _image_markdown(node, context) -> str:
    # Preserve literal alt text, including brackets, when serializing image tokens.
    alt = escape_text(node.meta["export_alt"])
    src = node.attrs["src"]
    title = node.attrs.get("title")
    suffix = f' "{escape_text(title)}"' if title is not None else ""
    return f"![{alt}](<{src}>{suffix})"


class _ImageRenderer:
    RENDERERS = {"image": _image_markdown}


def export_markdown_embedded(notes: Notes, session_root: Path) -> str:
    """Embed session images; preserve external, missing and outside-session references.

    Parse and serialize CommonMark, so whitespace/formatting can be normalized. Code
    examples are not images. Reference-style images are serialized as inline images.
    """
    root = session_root.resolve()
    parser = MarkdownIt("commonmark", renderer_cls=MDRenderer)
    parser.options["parser_extension"] = [_ImageRenderer]
    env: dict = {}
    title = escape_text(" ".join(notes.title.splitlines()))
    tokens = parser.parse(f"# {title}\n\n{notes.markdown}", env)

    def visit(tokens: list[Token]) -> None:
        for token in tokens:
            if token.children:
                visit(token.children)
            # markdown-it-py 4 emits escaped punctuation as text_special; the
            # plain-text image renderer only recognizes text tokens.
            if token.type == "text_special":
                token.type = "text"
            if token.type == "image":
                token.meta["export_alt"] = RendererHTML().renderInlineAsText(
                    token.children or [], parser.options, env
                )
                ref = token.attrGet("src") or ""
                url = urlsplit(ref)
                if ref and not url.scheme and not url.netloc:
                    path = (root / unquote(url.path)).resolve()
                    if path.is_relative_to(root) and path.is_file():
                        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                        data = base64.b64encode(path.read_bytes()).decode()
                        token.attrSet("src", f"data:{mime};base64,{data}")

    visit(tokens)
    return parser.renderer.render(tokens, parser.options, env)
