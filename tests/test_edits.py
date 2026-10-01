"""The fixer's find/replace edits: exactly-once matching, order, and code taking navigation back afterwards."""

import pytest

from simula.contracts import Edit
from simula.stages import mock, qa
from tests.conftest import APPS, ROOT
from tests.mock_fake import golden, skeleton_html


def edit(find: str, replace: str) -> Edit:
    return Edit(find=find, replace=replace, reason="test")


def test_an_edit_applies_only_when_its_find_matches_exactly_once():
    page = "<p>a</p><p>b</p><p>b</p>"
    out, results = qa.apply_edits(page, [edit("<p>a</p>", "<p>A</p>"), edit("<p>b</p>", "<p>B</p>"),
                                         edit("<p>c</p>", "<p>C</p>"), edit("", "x")])
    assert out == "<p>A</p><p>b</p><p>b</p>"
    assert [r["applied"] for r in results] == [True, False, False, False]
    assert [r["why"] for r in results[1:]] == ["find matches the page 2 times, not once",
                                               "find matches the page 0 times, not once",
                                               "find matches the page 0 times, not once"]


def test_edits_apply_in_order_each_to_the_page_the_last_one_left():
    out, results = qa.apply_edits("<p>a</p>", [edit("a", "b"), edit("b", "c"), edit("a", "z")])
    assert out == "<p>c</p>"
    assert [r["applied"] for r in results] == [True, True, False]


def test_a_find_the_page_repeats_applies_where_it_is_unique_in_the_sections_the_fixer_was_sent():
    """Red team, Perplexity's committed round 0: s01 and s10 draw the same header, so a fixer sent s01 alone sees one
    match. It applies in s01, and s10 keeps its header; a find twice inside the sections sent, or a fixer that saw the
    whole page, still needs one match in the page."""
    page = qa.without_runtime((ROOT / "runs/perplexity/20260929-212810-1f19585/qa/round0/index.html").read_text())
    header = '<div data-chrome="header" style="left:0;top:0;width:411px;height:60px">'
    taller = header.replace("height:60px", "height:64px")
    assert page.count(header) == 2 and qa.excerpt(page, {"s01"}).count(header) == 1

    out, [result] = qa.apply_edits(page, [edit(header, taller)], {"s01"})
    s01, s10 = (page[a:b] for sid in ("s01", "s10") for a, b in qa.section_spans(page, {sid}))
    assert result["applied"] and out == page.replace(s01, s01.replace(header, taller))
    assert s10 in out
    for sections in ({"s01", "s10"}, None):
        _, [result] = qa.apply_edits(page, [edit(header, taller)], sections)
        assert not result["applied"] and result["why"].startswith("find matches the page 2 times, not once")


def test_a_scoped_edit_never_lands_in_a_section_the_fixer_was_not_sent():
    """Fable's M9: sent s01, the fixer edits its `<b>Home</b>` twice with the same find. The second find now matches
    the page once, in s02, which the fixer never saw; it's rejected. A find unique in s01, or in the page's style
    block, still applies."""
    page = ('<style>b{color:red}</style><section data-screen="s01"><b>Home</b></section>'
            '<section data-screen="s02"><b>Home</b></section>')
    out, results = qa.apply_edits(page, [edit("<b>Home</b>", "<b>Start</b>"), edit("<b>Home</b>", "<b>Home!</b>"),
                                         edit("<b>Start</b>", "<b>Go</b>"), edit("color:red", "color:blue")], {"s01"})
    assert [r["applied"] for r in results] == [True, False, True, True]
    assert results[1]["why"] == "find matches the page once, not inside a single section or style block it was sent"
    assert out == ('<style>b{color:blue}</style><section data-screen="s01"><b>Go</b></section>'
                   '<section data-screen="s02"><b>Home</b></section>')


def test_a_find_spanning_two_sent_sections_is_refused_and_says_why():
    page = '<section data-screen="s01"><b>A</b></section><section data-screen="s02"><b>B</b></section>'
    find = '</section><section data-screen="s02">'
    out, [result] = qa.apply_edits(page, [edit(find, find.replace("><", "><hr><", 1))], {"s01", "s02"})
    assert not result["applied"] and out == page
    assert result["why"] == "find matches the page once, not inside a single section or style block it was sent"


STYLED = ('<html><head><style data-batch="1">.card{color:red}</style></head><body>\n'
          '<section data-screen="s01"><p style="color:red">A</p></section>\n'
          '<section data-screen="s02"><p style="color:red">B</p></section>\n</body></html>')


@pytest.mark.parametrize("page", [STYLED, STYLED.replace('<p style="color:red">A', "<p>A")],
                         ids=["twice-in-what-was-sent", "once-in-the-style-block-only"])
def test_a_find_the_fixer_saw_in_a_style_block_never_applies_by_the_section_rule(page):
    """Red team probes E1 and E3: sent s01, the fixer sees `color:red` in the style block (and maybe in s01); the page
    also has it in s02. Seen twice, it breaks the prompt's once-in-what-you-were-given rule; seen once, but in the style
    block, it must be unique in the whole page. Both are rejected, and the page is left alone."""
    out, [result] = qa.apply_edits(page, [edit("color:red", "color:blue")], {"s01"})
    assert not result["applied"] and out == page
    assert result["why"].startswith(f"find matches the page {page.count('color:red')} times, not once")


@pytest.mark.parametrize("app", APPS)
def test_the_fixer_never_sees_the_runtime_and_code_adds_exactly_one_back(app):
    model = golden(app)
    screens = [s.id for s in mock.pick_scope(model)]
    page = qa.rebuild(skeleton_html(model), model, screens)
    shown = qa.without_runtime(page)
    assert "simula-runtime" not in shown and "window.simula" not in shown
    out, results = qa.apply_edits(shown, [edit("window.simula = {", "window.simula = {hijack: 1, ")])
    assert not results[0]["applied"]
    rebuilt = qa.rebuild(out, model, screens)
    assert rebuilt == page
    assert rebuilt.count('id="simula-runtime"') == 1 and rebuilt.count('id="simula-runtime-js"') == 1


@pytest.mark.parametrize("app", APPS)
def test_code_rewires_a_tap_an_edit_dropped_or_changed(app):
    model = golden(app)
    scope = mock.pick_scope(model)
    screens = [s.id for s in scope]
    target = mock.scope_edges(model, scope)[0]
    page = qa.without_runtime(qa.rebuild(skeleton_html(model), model, screens))
    wired = f' data-edge="{target.id}" data-transition="{target.transition}"'
    wrong = f' data-edge="{target.id}" data-transition="{"replace" if target.transition != "replace" else "push"}"'
    for broken in (page.replace(wired, "", 1), page.replace(wired, wrong, 1)):
        tags = [t["attrs"] for t in mock.StartTags(qa.rebuild(broken, model, screens)).tags]
        assert {"data-edge": target.id, "data-transition": target.transition}.items() <= next(
            a for a in tags if a.get("data-edge") == target.id).items()
