"""Goal 2: the QA loop. Code measures the mock against the real screens and scores it; a critic explains the
numbers, a fixer edits the page, code re-wires navigation, and the best version goes to qa/approved/.

qa/round0 is the mock as stage 3 delivered it; qa/roundN is the version after N fix rounds, with the critique and
edits that made it."""

import hashlib
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from simula import config, llm, qa_metrics, render, runfolder
from simula.config import ROOT
from simula.contracts import (SCHEMA_VERSION, ContractError, ContractReport, Critique, Edge, Edit, Edits, Fix,
                              ProductModel, QAMetrics, Rect, ScreenMetrics, State)
from simula.runlog import read_trace, run_trace, write_exhibit
from simula.stages import Ctx, mock

MAX_ROUNDS = 3
MIN_GAIN = 0.3
WEIGHTS = {"bounds": 0.5, "nav": 0.3, "ssim": 0.2}
KEEP_FORMULA = (f"10 × ({' + '.join(f'{w} {term}' for term, w in WEIGHTS.items())}); a term with nothing to measure "
                "is dropped and the rest reweighted. It picks the round QA keeps; it is not a fidelity percentage.")
LOWEST_SHOWN = 3
CLICK_TIMEOUT_MS = 1500
TYPED = "hello"
GESTURE_MAP = "() => JSON.parse(document.getElementById('simula-actions')?.textContent || '{}')"
CRITIC_MAX_TOKENS = 16000
CRITIC_SCREENS = 4
PARALLEL_CRITICS = 4
FIXER_MAX_TOKENS = 64000
RECORDS = llm.CACHE / "qa"
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


@dataclass
class Loop:
    """How the fix loop went: a summary per round, why it stopped, the first round a score-only keep rule would have
    decided the other way, and the critic's findings on the approved version (None when no round critiqued it)."""
    rounds: list[dict]
    stop: str
    disagreement: dict | None = None
    open_findings: list[Fix] | None = None


def run(ctx: Ctx) -> None:
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    scope = mock_screens(ctx, model)
    shutil.rmtree(ctx.run_dir / "qa", ignore_errors=True)
    delivered = measure_or_replay(ctx, model, scope, 0, (ctx.run_dir / "mock" / "index.html").read_text())
    best, loop = improve(ctx, model, scope, delivered)
    run_trace(ctx.run_dir, stage="qa", step="stop", decider="code", note=loop.stop)

    approve(ctx, best)
    report = qa_report(best, loop, undrawn_screens(ctx))
    write_json(ctx.run_dir / "qa" / "qa_report.json", report)
    write_exhibit(ctx.run_dir, 4, "qa", exhibit(ctx, model, best, loop, report))


def improve(ctx: Ctx, model: ProductModel, scope: list[State], best: Version) -> tuple[Version, Loop]:
    """The fix loop from the delivered version: critic, fixer, code re-wires, measure, keep or discard. Returns the
    best version and how the loop went."""
    screens = [s.id for s in scope]
    budget = llm.Budget.for_stage("qa", ctx.run_dir / "trace.jsonl", ctx.usd_cap)
    loop, history, critiqued = Loop(rounds=[summary(best, kept=True)], stop=f"all {MAX_ROUNDS} rounds ran"), [], {}
    for n in range(1, MAX_ROUNDS + 1):
        try:
            critique = criticize(ctx, budget, best, history, n)
            critiqued[best.round] = critique
            edits = fix(ctx, budget, model, best, critique, n)
        except (llm.LLMFailure, llm.CapReached) as e:
            loop.stop = f"round {n} stopped before any edit: {e}"
            run_trace(ctx.run_dir, stage="qa", step=f"round{n}", decider="code", outcome="error",
                      note=loop.stop[:300])
            break
        html, results = apply_edits(without_runtime(best.html), edits.edits)
        html = rebuild(html, model, screens)
        write_json(ctx.run_dir / "qa" / f"round{n}" / "critique.json", critique.model_dump())
        write_json(ctx.run_dir / "qa" / f"round{n}" / "edits.json", {"edits": results})
        try:
            new = measure_or_replay(ctx, model, scope, n, html)
        except PlaywrightError as e:
            loop.stop = f"round {n} broke the page ({str(e).splitlines()[0][:200]}), so its edits were discarded"
            break
        gain = new.score - best.score
        kept = rank(new) <= rank(best)
        loop.disagreement = loop.disagreement or keep_rule_disagreement(new, best, kept)
        loop.rounds.append(summary(new, kept, results))
        history.append({"round": n, "score_before": round(best.score, 2), "score_after": round(new.score, 2),
                        "kept": kept, "fixes": [f"{f.element_id}: {f.problem}" for f in critique.fixes],
                        "rejected_edits": [r["why"] for r in results if not r["applied"]]})
        if not kept:
            loop.stop = f"round {n} {worse(new, best)}, so its edits were discarded"
            break
        repaired = len(new.contract_errors) < len(best.contract_errors)
        best = new
        if n >= 2 and gain < MIN_GAIN and not repaired:
            loop.stop = f"round {n} gained {gain:.2f} (< {MIN_GAIN})"
            break
    critique = critiqued.get(best.round)
    loop.open_findings = None if critique is None else critique.fixes
    return best, loop


