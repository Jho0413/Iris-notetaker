"""Model-written SVG: make it inert, and rasterize it for checking and export."""

import re
import xml.etree.ElementTree as ET

import resvg_py

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_BLOCKED_TAGS = {"script", "foreignObject", "iframe", "object", "embed", "audio", "video", "image"}


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def sanitize_svg(svg: str) -> str:
    """Parse model-written SVG and drop anything active or external. Raises on non-SVG input."""
    if re.search(r"<!(DOCTYPE|ENTITY)", svg, re.IGNORECASE):
        raise ValueError("svg must not declare a doctype or entities")
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    try:
        root = ET.fromstring(svg.strip())
    except ET.ParseError as exc:
        raise ValueError(f"invalid svg: {exc}") from exc
    if _local(root.tag) != "svg":
        raise ValueError("root element must be <svg>")
    if not root.get("viewBox"):
        raise ValueError("svg must have a viewBox")
    for parent in list(root.iter()):
        for child in list(parent):
            if _local(child.tag) in _BLOCKED_TAGS:
                parent.remove(child)
        for attr in list(parent.attrib):
            name, value = _local(attr).lower(), parent.attrib[attr].strip()
            active = name.startswith("on")
            external = (name == "href" and not value.startswith("#")) or (
                "url(" in value and not re.fullmatch(r"url\(#[\w\-.]+\)", value)
            )
            if active or external:
                del parent.attrib[attr]
        if (
            _local(parent.tag) == "style"
            and parent.text
            and re.search(r"@import|url\((?!#)", parent.text)
        ):
            parent.text = ""
    return ET.tostring(root, encoding="unicode")


def rasterize_svg(svg: str, width: int = 1200) -> bytes:
    """PNG of the SVG on white. Pure Rust (resvg) with system fonts; no system libraries."""
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, background="#ffffff"))
