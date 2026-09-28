"""Goal 3: rewarded-ad candidates. Code builds the lenses, one model call per lens proposes,
code checks every answer, adds the cost line, and ranks by reach."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template

from simula import config, economics, llm
from simula.config import ROOT
from simula.contracts import Candidate, CandidatesFile, Lens, LensesFile, LensOutput, ProductModel
from simula.runfolder import write_json_atomic
from simula.runlog import run_trace, write_exhibit
from simula.stages import Ctx

BIBLE = ROOT / "bible"
PROMPTS = ROOT / "prompts" / "propose"
ALLOWED_INPUTS = (BIBLE, PROMPTS)
MAX_TOKENS = 16000
MAX_CANDIDATES = 10
MAX_LEDGER_LENSES = 2
ANCHOR_MECHANICS = {"paywall", "limit", "currency", "entitlement"}
ANCHOR_LEDGER = {"price", "limit", "meter", "currency", "paywall_bullet"}
CHAT_PLACEMENT = re.compile(
    r"\bin-chat\b|\b(inside|within|into|in) (a|the|this|their|your) (chat|conversation|transcript)\b(?! (list|tab))|"
    r"\bmid[- ]conversation\b|\bbetween (chat )?(messages|replies)\b|\b(chat|message) bubble|\bchat transcript\b",
    re.I)
NEGATED = re.compile(r"\b(not|never|outside|away from)\b[^.;,]{0,20}\b(chat|conversation|transcript)", re.I)
SAFE_TRIGGER_RATINGS = {"safe", "mixed"}
# The assignment's two buckets, shown first on every candidate's title.
BUCKETS = {"existing_anchor": "Existing opportunity", "product_change": "Product change"}
FREE_OFFER = re.compile(r"\bfree\b[^.;,]{0,20}\btrial\b|\btrial\b[^.;,]{0,20}\bfree\b", re.I)

FIXED_LENSES = [
    Lens(id="free_at_limit", name="Free user at a limit", kind="fixed", ledger_ids=[],
         focus="A free user who has just hit a limit, a paywall, or a locked item, or would if the app had one. "
               "What do they want right now that a rewarded play could give them, partially and for a while?"),
    Lens(id="subscriber", name="Subscriber", kind="fixed", ledger_ids=[],
         focus="A paying user. What do they still lack or want more of that an opt-in rewarded play could add "
               "without devaluing what they pay for? If the app has no subscription, take its most engaged user."),
    Lens(id="drifting", name="Drifting user", kind="fixed", ledger_ids=[],
         focus="A user whose visits are thinning out. What would bring them back or protect progress they "
               "would lose, through a rewarded play at a natural moment rather than an interruption?"),
]


def read_input(path: Path) -> str:
    """The proposer's input allow-list: the bible and this stage's prompts. Nothing about the test apps
    from the web, and no reference deck, can reach a prompt."""
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root.resolve()) for root in ALLOWED_INPUTS):
        raise PermissionError(f"propose may not read {path}: not on the input allow-list")
    return resolved.read_text()


# ---------- lenses ----------

def build_lenses(model: ProductModel) -> list[Lens]:
    actors = [item for item in model.value_ledger if item.kind == "actor"][:MAX_LEDGER_LENSES]
    return FIXED_LENSES + [
        Lens(id=f"ledger_{item.id}", name=f"Someone else in the app: {item.verbatim}", kind="ledger",
             ledger_ids=[item.id],
             focus=f'Someone in this app besides the viewer, seen as "{item.verbatim}". Look for ideas where '
                   "the viewer's rewarded play also helps or involves them, and the viewer gets something too.")
        for item in actors]


# ---------- prompts ----------

def model_text(model: ProductModel) -> str:
    edges = {e.id: e for e in model.edges}
    lines = [f"App category: {model.app_category}", "", "### Mechanics"]
    lines += [f"- {m.id} [{m.kind}, {m.status}] {m.summary} Evidence: {', '.join(m.evidence_ids) or 'none'}."
              + (f" Numbers seen: {', '.join(m.observed_numbers)}." if m.observed_numbers else "")
              for m in model.mechanics] or ["- none observed"]
    lines += ["", "### Value ledger (verbatim app text)"]
    lines += [f'- {i.id} [{i.kind}] "{i.verbatim}" Evidence: {", ".join(i.evidence_ids)}.'
              for i in model.value_ledger] or ["- empty"]
    if model.cross_screen_values:
        lines += ["", "### Values shown on several screens"]
        lines += [f'- {v.label}: "{v.value_text}" ({", ".join(v.evidence_ids)})' for v in model.cross_screen_values]
    lines += ["", "### Core flows"]
    for f in model.flows:
        path = [edges[i].from_state for i in f.edge_ids[:1]] + [edges[i].to_state for i in f.edge_ids]
        lines.append(f"- {f.id} {f.name}: {f.purpose} ({' -> '.join(path)})")
    lines += ["", "### Open questions (not observed)"] + [f"- {q}" for q in model.open_questions]
    lines += ["", "### Screens in scope"]
    for s in model.states:
        if s.in_mock_scope:
            lines += ["", *state_text(s)]
    others = [f"- {s.id} {s.name} ({s.kind})" for s in model.states if not s.in_mock_scope]
    if others:
        lines += ["", "### Other screens (seen, not in scope)", *others]
    return "\n".join(lines)


def state_text(state) -> list[str]:
    over = f", over {state.parent_id}" if state.parent_id else ""
    lines = [f"#### {state.id} {state.name} ({state.kind}{over}; content {state.content_rating})", state.purpose]
    named = [e for e in state.elements if e.text or e.label]
    lines += [f"- {e.id} {e.role}: {' / '.join(t for t in (e.text, e.label) if t)}" for e in named]
    return lines if named else lines + ["- (no element list captured for this screen)"]


def system_prompt() -> str:
    return "\n\n".join([read_input(PROMPTS / "system.md"),
                        "# The rewarded-ad bible\n\n" + read_input(BIBLE / "BIBLE.md"),
                        "# Cost table (bible/data/economics.json)\n\n" + read_input(BIBLE / "data" / "economics.json")])


def lens_prompt(model: ProductModel, lens: Lens) -> str:
    return Template(read_input(PROMPTS / "lens.md")).substitute(
        lens_id=lens.id, lens_name=lens.name, lens_focus=lens.focus, product_model=model_text(model))


def ask_lens(ctx: Ctx, model: ProductModel, lens: Lens, system: str, budget: llm.Budget,
             step: str | None = None) -> list[Candidate] | None:
    """The lens's drafts, or None when its call failed twice."""
    role = config.roles(ctx.profile)["proposer"]
    step = step or f"lens:{lens.id}"
    messages = [{"role": "user", "content": [{"type": "text", "text": lens_prompt(model, lens)}]}]
    try:
        output, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="propose", step=step,
                             model=role["model"], effort=role.get("effort"), system=system, messages=messages,
                             max_tokens=min(MAX_TOKENS, config.models()[role["model"]]["max_out"]), budget=budget,
                             schema=LensOutput, no_cache=ctx.no_cache, replay=ctx.replay)
    except llm.LLMFailure as e:
        run_trace(ctx.run_dir, stage="propose", step=step, decider="code", outcome=e.outcome,
                  note="lens skipped after its retry failed")
        return None
    return [Candidate(**{**draft.model_dump(), "lens": lens.id}) for draft in output.candidates[:2]]


