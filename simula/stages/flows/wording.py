"""Plain words the deck and the exhibit share, and the check codes' plain names."""

import re

from simula.contracts import Candidate, ProductModel
from simula.stages.propose import BUCKETS, depths, root_id

IDS = re.compile(r"\s*\(?\b(?:s\d{2}(?:\.e\d+)?|c\d{2}|M\d{1,3}|new:[\w-]+)\b\)?")
FAIL_NOTE = "That didn't go through. Nothing was used, and the app is as it was."
NOT_WIRED = "not wired"
REWARD_NOT_SHOWN = "reward not shown"
CODES = re.compile(r"\b(?:g|c\d)_[a-z_]+\b")

REWARD_RULE = ("In every idea the user chooses to play. The reward comes once, only after the play is verified, and "
               "closing the game or a failed ad uses nothing up.")
VERDICTS = {"accept": "accepted", "conditional": "conditional", "reject": "rejected",
            "needs_human": "waiting on a person"}
PLAIN_CHECKS = {
    "g_policy": "a fair, opt-in offer",
    "g_no_cash": "no cash reward",
    "g_no_chat_content": "no chat content needed",
    "g_no_free_removal": "nothing free taken away",
    "g_brand_safety": "a brand-safe place for the offer",
    "c1_revealed_value": "people already value what it gives",
    "c2_evidence": "backed by what was seen in the app",
    "c4_protects_subscription": "protects the subscription",
    "c5_moment": "the right moment",
    "c6_fits_simula": "fits a rewarded game",
    "c7_specific": "specific to this app",
}


def app_title(model: ProductModel, key: str) -> str:
    """The app's name for the product team: ProductModel.app_name, as the app's own screens show it, else the config
    key title-cased, never the raw lowercase key."""
    # getattr until #15 (fix-model-hygiene) adds app_name, default "", to this branch's contract; then model.app_name
    return getattr(model, "app_name", None) or key.title()


def caption(c: Candidate) -> str:
    return c.title.removeprefix(f"{BUCKETS.get(c.kind, '')}: ")


def plain(text: str) -> str:
    """Slide text: no ids or check codes a product team would have to decode."""
    text = CODES.sub(lambda m: PLAIN_CHECKS.get(m.group(0), m.group(0)), text)
    return " ".join(IDS.sub("", text).split())


def reward_line(c: Candidate) -> str:
    r = c.reward
    amount = "" if r.amount == 1 else f"{r.amount:g} "
    return f"{amount}{r.unit}, {r.duration}" if r.duration else f"{amount}{r.unit}"


def reach_text(c: Candidate, model: ProductModel) -> str:
    """Where the offer sits and the moment it appears. Depth says where, never how many people reach it: a tab is
    depth 0 like the root, and the trigger narrows who sees it (the audience isn't measured). A modal or sheet is
    placed by the screen it covers."""
    trigger = next((s for s in model.states if s.id == c.trigger_state_id), None)
    popup = trigger is not None and trigger.kind != "screen" and trigger.parent_id is not None
    place = trigger.parent_id if popup else c.trigger_state_id
    if place == root_id(model):
        where = "the first screen people see"
    else:
        where = ("a main tab", "a screen one tap in", "a screen a few taps in")[min(depths(model).get(place, 2), 2)]
    return (f"Reach scenario, not a measurement: the offer sits {'in a pop-up over' if popup else 'on'} {where}, "
            f"and appears only when this happens: {c.trigger_event.rstrip('.')}.")
