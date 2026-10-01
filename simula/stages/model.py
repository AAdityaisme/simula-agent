"""Goal 1: the product model. Code writes every fact, one model call writes meaning keyed by those ids,
and code checks the merge. Reads explore/ only; writes model/ only."""

import json
import re
import shutil
import statistics
import unicodedata
from collections import Counter
from itertools import count
from io import BytesIO
from pathlib import Path

import numpy as np
import regex
from PIL import Image

from simula import config, llm, render, runfolder, text
from simula.contracts import (ActionLine, ContentRating, Device, Edge, Element, ExploreFile, IconLabel, LedgerItem,
                              ModelMeaning, OpenQuestion, Point, ProductModel, Rect, State, StateFile, Term,
                              VisionElement)
from simula.runlog import needs_human, run_trace, write_exhibit
from simula.stages import Ctx, rerun_command

PREFIX = "Found these elements on screen: "
PROMPT = config.ROOT / "prompts" / "model" / "meaning.md"
MAX_IMAGES = 20
IMAGE_LONG_SIDE = 1568
ANSWER_RESERVE_TOKENS = 8000
TOKENS_PER_NAME = 30
QUESTION_CAP = 5
NOT_OBSERVED = "meaning not observed"
EVERYDAY = " (everyday word, never flagged)"
# Two or more letters in a row, each with the marks written on it: "मैसेज" is three letters, not three runs of one.
WORD = re.compile(f"(?:[^\\W\\d_][{text.MARK}]*){{2,}}")
LABEL_CHARS = 30
SEPARATORS = ",;:、，；："
SCREEN_CHANGE = re.compile(r"→|(?:^|;\s*)[+-]['\"]")
MEASURE = re.compile(r"^(?P<what>.*?)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[^\d\s]*)$")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b|\bago\b", re.IGNORECASE)
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
    holds no text (a crop would bake that text in). Under the no-wallpaper limit of the content area, the area the
    mock and its validator measure; a bigger picture is left to the mock's art search."""
    r = e.rect_px
    holds_text = any((s.text or s.label) and s is not e and inside(s.rect_px, r) for s in siblings)
    small_enough = r.w * r.h < render.WALLPAPER_SHARE * device.w_px * (device.content_bottom_px - device.content_top_px)
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
    """Listed elements first, in tree order, then the controls only the vision pass saw. Tree text loses its
    placeholder characters here, so every later stage quotes the text a person sees."""
    names = {i.mcp_ref: i.name for i in icon_labels}
    elements = []
    for e in (e for e in tree if in_content(e, device)):
        c = e["coordinates"]
        elements.append(make_element(
            f"{sid}.e{len(elements) + 1:02d}", Rect(x=c["x"], y=c["y"], w=c["width"], h=c["height"]),
            e["type"].split(".")[-1], text.strip_placeholders(e.get("text") or ""),
            text.strip_placeholders(names.get(e["ref"]) or e.get("label") or ""), e["ref"], pixels, device))
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


def load_states(explore_dir: Path, device: Device) -> tuple[list[State], dict[str, Image.Image], set[str]]:
    """The states, their screenshots, and the ids of elements whose label a model wrote (an icon-pass name or a
    vision-pass element), which are never app text."""
    states, images, model_labels = [], {}, set()
    for path in sorted(p for p in (explore_dir / "states").glob("*.json") if "." not in p.stem):
        sf = StateFile.model_validate_json(path.read_text())
        image = Image.open(explore_dir / sf.screenshot).convert("RGB")
        tree = read_tree(explore_dir / sf.elements_reply) if sf.elements_reply else []
        images[sf.state_id] = image
        elements = build_elements(sf.state_id, tree, sf.icon_labels, sf.vision_elements, np.asarray(image), device)
        named = {i.mcp_ref for i in sf.icon_labels}
        model_labels |= {e.id for e in elements if e.source == "vision" or e.mcp_ref in named}
        states.append(State(
            id=sf.state_id, kind=sf.kind, parent_id=sf.parent_id, name=sf.state_id, purpose="",
            fingerprint=sf.fingerprint, canonical_png=f"states/{sf.state_id}.png", elements=elements,
            in_mock_scope=False, content_rating="unknown", dynamic_regions=sf.dynamic_regions,
            blocked_reason=sf.blocked_reason))
    return states, images, model_labels


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


