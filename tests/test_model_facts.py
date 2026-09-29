"""Stage 2's code facts, checked against each golden model built from the same captures. App-neutral."""

import re

import numpy as np
import pytest
from PIL import Image

from simula.contracts import ActionLine, Device, Point, ProductModel, Rect, State, StateFile, VisionElement
from simula.stages import model as stage
from tests.conftest import APPS, FIXTURES
from tests.explore_fixture import add_core_loop, build

DEVICE = Device()
ELEMENT_LINE = re.compile(r"^s\d+\.e\d+ ")


def facts(explore):
    states, images, _ = stage.load_states(explore, DEVICE)
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
    states, _, _ = stage.load_states(explore, DEVICE)
    edges, notes = stage.load_edges(explore, states)
    loops = [e for e in edges if e.from_state == e.to_state]
    assert [(e.id, e.action, e.change_summary) for e in loops] == [("s01.swipe>s01", "swipe", "counter 3 → 2")]
    assert not any(e.to_state == "s99" for e in edges)
    assert notes == ["step 904: unknown state in s01>s99"]


def move(step, frm, to, outcome="ok", summary="", action="tap") -> ActionLine:
    return ActionLine(step=step, from_state=frm, to_state=to, action=action, mcp_ref=None, tap_px=None,
                      transition="push", change_summary=summary, outcome=outcome)


def test_only_a_move_its_two_captures_sit_right_around_is_diffed():
    lines = [move(1, "s01", None, "denied"), move(2, "s01", "s02"), move(3, "s02", "s03"),
             move(4, "s03", "s01"), move(5, "s01", "s04"),
             move(6, "s04", "s04", summary="3 left → 2 left"), move(7, "s04", "s05"),
             move(8, "s05", None, "denied"), move(9, "s05", "s06"),
             move(10, "s06", None, "timeout"), move(11, "s06", "s07"),
             move(12, "s07", "s07", action="swipe"), move(13, "s07", "s08")]
    assert stage.bracketing_moves(lines) == {2, 3, 9, 13}, \
        "a revisit, a change in place, or a timeout leaves the screen unlike its capture"
    assert stage.bracketing_moves([]) == set()


def text_state(sid, *texts) -> State:
    """A state whose elements are (text, x, y, w, h) TextViews."""
    elements = [stage.make_element(f"{sid}.e{n:02d}", Rect(x=x, y=y, w=w, h=h), "TextView", t, "", None,
                                   np.zeros((1, 1, 3), np.uint8), DEVICE)
                for n, (t, x, y, w, h) in enumerate(texts, start=1)]
    return State(id=sid, kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                 elements=elements, in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)


def test_a_value_that_changed_in_one_spot_is_what_changed():
    before = text_state("s01", ("7 chats", 279, 521, 115, 41), ("My Chats", 155, 174, 799, 71),
                        ("08:52", 900, 450, 90, 40), ("8", 41, 838, 21, 42), ("1", 41, 838, 21, 42),
                        ("9 coins", 40, 300, 100, 40), ("Only here 5", 40, 1500, 200, 40))
    after = text_state("s02", ("8 chats", 279, 521, 115, 41), ("Search", 155, 174, 799, 71),
                       ("09:37", 900, 450, 90, 40), ("8", 41, 838, 21, 42), ("0", 41, 838, 21, 42),
                       ("10 coins", 40, 300, 118, 40), ("Elsewhere 6", 40, 1600, 200, 40))
    assert stage.value_changes(before, after) == "7 chats → 8 chats; 9 coins → 10 coins", \
        "a new title, a clock, a spot holding two texts, and a text that moved are not changes"
    assert stage.value_changes(before, before) == ""


@pytest.mark.parametrize("name", APPS)
def test_an_edge_says_what_changed_only_when_its_captures_sit_right_around_it(name, tmp_path):
    explore = build(name, tmp_path / "explore")
    states, _, _ = stage.load_states(explore, DEVICE)
    a, b = text_state(states[0].id, ("7 chats", 279, 521, 115, 41)), text_state(states[1].id, ("8 chats", 279, 521, 115, 41))
    lines = [move(1, a.id, b.id), move(2, b.id, a.id), move(3, a.id, b.id, action="swipe"),
             move(4, b.id, a.id, summary="reply started 2 s", action="back")]
    (explore / "actions.jsonl").write_text("".join(line.model_dump_json() + "\n" for line in lines))
    edges, _ = stage.load_edges(explore, [a, b, *states[2:]])
    assert [(e.id, e.change_summary) for e in edges] == [
        (f"{a.id}.tap>{b.id}", "7 chats → 8 chats"), (f"{b.id}.tap>{a.id}", ""), (f"{a.id}.swipe>{b.id}", ""),
        (f"{b.id}.back>{a.id}", "reply started 2 s")]


