"""The creative-generation MVP on the committed Luzia run (spec section 6). Four commands, run from exp-connect:

    uv run python tools/creative_mvp.py run        # facts, 4 variants (paid, $6 cap), review.csv, starvation, report
    uv run python tools/creative_mvp.py merge --reviewer fable=PATH --reviewer astra=PATH
                                                   # each model reviewer's filled copy of review.csv -> review.csv
    uv run python tools/creative_mvp.py finalize   # applies the merged verdicts
    uv run python tools/creative_mvp.py check      # recomputes every number in report.md; exit 1 on a mismatch

report.json is rebuilt from the files beside it (facts.json, run.json, variants/*, trace.jsonl, starvation/,
wrapper.json, s04_fixture.json) every time, and report.md is rendered from report.json, so check can recompute both."""

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import sys
from pathlib import Path

from simula import config, llm
from simula.config import ROOT
from simula.creative import th1
from simula.creative.assemble import SDK_EVENTS, TEMPLATE, font_css, proof_png, visible_strings
from simula.creative.facts import Facts, build_facts
from simula.creative.lines import PROMPT, HostLines, build_content
from simula.creative.policy import EPSILON_NEW, N_MIN, mix
from simula.creative.qa import MODES, PATHS, WIDTHS, grounding, tier
from simula.creative.repair import Draft, VariantResult, generate_variant, variant_result
from simula.creative.schema import (PROVENANCE, QA, Advertiser, Content, CreativeAttributes, EndCard, Interaction,
                                    Lineage, Proof, creative_id, dump, gate_for)
from simula.runlog import read_trace

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
OUT = ROOT / "evidence" / "creative-2026-10-04"
SEED = 20261004
HOOKS = ("challenge", "help_host")
CAP_USD = 6.0
REQUESTS = 10_000
BRIEF_ID = "br_luzia_v0_grid"
GENERATOR = "creative-v0"
REVIEW_FIELDS = ("variant", "screen", "string", "source", "verdict", "edit")
VERDICTS = ("ok", "edit", "reject")  # least to most strict: a merge keeps the strictest
REVIEWER = re.compile(r"\w[\w .-]*")
NUMBERS = ("variants_total", "first_pass_accept", "final_checks_pass", "final_accept", "review_edits", "usd_total",
           "usd_per_accepted", "seconds_per_variant", "starvation_share", "starvation_ci", "starvation_requests",
           "wrapper_min_propensity")
FIXTURE_LINES = HostLines(intro="Three quick ones!", captions=["One", "Two", "Three"], right_line="Yes!",
                          wrong_hint="Try again", end_headline="Done")
EMAIL = (
    "Your AI-Batman-to-DC idea is the part I tested first. In take-home 1's data it can't be tested: creatives are "
    "hashed ids, so a creative-by-genre effect that looks huge turns out to be where each ad ran (with exact surface "
    "controls p = 0.33, and tailoring by genre lost about 0.8 pp out of sample). An attribute record per creative, plus "
    "logged exploration, is what would make it testable. So I built that end: take-home 2's explorer reads an "
    "advertiser's app (Luzia), and a fixed mini-game template has the app's own Teacher or Toki pose three quick "
    "puzzles that code generates and answers. A proof card shows only exact strings from the app, next to a real "
    "screenshot. On {variants_total} variants, {first_pass_accept} passed every automatic check on the first draft "
    "and {final_checks_pass} after one repair round; {accepted}. On "
    "take-home 1's own held-out traffic, its ranker gave every unseen real creative zero chance in {starvation_share} "
    "of decisions where a seen one was eligible, so I added an exploration layer with exact logged probabilities. The "
    "limit: your sample advertisers are games, and my explorer reads element trees, so canvas games need a second "
    "fact source (store listing or an advertiser brief). Could you share one representative advertiser asset pack, "
    "and how a creative id joins to impressions and postbacks today?")
