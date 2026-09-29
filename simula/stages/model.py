"""Goal 1: the product model. Code writes every fact, one model call writes meaning keyed by those ids,
and code checks the merge. Reads explore/ only; writes model/ only."""

import json
import re
import shutil
import statistics
from collections import Counter
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from simula import config, llm, runfolder, text
from simula.contracts import (ActionLine, ContentRating, Device, Edge, Element, ExploreFile, IconLabel, LedgerItem,
                              ModelMeaning, OpenQuestion, Point, ProductModel, Rect, State, StateFile, Term,
                              VisionElement)
from simula.runlog import needs_human, run_trace, write_exhibit
from simula.stages import Ctx

PREFIX = "Found these elements on screen: "
PROMPT = config.ROOT / "prompts" / "model" / "meaning.md"
MAX_IMAGES = 20
IMAGE_LONG_SIDE = 1568
MAX_TOKENS = 64000
ANSWER_RESERVE_TOKENS = 8000
TOKENS_PER_NAME = 30
QUESTION_CAP = 5
NOT_OBSERVED = "meaning not observed"
LOOP_UNITS = ("s", "chars")
MEASURE = re.compile(r"^(?P<what>.*?)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[^\d\s]*)$")
MONEY_KINDS = ("paywall", "limit", "currency")
SCOPE_KINDS = (*MONEY_KINDS, "ad")
EDGE_ACTIONS = ("tap", "swipe", "back", "type")
ROLE_BY_CLASS = {"TextView": "text", "Button": "button", "ImageButton": "button", "ImageView": "image",
                 "EditText": "text input"}


# ---------- code facts ----------

def read_tree(reply_path: Path) -> list[dict]:
    text = json.loads(reply_path.read_text())["content"][0]["text"]
    return json.loads(text.removeprefix(PREFIX))


def in_content(e: dict, device: Device) -> bool:
    c = e["coordinates"]
    full_screen = c["width"] >= device.w_px and c["height"] >= 2000
    system = "systemui" in (e.get("identifier") or "")
    on_screen = device.content_top_px <= c["y"] < device.content_bottom_px and 0 <= c["x"] < device.w_px
    return on_screen and not full_screen and not system


def inside(inner: Rect, outer: Rect) -> bool:
    return (outer.x <= inner.x and outer.y <= inner.y
            and inner.x + inner.w <= outer.x + outer.w and inner.y + inner.h <= outer.y + outer.h)


def contains(r: Rect, p: Point) -> bool:
    return r.x <= p.x < r.x + r.w and r.y <= p.y < r.y + r.h


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


def is_image_like(e: Element, siblings: list[Element], device: Device) -> bool:
    """Art worth cropping: an image (text over it is an overlay, drawn separately), or a wordless box that
    holds no text (a crop would bake that text in)."""
    r = e.rect_px
    holds_text = any((s.text or s.label) and s is not e and inside(s.rect_px, r) for s in siblings)
    small_enough = r.w * r.h < 0.4 * device.w_px * device.h_px
    art = e.type == "ImageView" or not (e.text or e.label or holds_text)
    return art and min(r.w, r.h) >= 48 and small_enough


def make_element(eid: str, rect: Rect, kind: str, text: str, label: str, mcp_ref: str | None,
                 pixels: np.ndarray, device: Device) -> Element:
    x0, y0 = max(int(rect.x), 0), max(int(rect.y), 0)
    fg, bg = colors(pixels[y0:int(rect.y + rect.h), x0:int(rect.x + rect.w)])
    return Element(
        id=eid, mcp_ref=mcp_ref, type=kind, text=text, label=label, source="mcp" if mcp_ref else "vision",
        rect_px=rect, rect_dp=to_dp(rect, device), role=ROLE_BY_CLASS.get(kind, "container"), asset_png=None,
        fg_hex=fg, bg_hex=bg, font_px=rect.h if kind == "TextView" else None, font_guess="unknown",
        in_mock=False, repeat_group=None)


