"""Renders the mock in Playwright and checks it against the mock contract (docs/CONTRACTS.md §2, §7)."""

import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image
from playwright.sync_api import sync_playwright

from simula.contracts import ContractError, ContractReport, Device, ProductModel

VIEWPORT = {"width": 411, "height": 914}
SCALE = 2.625
TOP_INSET, BOTTOM_INSET = 51.8, 24.0
FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")
TRANSITIONS = ("push", "modal", "tab", "back", "replace", "unknown")
WALLPAPER_SHARE = 0.4

PAGE_FACTS = """() => {
  const screenOf = el => el.closest('[data-screen]')?.dataset.screen ?? null;
  return {
    hasApi: typeof window.simula?.go === 'function' && typeof window.simula?.state === 'function'
            && typeof window.simula?.reset === 'function',
    screens: [...document.querySelectorAll('[data-screen]')].map(s => ({id: s.dataset.screen, parent: s.dataset.parent ?? null})),
    els: [...document.querySelectorAll('[data-el]')].map(e => ({id: e.dataset.el, screen: screenOf(e)})),
    edges: [...document.querySelectorAll('[data-edge]')].map(e => ({id: e.dataset.edge, transition: e.dataset.transition ?? null, screen: screenOf(e)})),
    images: [...document.querySelectorAll('img')].map(i => ({src: i.getAttribute('src') ?? '', screen: screenOf(i)})),
  };
}"""

# After simula.go(id): is the screen shown, and which image covers the most of its content area?
SCREEN_FACTS = """(id) => {
  const section = document.querySelector(`[data-screen="${CSS.escape(id)}"]`);
  if (!section) return null;
  const box = section.getBoundingClientRect();
  const shown = getComputedStyle(section).display !== 'none' && box.width > 0 && box.height > 0;
  let biggest = {share: 0, what: ''};
  for (const el of [document.body, section, ...section.querySelectorAll('*')]) {
    const isImg = el.tagName === 'IMG' || getComputedStyle(el).backgroundImage.includes('url(');
    if (!isImg) continue;
    const r = el.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, box.right) - Math.max(r.left, box.left));
    const h = Math.max(0, Math.min(r.bottom, box.bottom) - Math.max(r.top, box.top));
    const share = (w * h) / (box.width * box.height);
    if (share > biggest.share) biggest = {share, what: el.getAttribute('src') || el.dataset.el || el.tagName.toLowerCase()};
  }
  return {state: window.simula.state(), shown, biggest};
}"""


@contextmanager
def open_mock(mock_dir: Path):
    """Yields (page, log) with the mock loaded at 411x914 @2.625, animation off, and every request blocked
    except the mock's own files and Google Fonts. log collects console errors, blocked and failed requests."""
    mock_dir = mock_dir.resolve()
    log = {"console": [], "blocked": [], "failed": []}

    def route(r):
        url = urlparse(r.request.url)
        local = url.scheme == "file" and Path(unquote(url.path)).resolve().is_relative_to(mock_dir)
        if local or url.hostname in FONT_HOSTS:
            r.continue_()
        else:
            log["blocked"].append(r.request.url)
            r.abort()

    def console(msg):
        # A failed or blocked request also logs "Failed to load resource"; those are counted as requests instead.
        if msg.type == "error" and not msg.text.startswith("Failed to load resource"):
            log["console"].append(msg.text)

    def failed(request):
        if request.url not in log["blocked"] and urlparse(request.url).hostname not in FONT_HOSTS:
            log["failed"].append(request.url)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE, reduced_motion="reduce")
        page.route("**/*", route)
        page.on("console", console)
        page.on("pageerror", lambda e: log["console"].append(str(e)))
        page.on("requestfailed", failed)
        page.goto((mock_dir / "index.html").as_uri())
        page.wait_for_load_state("networkidle")
        try:
            yield page, log
        finally:
            browser.close()


