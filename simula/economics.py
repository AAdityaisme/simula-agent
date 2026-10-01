"""The cost line for a candidate: what one reward costs to serve vs what one rewarded view earns.

A thin wrapper over bible/breakeven.py, which stays the source of truth for the math and the verdict.
The reward kind picks the cost term; the app category only picks between the two readings a kind can have.
"""

import importlib.util
import re

from simula.config import ROOT
from simula.contracts import Candidate, Economics, ProductModel, Reward

CONTEXTS = (2000, 8000)
REGION, PLATFORM = "na", "android"
# A content unlock displaces ads only where content carries them; elsewhere it is a paid unlock given away.
CONTENT_WITH_ADS = {"content"}
# In these apps a feature-time reward is a taste of a paid feature (cannibalization, judged by C4), not ad-free time.
PAID_FEATURE_APPS = {"chat", "learning"}
ZERO_COST_KINDS = {"cosmetic", "streak_protection", "queue_priority"}
# Where each of the bible's required fields comes from in a candidate. A price may be 0 (none observed); the
# line then says the cost isn't counted. Every other required field must be filled.
FIELD_SOURCE = {"count": "inference_count", "minutes": "minutes", "units": "amount", "amount": "amount",
                "unit_price_usd": None, "usd_per_unit": None}
# Ledger kinds that show the app selling something: a paid plan's perk, a price, a currency.
PAID_LEDGER = {"paywall_bullet", "price", "currency"}
# The kinds whose line counts a lost sale, at the price the proposer observed.
PRICED_KINDS = {"content_unlock", "currency"}
# The bible has no reward kind for visibility, so a cosmetic can be a look or a featured spot ahead of other users.
VISIBILITY_UNTYPED = {"cosmetic"}
# Rewards spent as they are used, so their amount bounds them. Any other reward lasts as long as its duration says.
USED_UP = {"inference", "image", "voice", "feature_time", "streak_protection"}
NO_END = re.compile(r"\b(permanent(ly)?|forever|for good|lifetime|never (expires|ends))\b", re.I)
AN_END = re.compile(r"\b(minutes?|hours?|days?|weeks?|months?|until|today|tonight|midnight)\b", re.I)
# A unit that is a chat reply and nothing more: the reward the bible's per-reply cost describes.
REPLY = re.compile(r"(chat )?(repl(y|ies)|messages?|answers?|responses?)", re.I)


