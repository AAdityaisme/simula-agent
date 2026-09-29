"""Goal 4: slide flows. Code picks the ideas that survived the judge and copies the approved mock for each; one model
call per idea adds the idea's new screens; code draws the simulated ad, taps through every step in Playwright, and
lays out slides for the app's product team, then prints them to PDF."""

import base64
import io
import json
import math
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
from string import Template

from PIL import Image, ImageChops, ImageDraw
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from simula import config, llm, render
from simula.config import ROOT, STAGES
from simula.contracts import (GATES, JUDGMENT, Candidate, CandidatesFile, Decision, DecisionsFile, Edit, Edits,
                              ProductModel, Verdict)
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx
from simula.stages.mock import StartTags, _insert_before, _rewrite, contract_text, with_runtime
from simula.stages.propose import BUCKETS, depths, root_id

PROMPTS = ROOT / "prompts" / "flows"
TEMPLATE = ROOT / "templates" / "slides.html"
FONTS = ROOT / "templates" / "fonts"  # Inter, static Latin instances, SIL OFL 1.1 (OFL.txt beside them)
FONT_WEIGHTS = (400, 700, 800, 900)  # the weights templates/slides.html uses; static, so a PDF embeds TrueType
MAX_IDEAS = 4
MAX_TOKENS = 16000
SURVIVED = ("accept", "conditional")
CLICK_MS = 2000
PLAY_MS = 5000
ART_MIN_PX = 240
SLIDE_W, SLIDE_H = 1280, 720
VIEW_W, VIEW_H = render.VIEWPORT["width"], render.VIEWPORT["height"]
RUNTIME_BLOCKS = (r'<style id="simula-runtime">.*?</style>\n?', r'<script id="simula-runtime-js">.*?</script>\n?')
IDS = re.compile(r"\s*\(?\b(?:s\d{2}(?:\.e\d+)?|c\d{2}|M\d{1,3}|new:[\w-]+)\b\)?")
FAIL_NOTE = "That didn't go through. Nothing was used, and the app is as it was."
NOT_WIRED = "not wired"
REWARD_NOT_SHOWN = "reward not shown"
ACCENT_MIN_CHROMA = 0.25  # 67 real palettes: accents 0.40-0.85, every other color 0.16 or less, grays 0.06 or less
LABEL_PAD = 16  # a reward label's shadow and anti-aliasing reach this far past its box, in CSS px
RENDER_NOISE = 8  # Chromium redraws a blurred glow up to 4 levels off after any style change (measured on a real mock)
CODES = re.compile(r"\b(?:g|c\d)_[a-z_]+\b")

PARTS = ("flow", "why")
WHY_TITLE = "Why this works for your app"
REWARD_RULE = ("In every idea the user chooses to play. The reward comes once, only after the play is verified, and "
               "closing the game or a failed ad uses nothing up.")
VERDICTS = {"accept": "accepted", "conditional": "conditional", "reject": "rejected",
            "needs_human": "waiting on a person"}
ROW_W, ROW_GAP = SLIDE_W - 2 * 64, 28
PHONE_MAX_W, COLUMN_MAX_W, RING_PAD = 160, 220, 5
# the score table's CSS: 12px text on 15px lines, 7px of padding and border a row, the rows' room between the table's
# head and the footer; chars per line run low so a page's estimate runs high
SCORE_LINE_PX, SCORE_ROW_PAD_PX, SCORE_PAGE_PX = 15, 7, 490
SCORE_TITLE_CHARS, SCORE_WHY_CHARS = 60, 72
PLAIN_CHECKS = {
    "g_policy": "a fair, opt-in offer",
    "g_no_cash": "no cash reward",
    "g_no_chat_content": "no chat content needed",
    "g_no_free_removal": "nothing free taken away",
    "g_brand_safety": "a brand-safe place for the offer",
    "c1_revealed_value": "people already value what it gives",
    "c2_evidence": "backed by what was seen in the app",
    "c4_protects_subscription": "protects the subscription",
    "c5_moment": "the right moment",
    "c6_fits_simula": "fits a rewarded game",
    "c7_specific": "specific to this app",
}

FLOW_CSS = """body:not(.simula-rewarded) [data-reward]{display:none!important}
body.simula-rewarded [data-unrewarded]{display:none!important}
body.simula-hide-replaced [data-unrewarded]{visibility:hidden!important}
[data-screen]{isolation:isolate}
.sa-dim{position:absolute;inset:0;background:rgba(8,10,14,.72)}
.sa-card{position:absolute;left:28px;right:28px;top:140px;border-radius:20px;background:var(--bg-1,#fff);color:var(--fg-1,#16181d);padding:18px;font:15px/1.35 var(--font-1,system-ui,-apple-system,Roboto,sans-serif);box-shadow:0 12px 40px rgba(0,0,0,.4)}
.sa-top{display:flex;justify-content:space-between;align-items:center}
.sa-tag{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;opacity:.6}
.sa-close{border:0;background:rgba(127,127,127,.2);color:inherit;border-radius:50%;width:30px;height:30px;font-size:18px;line-height:30px;cursor:pointer}
.sa-game{margin:14px 0 12px;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.sa-game i{aspect-ratio:1;border-radius:12px;background:linear-gradient(135deg,#ffb347,#ff5f6d)}
.sa-game i:nth-child(3n+2){background:linear-gradient(135deg,#43cea2,#185a9d)}
.sa-game i:nth-child(4n){background:linear-gradient(135deg,#a18cd1,#fbc2eb)}
.sa-bar{height:6px;border-radius:3px;background:rgba(127,127,127,.2);overflow:hidden}
.sa-bar i{display:block;height:100%;width:0;background:var(--sa-accent,#22c55e);transition:width 3s linear}
.sa-reward{margin:12px 0;font-weight:600}
.sa-play{width:100%;border:0;border-radius:12px;padding:12px;background:var(--sa-accent,#16181d);color:var(--sa-on-accent,#fff);font:700 16px var(--font-1,system-ui,sans-serif);cursor:pointer}
.sa-cta{width:100%;border:0;background:none;color:inherit;opacity:.7;padding:10px 0 0;font-size:14px;cursor:pointer}
.sa-note{position:absolute;left:24px;right:24px;bottom:90px;z-index:50;background:#16181d;color:#fff;border-radius:12px;padding:12px 14px;font:14px system-ui,sans-serif}
"""

# The simulated rewarded ad follows Simula's SDK lifecycle: the reward is granted on REWARD_VERIFIED only, once;
# CLICKED, CLOSED, EARNED_REWARD, or a failure never grant, and closing or a failure returns to the trigger screen.
FLOW_JS = """(() => {
  const FLOW = __FLOW__;
  const find = id => id && document.querySelector(`[data-screen="${CSS.escape(id)}"]`);
  const ad = find(FLOW.ad);
  const log = {events: [], grants: 0};
  const failures = ['LOAD_FAILED', 'DISPLAY_FAILED', 'REWARD_VERIFICATION_FAILED'];
  const onAd = () => ad && window.simula.state() === FLOW.ad;
  let timers = [];
  function note(text) {
    const el = document.createElement('div');
    el.className = 'sa-note';
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2500);
  }
  function emit(type) {
    log.events.push(type);
    if (type === 'REWARD_VERIFIED' && log.grants === 0) {
      log.grants = 1;
      document.body.classList.add('simula-rewarded');
      if (onAd()) window.simula.go(FLOW.afterAd);
    } else if ((type === 'CLOSED' || failures.includes(type)) && onAd()) {
      timers.forEach(clearTimeout);
      timers = [];
      window.simula.go(FLOW.trigger);
      if (type !== 'CLOSED') note(FLOW.failNote);
    }
  }
  window.simulaAd = {emit, complete: () => { emit('EARNED_REWARD'); emit('REWARD_VERIFIED'); },
                     grants: () => log.grants, events: () => [...log.events]};
  if (!ad) return;
  ad.innerHTML = FLOW.card;
  ad.querySelector('.sa-close').addEventListener('click', () => emit('CLOSED'));
  ad.querySelector('.sa-cta').addEventListener('click', () => emit('CLICKED'));
  ad.querySelector('.sa-play').addEventListener('click', () => {
    ad.querySelector('.sa-bar i').style.width = '100%';
    timers.push(setTimeout(() => {
      emit('EARNED_REWARD');
      timers.push(setTimeout(() => emit('REWARD_VERIFIED'), 400));
    }, 3000));
  });
  let shown = false;
  new MutationObserver(() => {
    const on = ad.classList.contains('simula-on');
    if (on && !shown) ['LOADED', 'DISPLAYED', 'IMPRESSION'].forEach(emit);
    shown = on;
  }).observe(ad, {attributes: true, attributeFilter: ['class']});
})();"""

