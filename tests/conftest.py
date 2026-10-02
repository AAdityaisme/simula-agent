import contextlib
import json
import os
from pathlib import Path

import pytest

from simula import qa_live, runfolder, runlog
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


@pytest.fixture(scope="session")
def quiet_osascript(tmp_path_factory) -> Path:
    """A directory holding an osascript that does nothing, for PATH: a run a test starts as its own process (the
    graders' replay of the pinned code) reaches macOS banners only through osascript."""
    folder = tmp_path_factory.mktemp("bin")
    (folder / "osascript").write_text("#!/bin/sh\nexit 0\n")
    (folder / "osascript").chmod(0o755)
    return folder


@pytest.fixture(autouse=True)
def no_device(request, monkeypatch, quiet_osascript):
    """Offline tests never reach the emulator: a chained run stops where explore would start mobile-mcp, the live
    walk where it would, and adb answers only dumpsys, as an emulator does with its soft keyboard up over the lower
    part of the screen. A test changes those answers through the dict this returns. Nor do they show a macOS banner
    when a run asks for a person, as CI's Linux runners never do; a test that checks the banner sets its own."""
    if request.node.get_closest_marker("live"):
        return None

    monkeypatch.setenv("SIMULA_REDACT", "offline-test-handle")
    monkeypatch.setattr(runlog, "notify", lambda title, message: False)
    monkeypatch.setenv("PATH", f"{quiet_osascript}{os.pathsep}{os.environ['PATH']}")

    def refuse(*args, **kwargs):
        raise NotImplementedError("PR 1: offline tests never start mobile-mcp")
    for module in (explore, qa_live):
        monkeypatch.setattr(module, "Server", refuse)
        monkeypatch.setattr(module, "emulator_lock", lambda *args, **kwargs: contextlib.nullcontext())
        monkeypatch.setattr(module, "resolve_serial", lambda flag: flag or "offline-test")
    dumpsys = {"input_method": "  mInputShown=true\n",
               "window": "  Window #3 Window{a1 u0 InputMethod}:\n    mFrame=[0,1500][1080,2400] last=[0,0][0,0]\n"}
    monkeypatch.setattr(explore, "adb_shell", lambda serial, args: dumpsys.get(args[1]) if args[0] == "dumpsys"
                        else None)
    return dumpsys


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