def rank(v: Version) -> tuple[int, float]:
    """Fewest contract errors first (each is a broken tap or edge in the clickable mock and the slides), then the
    higher score (which is mostly cosmetic)."""
    return len(v.contract_errors), -v.score


def worse(new: Version, best: Version) -> str:
    if len(new.contract_errors) > len(best.contract_errors):
        return f"added contract errors ({len(best.contract_errors)} → {len(new.contract_errors)})"
    return f"lowered the score {best.score:.2f} → {new.score:.2f}"


def keep_rule_disagreement(new: Version, best: Version, kept: bool) -> dict | None:
    """Where a score-only keep rule would have decided this round the other way, and the round it would approve."""
    if (new.score >= best.score) == kept:
        return None
    return {"round": new.round, "score_only_approves": best.round if kept else new.round,
            "contract_errors": [len(best.contract_errors), len(new.contract_errors)],
            "score": [round(best.score, 3), round(new.score, 3)]}


# ---------- measuring one version ----------

def measure_or_replay(ctx: Ctx, model: ProductModel, scope: list[State], n: int, html: str) -> Version:
    """Renders aren't byte-stable (image decode timing, a machine's fonts), so each measurement is recorded under the
    page and its inputs, and --replay takes the recorded numbers: the loop then decides, and asks the models, exactly
    as the recorded run did. A replay with no record stops rather than measure live. It still renders, for the round
    folder's images."""
    path = record_path("measure", n, html, inputs_digest(ctx))
    if not ctx.replay:
        version = measure(ctx, model, scope, n, html)
        write_record(path, version_record(version))
        return version
    if not path.exists():
        raise llm.ReplayMiss(f"--replay: qa has no measurement record for round {n}")
    measure(ctx, model, scope, n, html)
    version = version_from(json.loads(path.read_text()), n, html)
    write_json(ctx.run_dir / "qa" / f"round{n}" / "metrics.json", json.loads(version.metrics.model_dump_json()))
    run_trace(ctx.run_dir, stage="qa", step=f"round{n}", decider="code",
              note=f"replay: the recorded measurements, score {version.score:.2f}")
    return version


def measure(ctx: Ctx, model: ProductModel, scope: list[State], n: int, html: str) -> Version:
    """Renders one version in its own round folder and measures everything the score and the critic need, on the
    screens stage 3 drew: a placeholder for an undrawn screen is reported, never scored or fixed."""
    round_dir = ctx.run_dir / "qa" / f"round{n}"
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "index.html").write_text(html)
    shutil.copytree(ctx.run_dir / "mock" / "assets", round_dir / "assets", dirs_exist_ok=True)
    undrawn = undrawn_screens(ctx)
    drawn = [s for s in scope if s.id not in undrawn]
    screens = [s.id for s in drawn]
    with render.open_mock(round_dir) as (page, log):
        checked = render.check_contract(page, log, round_dir, model, screens,
                                        crops=mock.crop_origins(model, ctx.run_dir / "mock"))
        errors = [e.model_copy(update={"detail": e.detail.replace(round_dir.resolve().as_uri() + "/", "")})
                  for e in checked]
        render.screenshot_screens(page, screens, round_dir / "mock")
        dom = {}
        for sid in screens:
            page.evaluate("id => window.simula.go(id)", sid)
            dom[sid] = qa_metrics.screen_dom(page, sid)
        taps = check_taps(page, model, drawn)
        flows = walk_flows(page, model, scope, set(undrawn))
    details = [measure_screen(ctx, model, s, round_dir, *dom[s.id], taps) for s in drawn]
    metrics = QAMetrics(round=n, screens=[d["metrics"] for d in details], cross_screen_failures=[],
                        score=sum(d["metrics"].score for d in details) / len(details))
    write_json(round_dir / "metrics.json", json.loads(metrics.model_dump_json()))
    run_trace(ctx.run_dir, stage="qa", step=f"round{n}", decider="code",
              note=f"score {metrics.score:.2f}, {len(errors)} contract errors, "
                   f"{sum(1 for t in taps if t['problem'])} of {len(taps)} taps failing")
    return Version(n, html, metrics, details, taps, flows, errors)


