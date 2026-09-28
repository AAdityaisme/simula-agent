"""Goal 2: the QA loop. Code measures the mock against the real screens and scores it; a critic explains the
numbers, a fixer edits the page, code re-wires navigation, and the best version goes to qa/approved/.

qa/round0 is the mock as stage 3 delivered it; qa/roundN is the version after N fix rounds, with the critique and
edits that made it."""

import json
import re
import shutil
from dataclasses import dataclass

from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from simula import config, llm, qa_metrics, render
from simula.config import ROOT
from simula.contracts import (SCHEMA_VERSION, ContractError, Critique, Edge, Edit, Edits, ProductModel, QAMetrics,
                              ScreenMetrics, State)
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx, mock

MAX_ROUNDS = 3
MIN_GAIN = 0.3
WEIGHTS = {"bounds": 0.5, "nav": 0.3, "ssim": 0.2}
CLICK_TIMEOUT_MS = 1500
CRITIC_MAX_TOKENS = 8000
FIXER_MAX_TOKENS = 32000
RUNTIME = re.compile(r'<style id="simula-runtime">.*?</style>\n?|<script id="simula-runtime-js">.*?</script>\n?', re.S)


@dataclass
class Version:
    """One version of the mock and everything code measured on it."""
    round: int
    html: str
    metrics: QAMetrics
    screens: list[dict]
    taps: list[dict]
    flows: list[dict]
    contract_errors: list[ContractError]

    @property
    def score(self) -> float:
        return self.metrics.score

    def failed_taps(self) -> list[dict]:
        return [t for t in self.taps if t["problem"]]

    def failed_flows(self) -> list[dict]:
        return [f for f in self.flows if f["status"] == "failed"]


def run(ctx: Ctx) -> None:
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    scope = mock.pick_scope(model)
    screens = [s.id for s in scope]
    shutil.rmtree(ctx.run_dir / "qa", ignore_errors=True)
    budget = llm.Budget.for_stage("qa", ctx.run_dir / "trace.jsonl", ctx.usd_cap)

    best = measure(ctx, model, scope, 0, (ctx.run_dir / "mock" / "index.html").read_text())
    rounds, history, stop = [summary(best, kept=True)], [], f"all {MAX_ROUNDS} rounds ran"
    for n in range(1, MAX_ROUNDS + 1):
        try:
            critique = criticize(ctx, budget, best, history, n)
            edits = fix(ctx, budget, best, critique, n)
        except (llm.LLMFailure, llm.CapReached) as e:
            stop = f"round {n} stopped before any edit: {e}"
            run_trace(ctx.run_dir, stage="qa", step=f"round{n}", decider="code", outcome="error", note=stop[:300])
            break
        html, results = apply_edits(without_runtime(best.html), edits.edits)
        html = rebuild(html, model, screens)
        write_json(ctx.run_dir / "qa" / f"round{n}" / "critique.json", critique.model_dump())
        write_json(ctx.run_dir / "qa" / f"round{n}" / "edits.json", {"edits": results})
        new = measure(ctx, model, scope, n, html)
        gain = new.score - best.score
        kept = gain >= 0
        rounds.append(summary(new, kept, results))
        history.append({"round": n, "score_before": round(best.score, 2), "score_after": round(new.score, 2),
                        "kept": kept, "fixes": [f"{f.element_id}: {f.problem}" for f in critique.fixes],
                        "rejected_edits": [r["why"] for r in results if not r["applied"]]})
        if not kept:
            stop = f"round {n} lowered the score {best.score:.2f} → {new.score:.2f}, so its edits were discarded"
            break
        best = new
        if n >= 2 and gain < MIN_GAIN:
            stop = f"round {n} gained {gain:.2f} (< {MIN_GAIN})"
            break
    run_trace(ctx.run_dir, stage="qa", step="stop", decider="code", note=stop)

    approve(ctx, best)
    report = qa_report(best, rounds, stop)
    write_json(ctx.run_dir / "qa" / "qa_report.json", report)
    write_exhibit(ctx.run_dir, 4, "qa", exhibit(ctx, model, best, rounds, report))


