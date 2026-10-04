"""Creative QA, all code and no model: a Playwright bot plays every path at three widths with timers on and with
?fast=1, grounding checks every proof claim against the facts' allowed strings, and the tier comes from the screens,
art and generated text a creative uses."""

from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from simula import config
from simula.creative.facts import Facts, is_excerpt
from simula.creative.schema import Content, Tier

WIDTHS = (360, 390, 430)
HEIGHTS = {360: 780, 390: 844, 430: 932}
MODES = ("timers", "fast")
SOLVED = ["DISPLAYED", "CHALLENGE_STARTED", "CHALLENGE_PASS_1", "CHALLENGE_PASS_2", "CHALLENGE_PASS_3",
          "CHALLENGE_SOLVED", "ENDCARD_SHOWN", "CTA_CLICKED"]
PATHS = {
    "right": SOLVED,
    "wrong_then_right": SOLVED,
    "skip": ["DISPLAYED", "CHALLENGE_STARTED", "ENDCARD_SHOWN"],
    "idle": ["DISPLAYED", "CHALLENGE_STARTED", "ENDCARD_SHOWN"],
    "cta": ["DISPLAYED", "CHALLENGE_STARTED", "ENDCARD_SHOWN", "CTA_CLICKED"],
    "close": ["DISPLAYED", "CHALLENGE_STARTED", "ENDCARD_SHOWN", "CLOSED"],
}
TAP_DELAY_MS = 1500
END_DELAY_MS = 3000
IDLE_MS = 10_000
RIGHT_PATH_MS = (15_000, 25_000)
STEP_MS = 100
LIMIT_MS = 30_000
STATE = "() => window.simulaCreative.state()"
AT = "s => window.simulaCreative.state() === s"
LAYOUT = """() => {
  const vw = window.innerWidth, vh = window.innerHeight, found = [];
  const width = document.documentElement.scrollWidth;
  if (width > vw) found.push(`horizontal scroll: the page is ${width}px wide in a ${vw}px viewport`);
  for (const b of document.querySelectorAll('button')) {
    if (!b.checkVisibility()) continue;
    const r = b.getBoundingClientRect();
    if (r.left < -0.5 || r.top < -0.5 || r.right > vw + 0.5 || r.bottom > vh + 0.5)
      found.push(`control ${b.id || b.textContent.trim()} sits outside the viewport`);
  }
  for (const img of document.querySelectorAll('#host-art, #end-art')) {
    if (img.checkVisibility() && img.naturalWidth && img.getBoundingClientRect().width > img.naturalWidth * 1.25 + 0.5)
      found.push(`${img.id} is drawn above 1.25x its pixel size`);
  }
  const label = document.getElementById('sponsored'), r = label.getBoundingClientRect();
  if (!label.checkVisibility() || r.width === 0 || r.left < 0 || r.top < 0 || r.right > vw || r.bottom > vh)
    found.push('the Sponsored label is not visible');
  return found;
}"""


def _until(page, js: str, arg=None) -> None:
    """Advances the page's paused clock in STEP_MS ticks until js(arg) holds; RuntimeError after LIMIT_MS."""
    waited = 0
    while not page.evaluate(js, arg):
        if waited >= LIMIT_MS:
            raise RuntimeError(f"still waiting after {LIMIT_MS} ms for {js} ({arg})")
        page.clock.run_for(STEP_MS)
        waited += STEP_MS


def _drive(page, content: Content, path: str, mode: str, look) -> None:
    """Plays one path the way a person would; look() records layout problems on the screen showing."""
    if path in ("right", "wrong_then_right"):
        for puzzle in content.puzzles:
            _until(page, "t => window.simulaCreative.state() === 'puzzle' && "
                         "document.getElementById('prompt').textContent === t", puzzle.prompt)
            look()
            page.clock.run_for(TAP_DELAY_MS)
            if path == "wrong_then_right":
                page.click(f'.option[data-index="{next(k for k in range(3) if k != puzzle.answer)}"]')
                look()
            page.click(f'.option[data-index="{puzzle.answer}"]')
        if mode == "timers":
            _until(page, AT, "proof")
            look()
        _until(page, AT, "end")
        look()
        page.clock.run_for(END_DELAY_MS)
        page.click("#cta")
    elif path == "idle":
        _until(page, AT, "puzzle")
        look()
        page.clock.run_for(IDLE_MS)
        _until(page, AT, "end")
        look()
    else:
        _until(page, "() => !document.getElementById('skip').hidden")
        look()
        page.click("#skip")
        _until(page, AT, "end")
        look()
        if path == "cta":
            page.click("#cta")
        elif path == "close":
            page.click("#close")


def _is_int(value, want: int) -> bool:
    """value is the integer want; JSON true is not 1."""
    return type(value) is int and value == want


def _event_problems(events, path: str, mode: str) -> list[str]:
    """What the page's event log gets wrong; a malformed log is a problem, never an exception."""
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        return [f"events() returned {events!r}, not a list of events"]
    names = [e.get("name") for e in events]
    if names != PATHS[path]:
        return [f"events {names}, expected {PATHS[path]}"]
    taps = 2 if path == "wrong_then_right" else 1
    found = []
    for e in events:
        if e["name"].startswith("CHALLENGE_PASS_"):
            n = int(e["name"].removeprefix("CHALLENGE_PASS_"))
            if not _is_int(e.get("n"), n):
                found.append(f"{e['name']} says puzzle {e.get('n')!r}")
            if not _is_int(e.get("attempts"), taps):
                found.append(f"{e['name']} logged {e.get('attempts')!r} attempts; the bot tapped {taps}")
    times = [e.get("t") for e in events]
    if any(type(t) not in (int, float) for t in times):
        found.append(f"events carry times {times}; each must be a number")
    elif path == "right" and mode == "timers" and not RIGHT_PATH_MS[0] <= times[-1] - times[0] <= RIGHT_PATH_MS[1]:
        found.append(f"the right path took {times[-1] - times[0]} ms from DISPLAYED to CTA_CLICKED; it must take "
                     "15-25 s")
    return found


