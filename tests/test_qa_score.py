"""The QA score: the formula, dropped terms, masking (copied pixels where they sit over their origin, and dynamic
regions), what earns bounds and nav credit, and the per-screen average."""

import json
import re
import shutil
from itertools import count

import numpy as np
import pytest
from PIL import Image

from simula import qa_metrics
from simula.contracts import Edge, Flow, Rect
from simula.stages import mock, qa
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_gestures import gestured
from tests.test_mock_isolation import ctx_for


def test_the_formula_weights_bounds_nav_and_ssim():
    assert qa.screen_score(1.0, 1.0, 1.0) == pytest.approx(10.0)
    assert qa.screen_score(0.5, 1.0, 0.5) == pytest.approx(10 * (0.25 + 0.3 + 0.1))
    assert qa.screen_score(0.0, 0.0, 0.0) == 0.0
    assert qa.screen_score(1.0, 1.0, -0.2) == qa.screen_score(1.0, 1.0, 0.0)


def test_a_term_with_nothing_to_measure_is_dropped_and_the_rest_reweighted():
    assert qa.screen_score(None, 1.0, 0.5) == pytest.approx(10 * (0.3 + 0.2 * 0.5) / 0.5)
    assert qa.screen_score(0.5, None, 1.0) == pytest.approx(10 * (0.25 + 0.2) / 0.7)
    assert qa.screen_score(0.5, 1.0, None) == pytest.approx(10 * (0.25 + 0.3) / 0.8)
    assert qa.screen_score(None, None, 0.7) == pytest.approx(7.0)
    assert qa.screen_score(None, None, None) == 0.0


@pytest.mark.parametrize("app", APPS)
def test_changes_inside_a_dynamic_region_cost_nothing(app):
    model = golden(app)
    root = mock.pick_scope(model)[0]
    real = Image.open(FIXTURES / "golden" / app / root.canonical_png).convert("RGB")
    region_px = Rect(x=0, y=model.device.content_top_px + 800, w=model.device.w_px, h=400)
    noisy = np.asarray(real).copy()
    noisy[800:1200] = np.random.default_rng(0).integers(0, 256, noisy[800:1200].shape)
    noisy = Image.fromarray(noisy)
    region_dp = qa_metrics.device_to_dp(region_px, model.device)
    assert qa_metrics.compare(real, noisy, [])["ssim"] < 0.95
    assert qa_metrics.compare(real, noisy, [region_dp])["ssim"] > 0.995


@pytest.fixture(params=APPS)
def measured(request, tmp_path):
    """A skeleton mock of the app, measured the way QA round 0 measures a delivered mock."""
    run_dir = seed_model(tmp_path / "run", request.param)
    model = golden(request.param)
    scope = mock.pick_scope(model)
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    html = qa.rebuild(skeleton_html(model), model, [s.id for s in scope])
    return model, scope, qa.measure(ctx_for(run_dir, request.param), model, scope, 0, html)


def test_the_run_score_is_the_mean_of_the_screen_scores(measured):
    _, scope, version = measured
    scores = [s.score for s in version.metrics.screens]
    assert [s.state_id for s in version.metrics.screens] == [s.id for s in scope]
    assert version.score == pytest.approx(sum(scores) / len(scores))
    assert all(0 <= s <= 10 for s in scores)


def test_a_mock_that_places_every_element_and_wires_every_tap_gets_full_bounds_and_nav(measured):
    _, _, version = measured
    for s in version.metrics.screens:
        assert s.bounds_ok_share == 1.0 and s.nav_pass_rate == 1.0
    assert not version.failed_taps() and not version.contract_errors


def test_assets_the_mock_draws_are_masked(measured):
    model, scope, version = measured
    for state, s in zip(scope, version.metrics.screens, strict=True):
        drawn = [e for e in state.elements if mock.usable_asset(e, state.elements, model.device)]
        if drawn or state.dynamic_regions:
            assert s.masked_coverage < 1.0
        else:
            assert s.masked_coverage == 1.0


def test_a_screen_without_tagged_elements_scores_on_nav_and_ssim_only(tmp_path):
    app = APPS[0]
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    first = mock.pick_scope(model)[0]
    states = [s.model_copy(update={"elements": []}) if s.id == first.id else s for s in model.states]
    model = model.model_copy(update={"states": states, "edges": [e for e in model.edges if e.from_state != first.id]})
    scope = mock.pick_scope(model)
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    html = qa.rebuild(skeleton_html(model), model, [s.id for s in scope])
    version = qa.measure(ctx_for(run_dir, app), model, scope, 0, html)
    s = version.metrics.screens[0]
    assert version.screens[0]["tagged"] == 0 and version.screens[0]["taps"] == 0
    assert s.score == pytest.approx(qa.screen_score(None, None, s.ssim_masked))


