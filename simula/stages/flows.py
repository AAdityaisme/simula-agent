"""Goal 4: slide flows. Code picks the ideas that survived the judge and copies the approved mock for each; one model
call per idea adds the idea's new screens; code draws the simulated ad, taps through every step in Playwright, and
lays out slides for the app's product team, then prints them to PDF."""

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
from string import Template

from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from simula import config, llm, render
from simula.config import ROOT
from simula.contracts import (GATES, JUDGMENT, Candidate, CandidatesFile, Decision, DecisionsFile, Edit, Edits,
                              ProductModel, Verdict)
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx
from simula.stages.mock import StartTags, _insert_before, contract_text, with_runtime
from simula.stages.propose import BUCKETS, depths

PROMPTS = ROOT / "prompts" / "flows"
TEMPLATE = ROOT / "templates" / "slides.html"
MAX_IDEAS = 4
MAX_TOKENS = 16000
SURVIVED = ("accept", "conditional")
CLICK_MS = 2000
ART_MIN_PX = 240
SLIDE_W, SLIDE_H = 1280, 720
VIEW_W, VIEW_H = render.VIEWPORT["width"], render.VIEWPORT["height"]
RUNTIME_BLOCKS = (r'<style id="simula-runtime">.*?</style>\n?', r'<script id="simula-runtime-js">.*?</script>\n?')
IDS = re.compile(r"\s*\(?\b(?:s\d{2}(?:\.e\d+)?|c\d{2}|M\d{1,3}|new:[\w-]+)\b\)?")
FAIL_NOTE = "That didn't go through. Nothing was used, and the app is as it was."
NOT_WIRED = "not wired"

PARTS = {"start": "Where it starts", "change": "What changes", "offer": "The offer", "ad": "The ad plays",
         "value": "What they get", "why": "Why this works for your app"}
CHECK_NAMES = {
    "g_policy": "Gate: opt-in, reward stated first, no penalty for saying no, a failure path",
    "g_no_cash": "Gate: no cash or cash-like reward",
    "g_no_chat_content": "Gate: needs no chat content",
    "g_no_free_removal": "Gate: takes nothing free away",
    "g_brand_safety": "Gate: offer on app chrome, away from unsafe content",
    "c1_revealed_value": "C1 revealed value",
    "c2_evidence": "C2 evidence",
    "c4_protects_subscription": "C4 protects the subscription",
    "c5_moment": "C5 right moment",
    "c6_fits_simula": "C6 fits Simula",
    "c7_specific": "C7 specific to this app",
}

FLOW_CSS = """body:not(.simula-rewarded) [data-reward]{display:none!important}
.sa-dim{position:absolute;inset:0;background:rgba(8,10,14,.72)}
.sa-card{position:absolute;left:28px;right:28px;top:140px;border-radius:20px;background:#fff;color:#16181d;padding:18px;font:15px/1.35 system-ui,-apple-system,Roboto,sans-serif;box-shadow:0 12px 40px rgba(0,0,0,.4)}
.sa-top{display:flex;justify-content:space-between;align-items:center}
.sa-tag{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6b7280}
.sa-close{border:0;background:#eef0f3;border-radius:50%;width:30px;height:30px;font-size:18px;line-height:30px;cursor:pointer}
.sa-game{margin:14px 0 12px;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.sa-game i{aspect-ratio:1;border-radius:12px;background:linear-gradient(135deg,#ffb347,#ff5f6d)}
.sa-game i:nth-child(3n+2){background:linear-gradient(135deg,#43cea2,#185a9d)}
.sa-game i:nth-child(4n){background:linear-gradient(135deg,#a18cd1,#fbc2eb)}
.sa-bar{height:6px;border-radius:3px;background:#eef0f3;overflow:hidden}
.sa-bar i{display:block;height:100%;width:0;background:#22c55e;transition:width 3s linear}
.sa-reward{margin:12px 0;font-weight:600}
.sa-play{width:100%;border:0;border-radius:12px;padding:12px;background:#16181d;color:#fff;font-weight:700;font-size:16px;cursor:pointer}
.sa-cta{width:100%;border:0;background:none;color:#3b5bfd;padding:10px 0 0;font-size:14px;cursor:pointer}
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


# ---------- inputs and selection ----------

def load_candidates(run_dir: Path) -> dict[str, Candidate]:
    """Every proposed candidate, plus the judge's revised ones (judge/revisions.json, read as a CandidatesFile)."""
    files = [run_dir / "propose" / "candidates.json", run_dir / "judge" / "revisions.json"]
    return {c.id: c for f in files if f.exists() for c in CandidatesFile.model_validate_json(f.read_text()).candidates}