# ---------- measuring one version ----------

def measure(ctx: Ctx, model: ProductModel, scope: list[State], n: int, html: str) -> Version:
    """Renders one version in its own round folder and measures everything the score and the critic need."""
    round_dir = ctx.run_dir / "qa" / f"round{n}"
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "index.html").write_text(html)
    shutil.copytree(ctx.run_dir / "mock" / "assets", round_dir / "assets", dirs_exist_ok=True)
    screens = [s.id for s in scope]
    with render.open_mock(round_dir) as (page, log):
        errors = [e.model_copy(update={"detail": e.detail.replace(round_dir.resolve().as_uri() + "/", "")})
                  for e in render.check_contract(page, log, round_dir, model, screens)]
        render.screenshot_screens(page, screens, round_dir / "mock")
        dom = {}
        for sid in screens:
            page.evaluate("id => window.simula.go(id)", sid)
            dom[sid] = qa_metrics.screen_dom(page, sid)
        taps = check_taps(page, model, scope)
        flows = walk_flows(page, model, scope)
    details = [measure_screen(ctx, model, s, round_dir, *dom[s.id], taps) for s in scope]
    metrics = QAMetrics(round=n, screens=[d["metrics"] for d in details], cross_screen_failures=[],
                        score=sum(d["metrics"].score for d in details) / len(details))
    write_json(round_dir / "metrics.json", json.loads(metrics.model_dump_json()))
    run_trace(ctx.run_dir, stage="qa", step=f"round{n}", decider="code",
              note=f"score {metrics.score:.2f}, {len(errors)} contract errors, "
                   f"{sum(1 for t in taps if t['problem'])} of {len(taps)} taps failing")
    return Version(n, html, metrics, details, taps, flows, errors)


def measure_screen(ctx: Ctx, model: ProductModel, state: State, round_dir, boxes: dict, images: set[str],
                   taps: list[dict]) -> dict:
    """Scores one screen. Masked out of SSIM: the real screen's art the mock copied (so a pasted crop earns nothing),
    and regions that change between visits."""
    real = Image.open(ctx.run_dir / "model" / state.canonical_png)
    render_path = round_dir / "mock" / f"{state.id}.png"
    masked = ([e.rect_dp for e in state.elements if e.id in images]
              + [qa_metrics.device_to_dp(r, model.device) for r in state.dynamic_regions])
    pixels = qa_metrics.compare(real, Image.open(render_path), masked)
    save_png(render.content_dp(Image.open(render_path)), render_path)
    save_png(render.content_dp(real), round_dir / "real" / f"{state.id}.png")
    save_png(qa_metrics.heatmap(real, pixels["map"], pixels["keep"]), round_dir / "heatmap" / f"{state.id}.png")

    ids = mock.tagged_ids(state)
    tagged = [e for e in state.elements if e.id in ids]
    misses = [{"id": e.id, "text": e.text or e.label, "want": rect(e.rect_dp),
               "got": rect(boxes[e.id]) if e.id in boxes else "missing"}
              for e in tagged if not qa_metrics.within(boxes.get(e.id), e.rect_dp)]
    own_taps = [t for t in taps if t["screen"] == state.id]
    passed = sum(not t["problem"] for t in own_taps)
    bounds = 1 - len(misses) / len(tagged) if tagged else None
    nav = passed / len(own_taps) if own_taps else None
    # The contract's bounds and nav fields aren't optional: a dropped term stores 1.0 (vacuously, all of none pass)
    # and the score leaves it out.
    metrics = ScreenMetrics(state_id=state.id, ssim_masked=pixels["ssim"], pixelmatch_ratio=None,
                            masked_coverage=pixels["coverage"], bounds_ok_share=1.0 if bounds is None else bounds,
                            nav_pass_rate=1.0 if nav is None else nav, score=screen_score(bounds, nav, pixels["ssim"]))
    return {"metrics": metrics, "name": state.name, "tagged": len(tagged), "taps": len(own_taps),
            "taps_passed": passed, "misses": misses}


