"""QA's measurements: the pixel compare in content dp (masked SSIM and its heatmap, and pixelmatch beside it), the
identity render, and the DOM bounds of every data-el."""

import itertools
import math
import re
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from pixelmatch.contrib.PIL import pixelmatch
from skimage.metrics import structural_similarity

from simula.contracts import Device, ProductModel, Rect
from simula.render import content_dp, open_mock

WINDOW = 7
HALO = WINDOW // 2
MIN_COVERAGE = 0.3
BOUNDS_TOLERANCE_DP = 4.0
IDENTITY_GATE = 0.985
PIXELMATCH_THRESHOLD = 0.1

# On the section shown now: each visible data-el's box in the section's coordinates (content dp), and every copied
# image it draws (an <img> or a CSS background from assets/) with the box it is drawn in.
SCREEN_DOM = r"""(id) => {
  const section = document.querySelector(`[data-screen="${CSS.escape(id)}"]`);
  if (!section) return {boxes: {}, images: []};
  const origin = section.getBoundingClientRect();
  const local = r => ({x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height});
  const seen = el => el.checkVisibility({opacityProperty: true, visibilityProperty: true});
  const boxes = {};
  for (const el of section.querySelectorAll('[data-el]')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && seen(el) && !(el.dataset.el in boxes)) boxes[el.dataset.el] = local(r);
  }
  const images = [];
  for (const el of section.querySelectorAll('*')) {
    const src = el.tagName === 'IMG' ? el.getAttribute('src') ?? ''
      : getComputedStyle(el).backgroundImage.match(/assets\/[^\/"')]+\.png/)?.[0] ?? '';
    const r = el.getBoundingClientRect();
    if (/^assets\/[^\/]+\.png$/.test(src) && r.width > 0 && r.height > 0 && seen(el)) images.push({src, ...local(r)});
  }
  return {boxes, images};
}"""

# On the section shown now: the box each data-chrome part covers (all its visible tags, in the section's coordinates),
# and the text each visible data-value tag shows.
SHARED_DOM = r"""(id) => {
  const section = document.querySelector(`[data-screen="${CSS.escape(id)}"]`);
  if (!section) return {chrome: {}, values: []};
  const origin = section.getBoundingClientRect();
  const seen = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility({opacityProperty: true, visibilityProperty: true});
  };
  const edges = {};
  for (const el of section.querySelectorAll('[data-chrome]')) {
    if (!seen(el)) continue;
    const r = el.getBoundingClientRect(), e = edges[el.dataset.chrome];
    edges[el.dataset.chrome] = e ? {l: Math.min(e.l, r.left), t: Math.min(e.t, r.top), r: Math.max(e.r, r.right),
                                    b: Math.max(e.b, r.bottom)} : {l: r.left, t: r.top, r: r.right, b: r.bottom};
  }
  const chrome = {};
  for (const [kind, e] of Object.entries(edges))
    chrome[kind] = {x: e.l - origin.left, y: e.t - origin.top, w: e.r - e.l, h: e.b - e.t};
  const values = [...section.querySelectorAll('[data-value]')].filter(seen)
    .map(el => ({id: el.dataset.value, text: el.innerText}));
  return {chrome, values};
}"""


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
    than MIN_COVERAGE of the screen is left to score), coverage, and the per-pixel map for the heatmap."""
    a, b = np.asarray(content_dp(real)), np.asarray(content_dp(mock))
    _, full = structural_similarity(a, b, data_range=255, channel_axis=-1, full=True, win_size=WINDOW)
    ssim_map = full.mean(axis=-1)
    keep = unmasked(masked, ssim_map.shape)
    coverage = float(keep.mean())
    # skimage's own mean skips the half-window border; skipping it too keeps an unmasked score equal to skimage's.
    scored = keep.copy()
    scored[:HALO], scored[-HALO:], scored[:, :HALO], scored[:, -HALO:] = False, False, False, False
    ssim = float(ssim_map[scored].mean()) if coverage >= MIN_COVERAGE and scored.any() else None
    return {"ssim": ssim, "coverage": coverage, "map": ssim_map, "keep": keep}


def pixel_diff(real: Image.Image, mock: Image.Image, keep: np.ndarray) -> float | None:
    """The second pixel metric, pixelmatch (threshold 0.1, anti-aliased pixels not counted) in content dp: the share
    of the unmasked pixels (keep, from compare) that differ. None when too little of the screen is left to score, as
    for SSIM. Reported beside SSIM and never scored: a second view that reads as a plain share of changed pixels."""
    if keep.mean() < MIN_COVERAGE:
        return None
    a = content_dp(real)
    diff = Image.new("RGBA", a.size)
    pixelmatch(a, content_dp(mock), diff, threshold=PIXELMATCH_THRESHOLD, includeAA=False, diff_mask=True)
    return float((np.asarray(diff) == (255, 0, 0, 255)).all(axis=-1)[keep].mean())


def heatmap(real: Image.Image, ssim_map: np.ndarray, keep: np.ndarray) -> Image.Image:
    """The real screen dimmed to gray, red where the mock differs (1 - SSIM), blue over masked pixels."""
    gray = np.asarray(content_dp(real).convert("L"), float)[..., None] * 0.45
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
            page.screenshot(path=Path(tmp) / "render.png")
        return Image.open(Path(tmp) / "render.png").copy()


def screen_dom(page, screen: str) -> tuple[dict[str, Rect], list[tuple[str, Rect]]]:
    """For the screen on show: every visible data-el's box (content dp), and each copied image with its drawn box."""
    dom = page.evaluate(SCREEN_DOM, screen)
    images = [(i.pop("src"), Rect(**i)) for i in dom["images"]]
    return {eid: Rect(**box) for eid, box in dom["boxes"].items()}, images


