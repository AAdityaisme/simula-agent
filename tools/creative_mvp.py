"""The creative-generation MVP on the committed Luzia run (spec section 6). Three commands, run from exp-connect:

    uv run python tools/creative_mvp.py run        # facts, 4 variants (paid, $6 cap), review.csv, starvation, report
    uv run python tools/creative_mvp.py finalize   # after Aadi fills review.csv's verdict and edit columns
    uv run python tools/creative_mvp.py check      # recomputes every number in report.md; exit 1 on a mismatch

report.json is rebuilt from the files beside it (facts.json, run.json, variants/*, trace.jsonl, starvation/,
wrapper.json, s04_fixture.json) every time, and report.md is rendered from report.json, so check can recompute both."""

import argparse
import csv
import hashlib
import json
import shutil
import statistics
import sys
from pathlib import Path

from simula import config, llm
from simula.config import ROOT
from simula.creative import th1
from simula.creative.assemble import SDK_EVENTS, TEMPLATE, visible_strings
from simula.creative.facts import Facts, build_facts
from simula.creative.lines import PROMPT, HostLines, build_content
from simula.creative.policy import EPSILON_NEW, N_MIN, mix
from simula.creative.qa import MODES, PATHS, WIDTHS, grounding, tier
from simula.creative.repair import VariantResult, generate_variant
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
VERDICTS = ("ok", "edit", "reject")
NUMBERS = ("variants_total", "first_pass_accept", "final_accept", "human_edits", "usd_total", "usd_per_accepted",
           "seconds_per_variant", "starvation_share", "starvation_ci", "starvation_requests", "wrapper_min_propensity")
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
    "and {final_accept} after one repair round and my read of every string, at ${usd_per_accepted} per accepted "
    "creative. On take-home 1's own held-out traffic, its ranker gave every unseen real creative zero chance in "
    "{starvation_share} of decisions where a seen one was eligible, so I added an exploration layer with exact logged "
    "probabilities. The limit: your sample advertisers are games, and my explorer reads element trees, so canvas games "
    "need a second fact source (store listing or an advertiser brief). Could you share one representative advertiser "
    "asset pack, and how a creative id joins to impressions and postbacks today?")
NOT_CLAIMED = (
    "No measured outcomes: no lift in CTR, installs, ROAS, revenue or engagement. Nothing was served.",
    "Fun is untested: automation cannot establish it, and four variants and one reader cannot either.",
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


def read_review(out: Path) -> list[dict]:
    with open(out / "review.csv", newline="") as f:
        return list(csv.DictReader(f))


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


def human_read(out: Path) -> dict[str, tuple[str, str, int]]:
    """Each passing variant's (status, human_verdict, human_edits) from review.csv. A variant is accepted only when
    every string it shows has a verdict and none says reject; SystemExit when a string is missing or unread."""
    rows = read_review(out)
    listed = [(row["variant"], row["screen"], row["string"], row["source"]) for row in rows]
    expected = expected_rows(out)
    if listed != expected:
        missing, extra = [row for row in expected if row not in listed], [row for row in listed if row not in expected]
        raise SystemExit(f"review.csv must list exactly the strings the passing variants show, in order; missing "
                         f"{len(missing)} (first {missing[:1]}), not shown {len(extra)} (first {extra[:1]})")
    blank = [row for row in rows if row["verdict"] not in VERDICTS]
    if blank:
        raise SystemExit(f"review.csv: {len(blank)} rows have no verdict (ok, edit or reject); the first is "
                         f"{blank[0]['variant']} {blank[0]['string']!r}")
    read = {}
    for variant in dict.fromkeys(row["variant"] for row in rows):
        verdicts = [row["verdict"] for row in rows if row["variant"] == variant]
        edits = verdicts.count("edit")
        verdict = "rejected" if "reject" in verdicts else "accepted_with_edits" if edits else "accepted"
        read[variant] = ("failed" if verdict == "rejected" else "accepted", verdict, edits)
    return read


def fingerprint(facts_json: str, facts: Facts, run_dir: Path, seed: int, qa: dict) -> str:
    """sha256 of everything generation reads: the facts, the template, the host-line prompt, the host art, the seed
    and the QA matrix. Saved drafts are reusable only under the same fingerprint."""
    art = [(run_dir / "qa" / "approved" / "assets" / host.art).read_bytes() for host in facts.hosts]
    matrix = json.dumps({"seed": seed, "paths": list(qa.get("paths", PATHS)), "widths": list(qa.get("widths", WIDTHS)),
                         "modes": list(qa.get("modes", MODES))}).encode()
    digest = hashlib.sha256()
    for part in (facts_json.encode(), TEMPLATE.read_bytes(), PROMPT.read_bytes(), *art, matrix):
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()


def attributes(content: Content, html: str, facts: Facts, result: VariantResult) -> CreativeAttributes:
    """The attribute record of a variant that passed every code check, pending Aadi's read."""
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
              human_verdict="pending", human_edits=0),
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


