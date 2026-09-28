"""Stage 7 offline, on every golden: selection, the tap-through, the reward rule, and the deck's parts."""

import html
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from simula import llm, render
from simula.contracts import (GATES, JUDGMENT, CandidatesFile, Check, Decision, DecisionsFile, Economics, Edit, Edits,
                              Verdict)
from simula.runlog import read_trace
from simula.stages import flows
from simula.stages.mock import copy_assets, pick_scope, with_runtime
from simula.stages.propose import anchor_ids, with_bucket
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, skeleton_html
from tests.propose_fixtures import anchored, candidate
from tests.test_mock_isolation import ctx_for

COST_LINE = "Costs nothing extra to serve, so any completed view pays for it above $0.00 eCPM."
ROUND6 = FIXTURES / "flows" / "luzia-round6"


def decision(cid: str, final: str, rank: float, passed: int = 11) -> Decision:
    return Decision(candidate_id=cid, final=final, checks_passed=passed, checks_total=11, rank_score=rank,
                    gate_fails=[], judgment_splits=[], verdict_paths=[f"judge/verdicts/{cid}_judge_1_r1.json"],
                    economics_verdict=None, revision_of=None, failure_type=None, rerun_stage=None)


def verdict(cid: str, fail: str | None = None) -> Verdict:
    checks = {k: Check(passed=k != fail, reason=f"{k} reason for {cid}") for k in GATES + JUDGMENT}
    return Verdict(candidate_id=cid, **checks, other_concern="", fixable=False)


def seed_run(root, app: str, changes: dict | None = None):
    """A fixture run folder: the golden model, a skeleton mock, four candidates, and a judge's decisions. changes
    maps an idea id to field updates."""
    run_dir = root / "runs" / app / "20260928-000000-test-fixture"
    model = golden(app)
    shutil.copytree(FIXTURES / "golden" / app, run_dir / "model")
    scope = pick_scope(model)
    copy_assets(FIXTURES / "golden" / app, run_dir / "mock", scope, model.device)
    (run_dir / "mock" / "index.html").write_text(with_runtime(skeleton_html(model), scope[0].id))
    economics = Economics(cost_2k=0, cost_8k=0, breakeven_ecpm_2k=0, breakeven_ecpm_8k=0, benchmark_ecpm=9.2,
                          verdict="PASS", assumption_line=COST_LINE)
    second = anchored if anchor_ids(model) else candidate
    ideas = [candidate(model, id="c01", title="A daily bonus"),
             second(model, id="c02", title="A second look", offer_copy="Play a short game for one more try today."),
             candidate(model, id="c03", title="A rejected idea"),
             candidate(model, id="c04", title="A dropped idea", dropped_reason="duplicate of c01: same benefit")]
    ideas = [with_bucket(c).model_copy(update={"economics": economics, "reach_score": 1.0, "rank_score": 1.0,
                                               **(changes or {}).get(c.id, {})}) for c in ideas]
    (run_dir / "propose").mkdir()
    (run_dir / "propose" / "candidates.json").write_text(CandidatesFile(candidates=ideas).model_dump_json())
    decisions = [decision("c01", "accept", 1.0), decision("c02", "conditional", 0.5, passed=10),
                 decision("c03", "reject", 2.0, passed=9), decision("c04", "reject", 0.1)]
    (run_dir / "judge" / "verdicts").mkdir(parents=True)
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=decisions).model_dump_json())
    for cid, fail in (("c01", None), ("c02", "c5_moment"), ("c03", "g_brand_safety"), ("c04", None)):
        path = run_dir / "judge" / "verdicts" / f"{cid}_judge_1_r1.json"
        path.write_text(verdict(cid, fail).model_dump_json())
    return run_dir