def load_approvals(flows_dir: Path) -> list[str] | None:
    path = flows_dir / "approvals.json"
    return json.loads(path.read_text())["approved"] if path.exists() else None


def select(decisions: list[Decision], approvals: list[str] | None) -> list[Decision]:
    """Accepted and CONDITIONAL ideas, best rank first, at most 4. flows/approvals.json can only narrow the list."""
    picked = sorted((d for d in decisions if d.final in SURVIVED), key=lambda d: -(d.rank_score or 0))
    if approvals is not None:
        picked = [d for d in picked if d.candidate_id in approvals]
    return picked[:MAX_IDEAS]


def mock_source(run_dir: Path) -> Path:
    approved = run_dir / "qa" / "approved"
    return approved if (approved / "index.html").exists() else run_dir / "mock"


def clean(flows_dir: Path) -> None:
    """Clears the last run's output but keeps the human's approvals.json."""
    for child in flows_dir.iterdir():
        if child.name != "approvals.json":
            shutil.rmtree(child) if child.is_dir() else child.unlink()


# ---------- plain words ----------

def caption(c: Candidate) -> str:
    return c.title.removeprefix(f"{BUCKETS.get(c.kind, '')}: ")


def plain(text: str) -> str:
    """Slide text: no ids a product team would have to decode."""
    return " ".join(IDS.sub("", text).split())


def reward_line(c: Candidate) -> str:
    r = c.reward
    amount = "" if r.amount == 1 else f"{r.amount:g} "
    return f"{amount}{r.unit}, {r.duration}" if r.duration else f"{amount}{r.unit}"


