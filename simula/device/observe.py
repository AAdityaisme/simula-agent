"""Rules over one observation (an element list plus a screenshot): geometry, fingerprint, settle, modal
detection, candidates, and the deny-list. No device calls live here, so every rule is tested on fixtures."""

import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from skimage.measure import label, regionprops

from simula.contracts import Device, Rect

ELEMENTS_PREFIX = "Found these elements on screen: "
BUCKET_DP = 8
TOP_CHROME_BOTTOM_PX = 700
LAYOUT_SHARE = 0.4
MIN_CONTROL_PX = 48
TAB_BAND_PX = 330
# Calibrated on the labeled captures in tests/fixtures/trees/pairs.toml (128-bit dHash): same pairs sit at
# top <= 1 and content <= 11, different pairs at top >= 10 or content >= 18.
TOP_BITS = 8
CONTENT_BITS = 12
PATCH_DP = 24
LOOK_BITS = 16
# Arrival's structural threshold, picked once in step 4a on labeled pairs from all three test apps (the deep app's
# recorded runs and the other two apps' fixture captures): the highest value that keeps every true pair the model
# called same (the lowest is 0.345). It is also the best value on each app alone.
STRUCTURE_SAME = 0.34
# A same-window overlay's controls span less than this share of the content area (the tall sheets recorded so far
# span 0.927-0.948), unless a scrim blends what's above them with one color. The platform's lightest standard scrims
# are 32%: Material 3's black (androidx compose material3 ScrimTokens.ContainerOpacity = 0.32f) and Material 2's
# onSurface (compose material ModalBottomSheetDefaults.scrimColor, alpha 0.32f; white on darkColors), which leave 68%
# of the strip's contrast; a dialog's window dim defaults to 60% (frameworks/base themes.xml backgroundDimAmount). The
# cutoff sits halfway between 68% and no blend.
OVERLAY_SHARE = 0.9
SCRIM_SHARE = 0.84

# Entry words (upgrade, plans, premium, plus, try, remove ads) may open an upsell; confirm words never run. Every
# stem is a whole word, so "Preview", "Photos" and "Bitcoin" pass. What can't be undone (the account, its data,
# money) is denied on a control-shaped label, and on a longer one that starts with it ("Delete my account and all of
# its data"): a headline's verb follows its subject ("Apple to pay $490 million"). The rest is denied only on
# control-shaped labels, since a headline that mentions "report" is not a report button. A toggle's on-state
# ("Following", "Liked", "Subscribed") stays denied: tapping it undoes it.
DENY_ALWAYS = re.compile(r"\b(?:log ?out|sign ?out|sign ?in|sign ?up|log ?in|create account|continue with|delete|"
                         r"remove(?! ads\b)|close (?:my |your |the )?account|cancel|(?:un)?subscribed?|buy|"
                         r"pay(?:ments?)?|purchases?|restore|confirm|"
                         r"start\b.{0,24}\btrial|passwords?|place order|check ?out|donat\w*)\b", re.IGNORECASE)
# a command starts its clause, maybe after one adverb: "Yes, permanently delete my account", "Already have one? Log in"
# ponytail: a command written mid-clause ("Tap here to confirm your purchase") reads like a headline and passes, and a
# headline opening with an -ly word ("Early payments: …") is denied; the safe way round, it only loses a card
DENY_COMMAND = re.compile(rf"(?:^|[,;:?!.\u2013\u2014])\W*(?:\w+ly\s+)?({DENY_ALWAYS.pattern})",
                          re.IGNORECASE | re.MULTILINE)
DENY = re.compile(r"\b(?:report|(?:un)?block|clear|e[- ]?mails?|security|personas?|(?:un)?follow(?:ing)?|"
                  r"(?:un)?favou?rit\w*|(?:un)?liked?|hearts?|hide|terms|privacy|rate us|review|camera|photo|gallery|"
                  r"allow|permissions?|install|open in|submit|proceed|tip|rate|give \d stars?|save changes|publish|"
                  r"post)\b", re.IGNORECASE)
CONTROL_WORDS = 4
# the always-denied words that undo something; the rest ask for an account or money
UNDOING = re.compile(r"delete|remove|cancel|restore|confirm|unsubscribed?", re.IGNORECASE)
ACCOUNT = re.compile(r"log|sign|account|continue with|password", re.IGNORECASE)
# a content filter's own phrase: on the filter's row its verb is no deny hit, while a "Block user" or "Report" there is
FILTER_PHRASE = re.compile(r"\b(?:hide|block|allow)\s+(?:all\s+)?(?:nsfw|sfw|explicit|mature|adult|sensitive)\b",
                           re.IGNORECASE)
ID_WORDS = re.compile(r"(?<=[a-z])(?=[A-Z])|[_-]")  # an id's words ("buttonFavorite", "btn_like", "login-close") for \b
# an icon-only control (no text beyond an X) whose id names a dismissal of a sign-in sheet ("login-close-button")
# commits nothing: only the sign-in words leave its id, and any other deny word there ("delete-and-close") still
# denies it; a text button such as "Close account" meets every deny word
ICON_ONLY = re.compile(r"\s*[x×✕✖]?\s*", re.IGNORECASE)
DISMISS_ID = re.compile(r"\b(?:close|dismiss|skip|not now)\b", re.IGNORECASE)
SIGN_IN = re.compile(r"\b(?:log ?in|sign ?in|sign ?up|continue with)\b", re.IGNORECASE)
TOGGLE = re.compile(r"Switch|CheckBox|ToggleButton", re.IGNORECASE)
DENY_ON_UPSELL = re.compile(r"continue|try|start|get|claim|unlock|join|redeem|activate|\bremove\b", re.IGNORECASE)
DENY_IN_TOUR = re.compile(r"send|swipe|regenerate", re.IGNORECASE)
DENY_IN_CORE = re.compile(r"\b(?:gifts?|coins?|gems?|tips?|donat\w*|credits?)\b", re.IGNORECASE)
DISMISS = re.compile(r"^(close\b.*|not now|later|maybe later|no,? thanks|skip|dismiss|got it|x|×|✕)$", re.IGNORECASE)
BLOCKING = re.compile(r"emulator|rooted|captcha|verify (that )?you.?re (a )?human|age verification|"
                      r"date of birth|not supported on this device", re.IGNORECASE)