def measure_screen(ctx: Ctx, model: ProductModel, state: State, round_dir, boxes: dict, images: list,
                   taps: list[dict]) -> dict:
    """Scores one screen. Masked out of SSIM: wherever a copied image sits over the spot it was cropped from (so a
    pasted crop earns nothing, and a misplaced one is scored), and regions that change between visits."""
    real = Image.open(ctx.run_dir / "model" / state.canonical_png)
    render_path = round_dir / "mock" / f"{state.id}.png"
    origins = {f"assets/{e.id}.png": e.rect_dp for e in state.elements} | art_origins(ctx, state)
    copies = [qa_metrics.overlap(origins[src], drawn) for src, drawn in images if src in origins]
    masked = ([r for r in copies if r]
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


def art_origins(ctx: Ctx, state: State) -> dict:
    """The art crops of this screen's own elements in mock/art.json, each src with the content-dp rect it was cut
    from, so art reused from another screen earns no mask here. No file, no art."""
    path = ctx.run_dir / "mock" / "art.json"
    art = json.loads(path.read_text())["art"] if path.exists() else {}
    return {mock.art_src(e.id): Rect(**art[mock.art_src(e.id)]) for e in state.elements if mock.art_src(e.id) in art}


def mock_screens(ctx: Ctx, model: ProductModel) -> list[State]:
    """Exactly the screens stage 3 put on the page, in its order, from mock/contract_report.json. QA applies no
    scope or content-rating rule of its own."""
    report = ContractReport.model_validate_json((ctx.run_dir / "mock" / "contract_report.json").read_text())
    states = {s.id: s for s in model.states}
    return [states[sid] for sid in report.screens]


def undrawn_screens(ctx: Ctx) -> dict[str, str]:
    """Screens stage 3 left as placeholders (a batch that failed), with why, from mock/contract_report.json."""
    path = ctx.run_dir / "mock" / "contract_report.json"
    report = ContractReport.model_validate_json(path.read_text()) if path.exists() else None
    return {e.screen: e.detail for e in report.errors if e.kind == "undrawn_screen"} if report else {}


def screen_score(bounds: float | None, nav: float | None, ssim: float | None) -> float:
    """10 × (0.5 bounds + 0.3 nav + 0.2 SSIM). A term with nothing to measure (no tagged element, no tap, too little
    unmasked screen) is dropped and the others are reweighted."""
    terms = {"bounds": bounds, "nav": nav, "ssim": None if ssim is None else max(ssim, 0.0)}
    kept = {k: v for k, v in terms.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in kept)
    return 10 * sum(WEIGHTS[k] * v for k, v in kept.items()) / weight if weight else 0.0


# ---------- navigation ----------

def edge_tags(page, edge: Edge):
    return page.locator(f'[data-screen="{edge.from_state}"] [data-edge="{edge.id}"]')


def tap(page, edge: Edge) -> str | None:
    """Taps an edge's tag on its own screen through Playwright's hit-testing, so a covered, hidden, or zero-size
    target fails. Returns what went wrong, or None."""
    tags = edge_tags(page, edge)
    if tags.count() == 0:
        return "no data-edge tag on its screen"
    transition = tags.first.get_attribute("data-transition")
    box, screen = tags.first.bounding_box(), page.locator(f'[data-screen="{edge.from_state}"]').bounding_box()
    if box and screen and not (screen["x"] <= box["x"] + box["width"] / 2 <= screen["x"] + screen["width"]
                               and screen["y"] <= box["y"] + box["height"] / 2 <= screen["y"] + screen["height"]):
        return "the tag sits outside its screen"
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


def gesture(page, edge: Edge, gestures: dict) -> str | None:
    """Takes an edge that starts on no element through real input, as the page's gesture map (`GESTURE_MAP`) says
    its runtime performs it: a drag up its screen, Escape, or typing into the screen's text field and Enter.
    Returns what went wrong, or None."""
    mapped = gestures.get(edge.from_state, {}).get(edge.action)
    if not mapped or mapped[0] != edge.to_state:
        return f"the page has no {edge.action} from {edge.from_state} to {edge.to_state}"
    if edge.action == "swipe":
        drag_up(page, edge.from_state)
    elif edge.action == "back":
        page.keyboard.press("Escape")
    else:
        problem = type_into(page, edge.from_state, mapped[2])
        if problem:
            return problem
    landed = page.evaluate("() => window.simula.state()")
    return None if landed == edge.to_state else f"landed on {landed!r}"


def drag_up(page, screen: str) -> None:
    box = page.locator(f'[data-screen="{screen}"]').bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.7
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - box["height"] * 0.4, steps=8)
    page.mouse.up()