# ---------- code checks ----------

def plain(text: str) -> str:
    """Every run of whitespace, non-breaking spaces included, as one space (the rule PR 2's model stage uses)."""
    return " ".join(text.split())


def anchor_ids(model: ProductModel) -> set[str]:
    return ({i for m in model.mechanics if m.kind in ANCHOR_MECHANICS for i in m.evidence_ids}
            | {i for item in model.value_ledger if item.kind in ANCHOR_LEDGER for i in item.evidence_ids})


def mechanic_ids() -> set[str]:
    return {m["id"] for m in json.loads(read_input(BIBLE / "data" / "mechanics.json"))["mechanics"]}


def resolve_ids(c: Candidate, model: ProductModel) -> tuple[Candidate, str]:
    """Models cite a mechanic or ledger id where an element id belongs, or an element where a state belongs.
    Both point at something real, so code maps them to the ids it checks. Returns the candidate and a note
    listing every repair ("" when none)."""
    groups = {m.id: m.evidence_ids for m in model.mechanics} | {i.id: i.evidence_ids for i in model.value_ledger}
    repairs = [f"{i} -> {','.join(groups[i])}" for i in c.anchor_evidence_ids if i in groups]
    anchors = list(dict.fromkeys(e for i in c.anchor_evidence_ids for e in groups.get(i, [i])))

    def screen(state_id: str) -> str:
        if state_id.startswith("new:") or "." not in state_id:
            return state_id
        repairs.append(f"{state_id} -> {state_id.split('.')[0]}")
        return state_id.split(".")[0]

    trigger = screen(c.trigger_state_id)
    steps = [s.model_copy(update={"state_id": screen(s.state_id)}) for s in c.flow_steps]
    fixed = c.model_copy(update={"anchor_evidence_ids": anchors, "trigger_state_id": trigger, "flow_steps": steps})
    return fixed, "; ".join(dict.fromkeys(repairs))