def _load_breakeven():
    spec = importlib.util.spec_from_file_location("breakeven", ROOT / "bible" / "breakeven.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


breakeven = _load_breakeven()


def input_problem(candidate: Candidate) -> str | None:
    """Why a candidate's cost inputs can't price its reward kind, or None."""
    reward, inputs = candidate.reward, candidate.cost_inputs
    econ, _ = breakeven.load()
    if reward.kind in ZERO_COST_KINDS:
        if inputs.inference_count or inputs.tokens_in or inputs.tokens_out:
            return f"reward kind {reward.kind} doesn't match its cost inputs (it carries model replies or tokens)"
        return None
    if reward.kind == "inference" and not per_reply(reward.unit) and not (inputs.inference_count or inputs.tokens_out):
        return None  # no count at all: the line says its cost isn't counted
    values = {"inference_count": inputs.inference_count, "minutes": inputs.minutes, "amount": reward.amount}
    missing = [f for f in econ["reward_kinds"][reward.kind]["required_fields"]
               if FIELD_SOURCE[f] and values[FIELD_SOURCE[f]] <= 0]
    if reward.kind == "inference" and inputs.tokens_out <= 0:
        missing.append("tokens_out")
    return f"{reward.kind} reward without {', '.join(missing)}" if missing else None


def central(kind: str, params: dict) -> dict:
    """The inputs breakeven.py prices at the central case: the bible's defaults, overridden by params."""
    econ, _ = breakeven.load()
    defaults = econ["reward_kinds"][kind]["defaults"]
    return {**{k: v["central"] if isinstance(v, dict) else v for k, v in defaults.items()}, **params}


def favorable(kind: str, field: str) -> float:
    econ, _ = breakeven.load()
    return econ["reward_kinds"][kind]["defaults"][field]["favorable"]


def bounded(reward: Reward) -> bool | None:
    """Whether the reward runs out, as its fields say: not when its duration says it never ends; else an amount it
    spends, or a duration with an end. None when they say neither."""
    if NO_END.search(reward.duration):
        return False
    if reward.kind in USED_UP and reward.amount > 0:
        return True
    return True if AN_END.search(reward.duration) else None


def sale_marks(candidate: Candidate, model: ProductModel) -> tuple[str | None, str | None]:
    """(what the reward may give away that the app sells or could sell, when the line counts no price for it; a
    neutral note). Currency and priority are flagged by kind, and so is a piece of a price or currency line. A piece
    of a paywall perk is a sample, noted, only when users who don't pay get it and its fields say it runs out; more of
    it for payers, or a piece that never ends, is flagged."""
    reward = candidate.reward
    if reward.kind in PRICED_KINDS and candidate.cost_inputs.currency_amount:
        return None, None
    if reward.kind == "currency":
        return "in-app currency, which apps sell", None
    paid = next((i for i in model.value_ledger if i.id == candidate.grants_id and i.kind in PAID_LEDGER), None)
    if paid and paid.kind != "paywall_bullet":
        return f'part of what the app sells, {paid.id} "{paid.verbatim}"', None
    if paid:
        perk, ends = f'the paid benefit {paid.id} "{paid.verbatim}"', bounded(reward)
        if candidate.for_users == "paying":
            return f"more of {perk} to users who pay for it", None
        if ends is False:
            return f"{perk}, for good", None
        if ends is None:
            return None, f"It gives part of {perk}; the check can't tell from its fields whether that part runs out."
        return None, f"It is a sample of {perk}, which stays on sale."
    if reward.kind == "queue_priority":
        return "a place ahead of other users, which apps sell as a boost", None
    if reward.kind in VISIBILITY_UNTYPED:
        return None, ("The check can't tell whether it gives a place ahead of other users, which apps sell as a "
                      "boost: no reward kind types visibility.")
    return None, None


def per_reply(unit: str) -> bool:
    """Whether one unit of an inference reward is known to be one chat reply: the unit, punctuation aside, is a reply
    word and nothing more. Any qualifier ("voice messages", "agent task responses") or other word ("swipes") leaves it
    unknown, and so does what an app term means."""
    return bool(REPLY.fullmatch(" ".join(re.sub(r"[^\w\s]", " ", unit).split())))


def describe(candidate: Candidate, model: ProductModel) -> tuple[dict, str | None, str]:
    """Maps the proposer's reward onto breakeven.py's inputs. Returns (params, why the cost isn't counted or
    None, the assumptions behind the number in words)."""
    reward, inputs = candidate.reward, candidate.cost_inputs
    kind, app_category = reward.kind, model.app_category
    price = inputs.currency_amount
    if kind == "inference" and min(inputs.inference_count, inputs.tokens_out) <= 0:
        return {"count": 0}, (f"one of its {reward.unit} isn't known to be a chat reply, and the product model doesn't "
                              "say what one costs to serve"), f"{reward.amount:g} {reward.unit}"
    if kind == "inference":
        p = {"count": inputs.inference_count, "tokens_out": inputs.tokens_out}
        c = central(kind, p)
        return p, None, (f"{c['count']} replies of {c['tokens_out']} tokens out, at ${c['usd_per_mtok_in']:.2f} / "
                         f"${c['usd_per_mtok_out']:.2f} per million tokens in / out (central prices); "
                         f"the verdict uses the 8k figure")
    if kind == "image":
        p = {"count": inputs.inference_count}
        return p, None, f"{p['count']} images at ${central(kind, p)['usd_per_image']:.3f} each (central price)"
    if kind == "voice":
        p = {"minutes": inputs.minutes}
        return p, None, f"{p['minutes']:g} minutes at ${central(kind, p)['usd_per_minute']:.3f} a minute (central price)"
    if kind == "feature_time":
        if app_category in PAID_FEATURE_APPS:
            return {"minutes": inputs.minutes, "ads_per_minute": 0}, \
                "a timed taste of a paid feature; its cost is lost sales, judged by the subscription check", \
                f"{inputs.minutes:g} minutes of a paid feature; if it includes more or better replies, cost those as replies"
        p = {"minutes": inputs.minutes}
        c = central(kind, p)
        banner = favorable(kind, "displaced_ecpm_usd")
        econ, _ = breakeven.load()
        banner_be = breakeven.break_even_ecpm(
            breakeven.reward_cost(kind, {**p, "displaced_ecpm_usd": banner}, "central", econ))
        return p, None, (f"{inputs.minutes:g} ad-free minutes at {c['ads_per_minute']:g} ads a minute, each worth "
                         f"${c['displaced_ecpm_usd']:.2f} eCPM (the NA Android interstitial rate); if the ads it hides "
                         f"are banners (${banner:.2f} eCPM), it pays for itself above ${banner_be:.2f}")
    if kind == "content_unlock":
        units = max(reward.amount, 1)
        ads = 1 if app_category in CONTENT_WITH_ADS else 0
        p = {"units": units, "unit_price_usd": price / units, "ads_per_unit": ads}
        c = central(kind, p)
        words = f"{units:g} unlocked item(s)"
        if ads:
            words += f", one ad slot per item at ${c['displaced_ecpm_usd']:.2f} CPM"
        if price:
            words += f", a price of ${c['unit_price_usd']:.2f} each with a {c['p_would_pay']:.0%} chance the user would have paid"
        elif ads:
            words += "; no price observed, so lost sales are not counted"
        return p, None if price or ads else "no price observed", words
    if kind == "currency":
        p = {"amount": 1, "usd_per_unit": price}
        words = (f"${price:.2f} of currency at the app's price, with a {central(kind, p)['p_would_pay']:.0%} chance "
                 f"the user would have bought it")
        return p, None if price else "no price observed", words
    if kind == "queue_priority":
        return {}, None, "no marginal serving cost (the peak-capacity cost is real but unknown)"
    return {}, None, "no marginal serving cost"


def costs(kind: str, params: dict) -> tuple[float, float]:
    """Cost of one grant at 2k and 8k input tokens of context (only inference depends on context)."""
    econ, _ = breakeven.load()
    return tuple(breakeven.reward_cost(kind, {**params, "tokens_in": t}, "central", econ) for t in CONTEXTS)


def benchmarks() -> dict:
    _, ecpm = breakeven.load()
    return {(b["region"], b["platform"]): b for b in ecpm["benchmarks"]}


def cost_line(kind: str, cost_2k: float, cost_8k: float, not_counted: str | None, assumptions: str,
              lost: str | None, note: str | None) -> str:
    be_2k, be_8k = breakeven.break_even_ecpm(cost_2k), breakeven.break_even_ecpm(cost_8k)
    if not_counted:
        head = f"Serving cost not counted: {not_counted}."
    elif kind in ZERO_COST_KINDS:
        head = "Costs nothing extra to serve" + ("." if lost or note else ", so any completed view pays for it.")
    elif kind == "inference":
        head = (f"Costs ~${cost_2k:.4f} per reward to serve; pays for itself above ${be_2k:.2f} eCPM "
                f"at 2k context (${be_8k:.2f} at 8k).")
    else:
        head = f"Costs ~${cost_2k:.4f} per reward to serve; pays for itself above ${be_2k:.2f} eCPM."
    if lost:
        head += f" It may give away something the app could sell ({lost}); that lost sale isn't counted."
    if note:
        head += f" {note}"
    na, latam = benchmarks()[(REGION, PLATFORM)], benchmarks()[("latam", PLATFORM)]
    return (f"{head} A rewarded view earns ${na['low']:.2f}-{na['high']:.2f} eCPM in North America on Android, "
            f"${latam['low']:.2f} in LATAM. Assumes {assumptions}; one view per reward; a publisher-net eCPM "
            f"(share 1).")


def annotate(candidate: Candidate, model: ProductModel) -> Economics:
    """The cost mark. A possible lost sale is flagged beside it and never changes its verdict."""
    kind = candidate.reward.kind
    params, not_counted, assumptions = describe(candidate, model)
    lost, note = sale_marks(candidate, model)
    cost_2k, cost_8k = costs(kind, params)
    result = breakeven.verdict(kind, {**params, "tokens_in": CONTEXTS[1]}, REGION, PLATFORM)
    # A cost the line can't count is never a PASS: it depends on the missing number.
    verdict = "CONDITIONAL" if not_counted else result["verdict"]
    return Economics(cost_2k=round(cost_2k, 6), cost_8k=round(cost_8k, 6),
                     breakeven_ecpm_2k=round(breakeven.break_even_ecpm(cost_2k), 2),
                     breakeven_ecpm_8k=round(breakeven.break_even_ecpm(cost_8k), 2),
                     benchmark_ecpm=result["benchmark"]["central"], verdict=verdict, lost_sale=lost,
                     assumption_line=cost_line(kind, cost_2k, cost_8k, not_counted, assumptions, lost, note))


def uncounted(econ: Economics) -> bool:
    """Whether the line couldn't count the cost: `annotate` marks that CONDITIONAL at zero, which a counted cost never
    is (zero always passes)."""
    return econ.verdict == "CONDITIONAL" and econ.cost_2k == 0


def apply(candidates: list[Candidate], model: ProductModel, mode: str) -> list[Candidate]:
    """Adds economics to every live candidate. `annotate` never drops; `gate` drops only a FAIL."""
    out = []
    for c in candidates:
        if c.dropped_reason or c.kind == "no_opportunity":
            out.append(c)
            continue
        econ = annotate(c, model)
        dropped = "economics FAIL (gate mode)" if mode == "gate" and econ.verdict == "FAIL" else None
        out.append(c.model_copy(update={"economics": econ, "dropped_reason": dropped}))
    return out