def type_into(page, screen: str, field: str | None) -> str | None:
    if field is None:
        return "its screen has no text field to type into"
    box = page.locator(f'[data-screen="{screen}"] [data-el="{field}"]')
    if box.count() == 0:
        return f"no tag carries its text field {field}"
    try:
        box.first.click(timeout=CLICK_TIMEOUT_MS)
    except PlaywrightError as e:
        return f"the text field never took the tap ({str(e).splitlines()[0]})"[:300]
    page.keyboard.type(TYPED)
    page.keyboard.press("Enter")
    return None


def take(page, edge: Edge, gestures: dict) -> tuple[str | None, bool]:
    """One hop the way a person takes it: a tap on the tag the page carries for it (a back edge's too, on a drawn
    back control), else the gesture of an edge that starts on no element. Returns what went wrong, or None, and
    whether the hop was a gesture."""
    if edge.element_id or edge_tags(page, edge).count():
        return tap(page, edge), False
    return gesture(page, edge, gestures), True


def check_taps(page, model: ProductModel, scope: list[State]) -> list[dict]:
    """Every in-scope edge of the model that starts on an element, whether or not the page carries it. An edge with
    no element is taken by its gesture in the flow walks."""
    checks = []
    for edge in (e for e in mock.scope_edges(model, scope) if e.element_id):
        page.evaluate("id => window.simula.go(id)", edge.from_state)
        checks.append({"screen": edge.from_state, "edge": edge.id, "problem": tap(page, edge)})
    return checks


def walk_flows(page, model: ProductModel, scope: list[State], undrawn: set[str] = frozenset()) -> list[dict]:
    """Walks each core flow the way a person would: simula.go only puts the page on the flow's first screen (setup,
    never evidence), and every hop is then taken by real input (`take`). A flow that leaves the mock's scope can't be
    walked, nor one through a screen stage 3 didn't draw; each is reported as such. A failed walk names the screen
    of the hop that broke; `gestures` lists the hops taken by a gesture rather than a tap."""
    edges = {e.id: e for e in mock.scope_edges(model, scope)}
    gestures = page.evaluate(GESTURE_MAP)
    walks = []
    for flow in model.flows:
        walk = {"flow": flow.id, "name": flow.name, "status": "out_of_scope", "problem": None, "screen": None,
                "gestures": []}
        hops = [edges[i] for i in flow.edge_ids if i in edges]
        if flow.edge_ids and len(hops) == len(flow.edge_ids):
            if any({e.from_state, e.to_state} & undrawn for e in hops):
                walk["status"] = "undrawn"
            else:
                walk["screen"], walk["problem"], walk["gestures"] = walk_one(page, hops, gestures)
                walk["status"] = "failed" if walk["problem"] else "passed"
        walks.append(walk)
    return walks


def walk_one(page, edges: list[Edge], gestures: dict) -> tuple[str | None, str | None, list[str]]:
    """Takes one flow's hops in order; returns the screen and problem of the hop that broke (or None, None) and the
    hops taken by gesture."""
    page.evaluate("id => window.simula.go(id)", edges[0].from_state)
    gestured = []
    for edge in edges:
        problem, by_gesture = take(page, edge, gestures)
        if by_gesture:
            gestured.append(edge.id)
        if problem:
            return edge.from_state, f"{edge.id}: {problem}", gestured
    return None, None, gestured


# ---------- critic and fixer ----------