def bracketing_moves(lines: list[ActionLine]) -> set[int]:
    """The steps of moves whose two captures sit right around them: the move first captured its to-state, and the
    screen before it still matched the from-state's capture (the move just before captured it, and nothing changed
    in place since). Only there is a difference between the two captures what this move changed. A state is
    captured once, at its first arrival, so a revisit's screen may differ from its capture."""
    if not lines:
        return set()
    current, captured, found = lines[0].from_state, {lines[0].from_state}, set()
    for a in lines:
        if a.outcome != "ok" or a.to_state is None:
            current = current if a.outcome == "denied" else None
            continue
        if a.to_state == a.from_state:
            current = None if a.change_summary else current
            continue
        first = a.to_state not in captured
        if first and current == a.from_state:
            found.add(a.step)
        captured.add(a.to_state)
        current = a.to_state if first else None
    return found


def value_changes(before: State, after: State) -> str:
    """Values that changed in one spot between two captures of the same screen ("7 chats → 8 chats"): the same role
    at the same left edge, top, and height (the width follows the digits), one text there on each side, the same
    words, and a different number. The captures are one screen when most texts without a number are the same text in
    the same spot; two lists that share a layout are not, since their cards' stats sit in the same spots. The words
    occur once on each capture: a list's rows share theirs and can re-sort, so a spot there can't say whose value it
    holds. A bare number ("171", "1 / 295") has no word saying what it counts, and a clock time changes by itself:
    neither is one."""
    # ponytail: a list row's own count (one card's "7 chats → 8 chats") is never reported, and a lone relative time
    # without "ago" ("Synced 5 min") still is; match rows by their names, or add time units to CLOCK, if one reaches
    # a real edge (none of 201 real bracketed moves has either)
    def spots(s: State) -> dict[tuple, str]:
        found: dict[tuple, list[str]] = {}
        for e in s.elements:
            if e.text:
                found.setdefault((e.role, e.rect_px.x, e.rect_px.y, e.rect_px.h), []).append(e.text)
        return {k: texts[0] for k, texts in found.items() if len(texts) == 1}

    def lone(s: State) -> set[str]:
        counts = Counter(NUMBER.sub("#", e.text) for e in s.elements if e.text)
        return {t for t, n in counts.items() if n == 1}
    old, new = spots(before), spots(after)
    old_words, new_words = ([k for k, t in s.items() if not NUMBER.search(t)] for s in (old, new))
    if 2 * sum(old[k] == new.get(k) for k in old_words) <= max(len(old_words), len(new_words)):
        return ""
    once = lone(before) & lone(after)
    return "; ".join(f"{old[k]} → {new[k]}" for k in old if k in new and old[k] != new[k]
                     and NUMBER.sub("#", old[k]) == NUMBER.sub("#", new[k]) and NUMBER.sub("#", old[k]) in once
                     and WORD.search(NUMBER.sub("", old[k])) and not CLOCK.search(old[k]))


def load_edges(explore_dir: Path, states: list[State]) -> tuple[list[Edge], list[str]]:
    """One edge per distinct recorded move that reached a state (or changed something in place). The
    transition is the one explore recorded. What changed is the explorer's summary, or else, for a move its two
    captures sit right around, the values that changed between them. Returns the edges and a note for every
    line not taken as given."""
    by_id = {s.id: s for s in states}
    edges, notes = {}, []
    lines = read_actions(explore_dir)
    bracketed = bracketing_moves(lines)
    for a in lines:
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
        diffed = "" if a.change_summary or a.step not in bracketed else value_changes(by_id[a.from_state],
                                                                                        by_id[a.to_state])
        if diffed:
            notes.append(f"step {a.step}: what changed is code's diff of the move's two captures: {diffed}")
        changed = a.change_summary or diffed
        edges.setdefault(edge_id, Edge(
            id=edge_id, from_state=a.from_state, to_state=a.to_state, element_id=element.id if element else None,
            action=a.action, transition=a.transition, change_summary=changed))
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
    """One pass's measurements, in whatever units the explorer measured: the parts of the last line of the pass
    that has any. A pass writes several lines (tap the box, type, send; or open, back) and only one carries the
    measurement; a line that says what changed on screen ("3 left → 2 left", "+'typed text'") never does."""
    for a in reversed(lines):
        found = [] if SCREEN_CHANGE.search(a.change_summary) else measurements(a.change_summary)
        if found:
            return found
    return []