def _run(browser, html: Path, content: Content, path: str, mode: str, width: int) -> list[str]:
    """One playthrough on a fresh page with Playwright's clock installed and paused, so timers fire only as the bot
    advances them; every request but the creative's own file, and every WebSocket, is refused and counted."""
    context = browser.new_context(viewport={"width": width, "height": HEIGHTS[width]})
    page = context.new_page()
    page.set_default_timeout(3000)
    doc = html.resolve()
    console, requests, layout, found = [], [], [], []

    def route(r):
        url = urlparse(r.request.url)
        if url.scheme == "file" and Path(unquote(url.path)).resolve() == doc:
            r.continue_()
        else:
            requests.append(r.request.url)
            r.abort()

    def look():
        state = page.evaluate(STATE)
        layout.extend(f"{state}: {problem}" for problem in page.evaluate(LAYOUT))

    page.route("**/*", route)
    # A routed WebSocket never reaches the server unless connect_to_server() is called; ws.close() here deadlocks.
    page.route_web_socket("**/*", lambda ws: requests.append(ws.url))
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console.append(str(e)))
    try:
        page.clock.install(time=0)
        page.goto(doc.as_uri() + "?hold=1" + ("&fast=1" if mode == "fast" else ""))
        page.clock.pause_at(1000)
        page.evaluate("() => window.simulaCreative.start()")
        look()
        _drive(page, content, path, mode, look)
        found += _event_problems(page.evaluate("() => window.simulaCreative.events()"), path, mode)
    except (RuntimeError, PlaywrightError) as e:
        found.append(f"the {path} path stopped: {str(e).splitlines()[0]}")
    finally:
        context.close()
    return (found + [f"console error: {text}" for text in console]
            + [f"network request: {url}" for url in requests] + list(dict.fromkeys(layout)))


def playthrough(html: Path, content: Content, *, paths: tuple[str, ...] = tuple(PATHS),
                widths: tuple[int, ...] = WIDTHS, modes: tuple[str, ...] = MODES) -> dict[str, list[str]]:
    """Plays every path in every mode at every width. Returns {"<path>/<mode>/<width>": problems}; a run passes when
    its list is empty. Checks: events in the path's order with attempts matching the taps; the right path with
    timers on takes 15-25 s from DISPLAYED to CTA_CLICKED when the bot taps 1.5 s after each prompt and 3 s after the
    end card; no console error; no request; no horizontal scroll; every visible control inside the viewport; host art
    never above 1.25x its pixels; the Sponsored label visible on every screen."""
    runs = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for path in paths:
                for mode in modes:
                    for width in widths:
                        runs[f"{path}/{mode}/{width}"] = _run(browser, html, content, path, mode, width)
        finally:
            browser.close()
    return runs


def grounding(content: Content, facts: Facts) -> list[str]:
    """Every proof claim must be the exact text of an allowed string, or an excerpt of it on word boundaries, cited
    by that string's own evidence id; the proof screenshot must come from a safe-scope state. Allowed strings sit on
    safe-scope screens by construction (facts.build_facts). Evidence ids prove traceability, not truth."""
    allowed = {a.evidence_id: a for a in facts.allowed}
    found = [] if content.proof.screen_id in facts.safe_scope else [
        f"proof screen {content.proof.screen_id} is not a safe-scope screen"]
    for claim in content.proof.claims:
        source = allowed.get(claim.evidence_id)
        if source is None:
            found.append(f'claim "{claim.text}" cites {claim.evidence_id}, which holds no allowed proof string')
        elif not is_excerpt(claim.text, source.text):
            found.append(f'claim "{claim.text}" is not the text of {claim.evidence_id} nor an excerpt of it on word '
                         "boundaries")
    return found


def tier(content: Content, facts: Facts) -> tuple[Tier | None, list[str]]:
    """content_tier from code: every screen used (the proof screen, the art's source screen, the host's and each
    claim's evidence screen) must be rated safe, and no generated line may carry an adult keyword from
    config/profiles.toml [content]. Returns ("sfw", []) or (None, problems): mixed, unsafe and unknown are not
    allowed."""
    screens = {content.proof.screen_id, *(c.evidence_id.split(".")[0] for c in content.proof.claims)}
    screens |= {ref.split(".")[0] for ref in (content.host.art_ref, content.host.evidence_id) if ref}
    found = [f"{sid} is rated {facts.ratings.get(sid, 'unknown')}; only safe screens and art may feed a creative"
             for sid in sorted(screens) if facts.ratings.get(sid) != "safe"]
    copy = content.copy_
    texts = [copy.intro, *copy.captions, copy.right_line, copy.wrong_hint, copy.end_headline]
    for word in config.profiles()["content"]["adult_keywords"]:
        if any(is_excerpt(word, text.lower()) for text in texts):
            found.append(f'generated text uses the adult keyword "{word}"')
    return (None if found else "sfw"), found
