"""Two documents filled into the template: the app's product team's deck (the cover, then each idea's flow and why
slides) and Simula's own review (what the deck left out and why, the score pages, and Needs your call)."""

import base64
import json
import math
from html import escape
from pathlib import Path
from string import Template

from simula import economics
from simula.config import ROOT, STAGES
from simula.contracts import GATES, JUDGMENT, Candidate, Decision, ProductModel, SavedVerdict, Verdict
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
AFTER_PLAY = "A new screen opens after the verified play."
OPEN_NOTES = "Review notes: see Simula's review."
STEPS_PENDING = "Some steps aren't shown working yet: see Simula's review."
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
    a caption, what the user taps on it, and a flag for anything the walks couldn't show, which only Simula's review
    lists. A new screen after the ad has no reward to switch off and check, so its caption says only what the walk saw:
    the verified play opened it."""
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
        elif i > at and shot["wired"] and shot["reward_shown"] is None:
            text = AFTER_PLAY
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
    return (f'<div class="step"><div class="shot{" dim" if phone["flags"] else ""}"><span class="n">{number}</span>'
            f'<div class="phone">{image}{ring}</div></div>'
            f'<div class="cap"><b>{escape(phone["label"])}</b><p>{escape(plain(phone["text"]))}</p></div></div>')


def row_html(phones: list[dict], prefix: str) -> str:
    """Numbered phones in one row, arrows between them, a caption under each; a flagged phone is dimmed."""
    column, height = row_geometry(len(phones))
    steps = "".join(step_html(n, phone, prefix) for n, phone in enumerate(phones, 1))
    return (f'<div class="row" style="--gap:{ROW_GAP}px;--col:{column:.0f}px;--h:{height:.0f}px;'
            f'--aspect:{VIEW_W}/{VIEW_H}">{steps}</div>')


def slide_html(flow: dict, part: str, body: str, closest_idea: bool) -> str:
    """One of an idea's slides: a header with its kind, and a label if it is only the closest idea, then the body
    (HTML). A verdict or a person's approval is Simula's review's to say."""
    c = flow["candidate"]
    kind = "existing" if c.kind == "existing_anchor" else "change"
    labels = '<span class="chip conditional">Closest idea</span>' if closest_idea else ""
    idea = "" if part == "flow" else escape(plain(caption(c)))  # the flow slide's title already names the idea
    return (f'<section class="slide main" data-part="{part}" data-idea="{c.id}">'
            f'<header><span class="chip {kind}">{escape(BUCKETS.get(c.kind, ""))}</span>'
            f'<span class="idea">{idea}</span>{labels}'
            f'<span class="count">{PARTS.index(part) + 1} / {len(PARTS)}</span></header>'
            f"{body}</section>")


def verdicts(decision: Decision, run_dir: Path) -> list[tuple[str, Verdict]]:
    """(judge and round, verdict) for each verdict file the decision cites, e.g. ("judge_1_r1", …)."""
    return [(Path(p).stem.removeprefix(f"{decision.candidate_id}_"),
             SavedVerdict.model_validate_json((run_dir / p).read_text()))
            for p in decision.verdict_paths if (run_dir / p).exists()]


def failed_checks(decision: Decision, run_dir: Path) -> list[tuple[str, str]]:
    """(check, the first failing judge's reason) for every check some judge failed, in check order."""
    judged = [v for _, v in verdicts(decision, run_dir)]
    return [(k, next(getattr(v, k).reason for v in judged if not getattr(v, k).passed))
            for k in GATES + JUDGMENT if any(not getattr(v, k).passed for v in judged)]


def cost_question(c: Candidate) -> str | None:
    """The cost line's verdict and its lost-sale flag in plain words, with no numbers (the judge's exhibit has them)."""
    e = c.economics
    if e is None:
        return None
    serving = {"FAIL": "it may cost more to serve than a view earns",
               "CONDITIONAL": "what it costs to serve wasn't observed" if economics.uncounted(e)
               else "a view pays for what it costs to serve only where ad prices are high"}.get(e.verdict)
    sale = e.lost_sale and "it may give away something the app could sell"
    return "; ".join(p for p in (serving, sale) if p) or None


def is_fallback(decision: Decision, run_dir: Path, none_accepted: bool) -> bool:
    """The judge's fallback pick: nothing was accepted, so the closest idea goes on as CONDITIONAL. The judge marks it
    by keeping its reject's failure_type; a CONDITIONAL from a judge split (D10) has none."""
    return decision.final == "conditional" and none_accepted and decision.failure_type is not None


