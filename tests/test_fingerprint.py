"""The labeled capture pairs from all three apps: every known-same pair matches, every other pair differs."""

import itertools
import json
import tomllib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from simula.contracts import Device
from simula.device import observe as ob
from simula.device.mcp import parse_elements, parse_foreground

TREES = Path(__file__).parent / "fixtures" / "trees"
PAIRS = tomllib.loads((TREES / "pairs.toml").read_text())
SAME = {frozenset(pair) for pair in PAIRS["same"]}


def load(capture: str) -> tuple[str, list[dict], Image.Image]:
    app, name = capture.split("/")
    elements = parse_elements(json.loads((TREES / app / f"{name}.elements.json").read_text()))
    package = parse_foreground(json.loads((TREES / app / f"{name}.foreground.json").read_text()))
    png = TREES / app / f"{name}.png"
    image = Image.open(png).convert("RGB") if png.exists() else Image.new("RGB", (1080, 2400))
    return package, elements, image


def fingerprint(capture: str) -> ob.Fingerprint:
    return ob.fingerprint(*load(capture), Device())


FINGERPRINTS = {c: fingerprint(c) for c in PAIRS["captures"]}


@pytest.mark.parametrize("a,b", list(itertools.combinations(PAIRS["captures"], 2)), ids=lambda c: c.split("/")[1])
def test_pair(a, b):
    assert ob.same_state(FINGERPRINTS[a], FINGERPRINTS[b]) == (frozenset((a, b)) in SAME)


def test_every_app_has_a_same_pair():
    assert {next(iter(pair)).split("/")[0] for pair in SAME} == {"janitorai", "luzia", "aol"}


def test_a_feed_that_reloaded_other_items_is_the_same_screen_and_the_other_filter_is_not():
    home, relaunched, filtered = (FINGERPRINTS[f"janitorai/{name}"] for name in
                                  ("j04_tab1", "j11_home_relaunched", "janitorai-hidden"))
    assert home.skeleton != relaunched.skeleton and ob.hamming(home.content, relaunched.content) > ob.CONTENT_BITS
    assert home.band == relaunched.band == filtered.band and ob.hamming(home.top, filtered.top) <= ob.TOP_BITS
    assert ob.same_state(home, relaunched) and not ob.same_state(home, filtered)


def test_a_popup_over_the_feed_that_leaves_its_rows_listed_stays_another_state():
    package, elements, image = load("janitorai/j11_home_relaunched")
    popup = image.copy()
    ImageDraw.Draw(popup).rectangle((0, 1300, 1080, 2169), fill=(40, 40, 48))
    over = elements + [
        {"ref": "@p1", "type": "android.view.ViewGroup", "text": "Share this character",
         "coordinates": {"x": 0, "y": 1300, "width": 1080, "height": 110}},
        {"ref": "@p2", "type": "android.widget.Button", "text": "Copy link",
         "coordinates": {"x": 60, "y": 2000, "width": 960, "height": 120}}]
    feed, shown = ob.fingerprint(package, elements, image, Device()), ob.fingerprint(package, over, popup, Device())
    assert feed.band == shown.band and ob.hamming(feed.frame, shown.frame) <= ob.FRAME_BITS
    assert feed.rows <= shown.rows and not ob.same_state(feed, shown)


def test_a_screen_without_a_list_has_no_frame():
    fp = FINGERPRINTS["janitorai/j07_tab4"]
    assert fp.band is None and str(fp).endswith("|-")


def test_fingerprint_string_is_stable():
    fp = FINGERPRINTS["luzia/luzia-home"]
    assert str(fp) == str(fingerprint("luzia/luzia-home")) and str(fp).startswith("co.thewordlab.luzia|")