def screen_score(bounds: float | None, nav: float | None, ssim: float | None) -> float:
    """10 × (0.5 bounds + 0.3 nav + 0.2 SSIM). A term with nothing to measure (no tagged element, no tap, too little
    unmasked screen) is dropped and the others are reweighted."""
    terms = {"bounds": bounds, "nav": nav, "ssim": None if ssim is None else max(ssim, 0.0)}
    kept = {k: v for k, v in terms.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in kept)
    return 10 * sum(WEIGHTS[k] * v for k, v in kept.items()) / weight if weight else 0.0


# ---------- navigation ----------

def tap(page, edge: Edge) -> str | None:
    """Taps an edge's tag on its own screen through Playwright's hit-testing, so a covered, hidden, or zero-size
    target fails. Returns what went wrong, or None."""
    tags = page.locator(f'[data-screen="{edge.from_state}"] [data-edge="{edge.id}"]')
    if tags.count() == 0:
        return "no data-edge tag on its screen"
    transition = tags.first.get_attribute("data-transition")
    try:
        tags.first.click(timeout=CLICK_TIMEOUT_MS)
    except PlaywrightError as e:
        blocker = next((line.strip() for line in str(e).splitlines() if "intercepts pointer events" in line), "")
        return f"the tap never landed ({blocker or str(e).splitlines()[0]})"[:300]
    landed = page.evaluate("() => window.simula.state()")
    if landed != edge.to_state:
        return f"landed on {landed!r}"
    if edge.transition != "unknown" and transition != edge.transition:
        return f"data-transition={transition!r}, want {edge.transition!r}"
    return None


def check_taps(page, model: ProductModel, scope: list[State]) -> list[dict]:
    """Every in-scope edge of the model, whether or not the page carries it."""
    checks = []
    for edge in mock.scope_edges(model, scope):
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        checks.append({"screen": edge.from_state, "edge": edge.id, "problem": tap(page, edge)})
    return checks


def walk_flows(page, model: ProductModel, scope: list[State]) -> list[dict]:
    """Walks each core flow from its first screen by tapping, never jumping. A flow that leaves the mock's scope
    can't be walked and is reported as such."""
    edges = {e.id: e for e in mock.scope_edges(model, scope)}
    walks = []
    for flow in model.flows:
        walk = {"flow": flow.id, "name": flow.name, "status": "out_of_scope", "problem": None}
        if flow.edge_ids and all(i in edges for i in flow.edge_ids):
            walk["problem"] = walk_one(page, [edges[i] for i in flow.edge_ids])
            walk["status"] = "failed" if walk["problem"] else "passed"
        walks.append(walk)
    return walks


def walk_one(page, edges: list[Edge]) -> str | None:
    page.evaluate("id => window.simula.go(id)", edges[0].from_state)
    for edge in edges:
        problem = tap(page, edge)
        if problem:
            return f"{edge.id}: {problem}"
    return None


# ---------- critic and fixer ----------

def criticize(ctx: Ctx, budget: llm.Budget, version: Version, history: list[dict], n: int) -> Critique:
    role = config.roles(ctx.profile)["qa_critic"]
    round_dir = ctx.run_dir / "qa" / f"round{version.round}"
    content = []
    for s in version.screens:
        sid = s["metrics"].state_id
        for kind, what in (("real", "the real screen"), ("mock", "the mock"), ("heatmap", "the heatmap")):
            content += [{"type": "text", "text": f"{sid} ({s['name']}), {what}:"},
                        {"type": "image", "png": (round_dir / kind / f"{sid}.png").read_bytes()}]
    content.append({"type": "text", "text": "The numbers:\n" + json.dumps(numbers(version), separators=(",", ":"))
                    + "\n\nEarlier rounds:\n" + json.dumps(history, separators=(",", ":"))})
    critique, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="qa", step=f"critic r{n}",
                           model=role["model"], effort=role.get("effort"), system=prompt("critic"),
                           messages=[{"role": "user", "content": content}],
                           max_tokens=role.get("max_tokens", CRITIC_MAX_TOKENS), budget=budget, schema=Critique,
                           no_cache=ctx.no_cache, replay=ctx.replay)
    return critique


