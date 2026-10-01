"""Goal 3 review: blind judges score every candidate, code turns their verdicts into a decision, fixable
rejects get an Opus revision (a revision that fails again gets another, up to judge_revision_cap), and code ranks what
survives."""

import hashlib
import json
import os
import shutil
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template

from simula import config, economics, llm
from simula.config import ROOT
from simula.contracts import (GATES, JUDGMENT, Candidate, CandidatesFile, Decision, DecisionsFile, LensOutput,
                              ProductModel, Verdict)
from simula.runfolder import sha256, write_json_atomic
from simula.runlog import needs_human, run_trace, write_exhibit
from simula.stages import Ctx, propose

PROMPTS = ROOT / "prompts" / "judge"
FROZEN = ROOT / "config" / "frozen_prompts.toml"
CHECKS = GATES + JUDGMENT
# An idea resting on something never observed fails one of these; the CONDITIONAL fallback never rescues it.
PREMISE = ("c1_revealed_value", "c2_evidence")
# Nor does it draw an offer any judge found aimed at paying users: the closest idea would be the very ad that check
# stops. A person can still approve such a split.
PAYERS = "c3_spares_payers"
NO_FALLBACK = PREMISE + (PAYERS,)
SURVIVORS = ("accept", "conditional")
ECON_CONDITION = {"CONDITIONAL": "The cost to serve isn't known", "FAIL": "It may cost more to serve than a view earns"}


class PromptsChanged(RuntimeError):
    pass


# ---------- frozen prompts ----------

RENDERED = "rendered judge message (tests/fixtures/judge/pin.json)"


def prompt_hashes() -> dict[str, str]:
    """The judge prompt files, the code-built judge message for each pinned candidate, and each real judge's model,
    effort, and max_tokens: after validation, a change to what the judge sees or to who judges counts as a prompt
    change, wherever it comes from."""
    from simula.validate import PINS, pinned_message
    files = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(PROMPTS.glob("*.md"))}
    return files | {f"rendered judge message ({pin.relative_to(config.ROOT)})":
                    hashlib.sha256(pinned_message(pin).encode()).hexdigest() for pin in PINS} | judge_settings()


def judge_settings() -> dict[str, str]:
    roles = config.roles("real")
    return {f"judge role {r} (real profile)": f"{roles[r]['model']}, effort {roles[r].get('effort')}, max_tokens "
                                              f"{config.max_tokens(roles[r])}, fallback "
                                              f"{roles[r].get('declared_fallback')}"
            for r in sorted(roles) if r.startswith("judge_")}


def frozen_problems() -> list[str]:
    pinned = tomllib.loads(FROZEN.read_text()).get("prompts", {})
    return [f"{path} {'changed since it was frozen' if path in pinned else 'was never frozen'}"
            for path, digest in prompt_hashes().items() if pinned.get(path) != digest]


def check_frozen(unfreeze: bool = False) -> None:
    problems = frozen_problems()
    if problems and not unfreeze:
        raise PromptsChanged("judge prompts differ from config/frozen_prompts.toml: " + "; ".join(problems)
                             + ". Re-freeze with `uv run python -m simula.validate freeze` and re-run "
                               "validate-judge; SIMULA_UNFREEZE=1 runs the stage anyway.")


def freeze() -> dict[str, str]:
    hashes = prompt_hashes()
    lines = ["# Judge prompt hashes. The judge refuses a prompt that differs. After changing one, re-freeze and "
             "re-run validate-judge.", "[prompts]", *[f'"{path}" = "{digest}"' for path, digest in hashes.items()]]
    FROZEN.write_text("\n".join(lines) + "\n")
    return hashes


def read_prompt(name: str) -> str:
    return (PROMPTS / name).read_text()


# ---------- what a judge sees ----------

def blind_fields(c: Candidate) -> dict:
    """The proposal as a judge sees it: no id, lens, economics, rank or the typed cap it comes from, or code's drop
    reason or flags."""
    return c.model_dump(exclude={"id", "lens", "bible_mechanic", "cost_inputs", "economics", "daily_cap",
                                 "reach_score", "rank_score", "dropped_reason", "flags"})


