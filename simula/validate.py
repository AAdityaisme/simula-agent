"""Judge validation: planted defects and known-good ideas from tests/fixtures/judge/, the report and gate
(ARCHITECTURE §5), blind human labels, re-freezing the judge prompts, and one arm of a pre-registered experiment.

    uv run python -m simula.validate validate-judge [--profile dev] [--judges judge_1,judge_2] [--no-cache]
    uv run python -m simula.validate label [--limit 15] [--out validation/latest]
    uv run python -m simula.validate freeze
    uv run python -m simula.validate experiment NAME ARM
"""

import argparse
import hashlib
import json
import math
import random
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from simula import config, economics, llm, runfolder
from simula.config import ROOT
from simula.contracts import (GATES, JUDGMENT, Candidate, CandidateDraft, Check, ProductModel, SavedVerdict, Strict,
                              Verdict)
from simula.runfolder import write_json_atomic
from simula.runlog import read_trace
from simula.stages import judge, propose

FIXTURES = ROOT / "tests" / "fixtures"
CASES = FIXTURES / "judge"
REPORT = ROOT / "validation" / "report.md"
VERDICTS = ROOT / "validation" / "verdicts"
OUT = ROOT / "validation" / "latest"
EXPERIMENTS = ROOT / "validation"  # experiment-<name>/registration.toml
SLUG = re.compile(r"[a-z0-9-]+")
PIN = CASES / "pin.json"
# pin.json fills every optional part of the judge's message; pin-empty.json takes each empty branch.
PINS = [PIN, CASES / "pin-empty.json"]
LLM_CHECKS = GATES + JUDGMENT
C8 = "c8_economics"
JUDGES = ["judge_1", "judge_2"]
KNOWN_GOOD_BAR = 0.70
MIN_APP_TYPES = 3
LABEL_TARGET = 15
LABEL_SEED = 20260930  # fixed, so `label` shows cases in the same order on every run
Z95 = 1.96


# ---------- fixture files ----------

class KnownGood(Strict):
    """tests/fixtures/judge/known_good/<id>.json: a good idea for one app, unmutated."""
    id: str
    source: Literal["base", "run"]
    from_run: str = ""
    app: str
    app_type: str
    in_test_set: bool
    model: str
    author: str
    filled_by: dict[str, Literal["aadi", "agent"]] = {}
    note: str = ""
    candidate: dict


class Planted(Strict):
    """tests/fixtures/judge/planted/<id>.json: a known-good base with the fields its defect touches changed, or a real
    candidate from a run that fails one check (`from_run`, `candidate`, and the app fields instead)."""
    id: str
    target: Literal[GATES + JUDGMENT + (C8,)]
    tier: Literal["flagrant", "subtle"]
    one_liner: str
    author: str
    expanded_by: str = ""
    base: str | None = None
    change: dict | None = None
    from_run: str = ""
    app: str = ""
    app_type: str = ""
    in_test_set: bool = True
    model: str = ""
    candidate: dict | None = None
    expect_economics: Literal["PASS", "CONDITIONAL", "FAIL", "dropped"] | None = None


class Regression(Strict):
    """tests/fixtures/judge/regression/<id>.json: a real idea the judges must fail, on `target` when it names a check
    and on any check otherwise, with the product model its judge saw (a sketch of only the fields the judge reads).
    Held out of the gate; the report shows its verdict."""
    id: str
    target: Literal[GATES + JUDGMENT] | None = None
    from_run: str
    app: str
    note: str
    model: str
    candidate: dict


@dataclass
class Case:
    id: str
    source: str
    candidate: Candidate
    model: ProductModel
    app: str
    app_type: str
    in_test_set: bool
    target: str | None = None
    tier: str | None = None
    expect_economics: str | None = None


ZERO = {"x": 0, "y": 0, "w": 0, "h": 0}
ELEMENT = dict(mcp_ref=None, type="", text="", label="", source="mcp", rect_px=ZERO, rect_dp=ZERO, role="",
               asset_png=None, fg_hex=None, bg_hex=None, font_px=None, font_guess="", in_mock=True, repeat_group=None)
STATE = dict(kind="screen", parent_id=None, fingerprint="", canonical_png="", in_mock_scope=True,
             content_rating="safe", dynamic_regions=[], blocked_reason=None)
MODEL = dict(app_version="", run_id="fixture", device={}, edges=[], flows=[], mechanics=[], cross_screen_values=[],
             value_ledger=[], open_questions=[], provenance={"source": "fixture"},
             coverage=dict(states_found=0, actions_taken=0, stop_reason="fixture", checklist_answered=[],
                           checklist_open=[]))


def load_model(ref: str) -> ProductModel:
    """A golden product model, or a sketch of one for an app outside the test set."""
    return fill_model(json.loads((FIXTURES / ref).read_text()))


