"""The agent's hard blocks, checked at the moment it acts. The scripted explorer's deny-list doesn't apply to the
agent; only these do: no purchases, no posting or messaging real people, no account deletion or account changes."""

import re
import tomllib
import unicodedata

from simula import config
from simula.contracts import Rect
from simula.device.observe import TOGGLE, area, center, inside, rect, short_id
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
# on text that is no control, a phrase counts only in a label this short or one it opens: longer text that mentions
# one (a headline, an AI's reply around the composer's Send) is content
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


def blocks(*lists: list[str], patterns: list[str] | tuple = ()) -> tuple[re.Pattern, re.Pattern]:
    """The patterns of every entry, and of the phrases: the entries of two or more words; both with the raw
    patterns."""
    entries = [e for group in lists for e in group]
    longer = [e for e in entries if len(e.split()) > 1]
    return phrases(entries, patterns), phrases(longer, patterns)


BLOCKS = tomllib.loads((config.CONFIG / "hard_blocks.toml").read_text())
WORDS, PATTERNS = BLOCKS["words"], BLOCKS["patterns"]
ALWAYS = blocks(WORDS, patterns=PATTERNS)
OUTSIDE_CORE = blocks(WORDS, BLOCKS["outside_core"], patterns=PATTERNS)
CONFIRM = blocks(BLOCKS["confirm"])
SECURITY = phrases(BLOCKS["security"], [])


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


def hit(blocked: tuple[re.Pattern, re.Pattern], texts: list[str], ids: list[str] | tuple = (), *,
        content: bool = False) -> str | None:
    """The entry the first matching text or id holds: in a text of up to SHORT words, any entry anywhere; in a longer
    one, any entry it starts with, or a longer entry or pattern anywhere. An id holds no prose: any entry anywhere
    ("toolbar_menu_action_share_button"). Content (text that is no control) names an action only as a command
    does: any entry opening it, or a phrase in a text of up to CONTROL_WORDS words."""
    every, longer = blocked

    def find(t: str, whole: bool) -> re.Match | None:
        n = len(t.split())
        if whole:
            return every.search(t)
        if content:
            return every.match(t) or (longer.search(t) if n <= CONTROL_WORDS else None)
        return every.search(t) if n <= SHORT else every.match(t) or longer.search(t)

    found = (find(t, whole) for strings, whole in ((texts, False), (ids, True)) for s in strings for t in spellings(s))
    return next((m.group() for m in found if m), None)


def is_control(element: dict) -> bool:
    """A tap target by its own shape: a button, or an icon or row named only by its accessibility label. Other text
    (a heading, a feed row's title, an article) is content."""
    return "Button" in element.get("type", "") or not (element.get("text") or "").strip() and bool(labels(element))


def names(blocked: tuple[re.Pattern, re.Pattern], element: dict) -> str | None:
    """The entry element names as its own action: any in a control's label or an action id, only a command's in
    content."""
    return hit(blocked, labels(element), id_evidence(element), content=not is_control(element))


def reply(element: dict, label: str, core: bool) -> bool:
    """In the core loop, a label longer than CONTROL_WORDS words that is no button is the AI's reply ("Share …")
    around the composer, not a control a tap inside it lands on."""
    return core and "Button" not in element.get("type", "") and len(spellings(label)[1].split()) > CONTROL_WORDS


def dialog(element: dict, screen: list[dict]) -> list[dict]:
    """What a confirm answers: the text in the smallest element around it that holds a caption (text that is neither
    a control nor inside one: a dialog's title or message, an item's headline), or with none the screen's. Another
    choice (Cancel, its button row) is no caption, and an icon (an item's share) is not what the dialog says."""
    own, r = set(labels(element)), rect(element)
    text = [e for e in screen if "Image" not in e.get("type", "") and any(s not in own for s in labels(e))]
    choices = [rect(e) for e in screen if is_control(e)]
    captions = [rect(t) for t in text if not is_control(t) and not any(inside(rect(t), c) for c in choices)]
    boxes = [rect(e) for e in screen if inside(r, rect(e)) and area(rect(e)) > area(r)
             and any(inside(c, rect(e)) for c in captions)]
    box = min(boxes, key=area, default=None)
    return [t for t in text if box is None or inside(rect(t), box)]


def security_toggle(element: dict) -> str | None:
    """The security setting a switch or checkbox names ("Two-step verification"): flipping it is the change."""
    if not (TOGGLE.search(element.get("type", "")) or "checked" in element):
        return None
    found = (SECURITY.search(t) for s in labels(element) for t in spellings(s))
    return next((m.group() for m in found if m), None)


# ponytail: a command is told from content by its shape (a button, a label-only icon, a text opening with the
# action), so a text-only "Post" control is refused and so is a heading that opens with an entry ("Follow along",
# "Post Malone…") and a label-only reading link ("The Evening Post"); safety before coverage, and every refusal is
# logged denied, so the scorecard shows the cost
def blocked_tap(element: dict, screen: list[dict] | None = None, *, core: bool = False) -> str | None:
    """Why a tap on element must not run, or None. Refused: a hard-block action it names (account deletion or
    changes, sign-out, public posts, actions toward other people, purchases, and sending outside the core loop); a
    tap point inside an element of the screen that names one, since the tap lands on that; a confirm while the
    dialog around it names one; a switch that names a security setting. Words come from config/hard_blocks.toml."""
    blocked = ALWAYS if core else OUTSIDE_CORE
    if word := names(blocked, element) or security_toggle(element):
        return word
    if not screen:
        return None
    x, y = center(rect(element))
    point = Rect(x=x, y=y, w=0, h=0)
    under = next((word for e in screen if inside(point, rect(e)) and (word := hit(
        blocked, [s for s in labels(e) if not reply(e, s, core)], content=not is_control(e)))), None)
    if under:
        return f"{under} (at the tap point)"
    if yes := hit(CONFIRM, labels(element), id_evidence(element)):
        if shown := next((word for e in dialog(element, screen) if (word := hit(blocked, labels(e)))), None):
            return f"{yes} ({shown} on the screen)"
    return None


def allowed_text(text: str, *, core: bool, field: str) -> bool:
    """Typing is allowed only for a CORE_MESSAGES text in the composer inside the core loop (which picks the next
    one), or a SEARCH_QUERIES text in a search box (field == "search"). Nothing else is ever typed by the agent."""
    return (core and field == "composer" and text in CORE_MESSAGES) or (field == "search" and text in SEARCH_QUERIES)


def core_allowed(recipient: str) -> bool:
    """start_core only when the planner says the recipient is an AI or a bot."""
    return recipient == "ai"
