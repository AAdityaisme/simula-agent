"""Stage 2's code facts, checked against each golden model built from the same captures. App-neutral."""

import re

import numpy as np
import pytest
from PIL import Image

from simula.contracts import ActionLine, Device, Point, ProductModel, Rect, StateFile, VisionElement
from simula.stages import model as stage
from tests.conftest import APPS, FIXTURES
from tests.explore_fixture import build

DEVICE = Device()
ELEMENT_LINE = re.compile(r"^s\d+\.e\d+ ")


def facts(explore):
    states, images = stage.load_states(explore, DEVICE)
    edges, notes = stage.load_edges(explore, states)
    tapped = {e.element_id for e in edges if e.element_id}
    return [stage.group_repeats(s, tapped) for s in states], images, edges, notes


@pytest.fixture(params=APPS)
def app(request, tmp_path):
    explore = build(request.param, tmp_path / "explore")
    golden = ProductModel.model_validate_json((FIXTURES / "golden" / request.param / "product_model.json").read_text())
    return (golden, *facts(explore))


def append_lines(explore, *lines: dict) -> None:
    with open(explore / "actions.jsonl", "a") as f:
        for n, line in enumerate(lines, start=900):
            f.write(ActionLine(**{"step": n, "mcp_ref": None, "tap_px": None, "transition": "push",
                                  "change_summary": "", "outcome": "ok", **line}).model_dump_json() + "\n")


def test_states_carry_explore_kind_parent_regions_and_blocked_reason(app):
    golden, states, _, _, _ = app
    assert [(s.id, s.kind, s.parent_id, s.dynamic_regions, s.blocked_reason) for s in states] == \
           [(s.id, s.kind, s.parent_id, s.dynamic_regions, s.blocked_reason) for s in golden.states]


def test_element_facts_match_the_golden(app):
    golden, states, _, _, _ = app
    ours = {e.id: e for s in states for e in s.elements}
    theirs = {e.id: e for s in golden.states for e in s.elements}
    assert ours.keys() == theirs.keys()
    for eid, e in ours.items():
        g = theirs[eid]
        assert (e.mcp_ref, e.type, e.text, e.label, e.rect_px, e.rect_dp, e.fg_hex, e.bg_hex, e.font_px) == \
               (g.mcp_ref, g.type, g.text, g.label, g.rect_px, g.rect_dp, g.fg_hex, g.bg_hex, g.font_px), eid


def test_no_element_sits_off_screen(app):
    _, states, _, _, _ = app
    for e in (e for s in states for e in s.elements):
        assert 0 <= e.rect_px.x < DEVICE.w_px, e.id
        assert DEVICE.content_top_px <= e.rect_px.y < DEVICE.content_bottom_px, e.id


def test_rect_dp_follows_the_formula(app):
    _, states, _, _, _ = app
    for e in (e for s in states for e in s.elements):
        assert e.rect_dp.x == pytest.approx(e.rect_px.x / DEVICE.scale, abs=0.01)
        assert e.rect_dp.y == pytest.approx((e.rect_px.y - DEVICE.content_top_px) / DEVICE.scale, abs=0.01)


def test_edges_come_from_explore_with_their_transitions(app):
    golden, _, _, edges, notes = app
    assert notes == []
    assert [(e.id, e.from_state, e.to_state, e.element_id, e.transition) for e in edges] == \
           [(e.id, e.from_state, e.to_state, e.element_id, e.transition) for e in golden.edges]