def needs_call(decision: Decision) -> bool:
    """A CONDITIONAL idea the judges split on (D10) that isn't the fallback pick: it isn't drawn unless a person
    approves it, and waits on the Needs your call page (D11)."""
    return decision.final == "conditional" and bool(decision.judgment_splits) and decision.failure_type is None


def disagreement(k: str, failing: str, judged: list[Verdict]) -> str:
    """One check the judges split on: the open question in plain words, then both sides."""
    passing = next((getattr(v, k).reason for v in judged if getattr(v, k).passed), None)
    return (f'"{PLAIN_CHECKS[k]}": one reviewer: no ({failing.rstrip(".")}); another: yes'
            + (f" ({passing.rstrip('.')})." if passing else "."))


def condition(decision: Decision, run_dir: Path, none_accepted: bool,
              approved: bool = False) -> tuple[str, str] | None:
    """A CONDITIONAL idea's heading and sentence about the checks it missed, in plain words, with both judges'
    reasons when they split (D10); None when it missed none (its condition is then its cost line, see review_notes)."""
    if decision.final != "conditional":
        return None
    failed = failed_checks(decision, run_dir)
    names = ", ".join(PLAIN_CHECKS[k] for k, _ in failed)
    if is_fallback(decision, run_dir, none_accepted) and failed and not any(k in GATES for k, _ in failed):
        return ("The closest idea, not a recommendation:",
                f"No idea passed every check. This one passes every safety check but not {names} ({failed[0][1]}).")
    if failed and all(k in decision.judgment_splits for k, _ in failed):
        judged = [v for _, v in verdicts(decision, run_dir)]
        doubts = " ".join(disagreement(k, reason, judged) for k, reason in failed)
        checks = " and ".join(f'"{PLAIN_CHECKS[k]}"' for k, _ in failed)
        if approved:
            return "Approved by a person:", f"The reviewers split on {checks}. {doubts}"
        if none_accepted:  # drawn as the closest idea: nothing was accepted (stage.survivors)
            return ("Closest idea:", f"The reviewers split on {checks}; confirm {'it' if len(failed) == 1 else 'each'} "
                                     f"before building. {doubts}")
        return "The reviewers disagreed:", doubts
    if failed:
        return "Not every check passed:", f"It didn't pass {names} ({failed[0][1]})."
    if decision.checks_passed < decision.checks_total:
        return "Not every check passed:", f"It passed {decision.checks_passed} of {decision.checks_total} checks."
    return None


def saying_no(flow: dict) -> str:
    """What saying no does, as the decline walk found it, for every slide that says so."""
    return "saying no doesn't bring them back yet" if flow["decline"] else "saying no changes nothing"


def review_notes(flow: dict, run_dir: Path, none_accepted: bool) -> list[str]:
    """What a drawn idea's slides leave to Simula's review: each step's flags, the checks it missed with the judges'
    reasons, and its cost line."""
    c = flow["candidate"]
    notes = [f"Flow slide, step {n} ({phone['label']}{f', walk step {n - 1}' if n > 1 else ''}): {flag}."
             for n, phone in enumerate(row_phones(flow), 1) for flag in phone["flags"]]
    if note := condition(flow["decision"], run_dir, none_accepted, flow.get("approved", False)):
        notes.append(f"{note[0]} {plain(note[1])}")
    if cost := cost_question(c):
        notes.append(f"Cost check ({c.economics.verdict}): {cost}.")
    return notes


def why_html(flow: dict, model: ProductModel, run_dir: Path, none_accepted: bool, closest_idea: bool) -> str:
    """The why slide's blocks and its reach line, then one neutral line when Simula's review has notes on the idea."""
    c = flow["candidate"]
    gets = reward_line(c)  # the offer's terms: the flow slide already shows what the screen does after the play
    blocks = [("What the user gets", gets[:1].upper() + gets[1:] + "."),
              ("What the app gets", c.rationale),
              ("What it never costs them", f"Nothing free is taken away, and {saying_no(flow)}. "
                                           f"{c.subscriber_treatment}")]
    html = "".join(f'<div class="why-block"><b>{escape(label)}</b><p>{escape(plain(text))}</p></div>'
                   for label, text in blocks)
    html += f'<p class="reach">{escape(plain(reach_text(c, model)))}</p>'
    if review_notes(flow, run_dir, none_accepted):
        lead = "<b>The closest idea, not a recommendation.</b> " if closest_idea else ""
        html += f'<div class="condition">{lead}{OPEN_NOTES}</div>'
    return f'<div class="why">{html}</div>'