def fill_model(data: dict) -> ProductModel:
    """A sketch writes only what a judge reads (app, app_category, states with name, purpose, content_rating and
    elements with id, text, role, mechanics, value_ledger, open_questions); code fills the rest with empty
    defaults. A full product model passes through unchanged."""
    states = [{**STATE, **s, "elements": [{**ELEMENT, **e} for e in s.get("elements", [])]}
              for s in data.get("states", [])]
    return ProductModel.model_validate({**MODEL, **data, "states": states})


def pinned_message(pin: Path = PIN) -> str:
    """The judge's user message for a pinned candidate (tests/fixtures/judge/pin*.json), rendered by the stage's own
    code; its hash is frozen with the prompts."""
    data = json.loads(pin.read_text())
    return judge.judge_messages(as_candidate(data["candidate"], "pin"), fill_model(data["model"]))[0]["content"][0]["text"]


def as_candidate(draft: dict, case_id: str) -> Candidate:
    """A fixture's candidate fields as a Candidate; the id is the case id, `lens` may be left out, and a real
    candidate's bucket label isn't doubled."""
    c = Candidate(**{"lens": "fixture", **draft, "id": case_id})
    return propose.with_bucket(c.model_copy(update={"title": judge.strip_bucket(c.title)}))


def mutate(base: dict, change: dict) -> dict:
    return {**base, **{field: {**base[field], **value} if isinstance(base[field], dict) and isinstance(value, dict)
                       else value for field, value in change.items()}}


def check_test_set(name: str, in_test_set: bool, ref: str) -> None:
    """The out-of-test-set spread can't be faked with a label: a test-set app uses its golden model, any other app
    brings its own sketch."""
    if in_test_set != ref.startswith("golden/"):
        raise ValueError(f"{name}: in_test_set is {in_test_set} but its model is {ref}; a test-set app uses a golden "
                         "model and any other app a sketch under judge/known_good/models/")


def load_cases(root: Path = CASES) -> list[Case]:
    goods = {g.id: g for g in (KnownGood.model_validate_json(p.read_text())
                               for p in sorted((root / "known_good").glob("*.json")))}
    planted = [(path, Planted.model_validate_json(path.read_text()))
               for path in sorted((root / "planted").glob("*.json"))]
    for g in goods.values():
        check_test_set(f"known_good/{g.id}.json", g.in_test_set, g.model)
    refs = {g.model for g in goods.values()} | {p.model for _, p in planted if p.model}
    models = {ref: load_model(ref) for ref in refs}
    cases = [Case(g.id, g.source, as_candidate(g.candidate, g.id), models[g.model], g.app, g.app_type,
                  g.in_test_set) for g in goods.values()]
    regressions = [Regression.model_validate_json(p.read_text()) for p in sorted((root / "regression").glob("*.json"))]
    cases += [Case(r.id, "regression", as_candidate(r.candidate, r.id), load_model(r.model), r.app, "", True, r.target)
              for r in regressions]
    for path, p in planted:
        if (p.target == C8) != (p.expect_economics is not None):
            raise ValueError(f"{path.name}: expect_economics is set on, and only on, a {C8} case")
        if p.candidate is not None:
            if p.base or p.change or not (p.from_run and p.model and p.app and p.app_type):
                raise ValueError(f"{path.name}: a real candidate needs from_run, model, app, and app_type, "
                                 "and no base or change")
            check_test_set(path.name, p.in_test_set, p.model)
            cases.append(Case(p.id, "planted", as_candidate(p.candidate, p.id), models[p.model], p.app, p.app_type,
                              p.in_test_set, p.target, p.tier, p.expect_economics))
            continue
        if p.base not in goods:
            raise ValueError(f"{path.name}: base {p.base!r} is not a known_good id")
        if not p.change or not set(p.change) <= CandidateDraft.model_fields.keys():
            raise ValueError(f"{path.name}: change must name candidate fields, got {list(p.change or {})}")
        g = goods[p.base]
        if missing := [f for f in p.change if f not in g.candidate]:
            raise ValueError(f"{path.name}: change names {missing}, which base {p.base!r} doesn't have")
        candidate = as_candidate(mutate(g.candidate, p.change), p.id)
        cases.append(Case(p.id, "planted", candidate, models[g.model], g.app, g.app_type, g.in_test_set,
                          p.target, p.tier, p.expect_economics))
    return cases


# ---------- scoring ----------

def known_good(case: Case) -> bool:
    return case.source in ("base", "run")


def passes_all(v: Verdict | None) -> bool:
    return v is not None and not any(judge.failed(v, k) for k in LLM_CHECKS)


def caught(case: Case, v: Verdict | None) -> bool:
    return v is not None and judge.failed(v, case.target)


def combined(verdicts: dict[str, Verdict | None]) -> tuple[bool, set[str]]:
    """The stage's rule over every judge: (every judge passes every check, checks any judge failed). A check counts
    as caught when any judge fails it: a gate any judge fails rejects; a judgment check every judge fails rejects;
    one only some judges fail is a split, which the deck lists on its Needs your call page and draws only when a
    person approves that disagreement, or, in a deck with nothing accepted, as the closest idea (D10, D11)."""
    ran = [v for v in verdicts.values() if v]
    return (len(ran) == len(verdicts) and all(passes_all(v) for v in ran)), set(judge.failed_by_any(ran))


