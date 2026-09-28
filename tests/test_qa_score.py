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
from simula.contracts import Rect
from simula.stages import mock, qa
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, seed_model, skeleton_html
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
        drawn = [e for e in state.elements if mock.usable_asset(e, model.device)]
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
    state = next(s for s in mock.pick_scope(model) if any(mock.usable_asset(e, model.device) for e in s.elements))
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
