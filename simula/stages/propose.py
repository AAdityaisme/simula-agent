"""Goal 3: rewarded-ad candidates. Code builds the lenses, one model call per lens proposes,
code checks every answer, adds the cost line, and ranks by reach."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template

from simula import config, economics, llm
from simula.config import ROOT
from simula.contracts import (BenefitNames, Candidate, CandidatesFile, LedgerItem, Lens, LensesFile, LensOutput,
                              ProductModel)
from simula.runfolder import write_json_atomic
from simula.runlog import run_trace, write_exhibit
from simula.stages import Ctx

BIBLE = ROOT / "bible"
PROMPTS = ROOT / "prompts" / "propose"
ALLOWED_INPUTS = (BIBLE, PROMPTS)
MAX_TOKENS = 16000
MAX_CANDIDATES = 10
NAMING_MAX_TOKENS = 4000
MIN_DISTINCT = 4
MAX_LEDGER_LENSES = 2
ANCHOR_MECHANICS = {"paywall", "limit", "currency", "entitlement"}
ANCHOR_LEDGER = {"price", "limit", "meter", "currency", "paywall_bullet"}
CHAT_PLACEMENT = re.compile(
    r"\bin-chat\b|\b(inside|within|into|in)\s+(a|the|this|their|your)\s+(chat|conversation|transcript)\b"
    r"(?!\s+(list|tab))|\bmid(-|\s+)conversation\b|\bbetween\s+(chat\s+)?(messages|replies)\b|"
    r"\b(chat|message)\s+bubble|\bchat\s+transcript\b",
    re.I)
NEGATED = re.compile(r"\b(not|never|outside|away\s+from)\b[^.;,]{0,20}\b(chat|conversation|transcript)", re.I)
SAFE_TRIGGER_RATINGS = {"safe", "mixed"}
# The assignment's two buckets, shown first on every candidate's title.
BUCKETS = {"existing_anchor": "Existing opportunity", "product_change": "Product change"}
OUTSIDE_MOCK = "names screens outside the mock scope"

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
    if unobserved := unobserved_terms(model):
        lines += ["", "### App terms whose meaning was never observed (don't use them in a title, offer_copy, or "
                      "after_reward; code drops an idea that does)"] + [f'- "{t}"' for t in unobserved]
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

def unobserved_terms(model: ProductModel) -> list[str]:
    return [t.term for t in model.terms if not t.observed]


def uses_term(term: str, words: str) -> bool:
    """`term` as a whole word or phrase in `words`, ignoring case; any whitespace run matches any other, as in
    simula.text.find. Boundaries are "no word character next to it", so a term ending in "+" still matches."""
    parts = term.split()
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(p) for p in parts) + r"(?!\w)"
    return bool(parts) and re.search(pattern, words, re.IGNORECASE) is not None


def jargon(c: Candidate, model: ProductModel) -> str | None:
    return next((f'uses "{term}", whose meaning was never observed' for term in unobserved_terms(model)
                 if any(uses_term(term, words) for words in (c.title, c.offer_copy, c.after_reward))), None)


def anchor_ids(model: ProductModel) -> set[str]:
    return ({i for m in model.mechanics if m.kind in ANCHOR_MECHANICS for i in m.evidence_ids}
            | {i for item in model.value_ledger if item.kind in ANCHOR_LEDGER for i in item.evidence_ids})


def mechanic_ids() -> set[str]:
    return {m["id"] for m in json.loads(read_input(BIBLE / "data" / "mechanics.json"))["mechanics"]}


def resolve_ids(c: Candidate, model: ProductModel) -> tuple[Candidate, str]:
    """Models cite a mechanic or ledger id where an element id belongs, or an element where a state belongs.
    Both point at something real, so code maps them to the ids it checks. An `experience` item is a measured
    fact whose evidence is edges, not elements, so it maps to nothing: context, not an anchor. Returns the
    candidate and a note listing every repair ("" when none)."""
    groups = ({m.id: m.evidence_ids for m in model.mechanics}
              | {i.id: [] if i.kind == "experience" else i.evidence_ids for i in model.value_ledger})
    repairs = [f"{i} -> {','.join(groups[i]) or 'nothing (a measured experience, not an element)'}"
               for i in c.anchor_evidence_ids if i in groups]
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
    clauses = re.split(r"[.;,()]", placement)
    return any(CHAT_PLACEMENT.search(c) and not NEGATED.search(c) for c in clauses)


def observed_limit(model: ProductModel, evidence_ids: list[str]) -> bool:
    return any(m.kind == "limit" and m.status == "observed" and set(m.evidence_ids) & set(evidence_ids)
               for m in model.mechanics)


def grants_problem(c: Candidate, model: ProductModel) -> str | None:
    """Why the paid benefit the reward is a piece of rules the idea out, or None: payers would get more of an
    amount nobody saw."""
    if not c.grants_id:
        return None
    benefit = next((i for i in model.value_ledger if i.id == c.grants_id), None)
    if benefit is None:
        return f"grants_id {c.grants_id!r} is not a ledger id"
    problem = uncounted_problem(c, benefit, model)
    return f"{problem} (linked by the proposer's grants_id)" if problem else None


def uncounted_problem(c: Candidate, benefit: LedgerItem, model: ProductModel) -> str | None:
    bullet = benefit.verbatim
    if benefit.kind != "paywall_bullet" or c.for_users != "paying":
        return None
    if re.search(r"\d", bullet) or observed_limit(model, benefit.evidence_ids):
        return None
    return f'gives payers more of "{bullet}", but no amount or cap for it was observed'


def linked_problem(c: Candidate, bullet_id: str | None, model: ProductModel) -> str | None:
    """The uncounted-benefit check on the paywall bullet the naming call says the idea's benefit is part of, when
    the proposer's grants_id didn't already name it."""
    bullet = next((i for i in model.value_ledger if i.id == bullet_id and i.kind == "paywall_bullet"), None)
    problem = bullet and bullet.id != c.grants_id and uncounted_problem(c, bullet, model)
    return f"{problem} (linked by the benefit-naming call)" if problem else None


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
        return f"{OUTSIDE_MOCK}, which the slides can't draw: {', '.join(outside)}"
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
    if problem := jargon(c, model):
        return problem
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
    match = re.search(r"(\d+)\s*(?:x\s*|times\s*)?(?:per|a|/|each)\s*day", frequency_cap, re.I)
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


def ideas_text(live: list[Candidate]) -> str:
    lines = []
    for c in live:
        head = f"{c.id} | for: {c.for_users}" + (f" | piece of paid benefit {c.grants_id}" if c.grants_id else "")
        lines += [head, f"  title: {c.title}", f"  offer: {c.offer_copy}",
                  f"  reward: {c.reward.amount:g} {c.reward.unit} ({c.reward.kind}, {c.reward.duration})", ""]
    return "\n".join(lines)


def name_benefits(ctx: Ctx, live: list[Candidate], budget: llm.Budget, step: str,
                  model: ProductModel) -> tuple[dict[str, str], dict[str, str]]:
    """One model call names what each live idea gives the user, in a few words, so code can pair ideas that give
    the same thing, and says which paywall bullet (if any) that benefit is part of. Returns names and bullet links
    by idea id, both empty when there is nothing to name or the call failed (dedupe then uses grants_id alone)."""
    if not live:
        return {}, {}
    role = config.roles(ctx.profile)["propose_dedupe"]
    paid = [f'- {i.id}: "{i.verbatim}"' for i in model.value_ledger if i.kind == "paywall_bullet"]
    prompt = Template(read_input(PROMPTS / "dedupe.md")).substitute(paid="\n".join(paid) or "- none",
                                                                   ideas=ideas_text(live))
    try:
        output, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="propose", step=step,
                             model=role["model"], effort=role.get("effort"), system="",
                             messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                             max_tokens=NAMING_MAX_TOKENS, budget=budget, schema=BenefitNames,
                             no_cache=ctx.no_cache, replay=ctx.replay)
    except llm.LLMFailure as e:
        run_trace(ctx.run_dir, stage="propose", step=step, decider="code", outcome=e.outcome,
                  note="benefit naming failed; dedupe used grants_id alone")
        return {}, {}
    ideas = [i for i in output.ideas if i.id in {c.id for c in live}]
    return {i.id: i.benefit for i in ideas}, {i.id: i.part_of for i in ideas if i.part_of}


def fold(name: str) -> str:
    return " ".join(word.removesuffix("s") for word in name.casefold().replace("-", " ").split())


def users_overlap(a: str, b: str) -> bool:
    return a == b or "everyone" in (a, b)


def same_benefit(a: Candidate, b: Candidate, names: dict[str, str]) -> str | None:
    """What a and b both give, or None. The same paid benefit for the same users always counts; otherwise the
    two need the same benefit name and overlapping users (free and paying never overlap)."""
    if a.grants_id and (a.grants_id, a.for_users) == (b.grants_id, b.for_users):
        return names.get(a.id) or f"{a.grants_id} for {a.for_users} users"
    name = fold(names.get(a.id, ""))
    if name and name == fold(names.get(b.id, "")) and users_overlap(a.for_users, b.for_users):
        return names[a.id]
    return None


def dedupe(ranked: list[Candidate], names: dict[str, str]) -> list[Candidate]:
    """Takes live candidates best first. One that gives the same benefit as a better-ranked kept one is dropped
    as its duplicate, whatever its trigger."""
    out = []
    for c in ranked:
        kept = (k for k in out if not k.dropped_reason)
        twin, benefit = next(((k, b) for k in kept if (b := same_benefit(k, c, names))), (None, None))
        reason = f"duplicate of {twin.id}: same benefit ({benefit})" if twin else None
        out.append(c.model_copy(update={"dropped_reason": reason}))
    return out


def with_bucket(c: Candidate) -> Candidate:
    if c.kind not in BUCKETS:
        return c
    return c.model_copy(update={"title": f"{BUCKETS[c.kind]}: {c.title}"})


def finish(drafts: list[Candidate], model: ProductModel, mode: str, name=lambda live: ({}, {})
           ) -> tuple[list[Candidate], dict[str, str], dict[str, str]]:
    """Numbers the drafts, repairs near-miss ids, checks, prices, ranks, and dedupes them (`name` names the live
    ones' benefits and links them to paywall bullets). Returns the candidates (live first, best first; dropped ones kept with their reason), the id
    repairs by candidate id, and the benefit names."""
    checked, repairs = [], {}
    for n, draft in enumerate(drafts, 1):
        c, repaired = resolve_ids(draft.model_copy(update={"id": f"c{n:02d}"}), model)
        if repaired:
            repairs[c.id] = repaired
        reason = f"no opportunity: {c.rationale}" if c.kind == "no_opportunity" else check(c, model)
        checked.append(c.model_copy(update={"dropped_reason": reason}))
    ranked = [rank(c, model, mode) for c in economics.apply(checked, model.app_category, mode)]
    passing = sorted((c for c in ranked if not c.dropped_reason), key=lambda c: -c.rank_score)
    names, links = name(passing)
    passing = [c.model_copy(update={"dropped_reason": linked_problem(c, links.get(c.id), model)}) for c in passing]
    deduped = dedupe([c for c in passing if not c.dropped_reason], names)
    live = [c for c in deduped if not c.dropped_reason]
    over = [c.model_copy(update={"dropped_reason": f"over the {MAX_CANDIDATES}-candidate cap"})
            for c in live[MAX_CANDIDATES:]]
    dropped = [c for c in deduped + passing + ranked if c.dropped_reason]
    return [with_bucket(c) for c in live[:MAX_CANDIDATES] + over + dropped], repairs, names


# ---------- top-up ----------

def topup_lens(live: list[Candidate], names: dict[str, str]) -> Lens:
    given = "; ".join(dict.fromkeys(names.get(c.id) or c.reward.unit for c in live)) or "nothing yet"
    return Lens(id="topup", name="A different benefit", kind="fixed", ledger_ids=[],
                focus=f"Any user of this app. The ideas kept so far give: {given}. Propose ideas that give the user "
                      "something different from every one of these: a different benefit, not the same one at "
                      "another moment, amount, or duration.")


def mock_coverage(candidates: list[Candidate], model: ProductModel) -> list[str]:
    """Exhibit lines: how many proposed ideas start on a screen the mock draws, counted before the mock-scope
    filter drops any, and the ideas that filter dropped."""
    ideas = [c for c in candidates if c.kind != "no_opportunity"]
    states = {s.id: s for s in model.states}
    drawn = sum(c.trigger_state_id in states and states[c.trigger_state_id].in_mock_scope for c in ideas)
    off = [c for c in ideas if (c.dropped_reason or "").startswith(OUTSIDE_MOCK)]
    return ([f"- Mock coverage: {drawn} of {len(ideas)} ideas start on a screen the mock draws (counted before the "
             f"mock-scope filter); the filter dropped {len(off)}" + (":" if off else ".")]
            + [f"  - {c.id} · {c.title.removeprefix(f'{BUCKETS[c.kind]}: ')} · trigger {c.trigger_state_id} "
               f"{states[c.trigger_state_id].name}" for c in off])


# ---------- stage ----------

def exhibit(lenses: list[Lens], candidates: list[Candidate], repairs: dict[str, str], model: ProductModel,
            topup: str) -> str:
    live = [c for c in candidates if not c.dropped_reason]
    lines = ["# 05 · propose", "", f"{len(lenses)} lenses, {len(live)} live candidates, "
             f"{len(candidates) - len(live)} dropped, {len(repairs)} with near-miss ids repaired by code "
             f"(`resolve:<id>` lines in trace.jsonl).", "", *mock_coverage(candidates, model),
             f"- Top-up call: {topup}.", "", "| Lens | Kind | Focus |", "|---|---|---|"]
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


def live_count(candidates: list[Candidate]) -> int:
    return sum(not c.dropped_reason for c in candidates)


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
    mode = config.profiles()["economics_mode"]
    candidates, repairs, names = finish(drafts, model, mode, lambda live: name_benefits(ctx, live, budget, "dedupe", model))
    distinct = live_count(candidates)
    topup = f"not needed ({distinct} distinct after dedupe)"
    if distinct < MIN_DISTINCT:
        live = [c for c in candidates if not c.dropped_reason]
        extra = ask_lens(ctx, model, topup_lens(live, names), system, budget, step="topup")
        if extra:
            candidates, repairs, names = finish(drafts + extra, model, mode,
                                                lambda live: name_benefits(ctx, live, budget, "dedupe:topup", model))
        topup = (f"fired ({distinct} distinct after dedupe, under {MIN_DISTINCT}; "
                 + (f"{live_count(candidates)} after the top-up)" if extra else "its call failed)"))
    run_trace(ctx.run_dir, stage="propose", step="topup", decider="code", note=topup)
    for cid, note in repairs.items():
        run_trace(ctx.run_dir, stage="propose", step=f"resolve:{cid}", decider="code", note=note[:300])
    for c in candidates:
        if c.dropped_reason:
            run_trace(ctx.run_dir, stage="propose", step=f"check:{c.id}", decider="code", outcome="denied",
                      note=c.dropped_reason[:300])
    write_json_atomic(out / "candidates.json", CandidatesFile(candidates=candidates).model_dump_json(indent=1))
    write_exhibit(ctx.run_dir, 5, "propose", exhibit(lenses, candidates, repairs, model, topup))
