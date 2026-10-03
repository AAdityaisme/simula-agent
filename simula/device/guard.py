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
REAL_WORD = re.compile(r"[^\W\d_]{2,}")  # a label's letters ("Go", "OK"), not an icon's glyph or a count ("♥", "2K")
# a one-word entry counts in a label this short, or one it starts ("Pay $4.99 with saved card"): prose that says
# "like" is no Like button
SHORT = 4
# a refused label this short is a control that a tap inside it lands on, as a button's is and, outside the core loop,
# one that opens with the refused entry; any other longer one is text (an AI's reply around the composer's Send)
CONTROL_WORDS = 6


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


def blocks(*lists: list[str], anywhere: list[str] | tuple = (), patterns: list[str] | tuple = ()) \
        -> tuple[re.Pattern, re.Pattern]:
    """The patterns of every entry, and of those that count in any label: the entries of two or more words and the
    anywhere ones; both with the raw patterns."""
    entries = [e for group in lists for e in group]
    longer = [e for e in entries if len(e.split()) > 1]
    return phrases([*entries, *anywhere], patterns), phrases([*longer, *anywhere], patterns)


BLOCKS = tomllib.loads((config.CONFIG / "hard_blocks.toml").read_text())
WORDS, ANYWHERE, PATTERNS = BLOCKS["words"], BLOCKS["anywhere"], BLOCKS["patterns"]
ALWAYS = blocks(WORDS, anywhere=ANYWHERE, patterns=PATTERNS)
OUTSIDE_CORE = blocks(WORDS, BLOCKS["outside_core"], anywhere=ANYWHERE, patterns=PATTERNS)
CONFIRM = blocks(BLOCKS["confirm"])


def in_billing(foreground: str) -> bool:
    return foreground == BILLING


def signing_in(foreground: str) -> bool:
    return foreground == ACCOUNT_CHOOSER


def labels(element: dict) -> list[str]:
    """Its text and its label, each on its own, when not blank."""
    return [s for s in (element.get("text"), element.get("label")) if s and s.strip()]


def id_evidence(element: dict) -> list[str]:
    """Its id without the package (which may hold any word), unless its labels hold a real word: so a row's generic
    id ("post_item") never outweighs what it shows, and an icon's glyph or count ("♥", "2K") never hides its id."""
    worded = any(REAL_WORD.search(s) for s in labels(element))
    return [] if worded else [short_id(element.get("identifier"))]


def hit(blocked: tuple[re.Pattern, re.Pattern], texts: list[str], ids: list[str] | tuple = ()) -> str | None:
    """The entry the first matching text or id holds: in a text of up to SHORT words, any entry anywhere; in a longer
    one, any entry it starts with, or a longer entry or pattern anywhere. An id holds no prose: any entry anywhere
    ("toolbar_menu_action_share_button")."""
    every, longer = blocked
    found = (every.search(t) if whole or len(t.split()) <= SHORT else every.match(t) or longer.search(t)
             for strings, whole in ((texts, False), (ids, True)) for s in strings for t in spellings(s))
    return next((m.group() for m in found if m), None)


def is_control(element: dict, label: str, blocked: tuple[re.Pattern, re.Pattern], core: bool) -> bool:
    """Whether a tap inside element lands on the control its label names: a button, a label of up to CONTROL_WORDS
    words, or, outside the core loop, one that opens with a blocked entry ("Send message to all selected group
    members"). In the core loop such a long label is the AI's reply ("Share …") around the composer."""
    return ("Button" in element.get("type", "") or len(spellings(label)[1].split()) <= CONTROL_WORDS
            or not core and any(blocked[0].match(t) for t in spellings(label)))


# ponytail: whole words in any short label or one they start, so a title such as "Password safety tips" is refused
# too, and so is a confirm on a screen whose long text holds a phrase ("delete my account"); safety before coverage,
# and every refusal is logged denied, so the scorecard shows the cost
def blocked_tap(element: dict, screen: list[dict] | None = None, *, core: bool = False) -> str | None:
    """Why a tap on element must not run, or None. Refused: a hard-block word it carries (account deletion or
    changes, sign-out, public posts, actions toward other people, purchases, and sending outside the core loop); a
    tap point inside a control of the screen that carries one, since the tap lands on that; a confirm while any text
    on the screen carries one, as a dialog that names it does. Words come from config/hard_blocks.toml."""
    blocked = ALWAYS if core else OUTSIDE_CORE
    if word := hit(blocked, labels(element), id_evidence(element)):
        return word
    if not screen:
        return None
    x, y = center(rect(element))
    point = Rect(x=x, y=y, w=0, h=0)
    under = next((word for e in screen if inside(point, rect(e))
                  and (word := hit(blocked, [s for s in labels(e) if is_control(e, s, blocked, core)]))), None)
    if under:
        return f"{under} (at the tap point)"
    if yes := hit(CONFIRM, labels(element), id_evidence(element)):
        if shown := next((word for e in screen if (word := hit(blocked, labels(e)))), None):
            return f"{yes} ({shown} on the screen)"
    return None


def allowed_text(text: str, *, core: bool, field: str) -> bool:
    """Typing is allowed only for a CORE_MESSAGES text in the composer inside the core loop (which picks the next
    one), or a SEARCH_QUERIES text in a search box (field == "search"). Nothing else is ever typed by the agent."""
    return (core and field == "composer" and text in CORE_MESSAGES) or (field == "search" and text in SEARCH_QUERIES)


def core_allowed(recipient: str) -> bool:
    """start_core only when the planner says the recipient is an AI or a bot."""
    return recipient == "ai"
