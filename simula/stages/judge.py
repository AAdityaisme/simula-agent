"""Goal 3 review: blind judges score every candidate, code turns their verdicts into a decision, fixable
rejects get one Opus revision, and code ranks what survives."""

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
# One over config/models.toml's stream_above (16000, checked with >): judge calls stream, so a stall fails after
# 60 s idle with no SDK retries, and a reply lost mid-stream is still charged.
MAX_TOKENS = 16001
SURVIVORS = ("accept", "conditional")
ECON_CONDITION = {"CONDITIONAL": "The cost to serve isn't known", "FAIL": "It may cost more to serve than a view earns"}
DEFAULT_JUDGES = ["judge_1"]


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
    roles, models = config.roles("real"), config.models()
    return {f"judge role {r} (real profile)": f"{roles[r]['model']}, effort {roles[r].get('effort')}, max_tokens "
                                              f"{min(MAX_TOKENS, models[roles[r]['model']]['max_out'])}"
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
    """The proposal as a judge sees it: no id, lens, economics, rank, or code's drop reason or flags."""
    return c.model_dump(exclude={"id", "lens", "bible_mechanic", "cost_inputs", "economics", "reach_score",
                                 "rank_score", "dropped_reason", "flags"})


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
              budget: llm.Budget, no_cache: bool = False, replay: bool = False) -> Verdict:
    """One blind judge call on one candidate. Raises llm.LLMFailure when both attempts fail."""
    verdict, _ = llm.call(trace_path=trace_path, stage=stage, step=step, model=role["model"],
                          effort=role.get("effort"), system=read_prompt("rubric.md"),
                          messages=judge_messages(c, model),
                          max_tokens=min(MAX_TOKENS, config.models()[role["model"]]["max_out"]), budget=budget,
                          schema=Verdict, no_cache=no_cache, replay=replay, fallback=role.get("declared_fallback"))
    return verdict.model_copy(update={"candidate_id": c.id})


# ---------- the decision (code) ----------

def failed(v: Verdict, check: str) -> bool:
    return not getattr(v, check).passed


def failed_by_any(verdicts: list[Verdict]) -> list[str]:
    return [k for k in CHECKS if any(failed(v, k) for v in verdicts)]


def is_idea(c: Candidate) -> bool:
    return c.kind != "no_opportunity"


def decide(c: Candidate, verdicts: list[Verdict], judges: int, mode: str, paths: list[str] = (),
           revision_of: str | None = None) -> Decision:
    """Combines the verdicts of the judges that ran. Gates AND over the judges; a judgment check every judge fails
    rejects; a split goes to a person. In annotate mode the cost line is only a mark (economics_verdict) and never
    changes the verdict; in gate mode a FAIL rejects and an accept whose cost line isn't a PASS becomes conditional."""
    fails = {k: sum(failed(v, k) for v in verdicts) for k in CHECKS}
    unanimous = [k for k in JUDGMENT if verdicts and fails[k] == len(verdicts)]
    econ = c.economics.verdict if c.economics else None
    d = Decision(candidate_id=c.id, final="reject", checks_passed=sum(bool(verdicts) and not fails[k] for k in CHECKS),
                 checks_total=len(CHECKS), rank_score=c.rank_score, gate_fails=[k for k in GATES if fails[k]],
                 judgment_splits=[k for k in JUDGMENT if 0 < fails[k] < len(verdicts)], verdict_paths=list(paths),
                 economics_verdict=econ, revision_of=revision_of, failure_type=None, rerun_stage=None)
    if c.dropped_reason or not is_idea(c):
        return d.model_copy(update={"failure_type": "proposal"})
    if len(verdicts) < judges:
        return d.model_copy(update={"final": "needs_human", "rerun_stage": "judge"})
    if d.gate_fails:
        return d.model_copy(update={"failure_type": "proposal"})
    if "c2_evidence" in unanimous:
        return d.model_copy(update={"failure_type": "product_model", "rerun_stage": "explore"})
    if unanimous:
        return d.model_copy(update={"failure_type": "proposal"})
    if d.judgment_splits:
        return d.model_copy(update={"final": "needs_human"})
    if mode == "gate" and econ == "FAIL":
        return d.model_copy(update={"failure_type": "proposal"})
    return d.model_copy(update={"final": "accept" if mode == "annotate" or econ == "PASS" else "conditional"})