def idea_slides(flow: dict, model: ProductModel, run_dir: Path, none_accepted: bool) -> list[str]:
    """An idea's two slides: its whole flow in one row of phones, then why the system thinks it's a good idea
    (none_accepted: the judge accepted no idea, so a CONDITIONAL one may be its fallback pick, see is_fallback). A flow
    with a flagged phone says under its row that some steps aren't shown working; the review says which."""
    c, phones = flow["candidate"], row_phones(flow)
    pick = closest(flow["decision"], run_dir, none_accepted, flow.get("approved", False))
    pending = f'<p class="pending">{escape(STEPS_PENDING)}</p>' if any(p["flags"] for p in phones) else ""
    label, new = (("The new part", c.adds) if c.kind == "product_change" and c.adds
                  else ("Where the offer appears", c.placement))
    gets = f"<b>They get:</b> {escape(plain(reward_line(c)))} · <b>How often:</b> {escape(plain(c.frequency_cap))}"
    body = (f'<div class="flow"><h2>{escape(plain(caption(c)))}</h2><div class="new">'
            f"<p><b>{label}:</b> {escape(plain(new))}</p>"
            f"<p><b>The offer says:</b> “{escape(plain(c.offer_copy))}” {saying_no(flow).capitalize()}.</p></div>"
            f"{row_html(phones, c.id)}{pending}<footer>{gets}</footer></div>")
    runs_out = f"<footer><b>When the reward runs out:</b> {escape(plain(c.after_reward))}</footer>" \
        if c.after_reward else ""
    why = f"<h2>{WHY_TITLE}</h2>{why_html(flow, model, run_dir, none_accepted, pick)}{runs_out}"
    return [slide_html(flow, "flow", body, pick), slide_html(flow, "why", why, pick)]


def cover_html(app: str, flows: list[dict], *, fallbacks: int = 0) -> str:
    """The product team's overview: every idea in the deck, how to read it, the reward rule every idea follows, and
    whether what is drawn is only the closest idea. Notes on the run itself go on the review's cover."""
    items = "".join(f'<li><span class="chip {"existing" if f["candidate"].kind == "existing_anchor" else "change"}">'
                    f'{escape(BUCKETS.get(f["candidate"].kind, ""))}</span>{escape(plain(caption(f["candidate"])))}</li>'
                    for f in flows)
    notes = (["Each idea takes two slides: its whole flow, step by step, then why it works.", REWARD_RULE] if flows
             else ["No idea is drawn in this deck."])
    if fallbacks:
        notes.append("None of these is a recommendation yet; the strongest is drawn and marked as the closest idea.")
    body = (f"<ol>{items}</ol>" if flows else "") + "".join(f"<p class='how'>{escape(n)}</p>" for n in notes)
    return f'<section class="slide cover"><h1>Rewarded-ad ideas for {escape(app)}</h1>{body}</section>'


