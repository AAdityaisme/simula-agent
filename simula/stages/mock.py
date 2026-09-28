"""Goal 2: the interactive mock. One model call writes the HTML from model/ alone; code adds navigation,
renders every screen, and checks the mock contract."""

import io
import json
import re
import shutil
from collections import Counter

from PIL import Image

from simula import config, llm, render
from simula.config import ROOT
from simula.contracts import Device, Edge, Element, ProductModel, State
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx

MAX_SCREENS = 8
TAGGED_PER_REPEAT = 2
WALL_SECONDS = 20 * 60
IMAGE_LONG_SIDE = 1568
RETRY_EFFORT = {"max": "high", "xhigh": "high"}
SHORTER = "A first attempt ran out of output tokens. Write shorter CSS: shared classes, no repeated rules, no comments."

RUNTIME_CSS = """<style id="simula-runtime">
html,body{margin:0;padding:0;width:411px;height:914px;overflow:hidden}
body{position:relative}
[data-screen]{position:absolute!important;left:0!important;top:51.8px!important;width:411px!important;height:838.2px!important;overflow:hidden;box-sizing:border-box}
[data-screen]:not(.simula-on){display:none!important}
[data-screen][data-parent]{z-index:10;background:transparent!important}
.simula-push{animation:simula-slide .25s ease-out}
.simula-back{animation:simula-slide-back .25s ease-out}
.simula-modal{animation:simula-fade .2s ease-out}
@keyframes simula-slide{from{transform:translateX(100%)}}
@keyframes simula-slide-back{from{transform:translateX(-30%)}}
@keyframes simula-fade{from{opacity:0}}
@media (prefers-reduced-motion:reduce){.simula-push,.simula-back,.simula-modal{animation:none}}
</style>
"""

RUNTIME_JS = """<script id="simula-runtime-js">
(() => {
  const ROOT = %s;
  const find = id => document.querySelector(`[data-screen="${CSS.escape(id)}"]`);
  let current = null;
  function show(id, transition) {
    const target = find(id);
    if (!target) return false;
    const from = current && find(current);
    const closingOverlay = from && from.dataset.parent === id;
    const kind = transition === 'unknown' ? 'push' : transition;
    for (const s of document.querySelectorAll('[data-screen]')) s.classList.remove('simula-on', 'simula-push', 'simula-back', 'simula-modal');
    const parent = target.dataset.parent && find(target.dataset.parent);
    if (parent) parent.classList.add('simula-on');
    target.classList.add('simula-on');
    if (['push', 'back', 'modal'].includes(kind) && !closingOverlay) target.classList.add('simula-' + kind);
    current = id;
    document.body.dataset.transition = kind;
    return true;
  }
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-edge]');
    if (!el) return;
    e.preventDefault();
    show(el.dataset.edge.split('>').pop(), el.dataset.transition || 'push');
  });
  window.simula = {go: id => show(id, 'tab'), state: () => current, reset: () => show(ROOT, 'tab')};
  show(ROOT, 'tab');
})();
</script>
"""


def run(ctx: Ctx) -> None:
    model_dir, mock_dir = ctx.run_dir / "model", ctx.run_dir / "mock"
    model = ProductModel.model_validate_json((model_dir / "product_model.json").read_text())
    scope = pick_scope(model)
    screens = [s.id for s in scope]
    run_trace(ctx.run_dir, stage="mock", step="scope", decider="code", note=" ".join(screens))
    copy_assets(model_dir, mock_dir, scope, model.device)

    html = generate(ctx, model, scope)
    (mock_dir / "index.html").write_text(with_runtime(stamp_transitions(html, model), screens[0]))

    report = render.render_and_validate(mock_dir, model, screens)
    (mock_dir / "contract_report.json").write_text(report.model_dump_json(indent=1))
    run_trace(ctx.run_dir, stage="mock", step="contract", decider="code", outcome="ok" if report.passed else "error",
              note=f"{len(screens)} screens rendered, {len(report.errors)} contract errors")
    write_exhibit(ctx.run_dir, 3, "mock", exhibit(ctx, model, scope, html, report))


def pick_scope(model: ProductModel) -> list[State]:
    """The model stage chose the scope; this only enforces the contract's limits on it."""
    scope = [s for s in model.states if s.in_mock_scope and s.kind != "blocked" and s.content_rating != "unsafe"]
    if not scope:
        raise ValueError("the product model has no state in mock scope")
    return scope[:MAX_SCREENS]