def opaque_id(c: Candidate) -> str:
    return "p" + hashlib.sha256(json.dumps(blind_fields(c), sort_keys=True).encode()).hexdigest()[:8]


def candidate_text(c: Candidate, model: ProductModel) -> str:
    states = {s.id: s for s in model.states}
    elements = {e.id: (s, e) for s in model.states for e in s.elements}
    ledger = {i.id: i for i in model.value_ledger}

    def screen(sid: str) -> str:
        if sid in states:
            s = states[sid]
            return f"{sid} {s.name} ({s.kind}; content {s.content_rating})"
        return f"{sid} (a new screen)" if sid.startswith("new:") else f"{sid} (not in the product model)"

    def evidence(eid: str) -> str:
        if eid not in elements:
            return f"- {eid}: not in the product model"
        s, e = elements[eid]
        if s.content_rating == "unsafe":
            return f"- {eid} on {s.id} {s.name}: (text left out: unsafe content)"
        return f"- {eid} on {s.id} {s.name}: {e.role or e.type}: " + " / ".join(f'"{t}"' for t in (e.text, e.label) if t)

    grant = ledger.get(c.grants_id)
    r = c.reward
    lines = [f"id: {opaque_id(c)}", f"title: {c.title}", f"kind: {c.kind}", "cited evidence:",
             *([evidence(i) for i in c.anchor_evidence_ids] or ["- none"]),
             f"what makes it work here: {c.what_is_different_here}",
             f"adds: {c.adds or 'nothing new (uses what the app has)'}",
             f"removes nothing free: {'yes' if c.removes_nothing_free else 'no'}",
             f"trigger event: {c.trigger_event}", f"trigger screen: {screen(c.trigger_state_id)}",
             f"placement: {c.placement}", f"offer copy: {c.offer_copy}",
             f"reward: {r.amount:g} {r.unit} ({r.kind}), {r.duration}", f"offered to: {c.for_users} users",
             "piece of a paid benefit: " + (f'{grant.id} "{grant.verbatim}"' if grant else c.grants_id or "none"),
             f"frequency cap: {c.frequency_cap}", f"if the user declines: {c.decline_path}",
             f"if the ad fails: {c.ad_fail_path}", f"subscribers: {c.subscriber_treatment}",
             f"advertiser category: {c.advertiser_category}", f"characters in the ad moment: {c.character_use}",
             "flow:", *[f"{n}. {screen(s.state_id)}: {s.caption}" for n, s in enumerate(c.flow_steps, 1)],
             f"when the reward runs out: {c.after_reward}", f"rationale: {c.rationale}"]
    return "\n".join(lines)


def judge_messages(c: Candidate, model: ProductModel) -> list[dict]:
    text = f"## Product model\n\n{propose.model_text(model)}\n\n## Proposal\n\n{candidate_text(c, model)}"
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def ask_judge(role: dict, c: Candidate, model: ProductModel, *, trace_path: Path, stage: str, step: str,
              budget: llm.Budget, no_cache: bool = False, replay: bool = False, rubric: str | None = None) -> Verdict:
    """One blind judge call on one candidate, under the tracked rubric unless an experiment passes its own. Raises
    llm.LLMFailure when both attempts fail."""
    verdict, _ = llm.call(trace_path=trace_path, stage=stage, step=step, model=role["model"],
                          effort=role.get("effort"), system=read_prompt("rubric.md") if rubric is None else rubric,
                          messages=judge_messages(c, model),
                          max_tokens=config.max_tokens(role), budget=budget,
                          schema=Verdict, no_cache=no_cache, replay=replay, fallback=role.get("declared_fallback"))
    return verdict.model_copy(update={"candidate_id": c.id})


# ---------- the decision (code) ----------

def failed(v: Verdict, check: str) -> bool:
    return not getattr(v, check).passed


def failed_by_any(verdicts: list[Verdict]) -> list[str]:
    return [k for k in CHECKS if any(failed(v, k) for v in verdicts)]