def reach_text(c: Candidate, model: ProductModel) -> str:
    where = {0: "sits on the first screen people see, which every visit passes",
             1: "sits one tap from the first screen, which about half of visits reach (assumed)"}
    depth = depths(model).get(c.trigger_state_id, 2)
    return (f"Reach scenario, not a measurement: the offer "
            f"{where.get(depth, 'sits a few taps in, which about a quarter of visits reach (assumed)')}.")


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
    return "\n".join([
        f"Idea {c.id} ({BUCKETS.get(c.kind, c.kind)}): {caption(c)}", "",
        f"Trigger: {c.trigger_event}", f"Starts on: {c.trigger_state_id}", f"Placement of the offer: {c.placement}",
        f"Offer copy (verbatim): {c.offer_copy}", f"Reward: {reward_line(c)}",
        f"If they say no: {c.decline_path}", f"If the ad fails: {c.ad_fail_path}",
        f"When the reward runs out: {c.after_reward}", f"Subscribers: {c.subscriber_treatment}", "",
        "Steps, in order:", *steps, "", "Screens already in the page:", *drawn, "",
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


def blur_css(model: ProductModel, trigger: str, assets: Path) -> str:
    """Card art on a screen not rated safe is blurred, in the flow's mock and on every slide. New screens take their
    trigger screen's rating."""
    risky = [s.id for s in model.states if s.in_mock_scope and s.content_rating != "safe"]
    art = [p.name for p in sorted(assets.glob("*.png")) if is_art(p)] if assets.exists() else []
    if not risky or not art:
        return ""
    screens = [f'[data-screen="{sid}"]' for sid in risky] + (["[data-flow]"] if trigger in risky else [])
    images = [f'img[src="assets/{name}"],[style*="{name}"]' for name in art]
    return f":is({','.join(screens)}) :is({','.join(images)}){{filter:blur(14px)!important}}\n"


def with_flow_css(html: str, blur: str) -> str:
    return _insert_before(html, "</head>", f'<style id="simula-flow">\n{FLOW_CSS}{blur}</style>\n')


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


def flow_page(original: str, edited: str, c: Candidate, blur: str) -> tuple[str, str | None, int]:
    """The edited page with the navigation runtime, the flow's CSS, and the simulated ad. Returns it with the ad's
    screen id and its step index."""
    step_ids = [s.state_id for s in c.flow_steps]
    ad = ad_screen(edited, step_ids)
    at = ad_index(step_ids, ad)
    after_ad = step_ids[at + 1] if ad in step_ids and at + 1 < len(step_ids) else step_ids[0]
    html = with_flow_css(with_runtime(edited, page_root(original)), blur)
    return _insert_before(html, "</body>", flow_js(c, ad, after_ad)), ad, at


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
        page.evaluate("() => window.simulaAd.complete()")
        return state(page) == target, None, f"the verified play led to {state(page)}, not {target}"
    link = tappable(page, current, target)
    if link is None:
        return False, None, f"no tappable element on {current} leads to {target}"
    box = link.bounding_box()
    try:
        link.click(timeout=CLICK_MS)
    except PlaywrightTimeout:
        return False, None, f"the element leading to {target} can't be tapped (something covers it)"
    return state(page) == target, box, f"the tap led to {state(page)}, not {target}"


def walk(flow_dir: Path, c: Candidate, ad: str | None, ad_at: int) -> tuple[list[dict], dict]:
    """Taps through the flow's steps and screenshots each. A step that won't tap through keeps the last good
    screenshot and is marked not wired. Returns the shots and what the page recorded."""
    screens = flow_dir / "screens"
    shots, last = [], None
    with render.open_mock(flow_dir) as (page, log):
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
                page.screenshot(path=screens / last)
            shots.append({"state_id": step.state_id, "caption": step.caption, "png": last, "wired": wired,
                          "tap": None, "why": "" if wired else why})
        recorded = page.evaluate("() => window.simulaAd ? {grants: simulaAd.grants(), events: simulaAd.events()} "
                                 ": {grants: 0, events: []}")
        recorded["console"] = log["console"]
    return shots, recorded


def screenshot_before(flow_dir: Path, sid: str) -> str | None:
    with render.open_mock(flow_dir) as (page, _):
        if not show(page, sid):
            return None
        page.screenshot(path=flow_dir / "screens" / "before.png")
    return "before.png"


def build_flow(ctx: Ctx, model: ProductModel, source: Path, c: Candidate, decision: Decision,
               edits: Edits | None) -> dict:
    flow_dir = ctx.run_dir / "flows" / c.id
    (flow_dir / "screens").mkdir(parents=True)
    if (source / "assets").exists():
        shutil.copytree(source / "assets", flow_dir / "assets")
    original = (source / "index.html").read_text()
    blur = blur_css(model, c.trigger_state_id, flow_dir / "assets")
    (flow_dir / "index.html").write_text(with_flow_css(original, blur))
    before = screenshot_before(flow_dir, c.flow_steps[0].state_id)

    edited, rejected = apply_edits(strip_runtime(original), edits.edits if edits else [])
    applied = len(edits.edits) - len(rejected) if edits else 0
    run_trace(ctx.run_dir, stage="flows", step=f"edits:{c.id}", decider="code", outcome="ok" if applied else "error",
              note=f"{applied} applied, {len(rejected)} rejected" + (f"; first: {rejected[0]}" if rejected else ""))
    html, ad, ad_at = flow_page(original, edited, c, blur)
    (flow_dir / "index.html").write_text(html)

    shots, recorded = walk(flow_dir, c, ad, ad_at)
    for n, shot in enumerate(shots):
        if not shot["wired"]:
            run_trace(ctx.run_dir, stage="flows", step=f"walk:{c.id}", decider="code", outcome="error",
                      note=f"step {n + 1} {NOT_WIRED}: {shot['why']}"[:300])
    if recorded["console"]:
        run_trace(ctx.run_dir, stage="flows", step=f"walk:{c.id}", decider="code", outcome="error",
                  note=f"{len(recorded['console'])} console errors; first: {recorded['console'][0]}"[:300])
    return {"candidate": c, "decision": decision, "before": before, "shots": shots, "ad_at": ad_at,
            "grants": recorded["grants"], "events": recorded["events"], "applied": applied, "rejected": rejected}


# ---------- slides ----------

def phone_layout(count: int) -> list[tuple[float, float, float, float]]:
    """Phone frames in slide px, side by side in the right half."""
    left, right, top, height, gap = 600, 1216, 142, 400, 60
    width = min(height * VIEW_W / VIEW_H, (right - left - gap * (count - 1)) / count)
    height = width * VIEW_H / VIEW_W
    x = left + (right - left - count * width - gap * (count - 1)) / 2
    return [(x + i * (width + gap), top, width, height) for i in range(count)]


def phones_html(shots: list[dict], prefix: str, pointer: bool) -> str:
    """Phone screenshots with a ring on what the user taps, arrows between phones, and (when pointer) an arrow from
    the first callout to the first ring."""
    frames = phone_layout(len(shots))
    parts, lines, rings = [], [], []
    for shot, (x, y, w, h) in zip(shots, frames):
        image = f'<img src="{prefix}/screens/{shot["png"]}" alt="">' if shot["png"] else ""
        badge = f'<span class="notwired">{NOT_WIRED}</span>' if not shot["wired"] else ""
        text = f'<p class="phone-caption">{escape(plain(shot["caption"]))}</p>' if shot.get("show_caption") else ""
        parts.append(f'<div class="phone" style="left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{h:.0f}px">'
                     f"{image}{badge}</div>"
                     f'<div class="phone-text" style="left:{x - 28:.0f}px;top:{y + h + 10:.0f}px;width:{w + 56:.0f}px">'
                     f"{text}</div>")
        tap = shot.get("tap")
        if tap and shot["wired"]:
            s = w / VIEW_W
            rings.append((x + tap["x"] * s - 5, y + tap["y"] * s - 5, tap["width"] * s + 10, tap["height"] * s + 10))
    for (x, y, w, h), (nx, *_) in zip(frames, frames[1:]):
        lines.append((x + w + 10, y + h / 2, nx - 10, y + h / 2))
    if pointer and rings:
        rx, ry, _, rh = rings[0]
        lines.append((548, 196, rx - 4, ry + rh / 2))
    svg = "".join(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="10" class="ring"/>'
                  for x, y, w, h in rings)
    svg += "".join(f'<line x1="{a:.0f}" y1="{b:.0f}" x2="{c:.0f}" y2="{d:.0f}" marker-end="url(#head)"/>'
                   for a, b, c, d in lines)
    return "".join(parts) + f'<svg class="marks" viewBox="0 0 {SLIDE_W} {SLIDE_H}">{svg}</svg>'


def callouts_html(callouts: list[tuple[str, str]]) -> str:
    items = "".join(f'<div class="callout"><b>{escape(label)}</b><p>{escape(plain(text))}</p></div>'
                    for label, text in callouts if text)
    return f'<div class="callouts">{items}</div>'


def slide_html(flow: dict, number: int, part: str, body: str) -> str:
    c, decision = flow["candidate"], flow["decision"]
    kind = "existing" if c.kind == "existing_anchor" else "change"
    conditional = '<span class="chip conditional">Conditional</span>' if decision.final == "conditional" else ""
    footer = (f'<footer><b>When the reward runs out:</b> {escape(plain(c.after_reward))}</footer>'
              if c.after_reward else "")
    return (f'<section class="slide main" data-part="{part}" data-idea="{c.id}">'
            f'<header><span class="chip {kind}">{escape(BUCKETS.get(c.kind, ""))}</span>'
            f'<span class="idea">{escape(plain(caption(c)))}</span>{conditional}'
            f'<span class="count">{number} / {len(PARTS)}</span></header>'
            f'<h2><span class="num">{number}</span>{PARTS[part]}</h2>{body}{footer}</section>')


def verdicts(decision: Decision, run_dir: Path) -> list[tuple[str, Verdict]]:
    """(judge and round, verdict) for each verdict file the decision cites, e.g. ("judge_1_r1", …)."""
    return [(Path(p).stem.removeprefix(f"{decision.candidate_id}_"),
             Verdict.model_validate_json((run_dir / p).read_text()))
            for p in decision.verdict_paths if (run_dir / p).exists()]


def condition(decision: Decision, run_dir: Path) -> str:
    """A CONDITIONAL idea's condition, in the judge's words: the first judgment check a judge failed."""
    for _, verdict in verdicts(decision, run_dir):
        failed = next((getattr(verdict, k) for k in JUDGMENT if not getattr(verdict, k).passed), None)
        if failed:
            return failed.reason
    return "It passed every safety gate but not every quality check."


def with_captions(shots: list[dict]) -> list[dict]:
    return [{**shot, "show_caption": True} for shot in shots]


def idea_slides(flow: dict, model: ProductModel, run_dir: Path) -> list[str]:
    c, shots, at = flow["candidate"], flow["shots"], flow["ad_at"]
    prefix = c.id

    def missing(after: list[dict]) -> list[dict]:
        last = next((s["png"] for s in reversed(after) if s["png"]), None)
        return [{"png": last, "wired": False, "caption": "", "tap": None}]

    start = [{"png": flow["before"], "wired": bool(flow["before"]), "caption": "", "tap": None}]
    change = shots[:1]
    offer = with_captions(shots[1:at] or shots[:1])
    ad = with_captions(shots[at:at + 1]) or missing(shots[:at])
    value = with_captions(shots[at + 1:]) or missing(shots)
    new_part = ("The new part", c.adds) if c.kind == "product_change" and c.adds else ("Where the offer appears",
                                                                                        c.placement)
    slides = [
        ("start", [("Today", shots[0]["caption"])], start, False),
        ("change", [new_part, ("It shows up when", c.trigger_event)], change, True),
        ("offer", [("What the user sees", f"“{c.offer_copy}”"),
                   ("If they say no", "Everything stays exactly as it was.")], offer, True),
        ("ad", [("A short sponsored game", "The user chose to play it and can close it at any time."),
                ("The reward", "Granted once, only after the play is verified. Closing early or a failed ad uses "
                               "nothing up.")], ad, False),
        ("value", [("They get", reward_line(c)), ("How often", c.frequency_cap)], value, False),
    ]
    html = [slide_html(flow, n, part, callouts_html(callouts) + phones_html(phones, prefix, pointer))
            for n, (part, callouts, phones, pointer) in enumerate(slides, 1)]
    return html + [slide_html(flow, len(PARTS), "why", why_html(flow, model, run_dir))]


def why_html(flow: dict, model: ProductModel, run_dir: Path) -> str:
    c, decision, shots = flow["candidate"], flow["decision"], flow["shots"]
    gets = shots[-1]["caption"] if len(shots) > flow["ad_at"] + 1 else reward_line(c)
    blocks = [("What the user gets", gets),
              ("What the app gets", f"{c.rationale} {reach_text(c, model)}"),
              ("What it never costs them", f"Nothing free is taken away, and saying no changes nothing. "
                                           f"{c.subscriber_treatment}")]
    html = "".join(f'<div class="why-block"><b>{escape(label)}</b><p>{escape(plain(text))}</p></div>'
                   for label, text in blocks)
    if decision.final == "conditional":
        html += (f'<div class="condition"><b>Recommended with one condition:</b> '
                 f"{escape(plain(condition(decision, run_dir)))}</div>")
    return f'<div class="why">{html}</div>'


def cover_html(app: str, flows: list[dict]) -> str:
    items = "".join(f'<li><span class="chip {"existing" if f["candidate"].kind == "existing_anchor" else "change"}">'
                    f'{escape(BUCKETS.get(f["candidate"].kind, ""))}</span>{escape(plain(caption(f["candidate"])))}</li>'
                    for f in flows)
    body = (f"<ol>{items}</ol><p class='how'>Each idea in six slides: where it starts, what changes, the offer, the "
            "ad, what the user gets, and why it works.</p>" if flows else
            "<p class='how'>No idea passed every check. The appendix shows every idea's scores and reasons.</p>")
    return f'<section class="slide cover"><h1>Rewarded-ad ideas for {escape(app)}</h1>{body}</section>'


def verdict_table(decision: Decision, run_dir: Path) -> str:
    judged = verdicts(decision, run_dir)
    head = "".join(f"<th>{escape(name)}</th>" for name, _ in judged)
    rows = []
    for key in GATES + JUDGMENT:
        cells = "".join(f'<td class="{"pass" if getattr(v, key).passed else "fail"}">'
                        f'{"pass" if getattr(v, key).passed else "fail"}: {escape(getattr(v, key).reason)}</td>'
                        for _, v in judged)
        rows.append(f"<tr><td>{escape(CHECK_NAMES[key])}</td>{cells}</tr>")
    concerns = "".join(f"<p><b>{escape(name)}, other concern:</b> {escape(v.other_concern)}</p>"
                       for name, v in judged if v.other_concern)
    return f"<table><tr><th>Check</th>{head}</tr>{''.join(rows)}</table>{concerns}"


def appendix_html(decisions: list[Decision], candidates: dict[str, Candidate], run_dir: Path) -> str:
    cards = []
    for d in decisions:
        c = candidates.get(d.candidate_id)
        title = escape(c.title) if c else "(candidate not found)"
        facts = [f"<b>{d.final.upper()}</b> · {d.checks_passed}/{d.checks_total} checks passed"
                 + (f" · rank score {d.rank_score:g}" if d.rank_score is not None else "")]
        if d.gate_fails:
            facts.append(f"Gate fails: {escape(', '.join(d.gate_fails))}")
        if d.judgment_splits:
            facts.append(f"Judges split on: {escape(', '.join(d.judgment_splits))}")
        if d.revision_of:
            facts.append(f"Revision of {escape(d.revision_of)}")
        if d.failure_type:
            facts.append(f"Failure type {escape(d.failure_type)}, rerun {escape(d.rerun_stage or '')}")
        if c:
            steps = " → ".join(s.state_id for s in c.flow_steps)
            facts += [f"<b>Cost line:</b> {escape(c.economics.assumption_line) if c.economics else 'not priced'}",
                      f"<b>Evidence:</b> {escape(', '.join(c.anchor_evidence_ids) or 'none cited')} · trigger "
                      f"{escape(c.trigger_state_id)} · steps {escape(steps)}",
                      f"<b>If they say no:</b> {escape(c.decline_path)}",
                      f"<b>If the ad fails:</b> {escape(c.ad_fail_path)}"]
        cards.append(f'<div class="judged"><h3>{escape(d.candidate_id)} · {title}</h3>'
                     + "".join(f"<p>{f}</p>" for f in facts) + verdict_table(d, run_dir) + "</div>")
    dropped = [c for c in candidates.values() if c.dropped_reason]
    listed = "".join(f"<li>{escape(c.id)} · {escape(c.title)}: {escape(c.dropped_reason)}</li>" for c in dropped)
    rule = ("The simulated ad follows Simula's SDK lifecycle: the reward is granted on REWARD_VERIFIED only, once. "
            "CLICKED, CLOSED, EARNED_REWARD, LOAD_FAILED, and REWARD_VERIFICATION_FAILED never grant it, and "
            "closing the ad or a failure returns the user to where the offer appeared.")
    return ('<section class="appendix"><h2>Appendix: how the review scored every idea</h2>'
            f'<p class="rule">{rule}</p>{"".join(cards)}'
            + (f"<h3>Dropped by code before the review</h3><ul>{listed}</ul>" if dropped else "") + "</section>")


def deck(ctx: Ctx, model: ProductModel, flows: list[dict], decisions: list[Decision],
         candidates: dict[str, Candidate]) -> str:
    slides = [cover_html(ctx.app["name"], flows)]
    for flow in flows:
        slides += idea_slides(flow, model, ctx.run_dir)
    slides.append(appendix_html(decisions, candidates, ctx.run_dir))
    watermark = ('<div class="watermark">FIXTURE TEST DATA · not a deliverable</div>'
                 if ctx.run_dir.name.endswith("-fixture") else "")
    return Template(TEMPLATE.read_text()).substitute(title=f"Rewarded-ad ideas for {escape(ctx.app['name'])}",
                                                     slides=watermark + "\n".join(slides))


def write_pdf(slides: Path) -> Path:
    pdf = slides.with_suffix(".pdf")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H})
        page.goto(slides.as_uri())
        page.wait_for_load_state("networkidle")
        page.pdf(path=pdf, width=f"{SLIDE_W}px", height=f"{SLIDE_H}px", print_background=True)
        browser.close()
    return pdf