REWARDED_JS = "on => document.body.classList.toggle('simula-rewarded', on)"
REWARD_LABELS_JS = """() => [...document.querySelectorAll('[data-reward]')].filter(e => e.checkVisibility())
  .map(e => { const b = e.getBoundingClientRect(); return {text: (e.innerText ?? e.textContent).trim(), box: [b.x, b.y, b.width, b.height]}; })"""
HAS_REPLACED_JS = "() => document.querySelector('[data-unrewarded]') !== null"
HIDE_REPLACED_JS = "on => document.body.classList.toggle('simula-hide-replaced', on)"
NOTE_ON_TOP_JS = """() => { const note = document.querySelector('.sa-note');
  if (!note || !note.checkVisibility()) return false;
  const b = note.getBoundingClientRect();
  return note.contains(document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)); }"""

OVERFLOW_JS = """() => [...document.querySelectorAll('.slide')].flatMap((slide, i) => {
  const box = slide.getBoundingClientRect(), footer = slide.querySelector('footer');
  const floor = footer ? footer.getBoundingClientRect().top : box.bottom;
  const name = `slide ${i + 1}` + (slide.dataset.idea ? ` (${slide.dataset.idea} ${slide.dataset.part})` : '');
  const bad = [], lines = [];
  for (const el of slide.querySelectorAll('*')) {
    if (el === footer || footer?.contains(el) || el.contains(footer) || el.closest('.phone, svg') ||
        bad.some(b => b.contains(el)) || !el.checkVisibility()) continue;
    const b = el.getBoundingClientRect(), style = getComputedStyle(el);
    if (!b.width || !b.height) continue;
    const past = Math.max(b.right - box.right, b.bottom - box.bottom, box.left - b.left, box.top - b.top);
    const into = b.bottom - floor;
    const cut = style.overflow !== 'visible' && style.textOverflow !== 'ellipsis' &&
                (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1);
    if (past <= 1 && into <= 1 && !cut) continue;
    bad.push(el);
    const what = (el.className ? '.' + String(el.className).split(' ')[0] : el.tagName.toLowerCase()) +
                 ` "${el.textContent.trim().slice(0, 40)}"`;
    lines.push(`${name}: ${what} ` + (past > 1 ? `runs ${Math.round(past)}px past the slide's edge`
               : into > 1 ? `runs ${Math.round(into)}px into the footer` : 'is cut off'));
  }
  return lines;
})"""


# ---------- inputs and selection ----------

def load_candidates(run_dir: Path) -> dict[str, Candidate]:
    """Every proposed candidate, plus the judge's revised ones (judge/revisions.json, read as a CandidatesFile)."""
    files = [run_dir / "propose" / "candidates.json", run_dir / "judge" / "revisions.json"]
    return {c.id: c for f in files if f.exists() for c in CandidatesFile.model_validate_json(f.read_text()).candidates}


def load_approvals(flows_dir: Path) -> list[str] | None:
    path = flows_dir / "approvals.json"
    return json.loads(path.read_text())["approved"] if path.exists() else None


def survivors(decisions: list[Decision], approvals: list[str] | None) -> list[Decision]:
    """Accepted and CONDITIONAL ideas, best rank first. flows/approvals.json can only narrow the list."""
    picked = sorted((d for d in decisions if d.final in SURVIVED), key=lambda d: -(d.rank_score or 0))
    if approvals is not None:
        picked = [d for d in picked if d.candidate_id in approvals]
    return picked


def select(decisions: list[Decision], approvals: list[str] | None) -> list[Decision]:
    """The survivors the deck draws: the best MAX_IDEAS."""
    return survivors(decisions, approvals)[:MAX_IDEAS]


def mock_source(run_dir: Path) -> Path:
    approved = run_dir / "qa" / "approved"
    return approved if (approved / "index.html").exists() else run_dir / "mock"


def clean(flows_dir: Path) -> None:
    """Clears the last run's output but keeps the human's approvals.json."""
    for child in flows_dir.iterdir():
        if child.name != "approvals.json":
            shutil.rmtree(child) if child.is_dir() else child.unlink()


# ---------- plain words ----------

def app_title(model: ProductModel, key: str) -> str:
    """The app's name for the product team: ProductModel.app_name, as the app's own screens show it, else the config
    key title-cased, never the raw lowercase key."""
    # getattr until #15 (fix-model-hygiene) adds app_name, default "", to this branch's contract; then model.app_name
    return getattr(model, "app_name", None) or key.title()


def caption(c: Candidate) -> str:
    return c.title.removeprefix(f"{BUCKETS.get(c.kind, '')}: ")


def plain(text: str) -> str:
    """Slide text: no ids or check codes a product team would have to decode."""
    text = CODES.sub(lambda m: PLAIN_CHECKS.get(m.group(0), m.group(0)), text)
    return " ".join(IDS.sub("", text).split())


def reward_line(c: Candidate) -> str:
    r = c.reward
    amount = "" if r.amount == 1 else f"{r.amount:g} "
    return f"{amount}{r.unit}, {r.duration}" if r.duration else f"{amount}{r.unit}"


def reach_text(c: Candidate, model: ProductModel) -> str:
    """Where the offer sits and the moment it appears. Depth says where, never how many people reach it: a tab is
    depth 0 like the root, and the trigger narrows who sees it (the audience isn't measured). A modal or sheet is
    placed by the screen it covers."""
    trigger = next((s for s in model.states if s.id == c.trigger_state_id), None)
    popup = trigger is not None and trigger.kind != "screen" and trigger.parent_id is not None
    place = trigger.parent_id if popup else c.trigger_state_id
    if place == root_id(model):
        where = "the first screen people see"
    else:
        where = ("a main tab", "a screen one tap in", "a screen a few taps in")[min(depths(model).get(place, 2), 2)]
    return (f"Reach scenario, not a measurement: the offer sits {'in a pop-up over' if popup else 'on'} {where}, "
            f"and appears only when this happens: {c.trigger_event.rstrip('.')}.")


# ---------- the editor call ----------

def strip_runtime(html: str) -> str:
    for pattern in RUNTIME_BLOCKS:
        html = re.sub(pattern, "", html, flags=re.S)
    return html


def page_root(html: str) -> str:
    return json.loads(re.search(r"const ROOT = (.*?);", html).group(1))


def system_prompt() -> str:
    parts = [p.read_text() for p in sorted(PROMPTS.glob("*.md"))]
    return "\n\n".join(parts + [contract_text()])


