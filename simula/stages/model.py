"""Goal 1: the product model. Code writes every fact, one model call writes meaning keyed by those ids,
and code checks the merge. Reads explore/ only; writes model/ only."""

import json
import re
import shutil
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from simula import config, llm, runfolder
from simula.contracts import (ContentRating, Device, Edge, Element, ExploreFile,
                              ModelMeaning, ProductModel, Rect, State, StateFile)
from simula.runlog import needs_human, run_trace, write_exhibit
from simula.stages import Ctx

PREFIX = "Found these elements on screen: "
PROMPT = config.ROOT / "prompts" / "model" / "meaning.md"
MAX_IMAGES = 20
IMAGE_LONG_SIDE = 1568
MAX_TOKENS = 32000
SCOPE_CAP = 8
MONEY_KINDS = ("paywall", "limit", "currency")
ROLE_BY_CLASS = {"TextView": "text", "Button": "button", "ImageButton": "button", "ImageView": "image",
                 "EditText": "text input"}


# ---------- code facts ----------

def read_tree(reply_path: Path) -> list[dict]:
    if not reply_path.is_file():
        return []
    text = json.loads(reply_path.read_text())["content"][0]["text"]
    return json.loads(text.removeprefix(PREFIX))


def in_content(e: dict, device: Device) -> bool:
    c = e["coordinates"]
    full_screen = c["width"] >= device.w_px and c["height"] >= 2000
    system = "systemui" in (e.get("identifier") or "")
    return device.content_top_px <= c["y"] < device.content_bottom_px and not full_screen and not system


def to_dp(r: Rect, device: Device) -> Rect:
    s = device.scale
    return Rect(x=round(r.x / s, 2), y=round((r.y - device.content_top_px) / s, 2),
                w=round(r.w / s, 2), h=round(r.h / s, 2))


