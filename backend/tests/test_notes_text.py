import pytest

from iris.models import Notes, SessionType
from iris.notes.text import (
    MAX_PASSAGES_PER_SESSION,
    SNIPPET_CHARS,
    figure_origin,
    find_passages,
    passages,
    query_words,
    score,
    snippet,
    with_figures_as_text,
)
from tests.builders import notes, notes_visual


def notes_with(markdown: str) -> Notes:
    return notes(
        markdown,
        [notes_visual(caption="Forces on a\nloop")],
        session_type=SessionType.LECTURE,
        title="Loops",
    )


def test_figures_become_their_id_and_caption_on_one_line():
    text = with_figures_as_text(notes_with("Before.\n\n::figure{#v1}\n\nAfter."))
    assert text == "Before.\n\n[Figure v1: Forces on a loop]\n\nAfter."


def test_a_figure_missing_from_the_visuals_is_named_without_a_caption():
    assert with_figures_as_text(notes_with("::figure{#gone}")) == "[Figure]"


def test_a_figure_line_inside_fenced_code_is_left_as_code():
    markdown = "```\n::figure{#v1}\n```"
    assert with_figures_as_text(notes_with(markdown)) == markdown


def test_passages_split_at_top_level_headings_and_keep_text_before_the_first():
    found = passages("Intro line.\n\n# Forces\nGravity.\n\n## Speed\nFast.\n\n### Detail\nMore.")
    assert [(p.heading, p.text) for p in found] == [
        (None, "Intro line."),
        ("Forces", "# Forces\nGravity."),
        ("Speed", "## Speed\nFast.\n\n### Detail\nMore."),
    ]


def test_a_hash_line_in_fenced_code_is_not_a_heading():
    assert [p.heading for p in passages("# Code\n```\n# a comment\n```")] == ["Code"]


def test_a_heading_inside_a_step_never_splits_it():
    markdown = "# Calculus\nIntro.\n\n:::step[Integrate the area]\n## By parts\nUse u dv.\n:::"
    assert [p.heading for p in passages(markdown)] == ["Calculus"]
    found = find_passages("T", markdown, ["integrate", "parts"])
    assert [f.heading for f in found] == ["Calculus"]


def test_search_reads_boxes_and_steps_as_the_user_sees_them():
    markdown = ":::box[Key idea]{color=green}\nArea under a curve.\n:::"
    assert find_passages("T", markdown, ["green"]) == []
    assert find_passages("T", markdown, ["box"]) == []
    [found] = find_passages("T", markdown, ["idea", "area"])
    assert found.snippet == "Key idea Area under a curve."


def test_a_closing_fence_never_becomes_part_of_a_heading():
    [passage] = passages(":::box[Warn]{color=red}\nCareful.\n:::\n---\nAfter.")
    assert passage.heading is None
    assert ":::" not in passage.text and "color" not in passage.text


def test_notes_without_headings_are_one_passage_and_empty_notes_none():
    assert [p.heading for p in passages("Just text.\n\nMore.")] == [None]
    assert passages("  \n") == []


def test_query_words_are_case_folded_distinct_and_without_punctuation():
    assert query_words("Pricing, pricing? Q3!") == ["pricing", "q3"]
    assert query_words(" ?! ") == []


def test_score_needs_every_word_and_counts_parts_of_words():
    assert score("Eigenvalues and eigenvectors", ["eigen"]) == 2
    assert score("Eigenvalues only", ["eigen", "matrix"]) == 0
    assert score("anything", []) == 0


def test_snippet_starts_near_the_first_match_and_marks_cut_text():
    text = "word " * 200 + "target here " + "tail " * 200
    piece = snippet(text, ["target"])
    assert piece.startswith("…") and piece.endswith("…")
    assert "target here" in piece
    assert len(piece) == SNIPPET_CHARS + 2


def test_snippet_of_short_text_is_the_whole_text_on_one_line():
    assert snippet("A short\n\npassage.", ["short"]) == "A short passage."


def test_titles_count_towards_every_passage():
    found = find_passages("Standup 3", "# Pricing\nWe keep the price.", ["standup", "price"])
    assert [(f.heading, f.score) for f in found] == [("Pricing", 2)]


def test_a_session_gives_its_best_passages_first_and_only_a_few():
    sections = [f"# Part {i}\n" + "price " * i for i in range(1, 6)]
    found = find_passages("Title", "\n\n".join(sections), ["price"])
    assert len(found) == MAX_PASSAGES_PER_SESSION
    assert [f.heading for f in found] == ["Part 5", "Part 4", "Part 3"]


def test_search_folds_case_and_unicode_forms():
    decomposed = "Cafe\u0301 pricing"
    assert score(decomposed, query_words("CAFÉ")) == 1
    assert score("Straße", query_words("STRASSE")) == 1
    assert score("特征值的定义", query_words("特征值的定义")) == 1


def test_snippet_finds_its_place_when_case_folding_changes_lengths():
    text = "İ" * 400 + " target here"
    assert "target here" in snippet(text, ["target"])


def test_snippet_finds_a_match_that_case_folding_lengthens():
    text = "filler " * 100 + "Hauptstraße 5"
    assert "Hauptstraße 5" in snippet(text, query_words("hauptstrasse"))


@pytest.mark.parametrize(
    ("render_as", "verified", "says"),
    [
        ("svg", True, "Iris's redraw, checked against the screen."),
        ("image", True, "Iris's redraw, checked against the screen."),
        ("screenshot", True, "A crop of the screen, as it was shown."),
        ("screenshot", False, "no redraw of this figure passed Iris's check"),
        ("svg", None, "Iris's redraw, checked against the screen."),
        ("screenshot", None, "A crop of the screen, as it was shown."),
    ],
)
def test_a_figure_says_whether_it_is_a_redraw_or_the_screen(render_as, verified, says):
    visual = (
        notes_with("").visuals[0].model_copy(update={"render_as": render_as, "verified": verified})
    )
    assert says in figure_origin(visual)


def test_a_figure_that_was_never_redrawn_is_its_screen_crop():
    crop = "visuals/v1.source.jpg"
    visual = (
        notes_with("")
        .visuals[0]
        .model_copy(
            update={"render_as": "svg", "verified": None, "path": crop, "source_path": crop}
        )
    )
    assert figure_origin(visual) == "A crop of the screen: Iris didn't redraw this figure."
