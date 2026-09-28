"""Goal 2: the interactive mock. One model call writes the HTML from model/ alone; code adds navigation,
renders every screen, and checks the mock contract."""

import io
import json
import math
import re
import shutil
from collections import Counter
from html.parser import HTMLParser

import numpy as np
from PIL import Image

from simula import config, llm, render
from simula.config import ROOT
from simula.contracts import Device, Edge, Element, ProductModel, Rect, State
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx

MAX_SCREENS = 8
TAGGED_PER_REPEAT = 2
WALL_SECONDS = 30 * 60
IMAGE_LONG_SIDE = 1568
ART_MIN_SIDE = 48
ART_MIN_COLORS = 120
ALREADY_CROPPED = 0.9
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
    art = crop_art(model_dir, mock_dir, scope, model.device)

    html = generate(ctx, model, scope, art)
    html = with_runtime(wire_edges(html, model, screens), home_id(scope))
    (mock_dir / "index.html").write_text(html)

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


def home_id(scope: list[State]) -> str:
    """Where the mock opens and simula.reset() returns: the first screen that is not a dialog over another one."""
    return next((s.id for s in scope if s.parent_id is None), scope[0].id)


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
    return bool(e.in_mock and e.asset_png) and under_wallpaper_limit(e.rect_dp, device)


def under_wallpaper_limit(r: Rect, device: Device) -> bool:
    screen = content_rect(device)
    return area(overlap(r, screen)) <= render.WALLPAPER_SHARE * area(screen)


def copy_assets(model_dir, mock_dir, scope: list[State], device: Device) -> None:
    (mock_dir / "assets").mkdir(parents=True, exist_ok=True)
    for e in (e for s in scope for e in s.elements if usable_asset(e, device)):
        shutil.copyfile(model_dir / e.asset_png, mock_dir / "assets" / f"{e.id}.png")


# ---------- art painted inside containers ----------

def crop_art(model_dir, mock_dir, scope: list[State], device: Device) -> dict[str, Rect]:
    """Crops the real pictures that sit inside containers with no image node of their own into
    mock/assets/<element id>.art.png, and lists them in mock/art.json so QA can mask them."""
    art = {}
    for state in scope:
        image = Image.open(model_dir / state.canonical_png).convert("RGB")
        for eid, rect in find_art(state, image, device).items():
            crop_px(image, rect, device.scale).save(mock_dir / "assets" / f"{eid}.art.png")
            art[eid] = rect
    listed = {art_src(eid): r.model_dump() for eid, r in art.items()}
    (mock_dir / "art.json").write_text(json.dumps({"schema_version": 1, "art": listed}, indent=1))
    return art


def find_art(state: State, image: Image.Image, device: Device) -> dict[str, Rect]:
    """For each drawn element with no asset and no text of its own (its words cover its rect): the largest
    picture-like region inside it that nothing else is drawn over, unless that region is wallpaper-sized.
    image is the state's content-area screenshot."""
    screen = content_rect(device)
    cropped = [e.rect_dp for e in state.elements if usable_asset(e, device)]
    art = {}
    for e in state.elements:
        if not e.in_mock or e.asset_png or e.text:
            continue
        box = overlap(e.rect_dp, screen)
        blockers = [r for r in (overlap(o.rect_dp, box) for o in state.elements if drawn_over(o, e)) if area(r) > 0]
        for rect in free_rects(box, blockers, ART_MIN_SIDE):
            if any(area(overlap(rect, c)) >= ALREADY_CROPPED * area(rect) for c in cropped):
                continue
            if not is_picture(crop_px(image, rect, device.scale)):
                continue
            if under_wallpaper_limit(rect, device):
                art[e.id] = rect
                cropped.append(rect)
            break
    return art


def drawn_over(other: Element, container: Element) -> bool:
    """Another element with words or an image of its own. A bigger element around the container is its parent."""
    a, b = other.rect_dp, container.rect_dp
    around = contains(a, b) and area(a) > area(b)
    return other.id != container.id and bool(other.text or other.label or other.asset_png) and not around