NOT_CLAIMED = (
    "No measured outcomes: no lift in CTR, installs, ROAS, revenue or engagement. Nothing was served.",
    "Fun is untested: automation cannot establish it, and four variants and model reviewers cannot either.",
    "No person reads the strings: model reviewers read them independently, and the strictest verdict counts; their "
    "sheets are kept beside review.csv.",
    "The puzzles are not Luzia's product: they are code-made homework puzzles hosted by Luzia characters.",
    "Traceable is not true: a proof claim is an exact string the explorer saw, traceable to an element.",
    "One app: nothing here shows the method transfers to Simula's game advertisers or to canvas apps.",
    "Attributes are untested inputs: no training data has them.",
    "Spike A tested hashed ids among co-running ads, not content-matched creatives.",
    "The starvation candidate sets are ours, built from TH1's export; the share does not describe Simula's traffic.",
    "eps_new = 5% and n_min = 500 are untuned settings; n_min does not mark a winner.",
    "The tier rules, gate_tier and the v0 vocabularies are ours, not Simula's.",
    "Luzia has not approved the use of its screens and does not advertise with Simula.",
    "A number shown as pending is not a result.",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_sheet(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def read_review(out: Path) -> list[dict]:
    return read_sheet(out / "review.csv")


def expected_rows(out: Path) -> list[tuple[str, str, str, str]]:
    """(variant, screen, string, source) for every string each passing variant can show, in variant order."""
    rows = []
    for path in sorted((out / "variants").glob("*/content.json")):
        content = Content.model_validate_json(path.read_text())
        rows += [(content.variant_id, screen, string, source) for screen, string, source in visible_strings(content)]
    return rows


def write_review(out: Path) -> None:
    with open(out / "review.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(REVIEW_FIELDS)
        writer.writerows([*row, "", ""] for row in expected_rows(out))


def unread(rows: list[dict], out: Path) -> str | None:
    """Why a review sheet does not hold a verdict on exactly the strings the passing variants show, or None."""
    listed = [(row["variant"], row["screen"], row["string"], row["source"]) for row in rows]
    expected = expected_rows(out)
    if listed != expected:
        missing, extra = [row for row in expected if row not in listed], [row for row in listed if row not in expected]
        return (f"it must list exactly the strings the passing variants show, in order; missing {len(missing)} "
                f"(first {missing[:1]}), not shown {len(extra)} (first {extra[:1]})")
    blank = [row for row in rows if row["verdict"] not in VERDICTS]
    if blank:
        return (f"{len(blank)} rows have no verdict (ok, edit or reject); the first is {blank[0]['variant']} "
                f"{blank[0]['string']!r}")
    return None


def merged(out: Path, sheets: dict[str, Path]) -> list[dict]:
    """review.csv's rows from two or more reviewers' sheets: each string's strictest verdict, and every reviewer's
    note as "name: note; name: note". SystemExit naming the reviewer when a sheet misses a string or a verdict."""
    if len(sheets) < 2 or not all(REVIEWER.fullmatch(name) for name in sheets):
        raise SystemExit(f"merge needs two or more reviewers named with letters, digits, spaces, _, . or -; got "
                         f"{list(sheets)}")
    paths = list(sheets.values())
    if any(os.path.samefile(a, b) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise SystemExit("two reviewers gave the same sheet; each model reviewer reads every string on a sheet of its "
                         "own")
    copies = {}
    for name, path in sheets.items():
        copies[name] = read_sheet(path)
        problem = unread(copies[name], out)
        if problem:
            raise SystemExit(f"{name}'s sheet {path}: {problem}")
    return [{"variant": row["variant"], "screen": row["screen"], "string": row["string"], "source": row["source"],
             "verdict": max((rows[i]["verdict"] for rows in copies.values()), key=VERDICTS.index),
             "edit": "; ".join(f"{name}: {rows[i]['edit']}" for name, rows in copies.items() if rows[i]["edit"])}
            for i, row in enumerate(next(iter(copies.values())))]


def merge(out: Path, sheets: dict[str, Path]) -> None:
    """Writes review.csv from the reviewers' sheets (see merged), keeps each sheet beside it as review-<name>.csv,
    and records who reviewed in reviewers.json as {name: review-<name>.csv}."""
    rows = merged(out, sheets)
    kept = {name: f"review-{name}.csv" for name in sheets}
    for name, path in sheets.items():
        if Path(path).resolve() != (out / kept[name]).resolve():
            shutil.copy(path, out / kept[name])
    with open(out / "review.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (out / "reviewers.json").write_text(json.dumps(kept, indent=1))


def reviewer_sheets(out: Path) -> dict[str, Path]:
    """reviewers.json's {name: sheet}; reviewer names everywhere come from its keys. SystemExit unless it maps two or
    more names, each to its own review-<name>.csv."""
    path = out / "reviewers.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing; run merge first")
    mapping = json.loads(path.read_text())
    if not (isinstance(mapping, dict) and len(mapping) >= 2
            and all(REVIEWER.fullmatch(name) and sheet == f"review-{name}.csv" for name, sheet in mapping.items())):
        raise SystemExit(f"{path} must map two or more reviewer names to their review-<name>.csv; it holds {mapping}")
    return {name: out / sheet for name, sheet in mapping.items()}


def unmerged(out: Path, sheets: dict[str, Path]) -> str | None:
    """How review.csv differs from the merge of the reviewers' kept sheets, or None."""
    rows = read_review(out)
    expected = merged(out, sheets)
    if len(rows) != len(expected):
        return f"review.csv is not the merge of the reviewers' sheets: {len(rows)} rows, the merge has {len(expected)}"
    for row, merge_row in zip(rows, expected):
        if row != merge_row:
            return (f"review.csv is not the merge of the reviewers' sheets; the first differing row is {row}, "
                    f"the merge gives {merge_row}")
    return None


def read_verdicts(out: Path) -> dict[str, tuple[str, str, int]]:
    """Each passing variant's (status, review_verdict, review_edits) from the merged review.csv. A variant is accepted
    only when every string it shows has a verdict and none says reject; SystemExit when a string is missing or
    unread."""
    rows = read_review(out)
    problem = unread(rows, out)
    if problem:
        raise SystemExit(f"review.csv: {problem}")
    read = {}
    for variant in dict.fromkeys(row["variant"] for row in rows):
        verdicts = [row["verdict"] for row in rows if row["variant"] == variant]
        edits = verdicts.count("edit")
        verdict = "rejected" if "reject" in verdicts else "accepted_with_edits" if edits else "accepted"
        read[variant] = ("failed" if verdict == "rejected" else "accepted", verdict, edits)
    return read


def html_problems(out: Path) -> list[str]:
    """Each passing variant whose creative.html is missing or is no longer the creative its record was made from."""
    found = []
    for path in sorted((out / "variants").glob("*/creative.json")):
        record, html = CreativeAttributes.model_validate_json(path.read_text()), path.parent / "creative.html"
        if not html.exists():
            found.append(f"{path.parent.name}: creative.html is missing")
        elif creative_id(html.read_text()) != record.creative_id:
            found.append(f"{path.parent.name}: creative.html is not {record.creative_id}, the creative that was "
                         "checked and reviewed")
    return found


def results(out: Path) -> dict[str, VariantResult]:
    """Each variant's result as its saved drafts give it; result.json supplies only its host and hook."""
    found = {}
    for path in sorted((out / "variants").glob("*/result.json")):
        saved, variant = VariantResult.model_validate_json(path.read_text()), path.parent.name
        drafts = [Draft.model_validate_json(p.read_text())
                  for p in sorted((out / "drafts" / variant).glob("round*/draft.json"))]
        found[variant] = variant_result(variant, saved.host_id, saved.hook, drafts, out / "drafts")
    return found


def starvation_summary(records: list[dict], star: dict) -> dict:
    """starvation.json as tools/th1_starvation.py's summarize gives it for these records, the sample seed run uses
    and star's bundle, windows and policy."""
    spec = importlib.util.spec_from_file_location("th1_starvation", ROOT / "tools" / "th1_starvation.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    summary = tool.summarize(records, seed=th1.SAMPLE_SEED, feature_set=star["bundle"]["feature_set"],
                             rows_all=star["bundle"]["rows_all"], windows=star["windows"], policy=star["policy"])
    return json.loads(json.dumps(summary))


def fingerprint(facts_json: str, facts: Facts, run_dir: Path, seed: int, qa: dict) -> str:
    """sha256 of everything generation and QA read: the facts, the host art, the proof screenshots and font the
    assembler inlines, the content config, the seed, the QA matrix, and the source of the creative package, its
    template, the host-line prompt and this runner. Saved drafts are reusable only under the same fingerprint."""
    art = [(run_dir / "qa" / "approved" / "assets" / host.art).read_bytes() for host in facts.hosts]
    shots = [proof_png(run_dir, host.proof_screen, {p.evidence_id for p in host.proof}, host.proof_crop)
             for host in facts.hosts]
    code = [path.read_bytes() for path in sorted((ROOT / "simula" / "creative").glob("*.py"))]
    settings = json.dumps({"seed": seed, "paths": list(qa.get("paths", PATHS)),
                           "widths": list(qa.get("widths", WIDTHS)), "modes": list(qa.get("modes", MODES)),
                           "content": config.profiles()["content"]}, sort_keys=True).encode()
    digest = hashlib.sha256()
    for part in (facts_json.encode(), font_css(run_dir).encode(), TEMPLATE.read_bytes(), PROMPT.read_bytes(),
                 Path(__file__).read_bytes(), *art, *shots, *code, settings):
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()


def attributes(content: Content, html: str, facts: Facts, result: VariantResult) -> CreativeAttributes:
    """The attribute record of a variant that passed every code check, pending the reviewers' read."""
    return CreativeAttributes(
        schema_version=0, creative_id=creative_id(html), status="draft", format="INT",
        advertiser=Advertiser(app_package=facts.app_package, app_name=facts.app_name, app_version=facts.app_version,
                              run_id=facts.run_id, has_unsafe_screens=facts.has_unsafe_screens, campaign_id=None,
                              category_iab=None),
        content_tier=result.content_tier, gate_tier=gate_for(result.content_tier, facts.has_unsafe_screens),
        character=content.host, hook=content.hook,
        interaction=Interaction(kind="minigame", mechanic="quick_puzzles",
                                puzzle_families=[p.family for p in content.puzzles], puzzle_count=3, seed=content.seed),
        proof=content.proof, copy=content.copy_, end_card=EndCard(cta=content.cta),
        qa=QA(playthrough_pass=result.playthrough_pass, grounding_pass=result.grounding_pass,
              tier_pass=result.tier_pass, repair_round=result.repair_round, first_pass_accept=result.first_pass_accept,
              review_verdict="pending", review_edits=0, reviewers=[]),
        lineage=Lineage(brief_id=BRIEF_ID, parent_creative_id=None, generator_version=GENERATOR,
                        prompt_sha=sha256(PROMPT), template_sha=sha256(TEMPLATE)),
        provenance=dict(PROVENANCE))


def adset(content: Content) -> dict:
    """The API take-home's ad-set shape plus interactive_url, a field that spec does not have (needs_mapping)."""
    return {"campaign_id": None, "ad_set_name": f"{content.app_name} {content.variant_id} (concept, not served)",
            "character_names": [content.host.name], "ctas": [content.cta],
            "fallback_copy": [content.copy_.end_headline], "interactive_url": "creative.html"}


def write_variant_files(out: Path, facts: Facts, result: VariantResult) -> None:
    folder = out / "variants" / result.variant_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(result.model_dump_json(indent=1))
    if not result.passed:
        return
    for name in ("creative.html", "content.json"):
        shutil.copy(Path(result.final_dir) / name, folder / name)
    content = Content.model_validate_json((folder / "content.json").read_text())
    record = attributes(content, (folder / "creative.html").read_text(), facts, result)
    (folder / "creative.json").write_text(json.dumps(dump(record), indent=1))
    (folder / "adset.json").write_text(json.dumps(adset(content), indent=1))


def s04_fixture(facts: Facts, seed: int) -> dict:
    """The rejection fixture: the first host's content with s04's art, then with s04 as its proof screen."""
    content = build_content(facts, facts.hosts[0], HOOKS[0], seed, FIXTURE_LINES)
    art = content.model_copy(update={"host": content.host.model_copy(update={"art_ref": "s04.e04.art.png"})})
    screen = content.model_copy(update={"proof": Proof(screen_id="s04", claims=content.proof.claims)})
    found = {"art_tier": tier(art, facts)[1], "proof_grounding": grounding(screen, facts),
             "proof_tier": tier(screen, facts)[1]}
    return {**found, "rejected": all(found.values())}


def wrapper(decisions) -> dict:
    """The exploration wrapper over the starvation decisions, with each request's unseen tuples as its cold set."""
    lowest, with_cold, largest = None, 0, 0
    for d in decisions:
        cold = {str(i) for i, unseen in enumerate(d["u"]) if unseen}
        if not cold:
            continue
        decision = {"candidates": [{"candidate_id": str(i), "eligible": True, "selection_probability": p}
                                   for i, p in enumerate(d["p"])]}
        propensities = mix(decision, cold, seed=d["row"])["propensities"]
        low = min(propensities[i] for i in cold)
        lowest = low if lowest is None else min(lowest, low)
        with_cold, largest = with_cold + 1, max(largest, len(cold))
    return {"requests_with_cold": with_cold, "largest_cold_set": largest, "epsilon_new": EPSILON_NEW,
            "wrapper_min_propensity": None if lowest is None else round(lowest, 6)}


def creative_spend(out: Path) -> float:
    """Every creative call's spend in the trace, earlier runs included, and how many calls that is."""
    spends = [line.usd for line in read_trace(out / "trace.jsonl") if line.stage == "creative"]
    return sum(spends), len(spends)


def report(out: Path) -> dict:
    """Every number and table the report shows, recomputed from the files in out."""
    facts = Facts.model_validate_json((out / "facts.json").read_text())
    info = json.loads((out / "run.json").read_text())
    derived = list(results(out).values())
    records = {p.parent.name: CreativeAttributes.model_validate_json(p.read_text())
               for p in sorted((out / "variants").glob("*/creative.json"))}
    star = json.loads((out / "starvation" / "starvation.json").read_text())
    wrap = json.loads((out / "wrapper.json").read_text())
    pending = any(r.qa.review_verdict == "pending" for r in records.values())
    accepted = None if pending else sum(r.status == "accepted" for r in records.values())
    usd_total = round(creative_spend(out)[0], 4)
    table: dict[str, dict[str, list[int]]] = {}
    for result in derived:
        for run_name, found in result.drafts[-1].playthrough.items():
            path, _, width = run_name.split("/")
            cell = table.setdefault(path, {}).setdefault(width, [0, 0])
            cell[0] += not found
            cell[1] += 1
    return {
        "status": "pending_review" if pending else "final", "app": facts.app_name, "run_id": facts.run_id,
        "seed": info["seed"],
        "variants_total": len(derived), "first_pass_accept": sum(r.first_pass_accept for r in derived),
        "final_checks_pass": sum(r.passed for r in derived), "final_accept": accepted,
        "review_edits": None if pending else sum(r.qa.review_edits for r in records.values()),
        "reviewers": [] if pending else list(dict.fromkeys(name for r in records.values() for name in r.qa.reviewers)),
        "usd_total": usd_total, "usd_per_accepted": round(usd_total / accepted, 4) if accepted else None,
        "seconds_per_variant": round(statistics.mean(r.seconds for r in derived), 1) if derived else None,
        "starvation_share": star["starvation_share"], "starvation_ci": star["starvation_ci"],
        "starvation_requests": star["starvation_requests"], "requests_sampled": star["requests_sampled"],
        "exploration_set_mean": star["exploration_set_size"]["mean"],
        "pctr_gap_median": (star["pctr_gap_top_unseen_to_leader"] or {}).get("median"),
        "wrapper_min_propensity": wrap["wrapper_min_propensity"], "epsilon_new": EPSILON_NEW, "n_min": N_MIN,
        "variants": [{"variant": r.variant_id, "first_pass_accept": r.first_pass_accept, "repair_round": r.repair_round,
                      "passed": r.passed, "playthrough_pass": r.playthrough_pass, "grounding_pass": r.grounding_pass,
                      "tier_pass": r.tier_pass, "content_tier": r.content_tier,
                      "gate_tier": records[r.variant_id].gate_tier if r.variant_id in records else None,
                      "seconds": round(r.seconds, 1), "usd": round(r.usd, 4),
                      "review_verdict": records[r.variant_id].qa.review_verdict if r.variant_id in records else None,
                      "review_edits": records[r.variant_id].qa.review_edits if r.variant_id in records else None}
                     for r in derived],
        "playthrough": table,
        "s04_fixture": json.loads((out / "s04_fixture.json").read_text()),
        "sdk_events": SDK_EVENTS,
    }


def render_report(r: dict) -> str:
    """report.md, rendered from report.json alone."""
    def show(value):
        return "pending" if value is None else json.dumps(value)
    names = r["reviewers"]
    if not r["final_checks_pass"]:
        accepted = "none passed the automatic checks, so there was nothing to read"
    elif r["final_accept"] is None:
        accepted = "the model reviewers' read of every string is still pending"
    else:
        accepted = (f"after a read of every string by the model reviewers {', '.join(names[:-1])} and {names[-1]}, "
                    + (f"{r['final_accept']} accepted, at ${r['usd_per_accepted']:.2f} per accepted creative"
                       if r["final_accept"] else "none accepted"))
    email = EMAIL.format(accepted=accepted, variants_total=r["variants_total"],
        first_pass_accept=r["first_pass_accept"], final_checks_pass=r["final_checks_pass"],
        starvation_share="n/a" if r["starvation_share"] is None else f"{r['starvation_share']:.1%}")
    widths = sorted({w for cells in r["playthrough"].values() for w in cells}, key=int)
    lines = [
        f"# Creative generation MVP: {r['app']} (concept, not affiliated, not served)", "",
        f"Status: {r['status']}. Run {r['run_id']}, puzzle seed {r['seed']}. Strings read by model reviewers: "
        f"{', '.join(r['reviewers']) or 'pending'} (the strictest verdict counts; no person read them). "
        "`uv run python tools/creative_mvp.py check` recomputes every number here from report.json and the files "
        "beside it.", "",
        "## Numbers", "", "| field | value |", "|---|---|", *[f"| {k} | {show(r[k])} |" for k in NUMBERS], "",
        "## Variants", "",
        "| variant | first pass | repair round | passed | playthrough | grounding | tier | content tier | gate tier "
        "| seconds | $ | review verdict | edits |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        *[f"| {v['variant']} | {v['first_pass_accept']} | {v['repair_round']} | {v['passed']} | {v['playthrough_pass']} "
          f"| {v['grounding_pass']} | {v['tier_pass']} | {show(v['content_tier'])} | {show(v['gate_tier'])} "
          f"| {v['seconds']} | {v['usd']} | {show(v['review_verdict'])} | {show(v['review_edits'])} |"
          for v in r["variants"]], "",
        "## Playthrough by path and width", "",
        "Runs passed / runs, over every variant's final draft and both timer modes.", "",
        "| path | " + " | ".join(f"{w} px" for w in widths) + " |", "|---|" + "---|" * len(widths),
        *[f"| {path} | " + " | ".join("{}/{}".format(*cells.get(w, [0, 0])) for w in widths) + " |"
          for path, cells in r["playthrough"].items()], "",
        "## s04 rejection fixture", "",
        f"Rejected: {show(r['s04_fixture']['rejected'])}. s04 art: {show(r['s04_fixture']['art_tier'])}. "
        f"s04 proof screen: {show(r['s04_fixture']['proof_grounding'])} {show(r['s04_fixture']['proof_tier'])}.", "",
        "## Starvation on TH1's held-out traffic", "",
        f"Share of requests where every unseen tuple got probability 0: {show(r['starvation_share'])}, Wilson 95% "
        f"interval {show(r['starvation_ci'])}, over the {show(r['starvation_requests'])} of "
        f"{show(r['requests_sampled'])} sampled requests that had an eligible seen and an eligible unseen creative "
        "tuple. The log has no candidate sets: these are ours, built from TH1's export. The share shows how TH1's "
        "policy treats real unseen creatives, not what Simula's candidate sets look like. Mean exploration-set size: "
        f"{show(r['exploration_set_mean'])}; median pCTR gap from the top unseen tuple to the leader: "
        f"{show(r['pctr_gap_median'])}. Unseen values take LightGBM's missing branch (TH1 features.py:79-81), and only "
        "candidates within 0.01 of the leader get any probability.", "",
        f"Exploration wrapper (eps_new {r['epsilon_new']}, n_min {r['n_min']}): the smallest propensity any unseen "
        f"tuple gets is {show(r['wrapper_min_propensity'])}.", "",
        "## Needs mapping", "",
        "Event names to Simula SDK callbacks: " + ", ".join(f"{k} -> {v}" for k, v in r["sdk_events"].items())
        + "; the CHALLENGE_* events have no SDK counterpart. variants/*/adset.json adds interactive_url, which the "
        "API take-home's ad-set shape has no field for.", "",
        "## Email paragraph", "", "> " + email, "",
        "## Not claimed", "", *[f"- {line}" for line in NOT_CLAIMED], "",
    ]
    return "\n".join(lines)


def write_report(out: Path) -> dict:
    r = report(out)
    (out / "report.json").write_text(json.dumps(r, indent=1) + "\n")
    (out / "report.md").write_text(render_report(r))
    return r


def run(run_dir: Path = RUN, out: Path = OUT, *, seed: int = SEED, cache_dir: Path = llm.CACHE,
        qa: dict | None = None, requests: int = REQUESTS) -> dict:
    """Facts, the 2 hosts x 2 hooks variants with QA and one repair round under the $6 cap, the review sheet, the s04
    fixture, starvation (reused when out/starvation/starvation.json exists) and the wrapper, then the report. Refuses
    to start while review.csv holds a verdict or reviewers.json exists, so a rerun never wipes the reviewers' read. A
    rerun resumes from out/drafts only under the same input fingerprint, and refuses before touching any file
    otherwise; variants/ is rebuilt only once every variant is generated or resumed. One run per out at a time: it
    holds out/.run.lock, from before it reads earlier spend to the end."""
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".run.lock"
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise SystemExit(f"{lock} exists: another run is writing {out}. If none is (a killed run leaves the lock "
                         f"behind), remove {lock} and rerun") from None
    try:
        return _locked_run(run_dir, out, seed=seed, cache_dir=cache_dir, qa=qa, requests=requests)
    finally:
        lock.unlink()


def _locked_run(run_dir: Path, out: Path, *, seed: int, cache_dir: Path, qa: dict | None, requests: int) -> dict:
    if (out / "review.csv").exists() and any(row["verdict"] for row in read_review(out)):
        raise SystemExit(f"{out / 'review.csv'} holds verdicts; move it away before a new run")
    if (out / "reviewers.json").exists():
        raise SystemExit(f"{out} holds a merged review; move reviewers.json and review-*.csv away before a new run")
    facts = build_facts(run_dir)
    facts_json = facts.model_dump_json(indent=1)
    inputs = fingerprint(facts_json, facts, run_dir, seed, qa or {})
    if (out / "drafts").exists() and (not (out / "run.json").exists()
                                      or json.loads((out / "run.json").read_text()).get("fingerprint") != inputs):
        raise SystemExit(f"{out / 'drafts'} was made from other inputs (facts, art, screenshots, font, content "
                         "config, seed, QA matrix or the creative code); new inputs need a new --out")
    (out / "facts.json").write_text(facts_json)
    (out / "run.json").write_text(json.dumps({"run_dir": str(run_dir), "seed": seed, "requests": requests,
                                              "fingerprint": inputs}, indent=1))
    trace_path = out / "trace.jsonl"
    budget = llm.Budget.for_stage("creative", trace_path, cap=CAP_USD)
    generated = [generate_variant(facts=facts, host=host, hook=hook, seed=seed, run_dir=run_dir,
                                  drafts_dir=out / "drafts", budget=budget, trace_path=trace_path, cache_dir=cache_dir,
                                  **(qa or {}))
                 for host in facts.hosts for hook in HOOKS]
    shutil.rmtree(out / "variants", ignore_errors=True)
    for result in generated:
        write_variant_files(out, facts, result)
    write_review(out)
    (out / "s04_fixture.json").write_text(json.dumps(s04_fixture(facts, seed), indent=1))
    if not (out / "starvation" / "starvation.json").exists():
        th1.starvation(out / "starvation", requests=requests)
    (out / "wrapper.json").write_text(json.dumps(wrapper(th1.decisions(out / "starvation")), indent=1))
    return write_report(out)


def finalize(out: Path = OUT) -> dict:
    """Applies the merged review verdicts (see read_verdicts): a variant with a rejected string fails, one with edits
    is accepted_with_edits (edits are counted, not applied, in v0), the rest are accepted. Then rebuilds the report."""
    sheets = reviewer_sheets(out)
    problem = unmerged(out, sheets)
    if problem:
        raise SystemExit(problem)
    changed = html_problems(out)
    if changed:
        raise SystemExit("; ".join(changed))
    reviewers = list(sheets)
    read = read_verdicts(out)
    for path in sorted((out / "variants").glob("*/creative.json")):
        status, verdict, edits = read[path.parent.name]
        record = CreativeAttributes.model_validate_json(path.read_text())
        qa = record.qa.model_copy(update={"review_verdict": verdict, "review_edits": edits, "reviewers": reviewers})
        record = record.model_copy(update={"status": status, "qa": qa})
        path.write_text(json.dumps(dump(record), indent=1))
    return write_report(out)


def check(out: Path = OUT) -> list[str]:
    """Everything report.json or report.md says that the files don't, a review sheet that misses a string, a
    review.csv that is not the merge of the reviewers' sheets, verdicts that no longer match it, a creative.html that
    is not its record's creative, a result.json its drafts don't give, drafts that claim more spend than the trace,
    a starvation.json from another sample than the run asked for, and a starvation.json or wrapper.json the
    starvation decisions don't give."""
    saved = json.loads((out / "report.json").read_text())
    fresh = report(out)
    found = [f"{key}: report.json has {saved.get(key)!r}, the files give {value!r}"
             for key, value in fresh.items() if saved.get(key) != value]
    if (out / "report.md").read_text() != render_report(saved):
        found.append("report.md is not what report.json renders to")
    rows = [(row["variant"], row["screen"], row["string"], row["source"]) for row in read_review(out)]
    records = {p.parent.name: CreativeAttributes.model_validate_json(p.read_text())
               for p in sorted((out / "variants").glob("*/creative.json"))}
    sheets = None
    if (out / "reviewers.json").exists():
        try:
            sheets = reviewer_sheets(out)
        except SystemExit as e:
            found.append(str(e))
    if rows != expected_rows(out):
        found.append(f"review.csv has {len(rows)} rows; the passing variants show {len(expected_rows(out))} strings")
    elif any(r.qa.review_verdict != "pending" for r in records.values()):
        try:
            read = read_verdicts(out)
        except SystemExit as e:
            found.append(str(e))
        else:
            reviewers = list(sheets) if sheets else None
            for variant, r in records.items():
                has = (r.status, r.qa.review_verdict, r.qa.review_edits, r.qa.reviewers)
                gives = (*read[variant], reviewers)
                if has != gives:
                    found.append(f"{variant}: creative.json has {has}, review.csv and reviewers.json give {gives}")
    if sheets:
        try:
            problem = unmerged(out, sheets)
            if problem:
                found.append(problem)
        except SystemExit as e:
            found.append(str(e))
    found += html_problems(out)
    derived = results(out)
    found += [f"{variant}: result.json is not what its saved drafts give"
              for variant, result in derived.items()
              if VariantResult.model_validate_json((out / "variants" / variant / "result.json").read_text())
              .model_dump(exclude={"final_dir"}) != result.model_dump(exclude={"final_dir"})]  # absolute: out may move
    spent, (traced, calls) = sum(result.usd for result in derived.values()), creative_spend(out)
    if spent > traced + 5e-7 * calls + 1e-9:  # the trace rounds each call's usd to 6 places; drafts keep it whole
        found.append(f"the drafts spent ${spent:.6f}, more than the trace's creative spend ${traced:.6f}")
    star = json.loads((out / "starvation" / "starvation.json").read_text())
    requests = json.loads((out / "run.json").read_text()).get("requests")
    if (star["sample_seed"], star["requests_sampled"]) != (th1.SAMPLE_SEED, requests):
        found.append(f"starvation.json samples {star['requests_sampled']} requests with seed {star['sample_seed']}; "
                     f"this run asks for {requests} with seed {th1.SAMPLE_SEED}")
    if not (out / "starvation" / "starvation_decisions.jsonl").exists():
        found.append("wrapper.json and starvation.json not rechecked: starvation/starvation_decisions.jsonl is "
                     "git-ignored and absent here")
        return found
    records = list(th1.decisions(out / "starvation"))
    if len(records) != star["requests_sampled"]:
        found.append(f"starvation_decisions.jsonl holds {len(records)} records; starvation.json says "
                     f"{star['requests_sampled']} requests")
    summary = starvation_summary(records, star)
    if summary != star:
        found.append(f"starvation.json is not what starvation_decisions.jsonl gives: {summary}")
    wrap = wrapper(records)
    if json.loads((out / "wrapper.json").read_text()) != wrap:
        found.append(f"wrapper.json is not what starvation_decisions.jsonl gives: {wrap}")
    return found


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Creative-generation MVP on the committed Luzia run")
    parser.add_argument("command", choices=("run", "merge", "finalize", "check"))
    parser.add_argument("--reviewer", action="append", default=[], metavar="NAME=PATH",
                        help="merge: one reviewer's filled copy of review.csv; give two or more")
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    if args.command == "check":
        found = check(args.out)
        print("\n".join(found) or "report.md, report.json and review.csv match the files")
        sys.exit(1 if found else 0)
    if args.command == "merge":
        pairs = [arg.partition("=") for arg in args.reviewer]
        if any(not name or not path for name, _, path in pairs) or len({name for name, _, _ in pairs}) != len(pairs):
            parser.error("--reviewer takes NAME=PATH, one distinct NAME per reviewer")
        merge(args.out, {name: Path(path) for name, _, path in pairs})
        print(f"review.csv merged from {', '.join(name for name, _, _ in pairs)}")
        return
    if args.command == "run":
        config.load_env()
        result = run(args.run, args.out, seed=args.seed)
    else:
        result = finalize(args.out)
    print(json.dumps({key: result[key] for key in NUMBERS}, indent=1))


if __name__ == "__main__":
    main()
