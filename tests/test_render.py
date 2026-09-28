"""Rendering geometry and the code-owned navigation runtime, on every golden."""

import pytest
from PIL import Image

from simula import render
from simula.render import content_dp, open_mock, render_and_validate
from simula.stages.mock import copy_assets, pick_scope, scope_edges, with_runtime
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, skeleton_html


@pytest.fixture(params=APPS)
def model(request):
    return golden(request.param)


def write_mock(tmp_path, model):
    mock_dir = tmp_path / "mock"
    scope = pick_scope(model)
    copy_assets(FIXTURES / "golden" / model.app, mock_dir, scope, model.device)
    (mock_dir / "index.html").write_text(with_runtime(skeleton_html(model), scope[0].id))
    return mock_dir, [s.id for s in scope]


def click(page, edge_id):
    page.locator(f'[data-edge="{edge_id}"]').click(timeout=3000)
    return page.evaluate("() => [window.simula.state(), document.body.dataset.transition]")


def test_renders_are_device_size_and_crop_to_content_dp(tmp_path, model):
    mock_dir, screens = write_mock(tmp_path, model)
    render_and_validate(mock_dir, model, screens)
    for sid in screens:
        render = Image.open(mock_dir / "renders" / f"{sid}.png")
        assert render.size == (1079, 2399)
        assert content_dp(render).size == (411, 838)
    real = Image.open(FIXTURES / "golden" / model.app / pick_scope(model)[0].canonical_png)
    assert content_dp(real).size == (411, 838)


def test_every_edge_click_lands_on_its_target_with_its_transition(tmp_path, model):
    mock_dir, screens = write_mock(tmp_path, model)
    with open_mock(mock_dir) as (page, _):
        for edge in scope_edges(model, pick_scope(model)):
            page.evaluate("id => window.simula.go(id)", edge.from_state)
            assert click(page, edge.id) == [edge.to_state, edge.transition]
            assert page.evaluate("() => document.getAnimations().length") == 0


def test_unknown_transition_renders_as_push(tmp_path, model):
    edge = scope_edges(model, pick_scope(model))[0]
    model = model.model_copy(update={"edges": [e.model_copy(update={"transition": "unknown"}) if e.id == edge.id else e
                                               for e in model.edges]})
    mock_dir, screens = write_mock(tmp_path, model)
    assert render_and_validate(mock_dir, model, screens).passed
    with open_mock(mock_dir) as (page, _):
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        assert click(page, edge.id) == [edge.to_state, "push"]


def test_a_modal_draws_over_its_parent_and_reset_returns_to_root(tmp_path, model):
    mock_dir, screens = write_mock(tmp_path, model)
    shown = "id => getComputedStyle(document.querySelector(`[data-screen=\"${id}\"]`)).display !== 'none'"
    modals = [s for s in pick_scope(model) if s.parent_id in screens]
    with open_mock(mock_dir) as (page, _):
        for modal in modals:
            page.evaluate("id => window.simula.go(id)", modal.id)
            assert page.evaluate(shown, modal.id) and page.evaluate(shown, modal.parent_id)
        page.evaluate("() => window.simula.reset()")
        assert page.evaluate("() => window.simula.state()") == screens[0]
        assert [sid for sid in screens if page.evaluate(shown, sid)] == [screens[0]]


def test_an_overlay_never_paints_over_its_parent(tmp_path, model):
    screens = [s.id for s in pick_scope(model)]
    modals = [s for s in pick_scope(model) if s.parent_id in screens]
    mock_dir = tmp_path / "mock"
    copy_assets(FIXTURES / "golden" / model.app, mock_dir, pick_scope(model), model.device)
    opaque = skeleton_html(model).replace("</head>", "<style>section[data-screen]{background:#000}</style></head>")
    (mock_dir / "index.html").write_text(with_runtime(opaque, screens[0]))
    background = "id => getComputedStyle(document.querySelector(`[data-screen=\"${id}\"]`)).backgroundColor"
    with open_mock(mock_dir) as (page, _):
        for modal in modals:
            page.evaluate("id => window.simula.go(id)", modal.id)
            assert page.evaluate(background, modal.id) == "rgba(0, 0, 0, 0)"
            assert page.evaluate(background, modal.parent_id) == "rgb(0, 0, 0)"


def test_fonts_that_fail_to_load_are_not_a_contract_error(tmp_path, model, monkeypatch):
    monkeypatch.setattr(render, "FONT_HOSTS", ("fonts.invalid",))
    mock_dir, screens = write_mock(tmp_path, model)
    html = (mock_dir / "index.html").read_text()
    link = '<link rel="stylesheet" href="https://fonts.invalid/css2?family=Roboto">'
    (mock_dir / "index.html").write_text(html.replace("</head>", link + "</head>", 1))
    report = render_and_validate(mock_dir, model, screens)
    assert report.passed, report.errors
