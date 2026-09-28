"""The cost line for a candidate: what one reward costs to serve vs what one rewarded view earns.

A thin wrapper over bible/breakeven.py, which stays the source of truth for the math and the verdict.
The reward kind picks the cost term; the app category only picks between the two readings a kind can have.
"""

import importlib.util

from simula.config import ROOT
from simula.contracts import Candidate, Economics

CONTEXTS = (2000, 8000)
REGION, PLATFORM = "na", "android"
# A content unlock displaces ads only where content carries them; elsewhere it is a paid unlock given away.
CONTENT_WITH_ADS = {"content"}
# In these apps a feature-time reward is a taste of a paid feature (cannibalization, judged by C4), not ad-free time.
PAID_FEATURE_APPS = {"chat", "learning"}


def _load_breakeven():
    spec = importlib.util.spec_from_file_location("breakeven", ROOT / "bible" / "breakeven.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


breakeven = _load_breakeven()


def bible_params(candidate: Candidate, app_category: str) -> tuple[dict, str]:
    """Maps the proposer's reward and cost_inputs onto breakeven.py's fields, plus the reading used."""
    reward, inputs = candidate.reward, candidate.cost_inputs
    kind = reward.kind
    if kind == "inference":
        return {"count": inputs.inference_count, "tokens_out": inputs.tokens_out}, "fresh model replies"
    if kind == "image":
        return {"count": inputs.inference_count}, "generated images"
    if kind == "voice":
        return {"minutes": inputs.minutes}, "generated voice minutes"
    if kind == "feature_time":
        if app_category in PAID_FEATURE_APPS:
            return {"minutes": inputs.minutes, "ads_per_minute": 0}, \
                "a timed taste of a paid feature (no serving cost; the lost-sale risk is the subscription check's)"
        return {"minutes": inputs.minutes}, "ads the app no longer shows during the window"
    if kind == "content_unlock":
        units = max(reward.amount, 1)
        ads = 1 if app_category in CONTENT_WITH_ADS else 0
        reading = "one ad slot displaced per unlocked item" if ads else "a paid unlock given away"
        return {"units": units, "unit_price_usd": inputs.currency_amount / units, "ads_per_unit": ads}, reading
    if kind == "currency":
        return {"amount": 1, "usd_per_unit": inputs.currency_amount}, \
            "currency the user might otherwise have bought"
    if kind == "queue_priority":
        return {}, "no marginal serving cost (peak-capacity cost is real but unknown)"
    return {}, "no marginal serving cost"


def costs(kind: str, params: dict) -> tuple[float, float]:
    """Cost of one grant at 2k and 8k input tokens of context (only inference depends on context)."""
    econ, _ = breakeven.load()
    return tuple(breakeven.reward_cost(kind, {**params, "tokens_in": t}, "central", econ) for t in CONTEXTS)


def benchmarks() -> dict:
    _, ecpm = breakeven.load()
    return {(b["region"], b["platform"]): b for b in ecpm["benchmarks"]}


def cost_line(kind: str, cost_2k: float, cost_8k: float, reading: str) -> str:
    be_2k, be_8k = breakeven.break_even_ecpm(cost_2k), breakeven.break_even_ecpm(cost_8k)
    if cost_8k == 0:
        head = "Costs nothing extra to serve, so any completed view pays for it."
    elif kind == "inference":
        head = (f"Costs ~${cost_2k:.4f} per reward to serve; pays for itself above ${be_2k:.2f} eCPM "
                f"at 2k context (${be_8k:.2f} at 8k).")
    else:
        head = f"Costs ~${cost_2k:.4f} per reward to serve; pays for itself above ${be_2k:.2f} eCPM."
    na, latam = benchmarks()[(REGION, PLATFORM)], benchmarks()[("latam", PLATFORM)]
    return (f"{head} A rewarded view earns ${na['low']:.2f}-{na['high']:.2f} eCPM in North America on Android, "
            f"${latam['low']:.2f} in LATAM. Assumes {reading}, central serving prices, one view per reward, "
            f"and a publisher-net eCPM (share 1).")


def annotate(candidate: Candidate, app_category: str) -> Economics:
    kind = candidate.reward.kind
    params, reading = bible_params(candidate, app_category)
    cost_2k, cost_8k = costs(kind, params)
    result = breakeven.verdict(kind, params, REGION, PLATFORM)
    return Economics(cost_2k=round(cost_2k, 6), cost_8k=round(cost_8k, 6),
                     breakeven_ecpm_2k=round(breakeven.break_even_ecpm(cost_2k), 2),
                     breakeven_ecpm_8k=round(breakeven.break_even_ecpm(cost_8k), 2),
                     benchmark_ecpm=result["benchmark"]["central"], verdict=result["verdict"],
                     assumption_line=cost_line(kind, cost_2k, cost_8k, reading))


def apply(candidates: list[Candidate], app_category: str, mode: str) -> list[Candidate]:
    """Adds economics to every live candidate. `annotate` never drops; `gate` drops only a FAIL."""
    out = []
    for c in candidates:
        if c.dropped_reason or c.kind == "no_opportunity":
            out.append(c)
            continue
        econ = annotate(c, app_category)
        dropped = "economics FAIL (gate mode)" if mode == "gate" and econ.verdict == "FAIL" else None
        out.append(c.model_copy(update={"economics": econ, "dropped_reason": dropped}))
    return out