def build_elements(sid: str, tree: list[dict], icon_labels: list[IconLabel], vision: list[VisionElement],
                   pixels: np.ndarray, device: Device) -> list[Element]:
    """Listed elements first, in tree order, then the controls only the vision pass saw."""
    names = {i.mcp_ref: i.name for i in icon_labels}
    elements = []
    for e in (e for e in tree if in_content(e, device)):
        c = e["coordinates"]
        elements.append(make_element(
            f"{sid}.e{len(elements) + 1:02d}", Rect(x=c["x"], y=c["y"], w=c["width"], h=c["height"]),
            e["type"].split(".")[-1], e.get("text") or "", names.get(e["ref"]) or e.get("label") or "", e["ref"],
            pixels, device))
    for v in vision:
        elements.append(make_element(f"{sid}.e{len(elements) + 1:02d}", v.rect_px, "vision", "", v.name, None,
                                     pixels, device))
    return elements


def group_repeats(state: State, tapped: set[str]) -> State:
    """Three or more same-class, same-size boxes (8 dp buckets) are one repeated list, like feed cards.
    A box the explorer tapped is its own control, never a list item."""
    def key(e: Element):
        return e.type, round(e.rect_dp.w / 8), round(e.rect_dp.h / 8)
    members = [e for e in state.elements if e.id not in tapped and e.rect_dp.w >= 8 and e.rect_dp.h >= 8]
    counts = Counter(key(e) for e in members)
    groups = [k for k, n in counts.items() if n >= 3]
    member_ids = {e.id for e in members}
    elements = [e.model_copy(update={"repeat_group": f"{state.id}.r{groups.index(key(e)) + 1}"})
                if e.id in member_ids and key(e) in groups else e for e in state.elements]
    return state.model_copy(update={"elements": elements})


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
            elements=build_elements(sf.state_id, tree, sf.icon_labels, sf.vision_elements, np.asarray(image), device),
            in_mock_scope=False, content_rating="unknown", dynamic_regions=sf.dynamic_regions,
            blocked_reason=sf.blocked_reason))
    return states, images


def tapped_element(state: State, line: ActionLine) -> Element | None:
    """The contract's rule: the mcp_ref element if its box holds the tap, else the smallest box that does."""
    by_ref = next((e for e in state.elements if line.mcp_ref and e.mcp_ref == line.mcp_ref), None)
    if line.tap_px is None or (by_ref and contains(by_ref.rect_px, line.tap_px)):
        return by_ref
    holding = [e for e in state.elements if contains(e.rect_px, line.tap_px)]
    return min(holding, key=lambda e: e.rect_px.w * e.rect_px.h) if holding else None


def edge_for(state: State, line: ActionLine) -> tuple[Element | None, str]:
    element = tapped_element(state, line)
    return element, f"{element.id if element else f'{line.from_state}.{line.action}'}>{line.to_state}"


def read_actions(explore_dir: Path) -> list[ActionLine]:
    path = explore_dir / "actions.jsonl"
    return [ActionLine.model_validate_json(raw) for raw in path.read_text().splitlines()] if path.exists() else []


def load_edges(explore_dir: Path, states: list[State]) -> tuple[list[Edge], list[str]]:
    """One edge per distinct recorded move that reached a state (or changed something in place). The
    transition is the one explore recorded. Returns the edges and a note for every line not taken as given."""
    by_id = {s.id: s for s in states}
    edges, notes = {}, []
    for a in read_actions(explore_dir):
        if a.outcome != "ok" or a.to_state is None or a.action not in EDGE_ACTIONS:
            continue
        if a.from_state == a.to_state and not a.change_summary:
            continue
        if {a.from_state, a.to_state} - by_id.keys():
            notes.append(f"step {a.step}: unknown state in {a.from_state}>{a.to_state}")
            continue
        element, edge_id = edge_for(by_id[a.from_state], a)
        if a.mcp_ref and (element is None or element.mcp_ref != a.mcp_ref):
            where = f"the tap at {a.tap_px.x},{a.tap_px.y}" if a.tap_px else "the tap"
            notes.append(f"step {a.step}: {a.mcp_ref} does not hold {where}; "
                         f"bound to {element.id if element else 'no element'}")
        edges.setdefault(edge_id, Edge(
            id=edge_id, from_state=a.from_state, to_state=a.to_state, element_id=element.id if element else None,
            action=a.action, transition=a.transition, change_summary=a.change_summary))
    return list(edges.values()), notes