def failed_by_all(verdicts: list[Verdict]) -> list[str]:
    return [k for k in CHECKS if verdicts and all(failed(v, k) for v in verdicts)]


def is_idea(c: Candidate) -> bool:
    return c.kind != "no_opportunity"


def decide(c: Candidate, verdicts: list[Verdict], judges: int, mode: str, paths: list[str] = (),
           revision_of: str | None = None) -> Decision:
    """Combines the verdicts of the judges that ran. Gates AND over the judges, so one judge's gate fail rejects
    even when another judge's call failed. A judgment check every judge fails rejects. A judgment check only some
    judges fail (a split) makes the idea CONDITIONAL, ranked below every accept (D10); a person is asked only when a
    judge's call failed. In annotate mode the cost line is only a mark (economics_verdict) and never changes the
    verdict; in gate mode a FAIL rejects and an accept whose cost line isn't a PASS becomes conditional."""
    fails = {k: sum(failed(v, k) for v in verdicts) for k in CHECKS}
    unanimous = [k for k in JUDGMENT if verdicts and fails[k] == len(verdicts)]
    econ = c.economics.verdict if c.economics else None
    d = Decision(candidate_id=c.id, final="reject", checks_passed=sum(bool(verdicts) and not fails[k] for k in CHECKS),
                 checks_total=len(CHECKS), rank_score=c.rank_score, gate_fails=[k for k in GATES if fails[k]],
                 judgment_splits=[k for k in JUDGMENT if 0 < fails[k] < len(verdicts)], verdict_paths=list(paths),
                 economics_verdict=econ, revision_of=revision_of, failure_type=None, rerun_stage=None)
    if c.dropped_reason or not is_idea(c):
        return d.model_copy(update={"failure_type": "proposal"})
    if d.gate_fails:
        return d.model_copy(update={"failure_type": "proposal"})
    if len(verdicts) < judges:
        return d.model_copy(update={"final": "needs_human", "rerun_stage": "judge"})
    if "c2_evidence" in unanimous:
        return d.model_copy(update={"failure_type": "product_model", "rerun_stage": "explore"})
    if unanimous:
        return d.model_copy(update={"failure_type": "proposal"})
    if mode == "gate" and econ == "FAIL":
        return d.model_copy(update={"failure_type": "proposal"})
    accepted = not d.judgment_splits and (mode == "annotate" or econ == "PASS")
    return d.model_copy(update={"final": "accept" if accepted else "conditional"})


def proposal_fault(original: Decision, revision_verdicts: list[Verdict]) -> Decision:
    """An evidence fail the revision fixed without new facts was the proposal's wording, not a gap in the model,
    so nobody needs to re-explore for it."""
    if original.rerun_stage != "explore" or not revision_verdicts or "c2_evidence" in failed_by_any(revision_verdicts):
        return original
    return original.model_copy(update={"failure_type": "proposal", "rerun_stage": None})


def revisable(c: Candidate, d: Decision, verdicts: list[Verdict], judges: int) -> bool:
    """A fixable reject that every judge returned a verdict on: a lost call leaves the reject as it is, even when
    another judge failed a gate."""
    return (d.final == "reject" and is_idea(c) and not c.dropped_reason and len(verdicts) == judges
            and any(v.fixable for v in verdicts))


def barred(verdicts: list[Verdict]) -> bool:
    """Whether the fallback can't carry an idea: every judge failed a premise check, or any judge failed c3. A premise
    check only one judge fails is a split, which doesn't bar it (D10)."""
    return bool(set(PREMISE) & set(failed_by_all(verdicts))) or PAYERS in failed_by_any(verdicts)


def could_fall_back(c: Candidate, d: Decision, verdicts: list[Verdict]) -> bool:
    """An idea code kept that every judge passed on every gate, and that nothing `barred` keeps out."""
    return is_idea(c) and not c.dropped_reason and bool(verdicts) and not d.gate_fails and not barred(verdicts)


