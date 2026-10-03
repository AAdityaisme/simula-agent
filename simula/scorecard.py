"""`simula scorecard`: scores runs' outputs the same way, whichever explorer made them. It reads only the files every
explorer writes (manifest, trace, explore/, the product model) and the later stages' outputs when present, never
explore's own logic or its idea of screen identity, and never writes into a run. No model calls."""

import json
import re
import tomllib
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import get_args

from pydantic import Field

from simula import config, decide, text
from simula.contracts import (ActionLine, CandidatesFile, ContractReport, Decision, DecisionsFile, Device,
                              Element, ExploreFile, Manifest, MechanicKind, ProductModel, QAReport, State,
                              StateFile, Strict, TraceLine)
from simula.device import observe as ob
from simula.runlog import read_trace
from simula.stages import ROLES, model

BILLING = "com.android.vending"  # the Play Store, whose payment sheet is a hard block for every explorer
LIST_ITEMS = 3
NUMBER = re.compile(r"\d+")
SETTINGS = re.compile(r"\bsettings?\b", re.IGNORECASE)
MENU = re.compile(r"\bmenu\b|\blinks? to\b|\bnavigation\b", re.IGNORECASE)
# A purpose's subject ends at the first of these: stop punctuation, or a word that starts saying what the state holds.
SUBJECT_END = re.compile(r"[:;.(]|\s(?:with|listing|containing|showing|linking|asking|over)\b", re.IGNORECASE)
SEND = "(core loop: send)"
# The role each multi-role stage's model calls are made by, by step name: a judge step names its judge itself, and the
# benefit naming judge's revisions ask for runs under propose.
STEP_ROLES = {"qa": [("critic", "qa_critic"), ("fixer", "qa_fixer")],
              "propose": [("lens:", "proposer"), ("topup", "proposer"), ("dedupe", "propose_dedupe"),
                          ("judge:revisions", "propose_dedupe")],
              "judge": [("revise:", "proposer")]}
JEV_SECONDS = re.compile(r" in (\d+(?:\.\d+)?)s$")
JUDGE = "Judge (an unvalidated instrument: no human labels yet)"


class InventoryScreen(Strict):
    name: str
    texts: list[str] = Field(min_length=1, description="Texts that identify the screen; a state shows them all.")


class Inventory(Strict):
    """An app's named screens, written by a person who walked the app."""
    screens: list[InventoryScreen] = Field(min_length=1)


def load_inventory(path: Path) -> Inventory:
    return Inventory.model_validate(tomllib.loads(path.read_text()))


# ---------- reading a run ----------

Saved = list[tuple[StateFile, list[dict]]]


def saved_states(explore: Path) -> Saved | None:
    """Each saved state with its element tree; None when the run has no states folder."""
    folder = explore / "states"
    if not folder.is_dir():
        return None
    saved = []
    for path in sorted(p for p in folder.glob("*.json") if "." not in p.stem):
        state = StateFile.model_validate_json(path.read_text())
        reply = explore / state.elements_reply
        saved.append((state, model.read_tree(reply) if state.elements_reply and reply.exists() else []))
    return saved


def load(path: Path, schema):
    return schema.model_validate_json(path.read_text()) if path.exists() else None


# ---------- the scorecard's identity rule ----------

def fixed_texts(tree: list[dict], device: Device) -> frozenset[str]:
    """What a screen shows outside repeated lists, numbers masked and case-folded. A list is 3 or more boxes of one
    class and size (8 dp buckets) at different spots, so nested boxes of one size are no list; a text that is one of
    them, or lies inside one, is list content."""
    shown = [e for e in tree if ob.in_content(e, device)]
    groups = defaultdict(list)
    for e in shown:
        r = ob.rect(e)
        groups[e["type"], ob.bucket(r.w, device), ob.bucket(r.h, device)].append(r)
    items = [r for rects in groups.values()
             if len({(ob.bucket(r.x, device), ob.bucket(r.y, device)) for r in rects}) >= LIST_ITEMS for r in rects]
    return frozenset(NUMBER.sub("#", t).casefold() for e in shown
                     if (t := text.strip_placeholders(e.get("text") or "").strip())
                     and not any(ob.inside(ob.rect(e), r) for r in items))


def identity(state: StateFile, tree: list[dict], device: Device) -> tuple[str, frozenset[str]]:
    """Two saved screens are one screen when they share the package and the fixed texts."""
    return state.foreground_package, fixed_texts(tree, device)


def hidden_changes(actions: list[ActionLine]) -> list[ActionLine]:
    """Moves recorded as staying on their state while the screen changed: a screen change the record never saved."""
    return [a for a in actions if a.to_state == a.from_state and a.change_summary]


