"""Goal 3 review: blind judges score every candidate, code turns their verdicts into a decision, fixable
rejects get one Opus revision, and code ranks what survives."""

import hashlib
import json
import os
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
MAX_TOKENS = 16000
SURVIVORS = ("accept", "conditional")
ECON_CONDITION = {"CONDITIONAL": "The cost to serve isn't known", "FAIL": "It may cost more to serve than a view earns"}
DEFAULT_JUDGES = ["judge_1"]


class PromptsChanged(RuntimeError):
    pass


# ---------- frozen prompts ----------

def prompt_hashes() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): sha256(p) for p in sorted(PROMPTS.glob("*.md"))}


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
    """The proposal as a judge sees it: no id, lens, economics, rank, or propose's drop reason."""
    return c.model_dump(exclude={"id", "lens", "bible_mechanic", "cost_inputs", "economics", "reach_score",
                                 "rank_score", "dropped_reason"})


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
    rejects; a split goes to a person; an accept whose cost line isn't a PASS becomes conditional."""
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
    return d.model_copy(update={"final": "accept" if econ == "PASS" else "conditional"})


def revisable(c: Candidate, d: Decision, verdicts: list[Verdict]) -> bool:
    return (d.final == "reject" and is_idea(c) and not c.dropped_reason and bool(verdicts)
            and any(v.fixable for v in verdicts))


def fallback_pick(decisions: list[Decision], candidates: dict[str, Candidate],
                  verdicts: dict[str, dict[str, Verdict]], superseded: set[str]) -> str | None:
    """When nothing survives, the best reject that passes every gate and both premise checks: fewest checks
    failed, then rank. None when there is no such candidate."""
    if any(d.final in SURVIVORS for d in decisions):
        return None
    eligible = [d for d in decisions if d.final == "reject" and d.candidate_id not in superseded
                and is_idea(candidates[d.candidate_id]) and not candidates[d.candidate_id].dropped_reason
                and verdicts.get(d.candidate_id) and not d.gate_fails
                and not set(PREMISE) & set(failed_by_any([*verdicts[d.candidate_id].values()]))]
    best = min(eligible, key=lambda d: (-d.checks_passed, -(d.rank_score or 0)), default=None)
    return best and best.candidate_id


def ordered(decisions: list[Decision]) -> list[Decision]:
    """Survivors first by rank_score (code), then those waiting on a person, then rejects by score."""
    tier = {"accept": 0, "conditional": 0, "needs_human": 1, "reject": 2}
    return sorted(decisions, key=lambda d: (tier[d.final], -(d.rank_score or 0), -d.checks_passed))


def condition(d: Decision, c: Candidate, verdicts: list[Verdict]) -> str | None:
    """The one sentence a CONDITIONAL candidate carries on its slides."""
    if d.final != "conditional":
        return None
    parts = []
    fails = [k for k in JUDGMENT if k in failed_by_any(verdicts)]
    if fails:
        reason = next(getattr(v, fails[0]).reason for v in verdicts if failed(v, fails[0]))
        parts.append(f"No idea passed every check; this one passes every safety gate but not {', '.join(fails)} "
                     f"({reason})")
    if d.economics_verdict in ECON_CONDITION and c.economics:
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
    new = economics.apply([new.model_copy(update={"dropped_reason": reason})], model.app_category, mode)[0]
    return propose.with_bucket(propose.rank(new, model, mode))


# ---------- stage ----------