def hex_color(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def colors(pixels: np.ndarray) -> tuple[str | None, str | None]:
    """Background = the median pixel; foreground = the median of pixels far from it (text, icon strokes)."""
    if pixels.size == 0:
        return None, None
    flat = pixels.reshape(-1, 3)
    bg = np.median(flat, axis=0)
    far = flat[np.abs(flat.astype(int) - bg).sum(axis=1) > 90]
    return (hex_color(np.median(far, axis=0)) if len(far) > 20 else None), hex_color(bg)


def is_image_like(e: Element, device: Device) -> bool:
    r = e.rect_px
    no_words = not (e.text or e.label)
    small_enough = r.w * r.h < 0.4 * device.w_px * device.h_px
    return (e.type == "ImageView" or no_words) and min(r.w, r.h) >= 48 and small_enough


def build_elements(sid: str, tree: list[dict], pixels: np.ndarray, device: Device) -> list[Element]:
    elements = []
    for n, e in enumerate([e for e in tree if in_content(e, device)], start=1):
        c = e["coordinates"]
        rect = Rect(x=c["x"], y=c["y"], w=c["width"], h=c["height"])
        kind = e["type"].split(".")[-1]
        x0, y0 = max(int(rect.x), 0), max(int(rect.y), 0)
        fg, bg = colors(pixels[y0:int(rect.y + rect.h), x0:int(rect.x + rect.w)])
        elements.append(Element(
            id=f"{sid}.e{n:02d}", mcp_ref=e["ref"], type=kind, text=e.get("text") or "", label=e.get("label") or "",
            source="mcp", rect_px=rect, rect_dp=to_dp(rect, device), role=ROLE_BY_CLASS.get(kind, "container"),
            asset_png=None, fg_hex=fg, bg_hex=bg, font_px=rect.h if kind == "TextView" else None,
            font_guess="unknown", in_mock=False, repeat_group=None))
    return group_repeats(sid, elements)


def group_repeats(sid: str, elements: list[Element]) -> list[Element]:
    """Three or more same-class, same-size boxes (8 dp buckets) are one repeated list, like feed cards."""
    def key(e: Element):
        return e.type, round(e.rect_dp.w / 8), round(e.rect_dp.h / 8)
    counts: dict = {}
    for e in elements:
        if e.rect_dp.w >= 8 and e.rect_dp.h >= 8:
            counts[key(e)] = counts.get(key(e), 0) + 1
    groups = [k for k, n in counts.items() if n >= 3]
    return [e.model_copy(update={"repeat_group": f"{sid}.r{groups.index(key(e)) + 1}"}) if key(e) in groups else e
            for e in elements]


def load_states(explore_dir: Path, device: Device) -> tuple[list[State], dict[str, Image.Image]]:
    states, images = [], {}
    for path in sorted(p for p in (explore_dir / "states").glob("*.json") if "." not in p.stem):
        sf = StateFile.model_validate_json(path.read_text())
        image = Image.open(explore_dir / sf.screenshot).convert("RGB")
        tree = read_tree(explore_dir / sf.elements_reply) if sf.elements_reply else []
        images[sf.state_id] = image
        states.append(State(
            id=sf.state_id, kind=sf.kind, parent_id=sf.parent_id, name=sf.state_id, purpose="",
            fingerprint=sf.fingerprint, canonical_png=f"states/{sf.state_id}.png",
            elements=build_elements(sf.state_id, tree, np.asarray(image), device), in_mock_scope=False,
            content_rating="unknown", dynamic_regions=sf.dynamic_regions,
            blocked_reason="recorded as blocked by explore" if sf.kind == "blocked" else None))
    return states, images


def load_edges(explore_dir: Path, states: list[State]) -> tuple[list[Edge], list[str]]:
    """One edge per distinct recorded move that changed state. The transition is the one explore recorded."""
    known = {s.id for s in states}
    by_ref = {(s.id, e.mcp_ref): e.id for s in states for e in s.elements}
    edges, skipped = {}, []
    path = explore_dir / "actions.jsonl"
    for line in path.read_text().splitlines() if path.exists() else []:
        a = json.loads(line)
        start, end = a.get("from_state"), a.get("to_state")
        if a.get("outcome", "ok") != "ok" or not end or start == end:
            continue
        if {start, end} - known:
            skipped.append(f"action {start}>{end}: unknown state")
            continue
        element_id = by_ref.get((start, a.get("mcp_ref")))
        edge_id = f"{element_id or f'{start}.{a['action']}'}>{end}"
        edges.setdefault(edge_id, Edge(
            id=edge_id, from_state=start, to_state=end, element_id=element_id, action=a["action"],
            transition=a.get("transition") or "unknown", change_summary=a.get("change_summary") or ""))
    return list(edges.values()), skipped


def content_png(image: Image.Image, device: Device) -> Image.Image:
    return image.crop((0, device.content_top_px, device.w_px, device.content_bottom_px))


# ---------- the model call ----------

def describe(states: list[State], edges: list[Edge], app: dict) -> str:
    lines = [f"App package: {app['package']}", "", "STATES and their elements (rect in dp, content coordinates):"]
    for s in states:
        parent = f", over {s.parent_id}" if s.parent_id else ""
        lines.append(f"\n## {s.id} ({s.kind}{parent}), {len(s.elements)} elements")
        for e in s.elements:
            words = " ".join(f'{k}="{v}"' for k, v in (("text", e.text), ("label", e.label)) if v)
            r = e.rect_dp
            lines.append(f"{e.id} {e.type} {words} [{r.x:.0f},{r.y:.0f} {r.w:.0f}x{r.h:.0f}]".replace("  ", " "))
    lines += ["", "RECORDED EDGES (id: from -> to, action, transition, what changed):"]
    lines += [f"{e.id}: {e.from_state} -> {e.to_state}, {e.action}, {e.transition}, {e.change_summary or '-'}"
              for e in edges] or ["(none)"]
    return "\n".join(lines)


def png_bytes(image: Image.Image) -> bytes:
    scaled = image.copy()
    scaled.thumbnail((IMAGE_LONG_SIDE, IMAGE_LONG_SIDE), Image.LANCZOS)
    buf = BytesIO()
    scaled.save(buf, "PNG")
    return buf.getvalue()


def ask_meaning(ctx: Ctx, text: str, shots: list[tuple[str, bytes]], retry_note: tuple[str, str] | None):
    role = config.roles(ctx.profile)["model_meaning"]
    model = role["model"]
    trace_path = ctx.run_dir / "trace.jsonl"
    budget = llm.Budget.for_stage("model", trace_path, ctx.usd_cap)

    def attempt(kept: list[tuple[str, bytes]]) -> ModelMeaning:
        content = [{"type": "text", "text": text}]
        for sid, png in kept:
            content += [{"type": "text", "text": f"Screenshot of {sid}:"}, {"type": "image", "png": png}]
        messages = [{"role": "user", "content": content}]
        if retry_note:
            previous, problems = retry_note
            messages += [{"role": "assistant", "content": [{"type": "text", "text": previous}]},
                         {"role": "user", "content": [{"type": "text", "text": problems}]}]
        parsed, _ = llm.call(trace_path=trace_path, stage="model", step="retry" if retry_note else "meaning",
                             model=model, effort=role.get("effort"), system=PROMPT.read_text(), messages=messages,
                             max_tokens=min(MAX_TOKENS, config.models()[model]["max_out"]), budget=budget,
                             schema=ModelMeaning, no_cache=ctx.no_cache, replay=ctx.replay)
        return parsed

    meaning, refused = llm.without_refused_images(shots, attempt)
    if refused:
        run_trace(ctx.run_dir, stage="model", step="refused_images", decider="code",
                  note="sent without: " + ", ".join(sid for sid, _ in refused))
    return meaning


# ---------- the merge ----------

def check_meaning(meaning: ModelMeaning, states: list[State], edges: list[Edge]) -> tuple[ModelMeaning, list[str]]:
    """Drops every model-written item that cites something code didn't record. Returns the kept meaning and
    one line per rejection."""
    state_ids = {s.id for s in states}
    elements = {e.id: e for s in states for e in s.elements}
    edge_by_id = {e.id: e for e in edges}
    rejected = []

    def keep(items, ok, what, name=lambda item: item.id):
        kept = []
        for item in items:
            problem = ok(item)
            if problem:
                rejected.append(f"{what} {name(item)}: {problem}")
            else:
                kept.append(item)
        return kept

    def unknown(ids):
        missing = [i for i in ids if i not in state_ids and i not in elements]
        return f"unknown ids {missing}" if missing else None

    def flow_problem(f):
        missing = [i for i in f.edge_ids if i not in edge_by_id]
        if not f.edge_ids or missing:
            return f"missing edges {missing}" if missing else "no edges"
        hops = [edge_by_id[i] for i in f.edge_ids]
        for a, b in zip(hops, hops[1:]):
            if a.to_state != b.from_state:
                return f"hops do not connect: {a.id} ends at {a.to_state} but {b.id} starts at {b.from_state}"
        return unknown(f.evidence_ids)

    def mechanic_problem(m):
        if m.kind in MONEY_KINDS and not any(i in elements for i in m.evidence_ids):
            return f"a {m.kind} must cite an element"
        return unknown(m.evidence_ids)

    def ledger_problem(item):
        words = [elements[i].text + " " + elements[i].label for i in item.evidence_ids if i in elements]
        if not any(item.verbatim and item.verbatim in w for w in words):
            return f"{item.verbatim!r} is not verbatim in its evidence elements"
        return unknown(item.evidence_ids)

    cleaned = meaning.model_copy(update={
        "states": keep(meaning.states, lambda s: None if s.state_id in state_ids else "unknown state", "state",
                       lambda s: s.state_id),
        "elements": keep(meaning.elements, lambda e: None if e.element_id in elements else "unknown element",
                         "element", lambda e: e.element_id),
        "flows": keep(meaning.flows, flow_problem, "flow"),
        "mechanics": keep(meaning.mechanics, mechanic_problem, "mechanic"),
        "cross_screen_values": keep(meaning.cross_screen_values, lambda v: unknown(v.evidence_ids), "value"),
        "value_ledger": keep(meaning.value_ledger, ledger_problem, "ledger"),
    })
    return cleaned, rejected


def keyword_floor(state: State, keywords: list[str]) -> ContentRating:
    """The generic adult-keyword list can only raise a rating, never lower it."""
    words = " ".join(e.text + " " + e.label for e in state.elements)
    pattern = "|".join(rf"(?<!\w){re.escape(k)}(?!\w)" for k in keywords)
    return "unsafe" if pattern and re.search(pattern, words, re.IGNORECASE) else state.content_rating


def apply_meaning(states: list[State], meaning: ModelMeaning, keywords: list[str]) -> list[State]:
    by_state = {m.state_id: m for m in meaning.states}
    by_element = {m.element_id: m for m in meaning.elements}
    out = []
    for s in states:
        m = by_state.get(s.id)
        elements = [e.model_copy(update={"role": by_element[e.id].role, "font_guess": by_element[e.id].font_guess})
                    if e.id in by_element else e for e in s.elements]
        named = s.model_copy(update={"elements": elements, **({"name": m.name, "purpose": m.purpose,
                                                                "content_rating": m.content_rating} if m else {})})
        out.append(named.model_copy(update={"content_rating": keyword_floor(named, keywords)}))
    return out


# ---------- scope and assets ----------

def mock_scope(states: list[State], edges: list[Edge], meaning: ModelMeaning) -> list[str]:
    """Root, then tabs, then states with a mechanic, then depth-1 states; at most 8; never unsafe, blocked,
    or outside the app."""
    eligible = {s.id for s in states if s.content_rating != "unsafe" and s.kind not in ("blocked", "external")}
    screens = [s.id for s in states if s.kind == "screen"]
    root = screens[0] if screens else None
    tabs = [e.to_state for e in edges if e.transition == "tab"]
    mechanic_states = [i.split(".")[0] for m in meaning.mechanics for i in m.evidence_ids]
    depth1 = [e.to_state for e in edges if e.from_state == root]
    ordered = [root, *tabs, *mechanic_states, *depth1]
    return list(dict.fromkeys(s for s in ordered if s in eligible))[:SCOPE_CAP]


def finish_elements(state: State, scope: set[str], image: Image.Image, out: Path, device: Device) -> State:
    """Crops image assets and marks what the mock must draw: in-scope elements with words or art, and only
    the first two items of a repeated list."""
    in_scope = state.id in scope
    seen: dict[str, int] = {}
    elements = []
    for e in state.elements:
        asset = None
        if in_scope and is_image_like(e, device):
            asset = f"assets/{e.id}.png"
            r = e.rect_px
            image.crop((int(r.x), int(r.y), int(r.x + r.w), int(r.y + r.h))).save(out / asset)
        repeat_index = seen.get(e.repeat_group, 0) if e.repeat_group else 0
        if e.repeat_group:
            seen[e.repeat_group] = repeat_index + 1
        in_mock = in_scope and bool(e.text or e.label or asset) and repeat_index < 2
        elements.append(e.model_copy(update={"asset_png": asset, "in_mock": in_mock}))
    return state.model_copy(update={"elements": elements, "in_mock_scope": in_scope})


# ---------- markdown ----------

def mermaid_label(text: str) -> str:
    return re.sub(r'["\[\](){}|<>]', " ", text).strip() or "?"


def render_md(model: ProductModel) -> str:
    elements = {e.id: e for s in model.states for e in s.elements}
    edges = {e.id: e for e in model.edges}

    def edge_line(e: Edge) -> str:
        el = elements.get(e.element_id)
        what = mermaid_label((el.text or el.label or el.role) if el else e.action)
        return f"  {e.from_state} -->|{e.transition}: {what}| {e.to_state}"

    lines = [f"# Product model: {model.app} {model.app_version}", "",
             f"Category **{model.app_category}** · {len(model.states)} states · {len(model.edges)} edges · "
             f"{len(model.flows)} core flows · run `{model.run_id}` · source `{model.provenance.source}`", "",
             "## Navigation graph", "", "```mermaid", "flowchart TD"]
    lines += [f'  {s.id}["{s.id} {mermaid_label(s.name)}"]' for s in model.states]
    lines += [edge_line(e) for e in model.edges] + ["```", "", "## Core flows"]
    for f in model.flows:
        lines += ["", f"### {f.id} {f.name}", "", f.purpose, "", "```mermaid", "flowchart LR"]
        lines += [edge_line(edges[i]) for i in f.edge_ids] + ["```"]
    lines += ["", "## States", "", "| id | kind | name | rating | mock | elements | purpose |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {s.id} | {s.kind} | {s.name} | {s.content_rating} | {'yes' if s.in_mock_scope else ''} | "
              f"{len(s.elements)} | {s.purpose} |" for s in model.states]
    lines += ["", "## Mechanics", ""]
    lines += [f"- **{m.kind}** ({m.status}) {m.summary} · evidence {', '.join(m.evidence_ids)}"
              + (f" · numbers {', '.join(m.observed_numbers)}" if m.observed_numbers else "") for m in model.mechanics]
    lines += ["", "## Value ledger", ""]
    lines += [f"- {i.kind}: \"{i.verbatim}\" · {', '.join(i.evidence_ids)}" for i in model.value_ledger] or ["- none"]
    lines += ["", "## Values shared across screens", ""]
    lines += [f"- {v.label}: {v.value_text} · {', '.join(v.evidence_ids)}" for v in model.cross_screen_values] or ["- none"]
    lines += ["", "## Open questions", ""] + [f"- {q}" for q in model.open_questions]
    return "\n".join(lines) + "\n"


def exhibit(model: ProductModel, rejected: list[str], skipped: list[str]) -> str:
    scope = [s.id for s in model.states if s.in_mock_scope]
    lines = ["# 02 · model", "",
             f"- {len(model.states)} states, {sum(len(s.elements) for s in model.states)} elements, "
             f"{len(model.edges)} edges (all from explore, code-owned)",
             f"- app category: {model.app_category}; {len(model.flows)} core flows; {len(model.mechanics)} mechanics; "
             f"{len(model.value_ledger)} verbatim ledger items",
             f"- mock scope (code): {', '.join(scope) or 'none'}",
             "- ratings: " + ", ".join(f"{s.id} {s.content_rating}" for s in model.states),
             "", "## Dropped by the merge check", ""]
    lines += [f"- {r}" for r in rejected + skipped] or ["- nothing"]
    return "\n".join(lines) + "\n\nFull model: `model/product_model.md`.\n"


# ---------- the stage ----------

def run(ctx: Ctx) -> None:
    explore_dir, out = ctx.run_dir / "explore", ctx.run_dir / "model"
    explore = ExploreFile.model_validate_json((explore_dir / "explore.json").read_text())
    device = Device()
    for sub in ("states", "assets"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir(parents=True)

    states, images = load_states(explore_dir, device)
    edges, skipped = load_edges(explore_dir, states)
    for s in states:
        content_png(images[s.id], device).save(out / s.canonical_png)
    run_trace(ctx.run_dir, stage="model", step="facts", decider="code",
              note=f"{len(states)} states, {sum(len(s.elements) for s in states)} elements, {len(edges)} edges")

    text = describe(states, edges, ctx.app)
    shots = [(s.id, png_bytes(content_png(images[s.id], device)))
             for s in states if s.kind != "external"][:MAX_IMAGES]
    try:
        first = ask_meaning(ctx, text, shots, None)
        meaning, rejected = check_meaning(first, states, edges)
        if rejected:
            problems = ("These items were rejected by the merge check:\n" + "\n".join(f"- {r}" for r in rejected)
                        + "\nReturn the whole answer again with them fixed or removed. Cite only the given ids.")
            meaning, rejected = check_meaning(ask_meaning(ctx, text, shots, (first.model_dump_json(), problems)),
                                              states, edges)
    except llm.LLMFailure as e:
        needs_human(ctx.run_dir, "model", "the meaning call failed twice", str(e), ["trace.jsonl"],
                    f"simula model {ctx.app['name']} --run {ctx.run_dir.name}")
        raise
    run_trace(ctx.run_dir, stage="model", step="merge", decider="code",
              note=f"{len(rejected)} dropped" + (": " + "; ".join(rejected)[:250] if rejected else ""))

    states = apply_meaning(states, meaning, config.profiles()["content"]["adult_keywords"])
    scope = set(mock_scope(states, edges, meaning))
    states = [finish_elements(s, scope, images[s.id], out, device) for s in states]
    run_trace(ctx.run_dir, stage="model", step="scope", decider="code", note=", ".join(sorted(scope)))

    model = ProductModel(
        app=ctx.app["name"], app_version=explore.app_version or "unknown", app_category=meaning.app_category,
        run_id=ctx.run_dir.name, device=device, states=states, edges=edges, flows=meaning.flows,
        mechanics=meaning.mechanics, cross_screen_values=meaning.cross_screen_values,
        value_ledger=meaning.value_ledger, open_questions=meaning.open_questions, coverage=explore.coverage,
        provenance=runfolder.upstream_provenance(ctx.run_dir, ["explore"]))
    (out / "product_model.json").write_text(model.model_dump_json(indent=1))
    (out / "product_model.md").write_text(render_md(model))
    write_exhibit(ctx.run_dir, 2, "model", exhibit(model, rejected, skipped))