def tour_hidden_changes(actions: list[ActionLine]) -> list[ActionLine]:
    """The hidden changes outside the core loop and typing, which change the screen they stay on by design (a reply
    growing, typed text)."""
    return [a for a in hidden_changes(actions) if a.loop_pass is None and a.action != "type"]


def completed(lines: list[ActionLine]) -> bool:
    """A core pass whose result showed: what stopped the loop, or a measurement that isn't a wait for nothing
    ('no reply within 45 s')."""
    measured = model.pass_measurements([a for a in lines if a.outcome == "ok"])
    return any(a.loop_stop for a in lines) or any(not what.startswith("no ") for what, _, _ in measured)


# ---------- the explorer-independent checklist ----------

def name(e: Element) -> str:
    return e.text or e.label


def band_named(state: State, device: Device) -> list[Element]:
    return [e for e in state.elements if e.rect_px.y >= device.content_bottom_px - ob.TAB_BAND_PX and name(e)]


def size(e: Element, device: Device) -> tuple[str, int, int]:
    return e.type, ob.bucket(e.rect_px.w, device), ob.bucket(e.rect_px.h, device)


def candidate_rows(state: State, device: Device) -> list[list[Element]]:
    """What a tab bar looks like: rows of 2 or more same-class, same-size named elements in a screen's bottom band,
    evenly spaced (within one 8 dp bucket) across its middle. A composer's buttons are never evenly spaced, nor named
    on their outer boxes."""
    rows = defaultdict(dict)
    for e in band_named(state, device):
        rows[(*size(e, device), ob.bucket(e.rect_px.y, device))][ob.bucket(e.rect_px.x, device)] = e

    def even(row: list[Element]) -> bool:
        xs = sorted(e.rect_px.x + e.rect_px.w / 2 for e in row)
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        return bool(gaps) and xs[0] < device.w_px / 2 < xs[-1] and max(gaps) - min(gaps) <= ob.BUCKET_DP * device.scale
    return [row for row in (list(r.values()) for r in rows.values()) if even(row)]


def tab_bar(pm: ProductModel) -> tuple[list[Element], bool]:
    """The tab bar the run saw, on any saved screen, and whether it is confirmed: navigation stays put, so another saved
    screen showing all of a row's names in its bottom band confirms it. The biggest confirmed row, else the biggest
    candidate; none when no saved screen shows a candidate."""
    screens = [s for s in pm.states if s.kind == "screen"]
    shown = {s.id: {name(e) for e in band_named(s, pm.device)} for s in screens}
    rows = [(s.id, row) for s in screens for row in candidate_rows(s, pm.device)]
    confirmed = [row for sid, row in rows
                 if any({name(e) for e in row} <= names for other, names in shown.items() if other != sid)]
    return max(confirmed or [row for _, row in rows], key=len, default=[]), bool(confirmed)


def tab_of(state: State, element_id: str | None, bar: list[Element], device: Device) -> str | None:
    """The tab a tapped element is: one of the bar's own elements on that state (the bar's class and size, a tab's
    name), or a wordless box holding exactly one of them, as a tab's tap target holds its label."""
    names, kind = {name(e) for e in bar}, size(bar[0], device)
    members = [e for e in band_named(state, device) if size(e, device) == kind and name(e) in names]
    tapped = next((e for e in state.elements if e.id == element_id), None)
    if tapped is None:
        return None
    if any(m.id == tapped.id for m in members):
        return name(tapped)
    held = [m for m in members if ob.inside(m.rect_px, tapped.rect_px)]
    return name(held[0]) if len(held) == 1 and not name(tapped) else None


def all_tabs(pm: ProductModel) -> bool | None:
    """None when no saved screen shows a tab bar: no bar seen is no evidence the run walked one. Open for a bar no other
    saved screen confirms. Otherwise every tab needs a successful tap on its own control that went to another saved
    screen, not an overlay, counted once per tab. The tab the run started on counts only when its own control was
    tapped too: nothing else tells which tab that was."""
    bar, confirmed = tab_bar(pm)
    if not bar:
        return None
    if not confirmed:
        return False
    states = {s.id: s for s in pm.states}
    visited = {tab for e in pm.edges if e.action == "tap" and e.from_state in states and e.to_state != e.from_state
               and e.to_state in states and states[e.to_state].kind == "screen"
               and (tab := tab_of(states[e.from_state], e.element_id, bar, pm.device))}
    return visited >= {name(e) for e in bar}


