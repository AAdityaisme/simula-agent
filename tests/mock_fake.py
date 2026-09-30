"""A deterministic stand-in for the mock builder: draws each element as a box at its rect, straight from the model.
Lets the stage, the renderer, and the validator run offline on any product model."""

import shutil
from html import escape
from pathlib import Path

from simula.contracts import ProductModel
from simula.stages.mock import pick_scope, scope_edges, tagged_ids, usable_asset
from tests.conftest import FIXTURES


def skeleton_html(model: ProductModel, screens: list[str] | None = None) -> str:
    scope = [s for s in pick_scope(model) if screens is None or s.id in screens]
    edges = {e.element_id: e for e in scope_edges(model, scope)}
    # A mock batch's brief lists every edge from its screens to any in-scope screen, so a tap target stays on top
    # even when `screens` is one batch and its edge ends in another.
    tappable = {e.element_id for e in scope_edges(model, pick_scope(model))}
    sections = []
    for state in scope:
        tagged = tagged_ids(state)
        parent = f' data-parent="{state.parent_id}"' if state.parent_id else ""
        boxes = [f"<p>{escape(state.name)}</p>"]
        for e in state.elements:
            if not e.in_mock:
                continue
            r = e.rect_dp
            style = f"position:absolute;left:{r.x}px;top:{r.y}px;width:{r.w}px;height:{r.h}px;margin:0"
            attrs = f' data-el="{e.id}"' if e.id in tagged else ""
            edge = edges.get(e.id)
            if edge:
                attrs += f' data-edge="{edge.id}" data-transition="{edge.transition}"'
            if e.id in tappable:
                style += ";z-index:1"
            if usable_asset(e, state.elements, model.device):
                boxes.append(f'<img src="assets/{e.id}.png" style="{style}"{attrs}>')
            else:
                boxes.append(f'<div style="{style};color:{e.fg_hex or "#000"}"{attrs}>{escape(e.text or e.label)}</div>')
        sections.append(f'<section data-screen="{state.id}"{parent}>{"".join(boxes)}</section>')
    return f"<!doctype html><html><head><meta charset=\"utf-8\"></head><body>{''.join(sections)}</body></html>"


def golden(app: str) -> ProductModel:
    return ProductModel.model_validate_json((FIXTURES / "golden" / app / "product_model.json").read_text())


def seed_model(run_dir: Path, app: str) -> Path:
    """A run folder holding only model/, copied from the app's golden."""
    shutil.copytree(FIXTURES / "golden" / app, run_dir / "model")
    return run_dir
