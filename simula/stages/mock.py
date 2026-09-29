"""Goal 2: the interactive mock. Parallel model calls each draw a batch of screens from model/ alone; code joins
the batches into one page, adds navigation, renders every screen, and checks the mock contract."""

import base64
import hashlib
import http.client
import io
import json
import math
import re
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from html import escape, unescape
from html.parser import HTMLParser
from itertools import accumulate
import urllib.request
from urllib.parse import quote_plus

import numpy as np
from PIL import Image

from simula import config, llm, render
from simula.config import ROOT
from simula.contracts import (ContractError, ContractReport, Device, Edge, Element, ProductModel, Rect, StageOutcome,
                              State)
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx

# ponytail: a fixed batch size. If a batch still runs out of output tokens, size batches from measured tokens per screen.
BATCH_SCREENS = 4
PARALLEL_BATCHES = 4
DIALOGS = ("modal", "sheet")
PALETTE_SIZE = 4
FONT_NAME = re.compile(r"[A-Za-z0-9 ]+")
FONT_CSS = "https://fonts.googleapis.com/css2?family={family}:wght@400;500;600;700&display=swap"
FONT_FILE = re.compile(r"url\((https?://[^)\s]+)\)")
URL_TARGET = re.compile(r"url\(\s*['\"]?([^'\")\s]+)")
# Google's font repository files each family under its license: SIL OFL, Apache 2.0, or the Ubuntu Font Licence.
FONT_LICENSES = [f"https://raw.githubusercontent.com/google/fonts/main/{kind}/{{slug}}/{name}"
                 for kind, name in (("ofl", "OFL.txt"), ("apache", "LICENSE.txt"), ("ufl", "UFL.txt"))]
FONT_FACE = re.compile(r"(?:/\*\s*([^*]*?)\s*\*/\s*)?(@font-face\s*\{[^}]*\})")
UNICODE_RANGE = re.compile(r"unicode-range:([^;}]*)")
CODE_POINTS = re.compile(r"U\+([0-9a-fA-F]+)(?:-([0-9a-fA-F]+))?")
FETCH_TIMEOUT_S = 20
FONT_RECORDS = "font-records"
PLAN_RECORD = "plan.json"
CAP_REASON = "$ cap reached: "
# Every way urllib fails a download: a socket, TLS or HTTP status error (OSError), or a cut-off or malformed
# response (HTTPException, which isn't an OSError).
FETCH_ERRORS = (OSError, http.client.HTTPException)
# Google Fonts serves woff2 only to a browser it knows; without a user agent it serves TTF.
CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/140.0.0.0 Safari/537.36")
STYLE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
SCREEN_SELECTOR = re.compile(r"""section\[data-screen=["']?([^"'\]]+)["']?\]""")
GROUP_RULES = ("@media", "@supports", "@container", "@layer")
UNDRAWN_STYLE = "margin:0;padding:24px;font:16px system-ui,sans-serif;color:#888"
TAGGED_PER_REPEAT = 2
WALL_SECONDS = 30 * 60
IMAGE_LONG_SIDE = 1568
ART_MIN_SIDE = 48
ART_MIN_COLORS = 120
ALREADY_CROPPED = 0.9
RETRY_EFFORT = {"max": "high", "xhigh": "high"}
GESTURES = ("swipe", "back", "type")
# ponytail: Android's editable widget classes, the only platform explored so far. Add iOS field types with an iOS run.
TEXT_FIELDS = ("EditText", "AutoCompleteTextView", "MultiAutoCompleteTextView")
ACTIONS_BLOCK = re.compile(r'<script id="simula-actions" type="application/json">.*?</script>\n?', re.S)
ACTIONS_JS = '<script id="simula-actions" type="application/json">%s</script>\n'
SHORTER = "A first attempt ran out of output tokens. Write shorter CSS: shared classes, no repeated rules, no comments."

RUNTIME_CSS = """<style id="simula-runtime">
html,body{margin:0;padding:0;width:411px;height:914px;overflow:hidden}
body{position:relative}
[data-screen]{position:absolute!important;left:0!important;top:51.8px!important;width:411px!important;height:838.2px!important;overflow:hidden;box-sizing:border-box}
[data-screen]:not(.simula-on){display:none!important}
[data-screen][data-parent]{z-index:10;background:transparent!important}
.simula-push{animation:simula-slide .25s ease-out}
.simula-back{animation:simula-slide-back .25s ease-out}
.simula-modal{animation:simula-fade .2s ease-out}
@keyframes simula-slide{from{transform:translateX(100%)}}
@keyframes simula-slide-back{from{transform:translateX(-30%)}}
@keyframes simula-fade{from{opacity:0}}
@media (prefers-reduced-motion:reduce){.simula-push,.simula-back,.simula-modal{animation:none}}
[data-simula-field]{cursor:text;-webkit-user-select:text;user-select:text}
[data-simula-field]:focus{outline:none}
</style>
"""