def settings_screen(state: State) -> bool:
    """A heuristic over the model's wording. A settings screen's recorded purpose has settings in its subject: its
    words before the first ':', ';', '.' or '(' or a word like 'with' or 'listing' that starts saying what the screen
    holds. A purpose that says menu, links to or navigation describes a way to settings, as does one that names them
    only further on; one that never names them leaves it to the state's name."""
    purpose = " ".join(state.purpose.split())
    if MENU.search(purpose):
        return False
    if SETTINGS.search(purpose):
        return bool(SETTINGS.search(SUBJECT_END.split(purpose, maxsplit=1)[0]))
    return bool(SETTINGS.search(state.name))


def model_checklist(pm: ProductModel) -> dict[str, bool | None]:
    """explore's checklist items, answered from the product model alone; all_tabs is None when no tab bar was seen."""
    observed = {m.kind for m in pm.mechanics if m.status == "observed"}
    ledger = {item.kind for item in pm.value_ledger}
    found = {"root": any(s.kind == "screen" for s in pm.states),
             "all_tabs": all_tabs(pm),
             "paywall_or_membership": "paywall" in observed or bool({"price", "paywall_bullet"} & ledger),
             "settings": any(map(settings_screen, pm.states)),
             "limit": "limit" in observed or "limit" in ledger}
    items = tomllib.loads((config.CONFIG / "checklist.toml").read_text())["items"]
    return {item: found.get(item, False) for item in items}


# ---------- the sections ----------

def explore_record(states: Saved | None, actions: list[ActionLine] | None, explore: ExploreFile | None) -> dict:
    out = {}
    if states is not None:
        device = explore.device if explore else Device()
        kinds = Counter(s.kind for s, _ in states)
        screens = [identity(s, tree, device) for s, tree in states if s.kind == "screen"]
        out |= {"states saved": len(states), "screens saved": kinds["screen"],
                "overlays saved (modal, sheet)": kinds["modal"] + kinds["sheet"], "distinct screens": len(set(screens)),
                "screens with no fixed text": sum(not fixed for _, fixed in screens)}
    if actions is not None:
        outcomes = Counter(a.outcome for a in actions)
        out |= {"hidden screen changes": len(hidden_changes(actions)),
                "hidden screen changes outside the core loop and typing": len(tour_hidden_changes(actions)),
                **{f"actions {o}": outcomes[o] for o in get_args(ActionLine.model_fields["outcome"].annotation)}}
    if explore:
        out["relaunches"] = explore.relaunches
    return out


def core_action(trace: list[TraceLine], actions: list[ActionLine] | None) -> dict:
    passes = defaultdict(list)
    for a in actions or []:
        if a.loop_pass is not None:
            passes[a.loop_pass].append(a)
    named = any(t.stage == "explore" and t.step == "core" and t.note.startswith("core action: ") for t in trace)
    if actions is None:
        return {"core action found": named} if named else {}
    return {"core action found": named or bool(passes), "core passes attempted": len(passes),
            "core passes completed": sum(map(completed, passes.values()))}


def checklist(explore: ExploreFile | None, pm: ProductModel | None) -> dict:
    out = {}
    if explore:
        out |= {"answered by explore": len(explore.coverage.checklist_answered),
                "open in explore": ", ".join(explore.coverage.checklist_open) or "none"}
    if pm:
        answered = model_checklist(pm)
        still_open = [f"{i} (no tab bar seen)" if ok is None else i for i, ok in answered.items() if ok is not True]
        out |= {"answered by the product model": sum(ok is True for ok in answered.values()),
                "open in the product model": ", ".join(still_open) or "none",
                "settings screen (purpose/name heuristic)": answered.get("settings")}
    return out


def content_filter(explore: ExploreFile | None, trace: list[TraceLine]) -> dict:
    if explore is None:
        return {}
    checks = [t for t in trace if t.stage == "explore" and t.step == "filter.check"]
    passed = sum(t.outcome == "ok" for t in checks)
    applied = bool(explore.content_filter or explore.filter_controls or explore.filter_on is not None)
    verified = "none found" if not applied else "yes" if checks and passed == len(checks) else "no"
    return {"filter verified": verified, "filter checks": len(checks), "filter checks passed": passed}


def hard_blocks(states: Saved | None, actions: list[ActionLine] | None, trace: list[TraceLine]) -> dict:
    out = {"payment sheet in trace lines": sum(BILLING in t.note for t in trace)} if trace else {}
    if states is not None:
        out["payment sheet states"] = sum(s.foreground_package == BILLING for s, _ in states)
    if actions is not None:
        typed = [a for a in actions if a.action == "type" and a.outcome == "ok"]
        out |= {"typed actions outside the core loop": sum(a.loop_pass is None for a in typed),
                "core-loop typed actions": sum(a.loop_pass is not None for a in typed)}
    if explored := [t for t in trace if t.stage == "explore"]:
        out[f"sends (recorded '{SEND[1:-1]}')"] = sum(t.outcome == "ok" and t.note.endswith(SEND) for t in explored)
    return out