def content_png(image: Image.Image, device: Device) -> Image.Image:
    return image.crop((0, device.content_top_px, device.w_px, device.content_bottom_px))


def measurements(summary: str) -> list[tuple[str, float, str]]:
    """'reply started 2.1 s, finished 9.4 s, 612 chars' -> [('reply started', 2.1, 's'), ('finished', 9.4, 's'),
    ('chars', 612.0, 'chars')]. Parts without a number are skipped."""
    found = []
    for part in summary.split(","):
        m = MEASURE.match(part.strip())
        if m:
            found.append((m["what"] or m["unit"], float(m["value"]), m["unit"]))
    return found


def pass_measurements(lines: list[ActionLine]) -> list[tuple[str, float, str]]:
    """One pass's measurements: the timing parts (units s or chars) of the last line of the pass that has any.
    A pass writes several lines (tap the box, type, send; or open, back) and only one carries the timing."""
    for a in reversed(lines):
        found = [m for m in measurements(a.change_summary) if m[2] in LOOP_UNITS]
        if found:
            return found
    return []


def loop_facts(explore_dir: Path, states: list[State], edges: list[Edge]) -> list[LedgerItem]:
    """The measured free experience, from the explorer's core-loop passes: one item with each measurement's
    median, min, max and n, and one saying what stopped the loop, or that nothing did. Passes are counted by
    distinct loop_pass, not by line. A stop counts from any line, even a denied one; measurements only from
    lines that ran."""
    loop = [a for a in read_actions(explore_dir) if a.loop_pass is not None]
    if not loop:
        return []
    passes = [a for a in loop if a.outcome == "ok"]
    by_pass: dict[int, list[ActionLine]] = {}
    for a in passes:
        by_pass.setdefault(a.loop_pass, []).append(a)
    by_state, known = {s.id: s for s in states}, {e.id for e in edges}
    # A pass that recorded no edge still ran on its state; the step range in the text points at its action line.
    evidence = sorted({edge_for(by_state[a.from_state], a)[1] for a in passes if a.from_state in by_state} & known) \
        or sorted({a.from_state for a in loop} & by_state.keys())
    steps = f"explore steps {loop[0].step}-{loop[-1].step}"
    values: dict[tuple[str, str], list[float]] = {}
    for lines in by_pass.values():
        for what, value, unit in pass_measurements(lines):
            values.setdefault((what, unit), []).append(value)
    items = []
    if values:
        parts = [f"{what} median {statistics.median(v):g}{'' if unit == what else ' ' + unit} "
                 f"(min {min(v):g}, max {max(v):g}, n={len(v)})" for (what, unit), v in values.items()]
        items.append(LedgerItem(id="exp1", kind="experience", evidence_ids=evidence,
                                verbatim=f"Core action over {len(by_pass)} passes ({steps}): " + "; ".join(parts)))
    stop = next((a for a in loop if a.loop_stop), None)
    outcome = (f"{stop.loop_stop} appeared on pass {stop.loop_pass} of the core action" if stop else
               f"After {len(by_pass)} passes of the core action nothing limited it: no limit, paywall, or ad appeared")
    items.append(LedgerItem(id=f"exp{len(items) + 1}", kind="experience", evidence_ids=evidence,
                            verbatim=f"{outcome} ({steps})"))
    return items


# ---------- the model call ----------

def element_line(e: Element) -> str:
    words = " ".join(f'{k}="{v}"' for k, v in (("text", e.text), ("label", e.label)) if v)
    r = e.rect_dp
    return " ".join(p for p in (e.id, e.type, words, f"[{r.x:.0f},{r.y:.0f} {r.w:.0f}x{r.h:.0f}]") if p)


