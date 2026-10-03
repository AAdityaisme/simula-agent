"""The agent's hard blocks, checked at the moment it acts. The scripted explorer's deny-list doesn't apply to the
agent; only these do: no purchases, no posting or messaging real people, no account deletion or account changes."""

import re
import tomllib

from simula import config
from simula.device.observe import ID_WORDS, short_id
from simula.stages.explore import CORE_MESSAGES, SEARCH_QUERIES

BILLING = "com.android.vending"  # the Play Store: its payment sheet gets BACK before anyone sees it
ACCOUNT_CHOOSER = "com.google.android.gms"  # Google sign-in's account chooser: part of signing in, not leaving
WORDS = tomllib.loads((config.CONFIG / "hard_blocks.toml").read_text())["words"]
# ponytail: whole words anywhere in the text, so a card titled "How to share a story" is refused too; the safe way
# round, it only loses a card
BLOCKED = re.compile(r"\b(?:" + "|".join(r"\s+".join(map(re.escape, w.split())) for w in WORDS) + r")\b",
                     re.IGNORECASE)


def in_billing(foreground: str) -> bool:
    return foreground == BILLING


def signing_in(foreground: str) -> bool:
    return foreground == ACCOUNT_CHOOSER


def blocked_tap(element: dict) -> str | None:
    """The hard-block word the element's text, label or id carries (account deletion or deactivation, password, email
    or phone changes, sign-out, public post, publish or share), or None. Words come from config/hard_blocks.toml. The
    id is read without its package, which may hold any word."""
    said = "\n".join((element.get("text") or "", element.get("label") or "", short_id(element.get("identifier"))))
    hit = BLOCKED.search(ID_WORDS.sub(" ", said))
    return " ".join(hit.group().lower().split()) if hit else None


def allowed_text(text: str, *, core: bool, field: str) -> bool:
    """Typing is allowed only for a CORE_MESSAGES text inside the core loop (which picks the next one), or a
    SEARCH_QUERIES text in a search box (field == "search"). Nothing else is ever typed by the agent."""
    return (core and text in CORE_MESSAGES) or (field == "search" and text in SEARCH_QUERIES)


def core_allowed(recipient: str) -> bool:
    """start_core only when the planner says the recipient is an AI or a bot."""
    return recipient == "ai"