RUNTIME_JS = """<script id="simula-runtime-js">
(() => {
  const ROOT = %s;
  const find = id => document.querySelector(`[data-screen="${CSS.escape(id)}"]`);
  let current = null;
  function show(id, transition) {
    const target = find(id);
    if (!target) return false;
    const from = current && find(current);
    const closingOverlay = from && from.dataset.parent === id;
    const kind = transition === 'unknown' ? 'push' : transition;
    for (const s of document.querySelectorAll('[data-screen]')) s.classList.remove('simula-on', 'simula-push', 'simula-back', 'simula-modal');
    const parent = target.dataset.parent && find(target.dataset.parent);
    if (parent) parent.classList.add('simula-on');
    target.classList.add('simula-on');
    if (['push', 'back', 'modal'].includes(kind) && !closingOverlay) target.classList.add('simula-' + kind);
    current = id;
    document.body.dataset.transition = kind;
    return true;
  }
  // Edges that start on no element come from code's #simula-actions map: {screen: {action: [to, transition, field]}}.
  // A drag of 48 px or more is a swipe, or the system back when it starts at the left edge and runs right; Escape is
  // the system back too; Enter in the screen's text field sends what was typed.
  let actions = null;
  const actionsOf = () => actions = actions || JSON.parse(document.getElementById('simula-actions')?.textContent || '{}');
  const gesture = kind => { const a = (actionsOf()[current] || {})[kind]; return !!a && show(a[0], a[1]); };
  let start = null, dragged = false;
  document.addEventListener('pointerdown', e => { start = [e.clientX, e.clientY]; dragged = false; });
  document.addEventListener('pointercancel', () => { start = null; });
  // A drag that starts on a picture must stay a swipe: the browser's own image drag would cancel the pointer.
  document.addEventListener('dragstart', e => e.preventDefault());
  document.addEventListener('pointerup', e => {
    if (!start) return;
    const [x, y] = start, dx = e.clientX - x, dy = e.clientY - y;
    start = null;
    if (Math.hypot(dx, dy) < 48) return;
    dragged = true;
    if (!(x <= 24 && dx > Math.abs(dy) && gesture('back'))) gesture('swipe');
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') gesture('back');
    const field = e.target.closest && e.target.closest('[data-simula-field]');
    if (e.key === 'Enter' && field && field.closest('[data-screen]').dataset.screen === current) {
      e.preventDefault();
      gesture('type');
    }
  });
  const fields = () => {
    for (const [sid, a] of Object.entries(actionsOf())) {
      const id = a.type && a.type[2];
      const f = id && document.querySelector(`[data-screen="${CSS.escape(sid)}"] [data-el="${CSS.escape(id)}"]`);
      if (!f) continue;
      f.contentEditable = 'plaintext-only';
      f.tabIndex = 0;
      f.dataset.simulaField = '';
      // The drawn placeholder gives way on the first focus, as a real field's hint does.
      f.addEventListener('focus', () => {
        if ('simulaTyped' in f.dataset) return;
        f.dataset.simulaTyped = '';
        f.textContent = '';
      });
    }
  };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fields) : fields();
  document.addEventListener('click', e => {
    if (dragged) { dragged = false; return; }
    const el = e.target.closest('[data-edge]');
    if (!el) return;
    e.preventDefault();
    show(el.dataset.edge.split('>').pop(), el.dataset.transition || 'push');
  });
  window.simula = {go: id => show(id, 'tab'), state: () => current, reset: () => show(ROOT, 'tab')};
  show(ROOT, 'tab');
})();
</script>
"""


def run(ctx: Ctx) -> StageOutcome:
    model_dir, mock_dir = ctx.run_dir / "model", ctx.run_dir / "mock"
    model = ProductModel.model_validate_json((model_dir / "product_model.json").read_text())
    if not ctx.replay:
        # A live rerun starts from nothing: QA's replay key hashes mock/assets and later stages hash all of mock/, so a
        # file the new page no longer uses would count. A replay keeps them: the live run cleared them when it started,
        # so they are its own output, which a replay rewrites byte for byte, and a replay that misses leaves them
        # whole for the done.json it keeps. The records under mock/ are what --replay rebuilds from; they always stay.
        for built in ("assets", "renders"):
            shutil.rmtree(mock_dir / built, ignore_errors=True)
    scope = pick_scope(model)
    screens = [s.id for s in scope]
    groups = batches(scope)
    run_trace(ctx.run_dir, stage="mock", step="scope", decider="code",
              note=" | ".join(" ".join(s.id for s in batch) for batch in groups))
    copy_assets(model_dir, mock_dir, scope, model.device)
    art = crop_art(model_dir, mock_dir, scope, model.device)

    style = shared_style(scope)
    contents = [batch_content(ctx, model, batch, screens, art, style) for batch in groups]
    plan = batch_plan(ctx, contents)
    budget = llm.Budget.for_stage("mock", ctx.run_dir / "trace.jsonl", plan.cap)
    run_trace(ctx.run_dir, stage="mock", step="plan", decider="code",
              outcome="ok" if plan.keep == len(groups) else "cap", note=plan.line())
    parts, undrawn, batch_errors = draw_batches(ctx, groups, contents, budget, plan)
    fonts = vendor_fonts(ctx, mock_dir, fonts_of(scope), page_chars(parts))
    html = with_runtime(wire_edges(stitch(style, fonts, parts), model, screens), home_id(scope))
    (mock_dir / "index.html").write_text(html)

    checked = render.render_and_validate(mock_dir, model, screens, crops=crop_origins(model, mock_dir))
    errors = [ContractError(kind="undrawn_screen", detail=f"screen not drawn: {reason}", screen=sid)
              for sid, reason in undrawn.items()] + batch_errors + checked.errors
    report = ContractReport(passed=not errors, screens=screens, errors=errors)
    (mock_dir / "contract_report.json").write_text(report.model_dump_json(indent=1))
    run_trace(ctx.run_dir, stage="mock", step="contract", decider="code", outcome="ok" if report.passed else "error",
              note=f"{len(screens)} screens rendered, {len(undrawn)} not drawn, {len(errors)} contract errors")
    write_exhibit(ctx.run_dir, 3, "mock", exhibit(ctx, model, scope, groups, undrawn, html, report, plan))
    # A batch the $ cap left out makes the stage partial through its cap trace line (run_stage); the rest say why here.
    lost = [f"not drawn: {' '.join(s.id for s in batch)}: {undrawn[batch[0].id]}" for batch in groups
            if batch[0].id in undrawn and not undrawn[batch[0].id].startswith(CAP_REASON)]
    return StageOutcome(status="partial", reasons=lost) if lost else StageOutcome()


def pick_scope(model: ProductModel) -> list[State]:
    """Exactly the model stage's scope (it owns which states are in, unsafe and blocked ones included), in its
    priority order `mock_order`. States mock_order leaves out follow in state order."""
    scope = [s for s in model.states if s.in_mock_scope]
    if not scope:
        raise ValueError("the product model has no state in mock scope")
    rank = {sid: i for i, sid in enumerate(model.mock_order)}
    return sorted(scope, key=lambda s: rank.get(s.id, len(rank)))


def batches(scope: list[State]) -> list[list[State]]:
    """Consecutive screens in priority order, at most BATCH_SCREENS per batch. A modal or sheet joins the batch of
    the screen it sits on, so a batch holding a screen with more dialogs than that can run over."""
    by_id = {s.id: s for s in scope}
    units = {}
    for state in scope:
        units.setdefault(anchor(state, by_id), []).append(state)
    groups = []
    for unit in units.values():
        if groups and len(groups[-1]) + len(unit) <= BATCH_SCREENS:
            groups[-1] += unit
        else:
            groups.append(list(unit))
    return groups