def report(out: Path) -> dict:
    """Every number and table the report shows, recomputed from the files in out."""
    facts = Facts.model_validate_json((out / "facts.json").read_text())
    info = json.loads((out / "run.json").read_text())
    results = [VariantResult.model_validate_json(p.read_text()) for p in sorted((out / "variants").glob("*/result.json"))]
    records = {p.parent.name: CreativeAttributes.model_validate_json(p.read_text())
               for p in sorted((out / "variants").glob("*/creative.json"))}
    star = json.loads((out / "starvation" / "starvation.json").read_text())
    wrap = json.loads((out / "wrapper.json").read_text())
    pending = any(r.qa.human_verdict == "pending" for r in records.values())
    accepted = None if pending else sum(r.status == "accepted" for r in records.values())
    usd_total = round(sum(line.usd for line in read_trace(out / "trace.jsonl") if line.stage == "creative"), 4)
    table: dict[str, dict[str, list[int]]] = {}
    for result in results:
        for run_name, found in result.drafts[-1].playthrough.items():
            path, _, width = run_name.split("/")
            cell = table.setdefault(path, {}).setdefault(width, [0, 0])
            cell[0] += not found
            cell[1] += 1
    return {
        "status": "pending_human_read" if pending else "final", "app": facts.app_name, "run_id": facts.run_id,
        "seed": info["seed"],
        "variants_total": len(results), "first_pass_accept": sum(r.first_pass_accept for r in results),
        "final_accept": accepted,
        "human_edits": None if pending else sum(r.qa.human_edits for r in records.values()),
        "usd_total": usd_total, "usd_per_accepted": round(usd_total / accepted, 4) if accepted else None,
        "seconds_per_variant": round(statistics.mean(r.seconds for r in results), 1) if results else None,
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
                      "human_verdict": records[r.variant_id].qa.human_verdict if r.variant_id in records else None,
                      "human_edits": records[r.variant_id].qa.human_edits if r.variant_id in records else None}
                     for r in results],
        "playthrough": table,
        "s04_fixture": json.loads((out / "s04_fixture.json").read_text()),
        "sdk_events": SDK_EVENTS,
    }