def tagged_ids(state: State) -> set[str]:
    """Elements that carry data-el: every in_mock element, but only the first 2 items of a repeated list."""
    seen, ids = Counter(), set()
    for e in state.elements:
        if not e.in_mock:
            continue
        if e.repeat_group:
            seen[e.repeat_group] += 1
            if seen[e.repeat_group] > TAGGED_PER_REPEAT:
                continue
        ids.add(e.id)
    return ids


def scope_edges(model: ProductModel, scope: list[State]) -> list[Edge]:
    ids = {s.id for s in scope}
    return [e for e in model.edges if e.from_state in ids and e.to_state in ids]


def usable_asset(e: Element, device: Device) -> bool:
    """An asset the builder may use: one that, drawn at its rect, stays under the no-wallpaper limit."""
    if not (e.in_mock and e.asset_png):
        return False
    w, h = device.w_px / device.scale, (device.content_bottom_px - device.content_top_px) / device.scale
    r = e.rect_dp
    shown = max(0, min(r.x + r.w, w) - max(r.x, 0)) * max(0, min(r.y + r.h, h) - max(r.y, 0))
    return shown <= render.WALLPAPER_SHARE * w * h


def copy_assets(model_dir, mock_dir, scope: list[State], device: Device) -> None:
    (mock_dir / "assets").mkdir(parents=True, exist_ok=True)
    for e in (e for s in scope for e in s.elements if usable_asset(e, device)):
        shutil.copyfile(model_dir / e.asset_png, mock_dir / "assets" / f"{e.id}.png")


# ---------- the model call ----------

def generate(ctx: Ctx, model: ProductModel, scope: list[State]) -> str:
    role = config.roles(ctx.profile)["mock_builder"]
    system = system_prompt()
    content = screenshots(ctx, scope) + [{"type": "text", "text": brief(model, scope)}]
    budget = llm.Budget.for_stage("mock", ctx.run_dir / "trace.jsonl", ctx.usd_cap)

    def ask(effort, content):
        text, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="mock", step="generate", model=role["model"],
                           effort=effort, system=system, messages=[{"role": "user", "content": content}],
                           max_tokens=role.get("max_tokens", 64000), budget=budget, no_cache=ctx.no_cache,
                           replay=ctx.replay, attempts=1, total_timeout=WALL_SECONDS)
        return extract_html(text)

    effort = role.get("effort")
    try:
        return ask(effort, content)
    except llm.LLMFailure as e:
        if e.outcome != "max_tokens":
            raise
    retry_effort = RETRY_EFFORT.get(effort, effort)
    run_trace(ctx.run_dir, stage="mock", step="generate", decider="code", outcome="retry",
              note=f"max_tokens: retrying at effort={retry_effort} with shorter CSS")
    return ask(retry_effort, content + [{"type": "text", "text": SHORTER}])


def system_prompt() -> str:
    parts = [p.read_text() for p in sorted((ROOT / "prompts" / "mock").glob("*.md"))]
    return "\n\n".join(parts + [contract_text()])


def contract_text() -> str:
    """The geometry and mock-contract sections of docs/CONTRACTS.md, verbatim."""
    doc = (ROOT / "docs" / "CONTRACTS.md").read_text()
    sections = re.split(r"(?m)^(?=## )", doc)
    return "\n".join(s for s in sections if s.startswith(("## 2. Geometry", "## 7. Mock contract")))


def screenshots(ctx: Ctx, scope: list[State]) -> list[dict]:
    parts = []
    for s in scope:
        image = Image.open(ctx.run_dir / "model" / s.canonical_png)
        image.thumbnail((IMAGE_LONG_SIDE, IMAGE_LONG_SIDE))
        png = io.BytesIO()
        image.save(png, format="PNG")
        parts += [{"type": "text", "text": f"Screenshot of {s.id} ({s.name}), content area only:"},
                  {"type": "image", "png": png.getvalue()}]
    return parts


def brief(model: ProductModel, scope: list[State]) -> str:
    edges = scope_edges(model, scope)
    edge_ids = {e.id for e in edges}
    flows = [{"name": f.name, "edge_ids": [i for i in f.edge_ids if i in edge_ids]} for f in model.flows]
    screens = [state_brief(s, model.device) for s in scope]
    data = {
        "app": model.app,
        "image_files": [e["asset"] for s in screens for e in s["elements"] if "asset" in e],
        "screens": screens,
        "edges": [{"id": e.id, "from": e.from_state, "to": e.to_state, "element": e.element_id,
                   "transition": e.transition} for e in edges],
        "flows": [f for f in flows if f["edge_ids"]],
        "cross_screen_values": [v.model_dump() for v in model.cross_screen_values],
    }
    return ("The product model for the screens to mock. Rects are in CSS px relative to the screen's section "
            "(content coordinates). `tag: false` elements are drawn but carry no data-el. `image_files` is every "
            "image that exists and `edges` every data-edge allowed: never invent another id or file name.\n\n"
            + json.dumps(data, separators=(",", ":")))


