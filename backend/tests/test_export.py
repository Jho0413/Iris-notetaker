import base64

import pytest
from markdown_it import MarkdownIt

from iris.export import export_markdown_embedded
from iris.models import FrameRef, Notes, SessionType, Visual
from iris.pipeline.stages.assemble import resolve_markers


def notes(markdown):
    return Notes(
        session_id="s1",
        session_type=SessionType.MEETING,
        title="Review",
        markdown=markdown,
        visuals=[],
    )


def images(markdown):
    return [
        child
        for block in MarkdownIt().parse(markdown)
        for child in (block.children or [])
        if child.type == "image"
    ]


@pytest.mark.parametrize(
    "caption",
    [
        "Revenue [USD] by quarter",
        r"Path C:\users\[name]",
        "Growth (year over year)",
        '*Important* & <details> "quoted"',
        "[open and close] [nested [units]]",
    ],
)
def test_assembly_to_export_preserves_caption_and_embeds(tmp_path, caption):
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals/v1.jpg").write_bytes(b"real image bytes")
    visual = Visual(
        id="v1",
        moment_id="m1",
        frame=FrameRef(t=0, path="frames/1.jpg"),
        caption=caption,
        path="visuals/v1.jpg",
    )
    markdown = resolve_markers("[visual:v1]", [visual])
    exported = export_markdown_embedded(notes(markdown), tmp_path)
    image = images(exported)[0]
    assert (
        image.attrGet("src")
        == "data:image/jpeg;base64," + base64.b64encode(b"real image bytes").decode()
    )
    assert "".join(t.content for t in image.children) == caption
    assert len(images(exported)) == 1


def test_multiple_reference_images_titles_and_parentheses(tmp_path):
    (tmp_path / "chart (1).png").write_bytes(b"png")
    md = (
        '![Revenue [USD]](<chart (1).png> "First")\n\n![second][chart]\n\n'
        '[chart]: <chart (1).png> "Second"'
    )
    output = images(export_markdown_embedded(notes(md), tmp_path))
    assert len(output) == 2
    assert all(i.attrGet("src") == "data:image/png;base64,cG5n" for i in output)
    assert [i.attrGet("title") for i in output] == ["First", "Second"]


def test_code_and_nonlocal_images_remain_unembedded(tmp_path):
    (tmp_path / "local.jpg").write_bytes(b"image")
    md = """`![example](local.jpg)`

```markdown
![example](local.jpg)
```

![remote](https://example.com/image.jpg)
![missing](missing.jpg)
![outside](../outside.jpg)
![data](data:image/png;base64,cG5n)
"""
    exported = export_markdown_embedded(notes(md), tmp_path)
    assert "data:image/jpeg;base64," not in exported
    assert "`![example](local.jpg)`" in exported
    assert "![example](local.jpg)" in exported
    assert [i.attrGet("src") for i in images(exported)] == [
        "https://example.com/image.jpg",
        "missing.jpg",
        "../outside.jpg",
        "data:image/png;base64,cG5n",
    ]