def proposal_fault(original: Decision, revision_verdicts: list[Verdict]) -> Decision:
    """An evidence fail the revision fixed without new facts was the proposal's wording, not a gap in the model,
    so nobody needs to re-explore for it."""
    if original.rerun_stage != "explore" or not revision_verdicts or "c2_evidence" in failed_by_any(revision_verdicts):
        return original
    return original.model_copy(update={"failure_type": "proposal", "rerun_stage": None})


def revisable(c: Candidate, d: Decision, verdicts: list[Verdict]) -> bool:
    return (d.final == "reject" and is_idea(c) and not c.dropped_reason and bool(verdicts)
            and any(v.fixable for v in verdicts))


def could_fall_back(c: Candidate, d: Decision, verdicts: list[Verdict]) -> bool:
    """An idea code kept that every judge passed on every gate and both premise checks."""
    return (is_idea(c) and not c.dropped_reason and bool(verdicts) and not d.gate_fails
            and not set(PREMISE) & set(failed_by_any(verdicts)))


def superseded(revised: list[Candidate], decisions: dict[str, Decision],
               verdicts: dict[str, dict[str, Verdict]]) -> set[str]:
    """Originals whose revision stands in for them: one that survived, or a reject the fallback could pick. A revision
    that made the idea worse, or that waits on a person, leaves its original in play."""
    def stands_in(r: Candidate, d: Decision) -> bool:
        return d.final in SURVIVORS or (d.final == "reject" and could_fall_back(r, d, [*verdicts[r.id].values()]))
    return {r.id.removesuffix("-rev") for r in revised if stands_in(r, decisions[r.id])}


def fallback_pick(decisions: list[Decision], candidates: dict[str, Candidate],
                  verdicts: dict[str, dict[str, Verdict]], superseded: set[str]) -> str | None:
    """When nothing survives, the best reject that passes every gate and both premise checks: fewest checks
    failed, then rank. None when there is no such candidate."""
    if any(d.final in SURVIVORS for d in decisions):
        return None
    eligible = [d for d in decisions if d.final == "reject" and d.candidate_id not in superseded
                and could_fall_back(candidates[d.candidate_id], d, [*verdicts.get(d.candidate_id, {}).values()])]
    best = min(eligible, key=lambda d: (-d.checks_passed, -(d.rank_score or 0)), default=None)
    return best and best.candidate_id


def ordered(decisions: list[Decision]) -> list[Decision]:
    """Survivors first by rank_score (code), then those waiting on a person, then rejects by score. On an equal
    score an accept goes before a CONDITIONAL, so the judges' verdict breaks ties; in annotate mode cost never does,
    since it never makes an idea CONDITIONAL. flows' top-4 cut keeps this order."""
    tier = {"accept": 0, "conditional": 0, "needs_human": 1, "reject": 2}
    return sorted(decisions, key=lambda d: (tier[d.final], -(d.rank_score or 0), d.final != "accept",
                                            -d.checks_passed))


def condition(d: Decision, c: Candidate, verdicts: list[Verdict], mode: str) -> str | None:
    """The one sentence a CONDITIONAL candidate carries on its slides. Cost is part of it only in gate mode; in
    annotate mode it is a separate mark."""
    if d.final != "conditional":
        return None
    parts = []
    fails = [k for k in JUDGMENT if k in failed_by_any(verdicts)]
    if fails:
        reason = next(getattr(v, fails[0]).reason for v in verdicts if failed(v, fails[0]))
        parts.append(f"No idea passed every check; this one passes every safety gate but not {', '.join(fails)} "
                     f"({reason})")
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
        product_model=propose.model_text(model))


