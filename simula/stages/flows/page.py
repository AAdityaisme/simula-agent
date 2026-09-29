"""The flow's page: code-owned CSS and JS for the simulated ad, the ad card, the palette, blur, and the phone viewport."""

import json
import re
from html import escape
from pathlib import Path

from PIL import Image

from simula import render
from simula.contracts import Candidate, ProductModel
from simula.stages.flows.editor import page_root
from simula.stages.flows.wording import FAIL_NOTE, reward_line
from simula.stages.mock import StartTags, _insert_before, _rewrite, with_runtime

ART_MIN_PX = 240
VIEW_W, VIEW_H = render.VIEWPORT["width"], render.VIEWPORT["height"]
ACCENT_MIN_CHROMA = 0.25  # 67 real palettes: accents 0.40-0.85, every other color 0.16 or less, grays 0.06 or less
FLOW_CSS = """body:not(.simula-rewarded) [data-reward]{display:none!important}
body.simula-rewarded [data-unrewarded]{display:none!important}
body.simula-hide-replaced [data-unrewarded]{opacity:0!important}
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
