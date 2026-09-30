"""The three Playwright walks (the tap-through, saying no, a failed ad) and the reward on/off diff."""

import io
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from simula import render
from simula.contracts import Candidate
from simula.stages.flows.page import VIEW_W

CLICK_MS = 2000
PLAY_MS = 5000
LABEL_PAD = 16  # a reward label's shadow and anti-aliasing reach this far past its box, in CSS px
RENDER_NOISE = 8  # Chromium redraws a blurred glow up to 4 levels off after any style change (measured on a real mock)
REWARDED_JS = "on => document.body.classList.toggle('simula-rewarded', on)"
REWARD_LABELS_JS = """() => [...document.querySelectorAll('[data-reward]')].filter(e => e.checkVisibility())
  .map(e => { const b = e.getBoundingClientRect(); return {text: (e.innerText ?? e.textContent).trim(), box: [b.x, b.y, b.width, b.height]}; })"""
HAS_REPLACED_JS = "() => document.querySelector('[data-unrewarded]') !== null"
HIDE_REPLACED_JS = "on => document.body.classList.toggle('simula-hide-replaced', on)"
NOTE_ON_TOP_JS = """() => { const note = document.querySelector('.sa-note');
  if (!note || !note.checkVisibility()) return false;
  const b = note.getBoundingClientRect();
  return note.contains(document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)); }"""


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
    ignores the pointer, a transparent overlay, one offscreen or covered). Opacity hides all a subtree paints, a child
    that sets its own visibility included, and moves nothing."""
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