def pass_count(n: int) -> str:
    return f"{n} pass" if n == 1 else f"{n} passes"


def measured(what: str, unit: str, values: list[float]) -> str:
    """One measurement is said as one, not as a median, min, and max that are all the same number."""
    shown = "" if unit == what else " " + unit
    if len(values) == 1:
        return f"{what} {values[0]:g}{shown} (1 measurement)"
    return (f"{what} median {statistics.median(values):g}{shown} "
            f"(min {min(values):g}, max {max(values):g}, n={len(values)})")


def loop_facts(explore_dir: Path, states: list[State], edges: list[Edge]) -> list[LedgerItem]:
    """The measured experience, from the explorer's core-loop passes: one item with each measurement's median, min,
    max and n (or the one value, when there is one), and one saying what stopped the loop, or that nothing did on an
    account whose plan explore doesn't record. Passes are counted by distinct loop_pass, not by line. A stop counts
    from any line, even a denied one; measurements only from lines that ran."""
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
        parts = [measured(what, unit, v) for (what, unit), v in values.items()]
        items.append(LedgerItem(id="exp1", kind="experience", evidence_ids=evidence,
                                verbatim=f"Core action over {pass_count(len(by_pass))} ({steps}): " + "; ".join(parts)))
    stop = next((a for a in loop if a.loop_stop), None)
    outcome = (f"{stop.loop_stop} appeared on pass {stop.loop_pass} of the core action" if stop else
               f"After {pass_count(len(by_pass))} of the core action nothing limited it: no limit, paywall, or ad "
               "appeared, on an account whose plan (free or paid) was not recorded")
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
                             messages=messages, max_tokens=config.max_tokens(role), budget=budget,
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
        "open_questions": keep(meaning.open_questions, question_problem, "question",
                               lambda q: repr(q.question))[:QUESTION_CAP],
    })
    uses = {m.id: m.summary for m in cleaned.mechanics} | {i.id: i.verbatim for i in cleaned.value_ledger}

    def term_problem(t):
        if not any(i in uses and text.phrase(t.term).search(uses[i]) for i in t.used_in):
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


def resolve_terms(meaning: ModelMeaning, states: list[State], edges: list[Edge], model_labels: set[str]) -> list[Term]:
    """Which terms a screen explains. An anchor is app text that carries the term (as a whole word) and, with the term
    cut out, still says something in words of two or more letters, so a bare name or a count ("1.8k tokens") is never
    one. A screen is explained when a cited element on it is an anchor, or when a recorded tap on an anchor (cited or
    not) opened it: "Upgrade to <term>" opening the plan's benefit list; a tap that changed its own screen opens
    nothing. On an explained screen, a cited element that carries the term counts only if it is an anchor itself, and
    one that doesn't (the bullets under a plan's name) counts when it has any word character. `defined_by` keeps what
    counts, so it holds only the model's own citations and may name none of the anchors; `anchor_taps` names the taps
    it relied on. Only on-screen text decides: whether the model also quoted an element in the ledger doesn't matter,
    and a label a model wrote (`model_labels`) is never app text. A term with nothing that counts is marked 'meaning
    not observed', and an idea that uses it is flagged unless the model labeled it `everyday`; the label is kept as
    written and never makes a term observed. Known limits: a call to action ("Unlock <term>"), a role word in an app's
    own label ("<term> tab") or a sentence that only uses the term ("monthly <term> with our models") reads as an
    explanation, a tap on an element that names the term only in passing (a list row "<name>, 2 <term>") carries it
    to whatever screen that tap opened, and a price on a plan card ("Weekly", "$1.99") doesn't explain; which cited
    text explains the term stays the model's call."""
    elements = {e.id: e for s in states for e in s.elements}
    screen = {e.id: s.id for s in states for e in s.elements}
    taps = [g for g in edges if g.action == "tap" and g.element_id in elements and g.to_state != g.from_state]

    def app_text(e: Element) -> list[str]:
        return [e.text] if e.id in model_labels else [e.text, e.label]

    def rest(e: Element, name: re.Pattern[str]) -> str:
        return " ".join(name.sub(" ", f) for f in app_text(e))

    def carries(e: Element, name: re.Pattern[str]) -> bool:
        return any(name.search(f) for f in app_text(e))

    def anchors(e: Element, name: re.Pattern[str]) -> bool:
        return carries(e, name) and WORD.search(rest(e, name)) is not None

    def counts(e: Element, name: re.Pattern[str]) -> bool:
        return anchors(e, name) if carries(e, name) else re.search(r"\w", rest(e, name)) is not None

    terms = []
    for t in meaning.terms:
        name = text.phrase(t.term)
        cited = [elements[i] for i in t.defined_by if i in elements]
        anchored = {screen[e.id] for e in cited if anchors(e, name)}
        opened = [g for g in taps if anchors(elements[g.element_id], name)]
        explained = anchored | {g.to_state for g in opened}
        defined_by = [e.id for e in cited if screen[e.id] in explained and counts(e, name)]
        through = {screen[i] for i in defined_by} - anchored
        terms.append(Term(term=t.term, meaning=t.meaning if defined_by else NOT_OBSERVED, defined_by=defined_by,
                          used_in=t.used_in, everyday=t.everyday, observed=bool(defined_by),
                          anchor_taps=[g.id for g in opened if g.to_state in through]))
    return terms


