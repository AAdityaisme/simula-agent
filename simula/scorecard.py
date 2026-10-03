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

from simula import config, text
from simula.contracts import (ActionLine, CandidatesFile, ContractReport, Decision, DecisionsFile, Device,
                              Element, ExploreFile, Manifest, MechanicKind, ProductModel, QAReport, Rect, State,
                              StateFile, Strict, TraceLine)
from simula.device import observe as ob
from simula.runlog import read_trace
from simula.stages import ROLES, model

BILLING = "com.android.vending"  # the Play Store, whose payment sheet is a hard block for every explorer
LIST_ITEMS = 3
NUMBER = re.compile(r"\d+")
SETTINGS = re.compile(r"\bsettings?\b", re.IGNORECASE)
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
    """Moves recorded as staying on their state while the screen changed: a screen the record never saved. A
    core-loop pass and typing change the screen they stay on by design, so they never count."""
    return [a for a in actions if a.to_state == a.from_state and a.change_summary and a.loop_pass is None
            and a.action != "type"]


def completed(lines: list[ActionLine]) -> bool:
    """A core pass whose result showed: what stopped the loop, or a measurement that isn't a wait for nothing
    ('no reply within 45 s')."""
    measured = model.pass_measurements([a for a in lines if a.outcome == "ok"])
    return any(a.loop_stop for a in lines) or any(not what.startswith("no ") for what, _, _ in measured)


# ---------- the explorer-independent checklist ----------

def tab_bar(pm: ProductModel) -> list[Rect]:
    """The root's bottom tab bar: the biggest row of 2 or more same-class, same-size elements in its bottom band, each
    with a name (text or label), evenly spaced (within one 8 dp bucket) across the middle of the screen, whose names
    all show in the bottom band of another saved screen too, since navigation stays put. A composer's buttons are
    never evenly spaced, nor named on their outer boxes, and a screen's own pair of buttons shows on it alone."""
    device, band = pm.device, pm.device.content_bottom_px - ob.TAB_BAND_PX
    screens = [s for s in pm.states if s.kind == "screen"]
    if not screens:
        return []

    def named(state: State) -> list[Element]:
        return [e for e in state.elements if e.rect_px.y >= band and (e.text or e.label)]
    elsewhere = [{e.text or e.label for e in named(s)} for s in screens[1:]]
    rows = defaultdict(dict)
    for e in named(screens[0]):
        r = e.rect_px
        rows[e.type, ob.bucket(r.w, device), ob.bucket(r.h, device), ob.bucket(r.y, device)][ob.bucket(r.x, device)] = e

    def bar(row: list[Element]) -> bool:
        xs = sorted(e.rect_px.x + e.rect_px.w / 2 for e in row)
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        return (bool(gaps) and xs[0] < device.w_px / 2 < xs[-1] and max(gaps) - min(gaps) <= ob.BUCKET_DP * device.scale
                and any({e.text or e.label for e in row} <= names for names in elsewhere))
    found = [list(row.values()) for row in rows.values()]
    return [e.rect_px for e in max(filter(bar, found), key=len, default=[])]


def all_tabs(pm: ProductModel) -> bool:
    """Every tab of the root's bar but the one the root already shows has a recorded tap that went to a state other
    than its own and the root's: a tap in the bottom band within the tab's column (a tab's label often sits under its
    tap target). A tap back to the root's tab never counts."""
    tabs = tab_bar(pm)
    if not tabs:
        return True
    root = next(s.id for s in pm.states if s.kind == "screen")
    band = pm.device.content_bottom_px - ob.TAB_BAND_PX
    elements = {e.id: e.rect_px for s in pm.states for e in s.elements}
    taps = [ob.center(elements[e.element_id]) for e in pm.edges
            if e.element_id in elements and e.to_state not in (e.from_state, root)]
    reached = sum(any(tab.x <= x < tab.x + tab.w and y >= band for x, y in taps) for tab in tabs)
    return reached >= len(tabs) - 1


def model_checklist(pm: ProductModel) -> dict[str, bool]:
    """explore's checklist items, answered from the product model alone."""
    observed = {m.kind for m in pm.mechanics if m.status == "observed"}
    ledger = {item.kind for item in pm.value_ledger}
    found = {"root": any(s.kind == "screen" for s in pm.states),
             "all_tabs": all_tabs(pm),
             "paywall_or_membership": "paywall" in observed or bool({"price", "paywall_bullet"} & ledger),
             "settings": any(SETTINGS.search(s.name) for s in pm.states),  # a purpose names it on every menu listing it
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
        out |= {"answered by the product model": sum(answered.values()),
                "open in the product model": ", ".join(i for i, ok in answered.items() if not ok) or "none"}
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
        out |= {"typed actions": sum(a.action == "type" and a.outcome == "ok" for a in actions),
                "sent actions (a tap right after typing)": sum(
                    b.action == "type" and a.action == "tap" for b, a in zip(actions, actions[1:])
                    if a.outcome == b.outcome == "ok")}
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
        out |= {"mock contract passed": contract.passed, "mock screens drawn": len(contract.screens)}
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
    """The stage's role whose configured model made the call (the one the step names, when several share the model),
    or the stage and model when that leaves none or several."""
    named = [r for r in ROLES.get(line.stage, []) if line.model in roles.get(r, "").split()]
    named = [r for r in named if r in line.step] or named if len(named) > 1 else named
    return named[0] if len(named) == 1 else f"{line.stage} {line.model}"


def cost_and_time(trace: list[TraceLine], manifest: Manifest | None, actions: list[ActionLine] | None,
                  distinct: int | None) -> dict:
    if not trace:
        return {}
    by_stage = defaultdict(list)
    for t in trace:
        by_stage[t.stage].append(t)
    stages = [s for s in config.STAGES if s in by_stage]
    usd = {s: sum(t.usd for t in by_stage[s]) for s in stages}
    minutes = {s: (datetime.fromisoformat(by_stage[s][-1].ts) - datetime.fromisoformat(by_stage[s][0].ts))
               .total_seconds() / 60 for s in stages}
    roles = manifest.roles if manifest else {}
    calls = Counter({r: 0 for s in stages for r in ROLES[s] if r in roles and r != "jev"})  # Jev has its own rows
    calls.update(role(t, roles) for t in trace if t.decider == "model" and t.model and not t.cache_hit)
    jev = [t for t in trace if t.decider == "jev" and t.model and not t.cache_hit]
    seconds = [float(m[1]) for t in jev if (m := JEV_SECONDS.search(t.note))]
    out = {"$ total": sum(t.usd for t in trace), **{f"$ {s}": usd[s] for s in stages},
           **{f"minutes {s}": minutes[s] for s in stages}, **{f"model calls {r}": n for r, n in sorted(calls.items())},
           "Jev calls": len(jev), "Jev seconds": sum(seconds)}
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