def fix(ctx: Ctx, budget: llm.Budget, version: Version, critique: Critique, n: int) -> Edits:
    role = config.roles(ctx.profile)["qa_fixer"]
    effort = role.get("effort_last_round", role.get("effort")) if n == MAX_ROUNDS else role.get("effort")
    round_dir = ctx.run_dir / "qa" / f"round{version.round}"
    content = []
    for s in version.screens:
        sid = s["metrics"].state_id
        content += [{"type": "text", "text": f"{sid} ({s['name']}), the real screen; 1 image px = 1 CSS px of its section:"},
                    {"type": "image", "png": (round_dir / "real" / f"{sid}.png").read_bytes()}]
    task = {"fixes": [f.model_dump() for f in critique.fixes], **numbers(version)}
    content.append({"type": "text", "text": "What to fix:\n" + json.dumps(task, separators=(",", ":"))
                    + "\n\nThe page, without the navigation runtime (code adds it back after your edits):\n```html\n"
                    + without_runtime(version.html) + "\n```"})
    edits, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="qa", step=f"fixer r{n}", model=role["model"],
                        effort=effort, system=prompt("fixer") + "\n\n" + mock.contract_text(),
                        messages=[{"role": "user", "content": content}],
                        max_tokens=role.get("max_tokens", FIXER_MAX_TOKENS), budget=budget, schema=Edits,
                        no_cache=ctx.no_cache, replay=ctx.replay)
    return edits


def numbers(version: Version) -> dict:
    """What code measured, in the form the critic and the fixer read."""
    screens = [{"screen": s["metrics"].state_id, "name": s["name"], "score": round(s["metrics"].score, 2),
                "ssim": None if s["metrics"].ssim_masked is None else round(s["metrics"].ssim_masked, 3),
                "data_el_within_4dp": f"{s['tagged'] - len(s['misses'])}/{s['tagged']}",
                "data_el_misses": s["misses"]} for s in version.screens]
    return {"score": round(version.score, 2), "screens": screens, "failed_taps": version.failed_taps(),
            "failed_flows": version.failed_flows(),
            "contract_errors": [e.model_dump() for e in version.contract_errors]}


def prompt(name: str) -> str:
    return (ROOT / "prompts" / "qa" / f"{name}.md").read_text()


# ---------- edits ----------

def without_runtime(html: str) -> str:
    """The page as the fixer sees it: code owns the navigation runtime, so the fixer can neither read nor edit it."""
    return RUNTIME.sub("", html)


def rebuild(html: str, model: ProductModel, screens: list[str]) -> str:
    """Code takes navigation back after every edit batch: it re-wires every model edge and adds the runtime again."""
    return mock.with_runtime(mock.wire_edges(html, model, screens), screens[0])


def apply_edits(html: str, edits: list[Edit]) -> tuple[str, list[dict]]:
    """Applies each edit in order when its find matches the page exactly once; any other edit is rejected and logged."""
    results = []
    for edit in edits:
        matches = html.count(edit.find) if edit.find else 0
        applied = matches == 1
        if applied:
            html = html.replace(edit.find, edit.replace, 1)
        results.append({**edit.model_dump(), "applied": applied,
                        "why": "" if applied else f"find matches the page {matches} times, not once"})
    return html, results


# ---------- outputs ----------

def approve(ctx: Ctx, best: Version) -> None:
    approved = ctx.run_dir / "qa" / "approved"
    approved.mkdir(parents=True)
    (approved / "index.html").write_text(best.html)
    shutil.copytree(ctx.run_dir / "mock" / "assets", approved / "assets")