def earlier(cid: str):
    """The versions a revision came from, newest first: c01-rev-rev gives c01-rev, then c01."""
    while cid.endswith("-rev"):
        cid = cid.removesuffix("-rev")
        yield cid


def drawable(d: Decision) -> bool:
    """A survivor flows can draw without a person: any but a split on c3, which waits on the Needs your call page."""
    return d.final in SURVIVORS and PAYERS not in d.judgment_splits


def superseded(revised: list[Candidate], decisions: dict[str, Decision],
               verdicts: dict[str, dict[str, Verdict]]) -> set[str]:
    """Earlier versions a revision stands in for: one that survived, or a reject the fallback could pick, replaces its
    original and every revision before it. A revision that made the idea worse, or that waits on a person, leaves the
    versions before it in play."""
    def stands_in(r: Candidate, d: Decision) -> bool:
        return drawable(d) or (d.final == "reject" and could_fall_back(r, d, [*verdicts[r.id].values()]))
    return {e for r in revised if stands_in(r, decisions[r.id]) for e in earlier(r.id)}


def fallback_pick(decisions: list[Decision], candidates: dict[str, Candidate],
                  verdicts: dict[str, dict[str, Verdict]], superseded: set[str]) -> str | None:
    """When nothing survives, the best reject the fallback could carry (could_fall_back): fewest checks failed, then
    rank. None when there is no such candidate. A split on c3 doesn't count as surviving: flows never draws it unasked,
    so it would leave the deck empty."""
    if any(drawable(d) for d in decisions):
        return None
    eligible = [d for d in decisions if d.final == "reject" and d.candidate_id not in superseded
                and could_fall_back(candidates[d.candidate_id], d, [*verdicts.get(d.candidate_id, {}).values()])]
    best = min(eligible, key=lambda d: (-d.checks_passed, -(d.rank_score or 0)), default=None)
    return best and best.candidate_id


def ordered(decisions: list[Decision]) -> list[Decision]:
    """Accepts first, then CONDITIONAL ideas, then those waiting on a person, then rejects; by rank_score (code)
    within each. A CONDITIONAL never ranks above an accept (D10). flows draws the deck in this order."""
    tier = {"accept": 0, "conditional": 1, "needs_human": 2, "reject": 3}
    return sorted(decisions, key=lambda d: (tier[d.final], -(d.rank_score or 0), -d.checks_passed))


def condition(d: Decision, c: Candidate, verdicts: list[Verdict], mode: str) -> str | None:
    """The one sentence a CONDITIONAL candidate carries: for the fallback pick (marked by the failure_type it keeps),
    the checks it missed; for a judge split, each split check with a failing and a passing judge's reason (D10). Cost
    is part of it only in gate mode; in annotate mode it is a separate mark."""
    if d.final != "conditional":
        return None
    def reason(k: str, fails: bool) -> str:
        return next(getattr(v, k).reason for v in verdicts if failed(v, k) == fails).rstrip(".")
    parts = []
    missed = [k for k in JUDGMENT if k in failed_by_any(verdicts)]
    if d.failure_type and missed:
        parts.append(f"No idea passed every check; this one passes every safety gate but not {', '.join(missed)} "
                     f"({reason(missed[0], True)})")
    elif d.judgment_splits:
        parts.append("The judges split: " + "; ".join(
            f"on {k}, one fails it ({reason(k, True)}) and another passes it ({reason(k, False)})"
            for k in d.judgment_splits))
    if mode == "gate" and d.economics_verdict in ECON_CONDITION and c.economics:
        parts.append(f"{ECON_CONDITION[d.economics_verdict]}: {c.economics.assumption_line.split('. ')[0]}")
    return " ".join(p.rstrip(".") + "." for p in parts)


# ---------- revision ----------

def strip_bucket(title: str) -> str:
    return next((title.removeprefix(f"{b}: ") for b in propose.BUCKETS.values() if title.startswith(f"{b}: ")), title)


