"""Goal 4: slide flows. Code picks the ideas that survived the judge and copies the approved mock for each; one model
call per idea adds the idea's new screens; code draws the simulated ad, taps through every step in Playwright, and
lays out slides for the app's product team and, apart, Simula's own review, then prints both to PDF."""

import json
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from simula import llm
from simula.contracts import Candidate, CandidatesFile, Decision, DecisionsFile, Edits, ProductModel
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx
from simula.stages.flows.deck import deck, needs_call, review
from simula.stages.flows.editor import apply_edits, ask_editor
from simula.stages.flows.page import ad_palette_css, blur_css, decline_edges, flow_page, strip_runtime, with_flow_css
from simula.stages.flows.pdf import write_pdf
from simula.stages.flows.walk import screenshot_before, walk, walk_decline, walk_failed_ad
from simula.stages.flows.wording import NOT_WIRED, REWARD_NOT_SHOWN, caption
from simula.stages.judge import ordered

MAX_IDEAS = 4
SURVIVED = ("accept", "conditional")
OVER_BUDGET = "over the $ budget before its screens could be drawn"


def load_candidates(run_dir: Path) -> dict[str, Candidate]:
    """Every proposed candidate, plus the judge's revised ones (judge/revisions.json, read as a CandidatesFile)."""
    files = [run_dir / "propose" / "candidates.json", run_dir / "judge" / "revisions.json"]
    return {c.id: c for f in files if f.exists() for c in CandidatesFile.model_validate_json(f.read_text()).candidates}


def load_approvals(flows_dir: Path) -> tuple[list[str | dict], list[str]]:
    """A person's overrides, (promote, hold), from flows/approvals.json: {"promote": [entries], "hold": [ids]}. An
    older file's "approved" list is read as promote, so it adds to what the judges draw and never drops an idea."""
    path = flows_dir / "approvals.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    return data.get("promote", []) + data.get("approved", []), data.get("hold", [])


def honored(approvals: list[str | dict], decisions: list[Decision]) -> tuple[list[str], dict[str, str]]:
    """The promoted ids flows acts on, and why each approval of a split it sets aside doesn't hold. An entry is an
    id, or {"id": ..., "splits": [checks]}: approving a split holds only while the judges split on exactly the
    checks it names, so a changed disagreement goes back to a person."""
    ids = [e if isinstance(e, str) else e["id"] for e in approvals]
    checks = {e["id"]: set(e["splits"]) for e in approvals if isinstance(e, dict)}
    stale = {d.candidate_id: ("the reviewers' disagreement changed since your approval" if d.candidate_id in checks
                              else "approved without the checks the reviewers split on")
             for d in decisions if needs_call(d) and d.candidate_id in ids
             and checks.get(d.candidate_id) != set(d.judgment_splits)}
    return [i for i in ids if i not in stale], stale


def survivors(decisions: list[Decision], promoted: list[str] = (), held: list[str] = ()) -> list[Decision]:
    """Accepted and CONDITIONAL ideas in the judge's order (every accept first, D10), leaving out an idea the judges
    split on (D11) unless nothing was accepted: then the top-ranked split is drawn as the closest idea, so a deck
    with survivors has a full slide. A person promotes a split survivor into the deck and holds any idea out of it
    (flows/approvals.json); everything else follows the judges."""
    picked = [d for d in ordered(decisions) if d.final in SURVIVED]
    splits = [d for d in picked if needs_call(d)]
    closest = splits[:1] if not any(d.final == "accept" for d in decisions) else []
    return [d for d in picked if (not needs_call(d) or d in closest or d.candidate_id in promoted)
            and d.candidate_id not in held]


def select(decisions: list[Decision], promoted: list[str] = (), held: list[str] = ()) -> list[Decision]:
    """The survivors the deck draws: the best MAX_IDEAS."""
    return survivors(decisions, promoted, held)[:MAX_IDEAS]


def mock_source(run_dir: Path) -> Path:
    approved = run_dir / "qa" / "approved"
    return approved if (approved / "index.html").exists() else run_dir / "mock"


def clean(flows_dir: Path) -> None:
    """Clears the last run's output but keeps the human's approvals.json."""
    for child in flows_dir.iterdir():
        if child.name != "approvals.json":
            shutil.rmtree(child) if child.is_dir() else child.unlink()


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
    lines += ["", "The product team's deck: `flows/slides.html`, `flows/slides.pdf`. Simula's review: "
              "`flows/review.html`, `flows/review.pdf`."]
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


@dataclass
class Turn:
    """The stage's budget as the rank-th editor call sees it (llm.call only reserves and charges). Its first hold waits
    until every better-ranked call has taken its own or finished, since an answer from the cache takes none. So when
    the $ cap can't cover every idea, the best-ranked are drawn, not whichever thread got there first."""
    budget: llm.Budget
    rank: int
    settled: set[int]
    moved: threading.Condition

    def reserve(self, worst_usd: float, **where) -> None:
        # ponytail: only first holds queue; a retried attempt's hold takes what is left, in no fixed order
        with self.moved:
            self.moved.wait_for(lambda: self.settled.issuperset(range(self.rank)))
        try:
            self.budget.reserve(worst_usd, **where)
        finally:
            self.settle()

    def charge(self, usd: float, reserved: float) -> None:
        self.budget.charge(usd, reserved)

    def settle(self) -> None:
        with self.moved:
            self.settled.add(self.rank)
            self.moved.notify_all()