def state_brief(state: State, device: Device) -> dict:
    tagged = tagged_ids(state)
    elements = []
    for e in state.elements:
        if not e.in_mock:
            continue
        item = {"id": e.id, "type": e.type, "text": e.text, "label": e.label, "role": e.role,
                "x": round(e.rect_dp.x, 1), "y": round(e.rect_dp.y, 1), "w": round(e.rect_dp.w, 1), "h": round(e.rect_dp.h, 1),
                "fg": e.fg_hex, "bg": e.bg_hex, "font": e.font_guess,
                "text_h": round(e.font_px / device.scale, 1) if e.font_px else None,
                "asset": f"assets/{e.id}.png" if usable_asset(e, device) else None, "tag": e.id in tagged}
        elements.append({k: v for k, v in item.items() if v not in (None, "", "unknown")})
    return {"id": state.id, "kind": state.kind, "parent": state.parent_id, "name": state.name,
            "purpose": state.purpose, "elements": elements}


def extract_html(text: str) -> str:
    fenced = re.search(r"```html\s*\n(.*?)```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.lower().find("<!doctype"), text.lower().rfind("</html>")
    if start == -1 or end == -1:
        raise ValueError("the mock builder returned no ```html block")
    return text[start:end + len("</html>")]


def stamp_transitions(html: str, model: ProductModel) -> str:
    """Code owns every edge's transition, so code writes data-transition onto each known data-edge tag."""
    transitions = {e.id: e.transition for e in model.edges}

    def stamp(tag: re.Match) -> str:
        edge = re.search(r'data-edge="([^"]*)"', tag.group(0)).group(1)
        if edge not in transitions:
            return tag.group(0)
        bare = re.sub(r'\sdata-transition="[^"]*"', "", tag.group(0))
        return bare.replace(f'data-edge="{edge}"', f'data-edge="{edge}" data-transition="{transitions[edge]}"', 1)
    return re.sub(r'<[^>]*\sdata-edge="[^"]*"[^>]*>', stamp, html)


def with_runtime(html: str, root: str) -> str:
    """Adds the code-owned navigation runtime, so every mock moves between screens the same way."""
    html = _insert_before(html, "</head>", RUNTIME_CSS)
    return _insert_before(html, "</body>", RUNTIME_JS % json.dumps(root))


def _insert_before(html: str, tag: str, snippet: str) -> str:
    at = html.lower().rfind(tag)
    return html + snippet if at == -1 else html[:at] + snippet + html[at:]


# ---------- exhibit ----------

def exhibit(ctx: Ctx, model: ProductModel, scope: list[State], html: str, report) -> str:
    placed = Counter(i.split(".")[0] for i in re.findall(r'data-el="([^"]+)"', html))
    wired = set(re.findall(r'data-edge="([^"]+)"', html))
    edges = scope_edges(model, scope)
    lines = [f"# Mock: {model.app}", "",
             f"Contract: **{'PASS' if report.passed else 'FAIL'}** ({len(report.errors)} errors). "
             f"Model spend this stage: ${stage_usd(ctx):.4f}.", "",
             "| Screen | Name | data-el placed / expected | Render |", "|---|---|---|---|"]
    for s in scope:
        lines.append(f"| {s.id} | {s.name} | {placed[s.id]} / {len(tagged_ids(s))} | `mock/renders/{s.id}.png` |")
    lines += ["", f"Edges wired: {len(wired & {e.id for e in edges})} / {len(edges)} in scope."]
    if report.errors:
        lines += ["", "| Error | Screen | Detail |", "|---|---|---|"]
        lines += [f"| {e.kind} | {e.screen or ''} | {e.detail.replace('|', '/')} |" for e in report.errors]
        lines += ["", "Contract errors are fixed in QA round 1, not by regenerating."]
    return "\n".join(lines) + "\n"


def stage_usd(ctx: Ctx) -> float:
    return sum(line.usd for line in read_trace(ctx.run_dir / "trace.jsonl") if line.stage == "mock")