@pytest.fixture(params=APPS)
def one_screen(request, tmp_path):
    """The first screen that draws copied assets, and a measure() for edits of its skeleton mock."""
    app = request.param
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    state = next(s for s in mock.pick_scope(model)
                 if any(mock.usable_asset(e, s.elements, model.device) for e in s.elements))
    mock.copy_assets(run_dir / "model", run_dir / "mock", [state], model.device)
    rounds = count()

    def measure(edit=lambda html: html):
        html = qa.rebuild(edit(skeleton_html(model, [state.id])), model, [state.id])
        return qa.measure(ctx_for(run_dir, app), model, [state], next(rounds), html)
    return run_dir, state, measure


def coverage(version) -> float:
    return version.metrics.screens[0].masked_coverage


def redraw(draw):
    """An edit that swaps each copied <img> of the skeleton for draw(src, style, attrs)."""
    img = re.compile(r'<img src="(assets/[^"]+\.png)" style="([^"]*)"([^>]*)>')
    return lambda html: img.sub(lambda m: draw(*m.groups()), html)


def with_css(rule: str):
    return lambda html: html.replace("</head>", f"<style>{rule}</style></head>")


def test_copied_assets_are_masked_as_an_img_or_a_css_background(one_screen):
    _, _, measure = one_screen
    base = coverage(measure())
    background = redraw(lambda src, style, attrs: f'<div style="{style};background:url({src}) 0 0/100% 100%"'
                                                  f"{attrs}></div>")
    assert base < 1.0
    assert coverage(measure(background)) == pytest.approx(base)


def test_copies_drawn_away_from_their_origin_mask_nothing_there(one_screen):
    _, _, measure = one_screen
    corner = redraw(lambda src, style, attrs: f'<img src="{src}" style="position:absolute;left:0;top:0;width:4px;'
                                              f'height:4px"{attrs}>')
    assert coverage(measure(corner)) > coverage(measure())


def test_art_crops_are_masked_when_art_json_lists_their_origin(one_screen):
    run_dir, state, measure = one_screen
    base = coverage(measure())
    assets = run_dir / "mock" / "assets"
    for png in list(assets.glob("*.png")):
        shutil.copyfile(png, assets / f"{png.stem}.art.png")
    as_art = redraw(lambda src, style, attrs: f'<img src="{src[:-4]}.art.png" style="{style}"{attrs}>')
    assert coverage(measure(as_art)) > base
    art = {f"assets/{e.id}.art.png": e.rect_dp.model_dump()
           for e in state.elements if (assets / f"{e.id}.png").exists()}
    (run_dir / "mock" / "art.json").write_text(json.dumps({"schema_version": 1, "art": art}))
    assert coverage(measure(as_art)) == pytest.approx(base)


@pytest.mark.parametrize("hide", ["visibility:hidden", "opacity:0"])
def test_an_invisible_data_el_gets_no_bounds_credit(one_screen, hide):
    _, state, measure = one_screen
    eid = sorted(mock.tagged_ids(state))[0]
    version = measure(with_css(f'[data-el="{eid}"]{{{hide}!important}}'))
    assert version.metrics.screens[0].bounds_ok_share < 1.0
    assert eid in [m["id"] for m in version.screens[0]["misses"]]


@pytest.mark.parametrize("app", APPS)
def test_a_tap_target_outside_its_screen_fails(tmp_path, app):
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    scope = mock.pick_scope(model)
    edge = mock.scope_edges(model, scope)[0]
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    html = with_css(f'[data-edge="{edge.id}"]{{top:1500px!important}}')(skeleton_html(model))
    version = qa.measure(ctx_for(run_dir, app), model, scope, 0, qa.rebuild(html, model, [s.id for s in scope]))
    assert {t["edge"]: t["problem"] for t in version.taps}[edge.id] == "the tag sits outside its screen"


@pytest.mark.parametrize("app", APPS)
def test_the_rebuilt_page_opens_on_the_first_screen_that_is_not_a_dialog(app):
    model = golden(app)
    first, *rest = mock.pick_scope(model)
    second = next(s for s in rest if s.parent_id is None)
    dialog = first.model_copy(update={"parent_id": second.id})
    model = model.model_copy(update={"states": [dialog if s.id == first.id else s for s in model.states]})
    screens = [s.id for s in mock.pick_scope(model)]
    assert f'const ROOT = "{second.id}"' in qa.rebuild(skeleton_html(model), model, screens)