@pytest.mark.parametrize("name", APPS)
def test_a_tap_binds_to_the_element_that_holds_it(name, tmp_path):
    explore = build(name, tmp_path / "explore")
    states, _, _ = stage.load_states(explore, DEVICE)
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
    states, _, model_labels = stage.load_states(explore, DEVICE)
    listed, vision = states[0].elements[:-1], states[0].elements[-1]
    assert (vision.source, vision.mcp_ref, vision.label, vision.rect_px) == ("vision", None, "Share button", box)
    assert vision.id == f"s01.e{len(listed) + 1:02d}" and all(e.source == "mcp" for e in listed)
    named = {i.mcp_ref: i.name for i in sf.icon_labels}
    assert named and all(e.label == named[e.mcp_ref] for e in listed if e.mcp_ref in named)
    on_s01 = {i for i in model_labels if i.startswith("s01.")}
    assert on_s01 == {vision.id} | {e.id for e in listed if e.mcp_ref in named}, "labels a model wrote are marked"


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


def test_flat_crops_and_wordless_corner_boxes_get_no_asset(tmp_path):
    """The edge-gesture areas of a real run: wordless boxes in the bottom corners, whose crops show whatever the
    screen has under them. Art and a full-width wordless banner keep their crops."""
    rng = np.random.default_rng(0)
    pixels = rng.integers(0, 256, (DEVICE.h_px, DEVICE.w_px, 3), dtype=np.uint8)
    pixels[800:900, 500:600] = (40, 40, 60)
    bottom = DEVICE.h_px - 195

    def element(n, kind, rect, label=""):
        return stage.make_element(f"s01.e{n:02d}", rect, kind, "", label, f"@e{n}", pixels, DEVICE)
    elements = [element(1, "ViewGroup", Rect(x=0, y=bottom, w=58, h=195)),
                element(2, "ViewGroup", Rect(x=DEVICE.w_px - 58, y=bottom, w=58, h=195)),
                element(3, "ViewGroup", Rect(x=500, y=800, w=100, h=100)),
                element(4, "ViewGroup", Rect(x=0, y=400, w=DEVICE.w_px, h=300)),
                element(5, "ImageView", Rect(x=0, y=bottom, w=300, h=195)),
                element(6, "ViewGroup", Rect(x=200, y=1200, w=300, h=300))]
    state = State(id="s01", kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                  elements=elements, in_mock_scope=False, content_rating="safe", dynamic_regions=[],
                  blocked_reason=None)
    (tmp_path / "assets").mkdir()
    done = stage.finish_elements(state, {"s01"}, set(), Image.fromarray(pixels), tmp_path, DEVICE)
    assert [(e.id, bool(e.asset_png), e.in_mock) for e in done.elements] == [
        ("s01.e01", False, False), ("s01.e02", False, False), ("s01.e03", False, False),
        ("s01.e04", True, True), ("s01.e05", True, True), ("s01.e06", True, True)]
    assert sorted(p.name for p in (tmp_path / "assets").iterdir()) == ["s01.e04.png", "s01.e05.png", "s01.e06.png"]


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


def test_measurements_parse_the_explorers_summary():
    assert stage.measurements("reply started 2.1 s, finished 9.4 s, 612 chars") == \
           [("reply started", 2.1, "s"), ("finished", 9.4, "s"), ("chars", 612.0, "chars")]
    assert stage.measurements("no reply within 45 s") == [("no reply within", 45.0, "s")]
    assert stage.measurements("reply arrived") == []


@pytest.mark.parametrize("shape", ["chat", "feed"])
@pytest.mark.parametrize("name", APPS)
def test_core_loop_passes_become_experience_facts(name, shape, tmp_path):
    explore = build(name, tmp_path / "explore")
    lines = add_core_loop(explore, passes=5, shape=shape)
    assert len(lines) == 5 * (3 if shape == "chat" else 2)
    states, _, _ = stage.load_states(explore, DEVICE)
    edges, _ = stage.load_edges(explore, states)
    measured, outcome = stage.loop_facts(explore, states, edges)
    steps = f"explore steps {lines[0].step}-{lines[-1].step}"
    verb = "reply" if shape == "chat" else "load"
    assert (measured.kind, outcome.kind) == ("experience", "experience")
    assert measured.evidence_ids == outcome.evidence_ids and set(measured.evidence_ids) <= {e.id for e in edges}
    assert measured.verbatim == (f"Core action over 5 passes ({steps}): "
                                 f"{verb} started median 2.7 s (min 1.9, max 3.5, n=5); "
                                 "finished median 10.5 s (min 7.5, max 13.5, n=5); "
                                 "chars median 600 (min 400, max 800, n=5)")
    assert outcome.verbatim == f"After 5 passes of the core action nothing limited it: no limit, paywall, or ad appeared ({steps})"