def review_cover_html(app: str, flows: list[dict], *, cap: int, unbuilt: int = 0, fallbacks: int = 0,
                      unbuilt_fallbacks: int = 0, unbuilt_approved: int = 0, cut: int = 0, calls: int = 0,
                      approved: int = 0, held: list[str] = (), held_past_cap: list[str] = (),
                      set_aside: dict[str, str] | None = None, status: list[str] = ()) -> str:
    """The review's overview: which ideas the product team's deck draws, whether an idea is only the judge's fallback
    pick (drawn, or not drawn), how many survivors past the cap of `cap` were left out, what a person approved or
    held and which of their approvals don't hold, and status lines for anything an earlier stage couldn't finish.
    `unbuilt` counts only ideas that passed the review; `unbuilt_approved` the splits a person approved."""
    drawn = ", ".join(f["candidate"].id for f in flows) or "no idea"
    notes = [f"The product team's deck, flows/slides.pdf, draws {drawn}."]
    if fallbacks and unbuilt_fallbacks:
        notes.append(f"No idea passed every check, so the closest are marked as not a recommendation; "
                     f"{unbuilt_fallbacks} of them couldn't be drawn, and the score pages say why.")
    elif fallbacks:
        notes.append("No idea passed every check, so the closest is drawn and marked as not a recommendation.")
    elif unbuilt_fallbacks:
        notes.append("No idea passed every check; the closest couldn't be drawn, and the score pages say why.")
    if cut:
        notes.append(f"{cut} more idea(s) passed the review; the deck draws only the top {cap} by rank, and the "
                     "score pages score the rest.")
    if unbuilt:
        notes.append(f"{unbuilt} {'more ' if flows else ''}idea(s) passed the review but couldn't be drawn; the score "
                     "pages say why.")
    if unbuilt_approved:
        notes.append(f"{unbuilt_approved} idea(s) the reviewers split on were approved by a person but couldn't be "
                     "drawn; the score pages say why.")
    if approved:
        notes.append(f"{approved} idea(s) the reviewers split on are drawn because a person approved them.")
    notes += [f"The approval of {i} in flows/approvals.json doesn't hold ({why}), so it is treated as the closest "
              "idea, not as a person's approval." for i, why in (set_aside or {}).items()]
    if held:
        notes.append(f"A person held {', '.join(held)} out of the deck in flows/approvals.json.")
    if held_past_cap:
        notes.append(f"A person held {', '.join(held_past_cap)} in flows/approvals.json, past the deck's cap of {cap}, "
                     "so the deck would have left it out anyway.")
    if calls:
        notes.append(f"{calls} idea(s) split the reviewers, so they aren't drawn; the Needs your call page at the end "
                     "lists them.")
    if not flows and not unbuilt and not unbuilt_fallbacks and not unbuilt_approved and not calls and not held:
        notes.append("No idea passed the review. The score pages show every idea's score and why.")
    body = "".join(f"<p class='how'>{escape(n)}</p>" for n in [*notes, *status])
    return f'<section class="slide cover"><h1>Simula\'s review for {escape(app)}</h1>{body}</section>'


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


def list_slides(title: str, items: list[list[str]], footer: str) -> list[str]:
    """Pages of items, each its lines with the first in bold, packed in order onto as few pages as fit."""
    rows = [(f"<li><b>{escape(lines[0])}</b>" + "".join(f"<br>{escape(line)}" for line in lines[1:]) + "</li>",
             SCORE_ROW_PAD_PX + SCORE_LINE_PX * sum(math.ceil(len(line) / (SCORE_TITLE_CHARS + SCORE_WHY_CHARS))
                                                    for line in lines))
            for lines in items]
    pages, _ = score_pages(rows, SCORE_PAGE_PX)
    return [f'<section class="slide scores"><h2>{title}{f" ({n} of {len(pages)})" if len(pages) > 1 else ""}</h2>'
            f'<div class="score-body"><ul class="calls">{"".join(page)}</ul></div><footer>{footer}</footer></section>'
            for n, page in enumerate(pages, 1)] if items else []


def notes_slides(flows: list[dict], run_dir: Path, none_accepted: bool) -> list[str]:
    """Each drawn idea's review notes beside it: what its slides leave to this review (review_notes)."""
    items = [[f"{f['candidate'].id} · {plain(caption(f['candidate']))}", *notes] for f in flows
             if (notes := review_notes(f, run_dir, none_accepted))]
    return list_slides("Notes kept off the deck's slides", items,
                       "The deck's slides say only that these notes are here. Every walk, step by step: "
                       "<b>exhibits/07-flows.md</b>.")


def call_slides(waiting: list[Decision], candidates: dict[str, Candidate], run_dir: Path,
                set_aside: dict[str, str] | None = None) -> list[str]:
    """The last pages (D11): every idea the judges split on that no one approved, each split check as its open
    question with both sides. None of them is drawn; a person draws one by approving it."""
    items = []
    for d in waiting:
        judged = [v for _, v in verdicts(d, run_dir)]
        c = candidates.get(d.candidate_id)
        clauses = [disagreement(k, why, judged) for k, why in failed_checks(d, run_dir) if k in d.judgment_splits]
        # plain() would drop the id, and the id is what a person puts in approvals.json
        items.append([f"{d.candidate_id} · {plain(caption(c)) if c else ''}", *map(plain, clauses),
                      *([f"Not drawn: {set_aside[d.candidate_id]}."] if d.candidate_id in (set_aside or {}) else []),
                      f'To approve: {{"id": "{d.candidate_id}", "splits": {json.dumps(d.judgment_splits)}}}'])
    return list_slides("Needs your call", items,
                       "The reviewers split on these ideas, so none is drawn. To draw one, add its \"To approve\" "
                       "entry to <b>promote</b> in <b>flows/approvals.json</b> and run flows again.")


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