@pytest.mark.parametrize("app", APPS)
def test_an_edge_with_no_element_is_never_tapped_and_a_flow_takes_it_by_its_gesture(tmp_path, app):
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    scope = mock.pick_scope(model)
    tap = mock.scope_edges(model, scope)[0]
    back = Edge(id=f"{tap.to_state}.back>{tap.from_state}", from_state=tap.to_state, to_state=tap.from_state,
                element_id=None, action="back", transition="back", change_summary="system back")
    flow = Flow(id="fback", name="There and back", purpose="p", edge_ids=[tap.id, back.id, tap.id], evidence_ids=[])
    model = model.model_copy(update={"edges": [*model.edges, back], "flows": [flow]})
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    html = qa.rebuild(skeleton_html(model), model, [s.id for s in scope])
    version = qa.measure(ctx_for(run_dir, app), model, scope, 0, html)
    assert back.id not in [t["edge"] for t in version.taps] and not version.failed_taps()
    assert version.flows == [{"flow": "fback", "name": "There and back", "status": "passed", "problem": None,
                              "screen": None, "gestures": [back.id]}]


@pytest.mark.parametrize("app", APPS)
def test_the_home_follows_the_mocks_order_not_the_state_order(app):
    """A parent-less dialog listed before the root in the model's states must not become QA's home."""
    model = golden(app)
    root, second, *rest = mock.pick_scope(model)
    dialog = second.model_copy(update={"kind": "modal", "parent_id": None})
    states = [dialog, *(s for s in model.states if s.id != second.id)]
    model = model.model_copy(update={"states": states, "mock_order": [root.id, dialog.id, *(s.id for s in rest)]})
    screens = [s.id for s in mock.pick_scope(model)]
    assert [s.id for s in model.states].index(dialog.id) < [s.id for s in model.states].index(root.id)
    assert mock.home_id(mock.pick_scope(model)) == root.id
    assert f'const ROOT = "{root.id}"' in qa.rebuild(skeleton_html(model), model, screens)


@pytest.mark.parametrize("app", APPS)
def test_art_reused_from_another_screen_earns_no_mask_there(tmp_path, app):
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    scope = mock.pick_scope(model)[:3]
    a_screen, b_screen = scope[:2]

    def apart(r: Rect, q: Rect, gap: float = 8) -> bool:
        return r.x + r.w + gap < q.x or q.x + q.w + gap < r.x or r.y + r.h + gap < q.y or q.y + q.h + gap < r.y
    a, b = next((a, b) for a in a_screen.elements for b in b_screen.elements
                if min(a.rect_dp.w, a.rect_dp.h, b.rect_dp.w, b.rect_dp.h) >= 20 and apart(a.rect_dp, b.rect_dp))
    assets = run_dir / "mock" / "assets"
    assets.mkdir(parents=True)
    for e in (a, b):
        Image.new("RGB", (8, 8), "red").save(run_dir / "mock" / mock.art_src(e.id))
    art = {mock.art_src(e.id): e.rect_dp.model_dump() for e in (a, b)}
    (run_dir / "mock" / "art.json").write_text(json.dumps({"schema_version": 1, "art": art}))

    def img(e) -> str:
        r = e.rect_dp
        box = f"position:absolute;left:{r.x}px;top:{r.y}px;width:{r.w}px;height:{r.h}px"
        return f'<img src="{mock.art_src(e.id)}" style="{box}">'

    def coverage_by_screen(n: int, on_b: str) -> list[float]:
        sections = "".join(f'<section data-screen="{s.id}">{on_b if s is b_screen else ""}</section>' for s in scope)
        html = qa.rebuild(f"<!doctype html><html><head></head><body>{sections}</body></html>", model,
                          [s.id for s in scope])
        return [s.masked_coverage for s in qa.measure(ctx_for(run_dir, app), model, scope, n, html).metrics.screens]
    blank, own, own_and_reused = coverage_by_screen(0, ""), coverage_by_screen(1, img(b)), \
        coverage_by_screen(2, img(b) + img(a))
    assert own[1] < blank[1]
    assert own_and_reused[1] == pytest.approx(own[1])
    assert own_and_reused[0] == blank[0] and own_and_reused[2] == blank[2]


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("left, wallpaper", [(0, False), (30, True)])
def test_a_big_art_crop_passes_the_contract_in_a_qa_round_only_over_its_origin(tmp_path, app, left, wallpaper):
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    scope = mock.pick_scope(model)
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    src = mock.art_src(scope[0].elements[0].id)
    Image.new("RGB", (100, 100), "#223344").save(run_dir / "mock" / src)
    (run_dir / "mock" / "art.json").write_text(
        json.dumps({"schema_version": 1, "art": {src: Rect(x=0, y=0, w=411, h=600).model_dump()}}))
    img = f'<img src="{src}" style="position:absolute;left:{left}px;top:0;width:411px;height:600px">'
    html = qa.rebuild(skeleton_html(model).replace("</section>", img + "</section>", 1), model, [s.id for s in scope])
    version = qa.measure(ctx_for(run_dir, app), model, scope, 0, html)
    assert any(e.kind == "wallpaper" and e.screen == scope[0].id for e in version.contract_errors) == wallpaper