def editor_brief(c: Candidate, model: ProductModel, page: str) -> str:
    names = {s.id: s.name for s in model.states}
    steps = [f"{n}. {s.state_id} ({names.get(s.state_id, 'new screen')}): {s.caption}"
             for n, s in enumerate(c.flow_steps, 1)]
    drawn = [f"- {sid} {names.get(sid, '')}" for sid in re.findall(r'<section[^>]*data-screen="([^"]+)"', page)]
    new = list(dict.fromkeys(s.state_id for s in c.flow_steps if s.state_id.startswith("new:")))
    return "\n".join([
        f"Idea {c.id} ({BUCKETS.get(c.kind, c.kind)}): {caption(c)}", "",
        f"Trigger: {c.trigger_event}", f"Starts on: {c.trigger_state_id}", f"Placement of the offer: {c.placement}",
        f"Offer copy (verbatim): {c.offer_copy}", f"Reward: {reward_line(c)}",
        f"If they say no: {c.decline_path}", f"If the ad fails: {c.ad_fail_path}",
        f"When the reward runs out: {c.after_reward}", f"Subscribers: {c.subscriber_treatment}", "",
        "Steps, in order:", *steps, "", f"New screens to add, one section each (one of them is the ad): {', '.join(new)}",
        "", "Screens already in the page:", *drawn, "",
        "The page (code removed its navigation runtime and adds it back after your edits):",
        "```html", page, "```"])


def ask_editor(ctx: Ctx, c: Candidate, model: ProductModel, page: str, budget: llm.Budget) -> Edits | None:
    role = config.roles(ctx.profile)["flows_editor"]
    messages = [{"role": "user", "content": [{"type": "text", "text": editor_brief(c, model, page)}]}]
    try:
        edits, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="flows", step=f"edit:{c.id}",
                            model=role["model"], effort=role.get("effort"), system=system_prompt(), messages=messages,
                            max_tokens=min(MAX_TOKENS, config.models()[role["model"]]["max_out"]), budget=budget,
                            schema=Edits, no_cache=ctx.no_cache, replay=ctx.replay)
    except llm.LLMFailure as e:
        run_trace(ctx.run_dir, stage="flows", step=f"edit:{c.id}", decider="code", outcome=e.outcome,
                  note="edit call failed; the flow is walked on the unedited mock")
        return None
    return edits


def apply_edits(html: str, edits: list[Edit]) -> tuple[str, list[str]]:
    """Applies each edit whose find matches the page exactly once. Returns the page and why each other edit failed."""
    rejected = []
    for edit in edits:
        matches = html.count(edit.find) if edit.find else 0
        if matches == 1:
            html = html.replace(edit.find, edit.replace, 1)
        else:
            rejected.append(f"find matches the page {matches} times ({edit.reason[:80]})")
    return html, rejected


# ---------- the flow's page ----------

def is_art(path: Path) -> bool:
    with Image.open(path) as image:
        return min(image.size) >= ART_MIN_PX


def blur_css(model: ProductModel, assets: Path) -> str:
    """Card art from a screen not rated safe is blurred wherever it is drawn (a new flow screen can reuse it on a safe
    screen), in the flow's mock and on every slide. Assets are named by element id, so the prefix is the source
    screen."""
    risky = {s.id for s in model.states if s.content_rating != "safe"}
    art = [p.name for p in sorted(assets.glob("*.png")) if p.name.split(".")[0] in risky and is_art(p)]
    if not art:
        return ""
    images = [f'img[src="assets/{name}"],[style*="{name}"]' for name in art]
    return f":is({','.join(images)}){{filter:blur(14px)!important}}\n"


def with_flow_css(html: str, css: str) -> str:
    return _insert_before(html, "</head>", f'<style id="simula-flow">\n{FLOW_CSS}{css}</style>\n')


def decline_edges(html: str, first: str, known: set[str]) -> tuple[str, int]:
    """Code owns transitions: a new control that returns to the flow's first step is a way of saying no, so it goes
    back. Returns the page and how many controls code relabeled."""
    tags = [t for t in StartTags(html).tags
            if (edge := t["attrs"].get("data-edge")) and edge not in known and edge.split(">")[-1] == first
            and t["attrs"].get("data-transition") != "back"]
    for t in tags:
        t["attrs"] = {**t["attrs"], "data-transition": "back"}
    return _rewrite(html, tags), len(tags)


def rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def text_on(color: str) -> str:
    """Black or white, whichever has the higher WCAG contrast on the color."""
    r, g, b = (v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb(color))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#000" if (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) else "#fff"


def ad_palette_css(page: str) -> str:
    """The ad card's accent: the most colorful color in the mock's :root palette (the app's most used fills and
    text colors), with black or white text on it; "" when the palette is all grays or absent. Colorful is chroma, the
    RGB spread, which stays low near white and black where HLS saturation runs to 1. The card takes the app's
    background, text color, and font from the same :root either way."""
    block = re.search(r":root\{([^}]*)\}", page)
    colors = re.findall(r"--(?:bg|fg)-\d+:\s*(#[0-9a-fA-F]{6})\b", block.group(1)) if block else []
    vivid = [(max(rgb(color)) - min(rgb(color)), color) for color in colors]
    vivid = [(chroma, color) for chroma, color in vivid if chroma >= ACCENT_MIN_CHROMA]
    if not vivid:
        return ""
    accent = max(vivid)[1]
    return f".sa-card{{--sa-accent:{accent};--sa-on-accent:{text_on(accent)}}}\n"


def ad_card(c: Candidate) -> str:
    tiles = "<i></i>" * 9
    return ('<div class="sa-dim"></div><div class="sa-card"><div class="sa-top"><span class="sa-tag">Sponsored game'
            '</span><button class="sa-close" aria-label="Close">×</button></div>'
            f'<div class="sa-game">{tiles}</div><div class="sa-bar"><i></i></div>'
            f'<div class="sa-reward">Finish to get: {escape(reward_line(c))}</div>'
            '<button class="sa-play">Play</button><button class="sa-cta">Learn more</button></div>')


def ad_screen(html: str, step_ids: list[str]) -> str | None:
    """The new screen the editor marked data-ad, preferring one that is a flow step."""
    ads = [t["attrs"]["data-screen"] for t in StartTags(html).tags
           if "data-ad" in t["attrs"] and t["attrs"].get("data-screen")]
    return next((a for a in ads if a in step_ids), ads[0] if ads else None)


def ad_index(step_ids: list[str], ad: str | None) -> int:
    """Where the ad sits in the steps; without a marked ad, where the proposer's steps usually put it."""
    if ad in step_ids:
        return step_ids.index(ad)
    return max(1, min(2, len(step_ids) - 1))


def flow_js(c: Candidate, ad: str | None, after_ad: str) -> str:
    flow = {"trigger": c.flow_steps[0].state_id, "ad": ad, "afterAd": after_ad, "card": ad_card(c),
            "failNote": FAIL_NOTE}
    data = json.dumps(flow).replace("</", "<\\/")
    return f'<script id="simula-flow-js">\n{FLOW_JS.replace("__FLOW__", data)}\n</script>\n'


def mark_ad(html: str, sid: str, c: Candidate) -> tuple[str, str]:
    """Makes sid the ad screen, drawn over the trigger screen: marks the editor's section for it, or adds an empty
    one. Code draws the ad inside either way. Returns the page and which of the two it did."""
    parent = c.flow_steps[0].state_id
    tag = next((t for t in StartTags(html).tags if t["name"] == "section" and t["attrs"].get("data-screen") == sid),
               None)
    if tag:
        attrs = " data-ad" + ("" if "data-parent" in tag["attrs"] else f' data-parent="{parent}"')
        return html[:tag["end"] - 1] + attrs + html[tag["end"] - 1:], "marked the editor's"
    section = f'<section data-screen="{sid}" data-flow="{c.id}" data-parent="{parent}" data-ad></section>\n'
    return _insert_before(html, "</body>", section), "added the"


def flow_page(original: str, edited: str, c: Candidate, css: str) -> tuple[str, str | None, int, str]:
    """The edited page with the navigation runtime, the flow's CSS (plus css), and the simulated ad. Returns it with
    the ad's screen id, its step index, and what code did when the editor marked no ad screen ("" when it marked
    one)."""
    step_ids = [s.state_id for s in c.flow_steps]
    ad = ad_screen(edited, step_ids)
    at = ad_index(step_ids, ad)
    fallback = ""
    if ad is None and step_ids[at].startswith("new:"):
        ad = step_ids[at]
        edited, fallback = mark_ad(edited, ad, c)
    after_ad = step_ids[at + 1] if ad in step_ids and at + 1 < len(step_ids) else step_ids[0]
    html = with_flow_css(with_runtime(edited, page_root(original)), css)
    return _insert_before(html, "</body>", flow_js(c, ad, after_ad)), ad, at, fallback