def product_model(pm: ProductModel | None, trace: list[TraceLine]) -> dict:
    out = {}
    if pm:
        kinds = Counter(m.kind for m in pm.mechanics)
        out |= {"states": len(pm.states), "states in mock scope": sum(s.in_mock_scope for s in pm.states),
                "flows": len(pm.flows), **{f"mechanics: {k}": kinds[k] for k in get_args(MechanicKind)},
                "ledger items": len(pm.value_ledger), "cross-screen values": len(pm.cross_screen_values),
                "open questions": len(pm.open_questions), "terms": len(pm.terms), "app category": pm.app_category}
    lines = [t for t in trace if t.stage == "model"]
    if lines:
        out |= {"model retry": any(t.step == "retry" for t in lines),
                "model needs-human": any(t.step == "needs_human" and t.outcome == "blocked" for t in lines)}
    return out


def downstream(run: Path) -> dict:
    out = {}
    if contract := load(run / "mock" / "contract_report.json", ContractReport):
        scope = set(contract.screens)
        undrawn = {e.screen for e in contract.errors if e.kind == "undrawn_screen"}
        out |= {"mock contract passed": contract.passed, "mock screens in scope": len(scope),
                "mock screens drawn": len(scope - undrawn)}
    if qa := load(run / "qa" / "qa_report.json", QAReport):
        tagged = qa.structure.tagged
        out |= {"QA approved round": qa.approved_round, "QA score": qa.keep_score,
                "QA mean masked SSIM": qa.visual.masked_ssim_mean,
                "QA bounds share": qa.structure.within_4dp / tagged if tagged else None}
    if ideas := load(run / "propose" / "candidates.json", CandidatesFile):
        out |= {"propose drafts": len(ideas.candidates),
                "propose distinct ideas kept": sum(c.dropped_reason is None for c in ideas.candidates)}
    return out


def judge(run: Path) -> dict:
    if not (decisions := load(run / "judge" / "decisions.json", DecisionsFile)):
        return {}
    finals = Counter(d.final for d in decisions.decisions)
    return {f"judge {final}": finals[final] for final in get_args(Decision.model_fields["final"].annotation)}


def role(line: TraceLine, roles: dict[str, str]) -> str:
    """The role that made a call: the one its stage and step family make it by (STEP_ROLES), else the stage's role its
    step names ('judge:c01:judge_1:r1' is judge_1), else the stage's one role whose configured model made it, else the
    stage and model."""
    stage_roles = ROLES.get(line.stage, [])
    found = ([r for prefix, r in STEP_ROLES.get(line.stage, []) if line.step.startswith(prefix)]
             or [r for r in stage_roles if re.search(rf"\b{re.escape(r)}\b", line.step)]
             or [r for r in stage_roles if line.model in roles.get(r, "").split()])
    return found[0] if len(found) == 1 else f"{line.stage} {line.model}"


def jev_backend(line: TraceLine) -> str:
    """The backend a Jev call went to: an answer's note names it, a failure carries its priced model; a refusal carries
    TypeSafe's own model name, so anything else is TypeSafe."""
    return next((b for b in decide.BACKENDS if line.note.startswith(f"{b} ") or line.model == decide.priced_as(b)),
                "typesafe")