def describe(states: list[State], edges: list[Edge], app: dict, device: Device, name_limit: int,
             experience: list[LedgerItem] = ()) -> str:
    """Lists only what the mock could draw (words, art, or a tapped control). Items of a repeated list after
    the second fold into one line, so a long feed stays short. Past the naming budget only tapped controls
    are still listed."""
    tapped = {e.element_id for e in edges}
    listed = 0
    lines = [f"App package: {app['package']}", "", "STATES and their drawable elements (rect in dp, content coordinates):"]
    for s in states:
        parent = f", over {s.parent_id}" if s.parent_id else ""
        lines.append(f"\n## {s.id} ({s.kind}{parent})")
        seen, folded, omitted = Counter(), {}, 0
        for e in s.elements:
            if not (e.text or e.label or e.id in tapped or is_image_like(e, s.elements, device)):
                continue
            if e.repeat_group and seen[e.repeat_group] >= 2:
                folded.setdefault(e.repeat_group, []).append(e)
                continue
            seen[e.repeat_group] += 1
            if listed >= name_limit and e.id not in tapped:
                omitted += 1
                continue
            listed += 1
            lines.append(element_line(e))
        for group, items in folded.items():
            texts = ", ".join(f'{i.id} "{(i.text or i.label)[:40]}"' if i.text or i.label else i.id for i in items)
            lines.append(f"{group}: {len(items)} more items like the two above: {texts}")
        if omitted:
            lines.append(f"({omitted} more elements not listed: over the naming budget)")
    lines += ["", "RECORDED EDGES (id: from -> to, action, transition, what changed):"]
    lines += [f"{e.id}: {e.from_state} -> {e.to_state}, {e.action}, {e.transition}, {e.change_summary or '-'}"
              for e in edges] or ["(none)"]
    lines += ["", "MEASURED BY CODE while the explorer repeated the app's core action (already in the ledger; "
                  "don't restate them as ledger items):"]
    lines += [f"{i.id}: {i.verbatim} [evidence {', '.join(i.evidence_ids)}]" for i in experience] or \
             ["(the core action was not repeated in this run)"]
    return "\n".join(lines)


def png_bytes(image: Image.Image) -> bytes:
    scaled = image.copy()
    scaled.thumbnail((IMAGE_LONG_SIDE, IMAGE_LONG_SIDE), Image.LANCZOS)
    buf = BytesIO()
    scaled.save(buf, "PNG")
    return buf.getvalue()


def max_tokens(profile: str) -> int:
    model = config.roles(profile)["model_meaning"]["model"]
    return min(MAX_TOKENS, config.models()[model]["max_out"])


def ask_meaning(ctx: Ctx, dump: str, shots: list[tuple[str, bytes]],
                retry_note: tuple[str, str] | None) -> tuple[ModelMeaning, list[tuple[str, bytes]]]:
    """Returns the answer and the screenshots it was given (a refused one is left out)."""
    role = config.roles(ctx.profile)["model_meaning"]
    trace_path = ctx.run_dir / "trace.jsonl"
    budget = llm.Budget.for_stage("model", trace_path, ctx.usd_cap)

    def attempt(kept: list[tuple[str, bytes]]) -> ModelMeaning:
        content = [{"type": "text", "text": dump}]
        for sid, png in kept:
            content += [{"type": "text", "text": f"Screenshot of {sid}:"}, {"type": "image", "png": png}]
        messages = [{"role": "user", "content": content}]
        if retry_note:
            previous, problems = retry_note
            messages += [{"role": "assistant", "content": [{"type": "text", "text": previous}]},
                         {"role": "user", "content": [{"type": "text", "text": problems}]}]
        parsed, _ = llm.call(trace_path=trace_path, stage="model", step="retry" if retry_note else "meaning",
                             model=role["model"], effort=role.get("effort"), system=PROMPT.read_text(),
                             messages=messages, max_tokens=max_tokens(ctx.profile), budget=budget,
                             schema=ModelMeaning, no_cache=ctx.no_cache, replay=ctx.replay)
        return parsed

    meaning, refused = llm.without_refused_images(shots, attempt)
    if refused:
        run_trace(ctx.run_dir, stage="model", step="refused_images", decider="code",
                  note="sent without: " + ", ".join(sid for sid, _ in refused))
    return meaning, [s for s in shots if not any(s is r for r in refused)]