def folded_bullets(ledger: list[LedgerItem], states: list[State]) -> list[LedgerItem]:
    """Every paywall bullet gets a ledger item. The model sees two items of a repeated list and one folded line for
    the rest, so the third benefit of a paywall list goes missing. Code quotes it whole: an item of the same repeated
    list, at the same left edge, as a kept paywall bullet, with text and no ledger item yet, whose words no paywall
    bullet, and no item citing such a list, already quotes (a paywall captured twice lists each bullet twice). Ids are
    pb1, pb2, ..., skipping any the model used."""
    elements = {e.id: e for s in states for e in s.elements}
    cited = {i for item in ledger for i in item.evidence_ids}
    lists = {(elements[i].repeat_group, elements[i].rect_px.x) for item in ledger if item.kind == "paywall_bullet"
             for i in item.evidence_ids if i in elements and elements[i].repeat_group}

    def on_list(i: str) -> bool:
        return i in elements and (elements[i].repeat_group, elements[i].rect_px.x) in lists
    quoted = {tuple(item.verbatim.split()) for item in ledger
              if item.kind == "paywall_bullet" or any(on_list(i) for i in item.evidence_ids)}
    found = []
    for e in elements.values():
        words = tuple(e.text.split())
        if on_list(e.id) and words and e.id not in cited and words not in quoted:
            quoted.add(words)
            found.append(e)
    taken = {item.id for item in ledger}
    ids = (f"pb{n}" for n in count(1) if f"pb{n}" not in taken)
    return [LedgerItem(id=next(ids), kind="paywall_bullet", verbatim=e.text, evidence_ids=[e.id]) for e in found]


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
    currency, or ad, then every state showing any other mechanic (a modal or sheet brings every layer under
    it first), then every state on the core flows in flow order. No cap, and nothing else: a tab or depth-1
    screen is in only when it is on a flow or holds a mechanic. Never blocked or outside the app. An unsafe screen stays
    out (it doesn't belong in a pitch) unless it is on a core flow or under a flow's dialog, where a gap would break
    the flow. The
    product model keeps every state. Every mechanic's screen stays in, because propose drops an idea whose
    trigger screen isn't mocked."""
    by_id = {s.id: s for s in states}
    edge_by_id = {e.id: e for e in edges}
    root = next((s.id for s in states if s.kind == "screen"), None)

    def layers(sid: str) -> list[str]:
        """The state and every layer under it, down to the screen: a dialog over a dialog needs them all."""
        chain = [sid]
        while chain[0] in by_id and by_id[chain[0]].parent_id not in (None, *chain):
            chain.insert(0, by_id[chain[0]].parent_id)
        return chain

    flow_states = [sid for f in meaning.flows for i in f.edge_ids if i in edge_by_id
                   for sid in (edge_by_id[i].from_state, edge_by_id[i].to_state)]
    # A flow modal keeps its parent even when that parent is unsafe, since a gap would break the flow: the mock draws
    # exactly this scope (nothing blurs it), and propose never triggers an offer on an unsafe screen.
    on_flow = {layer for sid in flow_states for layer in layers(sid)}
    eligible = {s.id for s in states if s.kind not in ("blocked", "external", "rotated")
                and (s.content_rating != "unsafe" or s.id in on_flow)}
    first = sorted(meaning.mechanics, key=lambda m: m.kind not in SCOPE_KINDS)
    mechanic_states = [i.split(".")[0] for m in first for i in m.evidence_ids]
    ordered = []
    for sid in [root, *mechanic_states, *flow_states]:
        chain = layers(sid)
        if all(c in eligible for c in chain[:-1]):
            ordered += chain
    return list(dict.fromkeys(s for s in ordered if s in eligible))


def edge_overlay(e: Element, device: Device) -> bool:
    """A wordless container in a bottom corner of the screen, touching one side edge and running past the content
    area, like an edge-gesture area: not art, since its pixels are only what sits under it. A full-width wordless
    box touches both sides, and one inside the content area touches no corner: both are still art."""
    r = e.rect_px
    in_corner = (r.x <= 0) != (r.x + r.w >= device.w_px) and r.y + r.h > device.content_bottom_px
    return e.type not in ROLE_BY_CLASS and not (e.text or e.label) and in_corner


def flat(crop: Image.Image) -> bool:
    """One color: a solid fill the mock draws from the element's colors, not a picture worth an asset. Two colors can
    already be a shape, such as a glyph on a tile, which only its crop keeps."""
    return crop.getcolors(1) is not None


def finish_elements(state: State, scope: set[str], tapped: set[str], image: Image.Image, out: Path,
                    device: Device) -> State:
    """Crops image assets and marks what the mock draws: every in-scope element with words or art, and every
    element that starts an edge. Flat art (one color) is drawn from its colors, with no asset. Tagging
    only two items of a repeated list is the mock's job."""
    in_scope = state.id in scope
    elements = []
    for e in state.elements:
        asset, art = None, in_scope and is_image_like(e, state.elements, device) and not edge_overlay(e, device)
        if art:
            r = e.rect_px
            crop = image.crop((int(r.x), int(r.y), int(r.x + r.w), int(r.y + r.h)))
            if not flat(crop):
                asset = f"assets/{e.id}.png"
                crop.save(out / asset)
        in_mock = in_scope and (e.id in tapped or bool(e.text or e.label or art))
        elements.append(e.model_copy(update={"asset_png": asset, "in_mock": in_mock}))
    return state.model_copy(update={"elements": elements, "in_mock_scope": in_scope})


def hide_parent_copies(states: list[State], tapped: set[str]) -> tuple[list[State], list[str]]:
    """A modal or sheet's capture still lists its parent's elements, under its box and above it behind the backdrop.
    The mock shows the parent's layer under the modal's, so a copy of what the parent's layer draws is not drawn
    again (in_mock false). An element is the parent's when the parent's layer draws one of the same class, words, and
    box; one that starts an edge stays drawn. Returns the states and the ids no longer drawn."""
    # ponytail: the runtime shows one parent layer, so a dialog over a dialog still draws the screen under both in its
    # own layer; hide those too once the runtime shows every layer down the data-parent chain
    def key(e: Element) -> tuple:
        return e.type, e.text, e.label, e.rect_px.x, e.rect_px.y, e.rect_px.w, e.rect_px.h
    by_id = {s.id: s for s in states}
    copies: set[str] = set()
    layers: dict[str, set[tuple]] = {}

    def layer(s: State) -> set[tuple]:
        """What a state's own layer draws: its drawn elements less its copies of its parent's layer."""
        if s.id not in layers:
            dialog = s.kind in ("modal", "sheet") and s.parent_id in by_id
            theirs = layer(by_id[s.parent_id]) if dialog else set()
            mine = {e.id for e in s.elements if e.in_mock and key(e) in theirs and e.id not in tapped}
            copies.update(mine)
            layers[s.id] = {key(e) for e in s.elements if e.in_mock and e.id not in mine}
        return layers[s.id]
    for s in states:
        layer(s)
    hidden = [s.model_copy(update={"elements": [e.model_copy(update={"in_mock": False}) if e.id in copies else e
                                                for e in s.elements]}) for s in states]
    return hidden, sorted(copies)


def asset_note(states: list[State], device: Device, copies: list[str]) -> str:
    """What code decided about drawing the states in scope: crops saved, flat boxes drawn from their colors, wordless
    corner boxes and a modal's copies of its parent not drawn."""
    art = [e for s in states if s.in_mock_scope for e in s.elements if is_image_like(e, s.elements, device)]
    corner = {e.id for e in art if edge_overlay(e, device)}
    fills = [e.id for e in art if not e.asset_png and e.id not in corner]
    return (f"crops saved: {sum(bool(e.asset_png) for e in art)}; flat boxes drawn from their colors: {len(fills)}"
            + (f" ({', '.join(fills)})" if fills else "") + f"; wordless corner boxes not drawn: {len(corner)}; "
            f"a parent's elements a modal or sheet repeats, not drawn again: {len(copies)}")


# ---------- markdown ----------

def mermaid_label(text: str) -> str:
    return re.sub(r'["\[\](){}|<>]', " ", text).strip() or "?"


def short_name(name: str, limit: int = LABEL_CHARS) -> str:
    """`name` on one line, cut past `limit` drawn characters (Unicode grapheme clusters) with "…": at the last space
    in the cut's second half, else at the limit, as a script written without spaces needs. A space, dash, opening
    bracket or quote, or comma-like separator left before the "…" goes; "%", "?", "!", "." and closing quotes stay."""
    chars = regex.findall(r"\X", " ".join(name.split()))
    if len(chars) <= limit:
        return "".join(chars)
    cut = next((i for i in range(limit, limit // 2 - 1, -1) if chars[i] == " "), limit)
    kept = chars[:cut]
    while kept and (kept[-1] in SEPARATORS or unicodedata.category(kept[-1][0]) in ("Zs", "Pd", "Ps", "Pi")):
        kept.pop()
    return "".join(kept) + "…"


def render_md(model: ProductModel) -> str:
    elements = {e.id: e for s in model.states for e in s.elements}
    edges = {e.id: e for e in model.edges}

    def edge_line(e: Edge) -> str:
        el = elements.get(e.element_id)
        what = short_name(mermaid_label((el.text or el.label or el.role) if el else e.action))
        return f"  {e.from_state} -->|{e.transition}: {what}| {e.to_state}"

    lines = [f"# Product model: {model.app_name or model.app} {model.app_version}", "",
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
    lines += [f"- **{t.term}**{EVERYDAY if t.everyday else ''}: {t.meaning}"
              + (f" · defined by {', '.join(t.defined_by)}" if t.observed else "")
              + (f" · through tap {', '.join(t.anchor_taps)}" if t.anchor_taps else "")
              + f" · used in {', '.join(t.used_in)}" for t in model.terms] or ["- none"]
    lines += ["", "## Values shared across screens", ""]
    lines += [f"- {v.label}: {v.value_text} · {', '.join(v.evidence_ids)}" for v in model.cross_screen_values] or ["- none"]
    lines += ["", "## Open questions (for a targeted explore pass)", ""]
    lines += [f"- {q.id} {q.question} · start at {q.start_state}, look for: {q.look_for}" for q in model.questions] \
        or ["- none"]
    return "\n".join(lines) + "\n"


def exhibit(model: ProductModel, rounds: list[list[str]], notes: list[str], assets: str) -> str:
    lines = ["# 02 · model", "",
             f"- app name (the meaning call, read off the screens): {model.app_name or 'no screen shows it'}",
             f"- {len(model.states)} states, {sum(len(s.elements) for s in model.states)} elements, "
             f"{len(model.edges)} edges (all from explore, code-owned)",
             f"- app category: {model.app_category}; {len(model.flows)} core flows; {len(model.mechanics)} mechanics; "
             f"{len(model.value_ledger)} verbatim ledger items",
             "- measured experience: " + (" · ".join(i.verbatim for i in model.value_ledger if i.kind == "experience")
                                           or "the core action was not repeated"),
             f"- app terms: {len(model.terms)}, meaning not observed for: "
             + (", ".join(t.term + (EVERYDAY if t.everyday else "") for t in model.terms if not t.observed) or "none"),
             f"- open questions for the explorer: {len(model.questions)}",
             f"- mock scope (code, priority order): {', '.join(model.mock_order) or 'none'}",
             f"- drawing (code): {assets}",
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
                    ["trace.jsonl", "model/raw_reply.txt"], rerun_command("model", ctx))
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
                        ["exhibits/02-model.md", "model/raw_reply.txt"], rerun_command("model", ctx))
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

    states, images, model_labels = load_states(explore_dir, device)
    edges, notes = load_edges(explore_dir, states)
    tapped = {e.element_id for e in edges if e.element_id}
    states = [group_repeats(s, tapped) for s in states]
    experience = loop_facts(explore_dir, states, edges)
    for s in states:
        content_png(images[s.id], device).save(out / s.canonical_png)
    run_trace(ctx.run_dir, stage="model", step="facts", decider="code",
              note=f"{len(states)} states, {sum(len(s.elements) for s in states)} elements, {len(edges)} edges"
                   + (f"; {len(notes)} explore lines not taken as given" if notes else ""))

    # Known limit: past the naming budget, later states' elements go unnamed; split the call if a run ever gets there
    answer_tokens = config.max_tokens(config.roles(ctx.profile)["model_meaning"])
    name_limit = (answer_tokens - ANSWER_RESERVE_TOKENS) // TOKENS_PER_NAME
    dump = describe(states, edges, ctx.app, device, name_limit, experience)
    shots = [(s.id, png_bytes(content_png(images[s.id], device)))
             for s in states if s.kind not in ("external", "rotated")][:MAX_IMAGES]
    meaning, rounds = understand(ctx, dump, shots, states, edges)

    states = apply_meaning(states, meaning, config.profiles()["content"]["adult_keywords"])
    states = code_roles(states, edges)
    mock_order = mock_scope(states, edges, meaning)
    scope = set(mock_order)
    states = [finish_elements(s, scope, tapped, images[s.id], out, device) for s in states]
    states, copies = hide_parent_copies(states, tapped)
    assets = asset_note(states, device, copies)
    run_trace(ctx.run_dir, stage="model", step="scope", decider="code", note=", ".join(mock_order))
    run_trace(ctx.run_dir, stage="model", step="assets", decider="code", note=assets)
    bullets = folded_bullets(meaning.value_ledger, states)
    quoted = ", ".join(i.evidence_ids[0] for i in bullets) or "none"
    run_trace(ctx.run_dir, stage="model", step="bullets", decider="code",
              note=f"paywall bullets quoted from folded lists: {quoted}")

    model = ProductModel(
        app=ctx.app["name"], app_name=meaning.app_name, app_version=explore.app_version or "unknown",
        app_category=meaning.app_category,
        run_id=ctx.run_dir.name, device=device, states=states, edges=edges, flows=meaning.flows,
        mechanics=meaning.mechanics, cross_screen_values=meaning.cross_screen_values,
        value_ledger=meaning.value_ledger + bullets + experience,
        open_questions=[q.question for q in meaning.open_questions],
        coverage=explore.coverage, provenance=runfolder.upstream_provenance(ctx.run_dir, ["explore"]),
        terms=resolve_terms(meaning, states, edges, model_labels),
        questions=[OpenQuestion(id=f"q{n}", **q.model_dump()) for n, q in enumerate(meaning.open_questions, start=1)],
        mock_order=mock_order)
    for t in model.terms:
        if t.anchor_taps:
            run_trace(ctx.run_dir, stage="model", step="term_tap", decider="code",
                      note=f"{t.term} observed through tap {', '.join(t.anchor_taps)}; "
                           f"defined by {', '.join(t.defined_by)}")
    (out / "product_model.json").write_text(model.model_dump_json(indent=1))
    (out / "product_model.md").write_text(render_md(model))
    write_exhibit(ctx.run_dir, 2, "model", exhibit(model, rounds, notes, assets))
