"""Builds an explore/ folder from one app's fixture captures, shaped like the explorer's output, so stage 2
can run before PR 1 exists. The states and taps come from that app's golden model; the pixels and trees
come from the raw captures. TEST DATA ONLY.

    uv run python tests/explore_fixture.py OUT_DIR      # writes OUT_DIR/<app>/ for every test app
"""

import json
import shutil
import sys
from pathlib import Path

from simula.config import app_config
from simula.contracts import Coverage, ExploreFile, ProductModel, StateFile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
APPS = ("janitorai", "luzia", "aol")


def capture_paths(app: str, fingerprint: str) -> tuple[Path, Path | None]:
    """A golden fingerprint is fixture:tree:<name> or fixture:screen:<name>."""
    _, source, name = fingerprint.split(":", 2)
    if source == "tree":
        folder = FIXTURES / "trees" / app
        return folder / f"{name}.png", folder / f"{name}.elements.json"
    return FIXTURES / "screens" / app / f"{name}.png", None


def build(app: str, out: Path) -> Path:
    golden = ProductModel.model_validate_json((FIXTURES / "golden" / app / "product_model.json").read_text())
    package = app_config(app)["package"]
    shutil.rmtree(out, ignore_errors=True)
    (out / "states").mkdir(parents=True)
    for state in golden.states:
        png, reply = capture_paths(app, state.fingerprint)
        shutil.copy(png, out / "states" / f"{state.id}.png")
        if reply:
            shutil.copy(reply, out / "states" / f"{state.id}.elements.json")
        state_file = StateFile(
            state_id=state.id, kind=state.kind, parent_id=state.parent_id, fingerprint=state.fingerprint,
            foreground_package=package, screenshot=f"states/{state.id}.png",
            elements_reply=f"states/{state.id}.elements.json" if reply else "", settled=True, settle_seconds=0.0,
            dynamic_regions=state.dynamic_regions, captured_at="fixture")
        (out / "states" / f"{state.id}.json").write_text(state_file.model_dump_json(indent=1))
    refs = {e.id: e.mcp_ref for s in golden.states for e in s.elements}
    with open(out / "actions.jsonl", "w") as f:
        for edge in golden.edges:
            f.write(json.dumps({"from_state": edge.from_state, "to_state": edge.to_state, "action": edge.action,
                                "mcp_ref": refs.get(edge.element_id), "transition": edge.transition,
                                "change_summary": edge.change_summary, "outcome": "ok"}) + "\n")
    explore = ExploreFile(
        app_package=package, app_version=golden.app_version, budget="transfer", relaunches=0, content_filter=None,
        blocked_state_ids=[s.id for s in golden.states if s.kind == "blocked"],
        coverage=Coverage(states_found=len(golden.states), actions_taken=len(golden.edges),
                          stop_reason="fixture: assembled from captures", checklist_answered=[], checklist_open=[]))
    (out / "explore.json").write_text(explore.model_dump_json(indent=1))
    return out


if __name__ == "__main__":
    for name in APPS:
        print(build(name, Path(sys.argv[1]) / name))