# ---------- the tap-through ----------

def state(page) -> str | None:
    return page.evaluate("() => window.simula.state()")


def show(page, sid: str) -> bool:
    return bool(page.evaluate("id => window.simula.go(id)", sid)) and state(page) == sid


def tappable(page, current: str, target: str):
    """A visible element that leads to target, preferring one on the current screen."""
    edge = f'[data-edge$=">{target}"]'
    for selector in (f'[data-screen="{current}"] {edge}', edge):
        for link in page.locator(selector).all():
            if link.is_visible():
                return link
    return None


def advance(page, target: str, ad: str | None) -> tuple[bool, dict | None, str]:
    """Moves one step the way a user would. Returns whether it reached target, the box of what was tapped, and why
    not."""
    current = state(page)
    if current == target:
        return True, None, ""
    if current == ad:
        return play(page, ad, target)
    link = tappable(page, current, target)
    if link is None:
        return False, None, f"no tappable element on {current} leads to {target}"
    box = link.bounding_box()
    try:
        link.click(timeout=CLICK_MS)
    except PlaywrightTimeout:
        return False, None, f"the element leading to {target} can't be tapped (something covers it)"
    return state(page) == target, box, f"the tap led to {state(page)}, not {target}"


def play(page, ad: str, target: str) -> tuple[bool, dict | None, str]:
    """Taps the game's Play button like a user, then runs the page clock past the play and its verification."""
    button = page.locator(f'[data-screen="{ad}"] .sa-play')
    box = button.bounding_box()
    try:
        button.click(timeout=CLICK_MS)
    except PlaywrightTimeout:
        return False, None, "the game's Play button can't be tapped (something covers it)"
    page.clock.run_for(PLAY_MS)
    return state(page) == target, box, f"the verified play led to {state(page)}, not {target}"


def changes(before: bytes, after: bytes) -> Image.Image:
    """The pixels that differ between two captures by more than render noise, white on black."""
    with Image.open(io.BytesIO(before)) as a, Image.open(io.BytesIO(after)) as b:
        diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    return diff.point(lambda level: 255 if level > RENDER_NOISE else 0)


def replaced_shows(page, without: bytes) -> bool:
    """Whether something the reward replaces (data-unrewarded) is painted in the capture taken with the reward off:
    the same capture with only those elements hidden differs. Pixels settle what a hit test can't (an element that
    ignores the pointer, a transparent overlay, one offscreen or covered)."""
    if not page.evaluate(HAS_REPLACED_JS):
        return False
    page.evaluate(HIDE_REPLACED_JS, True)
    hidden = render.screenshot(page, animations="disabled")
    page.evaluate(HIDE_REPLACED_JS, False)
    return changes(without, hidden).getbbox() is not None


def reward_effect(page, granted: Path) -> tuple[bool, list[str] | None]:
    """What granting the reward visibly changes on the screen just captured with it granted: whether anything
    changes, and when everything that changes sits inside reward elements that appeared (a label, a badge), their
    words (None when more than that changes). Something the reward replaces that shows with the reward off is more
    than a label, even when its rewarded form is drawn in the same place."""
    labels = page.evaluate(REWARD_LABELS_JS)
    page.evaluate(REWARDED_JS, False)
    without = render.screenshot(page, animations="disabled")
    replaced = replaced_shows(page, without)
    page.evaluate(REWARDED_JS, True)
    diff = changes(granted.read_bytes(), without)
    if diff.getbbox() is None:
        return False, None
    if replaced:
        return True, None
    scale = diff.width / VIEW_W
    draw = ImageDraw.Draw(diff)
    for x, y, w, h in (label["box"] for label in labels):
        draw.rectangle([(x - LABEL_PAD) * scale, (y - LABEL_PAD) * scale, (x + w + LABEL_PAD) * scale,
                        (y + h + LABEL_PAD) * scale], fill=0)
    return True, None if diff.getbbox() else [label["text"] for label in labels if label["text"]]


def walk(flow_dir: Path, c: Candidate, ad: str | None, ad_at: int) -> tuple[list[dict], dict]:
    """Taps through the flow's steps and screenshots each. A step that won't tap through keeps the last good
    screenshot and is marked not wired. On an existing screen after the ad, it also records what the reward visibly
    changes there (a new screen after the ad exists only once the reward is granted). Returns the shots and what the
    page recorded."""
    screens = flow_dir / "screens"
    shots, last = [], None
    with render.open_mock(flow_dir) as (page, log):
        page.clock.install()
        for i, step in enumerate(c.flow_steps):
            if i == 0:
                wired, tap, why = show(page, step.state_id), None, f"{step.state_id} isn't in the page"
            else:
                wired, tap, why = advance(page, step.state_id, ad)
            if i == ad_at and step.state_id != ad:
                wired, why = False, f"{step.state_id} isn't the ad screen (no step is marked data-ad)"
            if wired and i > ad_at and not page.evaluate("() => window.simulaAd.grants()"):
                wired, why = False, "no verified play came before this step, so the reward isn't showing"
            if wired and shots:
                shots[-1]["tap"] = tap
            if wired:
                last = f"step-{i}.png"
                render.screenshot(page, path=screens / last, animations="disabled")
            shown, labels = (reward_effect(page, screens / last) if wired and i > ad_at
                             and not step.state_id.startswith("new:") else (None, None))
            shots.append({"state_id": step.state_id, "caption": step.caption, "png": last, "wired": wired,
                          "tap": None, "why": "" if wired else why, "reward_shown": shown, "reward_labels": labels})
        recorded = page.evaluate("() => window.simulaAd ? {grants: simulaAd.grants(), events: simulaAd.events()} "
                                 ": {grants: 0, events: []}")
        recorded["console"] = log["console"]
    return shots, recorded


def words(text: str) -> str:
    """Text as its words in any script, so the copy check ignores punctuation, case, and line breaks."""
    return " ".join(re.findall(r"\w+", text.casefold()))


def reach(page, steps: list[str], ad: str | None) -> str:
    """Shows the first step, then taps through the rest the way a user would. Returns why it stopped short ("" when
    it got to the last one)."""
    if not show(page, steps[0]):
        return f"{steps[0]} isn't in the page"
    for target in steps[1:]:
        reached, _, why = advance(page, target, ad)
        if not reached:
            return why
    return ""


def back_where_it_started(page, first: str, action: str) -> str:
    """Why the app isn't back on the first step with nothing granted and no reward showing ("" when it is)."""
    grants, reward = page.evaluate("() => [window.simulaAd.grants(), [...document.querySelectorAll("
                                   "'[data-reward]')].some(e => e.checkVisibility())]")
    if state(page) != first:
        return f"{action} led to {state(page)}, not back to {first}"
    return f"{action} granted the reward" if grants or reward else ""


def visible(page, selector: str) -> list:
    return [el for el in page.locator(selector).all() if el.is_visible()]


