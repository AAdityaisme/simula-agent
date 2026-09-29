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
def no_font_fetch(request, monkeypatch):
    """Offline tests never fetch web fonts: a test that needs fonts passes its own fetcher."""
    from simula.stages import mock
    if request.node.get_closest_marker("live"):
        return

    def refuse(url):
        raise RuntimeError(f"offline tests never fetch {url}")
    monkeypatch.setattr(mock, "fetch", refuse)


@pytest.fixture(autouse=True)
def no_machine_cache(monkeypatch):
    """Offline tests never read this machine's model cache (a real run fills it): a planner asking whether a call is
    cached hears no, unless the test sets up its own cache (with_cache_in)."""
    from simula import llm
    monkeypatch.setattr(llm, "answered_from_cache", lambda **kwargs: False)
