import json
from pathlib import Path

import pytest

from simula import runfolder

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
APPS = ("janitorai", "luzia", "aol")
PREFIX = "Found these elements on screen: "


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(runfolder, "RUNS", tmp_path / "runs")
    return tmp_path / "runs"


def tree(app: str, name: str) -> list[dict]:
    reply = json.loads((FIXTURES / "trees" / app / f"{name}.elements.json").read_text())
    return json.loads(reply["content"][0]["text"][len(PREFIX):])


@pytest.fixture(autouse=True)
def no_font_fetch(request, monkeypatch, tmp_path):
    """Offline tests never fetch web fonts, and their font records stay out of the repo's cache: a test that needs
    fonts passes its own fetcher."""
    from simula.stages import mock
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setattr(mock, "FONT_RECORDS", tmp_path / "font-records")

    def refuse(url):
        raise RuntimeError(f"offline tests never fetch {url}")
    monkeypatch.setattr(mock, "fetch", refuse)
