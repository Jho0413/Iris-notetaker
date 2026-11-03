"""Export notes as standalone CommonMark with local Markdown images embedded."""

import base64
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from mdformat.renderer import MDRenderer, RenderContext, RenderTreeNode

from iris.markdown import escape_text, protect_math, restore_math
from iris.models import Notes
from iris.svg import rasterize_svg


def _image_markdown(node: RenderTreeNode, context: RenderContext) -> str:
    # Preserve literal alt text, including brackets, when serializing image tokens.
    alt = escape_text(node.meta["export_alt"])
    # Escape Markdown delimiters and entity-like text without URL-encoding the URL.
    src = escape_text(node.attrs["src"])
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
    # Serialization would escape LaTeX (x_1 -> x\_1), so math is swapped out and restored.
    markdown, math = protect_math(notes.markdown)
    tokens = parser.parse(f"# {title}\n\n{markdown}", env)

    _prepare_images(tokens, root, parser, env)
    return restore_math(parser.renderer.render(tokens, parser.options, env), math)


def _embedded_source(ref: str, root: Path) -> str:
    """Resolve a local image within the session; preserve all other URLs."""
    url = urlsplit(ref)
    if not ref or url.scheme or url.netloc:
        return ref
    path = (root / unquote(url.path)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return ref
    if path.suffix == ".svg":
        # Markdown viewers reject SVG data URIs (SVG can carry script), so embed a PNG.
        mime, raw = "image/png", rasterize_svg(path.read_text(), width=1600)
    else:
        mime, raw = mimetypes.guess_type(path.name)[0] or "image/jpeg", path.read_bytes()
    data = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{data}"


def _prepare_images(tokens: list[Token], root: Path, parser: MarkdownIt, env: dict) -> None:
    """Normalize literal tokens and replace image sources before serialization."""
    for token in tokens:
        if token.children:
            _prepare_images(token.children, root, parser, env)
        # markdown-it-py 4 emits escaped punctuation as text_special; the
        # plain-text image renderer only recognizes text tokens.
        if token.type == "text_special":
            token.type = "text"
        if token.type == "image":
            token.meta["export_alt"] = RendererHTML().renderInlineAsText(
                token.children or [], parser.options, env
            )
            token.attrSet("src", _embedded_source(token.attrGet("src") or "", root))
