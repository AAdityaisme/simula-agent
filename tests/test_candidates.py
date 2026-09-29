"""Candidates on each app's fixtures: one per bottom tab on the home screen, nested text merged, nothing from
the system bars, dialogs and overlays found, vision points inside a box dropped."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from simula.contracts import Device
from simula.device import observe as ob
from simula.device.mcp import parse_elements

TREES = Path(__file__).parent / "fixtures" / "trees"
DEVICE = Device()
HOMES = {("janitorai", "janitorai-a"): 5, ("luzia", "luzia-home"): 3, ("aol", "aol-home"): 2}


def cands(app: str, name: str) -> list[ob.Candidate]:
    return ob.controls(parse_elements(json.loads((TREES / app / f"{name}.elements.json").read_text())), DEVICE)


@pytest.mark.parametrize("app,name", HOMES)
def test_one_candidate_per_bottom_tab(app, name):
    tabs = ob.tab_bar(cands(app, name), DEVICE)
    assert len(tabs) == HOMES[(app, name)]
    assert len({t.key for t in tabs}) == len(tabs)
    assert all(t.rect.y + t.rect.h <= DEVICE.content_bottom_px for t in tabs)


@pytest.mark.parametrize("app,name", HOMES)
def test_no_candidate_from_the_system_bars(app, name):
    assert all(DEVICE.content_top_px <= c.rect.y < DEVICE.content_bottom_px for c in cands(app, name))


def test_text_inside_a_control_is_merged_into_it():
    labels = [c.tree_label for c in cands("luzia", "luzia-home")]
    assert labels.count("Chat") == 1 and labels.count("Teacher") == 1
    tab = next(c for c in cands("luzia", "luzia-home") if c.tree_label == "Chat")
    assert tab.ident == "bottomBarHomeChats"


def test_a_nested_control_label_does_not_rename_its_parent():
    send = next(c for c in cands("luzia", "luzia-chat-thread") if c.ident == "sendButton")
    assert send.label == "sendButton" and not ob.denied(send, core=True)


def test_a_small_box_that_content_scrolls_under_is_still_a_tab():
    assert len(ob.tab_bar(cands("janitorai", "janitorai-limited"), DEVICE)) == 5


@pytest.mark.parametrize("app,name", [("janitorai", "j01_launch"), ("luzia", "luzia-pet-intro")])
def test_dialogs_are_found(app, name):
    box = ob.dialog_box(cands(app, name), DEVICE)
    assert box and ob.box_kind(box, DEVICE) == "modal"
    assert ob.dismiss_control([c for c in cands(app, name) if ob.inside(c.rect, box)])


@pytest.mark.parametrize("app,name", [*HOMES, ("janitorai", "j10_drawer_settled"), ("luzia", "luzia-paywall")])
def test_full_screens_are_not_dialogs(app, name):
    assert ob.dialog_box(cands(app, name), DEVICE) is None


def test_an_overlay_keeps_the_controls_under_it_and_a_new_feed_does_not():
    home = cands("luzia", "luzia-home")
    sheet = home + [ob.Candidate(label=f"option {n}", kind="View", rect=ob.Rect(x=0, y=1500 + 150 * n, w=1080, h=140),
                                 ref=f"@x{n}", tree_label=f"option {n}") for n in range(4)]
    assert ob.overlay_box(home, sheet, DEVICE)
    before, after = cands("janitorai", "janitorai-a"), cands("janitorai", "janitorai-limited")
    tabs = frozenset(t.key for t in ob.tab_bar(before, DEVICE))
    assert ob.overlay_box(before, after, DEVICE, tabs) is None


def test_a_tall_sheet_over_a_scrim_is_an_overlay_and_a_refreshed_feed_is_not():
    fix = Path(__file__).parent / "fixtures" / "invariants"
    parent, sheet = (ob.controls(parse_elements(json.loads((fix / f"{n}.elements.json").read_text())), DEVICE)
                     for n in ("sheet-parent", "sheet-over"))
    then, now = (Image.open(fix / f"{n}-top.png") for n in ("sheet-parent", "sheet-over"))
    assert ob.overlay_box(parent, sheet, DEVICE) is None
    box = ob.overlay_box(parent, sheet, DEVICE, dimmed=lambda b: ob.scrim(then, now, b, DEVICE))
    assert box and ob.area(box) >= ob.OVERLAY_SHARE * ob.content_area(DEVICE) and ob.box_kind(box, DEVICE) == "sheet"
    home, again = cands("aol", "aol-home-repeat"), cands("aol", "aol-a")
    shots = [Image.open(TREES / "aol" / f"{n}.png") for n in ("aol-home-repeat", "aol-a")]
    assert ob.overlay_box(home, again, DEVICE, dimmed=lambda b: ob.scrim(*shots, b, DEVICE)) is None


@pytest.mark.parametrize("dim,dark", [(0.32, True), (0.6, True), (0.8, True), (0.0, False), (0.05, False)])
def test_a_scrim_is_any_standard_dimming_from_material_3_up(dim, dark):
    then = Image.new("RGB", (1080, 2400), (250, 250, 250))
    now = Image.fromarray((np.asarray(then, dtype=float) * (1 - dim)).astype(np.uint8))
    box = ob.Rect(x=0, y=DEVICE.content_top_px + 150, w=1080, h=DEVICE.content_bottom_px - DEVICE.content_top_px - 150)
    assert ob.area(box) >= ob.OVERLAY_SHARE * ob.content_area(DEVICE)
    assert ob.scrim(then, now, box, DEVICE) is dark


def test_a_control_is_refound_by_its_words_after_the_bar_recenters():
    home = cands("aol", "aol-home")
    sports = next(c for c in home if c.tree_label == "Sports")
    moved = [c if c is not sports else ob.Candidate(**{**c.__dict__, "rect": ob.Rect(x=300, y=c.rect.y, w=c.rect.w,
                                                                                        h=c.rect.h)}) for c in home]
    assert ob.find(moved, sports).rect.x == 300


def box(ref, kind, x, y, w, h, text=""):
    return {"ref": ref, "type": f"android.view.{kind}", "text": text,
            "coordinates": {"x": x, "y": y, "width": w, "height": h}}


def test_content_scrolled_under_a_tab_does_not_label_it():
    feed_tag = box("@e1", "TextView", 290, 2200, 110, 40, text="#proud")
    tabs = [box(f"@t{n}", "ViewGroup", 72 + 192 * n, 2170, 167, 167) for n in range(5)]
    cands = ob.controls([feed_tag, *tabs], DEVICE)
    assert [c.tree_label for c in ob.tab_bar(cands, DEVICE)] == [""] * 5
    assert all(c.tree_label != "#proud" for c in cands)


def test_own_text_after_a_control_still_labels_it():
    tab, label = box("@t1", "ViewGroup", 72, 2170, 167, 167), box("@e2", "TextView", 90, 2280, 120, 40, text="Home")
    assert [c.tree_label for c in ob.controls([tab, label], DEVICE)] == ["Home"]


def test_short_buttons_are_the_apps_own_words_not_titles_counters_or_cards():
    create = [c.label for c in ob.short_buttons(cands("luzia", "luzia-tab-create"), DEVICE)]
    home = cands("janitorai", "j04_tab1")
    tabs = {t.key for t in ob.tab_bar(home, DEVICE)}
    feed = [c.label for c in ob.short_buttons(home, DEVICE, tabs, title="Build, Share, Explore")]
    assert "Create app" in create
    assert "Trending" in feed and "Build, Share, Explore" not in feed and not any(ch.isdigit() for ch in "".join(feed))
    assert not {c.label for c in ob.feed_items(home, DEVICE, tabs)} & set(feed)


def test_a_scroll_moves_the_elements_and_a_redrawn_ad_does_not():
    def elements(name):
        return parse_elements(json.loads((TREES / "aol" / f"{name}.elements.json").read_text()))
    assert ob.shifted(elements("aol-home"), elements("aol-home-scrolled"), DEVICE)
    assert not ob.shifted(elements("aol-a"), elements("aol-b"), DEVICE)  # its tree changed 105 -> 96 nodes in place
