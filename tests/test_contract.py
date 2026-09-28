"""The mock contract validator: a good mock passes, a broken one fails with named errors. Runs on every golden."""

import pytest
from PIL import Image

from simula.render import check_contract, open_mock, render_and_validate
from simula.stages.mock import copy_assets, pick_scope, scope_edges, with_runtime
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, skeleton_html


@pytest.fixture(params=APPS)
def model(request):
    return golden(request.param)


def two_screens(model) -> list[str]:
    edge = scope_edges(model, pick_scope(model))[0]
    return [edge.from_state, edge.to_state]


def validate(tmp_path, model, html, screens):
    mock_dir = tmp_path / "mock"
    copy_assets(FIXTURES / "golden" / model.app, mock_dir, [s for s in model.states if s.id in screens], model.device)
    (mock_dir / "index.html").write_text(with_runtime(html, screens[0]))
    return render_and_validate(mock_dir, model, screens)


def inject(html: str, screen: str, snippet: str) -> str:
    opening = html.index(f'data-screen="{screen}"')
    at = html.index(">", opening) + 1
    return html[:at] + snippet + html[at:]


def test_two_screen_mock_passes(tmp_path, model):
    screens = two_screens(model)
    report = validate(tmp_path, model, skeleton_html(model, screens), screens)
    assert report.passed, report.errors
    assert report.screens == screens


def test_every_in_scope_screen_passes_including_elementless_ones(tmp_path, model):
    screens = [s.id for s in pick_scope(model)]
    report = validate(tmp_path, model, skeleton_html(model), screens)
    assert report.passed, report.errors
    assert sorted(p.stem for p in (tmp_path / "mock" / "renders").glob("*.png")) == sorted(screens)


def test_broken_mock_fails_with_named_errors(tmp_path, model):
    first, second = two_screens(model)
    html = skeleton_html(model, [first, second])
    edge_attr = html.index("data-edge=")
    html = html[:edge_attr] + html[edge_attr:].replace(" data-transition=", " data-x=", 1)
    Image.new("RGB", (8, 8), "red").save((tmp_path / "wall.png"))
    html = inject(html, first, f'<div data-edge="{first}.e999>nowhere" data-transition="push"></div>'
                               '<img src="assets/missing.png">'
                               '<img src="assets/wall.png" style="position:absolute;left:0;top:0;width:411px;height:600px">'
                               '<img src="https://example.com/tracker.png">'
                               '<div data-el="s99.e01"></div>'
                               '<script>throw new Error("boom")</script>')
    html = html.replace(f'data-screen="{second}"', 'data-screen="s99"')
    mock_dir = tmp_path / "mock"
    mock_dir.mkdir()
    (mock_dir / "assets").mkdir()
    (tmp_path / "wall.png").rename(mock_dir / "assets" / "wall.png")
    report = validate(tmp_path, model, html, [first, second])

    assert not report.passed
    kinds = {e.kind for e in report.errors}
    assert {"dangling_edge", "unknown_edge", "missing_transition", "missing_asset", "wallpaper", "foreign_image",
            "unknown_el", "console_error", "missing_screen", "unknown_screen", "blocked_request"} <= kinds
    assert any(e.kind == "wallpaper" and e.screen == first for e in report.errors)
    assert any(e.kind == "missing_screen" and e.screen == second for e in report.errors)


def test_a_wrong_transition_is_named(tmp_path, model):
    screens = two_screens(model)
    edge = scope_edges(model, pick_scope(model))[0]
    other = "push" if edge.transition != "push" else "modal"
    html = skeleton_html(model, screens).replace(f'data-transition="{edge.transition}"', f'data-transition="{other}"', 1)
    report = validate(tmp_path, model, html, screens)
    assert [e.kind for e in report.errors] == ["wrong_transition"]


def test_a_page_without_the_runtime_fails_once(tmp_path, model):
    screens = two_screens(model)
    mock_dir = tmp_path / "mock"
    mock_dir.mkdir()
    (mock_dir / "index.html").write_text(skeleton_html(model, screens))
    with open_mock(mock_dir) as (page, log):
        errors = check_contract(page, log, mock_dir, model, screens)
    assert [e.kind for e in errors] == ["missing_api"]


def test_missing_and_misplaced_edges_are_named(tmp_path, model):
    first, second = two_screens(model)
    edge = next(e for e in scope_edges(model, pick_scope(model)) if e.from_state == first and e.to_state == second)
    html = skeleton_html(model, [first, second])
    tag = f' data-edge="{edge.id}" data-transition="{edge.transition}"'
    missing = validate(tmp_path / "missing", model, html.replace(tag, "", 1), [first, second])
    assert ("missing_edge", first) in {(e.kind, e.screen) for e in missing.errors}
    moved = inject(html.replace(tag, "", 1), second, f"<div{tag}></div>")
    misplaced = validate(tmp_path / "misplaced", model, moved, [first, second])
    assert ("wrong_screen", second) in {(e.kind, e.screen) for e in misplaced.errors}
    assert "missing_edge" not in {e.kind for e in misplaced.errors}