def criticize(ctx: Ctx, budget: llm.Budget, version: Version, history: list[dict], n: int) -> Critique:
    """One critic call per group of at most CRITIC_SCREENS screens, in the order QA measured them (the mock's order),
    at most PARALLEL_CRITICS at once; their fixes merge by data-el. A group whose call fails is skipped, unless every
    group fails."""
    screens = version.screens
    groups = [screens[i:i + CRITIC_SCREENS] for i in range(0, len(screens), CRITIC_SCREENS)] or [[]]

    def one(k: int, group: list[dict]):
        try:
            return criticize_group(ctx, budget, version, history, n, k, group)
        except llm.LLMFailure as e:
            return e

    # ponytail: llm.Budget doesn't hold a call's worst case while it is in flight, so parallel groups can pass the
    # cap by what they spend together (same as the mock's batches); PR 5's Budget hold fixes it.
    with ThreadPoolExecutor(PARALLEL_CRITICS) as pool:
        results = list(pool.map(one, range(1, len(groups) + 1), groups))
    critiques = [r for r in results if isinstance(r, Critique)]
    if not critiques:
        raise results[0]
    for k, r in enumerate(results, 1):
        if not isinstance(r, Critique):
            run_trace(ctx.run_dir, stage="qa", step=f"critic r{n} g{k}", decider="code", outcome="error",
                      note=f"group skipped, the other groups' fixes are used: {r}"[:300])
    return merge_critiques(critiques)


def criticize_group(ctx: Ctx, budget: llm.Budget, version: Version, history: list[dict], n: int, k: int,
                    group: list[dict]) -> Critique:
    """The critic on one group. Page-wide contract errors (no screen) go to the first group only."""
    role = config.roles(ctx.profile)["qa_critic"]
    round_dir = ctx.run_dir / "qa" / f"round{version.round}"
    content = []
    for s in group:
        sid = s["metrics"].state_id
        for kind, what in (("real", "the real screen"), ("mock", "the mock"), ("heatmap", "the heatmap")):
            content += [{"type": "text", "text": f"{sid} ({s['name']}), {what}:"},
                        {"type": "image", "png": (round_dir / kind / f"{sid}.png").read_bytes()}]
    ids = {s["metrics"].state_id for s in group} | ({None} if k == 1 else set())
    content.append({"type": "text", "text": "The numbers:\n" + json.dumps(numbers(version, ids), separators=(",", ":"))
                    + "\n\nEarlier rounds:\n" + json.dumps(history, separators=(",", ":"))})
    return ask(ctx, budget, version, step=f"critic r{n} g{k}", model=role["model"], effort=role.get("effort"),
               system=prompt("critic"), content=content, max_tokens=role.get("max_tokens", CRITIC_MAX_TOKENS),
               schema=Critique)


def merge_critiques(critiques: list[Critique]) -> Critique:
    """The groups' fix lists as one, in group order; a data-el two groups both name keeps the first group's fix."""
    fixes = {}
    for critique in critiques:
        for f in critique.fixes:
            fixes.setdefault(f.element_id, f)
    return Critique(fixes=list(fixes.values()), summary=" ".join(c.summary for c in critiques))


def fix(ctx: Ctx, budget: llm.Budget, model: ProductModel, version: Version, critique: Critique, n: int) -> Edits:
    """The fixer gets the whole page, but only the screens the critique names (and their numbers)."""
    role = config.roles(ctx.profile)["qa_fixer"]
    effort = role.get("effort_last_round", role.get("effort")) if n == MAX_ROUNDS else role.get("effort")
    round_dir = ctx.run_dir / "qa" / f"round{version.round}"
    named = named_screens(model, critique)
    content = []
    for s in (s for s in version.screens if s["metrics"].state_id in named):
        sid = s["metrics"].state_id
        content += [{"type": "text", "text": f"{sid} ({s['name']}), the real screen; 1 image px = 1 CSS px of its section:"},
                    {"type": "image", "png": (round_dir / "real" / f"{sid}.png").read_bytes()}]
    task = {"fixes": [f.model_dump() for f in critique.fixes], **numbers(version, named | {None})}
    content.append({"type": "text", "text": "What to fix:\n" + json.dumps(task, separators=(",", ":"))
                    + "\n\nThe page, without the navigation runtime (code adds it back after your edits):\n```html\n"
                    + without_runtime(version.html) + "\n```"})
    return ask(ctx, budget, version, step=f"fixer r{n}", model=role["model"], effort=effort,
               system=prompt("fixer") + "\n\n" + mock.contract_text(), content=content,
               max_tokens=role.get("max_tokens", FIXER_MAX_TOKENS), schema=Edits)


def named_screens(model: ProductModel, critique: Critique) -> set[str]:
    """The screens a critique's fixes are on: each fix names a data-el id or a screen id."""
    owner = {e.id: s.id for s in model.states for e in s.elements} | {s.id: s.id for s in model.states}
    return {owner[f.element_id] for f in critique.fixes if f.element_id in owner}