@pytest.mark.parametrize("name", APPS)
def test_only_moves_that_reach_a_state_or_change_something_are_edges(name, tmp_path):
    explore = build(name, tmp_path / "explore")
    append_lines(explore,
                 {"from_state": "s01", "to_state": "s01", "action": "swipe"},
                 {"from_state": "s01", "to_state": "s01", "action": "swipe", "change_summary": "counter 3 → 2"},
                 {"from_state": "s01", "to_state": None, "action": "tap", "outcome": "denied"},
                 {"from_state": "s01", "to_state": "s01", "action": "relaunch", "change_summary": "relaunched"},
                 {"from_state": "s01", "to_state": "s99", "action": "back"})
    states, _ = stage.load_states(explore, DEVICE)
    edges, notes = stage.load_edges(explore, states)
    loops = [e for e in edges if e.from_state == e.to_state]
    assert [(e.id, e.action, e.change_summary) for e in loops] == [("s01.swipe>s01", "swipe", "counter 3 → 2")]
    assert not any(e.to_state == "s99" for e in edges)
    assert notes == ["step 904: unknown state in s01>s99"]


@pytest.mark.parametrize("name", APPS)
def test_a_tap_binds_to_the_element_that_holds_it(name, tmp_path):
    explore = build(name, tmp_path / "explore")
    states, _ = stage.load_states(explore, DEVICE)
    root = next(s for s in states if s.elements)
    target, decoy = root.elements[-1], next(e for e in root.elements if e.rect_px.y + e.rect_px.h < root.elements[-1].rect_px.y)
    tap = Point(x=int(target.rect_px.x + target.rect_px.w / 2), y=int(target.rect_px.y + target.rect_px.h / 2))
    to = states[-1].id
    append_lines(explore,
                 {"from_state": root.id, "to_state": to, "action": "tap", "mcp_ref": decoy.mcp_ref, "tap_px": tap.model_dump()},
                 {"from_state": root.id, "to_state": to, "action": "tap", "mcp_ref": decoy.mcp_ref,
                  "tap_px": {"x": 5, "y": 5}})
    edges, notes = stage.load_edges(explore, states)
    holding = [e for e in root.elements if stage.contains(e.rect_px, tap)]
    smallest = min(holding, key=lambda e: e.rect_px.w * e.rect_px.h)
    assert f"{smallest.id}>{to}" in {e.id for e in edges}
    assert f"{root.id}.tap>{to}" in {e.id for e in edges}
    assert notes[-2:] == [f"step 900: {decoy.mcp_ref} does not hold the tap at {tap.x},{tap.y}; bound to {smallest.id}",
                          f"step 901: {decoy.mcp_ref} does not hold the tap at 5,5; bound to no element"]


def test_icon_names_and_vision_elements_become_elements(tmp_path):
    explore = build(APPS[0], tmp_path / "explore")
    path = explore / "states" / "s01.json"
    sf = StateFile.model_validate_json(path.read_text())
    box = Rect(x=900, y=300, w=126, h=126)
    sf.vision_elements.append(VisionElement(name="Share button", rect_px=box))
    path.write_text(sf.model_dump_json())
    states, _ = stage.load_states(explore, DEVICE)
    listed, vision = states[0].elements[:-1], states[0].elements[-1]
    assert (vision.source, vision.mcp_ref, vision.label, vision.rect_px) == ("vision", None, "Share button", box)
    assert vision.id == f"s01.e{len(listed) + 1:02d}" and all(e.source == "mcp" for e in listed)
    named = {i.mcp_ref: i.name for i in sf.icon_labels}
    assert named and all(e.label == named[e.mcp_ref] for e in listed if e.mcp_ref in named)


def test_a_missing_tree_file_fails_loudly(tmp_path):
    explore = build(APPS[0], tmp_path / "explore")
    (explore / "states" / "s01.elements.json").unlink()
    with pytest.raises(FileNotFoundError):
        stage.load_states(explore, DEVICE)


def test_canonical_png_is_the_content_crop(app):
    _, states, images, _, _ = app
    for s in states:
        assert stage.content_png(images[s.id], DEVICE).size == \
               (DEVICE.w_px, DEVICE.content_bottom_px - DEVICE.content_top_px)


def test_assets_are_cropped_to_their_rect_for_in_scope_states_only(app, tmp_path):
    _, states, images, edges, _ = app
    (tmp_path / "assets").mkdir()
    tapped = {e.element_id for e in edges}
    scope = {states[0].id}
    for s in (stage.finish_elements(s, scope, tapped, images[s.id], tmp_path, DEVICE) for s in states):
        for e in s.elements:
            if e.asset_png:
                assert s.id in scope
                assert Image.open(tmp_path / e.asset_png).size == (int(e.rect_px.w), int(e.rect_px.h))
            assert not e.in_mock or s.in_mock_scope


