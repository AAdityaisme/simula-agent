"""The slides: the cover, each idea's flow and why slides, and the score pages, filled into the template."""

import base64
import math
from html import escape
from pathlib import Path
from string import Template

from simula.config import ROOT, STAGES
from simula.contracts import GATES, JUDGMENT, Candidate, Decision, ProductModel, Verdict
from simula.runlog import read_marker
from simula.stages import Ctx
from simula.stages.flows.page import VIEW_H, VIEW_W
from simula.stages.flows.wording import (NOT_WIRED, PLAIN_CHECKS, REWARD_NOT_SHOWN, REWARD_RULE, VERDICTS, app_title,
                                         caption, plain, reach_text, reward_line)
from simula.stages.propose import BUCKETS

TEMPLATE = ROOT / "templates" / "slides.html"
FONTS = ROOT / "templates" / "fonts"  # Inter, static Latin instances, SIL OFL 1.1 (OFL.txt beside them)
FONT_WEIGHTS = (400, 700, 800, 900)  # the weights templates/slides.html uses; static, so a PDF embeds TrueType
SLIDE_W, SLIDE_H = 1280, 720
PARTS = ("flow", "why")
WHY_TITLE = "Why this works for your app"
ROW_W, ROW_GAP = SLIDE_W - 2 * 64, 28
PHONE_MAX_W, COLUMN_MAX_W, RING_PAD = 160, 220, 5
# the score table's CSS: 12px text on 15px lines, 7px of padding and border a row, the rows' room between the table's
# head and the footer; chars per line run low so a page's estimate runs high
SCORE_LINE_PX, SCORE_ROW_PAD_PX, SCORE_PAGE_PX = 15, 7, 490
SCORE_TITLE_CHARS, SCORE_WHY_CHARS = 60, 72


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


def cover_html(app: str, flows: list[dict], *, cap: int, unbuilt: int = 0, fallbacks: int = 0,
               unbuilt_fallbacks: int = 0, cut: int = 0, status: list[str] = ()) -> str:
    """The overview: every idea in the deck, how to read it, the reward rule every idea follows, whether an idea is
    only the judge's fallback pick (drawn, or not drawn), how many survivors past the cap of `cap` were left out, and
    status lines for anything an earlier stage couldn't finish. `unbuilt` counts only ideas that passed the review."""
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
        notes.append(f"{cut} more idea(s) passed the review; the deck draws only the top {cap} by rank, and the "
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
    """A cover line for each earlier stage whose marker says it finished only part of its work, with its reasons."""
    lines = []
    for stage in STAGES[:STAGES.index("flows")]:
        marker = read_marker(run_dir, stage)
        if marker and marker.outcome.status == "partial":
            lines.append(f"The {stage} step finished only part of its work: {'; '.join(marker.outcome.reasons)}.")
    return lines


def deck(ctx: Ctx, model: ProductModel, flows: list[dict], not_built: list[tuple[Decision, str]],
         decisions: list[Decision], candidates: dict[str, Candidate], *, cap: int, cut: int = 0) -> str:
    app = app_title(model, ctx.app["name"])
    none_accepted = not any(d.final == "accept" for d in decisions)
    fallbacks = sum(is_fallback(f["decision"], ctx.run_dir, none_accepted) for f in flows)
    unbuilt_fallbacks = sum(is_fallback(d, ctx.run_dir, none_accepted) for d, _ in not_built)
    slides = [cover_html(app, flows, cap=cap, unbuilt=len(not_built) - unbuilt_fallbacks, fallbacks=fallbacks,
                         unbuilt_fallbacks=unbuilt_fallbacks, cut=cut, status=unfinished_stages(ctx.run_dir))]
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