def economics_result(c: Candidate, model: ProductModel) -> str:
    if economics.input_problem(c):
        return "dropped"
    return economics.annotate(c, model).verdict


def wilson_lower(k: int, n: int, z: float = Z95) -> float:
    if n == 0:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def kappa(a: list[bool], b: list[bool]) -> float | None:
    if not a:
        return None
    n = len(a)
    agree = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if chance == 1 else (agree - chance) / (1 - chance)


def pair_complete(group: list[Case]) -> bool:
    return len(group) == 2 and {c.tier for c in group} == {"flagrant", "subtle"}


def rate(k: int, n: int) -> str:
    return f"{k}/{n}" + (f" ({k / n:.0%})" if n else "")


# ---------- the report ----------

def report(cases: list[Case], verdicts: dict[tuple[str, str], Verdict | None],
           reruns: dict[tuple[str, str], Verdict | None], labels: dict[str, dict], judges: list[str],
           fallbacks: list[str] = ()) -> tuple[str, bool]:
    """validation/report.md and whether the merge gate passes. `verdicts` and `reruns` are keyed by
    (case id, judge); a None is a call that failed twice and counts against the judge."""
    planted = [c for c in cases if c.source == "planted" and c.target != C8]
    subtle = [c for c in planted if c.tier == "subtle"]
    goods = [c for c in cases if known_good(c)]
    c8 = [c for c in cases if c.target == C8]
    columns = [*judges, "combined"] if len(judges) > 1 else judges

    def got(case: Case, who: str) -> Verdict | None:
        return verdicts.get((case.id, who))

    def is_caught(case: Case, who: str) -> bool:
        if who == "combined":
            return case.target in combined({j: got(case, j) for j in judges})[1]
        return caught(case, got(case, who))

    def is_passed(case: Case, who: str) -> bool:
        if who == "combined":
            return combined({j: got(case, j) for j in judges})[0]
        return passes_all(got(case, who))

    per_check = {k: [c for c in planted if c.target == k] for k in LLM_CHECKS}
    complete = all(pair_complete(v) for v in [*per_check.values(), c8])
    types = sorted({c.app_type for c in planted + c8})
    outside = sorted({c.app for c in planted + c8 if not c.in_test_set})
    spread = len(types) >= MIN_APP_TYPES and bool(outside)
    c8_right = [c for c in c8 if economics_result(c.candidate, c.model) == c.expect_economics]

    lines = ["# Judge validation", "", f"Generated {datetime.now().isoformat(timespec='minutes')}. Judges: "
             + ", ".join(judges) + ". Prompts frozen in `config/frozen_prompts.toml`.", "",
             f"Fixtures: {len(planted)} planted LLM cases ({len(subtle)} subtle), {len(c8)} C8 cases, "
             f"{len(goods)} known-good (bases and real-run ideas); "
             f"planted app types: {', '.join(types) or 'none'}; planted apps outside the test set: "
             f"{', '.join(outside) or 'none'}."]
    if not complete:
        lines += ["", f"**Fixtures incomplete:** the gate needs 1 flagrant + 1 subtle planted case for each of the "
                  f"{len(LLM_CHECKS)} LLM-judged checks and for {C8} ({2 * len(LLM_CHECKS) + 2} cases); "
                  + ", ".join(f"{k} has {len(v)}" for k, v in [*per_check.items(), (C8, c8)] if not pair_complete(v))
                  + "."]
    lines += [f"- Declared model fallback used: {f}" for f in fallbacks]

    lines += ["", "## Headline: recall over every LLM-judged planted defect (C8 excluded)", "",
              "The pooled rate and its Wilson 95% lower bound are the claim this report supports.", "",
              "| Judge | All planted | Wilson 95% lower | Subtle only | Wilson 95% lower |", "|---|---|---|---|---|"]
    for who in columns:
        k, ks = sum(is_caught(c, who) for c in planted), sum(is_caught(c, who) for c in subtle)
        lines.append(f"| {who} | {rate(k, len(planted))} | {wilson_lower(k, len(planted)):.3f} | "
                     f"{rate(ks, len(subtle))} | {wilson_lower(ks, len(subtle)):.3f} |")

    lines += ["", "## Planted defects caught vs known-good passed", "",
              "A judge that fails everything catches every defect and passes no known-good idea; read both rows.", ""]
    for who in columns:
        hit = sum(is_caught(c, who) for c in planted)
        ok = sum(is_passed(c, who) for c in goods)
        lines += [f"**{who}**", "", "| | caught / passed | missed / failed |", "|---|---|---|",
                  f"| Planted defects ({len(planted)}) | {hit} | {len(planted) - hit} |",
                  f"| Known-good ({len(goods)}) | {ok} | {len(goods) - ok} |", ""]

    lines += ["## Per check: a smoke test (a check with 0 of 2 caught is broken)", "",
              "Two cases per check tell a blind check (0 of 2) from one that works. They can't tell a 50% catch rate "
              "from 100%: a check that catches half its defects still scores 2 of 2 a quarter of the time.", "",
              "| Check | " + " | ".join(columns) + " |", "|---|" + "---|" * len(columns)]
    broken = {who: [] for who in columns}
    for k, group in per_check.items():
        cells = []
        for who in columns:
            hits = [c for c in group if is_caught(c, who)]
            if len(group) == 2 and not hits:
                broken[who].append(k)
            tiers = ", ".join(f"{c.tier} {'✓' if c in hits else '✗'}" for c in sorted(group, key=lambda c: c.tier))
            cells.append(f"{len(hits)}/{len(group)} {'**broken**' if len(group) == 2 and not hits else ''} "
                         f"({tiers or 'no cases'})")
        lines.append(f"| {k} | " + " | ".join(cells) + " |")


    lines += ["", f"## Known-good pass rate (every one of the {len(LLM_CHECKS)} checks passed)", "",
              "| Judge | Known-good (gate) |", "|---|---|"]
    kg_rate = {}
    for who in columns:
        ok = sum(is_passed(c, who) for c in goods)
        kg_rate[who] = ok / len(goods) if goods else 0.0
        lines.append(f"| {who} | {rate(ok, len(goods))} |")

    flips, unverified = {}, {}
    rerun_set = [c.id for c in planted if c.target in GATES]
    lines += ["", "## `--no-cache` rerun: do safety verdicts flip?", ""]
    for who in judges:
        pairs = [(cid, verdicts.get((cid, who)), reruns.get((cid, who))) for cid in rerun_set]
        both = [(cid, a, b) for cid, a, b in pairs if a and b]
        flips[who] = [cid for cid, a, b in both if any(judge.failed(a, g) != judge.failed(b, g) for g in GATES)]
        any_flips = [cid for cid, a, b in both if any(judge.failed(a, k) != judge.failed(b, k) for k in LLM_CHECKS)]
        unverified[who] = [cid for cid, a, b in pairs if not (a and b)]
        lines.append(f"- {who}: {len(both)} of {len(pairs)} gate cases compared; safety flips {len(flips[who])}"
                     + (f" ({', '.join(flips[who])})" if flips[who] else "") + f"; any-check flips {len(any_flips)}"
                     + (f"; no second verdict for {', '.join(unverified[who])}" if unverified[who] else "") + ".")

    regressions = [c for c in cases if c.source == "regression"]
    if regressions:
        lines += ["", "## Regression cases (held out of the gate; each must fail its target, or any check without one)",
                  "", "| Case | Target | " + " | ".join(columns) + " |", "|---|---|" + "---|" * len(columns)]
        for c in regressions:
            cells = []
            for who in columns:
                ran = [got(c, j) for j in judges] if who == "combined" else [got(c, who)]
                if None in ran:
                    cells.append("? no verdict: a judge call failed")
                    continue
                fails = judge.failed_by_any(ran)
                held = c.target in fails if c.target else not is_passed(c, who)
                cells.append(("✓ " if held else "✗ ")
                             + (f"fails {', '.join(fails)}" if fails else f"passes all {len(LLM_CHECKS)}"))
            lines.append(f"| {c.id} | {c.target or 'any'} | " + " | ".join(cells) + " |")

    if c8:
        lines += ["", "## C8 economics (code, not the judges)", "", "| Case | Tier | Expected | Code says | Caught |",
                  "|---|---|---|---|---|"]
        for c in c8:
            got_econ = economics_result(c.candidate, c.model)
            lines.append(f"| {c.id} | {c.tier} | {c.expect_economics} | {got_econ} | "
                         f"{'✓' if got_econ == c.expect_economics else '✗'} |")

    lines += ["", "## Human labels (secondary; ±0.4 at n ≈ 15)", ""]
    for who in judges:
        labeled = [c for c in cases if c.id in labels and got(c, who)]
        k = kappa([labels[c.id]["overall"] == "pass" for c in labeled], [passes_all(got(c, who)) for c in labeled])
        lines.append(f"- {who}: kappa {'n/a' if k is None else f'{k:.2f}'} over {len(labeled)} labels.")

    gate = [(f"fixtures complete (1 flagrant + 1 subtle per LLM check and for {C8})", complete),
            (f"planted defects span ≥ {MIN_APP_TYPES} app types, one app outside the test set "
             f"({len(types)} types, {len(outside)} outside)", spread),
            (f"{C8}: the economics code gives the expected result ({len(c8_right)}/{len(c8)})",
             bool(c8) and len(c8_right) == len(c8))]
    for who in judges:
        flagrant_gates = [c for c in planted if c.target in GATES and c.tier == "flagrant"]
        gate += [(f"{who}: no LLM check broken", complete and not broken[who]),
                 (f"{who}: every flagrant gate defect caught "
                  f"({sum(is_caught(c, who) for c in flagrant_gates)}/{len(flagrant_gates)})",
                  len(flagrant_gates) == len(GATES) and all(is_caught(c, who) for c in flagrant_gates)),
                 (f"{who}: known-good ≥ {KNOWN_GOOD_BAR:.0%} ({kg_rate[who]:.0%})",
                  bool(goods) and kg_rate[who] >= KNOWN_GOOD_BAR),
                 (f"{who}: no safety flip on the --no-cache rerun, every gate case compared",
                  bool(rerun_set) and not flips[who] and not unverified[who])]
    passed = all(ok for _, ok in gate)
    lines += ["", f"## Gate: {'PASS' if passed else 'FAIL'}", ""] + [f"- {'✓' if ok else '✗'} {what}" for what, ok in gate]
    if not passed:
        lines += ["", "A failure means fixing that judge's prompt and re-freezing; never loosening the rule."]
    return "\n".join(lines) + "\n", passed


