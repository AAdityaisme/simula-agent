"""QA's navigation check: every in-scope model edge is tapped with Playwright's hit-testing on its own screen, its
transition is checked, and every core flow is walked by tapping."""

import pytest

from simula.render import open_mock
from simula.stages import mock, qa
from tests.conftest import APPS
from tests.mock_fake import golden
from tests.test_contract import inject
from tests.test_render import write_mock


@pytest.fixture(params=APPS)
def model(request):
    return golden(request.param)


def edited(mock_dir, change) -> None:
    page = mock_dir / "index.html"
    page.write_text(change(page.read_text()))


def tag_of(edge) -> str:
    return f' data-edge="{edge.id}" data-transition="{edge.transition}"'


def test_every_edge_of_a_well_wired_mock_passes(tmp_path, model):
    mock_dir, _ = write_mock(tmp_path, model)
    scope = mock.pick_scope(model)
    with open_mock(mock_dir) as (page, _):
        taps = qa.check_taps(page, model, scope)
    assert [t["edge"] for t in taps] == [e.id for e in mock.scope_edges(model, scope)]
    assert [t for t in taps if t["problem"]] == []


def test_each_flow_is_walked_or_reported_as_out_of_scope(tmp_path, model):
    mock_dir, _ = write_mock(tmp_path, model)
    scope = mock.pick_scope(model)
    in_scope = {e.id for e in mock.scope_edges(model, scope)}
    with open_mock(mock_dir) as (page, _):
        walks = qa.walk_flows(page, model, scope)
    assert [w["flow"] for w in walks] == [f.id for f in model.flows]
    for flow, walk in zip(model.flows, walks, strict=True):
        walkable = bool(flow.edge_ids) and set(flow.edge_ids) <= in_scope
        assert walk["status"] == ("passed" if walkable else "out_of_scope"), walk


def test_a_covered_tap_target_fails(tmp_path, model):
    edge = mock.scope_edges(model, mock.pick_scope(model))[0]
    mock_dir, _ = write_mock(tmp_path, model)
    cover = '<div style="position:absolute;inset:0;z-index:99"></div>'
    edited(mock_dir, lambda html: inject(html, edge.from_state, cover))
    with open_mock(mock_dir) as (page, _):
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        problem = qa.tap(page, edge)
    assert problem and "never landed" in problem and "intercepts pointer events" in problem


def test_a_missing_or_misplaced_tag_fails(tmp_path, model):
    edge = mock.scope_edges(model, mock.pick_scope(model))[0]
    mock_dir, _ = write_mock(tmp_path, model)
    edited(mock_dir, lambda html: html.replace(tag_of(edge), "", 1))
    with open_mock(mock_dir) as (page, _):
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        assert qa.tap(page, edge) == "no data-edge tag on its screen"
    edited(mock_dir, lambda html: inject(html, edge.to_state, f"<div{tag_of(edge)}>x</div>"))
    with open_mock(mock_dir) as (page, _):
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        assert qa.tap(page, edge) == "no data-edge tag on its screen"


def test_a_wrong_transition_fails_and_an_unknown_one_is_not_checked(tmp_path, model):
    edge = mock.scope_edges(model, mock.pick_scope(model))[0]
    other = "replace" if edge.transition != "replace" else "push"
    mock_dir, _ = write_mock(tmp_path, model)
    edited(mock_dir, lambda html: html.replace(tag_of(edge), f' data-edge="{edge.id}" data-transition="{other}"', 1))
    with open_mock(mock_dir) as (page, _):
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        assert qa.tap(page, edge) == f"data-transition={other!r}, want {edge.transition!r}"
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        assert qa.tap(page, edge.model_copy(update={"transition": "unknown"})) is None


def test_a_flow_that_breaks_names_the_hop(tmp_path, model):
    scope = mock.pick_scope(model)
    edges = {e.id: e for e in mock.scope_edges(model, scope)}
    flow = next(f for f in model.flows if f.edge_ids and set(f.edge_ids) <= set(edges))
    last = edges[flow.edge_ids[-1]]
    mock_dir, _ = write_mock(tmp_path, model)
    edited(mock_dir, lambda html: html.replace(tag_of(last), "", 1))
    with open_mock(mock_dir) as (page, _):
        [walk] = [w for w in qa.walk_flows(page, model, scope) if w["flow"] == flow.id]
    assert walk["status"] == "failed"
    assert walk["problem"].startswith(f"{last.id}: ")