def cost_and_time(trace: list[TraceLine], manifest: Manifest | None, actions: list[ActionLine] | None,
                  distinct: int | None) -> dict:
    if not trace:
        return {}
    by_stage = defaultdict(list)
    for t in trace:
        by_stage[t.stage].append(t)
    stages = [s for s in config.STAGES if s in by_stage]
    usd = {s: sum(t.usd for t in by_stage[s]) for s in stages}
    # Stages write from thread pools, so a later line can carry an earlier time: the span runs earliest to latest.
    times = {s: [datetime.fromisoformat(t.ts) for t in by_stage[s]] for s in stages}
    minutes = {s: (max(times[s]) - min(times[s])).total_seconds() / 60 for s in stages}
    roles = manifest.roles if manifest else {}
    calls = Counter({r: 0 for s in stages for r in ROLES[s] if r in roles and r != "jev"})  # Jev has its own rows
    calls.update(role(t, roles) for t in trace if t.decider == "model" and t.model and not t.cache_hit)
    jev = defaultdict(list)
    for t in trace:
        if t.decider == "jev" and t.model and not t.cache_hit:
            jev[jev_backend(t)].append(t)

    def seconds(lines: list[TraceLine]) -> float:
        return sum(float(m[1]) for t in lines if (m := JEV_SECONDS.search(t.note)))
    out = {"$ total": sum(t.usd for t in trace), **{f"$ {s}": usd[s] for s in stages},
           **{f"minutes {s}": minutes[s] for s in stages}, **{f"model calls {r}": n for r, n in sorted(calls.items())},
           "Jev calls": len(jev["typesafe"]), "Jev seconds": seconds(jev["typesafe"]),
           "Jev adapter calls": len(jev["adapter"]), "Jev adapter seconds": seconds(jev["adapter"])}
    if distinct:
        out["explore $ per distinct screen"] = usd.get("explore", 0.0) / distinct
        if actions is not None:
            out["explore actions per distinct screen"] = sum(a.outcome == "ok" for a in actions) / distinct
    return out


def inventory_coverage(states: Saved | None, inventory: Inventory, device: Device) -> dict:
    """The share of the inventory's screens that some saved state shows every identifying text of."""
    shown = [ob.texts(tree, device) for _, tree in states or []]

    def matched(screen: InventoryScreen) -> bool:
        return any(all(any(text.find(want, t, ignore_case=True) for t in texts) for want in screen.texts)
                   for texts in shown)
    hits = sum(map(matched, inventory.screens))
    return {"inventory screens": len(inventory.screens), "inventory screens matched": hits,
            "inventory coverage": hits / len(inventory.screens)}


def score(run: Path, inventory: Inventory | None = None) -> dict[str, dict]:
    """Every metric of one run, by section; a stage the run lacks leaves its metrics out."""
    trace = read_trace(run / "trace.jsonl")
    explore_dir = run / "explore"
    states = saved_states(explore_dir)
    actions = model.read_actions(explore_dir) if (explore_dir / "actions.jsonl").exists() else None
    explore = load(explore_dir / "explore.json", ExploreFile)
    pm = load(run / "model" / "product_model.json", ProductModel)
    record = explore_record(states, actions, explore)
    sections = {"Explore record": record, "Core action": core_action(trace, actions),
                "Checklist": checklist(explore, pm), "Filter": content_filter(explore, trace),
                "Hard blocks": hard_blocks(states, actions, trace), "Product model": product_model(pm, trace),
                "Downstream": downstream(run), JUDGE: judge(run),
                "Cost and time": cost_and_time(trace, load(run / "manifest.json", Manifest), actions,
                                               record.get("distinct screens"))}
    if inventory:
        sections["Inventory"] = inventory_coverage(states, inventory, explore.device if explore else Device())
    return sections


# ---------- output ----------

def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "/")


def table(scored: list[tuple[str, dict[str, dict]]]) -> str:
    """One row per metric, one column per run, and min and max columns over the runs' numbers when there are two or
    more runs."""
    spread = len(scored) > 1
    head = ["metric", *(cell(label) for label, _ in scored), *(["min", "max"] if spread else [])]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    sections = list(dict.fromkeys(s for _, sections in scored for s in sections))
    for section in sections:
        metrics = list(dict.fromkeys(m for _, sections in scored for m in sections.get(section, {})))
        if not metrics:
            continue
        lines.append(f"| **{section}** |" + " |" * (len(head) - 1))
        for metric in metrics:
            values = [sections.get(section, {}).get(metric) for _, sections in scored]
            numbers = [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
            cells = [metric, *map(cell, values)]
            if spread:
                cells += [cell(min(numbers)), cell(max(numbers))] if numbers else ["", ""]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def label(run: Path) -> str:
    manifest = run / "manifest.json"
    app = json.loads(manifest.read_text()).get("app") if manifest.exists() else None
    return f"{app} {run.name}" if app else run.name


def main(runs: list[Path], inventory: Path | None = None, json_path: Path | None = None) -> int:
    for run in runs:
        if not run.is_dir():
            raise SystemExit(f"not a run folder: {run}")
    if json_path and any(json_path.resolve().is_relative_to(run.resolve()) for run in runs):
        raise SystemExit(f"--json {json_path} is inside a run folder; the scorecard never writes into a run")
    listed = load_inventory(inventory) if inventory else None
    scored = [(label(run), score(run, listed)) for run in runs]
    print(table(scored), end="")
    if json_path:
        json_path.write_text(json.dumps({str(run): sections for run, (_, sections) in zip(runs, scored)}, indent=1))
    return 0