def closest(d: Decision, run_dir: Path, none_accepted: bool, approved: bool = False) -> bool:
    """The judge's fallback pick, or a split drawn for want of an accept (stage.survivors)."""
    return is_fallback(d, run_dir, none_accepted) or (none_accepted and needs_call(d) and not approved)


def watermark(run_dir: Path) -> str:
    return ('<div class="watermark">FIXTURE TEST DATA · not a deliverable</div>'
            if run_dir.name.endswith("-fixture") else "")


def deck(ctx: Ctx, model: ProductModel, flows: list[dict], decisions: list[Decision]) -> str:
    """The product team's deck: the cover, then each drawn idea's two slides."""
    app = app_title(model, ctx.app["name"])
    none_accepted = not any(d.final == "accept" for d in decisions)
    fallbacks = sum(closest(f["decision"], ctx.run_dir, none_accepted, f.get("approved", False)) for f in flows)
    slides = [cover_html(app, flows, fallbacks=fallbacks)]
    for flow in flows:
        slides += idea_slides(flow, model, ctx.run_dir, none_accepted)
    return deck_html(f"Rewarded-ad ideas for {escape(app)}", watermark(ctx.run_dir) + "\n".join(slides))


def review(ctx: Ctx, model: ProductModel, flows: list[dict], not_built: list[tuple[Decision, str]],
           decisions: list[Decision], candidates: dict[str, Candidate], *, cap: int, cut: int = 0,
           waiting: list[Decision] = (), set_aside: dict[str, str] | None = None, held: list[str] = (),
           held_past_cap: list[str] = (), promoted: list[str] = ()) -> str:
    """Simula's own review, kept out of the product team's deck: its cover, the notes the deck's slides leave out,
    every idea's score, and Needs your call.
    `held` names the ideas a person's hold took out of the deck or off Needs your call; `held_past_cap` the ones the
    cap left out anyway."""
    app = app_title(model, ctx.app["name"])
    none_accepted = not any(d.final == "accept" for d in decisions)
    fallbacks = sum(closest(f["decision"], ctx.run_dir, none_accepted, f.get("approved", False)) for f in flows)
    unbuilt_fallbacks = sum(closest(d, ctx.run_dir, none_accepted, d.candidate_id in promoted) for d, _ in not_built)
    unbuilt_approved = sum(needs_call(d) and d.candidate_id in promoted for d, _ in not_built)
    asked = {d.candidate_id for d in waiting}  # a set-aside approval there says why on Needs your call
    slides = [review_cover_html(app, flows, cap=cap, unbuilt=len(not_built) - unbuilt_fallbacks - unbuilt_approved,
                                fallbacks=fallbacks, unbuilt_fallbacks=unbuilt_fallbacks,
                                unbuilt_approved=unbuilt_approved, cut=cut, calls=len(waiting),
                                approved=sum(f.get("approved", False) for f in flows), held=held,
                                held_past_cap=held_past_cap,
                                set_aside={i: why for i, why in (set_aside or {}).items() if i not in asked},
                                status=unfinished_stages(ctx.run_dir))]
    slides += notes_slides(flows, ctx.run_dir, none_accepted)
    slides += score_slides(decisions, candidates, not_built, ctx.run_dir)
    slides += call_slides(waiting, candidates, ctx.run_dir, set_aside)
    return deck_html(f"Simula's review for {escape(app)}", watermark(ctx.run_dir) + "\n".join(slides))


def deck_html(title: str, slides: str) -> str:
    """The slide template filled in. Its font is inlined, so the file lays out, and the overflow check measures, the
    same on every machine: a system font differs by OS and broke lines differently on Linux."""
    faces = "".join(f'@font-face{{font-family:Inter;font-weight:{weight};src:url(data:font/woff2;base64,'
                    f'{base64.b64encode((FONTS / f"inter-latin-{weight}.woff2").read_bytes()).decode()}) format("woff2")}}\n'
                    for weight in FONT_WEIGHTS)
    return Template(TEMPLATE.read_text()).substitute(title=title, slides=slides, fonts=faces)
