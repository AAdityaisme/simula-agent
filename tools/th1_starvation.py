"""Starvation on TH1's own held-out traffic (spec 3.5b). Runs only in TH1's own process (simula/creative/th1.py starts
it). Each row of a seeded sample of held-out rows is a request exactly as logged. Its candidates are every distinct real
creative tuple (banner_pos + C14..C21) seen on its surface in the held-out window, a seeded 50 of them when more
qualify. Every candidate is sfw under a mature ceiling and no exposure state is sent, so the gate never binds and
utility is pCTR. A tuple is seen when it appears in the refit window. The log has no candidate sets: this construction
is ours, and it shows how TH1's policy treats real unseen creatives, not what Simula's candidate sets look like.

Writes <out>/starvation_decisions.jsonl (one record per request: row, n eligible, e exploration-set size, p selection
probabilities and u unseen flags in rank order, c14 TH1's is_unseen_C14 flags, gap from the top unseen pCTR to the
leader's) and <out>/starvation.json (the summary). Only the standard library is imported at module level, so the pure
helpers load in exp-connect's tests."""

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

TUPLE = ("banner_pos", "C14", "C15", "C16", "C17", "C18", "C19", "C20", "C21")
SITE_PLACEHOLDER = "85f751fd"
Z95 = 1.959963984540054
SAMPLE_SEED = 20261004


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval for k successes in n trials."""
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def surface(site_id: str, app_id: str) -> str:
    """app_id when site_id is the export's placeholder, else site_id."""
    return app_id if site_id == SITE_PLACEHOLDER else site_id


def tuple_key(values) -> str:
    return "|".join(str(v) for v in values)


def candidate(key: str) -> dict:
    """The rank() candidate for one creative tuple: sfw, C20 a string as in TH1's data, the other fields integers."""
    fields = dict(zip(TUPLE, key.split("|")))
    return {"candidate_id": key, "content_tier": "sfw",
            **{name: value if name == "C20" else int(value) for name, value in fields.items()}}


def native(value):
    return value.item() if hasattr(value, "item") else value


def summarize(records: list[dict], *, seed: int, feature_set: str, rows_all: int, windows: dict, policy: dict) -> dict:
    """The starvation summary: among requests with at least one eligible seen and one eligible unseen tuple, the share
    where every unseen tuple gets probability 0, with its Wilson 95% interval; the exploration-set sizes; the pCTR gap
    from the top unseen tuple to the leader; and TH1's own is_unseen_C14 flag beside our seen/unseen split."""
    both = [r for r in records if any(r["u"]) and not all(r["u"])]
    starved = [r for r in both if all(p == 0 for p, u in zip(r["p"], r["u"]) if u)]
    sizes = [r["e"] for r in records]
    gaps = [r["gap"] for r in both]
    unseen_c14 = [c for r in records for c, u in zip(r["c14"], r["u"]) if u]
    seen_c14 = [c for r in records for c, u in zip(r["c14"], r["u"]) if not u]
    deciles = statistics.quantiles(gaps, n=10) if len(gaps) > 1 else None
    return {
        "requests_sampled": len(records), "sample_seed": seed,
        "starvation_requests": len(both), "starved": len(starved),
        "starvation_share": round(len(starved) / len(both), 4) if both else None,
        "starvation_ci": [round(v, 4) for v in wilson(len(starved), len(both))] if both else None,
        "exploration_set_size": {"mean": round(statistics.mean(sizes), 3),
                                 "histogram": {str(k): sizes.count(k) for k in sorted(set(sizes))}},
        "pctr_gap_top_unseen_to_leader": {
            "median": round(statistics.median(gaps), 6), "p10": round(deciles[0], 6) if deciles else None,
            "p90": round(deciles[-1], 6) if deciles else None,
            "zero_gap_share": round(sum(g == 0 for g in gaps) / len(gaps), 4)} if gaps else None,
        "candidates_per_request": {"mean": round(statistics.mean(r["n"] for r in records), 2),
                                   "at_cap_share": round(sum(r["n"] == policy["max_candidates"] for r in records)
                                                         / len(records), 4)},
        "unseen_tuples_with_unseen_C14_share": round(sum(unseen_c14) / len(unseen_c14), 4) if unseen_c14 else None,
        "seen_tuples_with_unseen_C14_share": round(sum(seen_c14) / len(seen_c14), 4) if seen_c14 else None,
        "bundle": {"feature_set": feature_set, "rows_all": rows_all},
        "windows": {"refit": list(windows["refit"]), "test": list(windows["test"])},
        "settings": {"content_tier": "sfw", "publisher_max_content_tier": "mature", "exposure": None,
                     "rank_seed": "row index", "candidate_sample_seed": "row index",
                     "max_candidates": policy["max_candidates"]},
        "policy": policy,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="TH1 starvation on held-out traffic")
    parser.add_argument("--model", required=True, help="bundle folder holding selected.json")
    parser.add_argument("--data", required=True, help="folder holding impressions.csv and characters.csv")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--requests", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args(argv)

    from simula.data import WINDOWS, load, load_characters, split
    from simula.rank import POLICY, REQUEST_FIELDS, rank
    from simula.train import load_bundle

    model_dir = Path(args.model)
    feature_set = json.loads((model_dir / "selected.json").read_text())["feature_set"]
    bundle = load_bundle(model_dir / feature_set)
    characters = load_characters(args.data)
    df = load(args.data)
    refit, test = split(df, "refit"), split(df, "test")
    seen = {tuple_key(t) for t in zip(*(refit[c] for c in TUPLE))}
    test["key"] = [tuple_key(t) for t in zip(*(test[c] for c in TUPLE))]
    test["surface"] = [surface(s, a) for s, a in zip(test["site_id"], test["app_id"])]
    by_surface = {name: sorted(set(keys)) for name, keys in test.groupby("surface")["key"]}
    sample = test.sample(n=min(args.requests, len(test)), random_state=args.seed)
    cap = POLICY["max_candidates"]
    records = []
    for done, (row_index, row) in enumerate(sample.iterrows(), start=1):
        keys = by_surface[row["surface"]]
        if len(keys) > cap:
            keys = random.Random(int(row_index)).sample(keys, cap)
        payload = {"request": {f: native(row[f]) for f in REQUEST_FIELDS}, "candidates": [candidate(k) for k in keys],
                   "publisher": {"max_content_tier": "mature"}, "seed": int(row_index)}
        rows = [c for c in rank(payload, bundle, characters)["candidates"] if c["eligible"]]
        unseen = [c["candidate_id"] not in seen for c in rows]
        top_unseen = max((c["calibrated_pctr"] for c, u in zip(rows, unseen) if u), default=None)
        records.append({"row": int(row_index), "n": len(rows), "e": sum(c["in_exploration_set"] for c in rows),
                        "p": [c["selection_probability"] for c in rows], "u": [int(u) for u in unseen],
                        "c14": [int(c["is_unseen_C14"]) for c in rows],
                        "gap": None if top_unseen is None else rows[0]["calibrated_pctr"] - top_unseen})
        if done % 1000 == 0:
            print(f"{done} of {len(sample)} requests ranked", file=sys.stderr)
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "starvation_decisions.jsonl", "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in records)
    summary = summarize(records, seed=args.seed, feature_set=feature_set,
                        rows_all=bundle[2]["data_row_counts"]["all"], windows=WINDOWS, policy=POLICY)
    (args.out / "starvation.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