def anchor(state: State, by_id: dict[str, State]) -> str:
    """The in-scope screen a dialog is drawn over (through dialogs over dialogs), or the state itself."""
    seen = set()
    while state.kind in DIALOGS and state.parent_id in by_id and state.id not in seen:
        seen.add(state.id)
        state = by_id[state.parent_id]
    return state.id


def home_id(scope: list[State]) -> str:
    """Where the mock opens and simula.reset() returns: the first screen that is not a dialog over another one."""
    return next((s.id for s in scope if s.parent_id is None), scope[0].id)


def tagged_ids(state: State) -> set[str]:
    """Elements that carry data-el: every in_mock element, but only the first 2 items of a repeated list."""
    seen, ids = Counter(), set()
    for e in state.elements:
        if not e.in_mock:
            continue
        if e.repeat_group:
            seen[e.repeat_group] += 1
            if seen[e.repeat_group] > TAGGED_PER_REPEAT:
                continue
        ids.add(e.id)
    return ids


def scope_edges(model: ProductModel, scope: list[State]) -> list[Edge]:
    ids = {s.id for s in scope}
    return [e for e in model.edges if e.from_state in ids and e.to_state in ids]


def usable_asset(e: Element, elements: list[Element], device: Device) -> bool:
    """An asset the builder may use. One holding no other element's words or image can't be a screenshot of
    interface, so it may be any size; any other stays under the no-wallpaper limit."""
    return bool(e.in_mock and e.asset_png) and (not holds_ui(e, elements) or under_wallpaper_limit(e.rect_dp, device))


def under_wallpaper_limit(r: Rect, device: Device) -> bool:
    screen = content_rect(device)
    return area(overlap(r, screen)) <= render.WALLPAPER_SHARE * area(screen)


def shows_ui(e: Element) -> bool:
    """Listed interface: an element with words or an image of its own."""
    return bool(e.text or e.label or e.asset_png)


def holds_ui(e: Element, elements: list[Element]) -> bool:
    """Whether another element's words or image sit on e's rect, even partly (its parents aside), so a crop of e
    would bake that interface in. These are exactly the elements art search keeps its crops clear of."""
    return any(drawn_over(o, e) and area(overlap(o.rect_dp, e.rect_dp)) > 0 for o in elements)


def crop_origins(model: ProductModel, mock_dir) -> dict[str, tuple[str, Rect]]:
    """The contract check's exemption list: every code-made image that holds no listed interface, each src with its
    screen and the content-dp rect it was cut from. That is every art crop in mock_dir/art.json (art search avoids
    words and images by construction) and every element asset with no other element's words or image inside it."""
    path = mock_dir / "art.json"
    art = json.loads(path.read_text())["art"] if path.exists() else {}
    crops = {}
    for s in model.states:
        for e in s.elements:
            if art_src(e.id) in art:
                crops[art_src(e.id)] = (s.id, Rect(**art[art_src(e.id)]))
            if e.asset_png and not holds_ui(e, s.elements):
                crops[e.asset_png] = (s.id, e.rect_dp)
    return crops


def copy_assets(model_dir, mock_dir, scope: list[State], device: Device) -> None:
    (mock_dir / "assets").mkdir(parents=True, exist_ok=True)
    for e in (e for s in scope for e in s.elements if usable_asset(e, s.elements, device)):
        shutil.copyfile(model_dir / e.asset_png, mock_dir / "assets" / f"{e.id}.png")


# ---------- art painted inside containers ----------

def crop_art(model_dir, mock_dir, scope: list[State], device: Device) -> dict[str, Rect]:
    """Crops the real pictures that sit inside containers with no image node of their own into
    mock/assets/<element id>.art.png, and lists them in mock/art.json so QA can mask them."""
    art = {}
    for state in scope:
        image = Image.open(model_dir / state.canonical_png).convert("RGB")
        for eid, rect in find_art(state, image, device).items():
            crop_px(image, rect, device.scale).save(mock_dir / "assets" / f"{eid}.art.png")
            art[eid] = rect
    listed = {art_src(eid): r.model_dump() for eid, r in art.items()}
    (mock_dir / "art.json").write_text(json.dumps({"schema_version": 1, "art": listed}, indent=1))
    return art


def find_art(state: State, image: Image.Image, device: Device) -> dict[str, Rect]:
    """For each element with no asset and no text of its own (its words cover its rect), drawn or a wordless
    container: the largest picture-like region inside it that nothing else is drawn over. Such a region holds no
    listed words or image, so it may be any size. image is the state's content-area screenshot."""
    screen = content_rect(device)
    cropped = [e.rect_dp for e in state.elements if usable_asset(e, state.elements, device)]
    art = {}
    for e in state.elements:
        if e.asset_png or e.text:
            continue
        box = overlap(e.rect_dp, screen)
        blockers = [r for r in (overlap(o.rect_dp, box) for o in state.elements if drawn_over(o, e)) if area(r) > 0]
        for rect in free_rects(box, blockers, ART_MIN_SIDE):
            if any(area(overlap(rect, c)) >= ALREADY_CROPPED * area(rect) for c in cropped):
                continue
            if not is_picture(crop_px(image, rect, device.scale)):
                continue
            art[e.id] = rect
            cropped.append(rect)
            break
    return art


def drawn_over(other: Element, container: Element) -> bool:
    """Another element with words or an image of its own. A bigger element around the container is its parent."""
    a, b = other.rect_dp, container.rect_dp
    around = contains(a, b) and area(a) > area(b)
    return other.id != container.id and shows_ui(other) and not around


def free_rects(box: Rect, blockers: list[Rect], min_side: float) -> list[Rect]:
    """Rectangles in box, both sides at least min_side, that overlap no blocker, largest first. The blockers'
    edges cut box into a grid; each grid row is then a largest-rectangle-in-a-histogram scan."""
    xs = sorted({box.x, box.x + box.w, *(v for b in blockers for v in (b.x, b.x + b.w))})
    ys = sorted({box.y, box.y + box.h, *(v for b in blockers for v in (b.y, b.y + b.h))})
    blocked = np.zeros((len(ys) - 1, len(xs) - 1), dtype=bool)
    for b in blockers:
        blocked[ys.index(b.y):ys.index(b.y + b.h), xs.index(b.x):xs.index(b.x + b.w)] = True
    heights, found = [0.0] * (len(xs) - 1), set()
    for row, bottom in enumerate(ys[1:]):
        heights = [0.0 if blocked[row, c] else h + bottom - ys[row] for c, h in enumerate(heights)]
        stack = []
        for c, h in enumerate(heights + [0.0]):
            start = c
            while stack and stack[-1][1] >= h:
                start, bar = stack.pop()
                rect = snap_inward(Rect(x=xs[start], y=bottom - bar, w=xs[c] - xs[start], h=bar))
                if min(rect.w, rect.h) >= min_side:
                    found.add((rect.x, rect.y, rect.w, rect.h))
            stack.append((start, h))
    return sorted((Rect(x=x, y=y, w=w, h=h) for x, y, w, h in found), key=area, reverse=True)