def walk_decline(flow_dir: Path, c: Candidate, ad: str | None, ad_at: int,
                 known: set[str]) -> tuple[bool | None, str]:
    """The second, short walk: reach the offer, check it shows the offer copy, tap the offer's own control that goes
    back (one whose edge isn't in the model's `known` edges; the app's own close only when the offer has none), and
    check the app is as it was. Returns whether the copy was shown (None when the offer couldn't be reached) and why
    saying no failed ("" when it worked)."""
    steps = [s.state_id for s in c.flow_steps]
    with render.open_mock(flow_dir) as (page, _):
        page.clock.install()
        if why := reach(page, steps[:ad_at], ad):
            return None, f"couldn't reach the offer: {why}"
        offer = state(page)
        copy = words(c.offer_copy)
        shown = bool(copy) and copy in words(page.locator(f'[data-screen="{offer}"]').inner_text())
        goes_back = visible(page, f'[data-screen="{offer}"] [data-edge][data-transition="back"]')
        back = [el for el in goes_back if el.get_attribute("data-edge") not in known] or goes_back
        if not back:
            return shown, f"the offer on {offer} has no visible control that goes back"
        try:
            back[0].click(timeout=CLICK_MS)
        except PlaywrightTimeout:
            return shown, "the offer's control that goes back can't be tapped (something covers it)"
        return shown, back_where_it_started(page, steps[0], "saying no")


def walk_failed_ad(flow_dir: Path, c: Candidate, ad: str | None, ad_at: int) -> str:
    """The third walk: reach the ad, fail it the way the SDK reports a load that failed, and check the app is as it
    was, with a note on top saying nothing was used. Returns why it isn't ("" when it is)."""
    steps = [s.state_id for s in c.flow_steps]
    with render.open_mock(flow_dir) as (page, _):
        page.clock.install()
        if (why := reach(page, steps[:ad_at + 1], ad)) or state(page) != ad:
            return f"couldn't reach the ad: {why or 'no step is the ad screen'}"
        page.evaluate("() => window.simulaAd.emit('LOAD_FAILED')")
        if why := back_where_it_started(page, steps[0], "a failed ad"):
            return why
        return "" if page.evaluate(NOTE_ON_TOP_JS) else "a failed ad shows no note that nothing was used"


def screenshot_before(flow_dir: Path, sid: str) -> str | None:
    with render.open_mock(flow_dir) as (page, _):
        if not show(page, sid):
            return None
        render.screenshot(page, path=flow_dir / "screens" / "before.png", animations="disabled")
    return "before.png"


def build_flow(ctx: Ctx, model: ProductModel, source: Path, c: Candidate, decision: Decision,
               edits: Edits | None) -> dict:
    flow_dir = ctx.run_dir / "flows" / c.id
    (flow_dir / "screens").mkdir(parents=True)
    if (source / "assets").exists():
        shutil.copytree(source / "assets", flow_dir / "assets")
    original = (source / "index.html").read_text()
    css = blur_css(model, flow_dir / "assets") + ad_palette_css(original)
    (flow_dir / "index.html").write_text(with_flow_css(original, css))
    before = screenshot_before(flow_dir, c.flow_steps[0].state_id)

    edited, rejected = apply_edits(strip_runtime(original), edits.edits if edits else [])
    applied = len(edits.edits) - len(rejected) if edits else 0
    run_trace(ctx.run_dir, stage="flows", step=f"edits:{c.id}", decider="code", outcome="ok" if applied else "error",
              note=f"{applied} applied, {len(rejected)} rejected" + (f"; first: {rejected[0]}" if rejected else ""))
    known = {e.id for e in model.edges}
    edited, relabeled = decline_edges(edited, c.flow_steps[0].state_id, known)
    if relabeled:
        run_trace(ctx.run_dir, stage="flows", step=f"edits:{c.id}", decider="code",
                  note=f"{relabeled} new control(s) that return to the first step now go back")
    html, ad, ad_at, fallback = flow_page(original, edited, c, css)
    if fallback:
        run_trace(ctx.run_dir, stage="flows", step=f"edits:{c.id}", decider="code",
                  note=f"no screen was marked data-ad; code {fallback} ad screen at step {ad_at + 1} ({ad})")
    (flow_dir / "index.html").write_text(html)

    shots, recorded = walk(flow_dir, c, ad, ad_at)
    copy_shown, decline = walk_decline(flow_dir, c, ad, ad_at, known)
    if decline or copy_shown is False:
        problem = decline or "the offer screen doesn't show the offer copy"
        run_trace(ctx.run_dir, stage="flows", step=f"decline:{c.id}", decider="code", outcome="error",
                  note=f"{NOT_WIRED}: {problem}"[:300])
    if ad_fail := walk_failed_ad(flow_dir, c, ad, ad_at):
        run_trace(ctx.run_dir, stage="flows", step=f"failed-ad:{c.id}", decider="code", outcome="error",
                  note=f"{NOT_WIRED}: {ad_fail}"[:300])
    for n, shot in enumerate(shots):
        if not shot["wired"]:
            run_trace(ctx.run_dir, stage="flows", step=f"walk:{c.id}", decider="code", outcome="error",
                      note=f"step {n + 1} {NOT_WIRED}: {shot['why']}"[:300])
        elif shot["reward_shown"] is False:
            run_trace(ctx.run_dir, stage="flows", step=f"walk:{c.id}", decider="code", outcome="error",
                      note=(f"step {n + 1} {REWARD_NOT_SHOWN}: nothing on {shot['state_id']} changes when the "
                            "reward is granted")[:300])
    if recorded["console"]:
        run_trace(ctx.run_dir, stage="flows", step=f"walk:{c.id}", decider="code", outcome="error",
                  note=f"{len(recorded['console'])} console errors; first: {recorded['console'][0]}"[:300])
    return {"candidate": c, "decision": decision, "before": before, "shots": shots, "ad_at": ad_at,
            "grants": recorded["grants"], "events": recorded["events"], "applied": applied, "rejected": rejected,
            "copy_shown": copy_shown, "decline": decline, "ad_fail": ad_fail}


# ---------- slides ----------

def row_geometry(count: int) -> tuple[float, float]:
    """(column width, tallest phone) in slide px for a row of count phones. Columns share the row, and a phone is
    never wider than its column or PHONE_MAX_W; the slide's CSS shrinks every phone alike when captions need room."""
    column = min(COLUMN_MAX_W, (ROW_W - ROW_GAP * (count - 1)) / count)
    return column, min(PHONE_MAX_W, column) * VIEW_H / VIEW_W


def ring_html(tap: dict) -> str:
    """A ring on what the user taps, placed in the screenshot's own proportions."""
    x, y = (tap["x"] - RING_PAD) / VIEW_W, (tap["y"] - RING_PAD) / VIEW_H
    w, h = (tap["width"] + 2 * RING_PAD) / VIEW_W, (tap["height"] + 2 * RING_PAD) / VIEW_H
    return f'<span class="ring" style="left:{x:.2%};top:{y:.2%};width:{w:.2%};height:{h:.2%}"></span>'


def step_label(i: int, ad_at: int) -> str:
    return "When it appears" if i == 0 else "The offer" if i < ad_at else "The ad plays" if i == ad_at else \
        "What they get"


def labels_text(labels: list[str]) -> str:
    """What the screen shows when all the reward adds is labels: said as that, not as the effect the idea promises."""
    if not labels:
        return "A label appears."
    quoted = ", ".join(f"“{label}”" for label in labels)
    return f"A label appears: {quoted}." if len(labels) == 1 else f"Labels appear: {quoted}."


def row_phones(flow: dict) -> list[dict]:
    """The flow slide's phones in order: the screen as it is today, then every step the walk took. Each has a label,
    a caption, what the user taps on it, and a red flag for anything the walks couldn't show."""
    c, shots, at = flow["candidate"], flow["shots"], flow["ad_at"]
    phones = [{"label": "Today", "text": shots[0]["caption"], "png": flow["before"], "tap": None,
               "flags": [] if flow["before"] else [NOT_WIRED]}]
    same_screen = len(shots) > 1 and shots[1]["state_id"] == shots[0]["state_id"]
    for i, shot in enumerate(shots):
        tap = shot["tap"] or (shots[1]["tap"] if i == 0 and same_screen else None)
        flags = ([] if shot["wired"] else [NOT_WIRED]) + ([REWARD_NOT_SHOWN] if shot["reward_shown"] is False else [])
        if i == 0:
            text = c.trigger_event
        elif shot["reward_labels"] is not None:
            text = labels_text(shot["reward_labels"])
        else:
            text = shot["caption"]
        phones.append({"label": step_label(i, at), "text": text, "png": shot["png"], "tap": tap, "flags": flags})
    phones[at]["flags"] += (["copy not on the screen"] if flow["copy_shown"] is False else []) + \
                           ([f"saying no: {NOT_WIRED}"] if flow["decline"] else [])  # the screen that makes the offer
    if flow["ad_fail"] and at + 1 < len(phones):
        phones[at + 1]["flags"].append(f"a failed ad: {NOT_WIRED}")
    return phones


