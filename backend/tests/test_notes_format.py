import pytest

from iris import notes_format as nf
from iris.models import FrameRef, Notes, NotesVisual, SessionType


def messages(markdown: str, ids: set[str] = frozenset()) -> list[tuple[int, str]]:
    return [(p.line, p.message) for p in nf.validate(markdown, set(ids))]


def visual(vid: str, path: str) -> NotesVisual:
    return NotesVisual(
        id=vid, moment_id="m1", frame=FrameRef(t=0, path="frames/0.jpg"), caption="c", path=path
    )


def test_each_close_closes_the_most_recent_block_still_open():
    md = (
        ":::step[Find the forces]{color=indigo}\n"
        "At the bottom.\n"
        ":::box[Key idea]{color=blue}\n"
        "The reaction force.\n"
        ":::\n"
        "::figure{#v1}\n"
        ":::\n"
        ":::box{color=green}\n"
        "Done.\n"
        ":::"
    )
    s = nf.scan(md)
    assert [(b.command, b.title, b.color, b.line, b.end, b.depth) for b in s.blocks] == [
        ("step", "Find the forces", "indigo", 0, 6, 0),
        ("box", "Key idea", "blue", 2, 4, 1),
        ("box", None, "green", 7, 9, 0),
    ]
    assert s.figures == [(5, "v1")]
    assert s.open_at == [0, 1, 1, 2, 2, 1, 1, 0, 1, 1]
    assert nf.validate(md, {"v1"}) == []


def test_lines_inside_code_are_text():
    md = "```markdown\n:::box[Not a box]\n::figure{#nope}\n:::\n```\n\n~~~\n:::\n~~~"
    s = nf.scan(md)
    assert s.blocks == [] and s.figures == [] and s.stray_closes == []
    assert nf.validate(md, set()) == []


def test_longer_fences_are_read_too():
    s = nf.scan("::::step[A]{color=red}\n:::box\nx\n:::\n::::")
    assert [(b.command, b.depth, b.end) for b in s.blocks] == [("step", 0, 4), ("box", 1, 3)]


def test_titles_keep_markdown_and_escaped_brackets():
    s = nf.scan(":::box[Definition: $v^2/r$ and **bold** \\] here]{color=blue}\nx\n:::")
    assert s.blocks[0].title == "Definition: $v^2/r$ and **bold** \\] here"


def test_non_ascii_titles():
    md = ":::box[Étape clé ✓ 収益]{color=teal}\nTexte.\n:::"
    assert nf.scan(md).blocks[0].title == "Étape clé ✓ 収益"
    assert nf.validate(md, set()) == []


def test_problems_name_the_line_in_plain_words():
    md = "\n".join(
        [
            ":::warning[Careful]{color=red}",
            "x",
            ":::",
            ":::box[A]{color=purple}",
            ":::box[B]",
            ":::box[C]",
            "deep",
            ":::",
            ":::",
            ":::",
            ":::",
            "::figure{#v9}",
            ":::box[Open]",
            "::chart{#x}",
        ]
    )
    found = messages(md, {"v1"})
    assert [line for line, _ in found] == [1, 4, 6, 11, 12, 13, 14]
    text = dict(found)
    assert text[1].startswith('":::warning" isn\'t something Iris knows')
    assert "\"purple\" isn't one of Iris's colours" in text[4]
    assert text[6].startswith("Blocks go one level deep")
    assert text[11] == "This ::: doesn't close anything."
    assert text[12] == 'There\'s no figure "v9" in this session.'
    assert text[13].startswith("This block isn't closed")
    assert text[14] == "Iris can't read this line as a box, step or figure."
    assert all("Traceback" not in m for _, m in found)


def test_html_and_outside_images_are_problems_but_maths_is_untouched():
    md = (
        "Fine $a < b > c$ and $$x<y$$.\n\n"
        "$$\n<div> is maths here\n$$\n\n"
        "<div>raw</div>\n\n"
        "Inline <b>bold</b>.\n\n"
        "![x](https://example.com/x.png)\n\n"
        "`<code>` is fine"
    )
    found = messages(md)
    assert [line for line, _ in found] == [7, 9, 9, 11]
    assert "HTML isn't supported" in found[0][1]
    assert "Images from outside the session" in found[-1][1]


