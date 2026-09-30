import contextlib
import json
import os
from pathlib import Path

import pytest

from simula import runfolder
from simula.stages import explore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
APPS = ("janitorai", "luzia", "aol")
PREFIX = "Found these elements on screen: "


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """CI splits the suite over PYTEST_SHARDS machines: shard PYTEST_SHARD keeps every test whose index in the
    collection is PYTEST_SHARD modulo PYTEST_SHARDS, so slow neighbours land on different machines. It runs last, after
    -m 'not live' has dropped its tests, and does nothing when the variables are unset."""
    shard, shards = os.environ.get("PYTEST_SHARD"), os.environ.get("PYTEST_SHARDS")
    if shard is None or shards is None:
        return
    mine = [i % int(shards) == int(shard) for i in range(len(items))]
    config.hook.pytest_deselected(items=[item for item, keep in zip(items, mine) if not keep])
    items[:] = [item for item, keep in zip(items, mine) if keep]


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
