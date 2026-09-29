"""Edges that start on no element: code writes their map into the page, and the runtime performs each one from its
gesture (a swipe, the system back, typing into the screen's text field). Runs on every golden."""

import json

import pytest

from simula.contracts import Edge
from simula.render import open_mock
from simula.stages import mock
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, skeleton_html


@pytest.fixture(params=APPS)
def app(request):
    return request.param


def gestured(app: str):
    """A golden with three element-less edges added: a swipe from the first tap edge's screen `a` to `c`, a back
    from `c` to `a`, and typing on `a` that lands on `d`. One tagged element of `a` becomes its text field."""
    model = golden(app)
    scope = mock.pick_scope(model)
    tap = mock.scope_edges(model, scope)[0]
    a = tap.from_state
    c, d = [s.id for s in scope if s.id not in (a, tap.to_state)][:2]
    state = next(s for s in scope if s.id == a)
    field = next(e.id for e in state.elements if e.id in mock.tagged_ids(state) and not e.asset_png
                 and uncovered(e, state.elements) and e.id not in {x.element_id for x in model.edges})
    states = [s.model_copy(update={"elements": [e.model_copy(update={"type": "EditText"}) if e.id == field else e
                                                for e in s.elements]}) if s.id == a else s for s in model.states]
    extra = [Edge(id=f"{a}.swipe>{c}", from_state=a, to_state=c, element_id=None, action="swipe", transition="push",
                  change_summary=""),
             Edge(id=f"{c}.back>{a}", from_state=c, to_state=a, element_id=None, action="back", transition="back",
                  change_summary=""),
             Edge(id=f"{a}.type>{d}", from_state=a, to_state=d, element_id=None, action="type", transition="push",
                  change_summary="+'hello'")]
    model = model.model_copy(update={"states": states, "edges": model.edges + extra})
    return model, tap, a, c, d, field


def uncovered(e, elements) -> bool:
    """No in_mock element drawn after e (the fake builder draws in element order) covers e's center."""
    r = e.rect_dp
    x, y = r.x + r.w / 2, r.y + r.h / 2
    later = elements[elements.index(e) + 1:]
    return min(r.w, r.h) >= 16 and not any(o.in_mock and o.rect_dp.x <= x <= o.rect_dp.x + o.rect_dp.w
                                           and o.rect_dp.y <= y <= o.rect_dp.y + o.rect_dp.h for o in later)


def write_mock(tmp_path, model):
    scope = mock.pick_scope(model)
    screens = [s.id for s in scope]
    mock_dir = tmp_path / "mock"
    mock.copy_assets(FIXTURES / "golden" / model.app, mock_dir, scope, model.device)
    html = mock.with_runtime(mock.wire_edges(skeleton_html(model), model, screens), mock.home_id(scope))
    (mock_dir / "index.html").write_text(html)
    return mock_dir


def state(page) -> str:
    return page.evaluate("() => window.simula.state()")


def drag(page, x0: float, y0: float, x1: float, y1: float) -> None:
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=8)
    page.mouse.up()


def test_the_map_lists_each_screens_swipe_back_and_type_with_its_text_field(app):
    model, tap, a, c, d, field = gestured(app)
    screens = [s.id for s in mock.pick_scope(model)]
    assert mock.gestures(model, screens) == {a: {"swipe": [c, "push", None], "type": [d, "push", field]},
                                             c: {"back": [a, "back", None]}}
    assert mock.gestures(model, [s for s in screens if s != d]) == {a: {"swipe": [c, "push", None]},
                                                                   c: {"back": [a, "back", None]}}


def test_a_tap_without_an_element_and_a_second_swipe_stay_out_of_the_map(app):
    model, tap, a, c, d, field = gestured(app)
    stray = [Edge(id=f"{a}.tap>{d}", from_state=a, to_state=d, element_id=None, action="tap", transition="push",
                  change_summary=""),
             Edge(id=f"{a}.swipe>{d}", from_state=a, to_state=d, element_id=None, action="swipe", transition="push",
                  change_summary="")]
    model = model.model_copy(update={"edges": model.edges + stray})
    assert mock.gestures(model, [s.id for s in mock.pick_scope(model)])[a]["swipe"] == [c, "push", None]
    assert "tap" not in mock.gestures(model, [s.id for s in mock.pick_scope(model)])[a]


def test_wire_edges_writes_the_map_once_and_rewriting_replaces_it(app):
    model, *_ = gestured(app)
    screens = [s.id for s in mock.pick_scope(model)]
    wired = mock.wire_edges(skeleton_html(model), model, screens)
    assert wired.count('id="simula-actions"') == 1
    assert mock.wire_edges(wired, model, screens) == wired
    block = wired.split('<script id="simula-actions" type="application/json">')[1].split("</script>")[0]
    assert json.loads(block) == mock.gestures(model, screens)


def test_a_drag_performs_the_swipe_and_never_the_tap_it_started_on(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    with open_mock(write_mock(tmp_path, model)) as (page, _):
        page.evaluate("id => window.simula.go(id)", a)
        box = page.locator(f'[data-screen="{a}"] [data-edge="{tap.id}"]').first.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        drag(page, x, y, x, y - 200 if y > 300 else y + 200)
        assert state(page) == c
        page.evaluate("id => window.simula.go(id)", a)
        page.locator(f'[data-screen="{a}"] [data-edge="{tap.id}"]').first.click()
        assert state(page) == tap.to_state


def test_escape_and_a_drag_in_from_the_left_edge_perform_the_back(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    with open_mock(write_mock(tmp_path, model)) as (page, _):
        page.evaluate("id => window.simula.go(id)", c)
        page.keyboard.press("Escape")
        assert state(page) == a
        page.evaluate("id => window.simula.go(id)", c)
        drag(page, 4, 450, 250, 460)
        assert state(page) == a


def test_typing_and_enter_in_the_text_field_performs_the_type_edge(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    with open_mock(write_mock(tmp_path, model)) as (page, _):
        page.evaluate("id => window.simula.go(id)", a)
        box = page.locator(f'[data-screen="{a}"] [data-el="{field}"]').first
        box.click()
        assert state(page) == a
        page.keyboard.type("hello")
        assert box.inner_text() == "hello"
        page.keyboard.press("Enter")
        assert state(page) == d


def test_a_gesture_with_no_edge_on_its_screen_does_nothing(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    with open_mock(write_mock(tmp_path, model)) as (page, _):
        page.evaluate("id => window.simula.go(id)", d)
        drag(page, 200, 600, 200, 300)
        page.keyboard.press("Escape")
        assert state(page) == d