def test_a_box_without_title_or_colour_is_valid_and_grey():
    s = nf.scan(":::box\nx\n:::")
    assert (s.blocks[0].title, s.blocks[0].color) == (None, None)
    assert nf.validate(":::box\nx\n:::", set()) == []


def test_empty_notes_are_valid():
    assert nf.validate("", set()) == []
    assert nf.scan("").blocks == []


def test_repair_fixes_colours_unclosed_blocks_and_stray_closes():
    md = ":::\n:::box[A]{color=purple}\nx\n:::step[S]\ny"
    repaired = nf.repair(md)
    assert repaired == ":::box[A]{color=grey}\nx\n:::step[S]{color=grey}\ny\n:::\n:::"
    assert nf.validate(repaired, set()) == []


def test_repair_leaves_what_needs_guessing():
    md = ":::warning[x]\ny\n:::"
    assert nf.repair(md) == md
    assert messages(md)


def test_ends_inside_block_finds_the_closing_line_of_a_top_level_block():
    assert nf.ends_inside_block(":::step[S]{color=indigo}\nx\n:::box\ny\n:::\n:::\n") == 5
    assert nf.ends_inside_block(":::box\nx\n:::\n\nAfter.") is None
    assert nf.ends_inside_block("Plain.") is None


def test_signatures_are_the_blocks_by_command_title_and_colour():
    md = ":::step[A]{color=indigo}\n:::box[B]{color=blue}\nx\n:::\n:::\n:::box\ny\n:::"
    assert nf.signatures(md) == {("step", "A", "indigo"), ("box", "B", "blue"), ("box", None, None)}


LEGACY_WORKED = r"""**Problem:** Explain how the **apparent weight** changes around a loop.

The key quantity is the reaction force $P$.

**Step 1: Identify the forces at the bottom and at the top of the loop.**

At the bottom, $P$ points up.

> **Key idea:** The apparent weight is the reaction force $P$.

![Forces on a rider \[top and bottom\]\.](visuals/v000001.svg)
*Forces on a rider \[top and bottom\]\.*

**Step 2: Write the resultant force at the bottom.**

$$
F = P - W
$$

**Answer:** The rider feels heavier at the bottom.

> **Watch out:** $W$ is constant."""

UPGRADED_WORKED = r""":::box[Problem]{color=indigo}
Explain how the **apparent weight** changes around a loop.

The key quantity is the reaction force $P$.
:::

:::step[Identify the forces at the bottom and at the top of the loop.]{color=indigo}
At the bottom, $P$ points up.

:::box[Key idea]{color=blue}
The apparent weight is the reaction force $P$.
:::

::figure{#v000001}
:::

:::step[Write the resultant force at the bottom.]{color=indigo}
$$
F = P - W
$$
:::

:::box[Answer]{color=green}
The rider feels heavier at the bottom.
:::

:::box[Watch out]{color=amber}
$W$ is constant.
:::"""


def test_upgrade_legacy_groups_cards_as_the_old_reading_view_did():
    visuals = [visual("v000001", "visuals/v000001.svg")]
    upgraded = nf.upgrade_legacy(LEGACY_WORKED, visuals)
    assert upgraded == UPGRADED_WORKED
    assert nf.validate(upgraded, {"v000001"}) == []


