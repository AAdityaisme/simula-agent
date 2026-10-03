"""Rendering geometry and the code-owned navigation runtime, on every golden."""

import ast
import logging
import re

import pytest
from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from simula import render
from simula.contracts import Edge
from simula.render import content_dp, open_mock, render_and_validate
from simula.render import CAPTURE_REFUSED, screenshot
from simula.stages.mock import copy_assets, pick_scope, scope_edges, with_runtime
from tests.conftest import APPS, FIXTURES, ROOT
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


def test_a_google_fonts_link_is_blocked_and_breaks_the_contract(tmp_path, model):
    """A mock carries its own fonts; a page that still links Google Fonts is not self-contained."""
    mock_dir, screens = write_mock(tmp_path, model)
    html = (mock_dir / "index.html").read_text()
    url = "https://fonts.googleapis.com/css2?family=Roboto"
    (mock_dir / "index.html").write_text(html.replace("</head>", f'<link rel="stylesheet" href="{url}"></head>', 1))
    report = render_and_validate(mock_dir, model, screens)
    assert [(e.kind, e.detail) for e in report.errors] == [("blocked_request", url)]


def test_an_edge_with_no_element_is_not_a_missing_edge(tmp_path, model):
    home, other = [s.id for s in pick_scope(model)[:2]]
    back = Edge(id=f"{other}.back>{home}", from_state=other, to_state=home, element_id=None, action="back",
                transition="back", change_summary="system back")
    model = model.model_copy(update={"edges": model.edges + [back]})
    mock_dir, screens = write_mock(tmp_path, model)
    report = render_and_validate(mock_dir, model, screens)
    assert report.passed, report.errors


def test_every_image_is_loaded_before_the_first_screenshot_even_a_lazy_one_on_a_hidden_screen(tmp_path, model):
    mock_dir, screens = write_mock(tmp_path, model)
    Image.new("RGB", (64, 64), "red").save(mock_dir / "assets" / "lazy.png")
    lazy = '<img loading="lazy" src="assets/lazy.png" style="width:40px;height:40px">'
    page_html = (mock_dir / "index.html").read_text()
    page_html = re.sub(rf'<section data-screen="{screens[1]}"[^>]*>', lambda m: m.group(0) + lazy, page_html, count=1)
    (mock_dir / "index.html").write_text(page_html)
    with open_mock(mock_dir) as (page, _):
        loaded = page.evaluate("() => [...document.images].every(i => i.complete && i.naturalWidth > 0)")
    assert loaded
    assert render_and_validate(mock_dir, model, screens).passed


class RefusingPage:
    """A page whose first `refusals` captures Chromium refuses with `error`."""
    def __init__(self, refusals: int, error: str = CAPTURE_REFUSED):
        self.calls, self.refusals, self.error = 0, refusals, error

    def screenshot(self, **options) -> bytes:
        self.calls += 1
        if self.calls <= self.refusals:
            raise PlaywrightError(f"Page.screenshot: Protocol error (Page.captureScreenshot): {self.error}")
        return b"png"


def test_a_refused_capture_is_tried_once_more_logged_and_nothing_else_is(caplog):
    caplog.set_level(logging.WARNING, logger="simula.render")
    once = RefusingPage(1)
    assert screenshot(once, animations="disabled") == b"png" and once.calls == 2
    warned = f"Chromium refused a screenshot ({CAPTURE_REFUSED}); trying once more"
    assert [r.getMessage() for r in caplog.records] == [warned]
    twice = RefusingPage(2)
    with pytest.raises(PlaywrightError, match=CAPTURE_REFUSED):
        screenshot(twice)
    assert twice.calls == 2 and len(caplog.records) == 2
    closed = RefusingPage(1, error="Target page, context or browser has been closed")
    with pytest.raises(PlaywrightError, match="has been closed"):
        screenshot(closed)
    assert closed.calls == 1 and len(caplog.records) == 2


# (file, receiver) pairs whose `.screenshot` isn't a Playwright capture, each with why. The syntax tree can't tell a
# data field or a device client from a browser page, so each one is named here.
NOT_BROWSER: dict[tuple[str, str], str] = {
    ("simula/stages/model.py", "sf"): "StateFile.screenshot, the saved file name of an explore capture",
    ("simula/stages/model.py", "capture"): "LaterCapture.screenshot, the saved file name of a later capture",
    ("simula/stages/explore.py", "self.phone"): "the mobile-mcp device client shooting the phone, not Playwright",
    ("simula/stages/explore.py", "self.later[-1]"): "LaterCapture.screenshot, the saved file name of a later capture",
    ("simula/stages/explore.py", "home.later[-2]"): "LaterCapture.screenshot, the saved file name of a later capture",
    ("simula/qa_live.py", "self.phone"): "the mobile-mcp device client shooting the phone, not Playwright",
    ("simula/qa_live.py", "sf"): "StateFile.screenshot, the saved file name of an explore capture",
}


def browser_captures(relative: str, source: str) -> tuple[list[int], int]:
    """Lines of the `.screenshot` references in one file (page, frame, tab, locator; called in place, bound to a name,
    or handed to a pool) that aren't render.screenshot, the helper's own, or a receiver in NOT_BROWSER, and how many
    are the helper's own. References come from the syntax tree, so spacing, line breaks, comments, and strings can't
    hide or fake one."""
    tree = ast.parse(source)
    helper = next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                   and node.name == "screenshot"), None) if relative == "simula/render.py" else None
    inside = {id(node) for node in ast.walk(helper)} if helper else set()
    direct, helper_refs = [], 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "screenshot"):
            continue
        receiver = ast.unparse(node.value)
        if receiver == "render" or (relative, receiver) in NOT_BROWSER:
            continue
        if id(node) in inside:
            helper_refs += 1
        else:
            direct.append(node.lineno)
    return direct, helper_refs


def test_every_browser_capture_goes_through_render_screenshot():
    direct, helper_refs = [], 0
    for path in sorted((ROOT / "simula").rglob("*.py")):
        relative = str(path.relative_to(ROOT))
        lines, refs = browser_captures(relative, path.read_text())
        direct += [f"{relative}:{n}" for n in lines]
        helper_refs += refs
    assert direct == [] and helper_refs == 2, (direct, helper_refs)


@pytest.mark.parametrize("source, caught", [
    ('page.locator("a").screenshot(\n    path="x.png")', [1]),
    ("capture = page.screenshot\ncapture(path='x.png')", [1]),
    ("pool.submit(page.screenshot, path='x.png')", [1]),
    ("page.screenshot (path='x.png')  # render.screenshot( is right", [1]),
    ("render.screenshot(page, path='x.png')", []),
    ("screenshot(page, path='x.png')", []),
])
def test_the_capture_guard_catches_every_way_a_direct_capture_can_be_written(source, caught):
    assert browser_captures("simula/stages/flows/walk.py", source)[0] == caught


def test_the_capture_guard_skips_a_named_receiver_only_in_its_own_file():
    assert browser_captures("simula/stages/model.py", "Image.open(sf.screenshot)")[0] == []
    assert browser_captures("simula/stages/flows/walk.py", "Image.open(sf.screenshot)")[0] == [1]