# ---------- running it ----------

def verdict_path(out: Path, case_id: str, who: str, round_: int) -> Path:
    return out / "verdicts" / f"{case_id}_{who}_r{round_}.json"


def run_judges(cases: list[Case], judges: list[str], roles: dict, budget: llm.Budget, out: Path, round_: int,
               no_cache: bool, rubric: str | None = None) -> dict[tuple[str, str], Verdict | None]:
    def one(job):
        case, who = job
        try:
            v = judge.ask_judge(roles[who], case.candidate, case.model, trace_path=out / "trace.jsonl",
                                stage="validate", step=f"{case.id}:{who}:r{round_}", budget=budget, no_cache=no_cache,
                                rubric=rubric)
        except llm.LLMFailure:
            return job, None
        write_json_atomic(verdict_path(out, case.id, who, round_), v.model_dump_json(indent=1))
        return job, v

    jobs = [(c, j) for c in cases for j in judges]
    with ThreadPoolExecutor(max_workers=8) as pool:
        return {(case.id, who): v for (case, who), v in pool.map(one, jobs)}


def read_labels(root: Path = CASES) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((root / "labels").glob("*.json"))}


def validate_judge(profile: str, judges: list[str], no_cache: bool, out: Path = OUT) -> bool:
    judge.check_frozen()
    cases = load_cases()
    llm_cases = [c for c in cases if c.target != C8]
    (out / "verdicts").mkdir(parents=True, exist_ok=True)
    roles = config.roles(profile)
    cap = config.profiles()["validation_usd_cap"]
    budget = llm.Budget.for_stage("validate", out / "trace.jsonl", cap)
    verdicts = run_judges(llm_cases, judges, roles, budget, out, 1, no_cache)
    rerun_set = [c for c in llm_cases if c.target in GATES]
    reruns = run_judges(rerun_set, judges, roles, budget, out, 2, no_cache=True)
    fallbacks = [line.note for line in read_trace(out / "trace.jsonl") if "declared fallback" in line.note]
    text, passed = report(cases, verdicts, reruns, read_labels(), judges, list(dict.fromkeys(fallbacks)))
    (out / "report.md").write_text(text)
    spent = sum(line.usd for line in read_trace(out / "trace.jsonl") if line.stage == "validate")
    print(f"{out / 'report.md'}: gate {'PASS' if passed else 'FAIL'}; ${spent:.2f} spent in total "
          f"under the ${cap:.0f} validation cap")
    return passed