# ---------- the merge ----------

def check_meaning(meaning: ModelMeaning, states: list[State], edges: list[Edge]) -> tuple[ModelMeaning, list[str]]:
    """Drops every model-written item that cites something code didn't record, and every observed number its
    evidence doesn't show; kept quotes and numbers take the evidence's exact text. Returns the kept meaning
    and one line per rejection or gap."""
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
        missing = [i for i in ids if i not in state_ids and i not in elements and i not in edge_by_id]
        return f"unknown ids {missing}" if missing else None

    def exact_text(evidence_ids, words: str, spaced: bool = True) -> str | None:
        """The evidence's own text for a model's quote or number, or None. Text and label are searched
        separately."""
        for field in (f for i in evidence_ids if i in elements for f in (elements[i].text, elements[i].label)):
            found = text.find(words, field, spaced)
            if found:
                return found
        return None

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
        if item.kind == "experience":
            return "experience items are measured by code, not written by the model"
        if exact_text(item.evidence_ids, item.verbatim) is None:
            return f"{item.verbatim!r} is not verbatim in the text or label of its evidence elements"
        return unknown(item.evidence_ids)

    def shown_numbers(m):
        found = [(n, exact_text(m.evidence_ids, n, spaced=False)) for n in m.observed_numbers]
        rejected.extend(f"mechanic {m.id}: number {n!r} is not shown in its evidence elements"
                        for n, exact in found if exact is None)
        return m.model_copy(update={"observed_numbers": [exact for _, exact in found if exact]})

    def question_problem(q):
        return None if q.start_state in state_ids else f"start_state {q.start_state!r} is not a recorded state"

    cleaned = meaning.model_copy(update={
        "states": keep(meaning.states, lambda s: None if s.state_id in state_ids else "unknown state", "state",
                       lambda s: s.state_id),
        "elements": keep(meaning.elements, lambda e: None if e.element_id in elements else "unknown element",
                         "element", lambda e: e.element_id),
        "flows": keep(meaning.flows, flow_problem, "flow"),
        "mechanics": [shown_numbers(m) for m in keep(meaning.mechanics, mechanic_problem, "mechanic")],
        "cross_screen_values": keep(meaning.cross_screen_values, lambda v: unknown(v.evidence_ids), "value"),
        "value_ledger": [i.model_copy(update={"verbatim": exact_text(i.evidence_ids, i.verbatim)})
                         for i in keep(meaning.value_ledger, ledger_problem, "ledger")],
        "open_questions": keep(meaning.open_questions, question_problem, "question")[:QUESTION_CAP],
    })
    uses = {m.id: m.summary for m in cleaned.mechanics} | {i.id: i.verbatim for i in cleaned.value_ledger}

    def term_problem(t):
        if not any(i in uses and text.find(t.term, uses[i], ignore_case=True) for i in t.used_in):
            return f"used_in {t.used_in} names no kept mechanic or ledger line that uses it"
        return None
    cleaned = cleaned.model_copy(update={"terms": keep(meaning.terms, term_problem, "term", lambda t: repr(t.term))})
    named = {s.state_id for s in cleaned.states}
    rejected += [f"state {s.id}: no meaning" for s in states if s.id not in named]
    if edges and not cleaned.flows:
        rejected.append("no core flow survived")
    return cleaned, rejected


def keyword_floor(state: State, keywords: list[str]) -> ContentRating:
    """The generic adult-keyword list can only raise a rating, never lower it."""
    words = " ".join(e.text + " " + e.label for e in state.elements)
    pattern = "|".join(rf"(?<!\w){re.escape(k)}(?!\w)" for k in keywords)
    return "unsafe" if pattern and re.search(pattern, words, re.IGNORECASE) else state.content_rating