def overlap(a: Rect, b: Rect) -> Rect | None:
    x0, y0, x1, y1 = max(a.x, b.x), max(a.y, b.y), min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    return Rect(x=x0, y=y0, w=x1 - x0, h=y1 - y0) if x1 > x0 and y1 > y0 else None


def within(got: Rect | None, want: Rect, tolerance: float = BOUNDS_TOLERANCE_DP) -> bool:
    return got is not None and max(abs(got.x - want.x), abs(got.y - want.y),
                                   abs(got.w - want.w), abs(got.h - want.h)) <= tolerance


# ---------- cross-screen check: shared chrome and values ----------

CHROME_GATE = 0.98
# Real screens that score at least this over a chrome box show the same part there. Measured on JanitorAI's real
# screens: a highlighted tab costs the pair about 0.02, while a different title or unrelated content costs 0.07 and more.
SAME_PART = 0.95


def shared_dom(page, screen: str) -> tuple[dict[str, Rect], list[tuple[str, str]]]:
    """For the screen on show: the box of each data-chrome part (content dp), and each data-value tag's id and text."""
    dom = page.evaluate(SHARED_DOM, screen)
    return {kind: Rect(**box) for kind, box in dom["chrome"].items()}, [(v["id"], v["text"]) for v in dom["values"]]


def union(*rects: Rect) -> Rect:
    x0, y0 = min(r.x for r in rects), min(r.y for r in rects)
    return Rect(x=x0, y=y0, w=max(r.x + r.w for r in rects) - x0, h=max(r.y + r.h for r in rects) - y0)


def box_ssim(a: np.ndarray, b: np.ndarray, box: Rect) -> float | None:
    """SSIM of the same box cut from two content-dp images; None when the box is smaller than the SSIM window."""
    h, w = a.shape[:2]
    x0, y0 = max(math.floor(box.x), 0), max(math.floor(box.y), 0)
    x1, y1 = min(math.ceil(box.x + box.w), w), min(math.ceil(box.y + box.h), h)
    if min(x1 - x0, y1 - y0) < WINDOW:
        return None
    return float(structural_similarity(a[y0:y1, x0:x1], b[y0:y1, x0:x1], data_range=255, channel_axis=-1,
                                       win_size=WINDOW))