def free_rects(box: Rect, blockers: list[Rect], min_side: float) -> list[Rect]:
    """Rectangles in box, both sides at least min_side, that overlap no blocker, largest first. The blockers'
    edges cut box into a grid; each grid row is then a largest-rectangle-in-a-histogram scan."""
    xs = sorted({box.x, box.x + box.w, *(v for b in blockers for v in (b.x, b.x + b.w))})
    ys = sorted({box.y, box.y + box.h, *(v for b in blockers for v in (b.y, b.y + b.h))})
    blocked = np.zeros((len(ys) - 1, len(xs) - 1), dtype=bool)
    for b in blockers:
        blocked[ys.index(b.y):ys.index(b.y + b.h), xs.index(b.x):xs.index(b.x + b.w)] = True
    heights, found = [0.0] * (len(xs) - 1), set()
    for row, bottom in enumerate(ys[1:]):
        heights = [0.0 if blocked[row, c] else h + bottom - ys[row] for c, h in enumerate(heights)]
        stack = []
        for c, h in enumerate(heights + [0.0]):
            start = c
            while stack and stack[-1][1] >= h:
                start, bar = stack.pop()
                rect = snap_inward(Rect(x=xs[start], y=bottom - bar, w=xs[c] - xs[start], h=bar))
                if min(rect.w, rect.h) >= min_side:
                    found.add((rect.x, rect.y, rect.w, rect.h))
            stack.append((start, h))
    return sorted((Rect(x=x, y=y, w=w, h=h) for x, y, w, h in found), key=area, reverse=True)


def snap_inward(r: Rect) -> Rect:
    """Rounds a rect to 0.1 dp without letting it grow past where it was."""
    x0, y0 = math.ceil(r.x * 10 - 1e-6) / 10, math.ceil(r.y * 10 - 1e-6) / 10
    x1, y1 = math.floor((r.x + r.w) * 10 + 1e-6) / 10, math.floor((r.y + r.h) * 10 + 1e-6) / 10
    return Rect(x=x0, y=y0, w=round(x1 - x0, 1), h=round(y1 - y0, 1))