def ask(ctx: Ctx, budget: llm.Budget, version: Version, *, step: str, model: str, effort: str | None, system: str,
        content: list[dict], max_tokens: int, schema):
    """llm.call, with the outcome also recorded under what the model was shown, each render stood in for by the page
    it was rendered from (the real screens are in the inputs). --replay looks there first, so it never depends on
    render bytes. A failed call is recorded too, so a replay fails the same way and follows the same path."""
    texts = [p["text"] for p in content if p["type"] == "text"]
    path = record_path(step, model, effort, max_tokens, system, version.html, texts, inputs_digest(ctx))
    if ctx.replay and path.exists():
        record = json.loads(path.read_text())
        run_trace(ctx.run_dir, stage="qa", step=step, decider="model", model=model, effort=effort, cache_hit=True,
                  outcome=record.get("failure", "ok"), note="replay: the outcome recorded for this page")
        if "failure" in record:
            raise llm.LLMFailure(record["failure"], record["detail"])
        return schema.model_validate(record)
    try:
        answer, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="qa", step=step, model=model,
                             effort=effort, system=system, messages=[{"role": "user", "content": content}],
                             max_tokens=max_tokens, budget=budget, schema=schema, no_cache=ctx.no_cache,
                             replay=ctx.replay)
    except llm.LLMFailure as e:
        write_record(path, json.dumps({"failure": e.outcome, "detail": str(e).removeprefix(f"{e.outcome}: ")}))
        raise
    write_record(path, answer.model_dump_json())
    return answer


def numbers(version: Version, ids: set | None = None) -> dict:
    """What code measured, in the form the critic and the fixer read: for the screens in ids (None in ids takes the
    page-wide contract errors), or everything."""
    def on(screen) -> bool:
        return ids is None or screen in ids
    screens = [{"screen": s["metrics"].state_id, "name": s["name"], "score": round(s["metrics"].score, 2),
                "ssim": None if s["metrics"].ssim_masked is None else round(s["metrics"].ssim_masked, 3),
                "data_el_within_4dp": f"{s['tagged'] - len(s['misses'])}/{s['tagged']}",
                "data_el_misses": s["misses"]} for s in version.screens if on(s["metrics"].state_id)]
    return {"score": round(version.score, 2), "screens": screens,
            "failed_taps": [t for t in version.failed_taps() if on(t["screen"])],
            "failed_flows": [f for f in version.failed_flows() if on(f["screen"])],
            "contract_errors": [e.model_dump() for e in version.contract_errors if on(e.screen)]}


def prompt(name: str) -> str:
    return (ROOT / "prompts" / "qa" / f"{name}.md").read_text()


# ---------- edits ----------

def without_runtime(html: str) -> str:
    """The page as the fixer sees it: code owns the navigation runtime and the gesture map, so the fixer can neither
    read nor edit them. rebuild writes both back."""
    return mock.ACTIONS_BLOCK.sub("", RUNTIME.sub("", html))


def rebuild(html: str, model: ProductModel, screens: list[str]) -> str:
    """Code takes navigation back after every edit batch: it re-wires every model edge and adds the runtime again,
    opening on the mock's home screen. screens keep the mock's order, which decides the home."""
    states = {s.id: s for s in model.states}
    return mock.with_runtime(mock.wire_edges(html, model, screens), mock.home_id([states[sid] for sid in screens]))


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


# ---------- replay records ----------

def inputs_digest(ctx: Ctx) -> str:
    """What QA reads besides the page: the model (not the run it was written in), the real screens, assets and art
    it draws from, and which screens stage 3 left undrawn."""
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    files = [ctx.run_dir / "model" / s.canonical_png for s in model.states]
    files += [ctx.run_dir / "mock" / "assets", ctx.run_dir / "mock" / "art.json"]
    return digest([model.model_dump(mode="json", exclude={"run_id", "provenance"}),
                   [h.model_dump() for h in runfolder.hashes(files, ctx.run_dir)], undrawn_screens(ctx)])


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def record_path(*key):
    return RECORDS / f"{digest(key)}.json"


