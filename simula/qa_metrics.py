"""QA's measurements: the pixel compare in content dp (masked SSIM and its heatmap, and pixelmatch beside it), the
identity render, and the DOM bounds of every data-el."""

import itertools
import math
import re
import tempfile
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity
from skimage.morphology import dilation

from simula import pixelmatch
from simula.contracts import Device, ProductModel, Rect
from simula.render import content_dp, open_mock, screenshot

WINDOW = 7
HALO = WINDOW // 2
MIN_COVERAGE = 0.3
BOUNDS_TOLERANCE_DP = 4.0
IDENTITY_GATE = 0.985
PIXELMATCH_THRESHOLD = 0.1

# On the section shown now, in the section's coordinates (content dp): each visible data-el's box, every copied image
# it draws (an <img> or a CSS background from assets/) with the box it is drawn in, the box each data-chrome part
# covers (all its visible tags), and the text each visible data-value tag shows.
SCREEN_DOM = r"""(id) => {
  const section = document.querySelector(`[data-screen="${CSS.escape(id)}"]`);
  if (!section) return {boxes: {}, images: [], chrome: {}, values: []};
  const origin = section.getBoundingClientRect();
  const local = r => ({x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height});
  const shown = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility({opacityProperty: true, visibilityProperty: true});
  };
  const boxes = {};
  for (const el of section.querySelectorAll('[data-el]'))
    if (shown(el) && !(el.dataset.el in boxes)) boxes[el.dataset.el] = local(el.getBoundingClientRect());
  const images = [];
  for (const el of section.querySelectorAll('*')) {
    const src = el.tagName === 'IMG' ? el.getAttribute('src') ?? ''
      : getComputedStyle(el).backgroundImage.match(/assets\/[^\/"')]+\.png/)?.[0] ?? '';
    if (/^assets\/[^\/]+\.png$/.test(src) && shown(el)) images.push({src, ...local(el.getBoundingClientRect())});
  }
  const chrome = {};
  for (const el of section.querySelectorAll('[data-chrome]')) {
    if (!shown(el)) continue;
    const r = local(el.getBoundingClientRect()), c = chrome[el.dataset.chrome];
    chrome[el.dataset.chrome] = !c ? r : {x: Math.min(c.x, r.x), y: Math.min(c.y, r.y),
      w: Math.max(c.x + c.w, r.x + r.w) - Math.min(c.x, r.x), h: Math.max(c.y + c.h, r.y + r.h) - Math.min(c.y, r.y)};
  }
  const values = [...section.querySelectorAll('[data-value]')].filter(shown)
    .map(el => ({id: el.dataset.value, text: el.innerText}));
  return {boxes, images, chrome, values};
}"""


class ScreenDom(NamedTuple):
    """What QA reads off one drawn screen, in its section's content dp: each data-el's box, each copied image with the
    box it is drawn in, each data-chrome part's box, and each data-value tag's id and text."""
    boxes: dict[str, Rect]
    images: list[tuple[str, Rect]]
    chrome: dict[str, Rect]
    values: list[tuple[str, str]]


def device_to_dp(rect: Rect, device: Device) -> Rect:
    """A device-px rect (like dynamic_regions) in content dp."""
    return Rect(x=rect.x / device.scale, y=(rect.y - device.content_top_px) / device.scale,
                w=rect.w / device.scale, h=rect.h / device.scale)


def unmasked(boxes: list[Rect], shape: tuple[int, int]) -> np.ndarray:
    """True where no masked box (grown by the halo) reaches. A window centered outside the halo never sees a masked
    pixel, so copied art can't lift or sink its neighbours' scores."""
    h, w = shape
    keep = np.ones(shape, bool)
    for r in boxes:
        x0, y0 = max(math.floor(r.x) - HALO, 0), max(math.floor(r.y) - HALO, 0)
        x1, y1 = min(math.ceil(r.x + r.w) + HALO, w), min(math.ceil(r.y + r.h) + HALO, h)
        keep[y0:y1, x0:x1] = False
    return keep


