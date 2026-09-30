import contextlib
import json
from pathlib import Path

import pytest

from simula import runfolder
from simula.stages import explore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
APPS = ("janitorai", "luzia", "aol")
PREFIX = "Found these elements on screen: "


@pytest.fixture(autouse=True)
def no_device(request, monkeypatch):
    """Offline tests never reach the emulator: a chained run stops where explore would start mobile-mcp."""
    if request.node.get_closest_marker("live"):
        return

    monkeypatch.setenv("SIMULA_REDACT", "offline-test-handle")

    def refuse(*args, **kwargs):
        raise NotImplementedError("PR 1: offline tests never start mobile-mcp")
    monkeypatch.setattr(explore, "Server", refuse)
    monkeypatch.setattr(explore, "emulator_lock", lambda *args, **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(explore, "resolve_serial", lambda flag: flag or "offline-test")


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
    monkeypatch.setattr(llm, "answered_from_cache", lambda **kwargs: None)