def is_picture(image: Image.Image) -> bool:
    # ponytail: distinct colors at 16 levels per channel. On the three goldens, flat fills, text and icons reach
    # at most 76 and pictures at least 194; a flat-shaded illustration with few colors is missed. Swap in a
    # texture measure if that happens.
    levels = (np.asarray(image) // 16).reshape(-1, 3)
    return len(np.unique(levels, axis=0)) >= ART_MIN_COLORS


def crop_px(image: Image.Image, r: Rect, scale: float) -> Image.Image:
    return image.crop((round(r.x * scale), round(r.y * scale), round((r.x + r.w) * scale), round((r.y + r.h) * scale)))


def art_src(eid: str) -> str:
    return f"assets/{eid}.art.png"


def content_rect(device: Device) -> Rect:
    return Rect(x=0, y=0, w=device.w_px / device.scale, h=(device.content_bottom_px - device.content_top_px) / device.scale)


def overlap(a: Rect, b: Rect) -> Rect:
    x, y = max(a.x, b.x), max(a.y, b.y)
    return Rect(x=x, y=y, w=max(0, min(a.x + a.w, b.x + b.w) - x), h=max(0, min(a.y + a.h, b.y + b.h) - y))


def area(r: Rect) -> float:
    return r.w * r.h


def contains(a: Rect, b: Rect) -> bool:
    return a.x <= b.x and a.y <= b.y and a.x + a.w >= b.x + b.w and a.y + a.h >= b.y + b.h


# ---------- the model call ----------

def generate(ctx: Ctx, model: ProductModel, scope: list[State], art: dict[str, Rect]) -> str:
    role = config.roles(ctx.profile)["mock_builder"]
    system = system_prompt()
    content = screenshots(ctx, scope) + [{"type": "text", "text": brief(model, scope, art)}]
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


def brief(model: ProductModel, scope: list[State], art: dict[str, Rect]) -> str:
    edges = scope_edges(model, scope)
    edge_ids = {e.id for e in edges}
    flows = [{"name": f.name, "edge_ids": [i for i in f.edge_ids if i in edge_ids]} for f in model.flows]
    screens = [state_brief(s, model.device, art) for s in scope]
    elements = [e for s in screens for e in s["elements"]]
    data = {
        "app": model.app,
        "image_files": [e["asset"] for e in elements if "asset" in e] + [e["art"]["src"] for e in elements if "art" in e],
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


def state_brief(state: State, device: Device, art: dict[str, Rect]) -> dict:
    tagged = tagged_ids(state)
    elements = []
    for e in state.elements:
        if not e.in_mock:
            continue
        item = {"id": e.id, "type": e.type, "text": e.text, "label": e.label, "role": e.role,
                "x": round(e.rect_dp.x, 1), "y": round(e.rect_dp.y, 1), "w": round(e.rect_dp.w, 1), "h": round(e.rect_dp.h, 1),
                "fg": e.fg_hex, "bg": e.bg_hex, "font": e.font_guess,
                "text_h": round(e.font_px / device.scale, 1) if e.font_px else None,
                "asset": f"assets/{e.id}.png" if usable_asset(e, device) else None, "tag": e.id in tagged,
                "art": {"src": art_src(e.id), "rect": art[e.id].model_dump()} if e.id in art else None}
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


class StartTags(HTMLParser):
    """Every start tag in a page: its decoded attributes and where its source text sits."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self._line_starts = [0] + [i + 1 for i, c in enumerate(html) if c == "\n"]
        self.feed(html)
        self.close()

    def handle_starttag(self, name, attrs):
        line, col = self.getpos()
        start, text = self._line_starts[line - 1] + col, self.get_starttag_text()
        self.tags.append({"name": name, "attrs": dict(attrs), "start": start, "end": start + len(text),
                          "self_closing": text.endswith("/>")})


def wire_edges(html: str, model: ProductModel, screens: list[str]) -> str:
    """Code owns every edge. It writes each known data-edge tag's data-transition, and puts an in-scope edge
    the builder left out on the tag that already carries its element's data-el."""
    edges = {e.id: e for e in model.edges}
    tags = StartTags(html).tags
    placed = {t["attrs"].get("data-edge") for t in tags}
    by_element = {}
    for t in tags:
        if t["attrs"].get("data-el"):
            by_element.setdefault(t["attrs"]["data-el"], t)
    changed = {}
    for e in model.edges:
        tag = by_element.get(e.element_id)
        missing = e.from_state in screens and e.to_state in screens and e.id not in placed
        if missing and tag and "data-edge" not in tag["attrs"]:
            tag["attrs"]["data-edge"] = e.id
            changed[tag["start"]] = tag
    for t in tags:
        edge = edges.get(t["attrs"].get("data-edge"))
        if edge and t["attrs"].get("data-transition") != edge.transition:
            t["attrs"] = _with_transition(t["attrs"], edge.transition)
            changed[t["start"]] = t
    return _rewrite(html, sorted(changed.values(), key=lambda t: t["start"]))


def _with_transition(attrs: dict, transition: str) -> dict:
    out = {}
    for name, value in attrs.items():
        if name != "data-transition":
            out[name] = value
        if name == "data-edge":
            out["data-transition"] = transition
    return out


def _rewrite(html: str, tags: list[dict]) -> str:
    """Re-serializes only the given start tags and leaves every other byte of the page as the builder wrote it."""
    parts, at = [], 0
    for t in tags:
        attrs = "".join(f" {name}" if value is None else f' {name}="{_attr_value(value)}"'
                        for name, value in t["attrs"].items())
        parts += [html[at:t["start"]], f"<{t['name']}{attrs}{' /' if t['self_closing'] else ''}>"]
        at = t["end"]
    return "".join(parts) + html[at:]


def _attr_value(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")


def with_runtime(html: str, root: str) -> str:
    """Adds the code-owned navigation runtime, so every mock moves between screens the same way. Replaces a
    runtime already in the page, so QA and flows can re-apply it after every edit."""
    html = re.sub(r'<style id="simula-runtime">.*?</style>\n?', "", html, flags=re.S)
    html = re.sub(r'<script id="simula-runtime-js">.*?</script>\n?', "", html, flags=re.S)
    html = _insert_before(html, "</head>", RUNTIME_CSS)
    return _insert_before(html, "</body>", RUNTIME_JS % json.dumps(root))


def _insert_before(html: str, tag: str, snippet: str) -> str:
    at = html.lower().rfind(tag)
    return html + snippet if at == -1 else html[:at] + snippet + html[at:]


# ---------- exhibit ----------

def exhibit(ctx: Ctx, model: ProductModel, scope: list[State], html: str, report) -> str:
    attrs = [t["attrs"] for t in StartTags(html).tags]
    placed = Counter(a["data-el"].split(".")[0] for a in attrs if a.get("data-el"))
    wired = {a["data-edge"] for a in attrs if a.get("data-edge")}
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