def resolve_terms(meaning: ModelMeaning, states: list[State]) -> list[Term]:
    """A term keeps its meaning only when a cited element's own text carries it, and that element is not the
    evidence of a line that uses the term (a bullet can't define itself); otherwise it is marked 'meaning not
    observed' and nothing downstream may build on it."""
    elements = {e.id: e for s in states for e in s.elements}
    evidence = {m.id: m.evidence_ids for m in meaning.mechanics} | {i.id: i.evidence_ids for i in meaning.value_ledger}

    def carries(eid: str, term: str) -> bool:
        e = elements.get(eid)
        return bool(e) and any(text.find(term, f, ignore_case=True) for f in (e.text, e.label))

    terms = []
    for t in meaning.terms:
        using = {e for i in t.used_in for e in evidence.get(i, [])}
        defined_by = [i for i in t.defined_by if i not in using and carries(i, t.term)]
        terms.append(Term(term=t.term, meaning=t.meaning if defined_by else NOT_OBSERVED, defined_by=defined_by,
                          used_in=t.used_in, observed=bool(defined_by)))
    return terms


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


def code_roles(states: list[State], edges: list[Edge]) -> list[State]:
    """What code already knows: a tab edge starts at a tab, any other tapped box is at least a button, and
    text the model didn't name takes the app's most common named font."""
    tabs = {e.element_id for e in edges if e.transition == "tab"}
    tapped = {e.element_id for e in edges}
    fonts = Counter(e.font_guess for s in states for e in s.elements if e.font_guess != "unknown")
    font = fonts.most_common(1)[0][0] if fonts else "unknown"

    def fix(e: Element) -> Element:
        role = "tab" if e.id in tabs else "button" if e.id in tapped and e.role == "container" else e.role
        guess = font if e.text and e.font_guess == "unknown" else e.font_guess
        return e.model_copy(update={"role": role, "font_guess": guess})
    return [s.model_copy(update={"elements": [fix(e) for e in s.elements]}) for s in states]


# ---------- scope and assets ----------

def mock_scope(states: list[State], edges: list[Edge], meaning: ModelMeaning) -> list[str]:
    """The experience the mock draws, in priority order: the root, every state showing a paywall, limit,
    currency, or ad, then every state showing any other mechanic (a modal or sheet brings its parent first),
    then every state on the core flows in flow order. No cap, and nothing else: a tab or depth-1 screen is in
    only when it is on a flow or holds a mechanic. Never blocked or outside the app. An unsafe screen stays
    out (it doesn't belong in a pitch) unless it is on a core flow, where a gap would break the flow. The
    product model keeps every state. Every mechanic's screen stays in, because propose drops an idea whose
    trigger screen isn't mocked."""
    by_id = {s.id: s for s in states}
    edge_by_id = {e.id: e for e in edges}
    root = next((s.id for s in states if s.kind == "screen"), None)
    flow_states = [sid for f in meaning.flows for i in f.edge_ids if i in edge_by_id
                   for sid in (edge_by_id[i].from_state, edge_by_id[i].to_state)]
    eligible = {s.id for s in states if s.kind not in ("blocked", "external", "rotated")
                and (s.content_rating != "unsafe" or s.id in flow_states)}
    first = sorted(meaning.mechanics, key=lambda m: m.kind not in SCOPE_KINDS)
    mechanic_states = [i.split(".")[0] for m in first for i in m.evidence_ids]
    ordered = []
    for sid in [root, *mechanic_states, *flow_states]:
        parent = by_id[sid].parent_id if sid in by_id else None
        if parent and parent not in eligible:
            continue
        ordered += [parent, sid] if parent else [sid]
    return list(dict.fromkeys(s for s in ordered if s in eligible))


