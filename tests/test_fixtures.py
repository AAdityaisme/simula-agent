"""The fixture set spans all three test apps, uses one tree format, and carries no account details."""

import hashlib
import re
import tomllib

import pytest
from PIL import Image

from tests.conftest import APPS, FIXTURES, tree

# The test account's handle, stored as a hash so the handle itself never lands in the repo.
HANDLE_SHA = "99b762c7fc355393"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(com|net|org|ai|io)\b")
TREES = sorted(p for p in (FIXTURES / "trees").rglob("*.elements.json"))


@pytest.mark.parametrize("app", APPS)
def test_every_app_has_trees_and_screens(app):
    assert list((FIXTURES / "trees" / app).glob("*.elements.json"))
    assert list((FIXTURES / "screens" / app).glob("*.png"))


@pytest.mark.parametrize("path", TREES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_trees_are_raw_mobile_mcp_replies(path):
    elements = tree(path.parent.name, path.name.removesuffix(".elements.json"))
    assert elements and all({"ref", "type", "coordinates"} <= set(e) for e in elements)
    assert Image.open(path.with_name(path.name.replace(".elements.json", ".png"))).size == (1080, 2400)


def test_no_account_details_in_any_text_fixture():
    for path in FIXTURES.rglob("*.json"):
        text = path.read_text()
        words = {w.lstrip("@") for w in re.findall(r"[@\w.+-]+", text)}
        assert HANDLE_SHA not in {hashlib.sha256(w.encode()).hexdigest()[:16] for w in words}, path
        assert not EMAIL.search(text), path


def test_fingerprint_pairs_point_at_real_captures():
    pairs = tomllib.loads((FIXTURES / "trees" / "pairs.toml").read_text())
    captures = set(pairs["captures"])
    assert {c.split("/")[0] for c in captures} == set(APPS)
    for name in captures:
        assert (FIXTURES / "trees" / f"{name}.elements.json").exists(), name
    assert all(set(pair) <= captures for pair in pairs["same"])