def chrome_failures(chrome: dict[str, dict[str, Rect]], mocks: dict[str, Image.Image],
                    reals: dict[str, Image.Image]) -> list[dict]:
    """Each data-chrome part (a header, a tab bar) must differ between two screens no more than the real app's does:
    a pair fails when its mock SSIM over both boxes is more than 1 - CHROME_GATE below the real screens' SSIM there.
    Chrome the app draws identically must score at least CHROME_GATE, and a highlighted tab may differ only as much as
    it does in the app. A bar drawn in another place fails too. Only pairs whose real screens show the same part (SSIM
    at least SAME_PART at one of the pair's boxes) are compared. A screen with no tag of the part is compared at the
    other screen's box, so a bar left off one screen fails. The screen that matches fewer others is the one to fix;
    on a tie, the later in the mock's order. The screen to copy is the marked one that matches the most others.
    chrome maps every drawn screen, in the mock's order, to its parts' boxes (none when it marks none); mocks and reals
    are content-dp images."""
    mock = {sid: np.asarray(image.convert("RGB")) for sid, image in mocks.items()}
    real = {sid: np.asarray(image.convert("RGB")) for sid, image in reals.items()}
    failures = []
    for kind in dict.fromkeys(k for parts in chrome.values() for k in parts):
        scores = {}
        for a, b in itertools.combinations(chrome, 2):
            boxes = [chrome[sid][kind] for sid in (a, b) if kind in chrome[sid]]
            in_app = max((s for s in (box_ssim(real[a], real[b], box) for box in boxes) if s is not None), default=0.0)
            if in_app >= SAME_PART:
                scores[a, b] = box_ssim(mock[a], mock[b], union(*boxes)), in_app
        matches = Counter(sid for pair, (m, r) in scores.items() if m >= r - (1 - CHROME_GATE) for sid in pair)
        differs = {}
        for (a, b), (m, r) in scores.items():
            if m < r - (1 - CHROME_GATE):
                odd, other = (a, b) if matches[a] < matches[b] else (b, a)
                differs.setdefault(odd, []).append((other, m, r))
        for odd, others in differs.items():
            names = ", ".join(other for other, _, _ in others)
            _, m, r = min(others, key=lambda o: o[1] - o[2])
            marked_others = [other for other, _, _ in others if kind in chrome[other]]
            like = max(marked_others or [others[0][0]], key=lambda other: matches[other])
            marked = kind in chrome[odd]
            part = f'data-chrome="{kind}" on {odd}' if marked else f'{odd} has no data-chrome="{kind}", and its place'
            failures.append({"kind": "chrome", "screen": odd, "detail":
                             f"{part} renders differently from {names} (SSIM {m:.3f}, where the real screens score "
                             f"{r:.3f}): draw it as {like} does, keeping only what the real screens show differently "
                             "(a highlighted tab, a title)"
                             + ("" if marked else f', marked data-chrome="{kind}"')})
    return failures


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def shows(value: str, text: str) -> bool:
    """Whether a tag's text shows a value. A value with numbers is shown when each of its numbers is a whole number in
    the tag, whatever words sit around it: '0 Following' and '💎 0' show '0', and '120' shows '120 coins', but '10',
    '1.0' and 'coins' don't. A value with no number must appear in whole words, in any case and spacing."""
    numbers = NUMBER.findall(value)
    if numbers:
        return all(n in NUMBER.findall(text) for n in numbers)
    words = value.split()
    pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)"
    return bool(words) and re.search(pattern, text, re.IGNORECASE) is not None


def value_failures(values: dict[str, list[tuple[str, str]]], model: ProductModel) -> list[dict]:
    """A shared value reads the same everywhere: each data-value tag shows the model's value for its id, the one
    value_text the product model records, and a drawn screen the model's evidence puts the value on carries its tag.
    values maps every drawn screen, in the mock's order, to its (id, text) tags."""
    known = {v.id: v for v in model.cross_screen_values}
    owner = {e.id: s.id for s in model.states for e in s.elements} | {s.id: s.id for s in model.states}
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
