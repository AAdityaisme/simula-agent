"""Builds the golden product models (TEST DATA ONLY) for every app that has a golden/<app>/meaning.json.

One generic script. Code computes every number from the fixture trees and screenshots (ids, rects, crops,
colors). The hand-written meaning (names, purposes, labels for unlabeled icons, mechanics, ledger, edges)
lives in golden/<app>/meaning.json and is hand-checked by Aadi. A state backed only by a screenshot has
no elements, because the fixture capture got no tree for it.

    uv run python tests/fixtures/golden/build_golden.py
"""

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from simula.contracts import (Coverage, CrossScreenValue, Device, Edge, Element, Flow, LedgerItem, Mechanic,
                              ProductModel, Provenance, Rect, State)

HERE = Path(__file__).parent
FIXTURES = HERE.parent
DEVICE = Device()
PREFIX = "Found these elements on screen: "
ROLE_BY_CLASS = {"TextView": "text", "Button": "button", "ImageButton": "button", "ImageView": "image",
                 "EditText": "text input"}


def load_tree(app: str, capture: str) -> list[dict]:
    reply = json.loads((FIXTURES / "trees" / app / f"{capture}.elements.json").read_text())
    return json.loads(reply["content"][0]["text"][len(PREFIX):])


def in_content(e: dict) -> bool:
    c = e["coordinates"]
    full_screen = c["width"] >= DEVICE.w_px and c["height"] >= 2000
    system = "systemui" in (e.get("identifier") or "")
    return DEVICE.content_top_px <= c["y"] < DEVICE.content_bottom_px and not full_screen and not system


def to_dp(c: dict) -> Rect:
    s = DEVICE.scale
    return Rect(x=round(c["x"] / s, 2), y=round((c["y"] - DEVICE.content_top_px) / s, 2),
                w=round(c["width"] / s, 2), h=round(c["height"] / s, 2))