def finish_elements(state: State, scope: set[str], tapped: set[str], image: Image.Image, out: Path,
                    device: Device) -> State:
    """Crops image assets and marks what the mock draws: every in-scope element with words or art, and every
    element that starts an edge. Tagging only two items of a repeated list is the mock's job."""
    in_scope = state.id in scope
    elements = []
    for e in state.elements:
        asset = None
        if in_scope and is_image_like(e, state.elements, device):
            asset = f"assets/{e.id}.png"
            r = e.rect_px
            image.crop((int(r.x), int(r.y), int(r.x + r.w), int(r.y + r.h))).save(out / asset)
        in_mock = in_scope and (e.id in tapped or bool(e.text or e.label or asset))
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
    lines += ["", "## App terms", ""]
    lines += [f"- **{t.term}**: {t.meaning}" + (f" · defined by {', '.join(t.defined_by)}" if t.observed else "")
              + f" · used in {', '.join(t.used_in)}" for t in model.terms] or ["- none"]
    lines += ["", "## Values shared across screens", ""]
    lines += [f"- {v.label}: {v.value_text} · {', '.join(v.evidence_ids)}" for v in model.cross_screen_values] or ["- none"]
    lines += ["", "## Open questions (for a targeted explore pass)", ""]
    lines += [f"- {q.id} {q.question} · start at {q.start_state}, look for: {q.look_for}" for q in model.questions] \
        or ["- none"]
    return "\n".join(lines) + "\n"


def exhibit(model: ProductModel, rounds: list[list[str]], notes: list[str]) -> str:
    lines = ["# 02 · model", "",
             f"- {len(model.states)} states, {sum(len(s.elements) for s in model.states)} elements, "
             f"{len(model.edges)} edges (all from explore, code-owned)",
             f"- app category: {model.app_category}; {len(model.flows)} core flows; {len(model.mechanics)} mechanics; "
             f"{len(model.value_ledger)} verbatim ledger items",
             "- measured experience: " + (" · ".join(i.verbatim for i in model.value_ledger if i.kind == "experience")
                                           or "the core action was not repeated"),
             f"- app terms: {len(model.terms)}, meaning not observed for: "
             + (", ".join(t.term for t in model.terms if not t.observed) or "none"),
             f"- open questions for the explorer: {len(model.questions)}",
             f"- mock scope (code, priority order): {', '.join(model.mock_order) or 'none'}",
             "- ratings: " + ", ".join(f"{s.id} {s.content_rating}" for s in model.states)]
    for n, rejected in enumerate(rounds, start=1):
        title = "first answer" if n == 1 else "retry"
        lines += ["", f"## Merge check, round {n} ({title})", ""] + ([f"- {r}" for r in rejected] or ["- nothing dropped"])
    if len(rounds) == 1 and rounds[0]:
        lines += ["", "The retry failed, so the checked first answer was kept (see trace.jsonl)."]
    lines += ["", "## Explore lines not taken as given", ""] + ([f"- {n}" for n in notes] or ["- none"])
    return "\n".join(lines) + "\n\nFull model: `model/product_model.md`.\n"


# ---------- the stage ----------

def understand(ctx: Ctx, dump: str, shots: list[tuple[str, bytes]], states: list[State],
               edges: list[Edge]) -> tuple[ModelMeaning, list[list[str]]]:
    """One call, the merge check, and at most one retry with the rejection list. A failed retry keeps the
    checked first answer. Returns the meaning and each round's rejections."""
    try:
        first, kept = ask_meaning(ctx, dump, shots, None)
    except llm.LLMFailure as e:
        if e.raw:
            (ctx.run_dir / "model" / "raw_reply.txt").write_text(e.raw)
        needs_human(ctx.run_dir, "model", "the meaning call failed twice", str(e),
                    ["trace.jsonl", "model/raw_reply.txt"], f"simula model {ctx.app['name']} --run {ctx.run_dir.name}")
        raise
    meaning, rejected = check_meaning(first, states, edges)
    rounds = [rejected]
    log_round(ctx, 1, rejected)
    if not rejected:
        return meaning, rounds
    problems = ("The merge check rejected these items or found these gaps:\n" + "\n".join(f"- {r}" for r in rejected)
                + "\nReturn the whole answer again with them fixed or removed. Cite only the given ids.")
    try:
        second, _ = ask_meaning(ctx, dump, kept, (first.model_dump_json(), problems))
    except llm.LLMFailure as e:
        if e.raw:
            (ctx.run_dir / "model" / "raw_reply.txt").write_text(e.raw)
        run_trace(ctx.run_dir, stage="model", step="retry", decider="code", outcome="retry",
                  note=f"retry failed ({e.outcome}); kept the checked first answer")
        gaps = gaps_in(meaning, states, edges)
        if gaps:
            needs_human(ctx.run_dir, "model", "the product model has gaps", "; ".join(gaps) + ". The retry failed "
                        f"({e.outcome}); the run continues on the checked first answer.",
                        ["exhibits/02-model.md", "model/raw_reply.txt"],
                        f"simula model {ctx.app['name']} --run {ctx.run_dir.name}")
        return meaning, rounds
    meaning, rejected = check_meaning(second, states, edges)
    rounds.append(rejected)
    log_round(ctx, 2, rejected)
    return meaning, rounds