def in_chat(placement: str) -> bool:
    # ponytail: keyword check per clause, skipping a clause whose negation sits right before the chat word
    # ("never shown in a chat"); recall belongs to the judge's brand-safety gate
    clauses = re.split(r"[.;,()]", plain(placement))
    return any(CHAT_PLACEMENT.search(c) and not NEGATED.search(c) for c in clauses)


def free_trial(model: ProductModel, screens: set[str]) -> str | None:
    """The first free-trial text shown on any of these screens."""
    texts = (plain(" ".join(t for t in (e.text, e.label) if t))
             for s in model.states if s.id in screens for e in s.elements)
    return next((t for t in texts if FREE_OFFER.search(t)), None)


def observed_limit(model: ProductModel, evidence_ids: list[str]) -> bool:
    return any(m.kind == "limit" and m.status == "observed" and set(m.evidence_ids) & set(evidence_ids)
               for m in model.mechanics)


def grants_problem(c: Candidate, model: ProductModel) -> str | None:
    """Why the paid benefit the reward is a piece of rules the idea out, or None: the free trial on the benefit's
    screen already gives it to non-payers, or payers would get more of an amount nobody saw."""
    if not c.grants_id:
        return None
    benefit = next((i for i in model.value_ledger if i.id == c.grants_id), None)
    if benefit is None:
        return f"grants_id {c.grants_id!r} is not a ledger id"
    if benefit.kind != "paywall_bullet":
        return None
    bullet = plain(benefit.verbatim)
    trial = free_trial(model, {i.split(".")[0] for i in benefit.evidence_ids})
    if trial and c.for_users != "paying":
        return f'a piece of "{bullet}", which the free trial on that screen already gives: "{trial}"'
    if c.for_users == "paying" and not re.search(r"\d", bullet) and not observed_limit(model, benefit.evidence_ids):
        return f'gives payers more of "{bullet}", but no amount or cap for it was observed'
    return None


def check(c: Candidate, model: ProductModel) -> str | None:
    """Returns why a candidate is dropped, or None when it passes every code check."""
    states = {s.id: s for s in model.states}
    elements = {e.id for s in model.states for e in s.elements}
    unknown = [i for i in c.anchor_evidence_ids if i not in elements]
    existing_steps = [s.state_id for s in c.flow_steps if not s.state_id.startswith("new:")]
    bad_steps = [i for i in existing_steps if i not in states]
    if unknown:
        return f"cites evidence ids that don't exist: {', '.join(unknown)}"
    if c.trigger_state_id not in states:
        return f"trigger state {c.trigger_state_id!r} doesn't exist"
    if bad_steps or not c.flow_steps:
        return f"flow steps name states that don't exist: {', '.join(bad_steps) or 'no steps'}"
    outside = list(dict.fromkeys(i for i in [c.trigger_state_id, *existing_steps] if not states[i].in_mock_scope))
    if outside:
        return f"names screens outside the mock scope, which the slides can't draw: {', '.join(outside)}"
    trigger = states[c.trigger_state_id]
    if trigger.content_rating not in SAFE_TRIGGER_RATINGS:
        return f"trigger screen {trigger.id} has {trigger.content_rating} content; the offer can't render next to it"
    if c.bible_mechanic.strip().lower() != "none" and c.bible_mechanic not in mechanic_ids():
        return f"bible_mechanic {c.bible_mechanic!r} is not an M-id in the bible"
    if c.kind == "existing_anchor" and not set(c.anchor_evidence_ids) & anchor_ids(model):
        return "existing_anchor cites no paywall, limit, currency, or entitlement element"
    if c.kind == "product_change" and not (c.adds or "").strip():
        return "product_change doesn't say what it adds"
    if c.kind == "product_change" and not c.removes_nothing_free:
        return "product_change removes or caps something free"
    if problem := economics.input_problem(c):
        return problem
    if not c.after_reward.strip():
        return "doesn't say what the user sees when the reward runs out"
    if problem := grants_problem(c, model):
        return problem
    if in_chat(c.placement):
        return "placement is inside a chat transcript, not app chrome"
    return None