# ---------- stage ----------

def exhibit(flows: list[dict], chosen_from: str, source: Path, run_dir: Path, usd: float) -> str:
    lines = ["# 07 · flows", "", f"{len(flows)} idea(s) drawn ({chosen_from}), from `{source.relative_to(run_dir)}/`. "
             f"Model spend this stage: ${usd:.4f}.", "",
             "| Idea | Verdict | Edits applied / rejected | Steps wired | Grants in the walk | Flow mock |",
             "|---|---|---|---|---|---|"]
    for f in flows:
        c, wired = f["candidate"], sum(s["wired"] for s in f["shots"])
        lines.append(f"| {c.id} · {caption(c)} | {f['decision'].final} | {f['applied']} / {len(f['rejected'])} | "
                     f"{wired} / {len(f['shots'])} | {f['grants']} | `flows/{c.id}/index.html` |")
    broken = [(f["candidate"].id, n, s) for f in flows for n, s in enumerate(f["shots"], 1) if not s["wired"]]
    if broken:
        lines += ["", f"## {NOT_WIRED.capitalize()}", ""]
        lines += [f"- {cid} step {n} (`{s['state_id']}`): {s['why']}" for cid, n, s in broken]
    lines += ["", "Deck: `flows/slides.html`, `flows/slides.pdf`."]
    return "\n".join(lines) + "\n"