def test_a_box_crop_never_holds_text(app):
    _, states, _, _, _ = app
    for s in states:
        words = [e for e in s.elements if e.text or e.label]
        for e in s.elements:
            if stage.is_image_like(e, s.elements, DEVICE) and e.type != "ImageView":
                assert not any(w is not e and stage.inside(w.rect_px, e.rect_px) for w in words), e.id


def test_an_image_with_a_text_overlay_is_still_art():
    def element(eid, kind, text, rect):
        return stage.make_element(eid, rect, kind, text, "", "@" + eid, np.zeros((2400, 1080, 3), np.uint8), DEVICE)
    image = element("s01.e01", "ImageView", "", Rect(x=0, y=300, w=500, h=500))
    box = element("s01.e02", "ViewGroup", "", Rect(x=500, y=300, w=500, h=500))
    overlays = [element("s01.e03", "TextView", "Title", Rect(x=20, y=700, w=300, h=60)),
                element("s01.e04", "TextView", "Title", Rect(x=520, y=700, w=300, h=60))]
    siblings = [image, box, *overlays]
    assert stage.is_image_like(image, siblings, DEVICE) and not stage.is_image_like(box, siblings, DEVICE)


def test_every_tapped_element_is_drawn_and_no_tapped_element_is_a_list_item(app, tmp_path):
    _, states, images, edges, _ = app
    (tmp_path / "assets").mkdir()
    tapped = {e.element_id for e in edges if e.element_id}
    for s in states:
        finished = stage.finish_elements(s, {s.id}, tapped, images[s.id], tmp_path, DEVICE)
        groups: dict = {}
        for e in finished.elements:
            if e.id in tapped:
                assert e.in_mock and e.repeat_group is None, e.id
            if e.repeat_group:
                groups.setdefault(e.repeat_group, []).append(e)
            if e.text or e.label or e.asset_png:
                assert e.in_mock, e.id
        for members in groups.values():
            assert len(members) >= 3 and len({m.type for m in members}) == 1


def test_colors_pick_the_ink_off_the_background():
    pixels = np.full((20, 20, 3), 255, dtype=np.uint8)
    pixels[5:15, 5:15] = (200, 0, 0)
    assert stage.colors(pixels) == ("#c80000", "#ffffff")
    assert stage.colors(np.zeros((0, 0, 3), dtype=np.uint8)) == (None, None)


def test_describe_lists_drawable_elements_and_folds_long_lists(app):
    _, states, _, edges, _ = app
    text = stage.describe(states, edges, {"package": "x"}, DEVICE, name_limit=10_000)
    listed = {line.split()[0] for line in text.splitlines() if ELEMENT_LINE.match(line)}
    tapped = {e.element_id for e in edges}
    for s in states:
        for e in s.elements:
            if e.id in listed:
                assert e.text or e.label or e.id in tapped or stage.is_image_like(e, s.elements, DEVICE), e.id
        counts: dict = {}
        for e in s.elements:
            if e.repeat_group and e.id in listed:
                counts[e.repeat_group] = counts.get(e.repeat_group, 0) + 1
        assert all(n <= 2 for n in counts.values())
    assert tapped - {None} <= listed


def test_describe_stops_naming_past_the_budget_except_tapped_controls(app):
    _, states, _, edges, _ = app
    text = stage.describe(states, edges, {"package": "x"}, DEVICE, name_limit=5)
    listed = {line.split()[0] for line in text.splitlines() if ELEMENT_LINE.match(line)}
    tapped = {e.element_id for e in edges if e.element_id}
    assert tapped <= listed and len(listed - tapped) <= 5
    if sum(len(s.elements) for s in states) > 5:
        assert "not listed" in text
    assert "RECORDED EDGES" in text
