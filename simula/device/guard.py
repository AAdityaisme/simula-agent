"""The agent's hard blocks, checked at the moment it acts. The scripted explorer's deny-list doesn't apply to the
agent; only these do: no purchases, no posting or messaging real people, no account deletion or account changes."""

import re
import tomllib
import unicodedata

from simula import config
from simula.contracts import Rect
from simula.device.observe import center, inside, rect, short_id
from simula.stages.explore import CORE_MESSAGES, SEARCH_QUERIES

BILLING = "com.android.vending"  # the Play Store: its payment sheet gets BACK before anyone sees it
ACCOUNT_CHOOSER = "com.google.android.gms"  # Google sign-in's account chooser: part of signing in, not leaving
# where an id's words meet: camelCase, an acronym before a word (LOGOUTButton), letters against digits (btn_post2)
WORD_BREAK = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
HYPHEN = re.compile("[-\u2010\u2011]")  # the hyphen, Unicode's hyphen and its non-breaking one
NOT_WORD = re.compile(r"[\W_]+")
POSSESSIVE = re.compile(r"(?<=\w)['\u2019]s\b", re.IGNORECASE)  # "this app's account" is "this app account"
REAL_WORD = re.compile(r"[^\W\d_]{3,}")  # a label's letters, not an icon's glyph or a count ("♥", "2K")
SHORT = 4  # a one-word entry counts only in a label this short: prose that says "like" is no Like button
CONTEXT = 6  # the screen's tap-point and dialog checks read only labels this short, never a story's container


def spellings(text: str) -> list[str]:
    """text as lowercase words with single spaces, its hyphens once dropped ("e-mail" is "email") and once spaces.
    Full-width letters fold (NFKC), invisible format characters (zero-width, soft hyphen) and possessives go, and an
    id's words split."""
    text = "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf")
    text = WORD_BREAK.sub(" ", POSSESSIVE.sub("", text))
    return [NOT_WORD.sub(" ", t.lower()).strip() for t in (HYPHEN.sub("", text), text)]


def phrases(entries: list[str], patterns: list[str]) -> re.Pattern:
    """One whole-word pattern over spellings() for every entry (longest first, so a hit names the longest) and the
    raw patterns."""
    spelled = sorted({s for e in entries for s in spellings(e)}, key=lambda s: (-len(s), s))
    return re.compile(r"(?<!\S)(?:" + "|".join([*map(re.escape, spelled), *patterns]) + r")(?!\S)")


def blocks(*lists: list[str], patterns: list[str] | tuple = ()) -> tuple[re.Pattern, re.Pattern]:
    """The patterns for a label of up to SHORT words (every entry) and for a longer one (the entries of two or more
    words, and the raw patterns)."""
    entries = [e for group in lists for e in group]
    return phrases(entries, patterns), phrases([e for e in entries if len(e.split()) > 1], patterns)


BLOCKS = tomllib.loads((config.CONFIG / "hard_blocks.toml").read_text())
WORDS = BLOCKS["words"]
ALWAYS = blocks(WORDS, patterns=BLOCKS["patterns"])
OUTSIDE_CORE = blocks(WORDS, BLOCKS["outside_core"], patterns=BLOCKS["patterns"])
CONFIRM = blocks(BLOCKS["confirm"])


def in_billing(foreground: str) -> bool:
    return foreground == BILLING


def signing_in(foreground: str) -> bool:
    return foreground == ACCOUNT_CHOOSER


def labels(element: dict) -> list[str]:
    """Its text and its label, each on its own, when not blank."""
    return [s for s in (element.get("text"), element.get("label")) if s and s.strip()]


def said(element: dict) -> list[str]:
    """What an element says: its labels, and its id without the package (which may hold any word) unless they hold a
    real word, so a row's generic id ("post_item") never outweighs what it shows, and an icon's glyph or count
    ("♥", "2K") never hides its id."""
    shown = labels(element)
    return shown if any(REAL_WORD.search(s) for s in shown) else [*shown, short_id(element.get("identifier"))]


def hit(blocked: tuple[re.Pattern, re.Pattern], texts: list[str], most: float = float("inf")) -> str | None:
    """The entry the first matching text holds: any entry in a text of up to SHORT words, only the longer entries
    and the patterns in a longer one. A text over `most` words isn't read."""
    spelled = ((t, len(t.split())) for s in texts for t in spellings(s))
    return next((m.group() for t, n in spelled if n <= most and (m := blocked[n > SHORT].search(t))), None)


# ponytail: whole words in any short label, so a title such as "Password safety tips" is refused too; safety before
# coverage, and every refusal is logged denied, so the scorecard shows the cost
def blocked_tap(element: dict, screen: list[dict] | None = None, *, core: bool = False) -> str | None:
    """Why a tap on element must not run, or None. Refused: a hard-block word it carries (account deletion or
    changes, sign-out, public posts, actions toward other people, purchases, and sending outside the core loop); a
    tap point inside an element of the screen whose label of up to CONTEXT words carries one, since the tap lands on
    that; a confirm while such a label shows, as a dialog that names it does. Words come from
    config/hard_blocks.toml."""
    blocked = ALWAYS if core else OUTSIDE_CORE
    if word := hit(blocked, said(element)):
        return word
    if not screen:
        return None
    x, y = center(rect(element))
    shown = [(rect(e), word) for e in screen if (word := hit(blocked, labels(e), CONTEXT))]
    if under := next((word for box, word in shown if inside(Rect(x=x, y=y, w=0, h=0), box)), None):
        return f"{under} (at the tap point)"
    if shown and (yes := hit(CONFIRM, said(element))):
        return f"{yes} ({shown[0][1]} on the screen)"
    return None


def allowed_text(text: str, *, core: bool, field: str) -> bool:
    """Typing is allowed only for a CORE_MESSAGES text in the composer inside the core loop (which picks the next
    one), or a SEARCH_QUERIES text in a search box (field == "search"). Nothing else is ever typed by the agent."""
    return (core and field == "composer" and text in CORE_MESSAGES) or (field == "search" and text in SEARCH_QUERIES)


def core_allowed(recipient: str) -> bool:
    """start_core only when the planner says the recipient is an AI or a bot."""
    return recipient == "ai"