def revision_prompt(c: Candidate, verdicts: list[Verdict], model: ProductModel) -> str:
    failures = [f"- {k}: {getattr(v, k).reason}" for v in verdicts for k in CHECKS if failed(v, k)]
    draft = c.model_dump(exclude={"economics", "reach_score", "rank_score", "dropped_reason"})
    draft["title"] = strip_bucket(c.title)
    return Template(read_prompt("revise.md")).substitute(
        failures="\n".join(dict.fromkeys(failures)), candidate=json.dumps(draft, indent=1),
        product_model=propose.model_text(model), unobserved=propose.unobserved_text(model)).rstrip("\n") + "\n"


def revise(ctx: Ctx, c: Candidate, verdicts: list[Verdict], model: ProductModel, mode: str,
           budget: llm.Budget) -> Candidate | None:
    """One Opus revision. The result goes through propose's code checks, cost line, and rank again."""
    role = config.roles(ctx.profile)["proposer"]
    step = f"revise:{c.id}"
    messages = [{"role": "user", "content": [{"type": "text", "text": revision_prompt(c, verdicts, model)}]}]
    try:
        output, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="judge", step=step, model=role["model"],
                             effort=role.get("effort"), system=propose.system_prompt(), messages=messages,
                             max_tokens=config.max_tokens(role),
                             budget=budget, schema=LensOutput, no_cache=ctx.no_cache, replay=ctx.replay)
    except llm.LLMFailure as e:
        run_trace(ctx.run_dir, stage="judge", step=step, decider="code", outcome=e.outcome,
                  note="revision failed twice; the reject stands")
        return None
    if len(output.candidates) != 1:
        run_trace(ctx.run_dir, stage="judge", step=step, decider="code",
                  note=f"the reviser returned {len(output.candidates)} ideas for 1 asked; "
                       + ("the first is judged" if output.candidates else "the reject stands"))
    if not output.candidates:
        return None
    new = Candidate(**{**output.candidates[0].model_dump(), "id": f"{c.id}-rev", "lens": c.lens})
    new, _ = propose.resolve_ids(new, model)
    reason = f"no opportunity: {new.rationale}" if not is_idea(new) else propose.check(new, model)
    flags = [] if reason else propose.jargon_flags(new, model)
    new = economics.apply([new.model_copy(update={"dropped_reason": reason, "flags": flags})], model, mode)[0]
    return propose.with_bucket(propose.rank(new, model, mode))


def recheck(ctx: Ctx, revisions: list[Candidate], candidates: list[Candidate], decisions: dict[str, Decision],
            model: ProductModel, budget: llm.Budget, round_: int) -> list[Candidate]:
    """Propose's last two checks on one round's revisions: the benefit-naming call's paywall-bullet link, and dedupe.
    A revision that gives the same benefit as an idea still standing (one of `candidates`, the proposals and earlier
    rounds' revisions, not dropped and not rejected) or as an earlier revision of its round is dropped as its
    duplicate. Revisions go unflagged first, then best first, and a flagged idea never evicts a clean one; a flagged
    idea still counts as live against other flagged ones."""
    fresh = sorted((r for r in revisions if is_idea(r) and not r.dropped_reason),
                   key=lambda r: (bool(r.flags), -(r.rank_score or 0)))
    if not fresh:
        return revisions
    standing = [c for c in candidates if is_idea(c) and not c.dropped_reason and decisions[c.id].final != "reject"]
    # the first revision round keeps the step name runs had before there was a second
    step = "judge:revisions" if round_ == 2 else f"judge:revisions:r{round_}"
    names, links = propose.name_benefits(ctx, standing + fresh, budget, step, model)
    reasons, kept = {}, list(standing)
    for r in fresh:
        rivals = [k for k in kept if r.flags or not k.flags]
        twin, benefit = next(((k, b) for k in rivals if (b := propose.same_benefit(k, r, names))), (None, None))
        reasons[r.id] = (propose.linked_problem(r, links.get(r.id), model)
                         or (f"duplicate of {twin.id}: same benefit ({benefit})" if twin else None))
        if not reasons[r.id]:
            kept.append(r)
    return [r.model_copy(update={"dropped_reason": reasons[r.id]}) if r.id in reasons else r for r in revisions]