CURRENCY = r"[$€£¥₹]|\b(?:USD|EUR|GBP|INR|JPY|CAD|AUD)\b"
PRICE = re.compile(rf"(?:{CURRENCY})\s?\d|\d(?:[\d.,]*\d)?\s?(?:{CURRENCY})", re.IGNORECASE)
PAYWALL = re.compile(rf"{PRICE.pattern}|subscription|membership|free trial|per (month|week|year)|"
                     r"/ ?(month|week|year|mo)\b", re.IGNORECASE)
CREATE = re.compile(r"\W*(generate|play|draw|spin|roll|scan)\b", re.IGNORECASE)
ENTRY = re.compile(r"upgrade|\bplans?\b|premium|\bplus\b|\bpro\b|(?<!\d)\+|membership|subscription|remove ads|"
                   r"\bad[- ]free\b|\bno ads\b", re.IGNORECASE)
LIMIT = re.compile(r"\blimits?\b|\bremaining\b|\bquota\b|resets? in|out of (free )?(messages|credits|swipes|chats|"
                   r"articles)|no more (free )?\w+|\bleft today\b", re.IGNORECASE)
DIGITS = re.compile(r"\d")
NUMBER = re.compile(r"\d+")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b|\bago\b", re.IGNORECASE)
LETTER = re.compile(r"[^\W\d_]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
LEAF = re.compile(r"(?:TextView|ImageView|EditText)$")
PAGED = re.compile(r"ViewPager|RecyclerView|ListView|ScrollView")
REDACTED = "[redacted]"
# --allow-account-create's words. A way on without an account is a whole label, so "Watch later" is content.
GUEST = re.compile(r"^\W*(?:[\w'’]+\s+){0,3}?(?:as (?:a )?(?:guest|visitor)|guest(?: mode)?|without (?:an? )?account|"
                   r"without (?:signing|logging) (?:up|in)|without (?:registering|registration|log ?in|sign ?in)|"
                   r"(?:explore|browse|look around) first|browse anonymously|just browsing)\W*$|"
                   r"^\W*(?:skip(?: for now)?|not now|(?:maybe |remind me )?later|no,? thanks?)\W*$", re.IGNORECASE)
# a control that agrees, accepts, consents or attests ("I'm 18+", "I understand", "Yes, ...") is never tapped on the
# way in: a label that speaks for the user in the first person is one, whatever it attests (ATTESTS); text that is no
# button or toggle only when it agrees or accepts, since "At least 8 characters" is a password hint
AGREES = re.compile(r"\bagree|\baccept|\bconsent|\backnowledg|\bunderstand\b|\badult\b", re.IGNORECASE)
ATTESTS = re.compile(r"\b\d+\s*\+|\b(?:over|at least|under) \d+|^\W*(?:yes|i|i['’]?m|i am)\b", re.IGNORECASE)
LOG_IN = re.compile(r"\b(?:log|sign) ?in\b", re.IGNORECASE)
# a sign-in with another account (Google, Apple, ...) or a phone is a real person's, so it is never a way in
OTHER_ACCOUNT = re.compile(r"\b(?:continue|sign ?(?:in|up)|log ?in|connect|register)\s+(?:with|using|via)\b"
                           r"(?!.*\be-?mail\b)", re.IGNORECASE)
PHONE = re.compile(r"\bphone\b|\bmobile\b|\bsms\b", re.IGNORECASE)
SIGN_UP = re.compile(r"sign ?up|create (?:an |my |your )?account|register|continue with|e-?mails?|passwords?|submit|"
                     r"proceed", re.IGNORECASE)
EMAIL_WAY = re.compile(r"\b(?:sign ?up|continue|register|use|join|start|create)\b.*\be-?mail\b", re.IGNORECASE)
SIGN_UP_WAY = re.compile(r"\bsign ?up\b|\bcreate (?:an |my |your )?account\b|\bregister\b", re.IGNORECASE)
# the only labels a way to an email sign-up may have, whole: anything more ("Sign up with email, I'm 18") isn't taken
TO_EMAIL = re.compile(r"^\W*(?:(?:sign ?up|continue|register|join|get started|start|create (?:an |my |your )?account)"
                      r"\s+(?:with|using|by|via)\s+(?:an? |your )?|use (?:an? |your )?)e-?mail(?: address)?\W*$",
                      re.IGNORECASE)
TO_SIGN_UP = re.compile(r"^\W*(?:sign ?up|create (?:an |my |your )?account|register)\W*$", re.IGNORECASE)
SUBMIT = re.compile(r"\b(?:sign ?up|create|register|continue|next|submit|done|join|get started|let'?s go)\b",
                    re.IGNORECASE)
# the only labels a sign-up form's own button may have: anything more ("Continue, I'm 18", "Create") is not sent
PLAIN_SUBMIT = re.compile(r"^\W*(?:sign ?up|create (?:an |my |your )?account|register|continue|next|submit|done|join|"
                          r"get started|let'?s go)\W*$", re.IGNORECASE)
# a name box is the person's name only when that is all its text says: "Character name", "Username" or "Name your
# companion" is another box
PERSON_NAME = re.compile(r"^\W*(?:enter )?(?:your )?(?:full |first |last |display )?name\W*$|"
                         r"^\W*what should we call you\W*$", re.IGNORECASE)
FIELDS = {"email": re.compile(r"e-?mail", re.IGNORECASE), "password": re.compile(r"pass ?word|\bpwd\b", re.IGNORECASE),
          "phone": PHONE, "name": PERSON_NAME}
HUMAN_CHECK = re.compile(rf"{BLOCKING.pattern}|not a robot", re.IGNORECASE)
VERIFY = re.compile(r"\bverif|\bconfirm\w* (?:your )?e-?mail|check your (?:e-?mail|inbox)|\bwe(?:'ve| have)? sent\b|"
                    r"\b(?:enter|type) the code\b|\bone[- ]time\b|\botp\b|magic link", re.IGNORECASE)
CARD = re.compile(r"card number|\bcvv\b|\bcvc\b|expir(?:y|ation) date|billing address", re.IGNORECASE)


# ---------- geometry ----------

def rect(e: dict) -> Rect:
    c = e["coordinates"]
    return Rect(x=c["x"], y=c["y"], w=c["width"], h=c["height"])


def area(r: Rect) -> float:
    return r.w * r.h


def inside(inner: Rect, outer: Rect) -> bool:
    return (outer.x <= inner.x and outer.y <= inner.y
            and inner.x + inner.w <= outer.x + outer.w and inner.y + inner.h <= outer.y + outer.h)


def absorbs(outer: dict, inner: dict) -> bool:
    """Whether outer takes inner in as its own: it holds inner's box, its words are a control's, and it is no leaf
    view (a text, a picture, a text box), which holds nothing. A reply container labelled with the whole reply is
    content: it holds a lifted composer's Send without taking it in."""
    kind = outer.get("type", "")
    return inside(rect(inner), rect(outer)) and control_shaped(words(outer), kind) and not LEAF.search(kind)


def overlaps(a: Rect, b: Rect) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def center(r: Rect) -> tuple[int, int]:
    return int(r.x + r.w / 2), int(r.y + r.h / 2)


def bbox(rects: list[Rect]) -> Rect:
    x0, y0 = min(r.x for r in rects), min(r.y for r in rects)
    x1, y1 = max(r.x + r.w for r in rects), max(r.y + r.h for r in rects)
    return Rect(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def content_area(device: Device) -> float:
    return device.w_px * (device.content_bottom_px - device.content_top_px)


def in_content(e: dict, device: Device) -> bool:
    """The same rule stage 2 uses: starts in the content area, not the status bar, not a full-screen scrim."""
    c = e["coordinates"]
    full_screen = c["width"] >= device.w_px and c["height"] >= 2000
    system = "systemui" in (e.get("identifier") or "")
    on_screen = device.content_top_px <= c["y"] < device.content_bottom_px and 0 <= c["x"] < device.w_px
    return on_screen and not full_screen and not system


def words(e: dict) -> str:
    return (e.get("text") or e.get("label") or "").strip()


def bucket(px: float, device: Device) -> int:
    return int(px / device.scale / BUCKET_DP)


def device_from(elements: list[dict], w_px: int, h_px: int, density: int) -> Device:
    """Insets from the status-bar and navigation-bar elements when listed; the defaults otherwise."""
    status = [e["coordinates"]["height"] for e in elements if (e.get("identifier") or "").endswith("id/status_bar")]
    nav = [e["coordinates"]["y"] for e in elements if (e.get("identifier") or "").endswith("navigationBarBackground")]
    default = Device()
    return Device(w_px=w_px, h_px=h_px, density=density, scale=density / 160,
                  content_top_px=status[0] if status else default.content_top_px,
                  content_bottom_px=nav[0] if nav else h_px - (default.h_px - default.content_bottom_px))


# ---------- redaction ----------

def redact(reply: dict, image: Image.Image, secrets: list[str], parts: list[str] = ()) -> tuple[dict, list[dict], int]:
    """Replaces every listed string (any case), each of parts as a whole word, and every email address in any of an
    element's strings with [redacted] and paints a solid box over those elements in the image, before anything reads
    or saves them. Also returns how many elements were redacted."""
    # the longest part first: "ann" must not take the front of "ann.test" and leave the rest
    listed = [re.escape(s.strip()) for s in secrets if s.strip()] + [rf"\b{re.escape(p)}\b"
                                                                      for p in sorted(parts, key=len, reverse=True)]
    pattern = re.compile("|".join([EMAIL.pattern, *listed]), re.IGNORECASE)
    elements = json.loads(reply["content"][0]["text"].removeprefix(ELEMENTS_PREFIX))
    draw = ImageDraw.Draw(image)
    hits = 0
    for e in elements:
        hit = False
        for key, value in list(e.items()):
            if key not in ("ref", "type") and isinstance(value, str) and pattern.search(value):
                e[key], hit = pattern.sub(REDACTED, value), True
        if hit:
            hits += 1
            r = rect(e)
            draw.rectangle((r.x, r.y, r.x + r.w, r.y + r.h), fill=(0, 0, 0))
    content = [{**reply["content"][0], "text": ELEMENTS_PREFIX + json.dumps(elements, ensure_ascii=False)}]
    return {**reply, "content": content + reply["content"][1:]}, elements, hits


# ---------- fingerprint ----------

def dhash(image: Image.Image) -> int:
    pixels = np.asarray(image.convert("L").resize((17, 8), Image.Resampling.BICUBIC), dtype=np.int16)
    bits = (pixels[:, 1:] > pixels[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def skeleton(elements: list[dict], device: Device) -> str:
    """Class plus size in 8 dp buckets for every content element, position-free, repeats collapsed to 1/2/3+."""
    shapes = Counter((e["type"].split(".")[-1], bucket(e["coordinates"]["width"], device),
                      bucket(e["coordinates"]["height"], device))
                     for e in elements if in_content(e, device))
    items = sorted(f"{kind}:{w}x{h}*{min(n, 3)}" for (kind, w, h), n in shapes.items())
    return hashlib.sha256("|".join(items).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Fingerprint:
    package: str
    top: int
    content: int
    skeleton: str

    def __str__(self) -> str:
        return f"{self.package}|{self.top:032x}|{self.content:032x}|{self.skeleton}"


def fingerprint(package: str, elements: list[dict], image: Image.Image, device: Device) -> Fingerprint:
    top = image.crop((0, device.content_top_px, device.w_px, TOP_CHROME_BOTTOM_PX))
    content = image.crop((0, device.content_top_px, device.w_px, device.content_bottom_px))
    return Fingerprint(package, dhash(top), dhash(content), skeleton(elements, device))


def same_state(a: Fingerprint, b: Fingerprint) -> bool:
    """Same package, same top chrome, and the same layout or nearly the same pixels (the tree can change
    on a pixel-identical screen). This names new states when recording; arrival is judged by what a screen shows."""
    return (a.package == b.package and hamming(a.top, b.top) <= TOP_BITS
            and (a.skeleton == b.skeleton or hamming(a.content, b.content) <= CONTENT_BITS))


def structure(target: list[dict], now: list[dict], device: Device, dynamic: list[Rect] = ()) -> float:
    """Arrival's second signal, read from the element lists instead of the pixels: the multiset overlap (Jaccard) of
    two screens' texts and classes, each token counted up to 3 times. Numbers are masked, since a count or a time
    doesn't make a screen another screen, and elements in the target's dynamic regions are left out."""
    def tokens(elements: list[dict]) -> Counter:
        kept = [e for e in elements if in_content(e, device)
                and not any(inside(Rect(x=center(rect(e))[0], y=center(rect(e))[1], w=0, h=0), d) for d in dynamic)]
        found = Counter(("text", NUMBER.sub("#", words(e))) for e in kept if words(e))
        found.update(("class", e["type"].split(".")[-1]) for e in kept)
        return Counter({token: min(n, 3) for token, n in found.items()})
    a, b = tokens(target), tokens(now)
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 1.0


# ---------- what the screen shows ----------

def looks_same(then: Image.Image, then_rect: Rect, now: Image.Image, now_rect: Rect, device: Device) -> bool:
    """A recorded control looks now as it did when recorded: its whole crop and a small patch around its tap point
    (a sheet halfway in covers the tap point before the rest) keep their dHash. dHash reads edges, not colors, so a
    selected tab's highlight is still the same tab."""
    def crops(image: Image.Image, r: Rect) -> tuple[Image.Image, Image.Image]:
        (cx, cy), half = center(r), min(PATCH_DP * device.scale, r.w, r.h) / 2
        return (image.crop((int(r.x), int(r.y), int(r.x + r.w), int(r.y + r.h))),
                image.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half))))
    return all(hamming(dhash(a), dhash(b)) <= LOOK_BITS
               for a, b in zip(crops(then, then_rect), crops(now, now_rect), strict=True))


def still(a: Image.Image, b: Image.Image, device: Device) -> bool:
    """Two captures show the same content: no cell of the content area changed. A whole-screen dHash is too coarse
    to see one new line of a reply, so this is changed_boxes' pixel diff, the tool dynamic regions use."""
    return changed_boxes(a, b, device) == []


# ---------- settle ----------

@dataclass
class Settled:
    ok: bool
    seconds: float
    reply: dict
    elements: list[dict]
    list_seconds: list[float]


def settle(list_elements, small_hash, device: Device, clock=time.monotonic, sleep=time.sleep,
           max_s: float = 15.0) -> Settled:
    """Two element lists >= 1.5 s apart with the same skeleton while the list call is fast (< 2 s), then 1 s,
    then two small screenshots within 4 bits. Gives up after max_s and reports unsettled."""
    start, last, took_all = clock(), None, []
    while True:
        at = clock()
        reply, elements = list_elements()
        took = clock() - at
        took_all.append(took)
        shape = skeleton(elements, device)
        if last and shape == last[0] and at - last[1] >= 1.5 and took < 2.0:
            sleep(1.0)
            if hamming(small_hash(), small_hash()) <= 4:
                return Settled(True, clock() - start, reply, elements, took_all)
        if clock() - start >= max_s:
            return Settled(False, clock() - start, reply, elements, took_all)
        last = (shape, at)
        sleep(max(0.0, 1.5 - (clock() - at)))


# ---------- candidates ----------

@dataclass
class Candidate:
    label: str
    kind: str
    rect: Rect
    ref: str | None
    tree_label: str
    ident: str = ""
    enabled: bool = True
    checked: bool | None = None  # a switch's or a chip's on/off; None for a control with no such state

    @property
    def key(self) -> str:
        r = self.rect
        return f"{self.tree_label}|{self.kind}|{int(r.x / 21)},{int(r.y / 21)},{int(r.w / 21)},{int(r.h / 21)}"

    @property
    def point(self) -> tuple[int, int]:
        return center(self.rect)


def short_id(identifier: str | None) -> str:
    return (identifier or "").rsplit("/", 1)[-1]


def controls(elements: list[dict], device: Device) -> list[Candidate]:
    """Tappable-looking elements in the content area. The list is parent-first, so an element's own texts come
    after it: those (not a nested control's label) merge into it, and content that scrolled under an overlay,
    which comes before it, doesn't. A big element without words that holds two or more different texts is a
    layout, not a control. Words without a letter ("8", "1 / 102") are counters, not controls."""
    content = [e for e in elements if in_content(e, device) and area(rect(e)) < LAYOUT_SHARE * content_area(device)
               and rect(e).y + rect(e).h <= device.content_bottom_px + 16]
    found = []
    for n, e in enumerate(content):
        r = rect(e)
        held = {o["text"].strip() for o in content[n + 1:]
                if (o.get("text") or "").strip() and inside(rect(o), r) and area(rect(o)) < area(r)}
        own = words(e)
        if not own and len(held) > 1 and area(r) >= 0.02 * content_area(device):
            continue
        if not own and not held and min(r.w, r.h) < MIN_CONTROL_PX:
            continue
        if own and not LETTER.search(own):
            continue
        tree_label = own or (next(iter(held)) if len(held) == 1 else "")
        ident = short_id(e.get("identifier"))
        # mobile-mcp writes "checked" only when it is true, so a switch without it is off
        checked = True if e.get("checked") else False if TOGGLE.search(e["type"]) else None
        found.append(Candidate(label=tree_label or ident, kind=e["type"].split(".")[-1], rect=r, ref=e["ref"],
                               tree_label=tree_label, ident=ident, enabled=e.get("enabled") is not False,
                               checked=checked))
    kept = [c for c in found if not any(o is not c and area(o.rect) > area(c.rect) and inside(c.rect, o.rect)
                                        for o in found)]
    return [c for n, c in enumerate(kept) if all(o.rect != c.rect for o in kept[:n])]


def tab_bar(cands: list[Candidate], device: Device) -> list[Candidate]:
    """The row of controls lowest on the screen, if at least two of them sit side by side near the bottom."""
    low = [c for c in cands if center(c.rect)[1] >= device.content_bottom_px - TAB_BAND_PX
           and c.rect.w <= 0.6 * device.w_px]
    if not low:
        return []
    floor = max(c.rect.y + c.rect.h for c in low)
    row = sorted((c for c in low if c.rect.y + c.rect.h >= floor - 48), key=lambda c: -area(c.rect))
    tabs = []
    for c in row:
        if not any(overlaps(c.rect, t.rect) for t in tabs):
            tabs.append(c)
    tabs.sort(key=lambda c: c.rect.x)
    spread = tabs[-1].rect.x + tabs[-1].rect.w - tabs[0].rect.x if tabs else 0
    return tabs if len(tabs) >= 2 and spread >= 0.4 * device.w_px else []


def covered(c: Candidate, elements: list[dict], device: Device) -> bool:
    """A tap at c's point lands elsewhere (invariant 3): on a clickable element there that isn't around all of c (the
    deepest clickable view takes a tap, even one inside c), or on an element listed after c's (drawn over it) that is
    neither part of c nor around all of it and shows words, its own or ones it holds: a card, even one whose body is
    no control, or the tab bar. Words count only after c: the list is in drawing order, and content listed before a
    floating button lies under it. mobile-mcp reports no clickable, so on a device a wordless overlay, or one an
    elevation draws over rows listed after it, is unseen; one app lists a wordless empty box over its sheet's main
    button, which still took the tap."""
    at = next((n for n, e in enumerate(elements) if e.get("ref") == c.ref), None)
    if at is None:
        return False
    x, y, after = *c.point, elements[at + 1:]
    over = [e for e in elements if in_content(e, device) and not inside(c.rect, rect(e))
            and inside(Rect(x=x, y=y, w=0, h=0), rect(e))]
    return any(e.get("clickable") for e in over) or any(
        e in after and not inside(rect(e), c.rect)
        and (words(e) or any(words(o) and inside(rect(o), rect(e)) for o in after)) for e in over)


def own_controls(cands: list[Candidate], box: Rect | None, behind: list[Candidate]) -> list[Candidate]:
    """A state's own controls: on a screen all of them; on an overlay in the parent's window, which leaves the
    parent's controls (behind) listed, maybe moved, only the new ones inside its box (a control without words is told
    by its place too)."""
    if box is None:
        return cands
    keys = {c.key for c in behind}
    named = {(c.tree_label, c.kind) for c in behind if c.tree_label and overlaps(c.rect, box)}
    return [c for c in cands if inside(c.rect, box) and c.key not in keys and (c.tree_label, c.kind) not in named]


def worded(elements: list[dict], device: Device) -> list[Candidate]:
    """Every element with words in the content area, each as the list has it: controls() merges a container's texts
    into one control and drops a control nested in a bigger one, so a button drawn inside a row's box is no control of
    its own there. What the deny-list reads at a tap point, and what a wall is read from."""
    return [Candidate(label=words(e), kind=e["type"].split(".")[-1], rect=rect(e), ref=e.get("ref"),
                      tree_label=words(e), ident=short_id(e.get("identifier")))
            for e in elements if words(e) and in_content(e, device)]


def windows(elements: list[dict]) -> list[int]:
    """Each element's window, numbered in list order. The device lists an upper window before the ones under it (an
    app's popup, systemui's clipboard overlay, then the status bar and the activity, measured 2026-10-01), each from
    its root: an activity's action_bar_root, or an android:id/content that isn't right under one."""
    found, n = [], 0
    for k, e in enumerate(elements):
        ident = e.get("identifier") or ""
        under_root = k and (elements[k - 1].get("identifier") or "").endswith(":id/action_bar_root")
        n += bool(k) and (ident.endswith(":id/action_bar_root") or ident == "android:id/content" and not under_root)
        found.append(n)
    return found


def denied_at(target: Candidate, elements: list[dict], device: Device, **deny) -> str:
    """Why a tap on target must not run, or "" for none: the deny-list's hit on an element of the live list over the
    tap point, else something drawn over the target there. The list as it is, not the controls, which merge a
    container's words and drop a button nested in a bigger one, so a sign-in button a card draws inside a row's box is
    read. Within a window the list is in drawing order, parent first. The run it lists right after the target inside
    its box is the target's own, never drawn over it. A wordless target's run is its look (a send button's icon
    labelled "Confirm button") and is not read; in a worded one's, a deny word at the tap point still refuses (a
    "Sign in" button inside a row's box, listed right after it). What it lists after
    that run is over the target: a deny word there refuses the tap, and so does an element that isn't around the
    whole target and shows words, its own or ones listed after it inside it (a card over a row, even one whose words
    ask for nothing). What it lists before the target is content under it, like a reply under a lifted composer's
    Send, unless it absorbs the target (a sheet's "Block character" row, listed before the page it covers, over a
    timestamp) or is the target's ancestor (everything listed between them lies inside it): a paywall card or a
    sign-in sheet labelled with all its text over a wordless call to action. Neither holds over a control that
    dismisses ("Not now" in a "Get Premium" container). An
    upper window's elements are over the target wherever they lie. A pager or list can list another of its pages in
    the same box, in a second container of the same kind (a news feed): the run listed right after it inside its box
    is that page, beside the target. Limits: with no hierarchy, a
    deny-worded sibling listed right after a wordless target inside its box reads as the target's own, a control-shaped
    container listed before the target and around it reads as over it, and a sibling an elevation draws over the
    target while it is listed before it reads as under it; a wordless overlay that holds no words is unseen: no list
    the device gives reports clickable (mobile-mcp's, mobilecli's dump), and uiautomator dump is killed on the emulator
    (measured 2026-10-01)."""
    x, y = target.point
    point = Rect(x=x, y=y, w=0, h=0)
    order = {e.get("ref"): n for n, e in enumerate(elements)}
    at = end = order.get(target.ref, -1)
    while 0 <= at and end + 1 < len(elements) and inside(rect(elements[end + 1]), target.rect):
        end += 1
    window = windows(elements)
    def place(e: dict) -> tuple:
        return *e["coordinates"].values(), e.get("type")
    mine = {place(e) for e in elements[:max(at, 0)] if PAGED.search(e.get("type", "")) and inside(target.rect, rect(e))}
    beside = set()
    for k in (k for k in range(end + 1, len(elements)) if place(elements[k]) in mine):
        run = next((j for j in range(k + 1, len(elements)) if not inside(rect(elements[j]), rect(elements[k]))),
                   len(elements))
        beside.update(range(k + 1, run))
    held = [n for n, e in enumerate(elements) if in_content(e, device) and inside(point, rect(e)) and n not in beside]
    over = [n for n in held if at < 0 or window[n] < window[at] or n > end and window[n] == window[at]]
    dismisses = DISMISS.match(target.label.strip()) or ICON_ONLY.fullmatch(target.tree_label) and DISMISS_ID.search(
        ID_WORDS.sub(" ", target.ident or target.label))
    absorbing = [n for n in held if 0 <= n < at and window[n] == window[at] and not dismisses and (
        absorbs(elements[n], elements[at])
        or all(inside(r, rect(elements[n])) for r in [target.rect, *map(rect, elements[n + 1:at])]))]
    own = [n for n in held if at < n <= end and words(elements[at])]
    for n in sorted(over + absorbing + own):
        reason = words(elements[n]) and denied(worded([elements[n]], device)[0], **deny)
        if reason:
            return f"{reason} ({words(elements[n])[:40]!r} at the tap point)"
    for n in over if at >= 0 else []:
        e = elements[n]
        said = [words(o) for k, o in enumerate(elements[n + 1:], n + 1)
                if window[k] == window[n] and words(o) and inside(rect(o), rect(e))]
        if (window[n] < window[at] or not inside(target.rect, rect(e))) and (words(e) or said):
            return f"drawn over ({(words(e) or said[0])[:40]!r} at the tap point)"
    return ""


def new_words(then: list[dict], now: list[dict], device: Device) -> list[Candidate]:
    """The worded elements now shows that then didn't show in the same place (same words and class, overlapping)."""
    shown = worded(then, device)
    return [c for c in worded(now, device)
            if not any(h.label == c.label and h.kind == c.kind and overlaps(h.rect, c.rect) for h in shown)]


def other_controls(then: list[Candidate], now: list[Candidate]) -> bool:
    """Whether now lacks a control then has or shows one it doesn't, by find()'s identity: a feed reloaded with the
    same card shapes and new titles keeps its fingerprint all the same."""
    then = [c for c in then if c.ref is not None]
    return any(find(now, c) is None for c in then) or any(find(then, c) is None for c in now)


def find(cands: list[Candidate], want: Candidate) -> Candidate | None:
    """Re-finds a control on a fresh list: by its words when it has some (bars recenter, so never by a saved
    coordinate), else by class and size near the same spot."""
    if want.ref is None:
        return want
    if want.kind == "EditText":
        same = [c for c in cands if c.kind == "EditText"]
    elif want.ident and not want.tree_label:
        same = [c for c in cands if c.ident == want.ident and c.kind == want.kind]
    elif want.tree_label:
        same = [c for c in cands if c.tree_label == want.tree_label and c.kind == want.kind]
    else:
        same = [c for c in cands if not c.tree_label and c.kind == want.kind
                and abs(c.rect.w - want.rect.w) <= 21 and abs(c.rect.h - want.rect.h) <= 21
                and abs(center(c.rect)[0] - want.point[0]) <= 64 and abs(center(c.rect)[1] - want.point[1]) <= 64]
    wx, wy = want.point
    return min(same, key=lambda c: abs(center(c.rect)[0] - wx) + abs(center(c.rect)[1] - wy), default=None)


def control_shaped(label: str, kind: str) -> bool:
    """A control's label rather than content: a few words, or a button's."""
    return len(label.split()) <= CONTROL_WORDS or "Button" in kind


def denied(c: Candidate, upsell: bool = False, core: bool = False, toggle_ok: bool = False,
           account: bool = False) -> str | None:
    """The deny-list word that blocks this tap, or None. On an upsell screen its call-to-action words are
    denied too. A control that shows the account's own name or email is never tapped: a tap can copy it where no
    redaction reaches, like the keyboard's clipboard chip. Sending and typing belong to the core-loop pass only. A
    switch or checkbox could undo the content filter, so only the filter's own row may flip one (toggle_ok). On that
    row a filter phrase ("Hide NSFW", "Block explicit content") is no deny hit; every other deny word still is. On
    the sign-up path (account) the words that sign up by email are no deny hit and a text box may be typed into,
    unless the control names a sign-in with another account or a phone; a consent and every other deny word still
    are."""
    text = "\n".join(dict.fromkeys(t for t in (c.label, c.tree_label) if t))
    if REDACTED in text:
        return "account text"
    if account and consents(c):
        return "consent"
    text = ID_WORDS.sub(" ", text)
    shaped = control_shaped(c.label, c.kind)
    rest = FILTER_PHRASE.sub(" ", text) if toggle_ok else text
    if ICON_ONLY.fullmatch(c.tree_label) and DISMISS_ID.search(ID_WORDS.sub(" ", c.ident or c.label)):
        text = rest = SIGN_IN.sub(" ", text)
    if account and not (OTHER_ACCOUNT.search(text) or PHONE.search(text)):
        text = rest = SIGN_UP.sub(" ", text)
    hit = (DENY_ALWAYS if shaped else DENY_COMMAND).search(text) or (DENY.search(rest) if shaped else None) \
        or (DENY_ON_UPSELL.search(text) if upsell else None) or (DENY_IN_CORE if core else DENY_IN_TOUR).search(text)
    if hit:
        return hit.group(hit.lastindex or 0).lower()  # a command's word, without the bullet before it
    if TOGGLE.search(c.kind) and not toggle_ok:
        return "toggle"
    if c.kind == "EditText" and not (core or account):
        return "text input"
    return None


def shaped(c: Candidate) -> bool:
    """A control's label, not a sentence: a few words, or a button. A picture the icon pass named is no control: its
    name describes it ("Sign in illustration")."""
    return c.kind != "picture" and (len(c.label.split()) <= CONTROL_WORDS or "Button" in c.kind)


def consents(c: Candidate, tapped: bool = True) -> bool:
    """A control that agrees to, accepts or attests something: "I agree", "Accept & continue", "I'm 18+", a consent
    checkbox. One that is not to be tapped and is neither a button nor a toggle only when it agrees or accepts."""
    toggle = bool(TOGGLE.search(c.kind))
    attests = (tapped or toggle or "Button" in c.kind) and ATTESTS.search(c.label)
    return (shaped(c) or toggle) and bool(AGREES.search(c.label) or attests)


def account_way(c: Candidate) -> bool:
    """A control on the way in or past an account wall: sign in or up, email, create an account, go on as a guest."""
    return any(rule.search(c.label) for rule in (SIGN_IN, EMAIL_WAY, SIGN_UP_WAY, GUEST))


def field_kind(e: dict, elements: list[dict]) -> str:
    """What a text box asks for ("email", "password", "phone", "name", or ""), from its own words and id and its
    caption: the closest text above it, unless another text box sits between, which owns that text."""
    r = rect(e)

    def above(o: dict) -> bool:
        t = rect(o)
        return t.y + t.h <= r.y + 8 and t.x < r.x + r.w and r.x < t.x + t.w
    nearest = max((o for o in elements if (words(o) or o["type"].endswith("EditText")) and above(o)),
                  key=lambda o: rect(o).y + rect(o).h, default=None)
    caption = words(nearest) if nearest and not nearest["type"].endswith("EditText") else ""
    said = [words(e), ID_WORDS.sub(" ", short_id(e.get("identifier"))).strip(), caption]
    return next((kind for kind, rule in FIELDS.items() for text in said if text and rule.search(text)), "")


def walled(cands: list[Candidate]) -> str:
    """What a control's own words ask for, read the way denied() reads them (anywhere in a control-shaped label, at
    the start of a clause in a longer one, so a chat's opening message stays content): "account" (sign in, log in,
    create account, password) or "money" (subscribe, buy, pay, check out); "" for neither. Words that undo something
    (delete, cancel, unsubscribe) are neither."""
    for c in cands:
        shaped = control_shaped(c.label, c.kind)
        for m in (DENY_ALWAYS if shaped else DENY_COMMAND).finditer(c.label):
            word = m.group(m.lastindex or 0)
            if not UNDOING.fullmatch(word):
                return "account" if ACCOUNT.search(word) else "money"
    return ""


def anr(elements: list[dict]) -> bool:
    """Android's own "isn't responding" dialog, in any language: its buttons carry android:id/aerr_* ids. A reply or
    headline that says "not responding" is not one."""
    return any((e.get("identifier") or "").startswith("android:id/aerr_") for e in elements)


def dismiss_control(cands: list[Candidate]) -> Candidate | None:
    return next((c for c in cands if DISMISS.match(c.label.strip()) and not denied(c)), None)


# ---------- modals ----------

def dialog_box(cands: list[Candidate], device: Device) -> Rect | None:
    """A dialog window: every control fits in one box clearly smaller than the content area, below the top
    chrome (a sparse screen still has its header up there)."""
    if not cands:
        return None
    box = bbox([c.rect for c in cands])
    small = area(box) < 0.6 * content_area(device) or box.w < 0.8 * device.w_px
    return box if small and box.y - device.content_top_px >= 48 * device.scale else None


def box_kind(box: Rect, device: Device) -> str:
    """A sheet reaches the bottom; its last control sits up to 48 dp above the edge, over the sheet's padding."""
    return "sheet" if box.y + box.h >= device.content_bottom_px - 48 * device.scale else "modal"


def scrim(then: Image.Image, now: Image.Image, box: Rect, device: Device) -> bool:
    """The content above the box was blended with one color: the parent a tall sheet opens over. A blend keeps the
    strip's picture (the two captures correlate) and scales its contrast by what the scrim leaves, whatever its color:
    black darkens a light app, and Material 2's scrim on a dark theme is white and lightens it."""
    top, bottom = device.content_top_px, int(box.y)
    if bottom - top < MIN_CONTROL_PX:
        return False
    a = np.asarray(then.convert("L"), dtype=float)[top:bottom].ravel()
    b = np.asarray(now.convert("L"), dtype=float)[top:bottom].ravel()
    # ponytail: a strip of one flat color has no picture to keep, so a scrim over it isn't seen; the size rule decides
    if a.std() < 1 or b.std() == 0:
        return False
    return bool(np.corrcoef(a, b)[0, 1] > 0.9 and b.std() < SCRIM_SHARE * a.std())


def overlay_box(before: list[Candidate], after: list[Candidate], device: Device,
                chrome: frozenset[str] = frozenset(), dimmed=lambda box: False) -> Rect | None:
    """An overlay in the same window: the new controls form one box smaller than the content area, or any box
    over a scrim (dimmed(box)), and the old controls under that box are still listed (a feed that changed would
    have replaced them). Chrome that content scrolls under, like the tab bar, is no evidence either way."""
    old = {c.key for c in before}
    new = [c for c in after if c.key not in old]
    if len(new) < 2:
        return None
    box = bbox([c.rect for c in new])
    if area(box) >= OVERLAY_SHARE * content_area(device) and not dimmed(box):
        return None
    now = {c.key for c in after}
    under = [c for c in before if overlaps(c.rect, box) and c.key not in chrome]
    return box if under and sum(c.key in now for c in under) >= len(under) / 2 else None


# ---------- diffs between captures ----------

def changed_boxes(a: Image.Image, b: Image.Image, device: Device, cell: int = 40) -> list[Rect] | None:
    """Device-px boxes whose pixels differ between two captures of one state. None when most of the content
    changed: that is a scroll or a new feed, not a dynamic region."""
    top, bottom = device.content_top_px, device.content_bottom_px
    ga = np.asarray(a.convert("L"), dtype=np.int16)[top:bottom]
    gb = np.asarray(b.convert("L"), dtype=np.int16)[top:bottom]
    if ga.shape != gb.shape:
        return None
    rows, cols = ga.shape[0] // cell, ga.shape[1] // cell
    moved = (np.abs(ga - gb) > 24)[:rows * cell, :cols * cell].reshape(rows, cell, cols, cell).mean(axis=(1, 3))
    cells = moved > 0.02
    if cells.mean() > 0.5:
        return None
    boxes = []
    for region in regionprops(label(cells)):
        r0, c0, r1, c1 = region.bbox
        boxes.append(Rect(x=c0 * cell, y=top + r0 * cell, w=(c1 - c0) * cell, h=(r1 - r0) * cell))
    return boxes


def shifted(before: list[dict], after: list[dict], device: Device) -> bool:
    """Some element both lists show (the same type, words, left edge and size) sits at another height: a scroll moves
    the content, while an animation, a video or an ad redrawn in place leaves every element where it was."""
    def spots(elements: list[dict]) -> dict[tuple, set[float]]:
        out: dict[tuple, set[float]] = {}
        for e in elements:
            if in_content(e, device):
                r = rect(e)
                out.setdefault((e["type"], words(e), r.x, r.w, r.h), set()).add(r.y)
        return out
    a, b = spots(before), spots(after)
    # ponytail: identical elements (same type, size, no words) scrolled by exactly their spacing look unmoved; words
    # elsewhere on the page catch it, a page of nothing but identical pictures would not
    return any(a[k] != b[k] for k in a.keys() & b.keys())


def counters(before: list[dict], after: list[dict], device: Device, bands: list[tuple[int, int]]) -> list[str]:
    """Short numbers that changed in place ("5 left" → "4 left") inside the given y bands (the header, the input
    bar): UI chrome, never the content that scrolls between them."""
    def at(elements):
        return {(bucket(e["coordinates"]["x"], device), bucket(e["coordinates"]["y"], device)): words(e)
                for e in elements if in_content(e, device) and words(e) and len(words(e)) <= 30
                and not CLOCK.search(words(e)) and any(y0 <= e["coordinates"]["y"] < y1 for y0, y1 in bands)}
    old, new = at(before), at(after)
    return [f"{old[p]} → {new[p]}" for p in old.keys() & new.keys()
            if old[p] != new[p] and DIGITS.search(old[p]) and DIGITS.search(new[p])]


def change_summary(before: list[dict], after: list[dict], device: Device, limit: int = 160) -> str:
    """What changed in the list: counters first ("3 → 2"), then texts that appeared or went away."""
    def texts(elements):
        return {(bucket(e["coordinates"]["x"], device), bucket(e["coordinates"]["y"], device)): words(e)
                for e in elements if in_content(e, device) and words(e)}
    old, new = texts(before), texts(after)
    counters = [f"{old[p]} → {new[p]}" for p in old.keys() & new.keys()
                if old[p] != new[p] and DIGITS.search(old[p]) and DIGITS.search(new[p])]
    added = sorted(set(new.values()) - set(old.values()) - {new[p] for p in old.keys() & new.keys()})
    removed = sorted(set(old.values()) - set(new.values()) - {old[p] for p in old.keys() & new.keys()})
    parts = counters + [f"+{t[:40]!r}" for t in added[:3]] + [f"-{t[:40]!r}" for t in removed[:3]]
    return "; ".join(parts)[:limit]


def texts(elements: list[dict], device: Device) -> set[str]:
    return {words(e) for e in elements if in_content(e, device) and words(e)}


def shows_text(elements: list[dict], text: str, device: Device) -> bool:
    """Text the model read off a screenshot is in the element list too: a free check that it read this screen."""
    want = " ".join(text.split()).casefold()
    return bool(want) and any(want in " ".join(t.split()).casefold() for t in texts(elements, device))


def read_on_screen(elements: list[dict], reading: str, title: str, device: Device) -> bool:
    """The target's identifying text, as the model read it, is on the screen now. An empty reading is the model not
    finding it, so the target's own title must show instead; only a target with no text title (a logo) has none."""
    wanted = reading.strip() or title.strip()
    return not wanted or shows_text(elements, wanted, device)


def is_upsell(elements: list[dict], device: Device) -> bool:
    return any(PAYWALL.search(t) for t in texts(elements, device))


def priced(elements: list[dict], device: Device) -> bool:
    """Shows a price ($4.99, 9,99 €, ₹199, USD 4.99): what makes an upsell a paywall, not a teaser."""
    return any(PRICE.search(t) for t in texts(elements, device))


# ---------- the core loop ----------

def composer(cands: list[Candidate], device: Device) -> tuple[Candidate, Candidate] | None:
    """A chat: a text box in the lower half of the screen plus its send control in the composer bar, the box's
    row or the toolbar row right under it: one that says "send", or else the first past the box's right edge. A
    text box near the top is a search; a "send" elsewhere ("Send feedback", "Send gift") is not this box's."""
    middle = (device.content_top_px + device.content_bottom_px) / 2
    box = next((c for c in cands if c.kind == "EditText" and center(c.rect)[1] > middle), None)
    if box is None:
        return None
    bar = [c for c in cands if c is not box and c.rect.y < box.rect.y + 2 * box.rect.h
           and box.rect.y < c.rect.y + c.rect.h and not denied(c, core=True)]
    send = next((c for c in bar if re.search(r"send", c.label, re.IGNORECASE)), None) or min(
        (c for c in bar if c.rect.x >= box.rect.x + box.rect.w - 24), key=lambda c: c.rect.x, default=None)
    return (box, send) if send else None


def feed_items(cands: list[Candidate], device: Device, exclude: set[str] = frozenset()) -> list[Candidate]:
    """The biggest group of controls with words, each at least 2% of the screen, that share a resource id or a
    class and width: the cards of a feed or a list."""
    big = [c for c in cands if c.tree_label and c.key not in exclude and not denied(c)
           and area(c.rect) >= 0.02 * content_area(device)]
    keys = (lambda c: c.ident, lambda c: (c.kind, bucket(c.rect.w, device)))
    groups = [[c for c in big if key(c) == value] for key in keys for value in dict.fromkeys(map(key, big)) if value]
    best = max(groups, key=len, default=[])
    return best if len(best) >= 2 else []


def short_buttons(cands: list[Candidate], device: Device, exclude: set[str] = frozenset(),
                  title: str = "") -> list[Candidate]:
    """Controls with a short label that aren't feed cards or text boxes, the biggest first: where an app puts its
    own word for what it is for ("Start lesson", "Convert", "Generate"), in any language. The screen's title and a
    label with a number in it (a counter, a time) are not actions."""
    feed = {c.key for c in feed_items(cands, device, exclude)}
    return sorted((c for c in cands if c.tree_label and len(c.label.split()) <= 3 and c.kind != "EditText"
                   and c.label != title and not DIGITS.search(c.label) and c.key not in exclude
                   and c.key not in feed and not denied(c)), key=lambda c: -area(c.rect))


def reply_timing(samples: list[tuple[float, frozenset[str]]]) -> tuple[float | None, float | None, int]:
    """From (seconds since the action, new texts on screen) samples: when new text first appeared, when it
    last changed, and how many characters it ended with."""
    started = next((t for t, new in samples if new), None)
    finished, last = None, None
    for t, new in samples:
        if new != last:
            finished, last = t, new
    if started is None:
        return None, None, 0
    return started, finished, sum(len(t) for t in last)


def timing_line(verb: str, samples: list[tuple[float, frozenset[str]]], waited: float) -> str:
    started, finished, chars = reply_timing(samples)
    if started is None:
        return f"no {verb} within {waited:.0f} s"
    return f"{verb} started {started:.1f} s, finished {finished:.1f} s, {chars} chars"
