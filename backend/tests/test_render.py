import pytest

from iris.models import (
    EventType,
    Notes,
    NotesDraft,
    Renders,
    Selection,
    SessionType,
)
from iris.pipeline.stages import assemble, render
from iris.store.layout import read_model, write_model
from iris.svg import rasterize_svg, sanitize_svg
from tests.builders import figure_to_render, stage_ctx

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"><rect width="20"/></svg>'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    events = []
    ctx = stage_ctx(tmp_path, events=events, render_attempts=2)
    visual = figure_to_render(ctx)
    write_model(ctx.paths.selection, Selection(visuals=[visual]))
    calls = []
    verdicts = []

    def drawer(name, data):
        async def draw(_ctx, _visual, _source, feedback):
            calls.append((name, list(feedback)))
            return data, b"preview"

        return draw

    async def check(_ctx, _visual, _source, _candidate):
        calls.append(("check", []))
        return verdicts.pop(0)

    monkeypatch.setitem(render.DRAWERS, "svg", (drawer("svg", SVG.encode()), "svg"))
    monkeypatch.setitem(render.DRAWERS, "image", (drawer("image", b"png-bytes"), "png"))
    monkeypatch.setattr(render, "check", check)
    return ctx, visual, calls, verdicts, events


def ok():
    return render.Check(matches=True, problems=[])


def bad(problem):
    return render.Check(matches=False, problems=[problem])


async def test_verified_svg_is_published_and_cached(setup):
    ctx, _, calls, verdicts, events = setup
    verdicts.append(ok())
    await render.run(ctx)
    [r] = read_model(ctx.paths.renders, Renders).visuals
    assert (r.path, r.render_as, r.verified, r.attempts) == ("visuals/v1.svg", "svg", True, 1)
    assert (ctx.paths.root / r.path).read_text() == SVG
    assert [e[0] for e in events] == [EventType.VISUAL_RENDERED]
    calls.clear()
    (ctx.paths.root / r.path).unlink()
    await render.run(ctx)
    assert calls == []
    assert (ctx.paths.root / r.path).read_text() == SVG


async def test_failed_check_retries_with_feedback(setup):
    ctx, _, calls, verdicts, _ = setup
    verdicts.extend([bad("label 'LIGO' missing"), ok()])
    await render.run(ctx)
    assert calls == [("svg", []), ("check", []), ("svg", ["label 'LIGO' missing"]), ("check", [])]
    [r] = read_model(ctx.paths.renders, Renders).visuals
    assert (r.render_as, r.verified, r.attempts, r.problems) == ("svg", True, 2, [])


async def test_falls_back_to_other_renderer_then_screenshot(setup):
    ctx, _, calls, verdicts, _ = setup
    verdicts.extend([bad("a"), bad("b"), ok()])
    await render.run(ctx)
    [r] = read_model(ctx.paths.renders, Renders).visuals
    assert (r.path, r.render_as, r.attempts) == ("visuals/v1.png", "image", 3)
    assert [c[0] for c in calls if c[0] != "check"] == ["svg", "svg", "image"]

    ctx.settings.image_quality = "low"
    calls.clear()
    verdicts.extend([bad("x")] * 4)
    await render.run(ctx)
    [r] = read_model(ctx.paths.renders, Renders).visuals
    assert (r.path, r.render_as, r.verified, r.problems) == (
        "visuals/v1.jpg",
        "screenshot",
        False,
        ["x"],
    )
    assert (ctx.paths.root / r.path).read_bytes() == (
        ctx.paths.root / "visuals/v1.source.jpg"
    ).read_bytes()


async def test_unusable_output_counts_as_a_failed_attempt(setup, monkeypatch):
    ctx, _, _, verdicts, _ = setup

    async def broken(*_):
        raise ValueError("invalid svg: not well-formed")

    monkeypatch.setitem(render.DRAWERS, "svg", (broken, "svg"))
    verdicts.append(ok())
    await render.run(ctx)
    [r] = read_model(ctx.paths.renders, Renders).visuals
    assert (r.render_as, r.attempts) == ("image", 3)


async def test_screenshot_needs_no_model(setup):
    ctx, visual, calls, _, _ = setup
    visual.render_as = "screenshot"
    write_model(ctx.paths.selection, Selection(visuals=[visual]))
    await render.run(ctx)
    [r] = read_model(ctx.paths.renders, Renders).visuals
    assert (r.render_as, r.verified, r.attempts) == ("screenshot", True, 0)
    assert calls == []


async def test_assemble_uses_rendered_visuals(setup):
    ctx, _, _, verdicts, _ = setup
    verdicts.append(ok())
    await render.run(ctx)
    write_model(
        ctx.paths.notes_draft,
        NotesDraft(session_type=SessionType.LECTURE, title="Waves", markdown="Text\n\n[visual:v1]"),
    )
    await assemble.run(ctx)
    notes = read_model(ctx.paths.notes, Notes)
    assert "::figure{#v1}" in notes.markdown
    assert notes.visuals[0].path == "visuals/v1.svg"


def test_sanitize_svg_removes_active_and_external_content():
    svg = sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        ' viewBox="0 0 10 10" onload="alert(1)"><script>alert(1)</script>'
        '<foreignObject><div/></foreignObject><image href="http://x/y.png"/>'
        '<use xlink:href="http://x/#a"/><use href="#ok"/><rect fill="url(#g)"/>'
        '<rect style="fill:url(http://x)"/><style>@import url(http://x);</style>'
        '<text x="1" y="5">LIGO</text></svg>'
    )
    for banned in ("script", "onload", "foreignObject", "http://x", "@import"):
        assert banned not in svg
    assert 'href="#ok"' in svg and 'fill="url(#g)"' in svg and "LIGO" in svg
    assert rasterize_svg(svg).startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "svg",
    [
        "not xml",
        '<html xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
        '<svg xmlns="http://www.w3.org/2000/svg"/>',
        '<!DOCTYPE svg [<!ENTITY a "b">]><svg viewBox="0 0 1 1"/>',
    ],
)
def test_sanitize_svg_rejects_unsafe_or_invalid(svg):
    with pytest.raises(ValueError):
        sanitize_svg(svg)


async def test_stale_renders_from_earlier_runs_are_removed(setup):
    ctx, _, _, verdicts, _ = setup
    (ctx.paths.root / "visuals/v9.svg").write_text("old")
    (ctx.paths.root / "visuals/v1.png").write_bytes(b"old renderer output")
    verdicts.append(ok())
    await render.run(ctx)
    names = sorted(p.name for p in ctx.paths.visuals_dir.iterdir())
    assert names == ["v1.source.jpg", "v1.svg"]