def gaps_in(meaning: ModelMeaning, states: list[State], edges: list[Edge]) -> list[str]:
    named = {s.state_id for s in meaning.states}
    gaps = [f"state {s.id} has no name or purpose" for s in states if s.id not in named]
    return gaps + (["no core flow survived"] if edges and not meaning.flows else [])


def log_round(ctx: Ctx, n: int, rejected: list[str]) -> None:
    run_trace(ctx.run_dir, stage="model", step=f"merge_round{n}", decider="code",
              note=f"{len(rejected)} rejected" + (": " + "; ".join(rejected)[:250] if rejected else ""))


def run(ctx: Ctx) -> None:
    explore_dir, out = ctx.run_dir / "explore", ctx.run_dir / "model"
    explore = ExploreFile.model_validate_json((explore_dir / "explore.json").read_text())
    device = explore.device
    for sub in ("states", "assets"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir(parents=True)

    states, images = load_states(explore_dir, device)
    edges, notes = load_edges(explore_dir, states)
    tapped = {e.element_id for e in edges if e.element_id}
    states = [group_repeats(s, tapped) for s in states]
    experience = loop_facts(explore_dir, states, edges)
    for s in states:
        content_png(images[s.id], device).save(out / s.canonical_png)
    run_trace(ctx.run_dir, stage="model", step="facts", decider="code",
              note=f"{len(states)} states, {sum(len(s.elements) for s in states)} elements, {len(edges)} edges"
                   + (f"; {len(notes)} explore lines not taken as given" if notes else ""))

    # ponytail: past the naming budget, later states' elements go unnamed; split the call if a run ever gets there
    name_limit = (max_tokens(ctx.profile) - ANSWER_RESERVE_TOKENS) // TOKENS_PER_NAME
    dump = describe(states, edges, ctx.app, device, name_limit, experience)
    shots = [(s.id, png_bytes(content_png(images[s.id], device)))
             for s in states if s.kind not in ("external", "rotated")][:MAX_IMAGES]
    meaning, rounds = understand(ctx, dump, shots, states, edges)

    states = apply_meaning(states, meaning, config.profiles()["content"]["adult_keywords"])
    states = code_roles(states, edges)
    mock_order = mock_scope(states, edges, meaning)
    scope = set(mock_order)
    states = [finish_elements(s, scope, tapped, images[s.id], out, device) for s in states]
    run_trace(ctx.run_dir, stage="model", step="scope", decider="code", note=", ".join(mock_order))

    model = ProductModel(
        app=ctx.app["name"], app_version=explore.app_version or "unknown", app_category=meaning.app_category,
        run_id=ctx.run_dir.name, device=device, states=states, edges=edges, flows=meaning.flows,
        mechanics=meaning.mechanics, cross_screen_values=meaning.cross_screen_values,
        value_ledger=meaning.value_ledger + experience, open_questions=[q.question for q in meaning.open_questions],
        coverage=explore.coverage, provenance=runfolder.upstream_provenance(ctx.run_dir, ["explore"]),
        terms=resolve_terms(meaning, states),
        questions=[OpenQuestion(**q.model_dump()) for q in meaning.open_questions], mock_order=mock_order)
    (out / "product_model.json").write_text(model.model_dump_json(indent=1))
    (out / "product_model.md").write_text(render_md(model))
    write_exhibit(ctx.run_dir, 2, "model", exhibit(model, rounds, notes))