# ---------- a pre-registered experiment (validation/EXPERIMENT-J.md) ----------

def experiment(name: str, arm: str, registered: Path = EXPERIMENTS) -> None:
    """One arm of a pre-registered experiment, run only as experiment-<name>/registration.toml says: the arm's rubric
    file (the tracked prompt is never edited), the judges, profile and cache use, and the dollar cap its arms share.
    Each judge judges every LLM fixture once; a failed call counts as a miss or a fail. The arm's settings, verdicts,
    trace and report go to the tracked experiment-<name>/<arm>/, to be committed before the next arm runs.

    Before any call or write it refuses unless: the experiment folder (registration and earlier arms) and the labels
    are committed; the labels are enough and include every known-good idea; the rubric has its registered sha256; the
    arm's folder is neither on disk nor anywhere in git history, so a re-run shows in git; and every earlier arm ran
    on the same inputs (experiment_inputs)."""
    for slug in (name, arm):
        if not SLUG.fullmatch(slug):
            raise SystemExit(f"experiment and arm names are plain slugs ([a-z0-9-]), not {slug!r}")
    root = registered / f"experiment-{name}"
    registration, folder = root / "registration.toml", root / arm
    if not registration.exists():
        raise SystemExit(f"experiment {name} isn't registered: there is no {registration}")
    reg = tomllib.loads(registration.read_text())
    if arm not in reg["arms"]:
        raise SystemExit(f"experiment {name} registers the arms {', '.join(reg['arms'])}, not {arm}")
    if folder.exists():
        raise SystemExit(f"arm {arm} of experiment {name} already ran ({folder}); an arm runs once")
    if ran := last_commit(folder):
        raise SystemExit(f"arm {arm} of experiment {name} already ran: its folder is in git history at {ran}. An arm "
                         "runs once; a new draw needs a new registered arm")
    if pending := uncommitted([root, CASES / "labels"]):
        raise SystemExit(f"experiment {name} runs only on committed files; commit these first: {', '.join(pending[:5])}"
                         + (f" and {len(pending) - 5} more" if len(pending) > 5 else ""))
    rubric = ROOT / reg["arms"][arm]["rubric"]
    text = rubric.read_text()
    digest = hashlib.sha256(text.encode()).hexdigest()
    if digest != reg["arms"][arm]["sha256"]:
        raise SystemExit(f"{rubric} hashes to {digest}, not the {reg['arms'][arm]['sha256']} registered for arm {arm}")
    cases = [c for c in load_cases() if c.target != C8]
    labels = read_labels()
    unlabeled = [c for c in cases if known_good(c) and c.id not in labels]
    if len(labels) < reg["labels"] or unlabeled:
        raise SystemExit(f"experiment {name} needs {reg['labels']} labels, every known-good idea among them; there are "
                         f"{len(labels)}, and {len(unlabeled)} known-good idea(s) have none. Run `simula label` until "
                         "it has nothing left to show (then with a higher --limit if any are still missing), and "
                         "commit the labels.")
    inputs = experiment_inputs(registration, reg, cases, labels)
    for before in sorted(root.glob("*/settings.json")):
        earlier = json.loads(before.read_text())
        if changed := sorted(k for k in inputs.keys() | earlier["inputs"].keys()
                             if inputs.get(k) != earlier["inputs"].get(k)):
            raise SystemExit(f"arm {earlier['arm']} ran at {earlier['git_sha']} on other inputs ({', '.join(changed)}); "
                             "an experiment's arms run on the same inputs")
    judge.check_frozen()
    dirty = runfolder.git_dirty()
    (folder / "verdicts").mkdir(parents=True)
    (folder / "rubric.md").write_text(text)
    roles = config.roles(reg["profile"])
    settings = {"experiment": name, "arm": arm, "rubric": reg["arms"][arm]["rubric"], "rubric_sha256": digest,
                "git_sha": runfolder.git_sha(), "git_dirty": dirty, "profile": reg["profile"],
                "roles": {j: roles[j] for j in reg["judges"]}, "no_cache": reg["no_cache"], "usd_cap": reg["usd_cap"],
                "cases": [c.id for c in cases], "inputs": inputs}
    write_json_atomic(folder / "settings.json", json.dumps(settings, indent=1))
    budget = llm.Budget("validate", reg["usd_cap"], spent_by(root), trace_path=folder / "trace.jsonl")
    verdicts = run_judges(cases, reg["judges"], roles, budget, folder, 1, reg["no_cache"], rubric=text)
    lost = sorted(f"{cid}:{who}" for (cid, who), v in verdicts.items() if v is None)
    fallbacks = [line.note for line in read_trace(folder / "trace.jsonl") if "declared fallback" in line.note]
    body, _ = report(cases, verdicts, {}, labels, reg["judges"], list(dict.fromkeys(fallbacks)))
    failed = f"{len(lost)} ({', '.join(lost)})" if lost else "0"
    head = [f"# Experiment {name}, arm {arm}", "",
            f"Rubric `{settings['rubric']}` (sha256 {digest[:12]}), one verdict per judge per case. Failed calls, "
            f"each counted as a miss or a fail: {failed}. No `--no-cache` rerun was made, so the rerun lines below "
            "compare nothing and the harness gate can't pass; arms are compared as validation/EXPERIMENT-J.md says.",
            ""]
    (folder / "report.md").write_text("\n".join(head) + body)
    print(f"{folder / 'report.md'}: {len(lost)} failed calls; ${spent_by(root):.2f} spent by experiment {name} under "
          f"its ${reg['usd_cap']:.2f} cap. Commit {folder} before the next arm.")