def swap_in(work: Path, out: Path) -> None:
    """Replaces judge/ with the finished work folder in two renames, putting the old one back if the second fails,
    so a run always has a whole judge/."""
    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        os.replace(out, old)
    try:
        os.replace(work, out)
    except BaseException:
        if old.exists() and not out.exists():
            os.replace(old, out)
        raise
    shutil.rmtree(old, ignore_errors=True)


# ---------- stage ----------

def judge_all(ctx: Ctx, work: Path, candidates: list[Candidate], model: ProductModel, judges: list[str],
              budget: llm.Budget, round_: int) -> tuple[dict[str, dict[str, Verdict]], dict[str, list[str]]]:
    """Every judge on every idea, one candidate per call, in parallel. Returns each candidate's verdicts by judge
    and their paths; a judge whose call failed twice is missing."""
    roles = config.roles(ctx.profile)
    jobs = [(c, j) for c in candidates if is_idea(c) for j in judges]

    def one(job):
        c, judge = job
        try:
            v = ask_judge(roles[judge], c, model, trace_path=ctx.run_dir / "trace.jsonl", stage="judge",
                          step=f"judge:{c.id}:{judge}:r{round_}", budget=budget, no_cache=ctx.no_cache,
                          replay=ctx.replay)
        except llm.LLMFailure as e:
            run_trace(ctx.run_dir, stage="judge", step=f"judge:{c.id}:{judge}:r{round_}", decider="code",
                      outcome=e.outcome, note="judge call failed twice; the candidate goes to a person")
            return c.id, judge, None, None
        name = f"{c.id}_{judge}_r{round_}.json"
        write_json_atomic(work / "verdicts" / name, v.model_dump_json(indent=1))
        return c.id, judge, v, f"judge/verdicts/{name}"

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(jobs)))) as pool:
        results = list(pool.map(one, jobs))
    verdicts = {c.id: {} for c in candidates}
    paths = {c.id: [] for c in candidates}
    for cid, judge, v, path in results:
        if v:
            verdicts[cid][judge] = v
            paths[cid].append(path)
    return verdicts, paths