def judge_all(ctx: Ctx, candidates: list[Candidate], model: ProductModel, judges: list[str], budget: llm.Budget,
              round_: int) -> tuple[dict[str, dict[str, Verdict]], dict[str, list[str]]]:
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
        path = f"judge/verdicts/{c.id}_{judge}_r{round_}.json"
        write_json_atomic(ctx.run_dir / path, v.model_dump_json(indent=1))
        return c.id, judge, v, path

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
    out = ctx.run_dir / "judge"
    (out / "verdicts").mkdir(parents=True, exist_ok=True)
    mode = config.profiles()["economics_mode"]
    judges = config.profiles().get("judges", DEFAULT_JUDGES)
    budget = llm.Budget.for_stage("judge", ctx.run_dir / "trace.jsonl", ctx.usd_cap)

    verdicts, paths = judge_all(ctx, candidates, model, judges, budget, 1)
    decisions = {c.id: decide(c, [*verdicts[c.id].values()], len(judges), mode, paths[c.id]) for c in candidates}
    to_revise = [c for c in candidates if revisable(c, decisions[c.id], [*verdicts[c.id].values()])]
    with ThreadPoolExecutor(max_workers=max(1, len(to_revise))) as pool:
        revised = [r for r in pool.map(lambda c: revise(ctx, c, [*verdicts[c.id].values()], model, mode, budget),
                                       to_revise) if r]
    if revised:
        v2, p2 = judge_all(ctx, revised, model, judges, budget, 2)
        verdicts |= v2
        decisions |= {r.id: decide(r, [*v2[r.id].values()], len(judges), mode, p2[r.id],
                                   revision_of=r.id.removesuffix("-rev")) for r in revised}

    everyone = {c.id: c for c in candidates + revised}
    superseded = {r.id.removesuffix("-rev") for r in revised}
    pick = fallback_pick(list(decisions.values()), everyone, verdicts, superseded)
    if pick:
        decisions[pick] = decisions[pick].model_copy(update={"final": "conditional"})
        run_trace(ctx.run_dir, stage="judge", step="fallback", decider="code",
                  note=f"nothing accepted; {pick} passes every gate and both premise checks -> CONDITIONAL")
    final = ordered(list(decisions.values()))
    for d in final:
        run_trace(ctx.run_dir, stage="judge", step=f"decide:{d.candidate_id}", decider="code",
                  outcome="ok" if d.final in SURVIVORS else "denied",
                  note=f"{d.final} {d.checks_passed}/{d.checks_total}" + why(d, everyone[d.candidate_id]))

    write_json_atomic(out / "revisions.json", CandidatesFile(candidates=revised).model_dump_json(indent=1))
    write_json_atomic(out / "decisions.json", DecisionsFile(decisions=final).model_dump_json(indent=1))
    write_queue(ctx, final, everyone, verdicts)
    if not any(d.final in SURVIVORS for d in final):
        (out / "no-opportunity.md").write_text(no_opportunity(final, everyone, verdicts))
    write_exhibit(ctx.run_dir, 6, "judge", exhibit(final, everyone, verdicts, judges, ctx.profile, mode))


def why(d: Decision, c: Candidate) -> str:
    if c.dropped_reason:
        return f"; dropped by propose: {c.dropped_reason}"
    parts = ([f"gates failed: {', '.join(d.gate_fails)}"] if d.gate_fails else []) \
        + ([f"split: {', '.join(d.judgment_splits)}"] if d.judgment_splits else []) \
        + ([f"rerun {d.rerun_stage} (human-gated)"] if d.rerun_stage else [])
    return "; " + "; ".join(parts) if parts else ""


# ---------- human-readable outputs ----------

def write_queue(ctx: Ctx, decisions: list[Decision], candidates: dict[str, Candidate],
                verdicts: dict[str, dict[str, Verdict]]) -> None:
    waiting = [d for d in decisions if d.final == "needs_human"]
    if not waiting:
        return
    app, run_id = ctx.app["name"], ctx.run_dir.name
    lines = ["# Judge: candidates waiting on a person", "",
             "The run continued without them. Nothing reruns on its own.", ""]
    for d in waiting:
        c = candidates[d.candidate_id]
        lines += [f"## {c.id} · {c.title}", ""]
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
    (ctx.run_dir / "judge" / "human-queue.md").write_text("\n".join(lines))
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
             "| Candidate | Final | Checks | Rank | Cost line |", "|---|---|---|---|---|"]
    lines += [f"| {d.candidate_id} · {candidates[d.candidate_id].title} | {d.final} | {d.checks_passed}/{d.checks_total}"
              f" | {'' if d.rank_score is None else f'{d.rank_score:g}'} | {d.economics_verdict or ''} |"
              for d in decisions]
    for d in decisions:
        c = candidates[d.candidate_id]
        lines += ["", f"## {c.id} · {c.title}", "",
                  f"- **{d.final}**, {d.checks_passed}/{d.checks_total} checks passed by every judge"
                  + (f", revision of {d.revision_of}" if d.revision_of else "") + why(d, c)]
        if text := condition(d, c, [*verdicts.get(c.id, {}).values()]):
            lines.append(f"- Condition: {text}")
        if c.economics:
            lines.append(f"- Cost: {c.economics.assumption_line}")
        for judge, v in verdicts.get(c.id, {}).items():
            lines += [f"- {judge}: " + ("fixable" if v.fixable else "not fixable")
                      + (f"; other concern: {v.other_concern}" if v.other_concern else "")]
            lines += [f"  - {'✗' if failed(v, k) else '✓'} {k}: {getattr(v, k).reason}" for k in CHECKS]
    return "\n".join(lines) + "\n"