def snap_inward(r: Rect) -> Rect:
    """Rounds a rect to 0.1 dp without letting it grow past where it was."""
    x0, y0 = math.ceil(r.x * 10 - 1e-6) / 10, math.ceil(r.y * 10 - 1e-6) / 10
    x1, y1 = math.floor((r.x + r.w) * 10 + 1e-6) / 10, math.floor((r.y + r.h) * 10 + 1e-6) / 10
    return Rect(x=x0, y=y0, w=round(x1 - x0, 1), h=round(y1 - y0, 1))


def is_picture(image: Image.Image) -> bool:
    # ponytail: distinct colors at 16 levels per channel. On the three goldens, flat fills, text and icons reach
    # at most 76 and pictures at least 194; a flat-shaded illustration with few colors is missed. Swap in a
    # texture measure if that happens.
    levels = (np.asarray(image) // 16).reshape(-1, 3)
    return len(np.unique(levels, axis=0)) >= ART_MIN_COLORS


def crop_px(image: Image.Image, r: Rect, scale: float) -> Image.Image:
    return image.crop((round(r.x * scale), round(r.y * scale), round((r.x + r.w) * scale), round((r.y + r.h) * scale)))


def art_src(eid: str) -> str:
    return f"assets/{eid}.art.png"


def content_rect(device: Device) -> Rect:
    return Rect(x=0, y=0, w=device.w_px / device.scale, h=(device.content_bottom_px - device.content_top_px) / device.scale)


def overlap(a: Rect, b: Rect) -> Rect:
    x, y = max(a.x, b.x), max(a.y, b.y)
    return Rect(x=x, y=y, w=max(0, min(a.x + a.w, b.x + b.w) - x), h=max(0, min(a.y + a.h, b.y + b.h) - y))


def area(r: Rect) -> float:
    return r.w * r.h


def contains(a: Rect, b: Rect) -> bool:
    return a.x <= b.x and a.y <= b.y and a.x + a.w >= b.x + b.w and a.y + a.h >= b.y + b.h


# ---------- the model calls, one per batch ----------

@dataclass(frozen=True)
class Plan:
    """How many batches, in priority order, the stage asks for: those whose planned costs (`usd`, one per batch)
    fit what was left of `cap` after the `spent` already in the run when the stage started."""
    keep: int
    cap: float
    spent: float
    usd: list[float]

    def line(self) -> str:
        cached = self.usd.count(0.0)
        return (f"{self.keep} of {len(self.usd)} batches fit the ${self.cap:.2f} cap with ${self.spent:.2f} already "
                f"spent, at worst case: ${sum(self.usd[:self.keep]):.2f} + ${max(self.usd):.2f} spare for one retry"
                + (f"; {cached} already cached, at $0" if cached else ""))


def batch_plan(ctx: Ctx, contents: list[list[dict]]) -> Plan:
    """A live run plans with the $ check's own arithmetic: it counts the mock spend already in the run against the
    cap (after any --usd-cap), and a batch whose answer is already cached costs nothing. It records the plan in
    mock/plan.json. --replay reads that record instead of planning again: its trace and cache also hold what the live
    run spent and drew, so a new plan could differ. With no record it stops, like a model call's replay miss."""
    path = ctx.run_dir / "mock" / PLAN_RECORD
    if ctx.replay:
        if not path.exists():
            raise llm.ReplayMiss(f"--replay: no recorded batch plan at mock/{PLAN_RECORD}")
        return Plan(**json.loads(path.read_text()))
    cap = config.stage_cap("mock") if ctx.usd_cap is None else ctx.usd_cap
    spent = stage_usd(ctx)
    usd = [planned_usd(ctx, content) for content in contents]
    plan = Plan(keep=affordable(usd, max(cap - spent, 0.0)), cap=cap, spent=spent, usd=usd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(plan)))
    return plan


def planned_usd(ctx: Ctx, content: list[dict]) -> float:
    """What one batch can cost this run: nothing when every call generate would make settles from the cache (an
    answer, or a known failure the call raises again, free; after a cut-off first answer, the retry's record decides),
    else its worst case."""
    if not ctx.no_cache:
        for request in builder_requests(ctx, content):
            settled = llm.answered_from_cache(**request)
            if settled is None:
                break
            if settled.failure != "max_tokens":
                return 0.0
        else:
            return 0.0
    return worst_usd(ctx, content)


def affordable(worst: list[float], cap: float) -> int:
    """How many batches, in priority order, fit the cap at their worst case with one worst case spare for a
    max_tokens retry. Stops at the first that doesn't fit: never skips a batch to fit a cheaper later one."""
    # ponytail: a priority prefix, so a cached ($0) batch behind the first batch that doesn't fit is left out too, and
    # a rerun under a tight cap stops the stage. Keep $0 batches past the prefix (keep as a list) if that gets common.
    spare = max(worst, default=0.0)
    return sum(1 for total in accumulate(worst) if total + spare <= cap)


def worst_usd(ctx: Ctx, content: list[dict]) -> float:
    """The most one batch's call can cost: its retry prompt (the longer one) answered up to max_tokens."""
    _, retry = builder_requests(ctx, content)
    return llm.worst_case_usd(retry["model"], llm.estimate_tokens_in(retry["system"], retry["messages"]),
                              retry["max_tokens"])


def builder_requests(ctx: Ctx, content: list[dict]) -> tuple[dict, dict]:
    """generate's two calls for one batch, as llm.call and llm.answered_from_cache take them: the first at the role's
    effort, and the retry after an answer cut off at max_tokens, at lower effort and asking for shorter CSS."""
    role = config.roles(ctx.profile)["mock_builder"]
    effort = role.get("effort")

    def request(content: list[dict], effort: str | None) -> dict:
        return {"model": role["model"], "effort": effort, "system": system_prompt(),
                "messages": [{"role": "user", "content": content}], "max_tokens": role["max_tokens"]}
    return (request(content, effort),
            request(content + [{"type": "text", "text": SHORTER}], RETRY_EFFORT.get(effort, effort)))


