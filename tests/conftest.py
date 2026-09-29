import json
import os
from pathlib import Path

import pytest

from simula import runfolder

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


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(runfolder, "RUNS", tmp_path / "runs")
    return tmp_path / "runs"


def tree(app: str, name: str) -> list[dict]:
    reply = json.loads((FIXTURES / "trees" / app / f"{name}.elements.json").read_text())
    return json.loads(reply["content"][0]["text"][len(PREFIX):])