def step_html(number: int, phone: dict, prefix: str) -> str:
    image = f'<img src="{prefix}/screens/{phone["png"]}" alt="">' if phone["png"] else ""
    ring = ring_html(phone["tap"]) if phone["tap"] else ""
    badge = f'<span class="notwired">{NOT_WIRED}</span>' if NOT_WIRED in phone["flags"] else ""
    flags = "".join(f'<span class="flag">{escape(flag)}</span>' for flag in phone["flags"])
    return (f'<div class="step"><div class="shot"><span class="n">{number}</span>'
            f'<div class="phone">{image}{ring}{badge}</div></div>'
            f'<div class="cap"><b>{escape(phone["label"])}</b>{flags}<p>{escape(plain(phone["text"]))}</p></div></div>')


def row_html(phones: list[dict], prefix: str) -> str:
    """Numbered phones in one row, arrows between them, a caption under each."""
    column, height = row_geometry(len(phones))
    steps = "".join(step_html(n, phone, prefix) for n, phone in enumerate(phones, 1))
    return (f'<div class="row" style="--gap:{ROW_GAP}px;--col:{column:.0f}px;--h:{height:.0f}px;'
            f'--aspect:{VIEW_W}/{VIEW_H}">{steps}</div>')


def slide_html(flow: dict, part: str, body: str) -> str:
    """One of an idea's slides: a header with the idea's labels, then the body (HTML)."""
    c, decision = flow["candidate"], flow["decision"]
    kind = "existing" if c.kind == "existing_anchor" else "change"
    labels = '<span class="chip conditional">Conditional</span>' if decision.final == "conditional" else ""
    if cost_question(c):
        labels += f'<span class="chip conditional">Cost check: {c.economics.verdict}</span>'
    idea = "" if part == "flow" else escape(plain(caption(c)))  # the flow slide's title already names the idea
    return (f'<section class="slide main" data-part="{part}" data-idea="{c.id}">'
            f'<header><span class="chip {kind}">{escape(BUCKETS.get(c.kind, ""))}</span>'
            f'<span class="idea">{idea}</span>{labels}'
            f'<span class="count">{PARTS.index(part) + 1} / {len(PARTS)}</span></header>'
            f"{body}</section>")


def verdicts(decision: Decision, run_dir: Path) -> list[tuple[str, Verdict]]:
    """(judge and round, verdict) for each verdict file the decision cites, e.g. ("judge_1_r1", …)."""
    return [(Path(p).stem.removeprefix(f"{decision.candidate_id}_"),
             Verdict.model_validate_json((run_dir / p).read_text()))
            for p in decision.verdict_paths if (run_dir / p).exists()]


def failed_checks(decision: Decision, run_dir: Path) -> list[tuple[str, str]]:
    """(check, the first failing judge's reason) for every check some judge failed, in check order."""
    failed = {}
    for _, verdict in verdicts(decision, run_dir):
        for key in GATES + JUDGMENT:
            check = getattr(verdict, key)
            if not check.passed:
                failed.setdefault(key, check.reason)
    return list(failed.items())


def cost_question(c: Candidate) -> str | None:
    """The cost line's verdict in plain words, with no numbers (the judge's exhibit has them). A CONDITIONAL cost of
    zero is one the line couldn't count."""
    e = c.economics
    if e is None or e.verdict == "PASS":
        return None
    if e.verdict == "FAIL":
        return "it may cost more to serve than a view earns"
    if e.cost_2k == 0:
        return "what it costs to serve wasn't observed"
    return "a view pays for what it costs to serve only where ad prices are high"


def is_fallback(decision: Decision, run_dir: Path, none_accepted: bool) -> bool:
    """The judge's fallback pick: nothing was accepted, so the closest idea goes on as CONDITIONAL with the checks it
    missed."""
    return decision.final == "conditional" and none_accepted and bool(failed_checks(decision, run_dir))


def condition(decision: Decision, run_dir: Path, none_accepted: bool) -> tuple[str, str] | None:
    """A CONDITIONAL idea's heading and sentence about the checks it missed, in plain words; None when it missed none
    (its condition is then its cost line, which the cost box says)."""
    if decision.final != "conditional":
        return None
    failed = failed_checks(decision, run_dir)
    names = ", ".join(PLAIN_CHECKS[k] for k, _ in failed)
    if is_fallback(decision, run_dir, none_accepted) and not any(k in GATES for k, _ in failed):
        return ("The closest idea, not a recommendation:",
                f"No idea passed every check. This one passes every safety check but not {names} ({failed[0][1]}).")
    if failed:
        return "Not every check passed:", f"It didn't pass {names} ({failed[0][1]})."
    if decision.checks_passed < decision.checks_total:
        return ("Not every check passed:", (f"It passed {decision.checks_passed} of {decision.checks_total} checks; "
                                            "the score pages at the end show which."))
    return None


def saying_no(flow: dict) -> str:
    """What saying no does, as the decline walk found it, for every slide that says so."""
    return "saying no doesn't bring them back yet" if flow["decline"] else "saying no changes nothing"


def why_html(flow: dict, model: ProductModel, run_dir: Path, none_accepted: bool) -> str:
    c, decision = flow["candidate"], flow["decision"]
    gets = reward_line(c)  # the offer's terms: the flow slide already shows what the screen does after the play
    blocks = [("What the user gets", gets[:1].upper() + gets[1:] + "."),
              ("What the app gets", c.rationale),
              ("What it never costs them", f"Nothing free is taken away, and {saying_no(flow)}. "
                                           f"{c.subscriber_treatment}")]
    html = "".join(f'<div class="why-block"><b>{escape(label)}</b><p>{escape(plain(text))}</p></div>'
                   for label, text in blocks)
    html += f'<p class="reach">{escape(plain(reach_text(c, model)))}</p>'
    if note := condition(decision, run_dir, none_accepted):
        html += f'<div class="condition"><b>{note[0]}</b> {escape(plain(note[1]))}</div>'
    if cost := cost_question(c):
        html += (f'<div class="condition"><b>Cost check ({c.economics.verdict}):</b> {escape(cost)}. '
                 "The review's cost line has the numbers.</div>")
    return f'<div class="why">{html}</div>'


def idea_slides(flow: dict, model: ProductModel, run_dir: Path, none_accepted: bool) -> list[str]:
    """An idea's two slides: its whole flow in one row of phones, then why the system thinks it's a good idea
    (none_accepted: the judge accepted no idea, so a CONDITIONAL one is its fallback pick)."""
    c = flow["candidate"]
    label, new = (("The new part", c.adds) if c.kind == "product_change" and c.adds
                  else ("Where the offer appears", c.placement))
    gets = f"<b>They get:</b> {escape(plain(reward_line(c)))} · <b>How often:</b> {escape(plain(c.frequency_cap))}"
    body = (f'<div class="flow"><h2>{escape(plain(caption(c)))}</h2><div class="new">'
            f"<p><b>{label}:</b> {escape(plain(new))}</p>"
            f"<p><b>The offer says:</b> “{escape(plain(c.offer_copy))}” {saying_no(flow).capitalize()}.</p></div>"
            f"{row_html(row_phones(flow), c.id)}<footer>{gets}</footer></div>")
    runs_out = f"<footer><b>When the reward runs out:</b> {escape(plain(c.after_reward))}</footer>" \
        if c.after_reward else ""
    why = f"<h2>{WHY_TITLE}</h2>{why_html(flow, model, run_dir, none_accepted)}{runs_out}"
    return [slide_html(flow, "flow", body), slide_html(flow, "why", why)]