def write_record(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def version_record(v: Version) -> str:
    return json.dumps({"metrics": json.loads(v.metrics.model_dump_json()),
                       "screens": [{**s, "metrics": json.loads(s["metrics"].model_dump_json())} for s in v.screens],
                       "taps": v.taps, "flows": v.flows,
                       "contract_errors": [e.model_dump(mode="json") for e in v.contract_errors]})


def version_from(record: dict, n: int, html: str) -> Version:
    return Version(n, html, QAMetrics.model_validate(record["metrics"]),
                   [{**s, "metrics": ScreenMetrics.model_validate(s["metrics"])} for s in record["screens"]],
                   record["taps"], record["flows"],
                   [ContractError.model_validate(e) for e in record["contract_errors"]])


# ---------- outputs ----------

def approve(ctx: Ctx, best: Version) -> None:
    approved = ctx.run_dir / "qa" / "approved"
    approved.mkdir(parents=True)
    (approved / "index.html").write_text(best.html)
    shutil.copytree(ctx.run_dir / "mock" / "assets", approved / "assets")


def summary(version: Version, kept: bool, edits: list[dict] = ()) -> dict:
    return {"round": version.round, "keep_score": round(version.score, 3), "kept": kept,
            "contract_errors": len(version.contract_errors), "failed_taps": len(version.failed_taps()),
            "failed_flows": len(version.failed_flows()),
            "edits_applied": sum(e["applied"] for e in edits), "edits_rejected": sum(not e["applied"] for e in edits)}


def qa_report(best: Version, loop: Loop, undrawn: dict[str, str]) -> dict:
    """qa_incomplete when the approved version still fails navigation; flows still run on it, with that label.
    keep_score is the loop's keep metric. structure, interaction and visual report fidelity apart from it, with no
    pass mark, and open_findings carries what the critic still saw on the approved version."""
    incomplete = bool(best.failed_taps() or best.failed_flows())
    return {"status": "qa_incomplete" if incomplete else "approved", "approved_round": best.round,
            "keep_score": round(best.score, 3), "keep_score_formula": KEEP_FORMULA,
            "structure": structure(best), "interaction": interaction(best), "visual": visual(best),
            "open_findings": None if loop.open_findings is None else [f.model_dump() for f in loop.open_findings],
            "stop_reason": loop.stop, "rounds": loop.rounds, "keep_rule_disagreement": loop.disagreement,
            "undrawn_screens": [{"screen": sid, "reason": why} for sid, why in undrawn.items()],
            "screens": [{**json.loads(s["metrics"].model_dump_json()), "name": s["name"], "tagged": s["tagged"],
                         "taps": s["taps"], "data_el_misses": s["misses"]} for s in best.screens],
            "failed_taps": best.failed_taps(), "flows": best.flows,
            "contract_errors": [e.model_dump() for e in best.contract_errors]}


def structure(best: Version) -> dict:
    """Tagged elements drawn within 4 dp of where the real screen has them, pooled over the scored screens."""
    tagged = sum(s["tagged"] for s in best.screens)
    return {"tagged": tagged, "within_4dp": tagged - sum(len(s["misses"]) for s in best.screens)}


def interaction(best: Version) -> dict:
    """Taps that land where the model says, and core flows a person can walk by real input."""
    return {"taps": len(best.taps), "taps_passing": len(best.taps) - len(best.failed_taps()),
            "flows": len(best.flows), "flows_walked": sum(f["status"] == "passed" for f in best.flows)}


def visual(best: Version) -> dict:
    """Masked SSIM on its own: the mean, and every scored screen from the lowest up. No pass mark: SSIM punishes a
    line of text 2 px off about as hard as a missing picture, so it ranks screens for a person to look at."""
    scored = sorted(((s["metrics"].state_id, s["metrics"].ssim_masked) for s in best.screens
                     if s["metrics"].ssim_masked is not None), key=lambda pair: pair[1])
    return {"masked_ssim_mean": round(sum(v for _, v in scored) / len(scored), 3) if scored else None,
            "screens_by_ssim": [{"screen": sid, "ssim": round(v, 3)} for sid, v in scored]}


def fidelity_lines(report: dict, start: float) -> list[str]:
    """The exhibit's three fidelity lines and what the keep score is."""
    st, it, vis = report["structure"], report["interaction"], report["visual"]
    lowest = ", ".join(f"{s['screen']} {s['ssim']:.3f}" for s in vis["screens_by_ssim"][:LOWEST_SHOWN])
    ssim = f"mean {vis['masked_ssim_mean']:.3f}, lowest {lowest}" if vis["screens_by_ssim"] else "nothing to score"
    return [f"- Structure: {st['within_4dp']} of {st['tagged']} tagged elements within 4 dp of the real screen.",
            f"- Interaction: {it['taps_passing']} of {it['taps']} taps land; "
            f"{it['flows_walked']} of {it['flows']} core flows walked by real input.",
            f"- Visual: masked SSIM {ssim} (see the heatmaps). No pass mark.",
            f"- Keep score {start:.2f} → {report['keep_score']:.2f}: {report['keep_score_formula']}"]


def exhibit(ctx: Ctx, model: ProductModel, best: Version, loop: Loop, report: dict) -> str:
    status = ("**approved**" if report["status"] == "approved"
              else f"**qa_incomplete**: {len(best.failed_taps())} taps and {len(best.failed_flows())} flows still fail")
    lines = [f"# QA: {model.app}", "",
             f"Status: {status}. Round {best.round} is approved and copied to `qa/approved/`. Stop: "
             f"{report['stop_reason']}. Model spend this stage: ${stage_usd(ctx):.4f}.", "",
             *fidelity_lines(report, loop.rounds[0]["keep_score"]), "",
             "Round 0 is the mock as stage 3 delivered it; round N is the page after N fix rounds. A round is kept "
             "when it has fewer contract errors, or as many and a keep score at least as high. "
             + keep_rule_line(report),
             *(["", "Replayed: the scores are the recorded run's; the images in `qa/round<N>/` are this machine's "
                    "renders, so they can differ slightly from what was scored."] if ctx.replay else []),
             "",
             "| Round | Keep score | Contract errors | Failed taps | Failed flows | Edits applied / rejected | Kept |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['round']} | {r['keep_score']:.2f} | {r['contract_errors']} | {r['failed_taps']} | "
              f"{r['failed_flows']} | {r['edits_applied']} / {r['edits_rejected']} | "
              f"{'yes' if r['kept'] else 'discarded'} |" for r in loop.rounds]
    lines += ["", f"Approved version (round {best.round}), per screen. A term with nothing to measure shows –.", "",
              "| Screen | Name | Keep score | Masked SSIM (coverage) | data-el within 4 dp | Taps passing | Heatmap |",
              "|---|---|---|---|---|---|---|"]
    for s in best.screens:
        m = s["metrics"]
        ssim = "–" if m.ssim_masked is None else f"{m.ssim_masked:.3f}"
        bounds = f"{s['tagged'] - len(s['misses'])} / {s['tagged']}" if s["tagged"] else "–"
        taps = f"{s['taps_passed']} / {s['taps']}" if s["taps"] else "–"
        lines.append(f"| {m.state_id} | {s['name']} | {m.score:.2f} | {ssim} ({m.masked_coverage:.0%}) | {bounds} | "
                     f"{taps} | `qa/round{best.round}/heatmap/{m.state_id}.png` |")
    lines += ["", "| Flow | Name | Walk |", "|---|---|---|"]
    lines += [f"| {f['flow']} | {f['name']} | {f['status'].replace('_', ' ')}{': ' + f['problem'] if f['problem'] else ''}"
              f"{' (by gesture: ' + ', '.join(f['gestures']) + ')' if f['gestures'] else ''} |"
              for f in best.flows]
    if report["undrawn_screens"]:
        lines += ["", "Screens stage 3 didn't draw (placeholders: not scored, not sent to the critic or the fixer):",
                  *[f"- {u['screen']}: {u['reason']}" for u in report["undrawn_screens"]]]
    if best.failed_taps():
        lines += ["", "Taps that still fail:", *[f"- `{t['edge']}`: {t['problem']}" for t in best.failed_taps()]]
    if best.contract_errors:
        lines += ["", "Contract errors on the approved version:",
                  *[f"- {e.kind} ({e.screen or 'page'}): {e.detail}" for e in best.contract_errors]]
    if loop.open_findings:
        lines += ["", "What the critic still saw on the approved version (its fixes were discarded or never made):",
                  *[f"- `{f.element_id}`: {f.problem}" for f in loop.open_findings]]
    lines += ["", "Known limit: when the critic refuses a group of screens, that group gets no fixes that round "
                  "(and the round stops if every group refuses). The refused images aren't bisected out to keep the "
                  "rest of the group."]
    return "\n".join(lines) + "\n"


def keep_rule_line(report: dict) -> str:
    d = report["keep_rule_disagreement"]
    if not d:
        return "A score-only rule would have approved the same round."
    return (f"A score-only rule would have approved round {d['score_only_approves']} instead: round {d['round']} went "
            f"from {d['contract_errors'][0]} to {d['contract_errors'][1]} contract errors and from score "
            f"{d['score'][0]:.2f} to {d['score'][1]:.2f}.")


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