def run(ctx: Ctx) -> None:
    check_frozen(os.environ.get("SIMULA_UNFREEZE") == "1")
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    candidates = CandidatesFile.model_validate_json((ctx.run_dir / "propose" / "candidates.json").read_text()).candidates
    # judge/ is built aside and swapped in whole at the end: a failed replay or a cap keeps the last good judge/,
    # and nothing from an earlier attempt (a queue, a no-opportunity note, a verdict) outlives it.
    work = ctx.run_dir / "judge.tmp"
    shutil.rmtree(work, ignore_errors=True)
    (work / "verdicts").mkdir(parents=True)
    try:
        judge_run(ctx, work, model, candidates)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def judge_run(ctx: Ctx, work: Path, model: ProductModel, candidates: list[Candidate]) -> None:
    mode = config.profiles()["economics_mode"]
    judges = config.profiles()["judges"]
    budget = llm.Budget.for_stage("judge", ctx.run_dir / "trace.jsonl", ctx.usd_cap)

    verdicts, paths = judge_all(ctx, work, candidates, model, judges, budget, 1)
    decisions = {c.id: decide(c, [*verdicts[c.id].values()], len(judges), mode, paths[c.id]) for c in candidates}
    revised, latest = [], candidates
    # Round 1 judged the proposals. Each later round revises only the newest rejects, so an idea is revised at most
    # judge_revision_cap times.
    for round_ in range(2, config.judge_revision_cap() + 2):
        to_revise = [c for c in latest if revisable(c, decisions[c.id], [*verdicts[c.id].values()], len(judges))]
        with ThreadPoolExecutor(max_workers=max(1, len(to_revise))) as pool:
            latest = [r for r in pool.map(lambda c: revise(ctx, c, [*verdicts[c.id].values()], model, mode, budget),
                                          to_revise) if r]
        latest = recheck(ctx, latest, candidates + revised, decisions, model, budget, round_)
        if latest:
            v, p = judge_all(ctx, work, latest, model, judges, budget, round_)
            verdicts |= v
            decisions |= {r.id: decide(r, [*v[r.id].values()], len(judges), mode, p[r.id],
                                       revision_of=r.id.removesuffix("-rev")) for r in latest}
            # a revision that clears an evidence fail without new facts clears it for every version before it, once
            # every judge has said so: a lost call leaves the fail unconfirmed
            for r in (r for r in latest if len(v[r.id]) == len(judges)):
                for e in earlier(r.id):
                    decisions[e] = proposal_fault(decisions[e], [*v[r.id].values()])
        revised += latest

    everyone = {c.id: c for c in candidates + revised}
    pick = fallback_pick(list(decisions.values()), everyone, verdicts, superseded(revised, decisions, verdicts))
    if pick:
        # the pick keeps its reject's failure_type: that is how flows and condition() tell it from a split (D10)
        decisions[pick] = decisions[pick].model_copy(update={"final": "conditional"})
        run_trace(ctx.run_dir, stage="judge", step="fallback", decider="code",
                  note=f"nothing survived (a split on {PAYERS} doesn't count); {pick} passes every gate, no premise "
                       f"check every judge failed, and no judge failed {PAYERS} -> CONDITIONAL")
    final = ordered(list(decisions.values()))
    for d in final:
        run_trace(ctx.run_dir, stage="judge", step=f"decide:{d.candidate_id}", decider="code",
                  outcome="ok" if d.final in SURVIVORS else "denied",
                  note=f"{d.final} {d.checks_passed}/{d.checks_total}" + why(d, everyone[d.candidate_id]))

    write_json_atomic(work / "revisions.json", CandidatesFile(candidates=revised).model_dump_json(indent=1))
    write_json_atomic(work / "decisions.json", DecisionsFile(decisions=final).model_dump_json(indent=1))
    write_queue(ctx, work, final, everyone)
    if not any(drawable(d) for d in final):
        (work / "no-opportunity.md").write_text(no_opportunity(final, everyone, verdicts))
    swap_in(work, ctx.run_dir / "judge")
    write_exhibit(ctx.run_dir, 6, "judge", exhibit(final, everyone, verdicts, judges, ctx.profile, mode))


def why(d: Decision, c: Candidate) -> str:
    if c.dropped_reason:
        return f"; dropped by code: {c.dropped_reason}"
    parts = ([f"gates failed: {', '.join(d.gate_fails)}"] if d.gate_fails else []) \
        + ([f"split: {', '.join(d.judgment_splits)}"] if d.judgment_splits else []) \
        + ([f"rerun {d.rerun_stage} (human-gated)"] if d.rerun_stage else [])
    return "; " + "; ".join(parts) if parts else ""


# ---------- human-readable outputs ----------

def write_queue(ctx: Ctx, work: Path, decisions: list[Decision], candidates: dict[str, Candidate]) -> None:
    """Ideas a judge's call failed on twice. A split is no longer queued: it goes on as CONDITIONAL (D10)."""
    waiting = [d for d in decisions if d.final == "needs_human"]
    if not waiting:
        return
    app, run_id = ctx.app["name"], ctx.run_dir.name
    lines = ["# Judge: candidates waiting on a person", "",
             "The run continued without them. Nothing reruns on its own.", ""]
    for d in waiting:
        c = candidates[d.candidate_id]
        lines += [f"## {c.id} · {c.title}", "", *[f"- Flag: {f}" for f in c.flags], *([""] if c.flags else []),
                  "**Why:** a judge call failed twice.", "", f"**Continue with:** `simula judge {app} --run {run_id}`",
                  ""]
    (work / "human-queue.md").write_text("\n".join(lines))
    needs_human(ctx.run_dir, "judge", f"{len(waiting)} candidate(s) need a person", "a judge call failed twice",
                ["judge/human-queue.md"], f"simula judge {app} --run {run_id}")