def compare(real: Image.Image, mock: Image.Image, masked: list[Rect]) -> dict:
    """Masked SSIM of a mock screen vs its real screen, both brought to content dp. Returns ssim (None when less
    than MIN_COVERAGE of the screen is left to score), coverage, the per-pixel map for the heatmap, and both screens
    as content-dp arrays, so no caller converts them again."""
    a, b = np.asarray(content_dp(real)), np.asarray(content_dp(mock))
    _, full = structural_similarity(a, b, data_range=255, channel_axis=-1, full=True, win_size=WINDOW)
    ssim_map = full.mean(axis=-1)
    keep = unmasked(masked, ssim_map.shape)
    coverage = float(keep.mean())
    # skimage's own mean skips the half-window border; skipping it too keeps an unmasked score equal to skimage's.
    scored = keep.copy()
    scored[:HALO], scored[-HALO:], scored[:, :HALO], scored[:, -HALO:] = False, False, False, False
    ssim = float(ssim_map[scored].mean()) if coverage >= MIN_COVERAGE and scored.any() else None
    return {"ssim": ssim, "coverage": coverage, "map": ssim_map, "keep": keep, "real": a, "mock": b}


def pixel_diff(real: np.ndarray, mock: np.ndarray, keep: np.ndarray) -> float | None:
    """The second pixel metric, pixelmatch (threshold 0.1, anti-aliased pixels not counted), on the content-dp arrays
    compare returns: the share of the unmasked pixels (keep, from compare) that differ. None when too little of the
    screen is left to score, as for SSIM. Reported beside SSIM and never scored: a second view that reads as a plain
    share of changed pixels."""
    if keep.mean() < MIN_COVERAGE:
        return None
    return float(pixelmatch.differing(real, mock, PIXELMATCH_THRESHOLD)[keep].mean())


def heatmap(real: np.ndarray, ssim_map: np.ndarray, keep: np.ndarray) -> Image.Image:
    """The real screen (the content-dp array compare returns) dimmed to gray, red where the mock differs (1 - SSIM),
    blue over masked pixels."""
    gray = np.asarray(Image.fromarray(real).convert("L"), float)[..., None] * 0.45
    base = np.repeat(gray, 3, axis=-1)
    bad = np.clip(1 - ssim_map, 0, 1)[..., None]
    out = base * (1 - bad) + np.array([255.0, 0, 0]) * bad
    out[~keep] = out[~keep] * 0.5 + np.array([0, 40.0, 140])
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def identity_render(real: Image.Image, device: Device = Device()) -> Image.Image:
    """Draws a real screen as one full-bleed <img> inside the content insets and renders it the way QA renders a mock.
    Scoring it against the real screen measures what rendering and resampling alone cost."""
    content_h = device.content_bottom_px - device.content_top_px
    if real.size != (device.w_px, content_h):
        real = real.crop((0, device.content_top_px, device.w_px, device.content_bottom_px))
    with tempfile.TemporaryDirectory() as tmp:
        real.convert("RGB").save(Path(tmp) / "real.png")
        box = (f"left:0;top:{device.content_top_px / device.scale}px;width:{device.w_px / device.scale}px;"
               f"height:{content_h / device.scale}px")
        (Path(tmp) / "index.html").write_text(
            f'<!doctype html><html><body style="margin:0;background:#000">'
            f'<img src="real.png" style="position:absolute;display:block;{box}"></body></html>')
        with open_mock(Path(tmp)) as (page, _):
            screenshot(page, path=Path(tmp) / "render.png")
        return Image.open(Path(tmp) / "render.png").copy()


def screen_dom(page, screen: str) -> ScreenDom:
    """Everything QA reads off the DOM of the screen on show, in one evaluate."""
    dom = page.evaluate(SCREEN_DOM, screen)
    return ScreenDom(boxes={eid: Rect(**box) for eid, box in dom["boxes"].items()},
                     images=[(i.pop("src"), Rect(**i)) for i in dom["images"]],
                     chrome={kind: Rect(**box) for kind, box in dom["chrome"].items()},
                     values=[(v["id"], v["text"]) for v in dom["values"]])


def overlap(a: Rect, b: Rect) -> Rect | None:
    x0, y0, x1, y1 = max(a.x, b.x), max(a.y, b.y), min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    return Rect(x=x0, y=y0, w=x1 - x0, h=y1 - y0) if x1 > x0 and y1 > y0 else None


def within(got: Rect | None, want: Rect, tolerance: float = BOUNDS_TOLERANCE_DP) -> bool:
    return got is not None and max(abs(got.x - want.x), abs(got.y - want.y),
                                   abs(got.w - want.w), abs(got.h - want.h)) <= tolerance


# ---------- cross-screen check: shared chrome and values ----------

CHROME_GATE = 0.98
# Two screens show the same part at a box when their real screens draw most of it alike (see alike); the rest is
# what the app itself changes, a highlighted tab or a title, however large. Measured on the three test apps' own
# screens: a tab bar two screens share leaves 0.69-1.0 of its box, a header with another title 0.63-0.87 (0.57 on the
# real JanitorAI run), a box over unrelated content 0.34 or less. Two different bars on a plain background land in
# between (0.45-0.68); above the mark they are compared only over that background.
SAME_PART = 0.5
# content_dp's Lanczos filter reaches 3 dp across an edge, so a box's outer rows mix in whatever is drawn beside it.
# A chrome box is compared this far inside its edges.
BLEED_DP = 3


