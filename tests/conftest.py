import contextlib
import json
import os
import statistics
from collections import Counter
from itertools import chain
from pathlib import Path

import pytest

from simula import qa_live, runfolder, runlog
from simula.stages import explore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
DURATIONS = ROOT / "tests" / "durations.json"
APPS = ("janitorai", "luzia", "aol")
PREFIX = "Found these elements on screen: "


def balance(tests: list[str], seconds: dict[str, float], shards: int) -> list[list[str]]:
    """Each shard's tests, longest first: every test in turn, longest first and then by node id, goes to the shard with
    the fewest seconds so far, a test seconds doesn't list counting as their mean. The order of tests doesn't change
    the result, so every machine computes the same split."""
    mean = statistics.fmean(seconds.values())
    load, split = [0.0] * shards, [[] for _ in range(shards)]
    for test in sorted(tests, key=lambda test: (-seconds.get(test, mean), test)):
        shard = min(range(shards), key=load.__getitem__)
        split[shard].append(test)
        load[shard] += seconds.get(test, mean)
    return split


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """CI splits the suite over PYTEST_SHARDS machines by the seconds each test took in tests/durations.json, and shard
    PYTEST_SHARD runs its tests longest first. It runs last, after -m 'not live' has dropped its tests, and splits
    nothing when the variables are unset. With PYTEST_IDS set it writes the node ids the run keeps there, one per line,
    which the CI gate checks the shards against."""
    shard, shards = os.environ.get("PYTEST_SHARD"), os.environ.get("PYTEST_SHARDS")
    if shard is not None and shards is not None:
        mine = balance([item.nodeid for item in items], json.loads(DURATIONS.read_text()), int(shards))[int(shard)]
        rank = {test: i for i, test in enumerate(mine)}
        config.hook.pytest_deselected(items=[item for item in items if item.nodeid not in rank])
        items[:] = sorted((item for item in items if item.nodeid in rank), key=lambda item: rank[item.nodeid])
    # Every xdist worker collects the same list, and the controller none, so one worker writes it.
    if (path := os.environ.get("PYTEST_IDS")) and os.environ.get("PYTEST_XDIST_WORKER", "gw0") == "gw0":
        Path(path).write_text("".join(f"{item.nodeid}\n" for item in items))


def pytest_terminal_summary(terminalreporter):
    """With PYTEST_DURATIONS set, writes there the seconds each test took, setup and teardown included, in
    tests/durations.json's format. xdist workers see only their own tests, so only the controller writes."""
    if not (path := os.environ.get("PYTEST_DURATIONS")) or "PYTEST_XDIST_WORKER" in os.environ:
        return
    seconds = Counter()
    for report in chain.from_iterable(terminalreporter.stats.values()):
        if isinstance(report, pytest.TestReport):
            seconds[report.nodeid] += report.duration
    Path(path).write_text(json.dumps({test: round(s, 3) for test, s in sorted(seconds.items())}, indent=2) + "\n")


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