def no_opportunity(decisions: list[Decision], candidates: dict[str, Candidate],
                   verdicts: dict[str, dict[str, Verdict]]) -> str:
    gate = [d for d in decisions if d.gate_fails]
    premise = [d for d in decisions if not d.gate_fails and barred([*verdicts.get(d.candidate_id, {}).values()])]
    waiting = [d for d in decisions if d.final == "needs_human"]
    payers = [d for d in decisions if d.final in SURVIVORS]
    lines = ["# No opportunity", "", "No candidate reached Goal 4, and none was manufactured.", "",
             f"- {len(decisions)} candidates judged or dropped.",
             f"- {len(gate)} failed a safety gate.",
             f"- {len(premise)} passed the gates but rest on something the product model doesn't show or aim "
             f"the offer at paying users ({', '.join(NO_FALLBACK)}), so the CONDITIONAL fallback can't carry them.",
             f"- {len(waiting)} wait on a person (`human-queue.md`).",
             f"- {len(payers)} split the judges on {PAYERS}; each waits on the Needs your call page, where a person "
             "can approve it.", ""]
    lines += [f"- {d.candidate_id} · {candidates[d.candidate_id].title}: {d.checks_passed}/{d.checks_total}"
              + why(d, candidates[d.candidate_id]) for d in decisions]
    return "\n".join(lines) + "\n"


def rank_text(d: Decision, c: Candidate, mode: str) -> str:
    """The rank as the exhibit prints it: gate mode's floor for an uncounted cost reads as what it means."""
    if d.rank_score is None:
        return ""
    uncounted = mode == "gate" and c.economics and economics.uncounted(c.economics)
    return "last (cost not counted)" if uncounted else f"{d.rank_score:g}"


def exhibit(decisions: list[Decision], candidates: dict[str, Candidate], verdicts: dict[str, dict[str, Verdict]],
            judges: list[str], profile: str, mode: str) -> str:
    roles = config.roles(profile)
    count = {f: sum(d.final == f for d in decisions) for f in ("accept", "conditional", "needs_human", "reject")}
    revised_from = [d.revision_of for d in decisions if d.revision_of]
    who = ", ".join(f"{j} ({roles[j]['model']}{', ' + roles[j]['effort'] if roles[j].get('effort') else ''})"
                    for j in judges)
    lines = ["# 06 · judge", "",
             f"Judges: {who}, blind (opaque ids, no lens or model names, one candidate per call). "
             f"Economics mode: `{mode}`. {len(decisions)} candidates: {count['accept']} accepted, "
             f"{count['conditional']} conditional, {count['needs_human']} waiting on a person, "
             f"{count['reject']} rejected; {len(revised_from)} revision(s) of "
             f"{sum(not r.endswith('-rev') for r in revised_from)} idea(s).", "",
             "| Candidate | Final | Checks | Rank | Cost mark |", "|---|---|---|---|---|"]
    lines += [f"| {d.candidate_id} · {candidates[d.candidate_id].title} | {d.final} | {d.checks_passed}/{d.checks_total}"
              f" | {rank_text(d, candidates[d.candidate_id], mode)} | {d.economics_verdict or ''} |" for d in decisions]
    for d in decisions:
        c = candidates[d.candidate_id]
        lines += ["", f"## {c.id} · {c.title}", "",
                  f"- **{d.final}**, {d.checks_passed}/{d.checks_total} checks passed by every judge"
                  + (f", revision of {d.revision_of}" if d.revision_of else "") + why(d, c)]
        if text := condition(d, c, [*verdicts.get(c.id, {}).values()], mode):
            lines.append(f"- Condition: {text}")
        if c.economics:
            lines.append(f"- Cost mark ({c.economics.verdict}): {c.economics.assumption_line}")
        lines += [f"- Flag: {f}" for f in c.flags]
        for judge, v in verdicts.get(c.id, {}).items():
            lines += [f"- {judge}: " + ("fixable" if v.fixable else "not fixable")
                      + (f"; other concern: {v.other_concern}" if v.other_concern else "")]
            lines += [f"  - {'✗' if failed(v, k) else '✓'} {k}: {getattr(v, k).reason}" for k in CHECKS]
    return "\n".join(lines) + "\n"