def summary(version: Version, kept: bool, edits: list[dict] = ()) -> dict:
    return {"round": version.round, "score": round(version.score, 3), "kept": kept,
            "contract_errors": len(version.contract_errors), "failed_taps": len(version.failed_taps()),
            "failed_flows": len(version.failed_flows()),
            "edits_applied": sum(e["applied"] for e in edits), "edits_rejected": sum(not e["applied"] for e in edits)}


def qa_report(best: Version, rounds: list[dict], stop: str) -> dict:
    """qa_incomplete when the approved version still fails navigation; flows still run on it, with that label."""
    incomplete = bool(best.failed_taps() or best.failed_flows())
    return {"status": "qa_incomplete" if incomplete else "approved", "approved_round": best.round,
            "score": round(best.score, 3), "stop_reason": stop, "rounds": rounds,
            "screens": [{**json.loads(s["metrics"].model_dump_json()), "name": s["name"], "tagged": s["tagged"],
                         "taps": s["taps"], "data_el_misses": s["misses"]} for s in best.screens],
            "failed_taps": best.failed_taps(), "flows": best.flows,
            "contract_errors": [e.model_dump() for e in best.contract_errors]}


def exhibit(ctx: Ctx, model: ProductModel, best: Version, rounds: list[dict], report: dict) -> str:
    status = ("**approved**" if report["status"] == "approved"
              else f"**qa_incomplete**: {len(best.failed_taps())} taps and {len(best.failed_flows())} flows still fail")
    lines = [f"# QA: {model.app}", "",
             f"Status: {status}. Score {rounds[0]['score']:.2f} → {best.score:.2f} (round {best.round}, copied to "
             f"`qa/approved/`). Stop: {report['stop_reason']}. Model spend this stage: ${stage_usd(ctx):.4f}.", "",
             "Round 0 is the mock as stage 3 delivered it; round N is the page after N fix rounds.", "",
             "| Round | Score | Contract errors | Failed taps | Failed flows | Edits applied / rejected | Kept |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['round']} | {r['score']:.2f} | {r['contract_errors']} | {r['failed_taps']} | {r['failed_flows']} | "
              f"{r['edits_applied']} / {r['edits_rejected']} | {'yes' if r['kept'] else 'discarded'} |" for r in rounds]
    lines += ["", f"Approved version (round {best.round}), per screen. A term with nothing to measure shows –.", "",
              "| Screen | Name | Score | Masked SSIM (coverage) | data-el within 4 dp | Taps passing | Heatmap |",
              "|---|---|---|---|---|---|---|"]
    for s in best.screens:
        m = s["metrics"]
        ssim = "–" if m.ssim_masked is None else f"{m.ssim_masked:.3f}"
        bounds = f"{s['tagged'] - len(s['misses'])} / {s['tagged']}" if s["tagged"] else "–"
        taps = f"{s['taps_passed']} / {s['taps']}" if s["taps"] else "–"
        lines.append(f"| {m.state_id} | {s['name']} | {m.score:.2f} | {ssim} ({m.masked_coverage:.0%}) | {bounds} | "
                     f"{taps} | `qa/round{best.round}/heatmap/{m.state_id}.png` |")
    lines += ["", "| Flow | Name | Walk |", "|---|---|---|"]
    lines += [f"| {f['flow']} | {f['name']} | {f['status'].replace('_', ' ')}{': ' + f['problem'] if f['problem'] else ''} |"
              for f in best.flows]
    if best.failed_taps():
        lines += ["", "Taps that still fail:", *[f"- `{t['edge']}`: {t['problem']}" for t in best.failed_taps()]]
    if best.contract_errors:
        lines += ["", "Contract errors on the approved version:",
                  *[f"- {e.kind} ({e.screen or 'page'}): {e.detail}" for e in best.contract_errors]]
    return "\n".join(lines) + "\n"


def stage_usd(ctx: Ctx) -> float:
    return sum(line.usd for line in read_trace(ctx.run_dir / "trace.jsonl") if line.stage == "qa")


def rect(r) -> dict:
    return {k: round(v, 1) for k, v in r.model_dump().items()}


def save_png(image: Image.Image, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def write_json(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, **data}, indent=1))
