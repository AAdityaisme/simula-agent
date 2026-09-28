"""The labeled capture pairs from all three apps: every known-same pair matches, every other pair differs."""

import itertools
import json
import tomllib
from pathlib import Path

import pytest
from PIL import Image

from simula.contracts import Device
from simula.device import observe as ob
from simula.device.mcp import parse_elements, parse_foreground

TREES = Path(__file__).parent / "fixtures" / "trees"
PAIRS = tomllib.loads((TREES / "pairs.toml").read_text())
SAME = {frozenset(pair) for pair in PAIRS["same"]}


def fingerprint(capture: str) -> ob.Fingerprint:
    app, name = capture.split("/")
    elements = parse_elements(json.loads((TREES / app / f"{name}.elements.json").read_text()))
    package = parse_foreground(json.loads((TREES / app / f"{name}.foreground.json").read_text()))
    png = TREES / app / f"{name}.png"
    image = Image.open(png).convert("RGB") if png.exists() else Image.new("RGB", (1080, 2400))
    return ob.fingerprint(package, elements, image, Device())


FINGERPRINTS = {c: fingerprint(c) for c in PAIRS["captures"]}


@pytest.mark.parametrize("a,b", list(itertools.combinations(PAIRS["captures"], 2)), ids=lambda c: c.split("/")[1])
def test_pair(a, b):
    assert ob.same_state(FINGERPRINTS[a], FINGERPRINTS[b]) == (frozenset((a, b)) in SAME)


def test_every_app_has_a_same_pair():
    assert {next(iter(pair)).split("/")[0] for pair in SAME} == {"janitorai", "luzia", "aol"}


def test_fingerprint_string_is_stable():
    fp = FINGERPRINTS["luzia/luzia-home"]
    assert str(fp) == str(fingerprint("luzia/luzia-home")) and str(fp).startswith("co.thewordlab.luzia|")