def revise(ctx: Ctx, c: Candidate, verdicts: list[Verdict], model: ProductModel, mode: str,
           budget: llm.Budget) -> Candidate | None:
    """One Opus revision. The result goes through propose's code checks, cost line, and rank again."""
    role = config.roles(ctx.profile)["proposer"]
    step = f"revise:{c.id}"
    messages = [{"role": "user", "content": [{"type": "text", "text": revision_prompt(c, verdicts, model)}]}]
    try:
        output, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="judge", step=step, model=role["model"],
                             effort=role.get("effort"), system=propose.system_prompt(), messages=messages,
                             max_tokens=min(propose.MAX_TOKENS, config.models()[role["model"]]["max_out"]),
                             budget=budget, schema=LensOutput, no_cache=ctx.no_cache, replay=ctx.replay)
    except llm.LLMFailure as e:
        run_trace(ctx.run_dir, stage="judge", step=step, decider="code", outcome=e.outcome,
                  note="revision failed twice; the reject stands")
        return None
    if not output.candidates:
        return None
    new = Candidate(**{**output.candidates[0].model_dump(), "id": f"{c.id}-rev", "lens": c.lens})
    new, _ = propose.resolve_ids(new, model)
    reason = f"no opportunity: {new.rationale}" if not is_idea(new) else propose.check(new, model)
    flags = [] if reason else propose.jargon_flags(new, model)
    new = economics.apply([new.model_copy(update={"dropped_reason": reason, "flags": flags})], model.app_category,
                          mode)[0]
    return propose.with_bucket(propose.rank(new, model, mode))


def recheck(ctx: Ctx, revisions: list[Candidate], candidates: list[Candidate], decisions: dict[str, Decision],
            model: ProductModel, budget: llm.Budget) -> list[Candidate]:
    """Propose's last two checks on the revisions: the benefit-naming call's paywall-bullet link, and dedupe. A
    revision that gives the same benefit as an idea still standing (not dropped, not rejected, not its own
    original) or as a better-ranked revision is dropped as its duplicate."""
    fresh = sorted((r for r in revisions if is_idea(r) and not r.dropped_reason), key=lambda r: -(r.rank_score or 0))
    if not fresh:
        return revisions
    standing = [c for c in candidates if is_idea(c) and not c.dropped_reason and decisions[c.id].final != "reject"]
    names, links = propose.name_benefits(ctx, standing + fresh, budget, "judge:revisions", model)
    reasons, kept = {}, list(standing)
    for r in fresh:
        twin, benefit = next(((k, b) for k in kept if (b := propose.same_benefit(k, r, names))), (None, None))
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
    judges = config.profiles().get("judges", DEFAULT_JUDGES)
    budget = llm.Budget.for_stage("judge", ctx.run_dir / "trace.jsonl", ctx.usd_cap)

    verdicts, paths = judge_all(ctx, work, candidates, model, judges, budget, 1)
    decisions = {c.id: decide(c, [*verdicts[c.id].values()], len(judges), mode, paths[c.id]) for c in candidates}
    to_revise = [c for c in candidates if revisable(c, decisions[c.id], [*verdicts[c.id].values()])]
    with ThreadPoolExecutor(max_workers=max(1, len(to_revise))) as pool:
        revised = [r for r in pool.map(lambda c: revise(ctx, c, [*verdicts[c.id].values()], model, mode, budget),
                                       to_revise) if r]
    revised = recheck(ctx, revised, candidates, decisions, model, budget)
    if revised:
        v2, p2 = judge_all(ctx, work, revised, model, judges, budget, 2)
        verdicts |= v2
        decisions |= {r.id: decide(r, [*v2[r.id].values()], len(judges), mode, p2[r.id],
                                   revision_of=r.id.removesuffix("-rev")) for r in revised}
        for r in revised:
            original = r.id.removesuffix("-rev")
            decisions[original] = proposal_fault(decisions[original], [*v2[r.id].values()])

    everyone = {c.id: c for c in candidates + revised}
    pick = fallback_pick(list(decisions.values()), everyone, verdicts, superseded(revised, decisions, verdicts))
    if pick:
        decisions[pick] = decisions[pick].model_copy(update={"final": "conditional", "failure_type": None})
        run_trace(ctx.run_dir, stage="judge", step="fallback", decider="code",
                  note=f"nothing accepted; {pick} passes every gate and both premise checks -> CONDITIONAL")
    final = ordered(list(decisions.values()))
    for d in final:
        run_trace(ctx.run_dir, stage="judge", step=f"decide:{d.candidate_id}", decider="code",
                  outcome="ok" if d.final in SURVIVORS else "denied",
                  note=f"{d.final} {d.checks_passed}/{d.checks_total}" + why(d, everyone[d.candidate_id]))

    write_json_atomic(work / "revisions.json", CandidatesFile(candidates=revised).model_dump_json(indent=1))
    write_json_atomic(work / "decisions.json", DecisionsFile(decisions=final).model_dump_json(indent=1))
    write_queue(ctx, work, final, everyone, verdicts)
    if not any(d.final in SURVIVORS for d in final):
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