def experiment_inputs(registration: Path, reg: dict, cases: list[Case], labels: dict[str, dict]) -> dict[str, str]:
    """What every arm of an experiment must share, each by sha256: the registration, every arm's rubric, the frozen
    judge prompts, the labels, and the cases as the judges see them. A commit that touches none of these may land
    between arms."""
    def short(p: Path) -> str:
        return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
    files = [registration, judge.FROZEN, *(ROOT / a["rubric"] for a in reg["arms"].values())]
    seen = [(c.id, judge.judge_messages(c.candidate, c.model)[0]["content"][0]["text"]) for c in cases]
    return {**{short(p): runfolder.sha256(p) for p in files},
            "labels": hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest(),
            "cases": hashlib.sha256(json.dumps(seen).encode()).hexdigest()}


def uncommitted(paths: list[Path]) -> list[str]:
    """The files at or under `paths` that their git checkout doesn't hold as committed (new, changed or deleted); a
    path with no checkout to ask counts whole."""
    found = []
    for path in paths:
        try:
            listed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", path.name],
                                    cwd=path.parent, capture_output=True, text=True, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
            found.append(str(path))
            continue
        found += [line[3:] for line in listed.splitlines()]
    return found


def last_commit(path: Path) -> str:
    """The last commit that touched `path` in its git checkout, or "" when none did."""
    try:
        return subprocess.run(["git", "log", "-1", "--format=%h", "--", path.name], cwd=path.parent,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return ""


def spent_by(experiment_dir: Path) -> float:
    return sum(line.usd for trace in experiment_dir.glob("*/trace.jsonl") for line in read_trace(trace))


# ---------- the committed report, rebuilt from saved runs ----------

def load_runs(runs: Path) -> dict[tuple[str, str], list[Verdict]]:
    """Saved verdicts, `<case>_<judge>_r<n>.json`, grouped by (case id, judge) in run order."""
    found: dict[tuple[str, str], list[tuple[int, Verdict]]] = {}
    for p in runs.glob("*_r*.json"):
        stem, n = p.stem.rsplit("_r", 1)
        who = next(j for j in JUDGES if stem.endswith(f"_{j}"))
        verdict = SavedVerdict.model_validate_json(p.read_text())
        found.setdefault((stem.removesuffix(f"_{who}"), who), []).append((int(n), verdict))
    return {key: [v for _, v in sorted(pairs, key=lambda x: x[0])] for key, pairs in found.items()}


def majority(runs: list[Verdict]) -> Verdict:
    """Each check by majority of the runs (a tie fails), carrying the reason of a run that agrees."""
    def agreed(k: str) -> Check:
        fail = 2 * sum(judge.failed(v, k) for v in runs) >= len(runs)
        return next(getattr(v, k) for v in runs if judge.failed(v, k) == fail)
    return runs[0].model_copy(update={k: agreed(k) for k in LLM_CHECKS})


def disagreeing(runs: list[Verdict], agreed: Verdict) -> Verdict:
    """The harness compares a verdict with one --no-cache rerun. From saved runs, the stand-in rerun is the first run
    whose gate verdicts differ from the majority's (else the first run), so a flip means the runs disagreed."""
    def gates(v: Verdict) -> set[str]:
        return {g for g in GATES if judge.failed(v, g)}
    return next((v for v in runs if gates(v) != gates(agreed)), runs[0])


def summarize(runs_dir: Path, preface: Path, out: Path = REPORT, only_judged: bool = False) -> bool:
    """validation/report.md from saved runs: the preface, then report() on each judge's majority verdicts over every
    fixture, a missing verdict counting as a miss. `only_judged` scores only the fixtures the runs judged (and the C8
    cases code scores), for runs saved before newer fixtures existed. Makes no calls, so the committed report can be
    rebuilt without re-judging."""
    runs = load_runs(runs_dir)
    judged = {cid for cid, _ in runs}
    cases = [c for c in load_cases() if not only_judged or c.id in judged or c.target == C8]
    agreed = {key: majority(vs) for key, vs in runs.items()}
    gate_cases = {c.id for c in cases if c.source == "planted" and c.target in GATES}
    reruns = {key: disagreeing(runs[key], v) for key, v in agreed.items() if key[0] in gate_cases}
    text, passed = report(cases, agreed, reruns, read_labels(), JUDGES)
    out.write_text(preface.read_text() + "\n" + text)
    return passed


# ---------- blind human labels ----------

def load_verdicts(out: Path = OUT) -> dict[tuple[str, str], Verdict]:
    found = {}
    for p in sorted((out / "verdicts").glob("*_r1.json")):
        who = next((j for j in JUDGES if p.stem.endswith(f"_{j}_r1")), None)
        if who:
            found[(p.stem.removesuffix(f"_{who}_r1"), who)] = SavedVerdict.model_validate_json(p.read_text())
    return found


def disagree(case: Case, verdicts: dict[tuple[str, str], Verdict]) -> bool:
    """The judges disagree with each other, or with what the case was built to show."""
    mine = [v for (cid, _), v in verdicts.items() if cid == case.id]
    overall = {passes_all(v) for v in mine}
    if case.source == "planted":
        return len(overall) > 1 or not all(caught(case, v) for v in mine)
    return len(overall) > 1 or False in overall


def label_order(cases: list[Case], verdicts: dict[tuple[str, str], Verdict], limit: int) -> list[Case]:
    """The order `label` shows cases in: every known-good idea and, to fill at least LABEL_TARGET, the cases the
    judges disagree on and then others at random, all shuffled together; then the rest. The seed is fixed, so a
    skipped case comes back in its place, and the order never says which case is which, whatever `limit` is."""
    rng = random.Random(LABEL_SEED)
    goods = [c for c in cases if known_good(c)]
    others = [c for c in cases if not known_good(c)]
    rng.shuffle(others)
    others.sort(key=lambda c: not disagree(c, verdicts))
    fill = max(0, max(limit, LABEL_TARGET) - len(goods))
    first = goods + others[:fill]
    rng.shuffle(first)
    return first + others[fill:]


def label_cases(cases: list[Case], verdicts: dict[tuple[str, str], Verdict], labels_dir: Path, ask=input,
                say=print, limit: int = LABEL_TARGET) -> int:
    """Shows each proposal blind, with the product model the judges get (no expected answer, no verdicts, no judge
    or arm), in label_order, and asks for an overall pass/fail and the deciding check; the verdicts are shown after.
    Returns how many were labeled."""
    labels_dir.mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in labels_dir.glob("*.json")}
    order = label_order([c for c in cases if c.target != C8], verdicts, limit)
    todo = [c for c in order if c.id not in done]
    labeled = 0
    for case in todo[:max(0, limit - len(done))]:
        shown = judge.judge_messages(case.candidate, case.model)[0]["content"][0]["text"]
        say(f"\n=== {case.app} ===\n{shown}\n")
        answer = ask("Overall: [p]ass, [f]ail, [s]kip, [q]uit? ").strip().lower()[:1]
        if answer == "q":
            break
        if answer not in ("p", "f"):
            continue
        deciding = None
        if answer == "f":
            deciding = ask(f"Deciding check ({', '.join(LLM_CHECKS + (C8,))}, or other)? ").strip() or "other"
        label = {"case_id": case.id, "overall": "pass" if answer == "p" else "fail", "deciding_check": deciding,
                 "labeled_at": datetime.now().isoformat(timespec="seconds")}
        write_json_atomic(labels_dir / f"{case.id}.json", json.dumps(label, indent=1))
        labeled += 1
        for (cid, who), v in sorted(verdicts.items()):
            if cid == case.id:
                fails = [f"{k}: {getattr(v, k).reason}" for k in LLM_CHECKS if judge.failed(v, k)]
                say(f"{who}: " + (f"passes all {len(LLM_CHECKS)}" if not fails else "fails " + " | ".join(fails)))
        if case.source == "planted":
            say(f"Planted to fail {case.target} ({case.tier}).")
    return labeled


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    config.load_env()
    p = argparse.ArgumentParser(prog="python -m simula.validate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate-judge", help="judge every fixture, write validation/latest/report.md (never the "
                       "committed validation/report.md), exit 1 on a gate fail")
    v.add_argument("--profile", choices=["real", "dev"], default="real")
    v.add_argument("--judges", default=",".join(JUDGES), help="comma-separated judge roles")
    v.add_argument("--no-cache", action="store_true")
    v.add_argument("--out", type=Path, default=OUT)
    lab = sub.add_parser("label", help="blind human labels, in a fixed shuffled order")
    lab.add_argument("--limit", type=int, default=LABEL_TARGET)
    lab.add_argument("--out", type=Path, default=OUT, help="where validate-judge wrote its verdicts")
    s = sub.add_parser("summarize", help="rebuild validation/report.md from saved runs; makes no calls")
    s.add_argument("--runs", type=Path, default=VERDICTS / "VF3", help="the live rubric's saved runs")
    s.add_argument("--preface", type=Path, default=REPORT.parent / "preface.md")
    s.add_argument("--out", type=Path, default=REPORT)
    s.add_argument("--only-judged", action="store_true",
                   help="score only the fixtures the runs judged, for runs saved before newer fixtures existed")
    sub.add_parser("freeze", help="pin the judge prompt hashes in config/frozen_prompts.toml")
    e = sub.add_parser("experiment", help="one arm of a pre-registered judge experiment, run as "
                       "validation/experiment-<name>/registration.toml says, into the tracked "
                       "validation/experiment-<name>/<arm>/")
    e.add_argument("name")
    e.add_argument("arm")
    args = p.parse_args(argv)
    if args.command == "validate-judge":
        return 0 if validate_judge(args.profile, args.judges.split(","), args.no_cache, args.out) else 1
    if args.command == "summarize":
        passed = summarize(args.runs, args.preface, args.out, args.only_judged)
        print(f"{args.out}: harness gate {'PASS' if passed else 'FAIL'} (rebuilt from {args.runs}, no calls)")
        return 0
    if args.command == "experiment":
        experiment(args.name, args.arm)
        return 0
    if args.command == "label":
        n = label_cases(load_cases(), load_verdicts(args.out), CASES / "labels", limit=args.limit)
        print(f"{n} labeled; labels in {CASES / 'labels'}")
        return 0
    for path, digest in judge.freeze().items():
        print(f"frozen {path} {digest[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