def edit_all(ctx: Ctx, model: ProductModel, page: str, budget: llm.Budget, chosen: list[Decision],
             candidates: dict[str, Candidate]) -> tuple[list[tuple[Decision, Edits | None]], list[Decision]]:
    """One editor call per idea, all at once on the stage's budget, holding it in rank order (`Turn`). The ideas the $
    cap turns away come back apart, so the ones whose edits fit are still drawn; any other failure stops the stage
    before anything is walked."""
    settled, moved = set(), threading.Condition()

    def in_turn(d: Decision, turn: Turn) -> Edits | None:
        try:
            return ask_editor(ctx, candidates[d.candidate_id], model, page, turn)
        finally:
            turn.settle()

    with ThreadPoolExecutor(max_workers=max(1, len(chosen))) as pool:
        asked = [(d, pool.submit(in_turn, d, Turn(budget, rank, settled, moved))) for rank, d in enumerate(chosen)]
    edited, capped = [], []
    for d, ask in asked:
        if isinstance(ask.exception(), llm.CapReached):
            capped.append(d)
        else:
            edited.append((d, ask.result()))
    return edited, capped


def run(ctx: Ctx) -> None:
    run_dir, out = ctx.run_dir, ctx.run_dir / "flows"
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    candidates = load_candidates(run_dir)
    out.mkdir(exist_ok=True)
    promote, held = load_approvals(out)
    promoted, set_aside = honored(promote, decisions)
    clean(out)
    chosen = select(decisions, promoted, held)
    kept = {d.candidate_id for d in survivors(decisions, promoted, held)}
    cut = survivors(decisions, promoted, held)[MAX_IDEAS:]  # an approved split past the cap is cut, not asked again
    waiting = [d for d in ordered(decisions) if needs_call(d) and d.candidate_id not in kept | set(held)]
    overrides = [f"{verb} {' '.join(ids)}" for verb, ids in (("promoting", promoted), ("holding", held)) if ids]
    chosen_from = "accepted + conditional" + (f", flows/approvals.json {' and '.join(overrides)}" if overrides else "")
    past_cap = f"; past the cap of {MAX_IDEAS}, not drawn: {' '.join(d.candidate_id for d in cut)}" if cut else ""
    calls = f"; needs your call: {' '.join(d.candidate_id for d in waiting)}" if waiting else ""
    run_trace(run_dir, stage="flows", step="select", decider="code",
              note=f"{chosen_from}: {' '.join(d.candidate_id for d in chosen) or 'none'}{past_cap}{calls}")
    source = mock_source(run_dir)
    page = strip_runtime((source / "index.html").read_text())
    budget = llm.Budget.for_stage("flows", run_dir / "trace.jsonl", ctx.usd_cap)
    not_built = [(d, why) for d in chosen if (why := unbuildable(candidates.get(d.candidate_id)))]
    buildable = [d for d in chosen if not unbuildable(candidates.get(d.candidate_id))]
    edited, capped = edit_all(ctx, model, page, budget, buildable, candidates)
    not_built += [(d, OVER_BUDGET) for d in capped]
    flows = []
    for d, e in edited:
        try:
            flows.append(build_flow(ctx, model, source, candidates[d.candidate_id], d, e))
        except Exception as error:  # one idea's failure (a hung page, a broken edit) must not cost the whole deck
            shutil.rmtree(out / d.candidate_id, ignore_errors=True)
            not_built.append((d, f"{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ''}"[:300]))
    for d, why in not_built:
        run_trace(run_dir, stage="flows", step=f"build:{d.candidate_id}", decider="code", outcome="error",
                  note=f"not built: {why}"[:300])
    for flow in flows:  # a drawn split is either a person's approval or, with nothing accepted, the closest idea
        flow["approved"] = needs_call(flow["decision"]) and flow["decision"].candidate_id in promoted
    (out / "slides.html").write_text(deck(ctx, model, flows, decisions))
    (out / "review.html").write_text(review(ctx, model, flows, not_built, decisions, candidates, cap=MAX_IDEAS,
                                            cut=len(cut), waiting=waiting, set_aside=set_aside, held=held))
    layout = write_pdf(out / "slides.html") + [f"review: {p}" for p in write_pdf(out / "review.html")]
    for problem in layout:
        run_trace(run_dir, stage="flows", step="layout", decider="code", outcome="error", note=problem[:300])
    usd = sum(line.usd for line in read_trace(run_dir / "trace.jsonl") if line.stage == "flows")
    write_exhibit(run_dir, 7, "flows", exhibit(flows, not_built, chosen_from, source, run_dir, usd, layout))
