"""The agent's hard blocks, checked at the moment it acts. The scripted explorer's deny-list doesn't apply to the
agent; only these do: no purchases, no posting or messaging real people, no account deletion or account changes."""

import re
import tomllib
import unicodedata

from simula import config
from simula.contracts import Rect
from simula.device.observe import center, inside, rect, short_id, words
from simula.stages.explore import CORE_MESSAGES, SEARCH_QUERIES

BILLING = "com.android.vending"  # the Play Store: its payment sheet gets BACK before anyone sees it
ACCOUNT_CHOOSER = "com.google.android.gms"  # Google sign-in's account chooser: part of signing in, not leaving
# where an id's words meet: camelCase, an acronym before a word (LOGOUTButton), letters against digits (btn_post2)
WORD_BREAK = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
HYPHEN = re.compile("[-\u2010\u2011]")  # the hyphen, Unicode's hyphen and its non-breaking one
NOT_WORD = re.compile(r"[\W_]+")


def spellings(text: str) -> list[str]:
    """text as lowercase words with single spaces, its hyphens once dropped ("e-mail" is "email") and once spaces.
    Full-width letters fold (NFKC), invisible format characters (zero-width, soft hyphen) go, and an id's words
    split."""
    text = "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf")
    text = WORD_BREAK.sub(" ", text)
    return [NOT_WORD.sub(" ", t.lower()).strip() for t in (HYPHEN.sub("", text), text)]


def phrases(*lists: list[str], patterns: list[str] | tuple = ()) -> re.Pattern:
    """One whole-word pattern over spellings() for every phrase in lists (longest first, so a hit names the longest)
    and the raw patterns."""
    spelled = sorted({s for group in lists for p in group for s in spellings(p)}, key=lambda s: (-len(s), s))
    return re.compile(r"(?<!\S)(?:" + "|".join([*map(re.escape, spelled), *patterns]) + r")(?!\S)")


BLOCKS = tomllib.loads((config.CONFIG / "hard_blocks.toml").read_text())
WORDS = BLOCKS["words"]
ALWAYS = phrases(WORDS, patterns=BLOCKS["patterns"])
OUTSIDE_CORE = phrases(WORDS, BLOCKS["outside_core"], patterns=BLOCKS["patterns"])
CONFIRM = phrases(BLOCKS["confirm"])


def in_billing(foreground: str) -> bool:
    return foreground == BILLING


def signing_in(foreground: str) -> bool:
    return foreground == ACCOUNT_CHOOSER


def said(element: dict) -> list[str]:
    """What an element says: its text and label, or its id without the package (which may hold any word) when it
    shows neither, so a row's generic id ("post_item") never outweighs what it shows."""
    return [s for s in (element.get("text"), element.get("label")) if s and s.strip()] \
        or [short_id(element.get("identifier"))]


def hit(pattern: re.Pattern, element: dict) -> str | None:
    return next((m.group() for s in said(element) for t in spellings(s) if (m := pattern.search(t))), None)


# ponytail: whole words anywhere, so a card titled "How to share a story" is refused too; safety before coverage,
# and every refusal is logged denied, so the scorecard shows the cost
def blocked_tap(element: dict, screen: list[dict] | None = None, *, core: bool = False) -> str | None:
    """Why a tap on element must not run, or None. Refused: a hard-block word it carries (account deletion or
    changes, sign-out, public posts, actions toward other people, purchases, and sending outside the core loop); a
    tap point inside a worded element of the screen that carries one, since the tap lands on that; a confirm while
    one shows on the screen, as a dialog that names it does. Words come from config/hard_blocks.toml."""
    blocked = ALWAYS if core else OUTSIDE_CORE
    if word := hit(blocked, element):
        return word
    if not screen:
        return None
    x, y = center(rect(element))
    shown = [(rect(e), word) for e in screen if words(e) and (word := hit(blocked, e))]
    if under := next((word for box, word in shown if inside(Rect(x=x, y=y, w=0, h=0), box)), None):
        return f"{under} (at the tap point)"
    if shown and (yes := hit(CONFIRM, element)):
        return f"{yes} ({shown[0][1]} on the screen)"
    return None


def allowed_text(text: str, *, core: bool, field: str) -> bool:
    """Typing is allowed only for a CORE_MESSAGES text in the composer inside the core loop (which picks the next
    one), or a SEARCH_QUERIES text in a search box (field == "search"). Nothing else is ever typed by the agent."""
    return (core and field == "composer" and text in CORE_MESSAGES) or (field == "search" and text in SEARCH_QUERIES)


def core_allowed(recipient: str) -> bool:
    """start_core only when the planner says the recipient is an AI or a bot."""
    return recipient == "ai"
