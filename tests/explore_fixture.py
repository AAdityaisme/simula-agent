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
from simula.contracts import ActionLine, Coverage, ExploreFile, IconLabel, Point, ProductModel, StateFile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
APPS = ("janitorai", "luzia", "aol")
PREFIX = "Found these elements on screen: "


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
        icon_labels = []
        if reply:
            shutil.copy(reply, out / "states" / f"{state.id}.elements.json")
            listed = {e["ref"]: e for e in json.loads(json.loads(reply.read_text())["content"][0]["text"][len(PREFIX):])}
            icon_labels = [IconLabel(mcp_ref=e.mcp_ref, name=e.label) for e in state.elements
                           if e.label and not (listed[e.mcp_ref].get("text") or listed[e.mcp_ref].get("label"))]
        state_file = StateFile(
            state_id=state.id, kind=state.kind, parent_id=state.parent_id, fingerprint=state.fingerprint,
            foreground_package=package, screenshot=f"states/{state.id}.png",
            elements_reply=f"states/{state.id}.elements.json" if reply else "", settled=True, settle_seconds=0.0,
            dynamic_regions=state.dynamic_regions, captured_at="fixture", icon_labels=icon_labels,
            blocked_reason=state.blocked_reason)
        (out / "states" / f"{state.id}.json").write_text(state_file.model_dump_json(indent=1))
    elements = {e.id: e for s in golden.states for e in s.elements}
    with open(out / "actions.jsonl", "w") as f:
        for step, edge in enumerate(golden.edges, start=1):
            tapped = elements.get(edge.element_id)
            center = Point(x=int(tapped.rect_px.x + tapped.rect_px.w / 2),
                           y=int(tapped.rect_px.y + tapped.rect_px.h / 2)) if tapped else None
            line = ActionLine(step=step, from_state=edge.from_state, to_state=edge.to_state, action=edge.action,
                              mcp_ref=tapped.mcp_ref if tapped else None, tap_px=center, transition=edge.transition,
                              change_summary=edge.change_summary, outcome="ok")
            f.write(line.model_dump_json() + "\n")
    explore = ExploreFile(
        app_package=package, app_version=golden.app_version, budget="transfer", relaunches=0, content_filter=None,
        blocked_state_ids=[s.id for s in golden.states if s.kind == "blocked"],
        device=golden.device, coverage=Coverage(states_found=len(golden.states), actions_taken=len(golden.edges),
                          stop_reason="fixture: assembled from captures", checklist_answered=[], checklist_open=[]))
    (out / "explore.json").write_text(explore.model_dump_json(indent=1))
    return out


if __name__ == "__main__":
    for name in APPS:
        print(build(name, Path(sys.argv[1]) / name))