def walk(tmp_path, app, model, flow_edges: list[str], edit=lambda html: html) -> dict:
    """Measures the rebuilt skeleton page of model with one flow over flow_edges; returns that flow's walk."""
    run_dir = seed_model(tmp_path / "run", app)
    scope = mock.pick_scope(model)
    model = model.model_copy(update={"flows": [Flow(id="fw", name="w", purpose="p", edge_ids=flow_edges,
                                                    evidence_ids=[])]})
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    html = edit(qa.rebuild(skeleton_html(model), model, [s.id for s in scope]))
    return qa.measure(ctx_for(run_dir, app), model, scope, 0, html).flows[0]


@pytest.mark.parametrize("app", APPS)
def test_a_flow_of_a_swipe_a_back_and_typing_is_walked_by_real_input(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    hops = [f"{a}.swipe>{c}", f"{c}.back>{a}", f"{a}.type>{d}"]
    assert walk(tmp_path, app, model, hops) == {"flow": "fw", "name": "w", "status": "passed", "problem": None,
                                                "screen": None, "gestures": hops}


@pytest.mark.parametrize("app", APPS)
def test_a_back_edge_on_a_drawn_back_control_is_tapped_not_gestured(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    control = (f'<button data-edge="{c}.back>{a}" data-transition="back" '
               'style="position:absolute;left:8px;top:8px;width:40px;height:40px;z-index:9"></button>')

    def draw_control(html):
        opening = re.search(rf'<section[^>]*data-screen="{c}"[^>]*>', html).end()
        return html[:opening] + control + html[opening:]
    result = walk(tmp_path, app, model, [f"{a}.swipe>{c}", f"{c}.back>{a}"], draw_control)
    assert (result["status"], result["gestures"]) == ("passed", [f"{a}.swipe>{c}"])


@pytest.mark.parametrize("app", APPS)
def test_a_hop_the_page_cannot_perform_fails_the_flow_instead_of_being_jumped(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    result = walk(tmp_path, app, model, [f"{a}.swipe>{c}", f"{c}.back>{a}"], lambda html: mock.ACTIONS_BLOCK.sub("", html))
    assert (result["status"], result["screen"]) == ("failed", a)
    assert result["problem"] == f"{a}.swipe>{c}: the page has no swipe from {a} to {c}"


@pytest.mark.parametrize("app", APPS)
def test_the_fixer_never_sees_the_gesture_map_and_rebuild_writes_it_back(app):
    model, *_ = gestured(app)
    screens = [s.id for s in mock.pick_scope(model)]
    page = qa.rebuild(skeleton_html(model), model, screens)
    assert 'id="simula-actions"' in page and 'id="simula-actions"' not in qa.without_runtime(page)
    assert qa.rebuild(qa.without_runtime(page), model, screens) == page


@pytest.mark.parametrize("app", APPS)
def test_a_typing_hop_that_stays_on_its_screen_passes_only_when_the_field_shows_the_text(tmp_path, app):
    model, tap, a, c, d, field = gestured(app)
    stay = Edge(id=f"{a}.type>{a}", from_state=a, to_state=a, element_id=None, action="type", transition="push",
                change_summary="+'hello'")
    model = model.model_copy(update={"edges": [e for e in model.edges if e.action != "type"] + [stay]})
    assert walk(tmp_path / "editable", app, model, [stay.id])["status"] == "passed"
    frozen = walk(tmp_path / "frozen", app, model, [stay.id],
                  lambda html: html.replace("f.contentEditable = 'plaintext-only';", ""))
    assert (frozen["status"], frozen["problem"]) == ("failed", f"{stay.id}: the text field didn't take the typing")