def box_slices(box: Rect, shape: tuple, inset: float = 0) -> tuple[slice, slice] | None:
    """The rows and columns a box covers in a content-dp image, inset on every side; None when that is smaller than
    the SSIM window."""
    h, w = shape[:2]
    x0, y0 = max(math.floor(box.x + inset), 0), max(math.floor(box.y + inset), 0)
    x1, y1 = min(math.ceil(box.x + box.w - inset), w), min(math.ceil(box.y + box.h - inset), h)
    return (slice(y0, y1), slice(x0, x1)) if min(x1 - x0, y1 - y0) >= WINDOW else None


def box_map(a: np.ndarray, b: np.ndarray, box: Rect) -> np.ndarray | None:
    """The per-pixel SSIM map (mean over channels) of the same box cut from two content-dp images, BLEED_DP inside
    its edges; None when that is smaller than the SSIM window."""
    cut = box_slices(box, a.shape, BLEED_DP)
    if cut is None:
        return None
    _, full = structural_similarity(a[cut], b[cut], data_range=255, channel_axis=-1, full=True, win_size=WINDOW)
    return full.mean(axis=-1)


def alike(a: np.ndarray, b: np.ndarray, box: Rect) -> np.ndarray | None:
    """Where two content-dp images draw a box alike (per-pixel SSIM at least CHROME_GATE), leaving out every pixel
    within BOUNDS_TOLERANCE_DP of one they draw differently: QA's placement tolerance, so a title or an icon the mock
    draws a few dp off never reads as the part changing. None when the box is smaller than the SSIM window."""
    ssim_map = box_map(a, b, box)
    if ssim_map is None:
        return None
    reach = 2 * math.ceil(BOUNDS_TOLERANCE_DP) + 1
    return ~dilation(ssim_map < CHROME_GATE, np.ones((reach, reach), bool))


def chrome_failures(chrome: dict[str, dict[str, Rect]], mocks: dict[str, Image.Image],
                    reals: dict[str, Image.Image]) -> list[dict]:
    """Each data-chrome part (a header, a tab bar) must render the same on two screens wherever their real screens
    draw it the same: over those pixels the mock pair must score at least CHROME_GATE. What the real screens draw
    differently there (a highlighted tab, a title) is left out, however large, so the mock may differ there too. A bar
    drawn in another place fails, and so does one left off a screen. chrome maps every drawn screen, in the mock's
    order, to its parts' boxes (none when it marks none); mocks and reals are content-dp images."""
    kinds = dict.fromkeys(kind for parts in chrome.values() for kind in parts)
    if not kinds:
        return []
    mock = {sid: np.asarray(image.convert("RGB")) for sid, image in mocks.items()}
    real = {sid: np.asarray(image.convert("RGB")) for sid, image in reals.items()}
    return [failure for kind in kinds for failure in part_failures(kind, chrome, mock, real)]


class PairScore(NamedTuple):
    """Two screens' part, over the pixels their real screens draw alike."""
    mock: float  # the mock pair's SSIM there, at the box where it is lowest
    share: float  # the share of that box those pixels cover
    fidelity: dict[str, float]  # each screen's SSIM against its own real screen, over every box compared


def part_scores(kind: str, chrome: dict, mock: dict, real: dict) -> dict[tuple[str, str], PairScore]:
    """A score for each pair of screens whose real screens show the part (they draw at least SAME_PART of a box
    alike), over those pixels. Either screen's box is tried, so a bar drawn somewhere else on one screen is caught at
    the other screen's box, and so is a bar left off a screen that carries no tag of it."""
    scores = {}
    for a, b in itertools.combinations(chrome, 2):
        compared = []
        for box in (chrome[sid][kind] for sid in (a, b) if kind in chrome[sid]):
            same = alike(real[a], real[b], box)
            if same is not None and same.mean() >= SAME_PART:
                compared.append((box, same))
        if compared:
            mock_ssim, share = min((float(box_map(mock[a], mock[b], box)[same].mean()), float(same.mean()))
                                   for box, same in compared)
            fidelity = {sid: float(np.concatenate([box_map(mock[sid], real[sid], box)[same]
                                                   for box, same in compared]).mean()) for sid in (a, b)}
            scores[a, b] = PairScore(mock_ssim, share, fidelity)
    return scores