def test_upgrade_legacy_process_steps_with_figures_and_a_result():
    md = (
        "Intro.\n\n"
        "![Two black holes\\.](visuals/v1.png)\n*Two black holes\\.*\n\n"
        "**Step 1: They spiral inward**\n\nText one.\n\n"
        "**Step 2:** They merge.\n\n"
        "**Result:** Waves.\n\n"
        "> **Note:** quoted.\n\n"
        "## Next topic\n\n"
        "> A plain quote stays a quote.\n\n"
        "---\n\n"
        "**Summary:** Done."
    )
    upgraded = nf.upgrade_legacy(md, [visual("v1", "visuals/v1.png")])
    assert upgraded == (
        "Intro.\n\n"
        "::figure{#v1}\n\n"
        ":::step[They spiral inward]{color=indigo}\nText one.\n:::\n\n"
        ":::step{color=indigo}\nThey merge.\n:::\n\n"
        ":::box[Result]{color=green}\nWaves.\n:::\n\n"
        ":::box[Note]{color=amber}\nquoted.\n:::\n\n"
        "## Next topic\n\n"
        "> A plain quote stays a quote.\n\n"
        "---\n\n"
        ":::box[Summary]{color=green}\nDone.\n:::"
    )
    assert nf.validate(upgraded, {"v1"}) == []


def test_upgrade_legacy_keeps_everything_else_as_written():
    md = (
        "## Heading\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\n**Problem:** x\n```\n\n"
        "- [ ] task\n\nSee [the chart][c].\n\n[c]: https://example.com\n\n"
        "![Not from this session](https://example.com/x.png)"
    )
    assert nf.upgrade_legacy(md, []) == md


def test_upgrade_legacy_titles_keep_brackets_and_maths():
    upgraded = nf.upgrade_legacy("**Step 1: Use [x] and $\\alpha_{[i]}$**\n\nText.", [])
    assert upgraded.startswith(":::step[Use [x] and $\\alpha_{[i]}$]{color=indigo}")
    assert nf.scan(upgraded).blocks[0].title == "Use [x] and $\\alpha_{[i]}$"
    assert nf.validate(upgraded, set()) == []


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("Closed interval [a, b]", "Closed interval [a, b]"),
        ("Half-open [a, b)", "Half-open \\[a, b)"),
        ("Ends with ]", "Ends with \\]"),
        ("Already \\] escaped", "Already \\] escaped"),
        ("Ends with \\", "Ends with \\\\"),
    ],
)
def test_escape_title_escapes_only_unpaired_brackets(text, escaped):
    assert nf.escape_title(text) == escaped
    assert nf.scan(f":::box[{escaped}]\nx\n:::").blocks[0].title == escaped


def test_titles_may_hold_brackets_in_pairs():
    md = ":::box[Closed interval [a, b]]{color=blue}\nx\n:::\n:::step[Interval $[a,b]$]\ny\n:::"
    assert [b.title for b in nf.scan(md).blocks] == ["Closed interval [a, b]", "Interval $[a,b]$"]
    assert nf.validate(md, set()) == []


@pytest.mark.parametrize("version", [1, 2])
def test_upgraded_only_changes_old_notes(version):
    notes = Notes(
        session_id="s1",
        session_type=SessionType.LECTURE,
        title="T",
        markdown="**Answer:** 42.",
        visuals=[],
        format_version=version,
    )
    result = nf.upgraded(notes)
    assert result.format_version == 2
    if version == 1:
        assert result.markdown == ":::box[Answer]{color=green}\n42.\n:::"
    else:
        assert result is notes


def test_old_notes_json_without_a_version_reads_as_version_1():
    raw = '{"session_id":"s","session_type":"lecture","title":"T","markdown":"x","visuals":[]}'
    assert Notes.model_validate_json(raw).format_version == 1


@pytest.mark.parametrize("odd_break", ["\r\n", "\r"])
def test_split_sections_reads_other_line_breaks_as_lf(odd_break):
    plain = "Intro\n# Setup\n:::box[Tip]{color=green}\nUse a clamp.\n:::\n# Next\nMore."
    assert nf.split_sections(plain.replace("\n", odd_break)) == nf.split_sections(plain)


def test_split_sections_keeps_a_line_separator_inside_its_line():
    markdown = (
        "Copied\u2028from a PDF\n# Setup\n:::box[Tip]{color=green}\n# Inner\n:::\n# Next\nMore."
    )
    assert nf.split_sections(markdown) == [
        "Copied\u2028from a PDF",
        "# Setup\n:::box[Tip]{color=green}\n# Inner\n:::",
        "# Next\nMore.",
    ]
