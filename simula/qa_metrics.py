"""QA's measurements: the pixel compare in content dp (masked SSIM and its heatmap), the identity render, and the
DOM bounds of every data-el."""

import math
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

from simula.contracts import Device, Rect
from simula.render import content_dp, open_mock

WINDOW = 7
HALO = WINDOW // 2
MIN_COVERAGE = 0.3
BOUNDS_TOLERANCE_DP = 4.0
IDENTITY_GATE = 0.985

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