def fake_edits(c, page: str, wire_accept: bool = True, ad_section: str = "marked", block_play: bool = False,
               decline_to: str | None = None, show_copy: bool = True, break_page: bool = False) -> Edits:
    """What a good editor returns for any idea: an entry point on the trigger, the offer, and an empty ad screen.
    The options plant one defect each: ad_section "unmarked" draws the ad screen without data-ad and "missing" leaves
    it out; block_play covers the game's Play button; decline_to sends "No thanks" to a new screen instead of back;
    show_copy=False drops the offer copy; break_page leaves an HTML comment open, which kills the page's scripts."""
    trigger, offer, ad = (s.state_id for s in c.flow_steps[:3])
    tag = re.search(rf'<section[^>]*data-screen="{trigger}"[^>]*>', page).group(0)
    button = 'style="position:absolute;left:20px;top:{}px;z-index:5"'
    entry = (f'<button data-edge="{trigger}>{offer}" data-transition="modal" {button.format(20)}>Get it</button>'
             f'<div data-reward {button.format(70)}>Badge on</div>')
    accept = f'<button data-edge="{offer}>{ad}" data-transition="modal" {button.format(300)}>Play</button>'
    ad_attrs = {"marked": f' data-parent="{trigger}" data-ad', "unmarked": ""}
    no = decline_to or trigger
    screens = (f'<section data-screen="{offer}" data-flow="{c.id}" data-parent="{trigger}">'
               f'<p {button.format(200)}>{c.offer_copy if show_copy else "A special offer"}</p>'
               f'{accept if wire_accept else ""}'
               f'<button data-edge="{offer}>{no}" data-transition="back" {button.format(360)}>No thanks</button>'
               "</section>")
    if decline_to:
        screens += f'<section data-screen="{decline_to}" data-flow="{c.id}"><p>Here is your reward</p></section>'
    if ad_section in ad_attrs:
        screens += (f'<section data-screen="{ad}" data-flow="{c.id}"{ad_attrs[ad_section]}>'
                    "<p>the editor's own game</p></section>")
    edits = [Edit(find=tag, replace=tag + ("<!--" if break_page else "") + entry, reason="entry point"),
             Edit(find="</body>", replace=screens + "</body>", reason="offer and ad screens"),
             Edit(find="not in the page", replace="x", reason="a find that can't apply")]
    if block_play:
        edits.append(Edit(find="</head>", replace="<style>.sa-play{pointer-events:none}</style></head>",
                          reason="something covers Play"))
    return Edits(edits=edits)


def fake_editor(run_dir, calls: list | None = None, only: str | None = None, **options):
    """A stand-in for the editor call. The options apply to every idea, or only to the idea `only` names."""
    ideas = {c.id: c for c in CandidatesFile.model_validate_json(
        (run_dir / "propose" / "candidates.json").read_text()).candidates}

    def call(**kwargs):
        cid = kwargs["step"].split(":")[1]
        if calls is not None:
            calls.append(cid)
        page = flows.strip_runtime((run_dir / "mock" / "index.html").read_text())
        return fake_edits(ideas[cid], page, **(options if only in (None, cid) else {})), None
    return call


def run_flows(root, app: str, changes: dict | None = None, calls: list | None = None, **options):
    run_dir = seed_run(root, app, changes)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir, calls, **options))
        flows.run(ctx_for(run_dir, app))
    return run_dir


@pytest.fixture(scope="module", params=APPS)
def built(request, tmp_path_factory):
    return run_flows(tmp_path_factory.mktemp(request.param), request.param)


def golden_idea(run_dir, cid: str = "c01"):
    ideas = CandidatesFile.model_validate_json((run_dir / "propose" / "candidates.json").read_text()).candidates
    return next(c for c in ideas if c.id == cid)


def slides(run_dir) -> list[tuple[str, str, str]]:
    """(idea, part, visible text) for every main slide."""
    deck = (run_dir / "flows" / "slides.html").read_text()
    found = re.findall(r'<section class="slide main" data-part="(\w+)" data-idea="(\w+)">(.*?)</section>', deck, re.S)
    return [(idea, part, html.unescape(re.sub(r"<[^>]+>", " ", body))) for part, idea, body in found]