def draw_batches(ctx: Ctx, groups: list[list[State]], contents: list[list[dict]], budget: llm.Budget,
                 plan: Plan) -> tuple[list[tuple[str, str]], dict[str, str], list[ContractError]]:
    """Each batch's (CSS, sections), drawn at most PARALLEL_BATCHES at once, the screens not drawn with why, and
    the contract errors of batches that reach outside their own screens. Only the plan's first `keep` batches are
    asked for; it left the rest over budget. A batch that fails becomes placeholder sections and the rest still
    ship; the stage fails only if all fail."""
    over = llm.CapReached(f"over budget: batches {plan.keep + 1}-{len(groups)} don't fit the ${plan.cap:.2f} mock "
                          f"cap at worst case, with ${plan.spent:.2f} already spent; raise with --usd-cap")

    def draw(n: int, content: list[dict]):
        if n > plan.keep:
            return over
        try:
            return batch_parts(generate(ctx, content, budget, f"batch{n}"))
        except (llm.LLMFailure, llm.CapReached, ValueError) as e:
            return e

    # ponytail: the plan fits every first call plus one max_tokens retry at worst case, so the $ check never turns a
    # planned call away. A second retry in one run is outside the plan: the budget's lock turns it away, so that
    # batch becomes a placeholder and the run's replay misses on it. Keep more spare if retries get common.
    with ThreadPoolExecutor(PARALLEL_BATCHES) as pool:
        results = list(pool.map(draw, range(1, len(groups) + 1), contents))
    failures = [r for r in results if isinstance(r, BaseException)]
    if len(failures) == len(results):
        # A cap failure gets the CLI's needs-human instructions (raise --usd-cap), so it wins over any other.
        raise next((f for f in failures if isinstance(f, llm.CapReached)), failures[0])
    parts, undrawn, errors = [], {}, []
    for n, (batch, result) in enumerate(zip(groups, results), 1):
        if isinstance(result, BaseException):
            reason = failure_reason(result)
            undrawn |= {s.id: reason for s in batch}
            run_trace(ctx.run_dir, stage="mock", step=f"batch{n}", decider="code", outcome=failure_outcome(result),
                      note=f"not drawn: {' '.join(s.id for s in batch)}: {reason}"[:300])
            result = ("", placeholders(batch, reason))
        else:
            errors += outside_errors(batch, *result)
        parts.append(result)
    return parts, undrawn, errors


def outside_errors(batch: list[State], css: str, markup: str) -> list[ContractError]:
    """Batches share one page, so a batch rule not scoped to its own screens, or a section for another batch's
    screen, changes screens that batch never saw. QA round 1 repairs these like any contract error."""
    ids, first = {s.id for s in batch}, batch[0].id
    unscoped = [ContractError(kind="unscoped_css", detail=f"{sel[:120]!r} is not scoped to {' '.join(sorted(ids))}",
                              screen=first) for sel in top_selectors(css) if not scoped_to(sel, ids)]
    drawn = [t["attrs"]["data-screen"] for t in StartTags(markup).tags if t["attrs"].get("data-screen")]
    return unscoped + [ContractError(kind="foreign_screen", detail=f"the batch of {first} drew section {sid!r}",
                                     screen=first) for sid in drawn if sid not in ids]


def top_selectors(css: str) -> list[str]:
    """The selectors of every rule not nested in another rule, at the top or inside @media-like groups.
    @keyframes and @font-face bodies hold no selectors."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors, open_blocks, start = [], [], 0
    for i, ch in enumerate(css):
        if ch not in "{};":
            continue
        prelude, start = css[start:i].strip(), i + 1
        if ch == "{":
            nested = any(kind != "group" for kind in open_blocks)
            kind = "group" if prelude.startswith(GROUP_RULES) else "other" if nested or prelude.startswith("@") else "rule"
            if kind == "rule":
                selectors += [sel.strip() for sel in re.split(r",(?![^()]*\))", prelude)]
            open_blocks.append(kind)
        elif ch == "}" and open_blocks:
            open_blocks.pop()
    return selectors


def scoped_to(selector: str, ids: set[str]) -> bool:
    """True when the selector starts with one of these screens' sections, or an :is()/:where() list of them."""
    group = re.match(r":(?:is|where)\(([^()]*)\)", selector)
    heads = group.group(1).split(",") if group else [selector]
    return all((m := SCREEN_SELECTOR.match(h.strip())) is not None and m.group(1) in ids for h in heads)


def failure_reason(e: BaseException) -> str:
    return (f"{CAP_REASON}{e}" if isinstance(e, llm.CapReached) else str(e))[:200]


def failure_outcome(e: BaseException) -> str:
    return "cap" if isinstance(e, llm.CapReached) else getattr(e, "outcome", "error")


def placeholders(batch: list[State], reason: str) -> str:
    return "\n".join(f'<section data-screen="{s.id}"{parent_attr(s)}><p style="{UNDRAWN_STYLE}">'
                     f"screen not drawn: {escape(reason)}</p></section>" for s in batch)


def parent_attr(state: State) -> str:
    return f' data-parent="{state.parent_id}"' if state.kind in DIALOGS and state.parent_id else ""


def generate(ctx: Ctx, content: list[dict], budget: llm.Budget, step: str) -> str:
    """One batch's call. An answer cut off at max_tokens is retried once, at lower effort, asking for shorter CSS."""
    def ask(request: dict) -> str:
        text, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="mock", step=step, **request, budget=budget,
                           no_cache=ctx.no_cache, replay=ctx.replay, attempts=1, total_timeout=WALL_SECONDS)
        return extract_html(text)

    first, retry = builder_requests(ctx, content)
    try:
        return ask(first)
    except llm.LLMFailure as e:
        if e.outcome != "max_tokens":
            raise
    run_trace(ctx.run_dir, stage="mock", step=step, decider="code", outcome="retry",
              note=f"max_tokens: retrying at effort={retry['effort']} with shorter CSS")
    return ask(retry)


