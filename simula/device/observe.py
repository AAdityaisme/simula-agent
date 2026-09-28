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

# Entry words (upgrade, plans, premium, plus, try) may open an upsell; confirm words never run.
DENY = re.compile(r"log ?out|sign ?out|delete|remove|cancel|subscribe|buy|pay|purchase|restore|confirm|"
                  r"start\b.{0,24}\btrial|report|block|clear|email|password|security|persona|follow|favorit|like|"
                  r"heart|hide|terms|privacy|continue with|rate us|review|camera|photo|gallery|allow|permission|"
                  r"install|open in", re.IGNORECASE)
DENY_ON_UPSELL = re.compile(r"continue|try|start|get|claim|unlock|join|redeem|activate", re.IGNORECASE)
DENY_IN_TOUR = re.compile(r"send|swipe|regenerate", re.IGNORECASE)
DISMISS = re.compile(r"^(close\b.*|not now|later|maybe later|no,? thanks|skip|dismiss|got it|x|×|✕)$", re.IGNORECASE)
ANR = re.compile(r"isn.t responding|not responding", re.IGNORECASE)
BLOCKING = re.compile(r"emulator|rooted|captcha|verify (that )?you.?re (a )?human|age verification|"
                      r"date of birth|not supported on this device", re.IGNORECASE)
PAYWALL = re.compile(r"[$€£]\s?\d|subscription|membership|free trial|per (month|week|year)|/ ?(month|week|year|mo)\b",
                     re.IGNORECASE)
PRIMARY = re.compile(r"\b(chat|message|talk|start|begin)\b", re.IGNORECASE)
CREATE = re.compile(r"\W*(generate|play|draw|spin|roll|scan)\b", re.IGNORECASE)
ENTRY = re.compile(r"upgrade|\bplans?\b|premium|\bplus\b|\bpro\b|\+|membership|subscription", re.IGNORECASE)
LIMIT = re.compile(r"\blimits?\b|\bremaining\b|\bquota\b|resets? in|out of (free )?(messages|credits|swipes|chats|"
                   r"articles)|no more (free )?\w+|\bleft today\b", re.IGNORECASE)
DIGITS = re.compile(r"\d")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b|\bago\b", re.IGNORECASE)
LETTER = re.compile(r"[^\W\d_]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
REDACTED = "[redacted]"


# ---------- geometry ----------

def rect(e: dict) -> Rect:
    c = e["coordinates"]
    return Rect(x=c["x"], y=c["y"], w=c["width"], h=c["height"])


def area(r: Rect) -> float:
    return r.w * r.h


def inside(inner: Rect, outer: Rect) -> bool:
    return (outer.x <= inner.x and outer.y <= inner.y
            and inner.x + inner.w <= outer.x + outer.w and inner.y + inner.h <= outer.y + outer.h)


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

def redact(reply: dict, image: Image.Image, secrets: list[str]) -> tuple[dict, list[dict], int]:
    """Replaces every listed string (any case) and every email address in the element list with [redacted] and
    paints a solid box over those elements in the image, before anything reads or saves them. Also returns how
    many elements were redacted."""
    listed = [re.escape(s.strip()) for s in secrets if s.strip()]
    pattern = re.compile("|".join([EMAIL.pattern, *listed]), re.IGNORECASE)
    elements = json.loads(reply["content"][0]["text"].removeprefix(ELEMENTS_PREFIX))
    draw = ImageDraw.Draw(image)
    hits = 0
    for e in elements:
        hit = False
        for key in ("text", "label", "identifier"):
            if e.get(key) and pattern.search(e[key]):
                e[key], hit = pattern.sub(REDACTED, e[key]), True
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
    on a pixel-identical screen)."""
    return (a.package == b.package and hamming(a.top, b.top) <= TOP_BITS
            and (a.skeleton == b.skeleton or hamming(a.content, b.content) <= CONTENT_BITS))


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
        found.append(Candidate(label=tree_label or ident, kind=e["type"].split(".")[-1], rect=r, ref=e["ref"],
                               tree_label=tree_label, ident=ident, enabled=e.get("enabled") is not False))
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


def denied(c: Candidate, upsell: bool = False, core: bool = False) -> str | None:
    """The deny-list word that blocks this tap, or None. On an upsell screen its call-to-action words are
    denied too. Sending and typing belong to the core-loop pass only."""
    text = "\n".join(dict.fromkeys(t for t in (c.label, c.tree_label) if t))
    hit = DENY.search(text) or (DENY_ON_UPSELL.search(text) if upsell else None) \
        or (None if core else DENY_IN_TOUR.search(text))
    if hit:
        return hit.group(0).lower()
    if c.kind == "EditText" and not core:
        return "text input"
    return None


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
    return "sheet" if box.y + box.h >= device.content_bottom_px - 48 else "modal"


def overlay_box(before: list[Candidate], after: list[Candidate], device: Device,
                chrome: frozenset[str] = frozenset()) -> Rect | None:
    """An overlay in the same window: the new controls form one box smaller than the content area, and the old
    controls under that box are still listed (a feed that changed would have replaced them). Chrome that
    content scrolls under, like the tab bar, is no evidence either way."""
    old = {c.key for c in before}
    new = [c for c in after if c.key not in old]
    if len(new) < 2:
        return None
    box = bbox([c.rect for c in new])
    if area(box) >= 0.9 * content_area(device):
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


def is_upsell(elements: list[dict], device: Device) -> bool:
    return any(PAYWALL.search(t) for t in texts(elements, device))


# ---------- the core loop ----------

def composer(cands: list[Candidate], device: Device) -> tuple[Candidate, Candidate] | None:
    """A chat: a text box in the lower half of the screen plus its send control, which says "send" or sits just
    past the box's right edge. A text box near the top is a search."""
    middle = (device.content_top_px + device.content_bottom_px) / 2
    box = next((c for c in cands if c.kind == "EditText" and center(c.rect)[1] > middle), None)
    if box is None:
        return None
    send = next((c for c in cands if re.search(r"send", c.label, re.IGNORECASE)), None)
    right = [c for c in cands if c.rect.x >= box.rect.x + box.rect.w - 24 and c.rect.y < box.rect.y + box.rect.h
             and box.rect.y < c.rect.y + c.rect.h and not denied(c, core=True)]
    send = send or min(right, key=lambda c: c.rect.x, default=None)
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