def part_failures(kind: str, chrome: dict, mock: dict, real: dict) -> list[dict]:
    """One failure per screen whose part differs from another screen's where their real screens draw it alike. Of a
    failing pair, the one to fix is the screen further from its own real screen over the pixels compared (the later
    in the mock's order on a tie), so a screen drawn as the app draws it is never told to change however many others
    are wrong, and what the app itself changes, a highlighted tab, never decides it."""
    differs = {}
    for (a, b), pair in part_scores(kind, chrome, mock, real).items():
        if pair.mock < CHROME_GATE:
            odd, other = (a, b) if pair.fidelity[a] < pair.fidelity[b] else (b, a)
            differs.setdefault(odd, []).append((other, pair))
    return [part_failure(kind, odd, others, chrome, set(differs)) for odd, others in differs.items()]


def part_failure(kind: str, odd: str, others: list[tuple[str, PairScore]], chrome: dict, flagged: set[str]) -> dict:
    """The fix for one screen: the screens it differs from, the worst pair's numbers, and the screen to copy. That is
    one of those it differs from, preferring one with no failure of its own, then one that marks the part, then the
    one drawn closest to its own real screen."""
    worst = min((pair for _, pair in others), key=lambda pair: pair.mock)
    like, _ = max(others, key=lambda o: (o[0] not in flagged, kind in chrome[o[0]], o[1].fidelity[o[0]]))
    marked = kind in chrome[odd]
    part = f'data-chrome="{kind}" on {odd}' if marked else f'{odd} has no data-chrome="{kind}", and its place'
    return {"kind": "chrome", "screen": odd, "detail":
            f"{part} renders differently from {', '.join(other for other, _ in others)} (SSIM {worst.mock:.3f} "
            f"over the {worst.share:.0%} of the box the real screens draw alike, {CHROME_GATE} needed): draw it as "
            f"{like} does, keeping only what the real screens show differently (a highlighted tab, a title)"
            + ("" if marked else f', marked data-chrome="{kind}"')}


def shows(value: str, text: str) -> bool:
    """Whether a tag's text shows the whole value, in any case and spacing, with words around it allowed: '0
    Following' and '💎 120' show '0' and '120'. A number never matches inside a longer one ('10', '1.0' and '1,200'
    don't show '0' or '200'), and a unit or currency must match too ('120 gems' doesn't show '120 coins', nor '€1.99'
    '$1.99'). text.find has no word boundary, so it would take 'Pro' inside 'Proton'."""
    words = value.split()
    if not words:
        return False
    start = r"(?<!\w)(?<!\d[.,])" if words[0][0].isalnum() else ""
    end = r"(?!\w)(?![.,]\d)" if words[-1][-1].isalnum() else ""
    return re.search(start + r"\s+".join(map(re.escape, words)) + end, text, re.IGNORECASE) is not None


def screen_of(model: ProductModel) -> dict[str, str]:
    """The screen each element id, and each screen id, belongs to."""
    return {e.id: s.id for s in model.states for e in s.elements} | {s.id: s.id for s in model.states}


def value_failures(values: dict[str, list[tuple[str, str]]], model: ProductModel) -> list[dict]:
    """A shared value reads the same everywhere: each data-value tag shows the model's value for its id, the one
    value_text the product model records, and a drawn screen the model's evidence puts the value on carries its tag.
    values maps every drawn screen, in the mock's order, to its (id, text) tags."""
    known = {v.id: v for v in model.cross_screen_values}
    owner = screen_of(model)
    failures = []
    for sid, tags in values.items():
        for vid, text in tags:
            v = known.get(vid)
            if v is None:
                failures.append({"kind": "value", "screen": sid,
                                 "detail": f'data-value="{vid}" on {sid} names no cross-screen value of the model'})
            elif not shows(v.value_text, text):
                right = [s for s, t in values.items() if any(i == vid and shows(v.value_text, x) for i, x in t)]
                failures.append({"kind": "value", "screen": sid, "detail":
                                 f'data-value="{vid}" ({v.label}) on {sid} reads {" ".join(text.split())!r}, not the '
                                 f"model's {v.value_text!r}" + (f" as on {', '.join(right)}" if right else "")})
    for v in model.cross_screen_values:
        for sid in dict.fromkeys(owner[i] for i in v.evidence_ids if i in owner):
            if sid in values and all(vid != v.id for vid, _ in values[sid]):
                failures.append({"kind": "value", "screen": sid, "detail":
                                 f"{v.label} ({v.value_text!r}) is on {sid} in the model, but no tag there carries "
                                 f'data-value="{v.id}"'})
    return failures