def write_queue(ctx: Ctx, work: Path, decisions: list[Decision], candidates: dict[str, Candidate],
                verdicts: dict[str, dict[str, Verdict]]) -> None:
    waiting = [d for d in decisions if d.final == "needs_human"]
    if not waiting:
        return
    app, run_id = ctx.app["name"], ctx.run_dir.name
    lines = ["# Judge: candidates waiting on a person", "",
             "The run continued without them. Nothing reruns on its own.", ""]
    for d in waiting:
        c = candidates[d.candidate_id]
        lines += [f"## {c.id} · {c.title}", "", *[f"- Flag: {f}" for f in c.flags], *([""] if c.flags else [])]
        if d.rerun_stage == "judge":
            lines += ["**Why:** a judge call failed twice.", "",
                      f"**Continue with:** `simula judge {app} --run {run_id}`", ""]
            continue
        lines += [f"**Why:** the judges split on {', '.join(d.judgment_splits)}.", ""]
        lines += [f"- {judge} {'fails' if failed(v, k) else 'passes'} {k}: {getattr(v, k).reason}"
                  for judge, v in verdicts[c.id].items() for k in d.judgment_splits]
        lines += ["", "**Evidence:** " + ", ".join(f"`{p}`" for p in d.verdict_paths), "",
                  "**Continue with:** nothing to run; flows leaves it out. A person reads the reasons and decides "
                  "whether the idea is worth a hand revision.", ""]
    (work / "human-queue.md").write_text("\n".join(lines))
    needs_human(ctx.run_dir, "judge", f"{len(waiting)} candidate(s) need a person",
                "the judges split or a judge call failed", ["judge/human-queue.md"],
                f"read judge/human-queue.md, then `simula flows {app} --run {run_id}`")


def no_opportunity(decisions: list[Decision], candidates: dict[str, Candidate],
                   verdicts: dict[str, dict[str, Verdict]]) -> str:
    gate = [d for d in decisions if d.gate_fails]
    premise = [d for d in decisions if not d.gate_fails
               and set(PREMISE) & set(failed_by_any([*verdicts.get(d.candidate_id, {}).values()]))]
    waiting = [d for d in decisions if d.final == "needs_human"]
    lines = ["# No opportunity", "", "No candidate reached Goal 4, and none was manufactured.", "",
             f"- {len(decisions)} candidates judged or dropped.",
             f"- {len(gate)} failed a safety gate.",
             f"- {len(premise)} passed the gates but rest on something the product model doesn't show "
             f"({', '.join(PREMISE)}), so the CONDITIONAL fallback can't carry them.",
             f"- {len(waiting)} wait on a person (`human-queue.md`).", ""]
    lines += [f"- {d.candidate_id} · {candidates[d.candidate_id].title}: {d.checks_passed}/{d.checks_total}"
              + why(d, candidates[d.candidate_id]) for d in decisions]
    return "\n".join(lines) + "\n"


def exhibit(decisions: list[Decision], candidates: dict[str, Candidate], verdicts: dict[str, dict[str, Verdict]],
            judges: list[str], profile: str, mode: str) -> str:
    roles = config.roles(profile)
    count = {f: sum(d.final == f for d in decisions) for f in ("accept", "conditional", "needs_human", "reject")}
    who = ", ".join(f"{j} ({roles[j]['model']}{', ' + roles[j]['effort'] if roles[j].get('effort') else ''})"
                    for j in judges)
    lines = ["# 06 · judge", "",
             f"Judges: {who}, blind (opaque ids, no lens or model names, one candidate per call). "
             f"Economics mode: `{mode}`. {len(decisions)} candidates: {count['accept']} accepted, "
             f"{count['conditional']} conditional, {count['needs_human']} waiting on a person, "
             f"{count['reject']} rejected; {sum(bool(d.revision_of) for d in decisions)} revised once.", "",
             "| Candidate | Final | Checks | Rank | Cost mark |", "|---|---|---|---|---|"]
    lines += [f"| {d.candidate_id} · {candidates[d.candidate_id].title} | {d.final} | {d.checks_passed}/{d.checks_total}"
              f" | {'' if d.rank_score is None else f'{d.rank_score:g}'} | {d.economics_verdict or ''} |"
              for d in decisions]
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