# ---------- reach and rank ----------

def depths(model: ProductModel) -> dict[str, int]:
    """Taps from the root (the lowest-numbered `screen` state, docs/CONTRACTS.md). A tab switch costs nothing;
    a modal sits at its parent's depth when no recorded edge reaches it."""
    depth = {min(s.id for s in model.states if s.kind == "screen"): 0}
    changed = True
    while changed:
        changed = False
        for e in model.edges:
            if e.from_state in depth:
                d = depth[e.from_state] + (e.transition != "tab")
                if d < depth.get(e.to_state, d + 1):
                    depth[e.to_state] = d
                    changed = True
    for s in model.states:
        if s.id not in depth:
            depth[s.id] = depth.get(s.parent_id, 2)
    return depth


def daily_cap(frequency_cap: str) -> int:
    # ponytail: reads "N per day" (or "N a day", "N/day", "N times a day") out of free text, else 1; durations
    # and clock times ("every 24 hours", "resets at 00:00") are ignored. A structured cap field would fix it.
    match = re.search(r"(\d+)\s*(?:x\s*|times\s*)?(?:per|a|/|each)\s*day", plain(frequency_cap), re.I)
    return max(1, int(match.group(1))) if match else 1


def rank(c: Candidate, model: ProductModel, mode: str) -> Candidate:
    """reach = eligible users (by the trigger's depth) x daily views: a scenario, not a measured audience."""
    if c.dropped_reason or c.kind == "no_opportunity":
        return c
    weight = {0: 1.0, 1: 0.5}.get(depths(model)[c.trigger_state_id], 0.25)
    reach = weight * daily_cap(c.frequency_cap)
    score = reach
    if mode == "gate":
        score = reach * (c.economics.benchmark_ecpm - c.economics.breakeven_ecpm_2k) / 1000
    return c.model_copy(update={"reach_score": reach, "rank_score": round(score, 6)})


def reward_key(c: Candidate) -> str:
    """What dedupe compares: the paid benefit and who gets it when the idea names one, else the reward's unit."""
    if c.grants_id:
        return f"{c.grants_id} for {c.for_users} users"
    # ponytail: the unit's words, case-folded, each with a plural "s" stripped; synonyms ("badge" vs
    # "checkmark") slip through. The prompt asks for the app's own word so one benefit gets one unit.
    return " ".join(word.removesuffix("s") for word in plain(c.reward.unit).casefold().split())


def dedupe(ranked: list[Candidate]) -> list[Candidate]:
    """Takes live candidates best first. One that gives the same reward as a better-ranked one (see reward_key)
    is dropped as its duplicate, whatever its trigger."""
    out = []
    for c in ranked:
        twin = next((k for k in out if reward_key(c) and reward_key(k) == reward_key(c)), None)
        reason = f"duplicate of {twin.id}: same reward ({reward_key(twin)})" if twin else None
        out.append(c.model_copy(update={"dropped_reason": reason}))
    return out


def with_bucket(c: Candidate) -> Candidate:
    if c.kind not in BUCKETS:
        return c
    return c.model_copy(update={"title": f"{BUCKETS[c.kind]}: {c.title}"})