def cover_html(app: str, flows: list[dict], unbuilt: int = 0, status: list[str] = (), fallbacks: int = 0,
               cut: int = 0, unbuilt_fallbacks: int = 0) -> str:
    """The overview: every idea in the deck, how to read it, the reward rule every idea follows, whether an idea is
    only the judge's fallback pick (drawn, or not drawn), how many survivors the cap left out, and status lines for
    anything an earlier stage couldn't finish. `unbuilt` counts only ideas that passed the review."""
    items = "".join(f'<li><span class="chip {"existing" if f["candidate"].kind == "existing_anchor" else "change"}">'
                    f'{escape(BUCKETS.get(f["candidate"].kind, ""))}</span>{escape(plain(caption(f["candidate"])))}</li>'
                    for f in flows)
    notes = []
    if flows:
        notes += ["Each idea takes two slides: its whole flow, step by step, then why it works. The score pages at "
                  "the end score every idea the review saw.", REWARD_RULE]
    if fallbacks and unbuilt_fallbacks:
        notes.append(f"No idea passed every check, so the closest are marked as not a recommendation; "
                     f"{unbuilt_fallbacks} of them couldn't be drawn, and the score pages at the end say why.")
    elif fallbacks:
        notes.append("No idea passed every check, so the closest is drawn and marked as not a recommendation.")
    elif unbuilt_fallbacks:
        notes.append("No idea passed every check; the closest couldn't be drawn, and the score pages at the end "
                     "say why.")
    if cut:
        notes.append(f"{cut} more idea(s) passed the review; the deck draws only the top {MAX_IDEAS} by rank, and the "
                     "score pages at the end score the rest.")
    if unbuilt:
        notes.append(f"{unbuilt} {'more ' if flows else ''}idea(s) passed the review but couldn't be drawn; the score "
                     "pages at the end say why.")
    if not flows and not unbuilt and not unbuilt_fallbacks:
        notes.append("No idea passed the review. The score pages at the end show every idea's score and why.")
    body = (f"<ol>{items}</ol>" if flows else "") + "".join(f"<p class='how'>{escape(n)}</p>" for n in [*notes, *status])
    return f'<section class="slide cover"><h1>Rewarded-ad ideas for {escape(app)}</h1>{body}</section>'


def score_reason(d: Decision, c: Candidate | None, run_dir: Path) -> str:
    """Why an idea got its verdict, in one line: code's drop reason, the checks a judge failed, the cost question,
    and code's flags."""
    if c is None:
        return "its candidate isn't in propose/candidates.json or judge/revisions.json"
    parts = [f"dropped by code: {c.dropped_reason}"] if c.dropped_reason else []
    if failed := failed_checks(d, run_dir):
        parts.append("didn't pass " + ", ".join(PLAIN_CHECKS[k] for k, _ in failed))
    if cost := cost_question(c):
        parts.append(f"cost check: {cost}")
    if d.revision_of:
        parts.append(f"a revision of {d.revision_of}")
    parts += [f"flagged by code: {flag}" for flag in c.flags]
    return "; ".join(parts) or "passed every check"


def row_px(*cells: tuple[str, int]) -> int:
    """A score row's height in px, from each (text, chars per line) cell's wrapped line count."""
    return SCORE_ROW_PAD_PX + SCORE_LINE_PX * max(1, *(math.ceil(len(text) / chars) for text, chars in cells))


def score_pages(rows: list[tuple[str, int]], budget: int) -> tuple[list[list[str]], int]:
    """Rows (html, height) packed in order onto as few pages as fit the budget each; also the last page's height."""
    pages, used = [[]], 0
    for html, height in rows:
        if pages[-1] and used + height > budget:
            pages, used = [*pages, []], 0
        pages[-1].append(html)
        used += height
    return pages, used


def score_slides(decisions: list[Decision], candidates: dict[str, Candidate], not_built: list[tuple[Decision, str]],
                 run_dir: Path) -> list[str]:
    """Every idea the review scored: verdict, checks passed, and one line on why, packed onto as few pages as fit.
    The judge's exhibit has every check's reasoning; ideas that passed but couldn't be drawn are listed last."""
    rows = []
    for d in decisions:
        c = candidates.get(d.candidate_id)
        title, why = caption(c) if c else "", score_reason(d, c, run_dir)
        rows.append((f"<tr><td>{escape(d.candidate_id)}</td><td>{escape(title)}</td><td>{VERDICTS[d.final]}</td>"
                     f"<td>{d.checks_passed}/{d.checks_total}</td><td>{escape(why)}</td></tr>",
                     row_px((title, SCORE_TITLE_CHARS), (why, SCORE_WHY_CHARS))))
    pages, used = score_pages(rows, SCORE_PAGE_PX)
    head = "<tr><th>Idea</th><th>Title</th><th>Verdict</th><th>Checks</th><th>Why</th></tr>"
    bodies = [f'<table class="scores">{head}{"".join(page)}</table>' for page in pages]
    if not_built:
        items = [f"{d.candidate_id} · {caption(candidates[d.candidate_id]) if d.candidate_id in candidates else ''}: "
                 f"not built: {why}" for d, why in not_built]
        block = f'<h3>Not built</h3><ul class="not-built">{"".join(f"<li>{escape(i)}</li>" for i in items)}</ul>'
        height = 2 * SCORE_LINE_PX + sum(row_px((item, SCORE_TITLE_CHARS + SCORE_WHY_CHARS)) for item in items)
        bodies = [*bodies, block] if used + height > SCORE_PAGE_PX else [*bodies[:-1], bodies[-1] + block]
    return [f'<section class="slide scores"><h2>Every idea the review scored'
            f'{f" ({n} of {len(bodies)})" if len(bodies) > 1 else ""}</h2><div class="score-body">{body}</div>'
            "<footer>Every judge's reasoning for every check, and every cost line with its numbers: "
            "<b>exhibits/06-judge.md</b>. Tap through any idea: <b>flows/&lt;idea&gt;/index.html</b>.</footer></section>"
            for n, body in enumerate(bodies, 1)]


def unfinished_stages(run_dir: Path) -> list[str]:
    """A cover line for each earlier stage whose done.json says it finished only part of its work, with its reasons.
    The outcome is read as JSON because this branch's DoneMarker predates it (pr0b-shared adds StageOutcome)."""
    lines = []
    for stage in STAGES[:STAGES.index("flows")]:
        path = run_dir / stage / "done.json"
        outcome = json.loads(path.read_text()).get("outcome") if path.exists() else None
        if outcome and outcome.get("status") == "partial":
            lines.append(f"The {stage} step finished only part of its work: {'; '.join(outcome.get('reasons', []))}.")
    return lines


def deck(ctx: Ctx, model: ProductModel, flows: list[dict], not_built: list[tuple[Decision, str]],
         decisions: list[Decision], candidates: dict[str, Candidate], cut: int = 0) -> str:
    app = app_title(model, ctx.app["name"])
    none_accepted = not any(d.final == "accept" for d in decisions)
    fallbacks = sum(is_fallback(f["decision"], ctx.run_dir, none_accepted) for f in flows)
    unbuilt_fallbacks = sum(is_fallback(d, ctx.run_dir, none_accepted) for d, _ in not_built)
    slides = [cover_html(app, flows, len(not_built) - unbuilt_fallbacks, unfinished_stages(ctx.run_dir), fallbacks, cut,
                         unbuilt_fallbacks)]
    for flow in flows:
        slides += idea_slides(flow, model, ctx.run_dir, none_accepted)
    slides += score_slides(decisions, candidates, not_built, ctx.run_dir)
    watermark = ('<div class="watermark">FIXTURE TEST DATA · not a deliverable</div>'
                 if ctx.run_dir.name.endswith("-fixture") else "")
    return deck_html(f"Rewarded-ad ideas for {escape(app)}", watermark + "\n".join(slides))


