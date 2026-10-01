"""The explorer's own code over every committed run's captures, on a fake phone that shows them, with no device and no
model: the first launch's root and tab choice, record()'s kind, box and own controls for each state, and every own
control the tap-time check refuses, compared with tests/fixtures/committed_captures.json. An explorer change that moves
any of them fails here until the expected file is rewritten, so its effect on real apps shows in a reviewed diff.
Rewrite it with: uv run python -m tests.test_committed_captures"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from simula.contracts import ActionLine, ExploreFile, StateFile
from simula.device import observe as ob
from simula.stages import explore as stage
from tests import fake_device
from tests.fake_device import FakePhone, Screen

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = Path(__file__).parent / "fixtures" / "committed_captures.json"


def place(r: ob.Rect) -> str:
    return f"{int(r.x)},{int(r.y)} {int(r.w)}x{int(r.h)}"


def control(c: ob.Candidate) -> str:
    return f"{c.label[:40]} | {c.kind} | {place(c.rect)}"


def explore_rules(explore: Path, monkeypatch, tmp: Path) -> dict:
    """What the first launch and record() make of each committed capture. A state's parent is the screen its first
    arrival came from, as the run's action log names it."""
    run = ExploreFile.model_validate_json((explore / "explore.json").read_text())
    arrivals, log = {}, explore / "actions.jsonl"  # a run blocked at launch has no log
    for raw in log.read_text().splitlines() if log.exists() else []:
        line = ActionLine.model_validate_json(raw)
        if line.outcome == "ok" and line.to_state and line.to_state != line.from_state:
            arrivals.setdefault(line.to_state, line.from_state)
    files = sorted((p for p in (explore / "states").glob("s*.json") if p.stem[1:].isdigit()), key=lambda p: int(p.stem[1:]))
    screens = {}
    for path in files:
        state = StateFile.model_validate_json(path.read_text())
        reply = json.loads((explore / state.elements_reply).read_text())
        elements = json.loads(reply["content"][0]["text"].removeprefix(ob.ELEMENTS_PREFIX))
        screens[state.state_id] = Screen(elements, Image.open(explore / state.screenshot).convert("RGB"),
                                         state.foreground_package)
    ex, phone = fake_device.explorer(tmp, monkeypatch, lambda clock: FakePhone(screens, next(iter(screens)), {}, clock),
                                     app={"name": explore.parent.parent.name, "package": run.app_package})
    ex.device = run.device
    monkeypatch.setattr(ex, "apply_filter", lambda first: None)  # its taps would lead nowhere on still captures
    monkeypatch.setattr(ex, "name_icons", lambda s: None)  # a model's names, not the explorer's rules
    try:
        ex.relaunch(first=True)
    except stage.Stop:
        pass
    root = ex.root.sid if ex.root and ex.root.kind == "screen" else None
    out = {}
    for sid in screens:
        ex.states, ex.by_id, ex.home, before = [], {}, None, None
        if sid in arrivals:
            phone.screen = arrivals[sid]
            before = ex.observe()
        phone.screen = sid
        obs = ex.observe()
        seen = ex.record(obs, None, None, before)
        refused = [f"{c.label[:40]} | {why}" for c in seen.cands if not ob.denied(c, upsell=seen.upsell)
                   for why in [ob.denied_at(c, obs.elements, ex.device, upsell=seen.upsell)] if why]
        out[sid] = {"kind": seen.kind, "box": place(seen.box) if seen.box else None,
                    "controls": [control(c) for c in seen.cands], "refused": refused}
    return {"root": root, "tabs": [control(t) for t in ex.tabs] if root else [], "states": out}


def committed(monkeypatch, tmp: Path) -> dict:
    """Tracked runs only: a measurement run left in a checkout is untracked."""
    files = subprocess.run(["git", "ls-files", "runs/*/*/explore/explore.json"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    return {"/".join(p.parts[1:3]): explore_rules(ROOT / p.parent, monkeypatch, tmp / p.parts[1])
            for p in sorted(map(Path, files))}


def test_every_committed_capture_reads_as_the_expected_file_says(tmp_path, monkeypatch):
    now, expected = committed(monkeypatch, tmp_path), json.loads(EXPECTED.read_text())
    assert now.keys() == expected.keys()
    for run in now:
        assert (now[run]["root"], now[run]["tabs"]) == (expected[run]["root"], expected[run]["tabs"]), run
        for sid, state in now[run]["states"].items():
            assert state == expected[run]["states"][sid], f"{run} {sid}"


if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory() as scratch:
        EXPECTED.write_text(json.dumps(committed(patch, Path(scratch)), indent=1, ensure_ascii=False) + "\n")