def finish(drafts: list[Candidate], model: ProductModel, mode: str) -> tuple[list[Candidate], dict[str, str]]:
    """Numbers the drafts, repairs near-miss ids, checks, prices, and ranks them. Returns the candidates (live
    first, best first; dropped ones kept with their reason) and the id repairs by candidate id."""
    checked, repairs = [], {}
    for n, draft in enumerate(drafts, 1):
        c, repaired = resolve_ids(draft.model_copy(update={"id": f"c{n:02d}"}), model)
        if repaired:
            repairs[c.id] = repaired
        reason = f"no opportunity: {c.rationale}" if c.kind == "no_opportunity" else check(c, model)
        checked.append(c.model_copy(update={"dropped_reason": reason}))
    ranked = [rank(c, model, mode) for c in economics.apply(checked, model.app_category, mode)]
    deduped = dedupe(sorted((c for c in ranked if not c.dropped_reason), key=lambda c: -c.rank_score))
    live = [c for c in deduped if not c.dropped_reason]
    over = [c.model_copy(update={"dropped_reason": f"over the {MAX_CANDIDATES}-candidate cap"})
            for c in live[MAX_CANDIDATES:]]
    dropped = [c for c in deduped + ranked if c.dropped_reason]
    return [with_bucket(c) for c in live[:MAX_CANDIDATES] + over + dropped], repairs


# ---------- stage ----------

def exhibit(lenses: list[Lens], candidates: list[Candidate], repairs: dict[str, str]) -> str:
    live = [c for c in candidates if not c.dropped_reason]
    lines = ["# 05 · propose", "", f"{len(lenses)} lenses, {len(live)} live candidates, "
             f"{len(candidates) - len(live)} dropped, {len(repairs)} with near-miss ids repaired by code "
             f"(`resolve:<id>` lines in trace.jsonl).", "", "| Lens | Kind | Focus |", "|---|---|---|"]
    lines += [f"| {l.name} | {l.kind} | {l.focus} |" for l in lenses]
    lines += ["", "## Candidates, by reach"]
    for c in live:
        lines += ["", f"### {c.id} · {c.title}",
                  f"- {c.kind}, lens `{c.lens}`, trigger on `{c.trigger_state_id}`: {c.trigger_event}",
                  f"- Offer: {c.offer_copy}",
                  f"- Reward: {c.reward.amount:g} {c.reward.unit} ({c.reward.kind}, {c.reward.duration})",
                  f"- When the reward ends: {c.after_reward}",
                  f"- Cost: {c.economics.assumption_line}",
                  f"- Reach scenario: {c.reach_score:g} (trigger depth x daily cap; not a measured audience)"]
    dropped = [c for c in candidates if c.dropped_reason]
    if dropped:
        lines += ["", "## Dropped by code", ""] + [f"- {c.id} · {c.title}: {c.dropped_reason}" for c in dropped]
    return "\n".join(lines) + "\n"


def run(ctx: Ctx) -> None:
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    out = ctx.run_dir / "propose"
    lenses = build_lenses(model)
    write_json_atomic(out / "lenses.json", LensesFile(lenses=lenses).model_dump_json(indent=1))
    budget = llm.Budget.for_stage("propose", ctx.run_dir / "trace.jsonl", ctx.usd_cap)
    system = system_prompt()
    with ThreadPoolExecutor(max_workers=len(lenses)) as pool:
        answers = list(pool.map(lambda lens: ask_lens(ctx, model, lens, system, budget), lenses))
    if all(a is None for a in answers):
        raise RuntimeError("every lens call failed; see trace.jsonl")
    drafts = [c for a in answers if a for c in a]
    candidates, repairs = finish(drafts, model, config.profiles()["economics_mode"])
    for cid, note in repairs.items():
        run_trace(ctx.run_dir, stage="propose", step=f"resolve:{cid}", decider="code", note=note[:300])
    for c in candidates:
        if c.dropped_reason:
            run_trace(ctx.run_dir, stage="propose", step=f"check:{c.id}", decider="code", outcome="denied",
                      note=c.dropped_reason[:300])
    write_json_atomic(out / "candidates.json", CandidatesFile(candidates=candidates).model_dump_json(indent=1))
    write_exhibit(ctx.run_dir, 5, "propose", exhibit(lenses, candidates, repairs))
