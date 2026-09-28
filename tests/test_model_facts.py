"""Stage 2's code facts, checked against each golden model built from the same captures. App-neutral."""

import pytest
from PIL import Image

from simula.contracts import Device, ProductModel
from simula.stages import model as stage
from tests.conftest import APPS, FIXTURES
from tests.explore_fixture import build

DEVICE = Device()


@pytest.fixture(params=APPS)
def app(request, tmp_path):
    explore = build(request.param, tmp_path / "explore")
    golden = ProductModel.model_validate_json((FIXTURES / "golden" / request.param / "product_model.json").read_text())
    states, images = stage.load_states(explore, DEVICE)
    edges, skipped = stage.load_edges(explore, states)
    return golden, states, images, edges, skipped


def test_states_carry_explore_kind_parent_and_regions(app):
    golden, states, _, _, _ = app
    assert [(s.id, s.kind, s.parent_id, s.dynamic_regions) for s in states] == \
           [(s.id, s.kind, s.parent_id, s.dynamic_regions) for s in golden.states]
    assert all((s.blocked_reason is not None) == (s.kind == "blocked") for s in states)


def test_element_facts_match_the_golden(app):
    golden, states, _, _, _ = app
    ours = {e.id: e for s in states for e in s.elements}
    theirs = {e.id: e for s in golden.states for e in s.elements}
    assert ours.keys() == theirs.keys()
    for eid, e in ours.items():
        g = theirs[eid]
        assert (e.mcp_ref, e.type, e.text, e.rect_px, e.rect_dp, e.fg_hex, e.bg_hex, e.font_px) == \
               (g.mcp_ref, g.type, g.text, g.rect_px, g.rect_dp, g.fg_hex, g.bg_hex, g.font_px), eid


def test_rect_dp_follows_the_formula(app):
    _, states, _, _, _ = app
    for e in (e for s in states for e in s.elements):
        assert e.rect_dp.x == pytest.approx(e.rect_px.x / DEVICE.scale, abs=0.01)
        assert e.rect_dp.y == pytest.approx((e.rect_px.y - DEVICE.content_top_px) / DEVICE.scale, abs=0.01)
        assert DEVICE.content_top_px <= e.rect_px.y < DEVICE.content_bottom_px


def test_edges_come_from_explore_with_their_transitions(app):
    golden, _, _, edges, skipped = app
    assert not skipped
    assert [(e.id, e.from_state, e.to_state, e.element_id, e.transition) for e in edges] == \
           [(e.id, e.from_state, e.to_state, e.element_id, e.transition) for e in golden.edges]


def test_moves_that_do_not_change_state_are_not_edges(tmp_path):
    explore = build(APPS[0], tmp_path / "explore")
    with open(explore / "actions.jsonl", "a") as f:
        f.write('{"from_state": "s01", "to_state": "s01", "action": "swipe", "mcp_ref": null, "outcome": "ok"}\n')
        f.write('{"from_state": "s01", "to_state": "s02", "action": "tap", "mcp_ref": "@e9", "outcome": "denied"}\n')
        f.write('{"from_state": "s01", "to_state": "s99", "action": "back", "mcp_ref": null, "outcome": "ok"}\n')
    states, _ = stage.load_states(explore, DEVICE)
    edges, skipped = stage.load_edges(explore, states)
    assert not any(e.from_state == e.to_state or e.to_state == "s99" for e in edges)
    assert skipped == ["action s01>s99: unknown state"]


def test_canonical_png_is_the_content_crop(app, tmp_path):
    _, states, images, _, _ = app
    for s in states:
        png = stage.content_png(images[s.id], DEVICE)
        assert png.size == (DEVICE.w_px, DEVICE.content_bottom_px - DEVICE.content_top_px)


def test_assets_are_cropped_to_their_rect_for_in_scope_states_only(app, tmp_path):
    _, states, images, _, _ = app
    (tmp_path / "assets").mkdir()
    scope = {states[0].id}
    finished = [stage.finish_elements(s, scope, images[s.id], tmp_path, DEVICE) for s in states]
    for s in finished:
        for e in s.elements:
            if e.asset_png:
                assert s.id in scope
                assert Image.open(tmp_path / e.asset_png).size == (int(e.rect_px.w), int(e.rect_px.h))
            assert not e.in_mock or s.in_mock_scope


def test_repeated_lists_mark_only_two_items_for_the_mock(app, tmp_path):
    _, states, images, _, _ = app
    (tmp_path / "assets").mkdir()
    for s in states:
        finished = stage.finish_elements(s, {s.id}, images[s.id], tmp_path, DEVICE)
        groups: dict = {}
        for e in finished.elements:
            if e.repeat_group:
                groups.setdefault(e.repeat_group, []).append(e)
        for members in groups.values():
            assert len(members) >= 3 and len({m.type for m in members}) == 1
            assert sum(m.in_mock for m in members) <= 2


def test_colors_pick_the_ink_off_the_background():
    import numpy as np
    pixels = np.full((20, 20, 3), 255, dtype=np.uint8)
    pixels[5:15, 5:15] = (200, 0, 0)
    fg, bg = stage.colors(pixels)
    assert (fg, bg) == ("#c80000", "#ffffff")
    assert stage.colors(np.zeros((0, 0, 3), dtype=np.uint8)) == (None, None)
