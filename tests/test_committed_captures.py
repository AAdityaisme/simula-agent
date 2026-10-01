"""Today's explorer rules over every committed run's own captures, with no device and no model: each state's kind,
box and own controls, and the launch screen's tab bar, compared with tests/fixtures/committed_captures.json. An
explorer change that moves any of them fails here until the expected file is rewritten, so its effect on real apps
shows in a reviewed diff. Rewrite it with: uv run python -m tests.test_committed_captures"""

import json
import subprocess
from pathlib import Path

from PIL import Image

from simula.contracts import ActionLine, ExploreFile, StateFile
from simula.device import observe as ob

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = Path(__file__).parent / "fixtures" / "committed_captures.json"


def place(r: ob.Rect) -> str:
    return f"{int(r.x)},{int(r.y)} {int(r.w)}x{int(r.h)}"


def control(c: ob.Candidate) -> str:
    return f"{c.label[:40]} | {c.kind} | {place(c.rect)}"


def explore_rules(explore: Path) -> dict:
    """What record() and the first launch make of each committed capture. A state's parent is the screen its first
    arrival came from, as the run's action log names it."""
    run = ExploreFile.model_validate_json((explore / "explore.json").read_text())
    device, tabs, out, root = run.device, [], {}, None
    arrivals, log = {}, explore / "actions.jsonl"  # a run blocked at launch has no log
    for raw in log.read_text().splitlines() if log.exists() else []:
        line = ActionLine.model_validate_json(raw)
        if line.outcome == "ok" and line.to_state and line.to_state != line.from_state:
            arrivals.setdefault(line.to_state, line.from_state)
    seen: dict[str, tuple[list[dict], list[ob.Candidate], Path]] = {}
    files = [p for p in (explore / "states").glob("s*.json") if p.stem[1:].isdigit()]
    for path in sorted(files, key=lambda p: int(p.stem[1:])):
        state = StateFile.model_validate_json(path.read_text())
        reply = json.loads((explore / state.elements_reply).read_text())
        elements = json.loads(reply["content"][0]["text"].removeprefix(ob.ELEMENTS_PREFIX))
        cands, png = ob.controls(elements, device), explore / state.screenshot
        seen[state.state_id] = elements, cands, png
        before = seen.get(arrivals.get(state.state_id, ""))
        with Image.open(png) as image:
            sideways = image.width > image.height
        box = None
        if state.foreground_package != run.app_package:
            kind = "external"
        elif sideways:
            kind = "rotated"
        else:
            box = ob.dialog_box(cands, device) or (ob.overlay_box(
                before[1], cands, device, frozenset(t.key for t in tabs),
                lambda b: ob.scrim(Image.open(before[2]), Image.open(png), b, device)) if before else None)
            kind = ob.box_kind(box, device) if box else "screen"
        own = ob.own_controls(cands, box, before[1] if before else [])
        if kind == "screen" and root is None:
            root, tabs = state.state_id, [t for t in ob.tab_bar(own, device) if not ob.denied(t)]
        out[state.state_id] = {"kind": kind, "box": place(box) if box else None, "controls": [control(c) for c in own]}
    return {"root": root, "tabs": [control(t) for t in tabs], "states": out}


def committed() -> dict:
    """Tracked runs only: a measurement run left in a checkout is untracked."""
    files = subprocess.run(["git", "ls-files", "runs/*/*/explore/explore.json"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    return {"/".join(p.parts[1:3]): explore_rules(ROOT / p.parent) for p in sorted(map(Path, files))}


def test_every_committed_capture_reads_as_the_expected_file_says():
    now, expected = committed(), json.loads(EXPECTED.read_text())
    assert now.keys() == expected.keys()
    for run in now:
        assert (now[run]["root"], now[run]["tabs"]) == (expected[run]["root"], expected[run]["tabs"]), run
        for sid, state in now[run]["states"].items():
            assert state == expected[run]["states"][sid], f"{run} {sid}"


if __name__ == "__main__":
    EXPECTED.write_text(json.dumps(committed(), indent=1, ensure_ascii=False) + "\n")