def deck_html(title: str, slides: str) -> str:
    """The slide template filled in. Its font is inlined, so the file lays out, and the overflow check measures, the
    same on every machine: a system font differs by OS and broke lines differently on Linux."""
    faces = "".join(f'@font-face{{font-family:Inter;font-weight:{weight};src:url(data:font/woff2;base64,'
                    f'{base64.b64encode((FONTS / f"inter-latin-{weight}.woff2").read_bytes()).decode()}) format("woff2")}}\n'
                    for weight in FONT_WEIGHTS)
    return Template(TEMPLATE.read_text()).substitute(title=title, slides=slides, fonts=faces)


def overflows(page) -> list[str]:
    """Where the rendered deck's text or boxes run past a slide's edge, into its footer, or get cut off: one line
    per outermost element, naming its slide, measured once the deck's font has loaded and its why slides have fitted
    their text. Screenshots and deliberate ellipses don't count."""
    page.wait_for_function("'fitted' in document.documentElement.dataset")
    return page.evaluate(OVERFLOW_JS)


def write_pdf(slides: Path) -> list[str]:
    """Prints the deck to PDF beside it and returns where its layout overflows."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H})
        page.goto(slides.as_uri())
        page.wait_for_load_state("networkidle")
        problems = overflows(page)
        page.pdf(path=slides.with_suffix(".pdf"), width=f"{SLIDE_W}px", height=f"{SLIDE_H}px", print_background=True)
        browser.close()
    return problems


# ---------- stage ----------

def reward_text(flow: dict) -> str:
    """The exhibit's word on the reward after the ad: not reached, not shown, shown only on new screens (which the
    on/off check doesn't measure), only a label, or shown."""
    after = list(enumerate(flow["shots"], 1))[flow["ad_at"] + 1:]
    reached = [(n, s) for n, s in after if s["wired"]]
    if not reached:
        return "not reached"
    if missing := [str(n) for n, s in reached if s["reward_shown"] is False]:
        return f"{REWARD_NOT_SHOWN}: step {', '.join(missing)}"
    measured = [(n, s) for n, s in reached if s["reward_shown"]]
    if not measured:
        return "new screen, not measured"
    if all(s["reward_labels"] is not None for _, s in measured):
        return f"only a label: step {', '.join(str(n) for n, _ in measured)}"
    return "yes"


def decline_text(flow: dict) -> str:
    if flow["decline"]:
        return f"{NOT_WIRED}: {flow['decline']}"
    return "ok" if flow["copy_shown"] else "ok; the offer screen doesn't show the offer copy"


def exhibit(flows: list[dict], not_built: list[tuple[Decision, str]], chosen_from: str, source: Path,
            run_dir: Path, usd: float, layout: list[str]) -> str:
    lines = ["# 07 · flows", "", f"{len(flows)} idea(s) drawn ({chosen_from}), from `{source.relative_to(run_dir)}/`. "
             f"Model spend this stage: ${usd:.4f}.", "",
             "| Idea | Verdict | Edits applied / rejected | Steps wired | Grants in the walk | Saying no | A failed ad "
             "| Reward shown | Flow mock |",
             "|---|---|---|---|---|---|---|---|---|"]
    for f in flows:
        c, wired = f["candidate"], sum(s["wired"] for s in f["shots"])
        lines.append(f"| {c.id} · {caption(c)} | {f['decision'].final} | {f['applied']} / {len(f['rejected'])} | "
                     f"{wired} / {len(f['shots'])} | {f['grants']} | {decline_text(f)} | "
                     f"{f'{NOT_WIRED}: ' + f['ad_fail'] if f['ad_fail'] else 'ok'} | {reward_text(f)} | "
                     f"`flows/{c.id}/index.html` |")
    broken = [(f["candidate"].id, n, s) for f in flows for n, s in enumerate(f["shots"], 1) if not s["wired"]]
    if broken:
        lines += ["", f"## {NOT_WIRED.capitalize()}", ""]
        lines += [f"- {cid} step {n} (`{s['state_id']}`): {s['why']}" for cid, n, s in broken]
    unseen = [(f["candidate"].id, n, s) for f in flows for n, s in enumerate(f["shots"], 1) if s["reward_shown"] is False]
    if unseen:
        lines += ["", f"## {REWARD_NOT_SHOWN.capitalize()}", ""]
        lines += [f"- {cid} step {n} (`{s['state_id']}`): nothing on it changes when the reward is granted"
                  for cid, n, s in unseen]
    if not_built:
        lines += ["", "## Not built", ""] + [f"- {d.candidate_id}: {why}" for d, why in not_built]
    if layout:
        lines += ["", "## Layout", ""] + [f"- {problem}" for problem in layout]
    lines += ["", "Deck: `flows/slides.html`, `flows/slides.pdf`."]
    return "\n".join(lines) + "\n"


def unbuildable(c: Candidate | None) -> str | None:
    """Why an idea can't be drawn before anything is paid for, or None."""
    if c is None:
        return "its candidate isn't in propose/candidates.json or judge/revisions.json"
    if not re.fullmatch(r"[\w-]+", c.id):
        return f"its id {c.id!r} isn't a plain name, so it can't name a folder under flows/"
    if len(c.flow_steps) < 2:
        return "its flow has one step, so there is nothing to tap through"
    return None


def run(ctx: Ctx) -> None:
    run_dir, out = ctx.run_dir, ctx.run_dir / "flows"
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    candidates = load_candidates(run_dir)
    out.mkdir(exist_ok=True)
    approvals = load_approvals(out)
    clean(out)
    chosen = select(decisions, approvals)
    cut = survivors(decisions, approvals)[MAX_IDEAS:]
    chosen_from = "narrowed by flows/approvals.json" if approvals is not None else "accepted + conditional"
    past_cap = f"; past the cap of {MAX_IDEAS}, not drawn: {' '.join(d.candidate_id for d in cut)}" if cut else ""
    run_trace(run_dir, stage="flows", step="select", decider="code",
              note=f"{chosen_from}: {' '.join(d.candidate_id for d in chosen) or 'none'}{past_cap}")
    source = mock_source(run_dir)
    page = strip_runtime((source / "index.html").read_text())
    budget = llm.Budget.for_stage("flows", run_dir / "trace.jsonl", ctx.usd_cap)
    not_built = [(d, why) for d in chosen if (why := unbuildable(candidates.get(d.candidate_id)))]
    buildable = [d for d in chosen if not unbuildable(candidates.get(d.candidate_id))]
    with ThreadPoolExecutor(max_workers=max(1, len(buildable))) as pool:
        edits = list(pool.map(lambda d: ask_editor(ctx, candidates[d.candidate_id], model, page, budget), buildable))
    flows = []
    for d, e in zip(buildable, edits):
        try:
            flows.append(build_flow(ctx, model, source, candidates[d.candidate_id], d, e))
        except Exception as error:  # one idea's failure (a hung page, a broken edit) must not cost the whole deck
            shutil.rmtree(out / d.candidate_id, ignore_errors=True)
            not_built.append((d, f"{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ''}"[:300]))
    for d, why in not_built:
        run_trace(run_dir, stage="flows", step=f"build:{d.candidate_id}", decider="code", outcome="error",
                  note=f"not built: {why}"[:300])
    (out / "slides.html").write_text(deck(ctx, model, flows, not_built, decisions, candidates, len(cut)))
    layout = write_pdf(out / "slides.html")
    for problem in layout:
        run_trace(run_dir, stage="flows", step="layout", decider="code", outcome="error", note=problem[:300])
    usd = sum(line.usd for line in read_trace(run_dir / "trace.jsonl") if line.stage == "flows")
    write_exhibit(run_dir, 7, "flows", exhibit(flows, not_built, chosen_from, source, run_dir, usd, layout))