def hex_color(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def colors(pixels: np.ndarray) -> tuple[str | None, str | None]:
    if pixels.size == 0:
        return None, None
    flat = pixels.reshape(-1, 3)
    bg = np.median(flat, axis=0)
    far = flat[np.abs(flat.astype(int) - bg).sum(axis=1) > 90]
    return (hex_color(np.median(far, axis=0)) if len(far) > 20 else None), hex_color(bg)


def is_image_like(e: dict) -> bool:
    c = e["coordinates"]
    no_words = not (e.get("text") or e.get("label"))
    small_enough = c["width"] * c["height"] < 0.4 * DEVICE.w_px * DEVICE.h_px
    return (e["type"].endswith("ImageView") or no_words) and min(c["width"], c["height"]) >= 48 and small_enough


def build_elements(app: str, spec: dict, image: Image.Image, out: Path) -> list[Element]:
    sid, scope = spec["id"], spec["in_mock_scope"]
    pixels = np.asarray(image)
    labels = spec.get("labels", {})
    elements = []
    for n, e in enumerate([e for e in load_tree(app, spec["tree"]) if in_content(e)], start=1):
        c, eid = e["coordinates"], f"{sid}.e{n:02d}"
        text, label = e.get("text") or "", labels.get(e["ref"]) or e.get("label") or ""
        kind = e["type"].split(".")[-1]
        asset = None
        if scope and is_image_like(e) and e["ref"] not in labels:
            asset = f"assets/{eid}.png"
            image.crop((c["x"], c["y"], c["x"] + c["width"], c["y"] + c["height"])).save(out / asset)
        fg, bg = colors(pixels[c["y"]:c["y"] + c["height"], c["x"]:c["x"] + c["width"]])
        elements.append(Element(
            id=eid, mcp_ref=e["ref"], type=kind, text=text, label=label, source="mcp",
            rect_px=Rect(x=c["x"], y=c["y"], w=c["width"], h=c["height"]), rect_dp=to_dp(c),
            role=spec.get("roles", {}).get(e["ref"]) or ROLE_BY_CLASS.get(kind, "container"),
            asset_png=asset, fg_hex=fg, bg_hex=bg, font_px=float(c["height"]) if kind == "TextView" else None,
            font_guess="unknown", in_mock=scope and bool(text or label or asset), repeat_group=None))
    return elements


def build_state(app: str, spec: dict, out: Path) -> State:
    name = spec.get("tree") or spec["screen"]
    source = FIXTURES / ("trees" if "tree" in spec else "screens") / app / f"{name}.png"
    image = Image.open(source).convert("RGB")
    content = image.crop((0, DEVICE.content_top_px, DEVICE.w_px, DEVICE.content_bottom_px))
    content.save(out / "states" / f"{spec['id']}.png")
    return State(
        id=spec["id"], kind=spec["kind"], parent_id=spec.get("parent"), name=spec["name"], purpose=spec["purpose"],
        fingerprint=f"fixture:{'tree' if 'tree' in spec else 'screen'}:{name}",
        canonical_png=f"states/{spec['id']}.png",
        elements=build_elements(app, spec, image, out) if "tree" in spec else [],
        in_mock_scope=spec["in_mock_scope"], content_rating=spec.get("rating", "safe"), dynamic_regions=[],
        blocked_reason=spec.get("blocked_reason"))


def find(states: dict[str, State], ref: list[str]) -> str:
    """ref = [state id, "@e12" (an MCP ref) or a text/label prefix]."""
    sid, key = ref
    for e in states[sid].elements:
        if e.mcp_ref == key or (not key.startswith("@") and (e.text or e.label).startswith(key)):
            return e.id
    raise KeyError(f"{sid}: no element matches {key!r}")


def transition(states: dict[str, State], element_id: str, to: str) -> str:
    """The same rule stage 2 uses: closing a modal goes back, opening one is a modal, a tab tap is a tab switch,
    anything else is a push."""
    element = next(e for s in states.values() for e in s.elements if e.id == element_id)
    source = states[element_id.split(".")[0]]
    if source.kind in ("modal", "sheet") and source.parent_id == to:
        return "back"
    if states[to].kind in ("modal", "sheet"):
        return "modal"
    return "tab" if element.role == "tab" else "push"


def build(app: str) -> ProductModel:
    out = HERE / app
    meaning = json.loads((out / "meaning.json").read_text())
    for sub in ("states", "assets"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir()
    states = {spec["id"]: build_state(app, spec, out) for spec in meaning["states"]}
    edges = [Edge(id=f"{find(states, e['from'])}>{e['to']}", from_state=e["from"][0], to_state=e["to"],
                  element_id=find(states, e["from"]), action="tap",
                  transition=transition(states, find(states, e["from"]), e["to"]), change_summary=e["summary"])
             for e in meaning["edges"]]
    edge_by_hop = {(e.from_state, e.to_state): e.id for e in edges}
    flows = [Flow(id=f["id"], name=f["name"], purpose=f["purpose"],
                  edge_ids=[edge_by_hop[(a, b)] for a, b in zip(f["path"], f["path"][1:])],
                  evidence_ids=[find(states, r) for r in f.get("evidence", [])])
             for f in meaning["flows"]]
    mechanics = [Mechanic(id=m["id"], kind=m["kind"], evidence_ids=[find(states, r) for r in m["evidence"]],
                          summary=m["summary"], observed_numbers=m.get("numbers", []), status=m["status"])
                 for m in meaning["mechanics"]]
    ledger = [LedgerItem(id=item["id"], kind=item["kind"], verbatim=item["verbatim"],
                         evidence_ids=[find(states, item["evidence"])]) for item in meaning["ledger"]]
    values = [CrossScreenValue(id=v["id"], label=v["label"], value_text=v["value"],
                               evidence_ids=[find(states, r) for r in v["evidence"]])
              for v in meaning.get("cross_screen_values", [])]
    model = ProductModel(
        app=app, app_version=meaning["app_version"], app_category=meaning["app_category"], run_id="golden",
        device=DEVICE, states=list(states.values()), edges=edges, flows=flows, mechanics=mechanics, cross_screen_values=values,
        value_ledger=ledger, open_questions=meaning["open_questions"],
        coverage=Coverage(states_found=len(states), actions_taken=0, stop_reason="fixture: assembled from captures",
                          checklist_answered=meaning["checklist_answered"], checklist_open=meaning["checklist_open"]),
        provenance=Provenance(source="fixture", fixture_path=f"tests/fixtures/golden/{app}"))
    (out / "product_model.json").write_text(model.model_dump_json(indent=1))
    return model


def main() -> None:
    for meaning in sorted(HERE.glob("*/meaning.json")):
        model = build(meaning.parent.name)
        print(f"{model.app}: {len(model.states)} states, {sum(len(s.elements) for s in model.states)} elements, "
              f"{len(model.edges)} edges, {len(model.mechanics)} mechanics, {len(model.value_ledger)} ledger items")


if __name__ == "__main__":
    main()