def test_each_idea_gets_the_four_flow_parts_a_why_slide_and_every_score_goes_in_the_appendix(built):
    parts = {}
    for idea, part, _ in slides(built):
        parts.setdefault(idea, []).append(part)
    assert parts == {"c01": list(flows.PARTS), "c02": list(flows.PARTS)}
    deck = (built / "flows" / "slides.html").read_text()
    appendix = deck[deck.index('<section class="appendix">'):]
    for cid, score in (("c01", "11/11"), ("c02", "10/11"), ("c03", "9/11")):
        assert f"{cid} · " in appendix and f"{score} checks passed" in appendix
    assert "c5_moment reason for c02" in appendix and "g_brand_safety reason for c03" in appendix
    assert html.escape(COST_LINE) in appendix
    assert "FIXTURE TEST DATA" in deck
    assert (built / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")
    assert "## Not wired" not in (built / "exhibits" / "07-flows.md").read_text()


def test_main_slides_carry_no_ids_or_cost_math(built):
    texts = [text for _, _, text in slides(built)]
    assert texts
    for text in texts:
        assert not re.search(r"\b(?:[sc]\d{2}(?:\.e\d+)?|M\d+)\b|new:|\$|eCPM|REWARD_VERIFIED|judge_1", text), text


def test_a_conditional_idea_names_the_check_it_failed_in_plain_words_and_carries_its_label(built):
    for idea, part, text in slides(built):
        assert ("Conditional" in text) == (idea == "c02")
        if part == "why" and idea == "c02":
            assert "It didn't pass the right moment" in text and "c5_moment" not in text


def test_why_slides_on_real_output_never_claim_a_failed_check_that_passed():
    decisions = DecisionsFile.model_validate_json((ROUND6 / "judge" / "decisions.json").read_text()).decisions
    candidates = flows.load_candidates(ROUND6)
    chosen = flows.select(decisions, None)
    assert len(chosen) == 4
    for d in chosen:
        c = candidates[d.candidate_id]
        flow = {"candidate": c, "decision": d, "shots": [{"caption": s.caption} for s in c.flow_steps], "ad_at": 2}
        text = html.unescape(re.sub(r"<[^>]+>", " ", flows.why_html(flow, golden("luzia"), ROUND6)))
        assert not re.search(r"\$|eCPM|\b(?:g|c\d)_[a-z]", text), text
        if d.checks_passed == d.checks_total:
            assert "didn't pass" not in text and "not every" not in text
        if d.final == "conditional" and d.checks_passed == d.checks_total:
            assert "It passed every check" in text
        if c.economics.verdict == "FAIL":
            assert "it may cost more to serve than a view earns" in text


def test_a_failed_check_is_named_with_a_cost_question_and_no_numbers():
    c = candidate(golden("aol")).model_copy(update={"economics": Economics(
        cost_2k=0.034, cost_8k=0.034, breakeven_ecpm_2k=34, breakeven_ecpm_8k=34, benchmark_ecpm=9.2,
        verdict="CONDITIONAL", assumption_line="Costs ~$0.0340 per reward to serve.")})
    d = decision("c01", "conditional", 1.0, passed=10)
    text = flows.condition(d, c, ROUND6.parent / "nowhere")
    assert text == "It passed 10 of 11 checks; the appendix shows which. Also, a view pays for what it costs to " \
                   "serve only where ad prices are high; the cost line in the appendix has the numbers."
    free = c.model_copy(update={"economics": c.economics.model_copy(update={"cost_2k": 0})})
    assert "wasn't observed" in flows.condition(decision("c01", "conditional", 1.0), free, ROUND6)


def test_the_walk_reaches_every_step_and_grants_the_reward_once(built):
    screens = sorted(p.name for p in (built / "flows" / "c01" / "screens").iterdir())
    assert screens == ["before.png", "step-0.png", "step-1.png", "step-2.png", "step-3.png"]
    exhibit = (built / "exhibits" / "07-flows.md").read_text()
    assert "| c01 · A daily bonus | accept | 2 / 1 | 4 / 4 | 1 | ok |" in exhibit
    assert any(line.step == "edits:c01" and "2 applied, 1 rejected" in line.note
               for line in read_trace(built / "trace.jsonl"))


def test_reward_only_on_verification_and_only_once(built):
    ideas = CandidatesFile.model_validate_json((built / "propose" / "candidates.json").read_text()).candidates
    trigger, _, ad = (s.state_id for s in ideas[0].flow_steps[:3])
    where = "() => [window.simula.state(), window.simulaAd.grants()]"
    with render.open_mock(built / "flows" / "c01") as (page, _):
        reward = page.locator("[data-reward]")
        page.evaluate("id => window.simula.go(id)", ad)
        page.locator(".sa-cta").click()
        assert page.evaluate(where) == [ad, 0]
        page.locator(".sa-close").click()
        assert page.evaluate(where) == [trigger, 0]
        for failure in ("LOAD_FAILED", "REWARD_VERIFICATION_FAILED"):
            page.evaluate("id => window.simula.go(id)", ad)
            page.evaluate("t => window.simulaAd.emit(t)", failure)
            assert page.evaluate(where) == [trigger, 0]
        assert not reward.is_visible()
        page.evaluate("() => window.simulaAd.emit('EARNED_REWARD')")
        assert page.evaluate("() => window.simulaAd.grants()") == 0
        for _ in range(2):
            page.evaluate("() => window.simulaAd.emit('REWARD_VERIFIED')")
            assert page.evaluate("() => window.simulaAd.grants()") == 1
        assert reward.is_visible()
        assert "CLICKED" in page.evaluate("() => window.simulaAd.events()")


@pytest.mark.parametrize("app", APPS)
def test_a_step_that_wont_tap_through_shows_the_last_good_screen_marked_not_wired(tmp_path, app):
    run_dir = run_flows(tmp_path, app, wire_accept=False)
    parts = {(idea, part): text for idea, part, text in slides(run_dir)}
    assert flows.NOT_WIRED in parts[("c01", "ad")] and flows.NOT_WIRED not in parts[("c01", "offer")]
    deck = (run_dir / "flows" / "slides.html").read_text()
    ad_slide = re.search(r'data-part="ad" data-idea="c01">(.*?)</section>', deck, re.S).group(1)
    assert "c01/screens/step-1.png" in ad_slide
    assert not (run_dir / "flows" / "c01" / "screens" / "step-2.png").exists()
    broken = [line for line in read_trace(run_dir / "trace.jsonl") if line.step == "walk:c01" and line.outcome == "error"]
    assert broken and "step 3 not wired" in broken[0].note
    assert "## Not wired" in (run_dir / "exhibits" / "07-flows.md").read_text()


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("ad_section, done", [("missing", "added the"), ("unmarked", "marked the editor's")])
def test_code_owns_the_ad_screen_when_the_editor_marks_none(tmp_path, app, ad_section, done):
    run_dir = run_flows(tmp_path, app, ad_section=ad_section)
    assert "| 4 / 4 | 1 |" in (run_dir / "exhibits" / "07-flows.md").read_text()
    assert any(f"code {done} ad screen at step 3" in line.note for line in read_trace(run_dir / "trace.jsonl"))
    trigger, _, ad = (s.state_id for s in golden_idea(run_dir).flow_steps[:3])
    with render.open_mock(run_dir / "flows" / "c01") as (page, _):
        page.evaluate("id => window.simula.go(id)", ad)
        assert page.locator(f'[data-screen="{trigger}"]').is_visible()


@pytest.mark.parametrize("app", APPS)
def test_the_walk_taps_play_so_a_covered_play_button_is_not_wired(tmp_path, app):
    run_dir = run_flows(tmp_path, app, block_play=True)
    assert "| 3 / 4 | 0 |" in (run_dir / "exhibits" / "07-flows.md").read_text()
    broken = [line.note for line in read_trace(run_dir / "trace.jsonl") if line.step == "walk:c01"]
    assert any("step 4 not wired: the game's Play button can't be tapped" in note for note in broken)


def test_default_selection_is_accepted_and_conditional_best_rank_first_at_most_four():
    decisions = [decision("c01", "reject", 9.0), decision("c02", "accept", 1.0), decision("c03", "conditional", 3.0),
                 decision("c04", "needs_human", 5.0), *(decision(f"c1{n}", "accept", 0.5) for n in range(4))]
    assert [d.candidate_id for d in flows.select(decisions, None)] == ["c03", "c02", "c10", "c11"]


def test_approvals_only_narrow():
    decisions = [decision("c01", "accept", 1.0), decision("c02", "conditional", 0.5), decision("c03", "reject", 2.0)]
    assert [d.candidate_id for d in flows.select(decisions, ["c03", "c02", "zz"])] == ["c02"]
    assert flows.select(decisions, []) == []


def test_approvals_survive_the_cleanup(tmp_path):
    (tmp_path / "c01").mkdir()
    (tmp_path / "slides.html").write_text("old")
    (tmp_path / "approvals.json").write_text('{"approved": ["c01"]}')
    flows.clean(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["approvals.json"]
    assert flows.load_approvals(tmp_path) == ["c01"]


@pytest.mark.parametrize("app", APPS)
def test_art_from_a_risky_screen_is_blurred_wherever_it_is_drawn(tmp_path, app):
    """The red team's shape: a new offer over a safe trigger reuses card art from a screen rated mixed."""
    model = golden(app)
    scope = pick_scope(model)
    risky, safe = scope[0].id, scope[1].id
    states = [s.model_copy(update={"content_rating": "mixed" if s.id == risky else "safe"}) for s in model.states]
    model = model.model_copy(update={"states": states})
    (tmp_path / "assets").mkdir()
    for name, size in ((f"{risky}.e90.art.png", (400, 480)), (f"{risky}.e91.png", (84, 84)),
                       (f"{safe}.e92.art.png", (400, 480))):
        Image.new("RGB", size, "red").save(tmp_path / "assets" / name)
    images = "".join(f'<img src="assets/{n}" style="width:60px;height:60px">'
                     for n in (f"{risky}.e90.art.png", f"{risky}.e91.png", f"{safe}.e92.art.png"))
    offer = f'<section data-screen="new:offer" data-flow="c01" data-parent="{safe}">{images}</section></body>'
    page = with_runtime(skeleton_html(model), safe).replace("</body>", offer)
    (tmp_path / "index.html").write_text(flows.with_flow_css(page, flows.blur_css(model, tmp_path / "assets")))
    with render.open_mock(tmp_path) as (page, _):
        page.evaluate("() => window.simula.go('new:offer')")
        blurred = page.evaluate("() => [...document.querySelectorAll('[data-screen=\"new:offer\"] img')]"
                                ".map(i => getComputedStyle(i).filter)")
    assert blurred == ["blur(14px)", "none", "none"]
    safe_model = model.model_copy(update={"states": [s.model_copy(update={"content_rating": "safe"})
                                                     for s in model.states]})
    assert flows.blur_css(safe_model, tmp_path / "assets") == ""


def assert_contained(run_dir, reason: str):
    """c02 failed to build: c01 still gets its six slides, and the deck, the PDF, and the exhibit still get written,
    with c02 listed as not built."""
    assert {idea for idea, _, _ in slides(run_dir)} == {"c01"}
    deck = (run_dir / "flows" / "slides.html").read_text()
    not_built = re.search(r'<ul class="not-built">(.*?)</ul>', deck, re.S).group(1)
    assert "c02 · " in not_built and reason in html.unescape(not_built)
    assert (run_dir / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")
    assert not (run_dir / "flows" / "c02").exists()
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    assert "## Not built" in exhibit and f"- c02: " in exhibit and reason in exhibit
    assert "1 idea(s) passed the review but couldn't be drawn" in html.unescape(deck).replace("1 more", "1")


def test_a_hung_page_leaves_only_that_idea_not_built(tmp_path, monkeypatch):
    real = render.open_mock

    @contextmanager
    def hangs_for_c02(mock_dir):
        if Path(mock_dir).name == "c02":
            raise PlaywrightTimeout("Page.goto: Timeout 30000ms exceeded.\nCall log: waiting for fonts")
        with real(mock_dir) as opened:
            yield opened
    monkeypatch.setattr(render, "open_mock", hangs_for_c02)
    assert_contained(run_flows(tmp_path, "janitorai"), "TimeoutError: Page.goto: Timeout 30000ms exceeded.")


def test_an_edit_that_breaks_the_page_leaves_only_that_idea_not_built(tmp_path):
    assert_contained(run_flows(tmp_path, "luzia", only="c02", break_page=True), "Cannot read properties of undefined")


def test_a_one_step_flow_is_not_built_and_costs_no_editor_call(tmp_path):
    first = candidate(golden("aol")).flow_steps[:1]
    calls = []
    run_dir = run_flows(tmp_path, "aol", changes={"c02": {"flow_steps": first}}, calls=calls)
    assert_contained(run_dir, "its flow has one step")
    assert calls == ["c01"]


@pytest.mark.parametrize("app", APPS)
def test_saying_no_must_return_to_the_start_with_nothing_granted_and_the_offer_must_show_its_copy(tmp_path, app):
    run_dir = run_flows(tmp_path, app, only="c01", decline_to="new:gift", show_copy=False)
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    first = golden_idea(run_dir).flow_steps[0].state_id
    assert f"not wired: saying no led to new:gift, not back to {first}" in exhibit
    assert "| c02 · A second look | conditional | 2 / 1 | 4 / 4 | 1 | ok |" in exhibit
    offer = {(idea, part): text for idea, part, text in slides(run_dir)}[("c01", "offer")]
    assert "If they say no not wired" in " ".join(offer.split())
    assert "What the user sees not on the offer screen" in " ".join(offer.split())
    assert any(line.step == "decline:c01" and line.outcome == "error" for line in read_trace(run_dir / "trace.jsonl"))


@pytest.mark.parametrize("app", APPS)
def test_a_new_control_that_returns_to_the_first_step_goes_back(app):
    model = golden(app)
    known = model.edges[0]
    first = known.to_state
    page = (f'<a data-edge="new:offer>{first}" data-transition="replace">No</a><b data-edge="new:offer>new:ad">Yes</b>'
            f'<i data-edge="{known.id}" data-transition="{known.transition}">x</i>')
    fixed, relabeled = flows.decline_edges(page, first, {e.id for e in model.edges})
    assert relabeled == 1
    assert fixed == page.replace('data-transition="replace"', 'data-transition="back"')


def test_the_copy_check_reads_words_in_any_script():
    assert flows.words("「広告を見て」30分 無料!") == "広告を見て 30分 無料"
    assert flows.words("Échale un vistazo, ¡gratis!") == "échale un vistazo gratis"
    assert flows.words("!!!") == ""


def test_code_dropped_ideas_show_their_reason_on_the_card(built):
    deck = (built / "flows" / "slides.html").read_text()
    card = deck[deck.index("c04 · Product change: A dropped idea"):]
    card = html.unescape(re.sub(r"<[^>]+>", "", card[:card.index("</div>")]))
    assert "Dropped by code: duplicate of c01: same benefit" in card
    assert "<h3>Dropped by code</h3>" in deck and "before the review" not in deck