@pytest.mark.parametrize("name", APPS)
def test_a_loop_that_hit_a_limit_says_on_which_pass(name, tmp_path):
    explore = build(name, tmp_path / "explore")
    add_core_loop(explore, stop_at=2)
    states, _, _ = stage.load_states(explore, DEVICE)
    edges, _ = stage.load_edges(explore, states)
    outcome = stage.loop_facts(explore, states, edges)[-1]
    assert outcome.verbatim.startswith("limit banner appeared on pass 2 of the core action")
    assert set(outcome.evidence_ids) <= {e.id for e in edges}


def test_no_core_loop_means_no_experience_facts(tmp_path):
    explore = build(APPS[0], tmp_path / "explore")
    states, _, _ = stage.load_states(explore, DEVICE)
    edges, _ = stage.load_edges(explore, states)
    assert stage.loop_facts(explore, states, edges) == []


def loop_line(step: int, n: int, summary: str, **extra) -> ActionLine:
    return ActionLine(**{"step": step, "from_state": "s01", "to_state": "s01", "action": "type", "mcp_ref": None,
                         "tap_px": None, "transition": "unknown", "change_summary": summary, "outcome": "ok",
                         "loop_pass": n, **extra})


def with_loop(tmp_path, *lines: ActionLine):
    explore = build(APPS[0], tmp_path / "explore")
    with open(explore / "actions.jsonl", "a") as f:
        f.writelines(line.model_dump_json() + "\n" for line in lines)
    states, _, _ = stage.load_states(explore, DEVICE)
    edges, _ = stage.load_edges(explore, states)
    return stage.loop_facts(explore, states, edges)


def test_a_pass_is_counted_once_and_measured_only_from_its_timing_line(tmp_path):
    lines = [loop_line(900 + 3 * n, n, "counter 3 → 2, 5 credits") for n in (1, 2)]
    lines += [loop_line(901 + 3 * n, n, "typed 40 chars") for n in (1, 2)]
    lines += [loop_line(902 + 3 * n, n, f"reply started {n} s, {100 * n} chars") for n in (1, 2)]
    measured, outcome = with_loop(tmp_path, *sorted(lines, key=lambda a: a.step))
    assert "over 2 passes" in measured.verbatim and outcome.verbatim.startswith("After 2 passes")
    assert "reply started median 1.5 s (min 1, max 2, n=2); chars median 150 (min 100, max 200, n=2)" in measured.verbatim
    assert "credits" not in measured.verbatim and "typed" not in measured.verbatim


def test_measurements_in_different_units_are_never_mixed(tmp_path):
    measured, _ = with_loop(tmp_path, loop_line(900, 1, "reply 2 s"), loop_line(901, 2, "reply 4 s"),
                            loop_line(902, 3, "reply 300 chars, reply started 900 ms"))
    assert "reply median 3 s (min 2, max 4, n=2); reply median 300 chars (min 300, max 300, n=1)" in measured.verbatim
    assert "ms" not in measured.verbatim.split("): ", 1)[1], "only s and chars are core-loop units"


def test_a_stop_on_a_denied_pass_is_kept(tmp_path):
    measured, outcome = with_loop(tmp_path, loop_line(900, 1, "reply started 2 s"), loop_line(901, 2, "reply started 3 s"),
                                  loop_line(902, 3, "", to_state=None, outcome="denied", loop_stop="paywall"))
    assert measured.verbatim.startswith("Core action over 2 passes (explore steps 900-902)")
    assert outcome.verbatim == "paywall appeared on pass 3 of the core action (explore steps 900-902)"


def test_a_stop_is_kept_even_when_no_pass_ran(tmp_path):
    (outcome,) = with_loop(tmp_path, loop_line(900, 1, "", to_state=None, outcome="denied", loop_stop="paywall"))
    assert outcome.verbatim.startswith("paywall appeared on pass 1") and outcome.evidence_ids == ["s01"]