def run(ctx: Ctx) -> None:
    run_dir, out = ctx.run_dir, ctx.run_dir / "flows"
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    candidates = load_candidates(run_dir)
    out.mkdir(exist_ok=True)
    approvals = load_approvals(out)
    clean(out)
    chosen = [d for d in select(decisions, approvals) if d.candidate_id in candidates]
    chosen_from = "narrowed by flows/approvals.json" if approvals is not None else "accepted + conditional"
    run_trace(run_dir, stage="flows", step="select", decider="code",
              note=f"{chosen_from}: {' '.join(d.candidate_id for d in chosen) or 'none'}")
    source = mock_source(run_dir)
    page = strip_runtime((source / "index.html").read_text())
    budget = llm.Budget.for_stage("flows", run_dir / "trace.jsonl", ctx.usd_cap)
    with ThreadPoolExecutor(max_workers=max(1, len(chosen))) as pool:
        edits = list(pool.map(lambda d: ask_editor(ctx, candidates[d.candidate_id], model, page, budget), chosen))
    flows = [build_flow(ctx, model, source, candidates[d.candidate_id], d, e) for d, e in zip(chosen, edits)]
    (out / "slides.html").write_text(deck(ctx, model, flows, decisions, candidates))
    write_pdf(out / "slides.html")
    usd = sum(line.usd for line in read_trace(run_dir / "trace.jsonl") if line.stage == "flows")
    write_exhibit(run_dir, 7, "flows", exhibit(flows, chosen_from, source, run_dir, usd))