def render_report(r: dict) -> str:
    """report.md, rendered from report.json alone."""
    def show(value):
        return "pending" if value is None else json.dumps(value)
    email = EMAIL.format(
        variants_total=r["variants_total"], first_pass_accept=r["first_pass_accept"],
        final_accept="pending" if r["final_accept"] is None else r["final_accept"],
        usd_per_accepted="pending" if r["usd_per_accepted"] is None else f"{r['usd_per_accepted']:.2f}",
        starvation_share="n/a" if r["starvation_share"] is None else f"{r['starvation_share']:.1%}")
    widths = sorted({w for cells in r["playthrough"].values() for w in cells}, key=int)
    lines = [
        f"# Creative generation MVP: {r['app']} (concept, not affiliated, not served)", "",
        f"Status: {r['status']}. Run {r['run_id']}, puzzle seed {r['seed']}. "
        "`uv run python tools/creative_mvp.py check` recomputes every number here from report.json and the files "
        "beside it.", "",
        "## Numbers", "", "| field | value |", "|---|---|", *[f"| {k} | {show(r[k])} |" for k in NUMBERS], "",
        "## Variants", "",
        "| variant | first pass | repair round | passed | playthrough | grounding | tier | content tier | gate tier "
        "| seconds | $ | human verdict | edits |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        *[f"| {v['variant']} | {v['first_pass_accept']} | {v['repair_round']} | {v['passed']} | {v['playthrough_pass']} "
          f"| {v['grounding_pass']} | {v['tier_pass']} | {show(v['content_tier'])} | {show(v['gate_tier'])} "
          f"| {v['seconds']} | {v['usd']} | {show(v['human_verdict'])} | {show(v['human_edits'])} |"
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
    to start while review.csv holds a verdict, so a rerun never wipes Aadi's read. A rerun resumes from out/drafts
    only under the same input fingerprint, and refuses before touching any file otherwise."""
    if (out / "review.csv").exists() and any(row["verdict"] for row in read_review(out)):
        raise SystemExit(f"{out / 'review.csv'} holds verdicts; move it away before a new run")
    facts = build_facts(run_dir)
    facts_json = facts.model_dump_json(indent=1)
    inputs = fingerprint(facts_json, facts, run_dir, seed, qa or {})
    if (out / "drafts").exists() and (not (out / "run.json").exists()
                                      or json.loads((out / "run.json").read_text()).get("fingerprint") != inputs):
        raise SystemExit(f"{out / 'drafts'} was made from other facts, template, prompt, art, seed or QA matrix; "
                         "new inputs need a new --out")
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / "variants", ignore_errors=True)
    (out / "facts.json").write_text(facts_json)
    (out / "run.json").write_text(json.dumps({"run_dir": str(run_dir), "seed": seed, "fingerprint": inputs}, indent=1))
    trace_path = out / "trace.jsonl"
    budget = llm.Budget.for_stage("creative", trace_path, cap=CAP_USD)
    for host in facts.hosts:
        for hook in HOOKS:
            result = generate_variant(facts=facts, host=host, hook=hook, seed=seed, run_dir=run_dir,
                                      drafts_dir=out / "drafts", budget=budget, trace_path=trace_path,
                                      cache_dir=cache_dir, **(qa or {}))
            write_variant_files(out, facts, result)
    write_review(out)
    (out / "s04_fixture.json").write_text(json.dumps(s04_fixture(facts, seed), indent=1))
    if not (out / "starvation" / "starvation.json").exists():
        th1.starvation(out / "starvation", requests=requests)
    (out / "wrapper.json").write_text(json.dumps(wrapper(th1.decisions(out / "starvation")), indent=1))
    return write_report(out)


def finalize(out: Path = OUT) -> dict:
    """Applies Aadi's verdicts (see human_read): a variant with a rejected string fails, one with edits is
    accepted_with_edits (edits are counted, not applied, in v0), the rest are accepted. Then rebuilds the report."""
    read = human_read(out)
    for path in sorted((out / "variants").glob("*/creative.json")):
        status, verdict, edits = read[path.parent.name]
        record = CreativeAttributes.model_validate_json(path.read_text())
        record = record.model_copy(update={"status": status,
                                           "qa": record.qa.model_copy(update={"human_verdict": verdict,
                                                                              "human_edits": edits})})
        path.write_text(json.dumps(dump(record), indent=1))
    return write_report(out)


def check(out: Path = OUT) -> list[str]:
    """Everything report.json or report.md says that the files don't, a review sheet that misses a string, verdicts
    that no longer match review.csv, and a wrapper.json the starvation decisions no longer give."""
    saved = json.loads((out / "report.json").read_text())
    fresh = report(out)
    found = [f"{key}: report.json has {saved.get(key)!r}, the files give {value!r}"
             for key, value in fresh.items() if saved.get(key) != value]
    if (out / "report.md").read_text() != render_report(saved):
        found.append("report.md is not what report.json renders to")
    rows = [(row["variant"], row["screen"], row["string"], row["source"]) for row in read_review(out)]
    records = {p.parent.name: CreativeAttributes.model_validate_json(p.read_text())
               for p in sorted((out / "variants").glob("*/creative.json"))}
    if rows != expected_rows(out):
        found.append(f"review.csv has {len(rows)} rows; the passing variants show {len(expected_rows(out))} strings")
    elif any(r.qa.human_verdict != "pending" for r in records.values()):
        try:
            read = human_read(out)
        except SystemExit as e:
            found.append(str(e))
        else:
            found += [f"{variant}: creative.json has {(r.status, r.qa.human_verdict, r.qa.human_edits)}, review.csv "
                      f"gives {read[variant]}" for variant, r in records.items()
                      if (r.status, r.qa.human_verdict, r.qa.human_edits) != read[variant]]
    if not (out / "starvation" / "starvation_decisions.jsonl").exists():
        found.append("wrapper.json not rechecked: starvation/starvation_decisions.jsonl is git-ignored and absent here")
        return found
    wrap = wrapper(th1.decisions(out / "starvation"))
    if json.loads((out / "wrapper.json").read_text()) != wrap:
        found.append(f"wrapper.json is not what starvation_decisions.jsonl gives: {wrap}")
    return found


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Creative-generation MVP on the committed Luzia run")
    parser.add_argument("command", choices=("run", "finalize", "check"))
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    if args.command == "check":
        found = check(args.out)
        print("\n".join(found) or "report.md, report.json and review.csv match the files")
        sys.exit(1 if found else 0)
    if args.command == "run":
        config.load_env()
        result = run(args.run, args.out, seed=args.seed)
    else:
        result = finalize(args.out)
    print(json.dumps({key: result[key] for key in NUMBERS}, indent=1))


if __name__ == "__main__":
    main()
