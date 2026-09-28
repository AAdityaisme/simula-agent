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