def batch_content(ctx: Ctx, model: ProductModel, batch: list[State], screens: list[str], art: dict[str, Rect],
                  style: str) -> list[dict]:
    return screenshots(ctx, batch) + [
        {"type": "text", "text": f"The page's shared style, already in the page:\n<style>\n{style}\n</style>"},
        {"type": "text", "text": brief(model, batch, screens, art)}]


def system_prompt() -> str:
    parts = [p.read_text() for p in sorted((ROOT / "prompts" / "mock").glob("*.md"))]
    return "\n\n".join(parts + [contract_text()])


def contract_text() -> str:
    """The geometry and mock-contract sections of docs/CONTRACTS.md, verbatim."""
    doc = (ROOT / "docs" / "CONTRACTS.md").read_text()
    sections = re.split(r"(?m)^(?=## )", doc)
    return "\n".join(s for s in sections if s.startswith(("## 2. Geometry", "## 7. Mock contract")))


def screenshots(ctx: Ctx, scope: list[State]) -> list[dict]:
    parts = []
    for s in scope:
        image = Image.open(ctx.run_dir / "model" / s.canonical_png)
        image.thumbnail((IMAGE_LONG_SIDE, IMAGE_LONG_SIDE))
        png = io.BytesIO()
        image.save(png, format="PNG")
        parts += [{"type": "text", "text": f"Screenshot of {s.id} ({s.name}), content area only:"},
                  {"type": "image", "png": png.getvalue()}]
    return parts


def brief(model: ProductModel, batch: list[State], screens: list[str], art: dict[str, Rect]) -> str:
    """The product model for one batch. Its edges start on the batch's screens and may end on any in-scope screen."""
    ids = {s.id for s in batch}
    edges = [e for e in model.edges if e.from_state in ids and e.to_state in screens]
    edge_ids = {e.id for e in edges}
    flows = [{"name": f.name, "edge_ids": [i for i in f.edge_ids if i in edge_ids]} for f in model.flows]
    drawn = [state_brief(s, model.device, art) for s in batch]
    elements = [e for s in drawn for e in s["elements"]]
    data = {
        "app": model.app,
        "image_files": [e["asset"] for e in elements if "asset" in e] + [e["art"]["src"] for e in elements if "art" in e],
        "screens": drawn,
        "edges": [{"id": e.id, "from": e.from_state, "to": e.to_state, "element": e.element_id,
                   "transition": e.transition} for e in edges],
        "flows": [f for f in flows if f["edge_ids"]],
        "cross_screen_values": [v.model_dump() for v in model.cross_screen_values],
    }
    return ("The product model for the screens in your batch. Rects are in CSS px relative to the screen's section "
            "(content coordinates). `tag: false` elements are drawn but carry no data-el. `image_files` is every "
            "image that exists and `edges` every data-edge allowed: never invent another id or file name.\n\n"
            + json.dumps(data, separators=(",", ":")))


def state_brief(state: State, device: Device, art: dict[str, Rect]) -> dict:
    tagged = tagged_ids(state)
    elements = []
    for e in state.elements:
        if not e.in_mock and e.id not in art:
            continue
        item = {"id": e.id, "type": e.type, "text": e.text, "label": e.label, "role": e.role,
                "x": round(e.rect_dp.x, 1), "y": round(e.rect_dp.y, 1), "w": round(e.rect_dp.w, 1), "h": round(e.rect_dp.h, 1),
                "fg": e.fg_hex, "bg": e.bg_hex, "font": e.font_guess,
                "text_h": round(e.font_px / device.scale, 1) if e.font_px else None,
                "asset": f"assets/{e.id}.png" if usable_asset(e, state.elements, device) else None, "tag": e.id in tagged,
                "art": {"src": art_src(e.id), "rect": art[e.id].model_dump()} if e.id in art else None}
        elements.append({k: v for k, v in item.items() if v not in (None, "", "unknown")})
    return {"id": state.id, "kind": state.kind, "parent": state.parent_id, "name": state.name,
            "purpose": state.purpose, "elements": elements}


