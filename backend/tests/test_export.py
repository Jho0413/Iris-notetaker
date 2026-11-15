import base64
from pathlib import Path

import pytest

from iris.export import export_markdown_embedded
from iris.models import FrameRef, Visual
from iris.pipeline.stages.assemble import resolve_markers
from tests.builders import markdown_images, notes


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
    exported = export_markdown_embedded(notes(markdown, [visual]), tmp_path)
    image = markdown_images(exported)[0]
    assert (
        image.attrGet("src")
        == "data:image/jpeg;base64," + base64.b64encode(b"real image bytes").decode()
    )
    assert "".join(t.content for t in image.children) == caption
    assert len(markdown_images(exported)) == 1


def test_multiple_reference_images_titles_and_parentheses(tmp_path):
    (tmp_path / "chart (1).png").write_bytes(b"png")
    md = (
        '![Revenue [USD]](<chart (1).png> "First")\n\n![second][chart]\n\n'
        '[chart]: <chart (1).png> "Second"'
    )
    output = markdown_images(export_markdown_embedded(notes(md), tmp_path))
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
    assert [i.attrGet("src") for i in markdown_images(exported)] == [
        "https://example.com/image.jpg",
        "missing.jpg",
        "../outside.jpg",
        "data:image/png;base64,cG5n",
    ]


@pytest.mark.parametrize(
    "destination",
    [
        "https://example.com/?x=&amp;copy;",
        "https://example.com/a(b)?q=&amp;quot;",
        "missing&amp;copy;.jpg",
    ],
)
def test_export_preserves_image_destinations(tmp_path, destination):
    original = f"![image](<{destination}>)"
    exported = export_markdown_embedded(notes(original), tmp_path)
    assert markdown_images(exported)[0].attrGet("src") == markdown_images(original)[0].attrGet(
        "src"
    )


def test_export_api_embeds_captioned_image(client, settings):
    from iris.store.layout import session_paths, write_model

    sid = client.post("/api/live", json={"title": "Review"}).json()["id"]
    paths = session_paths(settings.sessions_dir, sid)
    (paths.visuals_dir / "v1.jpg").write_bytes(b"image bytes")
    visual = Visual(
        id="v1",
        moment_id="m1",
        frame=FrameRef(t=0, path="frames/1.jpg"),
        caption=r"Revenue [USD] \ Q1",
        path="visuals/v1.jpg",
    )
    document = notes(resolve_markers("[visual:v1]", [visual]), [visual])
    document.session_id = sid
    write_model(paths.notes, document)

    response = client.get(f"/api/sessions/{sid}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Review.md\"; filename*=UTF-8''Review.md"
    )
    image = markdown_images(response.text)[0]
    assert image.attrGet("src") == "data:image/jpeg;base64,aW1hZ2UgYnl0ZXM="
    assert "".join(t.content for t in image.children) == visual.caption


def test_repeated_marker_places_visual_once():
    visual = Visual(
        id="v1",
        moment_id="m1",
        frame=FrameRef(t=0, path="frames/1.jpg"),
        caption="Worked example",
        path="visuals/v1.jpg",
    )
    markdown = resolve_markers("Intro\n\n[visual:v1]\n\nMore\n\n[visual:v1]", [visual])
    assert markdown == "Intro\n\n::figure{#v1}\n\nMore\n\n"


def test_export_keeps_latex_intact():
    markdown = "Step 1: $x_1 * y_2$ and\n\n$$\n3\\left[(x+3)^2 - 9\\right] - 1\n$$\n\nCosts $5."
    exported = export_markdown_embedded(notes(markdown), Path("/nonexistent"))
    assert "$x_1 * y_2$" in exported
    assert "$$\n3\\left[(x+3)^2 - 9\\right] - 1\n$$" in exported


def test_export_keeps_boxes_and_steps_and_embeds_figures_inside_them(tmp_path):
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals/v1.jpg").write_bytes(b"image")
    visual = Visual(
        id="v1",
        moment_id="m1",
        frame=FrameRef(t=0, path="frames/1.jpg"),
        caption="Forces [N]",
        path="visuals/v1.jpg",
    )
    md = (
        ":::step[Find the forces]{color=indigo}\nAt the bottom, $P_1 * W$ points up.\n"
        ":::box[Key idea]{color=blue}\n- one\n- two\n:::\n::figure{#v1}\n:::\n\n"
        "::figure{#gone}\n\n```\n:::box\n```"
    )
    exported = export_markdown_embedded(notes(md, [visual]), tmp_path)
    lines = exported.split("\n")
    assert lines[:3] == ["# Review", "", ":::step[Find the forces]{color=indigo}"]
    assert "At the bottom, $P_1 * W$ points up." in lines
    assert ":::box[Key idea]{color=blue}" in lines and "- two" in lines
    assert lines.count(":::") == 2
    assert [i.attrGet("src") for i in markdown_images(exported)] == [
        "data:image/jpeg;base64,aW1hZ2U="
    ]
    assert "*Forces \\[N\\]*" in exported or "*Forces [N]*" in exported
    assert "gone" not in exported
    assert "```\n:::box\n```" in exported


def test_export_upgrades_old_notes(tmp_path):
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals/v1.jpg").write_bytes(b"image")
    visual = Visual(
        id="v1",
        moment_id="m1",
        frame=FrameRef(t=0, path="frames/1.jpg"),
        caption="Chart",
        path="visuals/v1.jpg",
    )
    old = "**Answer:** It works.\n\n![Chart](visuals/v1.jpg)\n*Chart*"
    exported = export_markdown_embedded(notes(old, [visual], format_version=1), tmp_path)
    assert ":::box[Answer]{color=green}\n\nIt works." in exported
    assert len(markdown_images(exported)) == 1
