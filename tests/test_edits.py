"""The fixer's find/replace edits: exactly-once matching, order, and code taking navigation back afterwards."""

import pytest

from simula.contracts import Edit
from simula.stages import mock, qa
from tests.conftest import APPS
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