def extract_html(text: str) -> str:
    fenced = re.search(r"```html\s*\n(.*?)```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.lower().find("<!doctype"), text.lower().rfind("</html>")
    if start == -1 or end == -1:
        raise ValueError("the mock builder returned no ```html block")
    return text[start:end + len("</html>")]


def batch_parts(html: str) -> tuple[str, str]:
    """A batch's answer as (its CSS, its sections). Takes the body's inside when the answer is a whole document."""
    css = "\n".join(part.strip() for part in STYLE.findall(html))
    markup = STYLE.sub("", html)
    body = re.search(r"<body\b[^>]*>(.*)</body>", markup, re.S | re.I)
    return css, (body.group(1) if body else markup).strip()


# ---------- one page from every batch ----------

def shared_style(scope: list[State]) -> str:
    """Written once by code for the whole page: the most used fills, text colors and fonts of the in-scope elements
    as CSS variables, and the body in the first of each."""
    elements = [e for s in scope for e in s.elements if e.in_mock]
    palette = {"bg": most_used(e.bg_hex for e in elements) or ["#ffffff"],
               "fg": most_used(e.fg_hex for e in elements) or ["#000000"],
               "font": [f'"{f}",system-ui,sans-serif' for f in fonts_of(scope)] or ["system-ui,sans-serif"]}
    variables = [f"--{name}-{i}:{value}" for name, values in palette.items() for i, value in enumerate(values, 1)]
    return ":root{" + ";".join(variables) + "}\nbody{background:var(--bg-1);font-family:var(--font-1)}"


def fonts_of(scope: list[State]) -> list[str]:
    return most_used(e.font_guess for s in scope for e in s.elements
                     if e.in_mock and e.font_guess != "unknown" and FONT_NAME.fullmatch(e.font_guess))


def most_used(values) -> list[str]:
    return [v for v, _ in Counter(v for v in values if v).most_common(PALETTE_SIZE)]


def page_chars(parts: list[tuple[str, str]]) -> set[str]:
    """Every character the drawn batches hold, entities decoded: the elements' text and what the builder copied from
    the screenshots alone. Tags and CSS syntax are ASCII, which the Latin faces cover, so only written text can ask
    for another face."""
    return set(unescape("".join(css + markup for css, markup in parts)))


def vendor_fonts(ctx: Ctx, mock_dir, families: list[str], chars: set[str]) -> str:
    """Copies each family's Google Fonts CSS (weights 400-700, the faces `chars` needs) and every woff2 file it names into
    mock/assets/fonts/, so rendering never waits on the network. Returns the page's <link> to that CSS, or "" when no
    family was fetched. A family that can't be fetched is left out, traced, and the page falls back to the system
    font stack; this never fails the stage. Every fetch goes through its record, so --replay rebuilds the same fonts
    offline. The fonts ship with LICENSE.txt, each family's license text, which their licenses require; a family
    whose license can't be found isn't shipped."""
    font_dir = mock_dir / "assets" / "fonts"
    if not ctx.replay:
        # A live rerun starts empty: QA's replay key hashes all of mock/assets, so a file the page no longer uses would
        # count. A replay keeps them, as mock.run keeps assets/: one that misses a record leaves done.json's files whole.
        shutil.rmtree(font_dir, ignore_errors=True)
    faces, licenses, skipped = [], [], {}
    for family in families:
        try:
            css = used_faces(fetch_recorded(ctx, FONT_CSS.format(family=quote_plus(family))).decode(), chars)
            license = font_license(ctx, family)
            files = {url: fetch_recorded(ctx, url) for url in dict.fromkeys(FONT_FILE.findall(css))}
            names = {url: hashlib.sha256(data).hexdigest()[:16] + ".woff2" for url, data in files.items()}
            for url, name in names.items():
                css = css.replace(f"url({url})", f"url({name})")
            if outside := [u for u in URL_TARGET.findall(css) if u not in names.values()]:
                raise ValueError(f"its CSS still loads {outside[0]}")
        except (*FETCH_ERRORS, ValueError) as e:
            skipped[family] = str(e)[:100]
            continue
        font_dir.mkdir(parents=True, exist_ok=True)
        for url, data in files.items():
            (font_dir / names[url]).write_bytes(data)
        faces.append(css)
        licenses.append(license)
    if skipped:
        run_trace(ctx.run_dir, stage="mock", step="fonts", decider="code", outcome="error",
                  note=("webfonts skipped, system fonts used: "
                        + "; ".join(f"{f}: {why}" for f, why in skipped.items()))[:300])
    if not faces:
        return ""
    (font_dir / "fonts.css").write_text("\n".join(faces) + "\n")
    (font_dir / "LICENSE.txt").write_text("\n\n".join(licenses) + "\n")
    return '<link rel="stylesheet" href="assets/fonts/fonts.css">'


def font_license(ctx: Ctx, family: str) -> str:
    """The family's license text, headed by the family and where it came from: the first license file Google's font
    repository has for it (a 404 on the others is recorded like any fetch, so --replay takes the same path)."""
    slug = family.lower().replace(" ", "")
    for url in (template.format(slug=slug) for template in FONT_LICENSES):
        try:
            return f"{family}: {url}\n\n{fetch_recorded(ctx, url).decode()}"
        except FETCH_ERRORS:
            continue
    raise OSError(f"no license file for {family} in Google's font repository")


def used_faces(css: str, chars: set[str]) -> str:
    """Google splits a family's faces by unicode-range, most after a /* subset */ comment (CJK faces come numbered and
    uncommented). Keeps the Latin faces always, any face with no range, and each other face whose range covers a
    character the screens show that Latin doesn't: Google declares Latin last, so the browser tries it first and
    would never download another face for a character Latin has. A script the screens use gets its font; no other
    subset is downloaded."""
    faces = [(subset, face, face_ranges(face)) for subset, face in FONT_FACE.findall(css)]
    latin = [r for subset, _, ranges in faces if subset == "latin" for r in ranges or []]
    needed = {p for p in map(ord, chars) if not covers(latin, p)}
    kept = [face for subset, face, ranges in faces
            if subset == "latin" or ranges is None or any(covers(ranges, p) for p in needed)]
    return "\n".join(kept) or css


def face_ranges(face: str) -> list[tuple[int, int]] | None:
    found = UNICODE_RANGE.search(face)
    return [(int(lo, 16), int(hi or lo, 16)) for lo, hi in CODE_POINTS.findall(found.group(1))] if found else None


def covers(ranges: list[tuple[int, int]], point: int) -> bool:
    return any(lo <= point <= hi for lo, hi in ranges)


def fetch_recorded(ctx: Ctx, url: str) -> bytes:
    """A live build fetches and records what it got, the bytes or the error, in its own run's mock/font-records/,
    keyed by URL. --replay reads only that record: no network, and the same fonts (or the same system fallback) as
    the live build. The record belongs to the run, because the same URL can answer another run differently later (a
    failed fetch, a new font version). With no record it stops, like a model call's replay miss."""
    records = ctx.run_dir / "mock" / FONT_RECORDS
    path = records / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
    if ctx.replay:
        if not path.exists():
            raise llm.ReplayMiss(f"--replay: no recorded fetch of {url} (key {path.stem[:12]})")
        record = json.loads(path.read_text())
        if "error" in record:
            raise OSError(record["error"])
        return base64.b64decode(record["data"])
    records.mkdir(parents=True, exist_ok=True)
    try:
        data = fetch_twice(url)
    except FETCH_ERRORS as e:
        path.write_text(json.dumps({"url": url, "error": str(e)}))
        raise
    path.write_text(json.dumps({"url": url, "data": base64.b64encode(data).decode()}))
    return data


def fetch_twice(url: str) -> bytes:
    try:
        return fetch(url)
    except FETCH_ERRORS:
        return fetch(url)


def fetch(url: str) -> bytes:
    """The mock stage's only network call, at build time; tests replace it."""
    request = urllib.request.Request(url, headers={"User-Agent": CHROME_UA})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
        return response.read()


def stitch(style: str, fonts: str, parts: list[tuple[str, str]]) -> str:
    """One page: the fonts link and the shared style once, each batch's own CSS, then every batch's sections in
    order."""
    head = ['<meta charset="utf-8">', fonts, f'<style id="simula-shared">\n{style}\n</style>']
    head += [f'<style data-batch="{n}">\n{css}\n</style>' for n, (css, _) in enumerate(parts, 1) if css]
    body = [markup for _, markup in parts]
    return ("<!doctype html>\n<html><head>\n" + "\n".join(h for h in head if h) + "\n</head><body>\n"
            + "\n".join(body) + "\n</body></html>\n")


class StartTags(HTMLParser):
    """Every start tag in a page: its decoded attributes and where its source text sits."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self._line_starts = [0] + [i + 1 for i, c in enumerate(html) if c == "\n"]
        self.feed(html)
        self.close()

    def handle_starttag(self, name, attrs):
        line, col = self.getpos()
        start, text = self._line_starts[line - 1] + col, self.get_starttag_text()
        self.tags.append({"name": name, "attrs": dict(attrs), "start": start, "end": start + len(text),
                          "self_closing": text.endswith("/>")})


def wire_edges(html: str, model: ProductModel, screens: list[str]) -> str:
    """Code owns every edge. It writes each known data-edge tag's data-transition, puts an in-scope edge the
    builder left out on the tag that already carries its element's data-el, and writes the map of edges that start
    on no element (`gestures`) for the runtime, replacing any map already in the page."""
    edges = {e.id: e for e in model.edges}
    tags = StartTags(html).tags
    placed = {t["attrs"].get("data-edge") for t in tags}
    by_element = {}
    for t in tags:
        if t["attrs"].get("data-el"):
            by_element.setdefault(t["attrs"]["data-el"], t)
    changed = {}
    for e in model.edges:
        tag = by_element.get(e.element_id)
        missing = e.from_state in screens and e.to_state in screens and e.id not in placed
        if missing and tag and "data-edge" not in tag["attrs"]:
            tag["attrs"]["data-edge"] = e.id
            changed[tag["start"]] = tag
    for t in tags:
        edge = edges.get(t["attrs"].get("data-edge"))
        if edge and t["attrs"].get("data-transition") != edge.transition:
            t["attrs"] = _with_transition(t["attrs"], edge.transition)
            changed[t["start"]] = t
    html = ACTIONS_BLOCK.sub("", _rewrite(html, sorted(changed.values(), key=lambda t: t["start"])))
    actions = gestures(model, screens)
    return _insert_before(html, "</body>", ACTIONS_JS % json.dumps(actions, sort_keys=True)) if actions else html


def gestures(model: ProductModel, screens: list[str]) -> dict[str, dict[str, list]]:
    """The in-scope swipe, back and type edges that start on no element, per screen and action: {screen: {action:
    [to_state, transition, text field]}}. The runtime performs each from its gesture. A type edge names the screen's
    first tagged text field, where the typing goes (None when it has none). A second edge with the same action on
    one screen is left out: the explorer doesn't record which way it swiped."""
    states = {s.id: s for s in model.states}
    out = {}
    for e in model.edges:
        if e.element_id or e.action not in GESTURES or e.from_state not in screens or e.to_state not in screens:
            continue
        state = states[e.from_state]
        field = next((x.id for x in state.elements if x.type in TEXT_FIELDS and x.id in tagged_ids(state)), None)
        out.setdefault(e.from_state, {}).setdefault(e.action, [e.to_state, e.transition,
                                                             field if e.action == "type" else None])
    return out


def _with_transition(attrs: dict, transition: str) -> dict:
    out = {}
    for name, value in attrs.items():
        if name != "data-transition":
            out[name] = value
        if name == "data-edge":
            out["data-transition"] = transition
    return out


def _rewrite(html: str, tags: list[dict]) -> str:
    """Re-serializes only the given start tags and leaves every other byte of the page as the builder wrote it."""
    parts, at = [], 0
    for t in tags:
        attrs = "".join(f" {name}" if value is None else f' {name}="{_attr_value(value)}"'
                        for name, value in t["attrs"].items())
        parts += [html[at:t["start"]], f"<{t['name']}{attrs}{' /' if t['self_closing'] else ''}>"]
        at = t["end"]
    return "".join(parts) + html[at:]


def _attr_value(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")


def with_runtime(html: str, root: str) -> str:
    """Adds the code-owned navigation runtime, so every mock moves between screens the same way. Replaces a
    runtime already in the page, so QA and flows can re-apply it after every edit."""
    html = re.sub(r'<style id="simula-runtime">.*?</style>\n?', "", html, flags=re.S)
    html = re.sub(r'<script id="simula-runtime-js">.*?</script>\n?', "", html, flags=re.S)
    html = _insert_before(html, "</head>", RUNTIME_CSS)
    return _insert_before(html, "</body>", RUNTIME_JS % json.dumps(root))


def _insert_before(html: str, tag: str, snippet: str) -> str:
    at = html.lower().rfind(tag)
    return html + snippet if at == -1 else html[:at] + snippet + html[at:]


# ---------- exhibit ----------

def exhibit(ctx: Ctx, model: ProductModel, scope: list[State], groups: list[list[State]], undrawn: dict[str, str],
            html: str, report: ContractReport, plan: Plan) -> str:
    attrs = [t["attrs"] for t in StartTags(html).tags]
    placed = Counter(a["data-el"].split(".")[0] for a in attrs if a.get("data-el"))
    wired = {a["data-edge"] for a in attrs if a.get("data-edge")}
    edges = scope_edges(model, scope)
    lines = [f"# Mock: {model.app}", "",
             f"Contract: **{'PASS' if report.passed else 'FAIL'}** ({len(report.errors)} errors). "
             f"Model spend this stage: ${stage_usd(ctx):.4f}.", "",
             f"Batch plan: {plan.line()}.", "",
             "| Batch | Screens | Result | Worst case |", "|---|---|---|---|"]
    for n, (batch, cost) in enumerate(zip(groups, plan.usd), 1):
        reason = undrawn.get(batch[0].id)
        result = f"not drawn: {reason.replace('|', '/')}" if reason else "drawn"
        lines.append(f"| {n} | {' '.join(s.id for s in batch)} | {result} | ${cost:.2f} |")
    lines += ["", "| Screen | Name | data-el placed / expected | Render |", "|---|---|---|---|"]
    for s in scope:
        lines.append(f"| {s.id} | {s.name} | {placed[s.id]} / {len(tagged_ids(s))} | `mock/renders/{s.id}.png` |")
    lines += ["", f"Edges wired: {len(wired & {e.id for e in edges})} / {len(edges)} in scope."]
    if report.errors:
        lines += ["", "| Error | Screen | Detail |", "|---|---|---|"]
        lines += [f"| {e.kind} | {e.screen or ''} | {e.detail.replace('|', '/')} |" for e in report.errors]
        lines += ["", "Contract errors are fixed in QA round 1, not by regenerating."]
    return "\n".join(lines) + "\n"


def stage_usd(ctx: Ctx) -> float:
    return sum(line.usd for line in read_trace(ctx.run_dir / "trace.jsonl") if line.stage == "mock")
