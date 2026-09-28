"""Reference break-even math for rewarded-ad ideas. Reads bible/data/economics.json + ecpm.json.

break_even_ecpm = 1000 * reward_cost_usd / views_per_grant
Verdict (per red team rt-c F2): PASS if break-even <= low benchmark under central cost,
CONDITIONAL if break-even under the favorable cost <= high benchmark, else FAIL.
"""
import json
from pathlib import Path

DATA = Path(__file__).parent / "data"


def load():
    return (json.loads((DATA / "economics.json").read_text()),
            json.loads((DATA / "ecpm.json").read_text()))


def reward_cost(kind: str, p: dict, case: str, econ: dict) -> float:
    """USD cost to serve one grant of a reward. `case` is 'favorable' | 'central' | 'unfavorable'."""
    d = {k: (v[case] if isinstance(v, dict) and case in v else v)
         for k, v in econ["reward_kinds"][kind]["defaults"].items()}
    d.update(p)  # proposer/app-supplied fields override defaults
    if kind == "inference":
        per = (d["tokens_in"] * d["usd_per_mtok_in"] + d["tokens_out"] * d["usd_per_mtok_out"]) / 1e6
        return d["count"] * per
    if kind == "image":
        return d["count"] * d["usd_per_image"]
    if kind == "voice":
        return d["minutes"] * d["usd_per_minute"]
    if kind == "feature_time":  # e.g. ad-free minutes: ads the app no longer shows
        return d["minutes"] * d["ads_per_minute"] * d["displaced_ecpm_usd"] / 1000
    if kind == "content_unlock":  # page/episode views that would have carried ads, or a paid unlock given away
        return d["units"] * (d["ads_per_unit"] * d["displaced_ecpm_usd"] / 1000 + d["p_would_pay"] * d["unit_price_usd"])
    if kind == "currency":  # cannibalization: chance the user would have bought it instead
        return d["amount"] * d["usd_per_unit"] * d["p_would_pay"]
    if kind in ("cosmetic", "queue_priority", "streak_protection"):
        return d.get("usd", 0.0)
    raise ValueError(f"unknown reward kind {kind}")


def break_even_ecpm(cost_usd: float, views_per_grant: int = 1) -> float:
    return 1000 * cost_usd / views_per_grant


def verdict(kind: str, p: dict, region: str = "na", platform: str = "android", views_per_grant: int = 1):
    econ, ecpm = load()
    bench = next(b for b in ecpm["benchmarks"] if b["region"] == region and b["platform"] == platform)
    central = break_even_ecpm(reward_cost(kind, p, "central", econ), views_per_grant)
    fav = break_even_ecpm(reward_cost(kind, p, "favorable", econ), views_per_grant)
    if central <= bench["low"]:
        v = "PASS"
    elif fav <= bench["high"]:
        v = "CONDITIONAL"
    else:
        v = "FAIL"
    return {"verdict": v, "break_even_central": round(central, 2), "break_even_favorable": round(fav, 2),
            "benchmark": bench}


if __name__ == "__main__":
    # guide's worked number: 5 replies, 2k in / 300 out, $1/$5 per MTok -> $0.0175 -> $17.50 eCPM
    econ, _ = load()
    c = reward_cost("inference", {"count": 5, "tokens_in": 2000, "tokens_out": 300,
                                  "usd_per_mtok_in": 1.0, "usd_per_mtok_out": 5.0}, "central", econ)
    assert abs(c - 0.0175) < 1e-9 and abs(break_even_ecpm(c) - 17.5) < 1e-6
    assert reward_cost("cosmetic", {}, "central", econ) == 0.0
    print(verdict("inference", {"count": 1}, "na", "android"))
    print(verdict("cosmetic", {}, "latam", "android"))
    print("ok")