def screenshot_screens(page, screens: list[str], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for sid in screens:
        page.evaluate("id => window.simula.go(id)", sid)
        path = out_dir / f"{sid}.png"
        page.screenshot(path=path)
        paths.append(path)
    page.evaluate("() => window.simula.reset()")
    return paths


def check_contract(page, log: dict, mock_dir: Path, model: ProductModel, screens: list[str]) -> list[ContractError]:
    facts = page.evaluate(PAGE_FACTS)
    if not facts["hasApi"]:
        return [ContractError(kind="missing_api", detail="window.simula.go/state/reset not defined", screen=None)]
    return (_screen_errors(facts, model, screens) + _element_errors(facts, model) + _edge_errors(facts, model, screens)
            + _image_errors(facts, mock_dir) + _shown_errors(page, screens) + _log_errors(log, facts, mock_dir))


def render_and_validate(mock_dir: Path, model: ProductModel, screens: list[str]) -> ContractReport:
    """Renders every screen to mock_dir/renders/<sid>.png and validates the contract in the same browser."""
    with open_mock(mock_dir) as (page, log):
        errors = check_contract(page, log, mock_dir, model, screens)
        screenshot_screens(page, screens, mock_dir / "renders")
    return ContractReport(passed=not errors, screens=screens, errors=errors)


def content_dp(image: Image.Image, device: Device = Device()) -> Image.Image:
    """Brings a render or a real screenshot to content dp (411x838) for pixel comparison. Accepts a full
    device-size image (renders come out 1079x2399) or one already cropped to the content area."""
    content_h = device.content_bottom_px - device.content_top_px
    size = (int(device.w_px / device.scale), int(content_h / device.scale))
    image = image.convert("RGB")
    if image.size != (device.w_px, content_h):
        image = image.resize((device.w_px, device.h_px), Image.LANCZOS)
        image = image.crop((0, device.content_top_px, device.w_px, device.content_bottom_px))
    return image.resize(size, Image.LANCZOS)


def _error(kind: str, detail: str, screen: str | None = None) -> ContractError:
    return ContractError(kind=kind, detail=detail, screen=screen)


def _screen_errors(facts: dict, model: ProductModel, screens: list[str]) -> list[ContractError]:
    states = {s.id: s for s in model.states}
    present = {s["id"]: s["parent"] for s in facts["screens"]}
    errors = [_error("missing_screen", f"no <section data-screen=\"{sid}\">", sid) for sid in screens if sid not in present]
    for sid, parent in present.items():
        if sid.startswith("new:"):
            continue
        if sid not in states:
            errors.append(_error("unknown_screen", f"{sid} is not a state in the model", sid))
        elif states[sid].kind in ("modal", "sheet") and states[sid].parent_id in present and parent != states[sid].parent_id:
            errors.append(_error("wrong_parent", f"data-parent={parent!r}, model says {states[sid].parent_id!r}", sid))
    return errors


def _element_errors(facts: dict, model: ProductModel) -> list[ContractError]:
    known = {e.id for s in model.states for e in s.elements}
    return [_error("unknown_el", f"data-el={e['id']!r} is not an element in the model", e["screen"])
            for e in facts["els"] if e["id"] not in known]


def _edge_errors(facts: dict, model: ProductModel, screens: list[str]) -> list[ContractError]:
    edges = {e.id: e for e in model.edges}
    present = {s["id"] for s in facts["screens"]}
    placed = {e["id"] for e in facts["edges"]}
    errors = [_error("missing_edge", f"no data-edge=\"{e.id}\"", e.from_state) for e in model.edges
              if e.from_state in screens and e.to_state in screens and e.id not in placed]
    for e in facts["edges"]:
        eid, transition, screen = e["id"], e["transition"], e["screen"]
        target = eid.split(">")[-1]
        known = edges.get(eid)
        if known is None and "new:" not in eid:
            errors.append(_error("unknown_edge", f"data-edge={eid!r} is not an edge in the model", screen))
        if known and screen != known.from_state:
            errors.append(_error("wrong_screen", f"{eid} sits in {screen!r}; the edge starts at {known.from_state!r}", screen))
        if target not in present:
            errors.append(_error("dangling_edge", f"{eid} points at {target!r}, which has no data-screen", screen))
        if transition is None:
            errors.append(_error("missing_transition", f"{eid} has no data-transition", screen))
        elif (known and transition != known.transition) or transition not in TRANSITIONS:
            want = known.transition if known else "|".join(TRANSITIONS)
            errors.append(_error("wrong_transition", f"{eid} has data-transition={transition!r}, want {want!r}", screen))
    return errors


def _image_errors(facts: dict, mock_dir: Path) -> list[ContractError]:
    errors = []
    for img in facts["images"]:
        src, screen = img["src"], img["screen"]
        if not re.fullmatch(r"assets/[^/]+\.png", src):
            errors.append(_error("foreign_image", f"<img src={src[:80]!r}>: images come only from assets/<element_id>.png", screen))
        elif not (mock_dir / src).exists():
            errors.append(_error("missing_asset", f"{src} does not exist", screen))
    return errors


def _shown_errors(page, screens: list[str]) -> list[ContractError]:
    errors = []
    for sid in screens:
        page.evaluate("id => window.simula.go(id)", sid)
        facts = page.evaluate(SCREEN_FACTS, sid)
        if facts is None:
            continue
        if facts["state"] != sid or not facts["shown"]:
            errors.append(_error("go_failed", f"simula.go({sid!r}) left state={facts['state']!r}, shown={facts['shown']}", sid))
        if facts["biggest"]["share"] > WALLPAPER_SHARE:
            share, what = facts["biggest"]["share"], facts["biggest"]["what"]
            errors.append(_error("wallpaper", f"{what} covers {share:.0%} of the screen (max {WALLPAPER_SHARE:.0%})", sid))
    page.evaluate("() => window.simula.reset()")
    return errors


def _log_errors(log: dict, facts: dict, mock_dir: Path) -> list[ContractError]:
    img_urls = {(mock_dir / i["src"]).resolve().as_uri() for i in facts["images"]}
    return ([_error("console_error", text[:300]) for text in log["console"]]
            + [_error("blocked_request", url[:200]) for url in log["blocked"]]
            + [_error("missing_asset", f"request failed: {url[:200]}") for url in log["failed"] if url not in img_urls])
