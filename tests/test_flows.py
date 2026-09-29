"""Stage 7 offline, on every golden: selection, the tap-through, the reward rule, and the deck's parts."""

import html
import json
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from simula import llm, render
from simula.contracts import (GATES, JUDGMENT, CandidatesFile, Check, Decision, DecisionsFile, Economics, Edit, Edits,
                              FlowStep, Verdict)
from simula.runlog import read_trace
from simula.stages import flows
from simula.stages.mock import copy_assets, pick_scope, with_runtime
from simula.stages.propose import anchor_ids, depths, root_id, with_bucket
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, skeleton_html
from tests.propose_fixtures import anchored, candidate
from tests.test_mock_isolation import ctx_for

COST_LINE = "Costs nothing extra to serve, so any completed view pays for it above $0.00 eCPM."
ROUND6 = FIXTURES / "flows" / "luzia-round6"
FLAG = 'uses "Zap", whose meaning was never observed'


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
               decline_to: str | None = None, show_copy: bool = True, break_page: bool = False,
               show_reward: bool = True, hide_note: bool = False) -> Edits:
    """What a good editor returns for any idea: an entry point on the trigger, the offer, an empty ad screen, and a
    badge that shows the reward. The options plant one defect each: ad_section "unmarked" draws the ad screen without
    data-ad and "missing" leaves it out; block_play covers the game's Play button; decline_to sends "No thanks" to a new
    screen instead of back; show_copy=False drops the offer copy; break_page leaves an HTML comment open, which kills
    the page's scripts; show_reward=False draws no badge; hide_note hides the note a failed ad shows."""
    trigger, offer, ad = (s.state_id for s in c.flow_steps[:3])
    tag = re.search(rf'<section[^>]*data-screen="{trigger}"[^>]*>', page).group(0)
    button = 'style="position:absolute;left:20px;top:{}px;z-index:5"'
    entry = (f'<button data-edge="{trigger}>{offer}" data-transition="modal" {button.format(20)}>Get it</button>'
             + (f'<div data-reward {button.format(70)}>Badge on</div>' if show_reward else ""))
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
    if hide_note:
        edits.append(Edit(find="</head>", replace="<style>.sa-note{display:none}</style></head>", reason="no note"))
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


def text_of(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def drawn(c, d) -> dict:
    """A flow as build_flow returns it, every step wired and the ad at step 3, for building slides without a walk."""
    shots = [{"state_id": s.state_id, "caption": s.caption, "png": f"step-{i}.png", "wired": True, "tap": None,
              "reward_shown": None, "reward_labels": None} for i, s in enumerate(c.flow_steps)]
    return {"candidate": c, "decision": d, "before": "before.png", "shots": shots, "ad_at": 2, "copy_shown": True,
            "decline": "", "ad_fail": ""}


def slides(run_dir) -> list[tuple[str, str, str]]:
    """(idea, part, visible text) for every main slide."""
    deck = (run_dir / "flows" / "slides.html").read_text()
    found = re.findall(r'<section class="slide main" data-part="(\w+)" data-idea="(\w+)">(.*?)</section>', deck, re.S)
    return [(idea, part, html.unescape(re.sub(r"<[^>]+>", " ", body))) for part, idea, body in found]


def flow_phones(run_dir, idea: str) -> list[dict]:
    """The phones on an idea's flow slide, in order: label, red flags, caption, and screenshot."""
    deck = (run_dir / "flows" / "slides.html").read_text()
    slide = re.search(rf'data-part="flow" data-idea="{idea}">(.*?)</section>', deck, re.S).group(1)
    return [{"label": re.search(r"<b>(.*?)</b>", step).group(1),
             "flags": re.findall(r'<span class="flag">(.*?)</span>', step),
             "text": html.unescape(re.search(r"<p>(.*?)</p>", step, re.S).group(1)),
             "img": (re.search(r'<img src="([^"]+)"', step) or [None, None])[1]}
            for step in slide.split('<div class="step">')[1:]]


def scores_text(run_dir) -> str:
    deck = (run_dir / "flows" / "slides.html").read_text()
    return text_of(deck[deck.index('<section class="slide scores">'):])


def test_each_idea_gets_a_flow_slide_and_a_why_slide_and_every_idea_is_scored_on_the_last_page(built):
    parts = {}
    for idea, part, _ in slides(built):
        parts.setdefault(idea, []).append(part)
    assert parts == {"c01": list(flows.PARTS), "c02": list(flows.PARTS)}
    deck = (built / "flows" / "slides.html").read_text()
    scores = scores_text(built)
    for cid, verdict, checks in (("c01", "accepted", "11/11"), ("c02", "conditional", "10/11"),
                                 ("c03", "rejected", "9/11"), ("c04", "rejected", "11/11")):
        assert f"{cid} " in scores and f"{verdict} {checks}" in scores
    assert "didn't pass the right moment" in scores and "didn't pass a brand-safe place for the offer" in scores
    assert "exhibits/06-judge.md" in scores and "appendix" not in deck
    assert deck.count('<section class="slide scores">') == 1
    assert "FIXTURE TEST DATA" in deck
    assert (built / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")
    exhibit = (built / "exhibits" / "07-flows.md").read_text()
    assert "## Not wired" not in exhibit and "## Layout" not in exhibit


def test_the_flow_slide_walks_from_today_to_what_they_get_and_says_each_thing_once(built):
    c = golden_idea(built)
    phones = flow_phones(built, "c01")
    assert [p["label"] for p in phones] == ["Today", "When it appears", "The offer", "The ad plays", "What they get"]
    assert [p["img"] for p in phones] == ["c01/screens/before.png", *(f"c01/screens/step-{i}.png" for i in range(4))]
    assert phones[1]["text"] == flows.plain(c.trigger_event) and phones[-1]["text"] == "A label appears: “Badge on”."
    assert not any(p["flags"] for p in phones)
    flow = {(idea, part): text for idea, part, text in slides(built)}[("c01", "flow")]
    for words in (flows.plain(flows.caption(c)), f"The offer says: “{c.offer_copy}” Saying no changes nothing.",
                  f"They get: {flows.reward_line(c)}", f"How often: {c.frequency_cap}"):
        assert words in " ".join(flow.split()), words
    why = " ".join({(idea, part): text for idea, part, text in slides(built)}[("c01", "why")].split())
    reward = flows.reward_line(c)
    assert f"What the user gets {reward[:1].upper()}{reward[1:]}." in why and "Badge shows" not in why
    deck = text_of((built / "flows" / "slides.html").read_text())
    assert deck.count("When the reward runs out") == 2


def test_the_cover_names_the_app_as_the_model_reads_it_else_the_config_key_title_cased(built):
    deck = (built / "flows" / "slides.html").read_text()
    app = built.parent.name
    assert f"<h1>Rewarded-ad ideas for {app.title()}</h1>" in deck and f"<title>Rewarded-ad ideas for {app.title()}" in deck
    named = golden(app).model_copy(update={"app_name": "Janitor AI"})
    assert flows.app_title(named, app) == "Janitor AI" and flows.app_title(golden(app), app) == app.title()


def test_the_cover_carries_a_line_for_each_earlier_stage_that_finished_only_part_of_its_work(tmp_path):
    run_dir = seed_run(tmp_path, "luzia")
    (run_dir / "qa").mkdir()
    (run_dir / "qa" / "done.json").write_text(json.dumps({"stage": "qa", "outcome": {
        "status": "partial", "reasons": ["round 1 stopped on the $ cap"], "resume": "simula run luzia --from qa"}}))
    (run_dir / "judge" / "done.json").write_text(json.dumps({"stage": "judge", "outcome": {"status": "complete"}}))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.run(ctx_for(run_dir, "luzia"))
    cover = text_of((run_dir / "flows" / "slides.html").read_text().split('<section class="slide main"')[0])
    assert cover.endswith("The qa step finished only part of its work: round 1 stopped on the $ cap.")
    assert "judge step" not in cover


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
        flow = {"candidate": c, "decision": d, "shots": [{"caption": s.caption} for s in c.flow_steps], "ad_at": 2,
                "decline": ""}
        text = html.unescape(re.sub(r"<[^>]+>", " ", flows.why_html(flow, golden("luzia"), ROUND6, False)))
        assert not re.search(r"\$|eCPM|\b(?:g|c\d)_[a-z]", text), text
        if d.checks_passed == d.checks_total:
            assert "didn't pass" not in text and "not every" not in text
        if d.final == "conditional" and d.checks_passed == d.checks_total:
            assert "closest idea" not in text and "Recommended" not in text
        if c.economics.verdict == "FAIL":
            assert "it may cost more to serve than a view earns" in text


@pytest.mark.parametrize("app", APPS)
def test_the_reach_line_says_where_the_offer_sits_and_when_it_appears_but_never_how_many_see_it(app):
    model = golden(app)
    depth = depths(model)
    screens = [s.id for s in model.states if s.kind == "screen"]
    places = {root_id(model): "sits on the first screen people see",
              next(s for s in screens if depth[s] == 1): "sits on a screen one tap in"}
    if tab := next((s for s in screens if depth[s] == 0 and s != root_id(model)), None):
        places[tab] = "sits on a main tab"
    for sid, where in places.items():
        text = flows.reach_text(candidate(model, trigger_state_id=sid, trigger_event="After 3 days away."), model)
        assert text == (f"Reach scenario, not a measurement: the offer {where}, and appears only when this happens: "
                        "After 3 days away.")
    deep = candidate(model, trigger_state_id="s99", trigger_event="The user saves a story")
    assert "sits on a screen a few taps in" in flows.reach_text(deep, model)
    assert "every visit" not in flows.reach_text(deep, model)


@pytest.mark.parametrize("app", APPS)
def test_a_pop_up_is_placed_by_the_screen_it_covers_not_called_a_main_tab(app):
    model = golden(app)
    popups = [s for s in model.states if s.kind in ("modal", "sheet") and s.parent_id]
    if not popups:
        pytest.skip(f"{app}'s golden has no modal or sheet")
    for s in popups:
        covered = flows.reach_text(candidate(model, trigger_state_id=s.parent_id), model).split("sits on ")[1]
        text = flows.reach_text(candidate(model, trigger_state_id=s.id), model)
        assert text.split("the offer ")[1] == f"sits in a pop-up over {covered}", (s.id, text)
    if app == "janitorai":
        assert "sits in a pop-up over the first screen people see" in flows.reach_text(
            candidate(model, trigger_state_id="s02"), model)


def judged(tmp_path, cid: str, fail: str | None) -> Decision:
    """A CONDITIONAL decision whose one verdict file fails `fail` (or nothing)."""
    (tmp_path / "judge" / "verdicts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "judge" / "verdicts" / f"{cid}_judge_1_r1.json").write_text(verdict(cid, fail).model_dump_json())
    return decision(cid, "conditional", 1.0, passed=10 if fail else 11)


def test_the_judges_fallback_pick_reads_as_the_closest_idea_not_a_recommendation(tmp_path):
    missed = judged(tmp_path, "c01", "c5_moment")
    assert flows.condition(missed, tmp_path, none_accepted=True) == (
        "The closest idea, not a recommendation:",
        ("No idea passed every check. This one passes every safety check but not the right moment "
         "(c5_moment reason for c01)."))
    assert flows.condition(missed, tmp_path, none_accepted=False) == (
        "Not every check passed:", "It didn't pass the right moment (c5_moment reason for c01).")
    assert flows.condition(judged(tmp_path, "c02", "g_policy"), tmp_path, none_accepted=True)[0] == \
        "Not every check passed:"
    assert flows.condition(decision("c03", "conditional", 1.0, passed=10), ROUND6.parent / "nowhere", True) == (
        "Not every check passed:", "It passed 10 of 11 checks; the score pages at the end show which.")
    assert flows.condition(judged(tmp_path, "c04", None), tmp_path, True) is None
    assert flows.condition(decision("c05", "accept", 1.0), tmp_path, True) is None


def test_a_survivor_past_the_cap_is_named_in_the_trace_and_counted_on_the_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(flows, "MAX_IDEAS", 1)
    run_dir = run_flows(tmp_path, "luzia")
    note = next(line.note for line in read_trace(run_dir / "trace.jsonl") if line.step == "select")
    assert note == "accepted + conditional: c01; past the cap of 1, not drawn: c02"
    cover = text_of((run_dir / "flows" / "slides.html").read_text().split('<section class="slide main"')[0])
    assert ("1 more idea(s) passed the review; the deck draws only the top 1 by rank, and the score pages at the end "
            "score the rest.") in cover
    assert {idea for idea, _, _ in slides(run_dir)} == {"c01"}


def test_a_fallback_pick_is_named_on_the_cover_and_its_why_slide_never_says_recommended(tmp_path):
    run_dir = seed_run(tmp_path, "luzia", {"c01": {}})
    decisions = [decision("c01", "conditional", 1.0, passed=10), decision("c03", "reject", 2.0, passed=9)]
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=decisions).model_dump_json())
    (run_dir / "judge" / "verdicts" / "c01_judge_1_r1.json").write_text(verdict("c01", "c5_moment").model_dump_json())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.run(ctx_for(run_dir, "luzia"))
    deck = (run_dir / "flows" / "slides.html").read_text()
    cover = text_of(deck.split('<section class="slide main"')[0])
    assert "No idea passed every check, so the closest is drawn and marked as not a recommendation." in cover
    why = {(idea, part): " ".join(text.split()) for idea, part, text in slides(run_dir)}[("c01", "why")]
    assert "The closest idea, not a recommendation: No idea passed every check." in why and "Recommended" not in why


def test_a_cost_line_that_isnt_pass_is_a_mark_on_every_slide_and_never_the_verdict():
    """Real round-6 output, as recorded (the old judge made three 11/11 ideas CONDITIONAL for cost) and as the judge
    now decides them in annotate mode (accepted, cost carried as a mark)."""
    decisions = DecisionsFile.model_validate_json((ROUND6 / "judge" / "decisions.json").read_text()).decisions
    ideas, model = flows.load_candidates(ROUND6), golden("luzia")
    chosen = flows.select(decisions, None)
    assert sorted(ideas[d.candidate_id].economics.verdict for d in chosen) == ["CONDITIONAL", "CONDITIONAL", "FAIL",
                                                                               "PASS"]
    for d in chosen:
        c, marked = ideas[d.candidate_id], ideas[d.candidate_id].economics.verdict != "PASS"
        for final in ("conditional", "accept"):
            texts = [text_of(s) for s in flows.idea_slides(drawn(c, d.model_copy(update={"final": final})), model,
                                                            ROUND6, False)]
            for text in texts:
                assert (f"Cost check: {c.economics.verdict}" in text) == marked
                assert (" Conditional " in text) == (final == "conditional")
                assert not re.search(r"\$|eCPM", text), text
            if marked:
                assert texts[-1].count(flows.cost_question(c)) == 1
            if marked and final == "accept":
                assert f"Cost check ({c.economics.verdict}): {flows.cost_question(c)}." in texts[-1]
                assert "Recommended with one condition" not in texts[-1]


def rendered_overflows(slides_html: str) -> list[str]:
    deck = flows.deck_html("t", slides_html)
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": flows.SLIDE_W, "height": flows.SLIDE_H})
        page.set_content(deck)
        return flows.overflows(page)


def why_slide(words: int) -> str:
    return ('<section class="slide main" data-part="why" data-idea="c01"><div class="why">'
            f'<div class="why-block"><b>What the app gets</b><p>{"word " * words}</p></div></div>'
            '<footer>When the reward runs out: it ends.</footer></section>')


@pytest.mark.parametrize("words, fits", [(150, True), (400, False)])
def test_a_why_slide_steps_its_text_down_to_fit_and_the_check_reports_what_still_doesnt(words, fits):
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": flows.SLIDE_W, "height": flows.SLIDE_H})
        page.set_content(flows.deck_html("t", why_slide(words)))
        problems = flows.overflows(page)
        size = page.evaluate("document.querySelector('.why').style.fontSize")
    assert size in ("15px", "14px", "13px") and (problems == []) == fits, (size, problems)
    if not fits:
        assert size == "13px" and problems[0].startswith("slide 1 (c01 why)")


def test_the_deck_brings_its_own_font_so_it_lays_out_the_same_on_every_machine():
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": flows.SLIDE_W, "height": flows.SLIDE_H})
        page.set_content(flows.deck_html("t", why_slide(10)))
        flows.overflows(page)
        loaded = page.evaluate("() => Promise.all([...document.fonts].map(f => f.load()))"
                               ".then(faces => faces.filter(f => f.family === 'Inter').map(f => Number(f.weight)))")
    assert sorted(loaded) == list(flows.FONT_WEIGHTS)
    assert (flows.FONTS / "OFL.txt").exists()


def test_the_overflow_check_names_each_slide_whose_text_runs_off_it_or_into_its_footer():
    long = "word " * 400
    found = rendered_overflows(
        '<section class="slide main" data-part="flow" data-idea="c01"><div style="position:absolute;left:1200px;'
        'width:200px">wide</div></section>'
        '<section class="slide main" data-part="why" data-idea="c02"><p style="position:absolute;top:640px;margin:0">'
        "two lines<br>of text</p><footer>the footer</footer></section>"
        f'<section class="slide cover"><div style="height:40px;overflow:hidden">{long}</div></section>'
        f'<section class="slide main" data-part="why" data-idea="c03"><header><span class="idea">{long}</span>'
        "</header><p>fits</p><footer>the footer</footer></section>")
    assert len(found) == 3, found
    assert found[0].startswith('slide 1 (c01 flow): div "wide" runs 120px past the slide')
    assert found[1].startswith('slide 2 (c02 why): p "two linesof text" runs') and found[1].endswith("into the footer")
    assert found[2].startswith('slide 3: div "word') and found[2].endswith("is cut off")


def test_every_slide_fits_on_real_output(tmp_path):
    """Round 6's long rationales, offer copy, and triggers, as accepted ideas and as the judge's fallback pick, whose
    why slide carries both the missed-check box (with round 6's longest judge reason) and the cost box."""
    decisions = DecisionsFile.model_validate_json((ROUND6 / "judge" / "decisions.json").read_text()).decisions
    ideas, model = flows.load_candidates(ROUND6), golden("luzia")
    fallback_run = tmp_path / "run"
    shutil.copytree(ROUND6, fallback_run)
    reasons = [getattr(v, k).reason for _, v in flows.verdicts(decisions[0], ROUND6) for k in GATES + JUDGMENT]
    for path in (fallback_run / "judge" / "verdicts").iterdir():
        v = Verdict.model_validate_json(path.read_text())
        path.write_text(v.model_copy(update={"c5_moment": Check(passed=False, reason=max(reasons, key=len))})
                        .model_dump_json())
    chosen = flows.select(decisions, None)
    deck = [slide for d in chosen for slide in flows.idea_slides(drawn(ideas[d.candidate_id], d), model, ROUND6, False)]
    deck += [slide for d in chosen for slide in flows.idea_slides(
        drawn(ideas[d.candidate_id], d.model_copy(update={"final": "conditional"})), model, fallback_run, True)]
    deck += flows.score_slides(decisions, ideas, [], ROUND6)
    assert sum("The closest idea, not a recommendation" in s for s in deck) == 4
    assert sum("Cost check (" in s for s in deck) == 6
    assert rendered_overflows("".join(deck)) == []


@pytest.mark.parametrize("app", APPS)
def test_a_slide_that_overflows_is_reported_in_the_exhibit_and_the_trace(tmp_path, app):
    run_dir = run_flows(tmp_path, app, changes={"c01": {"trigger_event": "The user waits. " * 120}})
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    assert "## Layout" in exhibit and '- slide 2 (c01 flow): .cap "When it appears' in exhibit
    assert any(line.step == "layout" and line.outcome == "error" and line.note.startswith("slide 2 (c01 flow)")
               for line in read_trace(run_dir / "trace.jsonl"))


@pytest.mark.parametrize("app", APPS)
def test_code_flags_show_on_the_score_page_and_reach_no_slide_or_model(tmp_path, app):
    run_dir = seed_run(tmp_path, app, {"c01": {"flags": [FLAG]}})
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    ideas, model = flows.load_candidates(run_dir), golden(app)
    scores = text_of("".join(flows.score_slides(decisions, ideas, [], run_dir)))
    assert scores.count("flagged by code") == 1
    assert f"flagged by code: {FLAG}" in scores[scores.index("c01 "):scores.index("c02 ")]
    main = text_of("".join(flows.idea_slides(drawn(ideas["c01"], decisions[0]), model, run_dir, False)))
    page = flows.strip_runtime((run_dir / "mock" / "index.html").read_text())
    editor = flows.editor_brief(ideas["c01"], model, page) + flows.system_prompt()
    assert "Flagged" not in main and FLAG not in main and FLAG not in editor


def test_the_walk_reaches_every_step_and_grants_the_reward_once(built):
    screens = sorted(p.name for p in (built / "flows" / "c01" / "screens").iterdir())
    assert screens == ["before.png", "step-0.png", "step-1.png", "step-2.png", "step-3.png"]
    exhibit = (built / "exhibits" / "07-flows.md").read_text()
    assert "| c01 · A daily bonus | accept | 2 / 1 | 4 / 4 | 1 | ok | ok | only a label: step 4 |" in exhibit
    assert any(line.step == "edits:c01" and "2 applied, 1 rejected" in line.note
               for line in read_trace(built / "trace.jsonl"))


@pytest.mark.parametrize("app", APPS)
def test_a_reward_that_changes_nothing_on_screen_is_marked_not_shown(tmp_path, app):
    run_dir = run_flows(tmp_path, app, only="c01", show_reward=False)
    value = flow_phones(run_dir, "c01")[-1]
    assert value["flags"] == [flows.REWARD_NOT_SHOWN] and value["text"] == "Badge shows"
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    root = golden_idea(run_dir).flow_steps[-1].state_id
    assert f"| {flows.REWARD_NOT_SHOWN}: step 4 |" in exhibit
    assert f"## Reward not shown\n\n- c01 step 4 (`{root}`): nothing on it changes when the reward is granted" in exhibit
    assert "| c02 · A second look | conditional | 2 / 1 | 4 / 4 | 1 | ok | ok | only a label: step 4 |" in exhibit
    assert any(line.step == "walk:c01" and line.outcome == "error" and flows.REWARD_NOT_SHOWN in line.note
               for line in read_trace(run_dir / "trace.jsonl"))


@pytest.mark.parametrize("app", APPS)
def test_a_failed_ad_must_bring_the_user_back_with_nothing_granted_and_say_so(tmp_path, app):
    run_dir = run_flows(tmp_path, app, only="c01", hide_note=True)
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    assert f"| ok | {flows.NOT_WIRED}: a failed ad shows no note that nothing was used |" in exhibit
    assert "| c02 · A second look | conditional | 2 / 1 | 4 / 4 | 1 | ok | ok |" in exhibit
    assert flow_phones(run_dir, "c01")[3]["flags"] == [f"a failed ad: {flows.NOT_WIRED}"]
    assert any(line.step == "failed-ad:c01" and line.outcome == "error" for line in read_trace(run_dir / "trace.jsonl"))


REWARD_PAGE = """<style>body{margin:0;background:#fff}p{position:absolute;margin:0;font:16px sans-serif}
{flow_css}{extra}</style>
<p style="left:20px;top:20px">Home</p><p data-reward style="left:20px;top:200px">{label}</p>
<p class="card" style="left:20px;top:400px">A card</p><p data-unrewarded style="left:20px;top:600px">{before}</p>"""


@pytest.mark.parametrize("extra, label, before, expected", [
    ("", "Badge on", "", (True, ["Badge on"])),
    ("body.simula-rewarded .card{top:300px!important}", "Badge on", "", (True, None)),
    ("body.simula-rewarded .card{color:rgb(3,3,3)}", "", "", (False, None)),
    ("", "Level 2 open", "Level 2 locked", (True, None)),
])
def test_the_reward_effect_names_labels_and_ignores_render_noise(tmp_path, extra, label, before, expected):
    """Only a label appears; a label appears and a card moves; nothing but a 3-level shade (render noise) changes;
    the unlocked form appears and the locked one the page marked data-unrewarded goes."""
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport=render.VIEWPORT)
        page.set_content(REWARD_PAGE.replace("{flow_css}", flows.FLOW_CSS).replace("{extra}", extra)
                         .replace("{label}", label).replace("{before}", before))
        page.evaluate(flows.REWARDED_JS, True)
        render.screenshot(page, path=tmp_path / "on.png", animations="disabled")
        assert flows.reward_effect(page, tmp_path / "on.png") == expected


def shot(wired: bool = True, shown: bool | None = None, labels: list[str] | None = None) -> dict:
    return {"wired": wired, "reward_shown": shown, "reward_labels": labels}


@pytest.mark.parametrize("after_ad, expected", [
    ([shot(wired=False)], "not reached"),
    ([shot()], "new screen, not measured"),
    ([shot(), shot(shown=False)], f"{flows.REWARD_NOT_SHOWN}: step 5"),
    ([shot(shown=True, labels=["Badge on"])], "only a label: step 4"),
    ([shot(shown=True, labels=["Badge on"]), shot(shown=True)], "yes"),
])
def test_the_exhibit_says_yes_only_when_the_reward_check_ran_and_found_more_than_a_label(after_ad, expected):
    before = [shot()] * 3
    assert flows.reward_text({"shots": before + after_ad, "ad_at": 2}) == expected


def test_labels_are_quoted_as_what_appears():
    assert flows.labels_text(["Badge on"]) == "A label appears: “Badge on”."
    assert flows.labels_text(["A", "B"]) == "Labels appear: “A”, “B”."
    assert flows.labels_text([]) == "A label appears."


def test_the_ad_card_takes_the_apps_palette_and_its_most_colorful_color_as_the_accent():
    root = ":root{--bg-1:#303337;--bg-2:#000000;--fg-1:#ffffff;--fg-2:#af89f0;--fg-3:#5a5c63;--font-1:Roboto,sans-serif}"
    assert flows.ad_palette_css(root) == ".sa-card{--sa-accent:#af89f0;--sa-on-accent:#000}\n"
    assert flows.ad_palette_css(":root{--bg-1:#ffffff;--bg-3:#4264fc}") == ".sa-card{--sa-accent:#4264fc;--sa-on-accent:#fff}\n"
    assert flows.ad_palette_css(":root{--bg-1:#303337;--fg-1:#ffffff}") == ""
    assert flows.ad_palette_css("<p>a mock without a palette</p>") == ""
    assert "--sa-accent:#e11d48;" in flows.ad_palette_css(":root{--bg-1:#fffdf7;--bg-2:#e11d48}")
    assert "--sa-accent:#20808d;" in flows.ad_palette_css(":root{--bg-1:#000814;--fg-1:#20808d}")
    c = candidate(golden("luzia"))
    page = (f"<style>{root}{flows.FLOW_CSS}{flows.ad_palette_css(root)}</style>"
            f'<section data-screen="ad">{flows.ad_card(c)}</section>')
    with sync_playwright() as p:
        tab = p.chromium.launch().new_page()
        tab.set_content(page)
        styles = tab.evaluate("() => ['.sa-card', '.sa-play'].map(q => getComputedStyle(document.querySelector(q)))"
                              ".map(s => [s.backgroundColor, s.color])")
    assert styles == [["rgb(48, 51, 55)", "rgb(255, 255, 255)"], ["rgb(175, 137, 240)", "rgb(0, 0, 0)"]]


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
    offer, ad = flow_phones(run_dir, "c01")[2:4]
    assert flows.NOT_WIRED in ad["flags"] and not offer["flags"]
    assert ad["img"] == "c01/screens/step-1.png"
    assert "| 2 / 4 | 0 | ok | " in (run_dir / "exhibits" / "07-flows.md").read_text()
    assert "| not reached | `flows/c01/index.html` |" in (run_dir / "exhibits" / "07-flows.md").read_text()
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
    """c02 failed to build: c01 still gets its slides, and the deck, the PDF, and the exhibit still get written, with
    c02 listed as not built."""
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
    assert flow_phones(run_dir, "c01")[2]["flags"] == ["copy not on the screen", f"saying no: {flows.NOT_WIRED}"]
    texts = {(idea, part): " ".join(text.split()) for idea, part, text in slides(run_dir)}
    assert "Saying no doesn't bring them back yet." in texts[("c01", "flow")]
    assert "Nothing free is taken away, and saying no doesn't bring them back yet." in texts[("c01", "why")]
    assert "saying no changes nothing" not in texts[("c01", "why")].lower()
    assert "Nothing free is taken away, and saying no changes nothing." in texts[("c02", "why")]
    assert any(line.step == "decline:c01" and line.outcome == "error" for line in read_trace(run_dir / "trace.jsonl"))


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("starts_on_offer", [True, False])
def test_saying_no_taps_the_offers_own_way_back_never_the_apps_close(tmp_path, app, starts_on_offer):
    """The offer is drawn on a screen that has its own close, and its No thanks returns to that screen. Starting
    there, saying no works. Starting on the screen the app's close returns to, No thanks strays, and the walk must
    say so instead of tapping the app's close."""
    model = golden(app)
    scope = {s.id for s in pick_scope(model)}
    own = next((e for e in model.edges if e.transition == "back" and e.from_state != e.to_state
                and {e.from_state, e.to_state} <= scope), None)
    if own is None:
        pytest.skip(f"no screen in {app}'s golden scope has its own back control")
    screen, first = own.from_state, own.from_state if starts_on_offer else own.to_state
    steps = [FlowStep(state_id=state, caption=caption) for state, caption in
             [(first, "The screen is open"), (screen, "The offer shows"), ("new:ad", "They play"),
              (first, "The bonus shows")]]
    run_dir = seed_run(tmp_path, app, {"c01": {"trigger_state_id": first, "flow_steps": steps}})
    c = golden_idea(run_dir)
    style = 'style="position:absolute;left:20px;top:{}px;z-index:20"'
    added = {screen: (f'<p {style.format(500)}>{c.offer_copy}</p><div data-reward {style.format(40)}>Bonus on</div>'
                      f'<button data-edge="{screen}>new:ad" data-transition="modal" {style.format(560)}>Play</button>'
                      f'<button data-edge="{screen}>{screen}" data-transition="back" {style.format(620)}>No thanks'
                      '</button>')}
    if not starts_on_offer:
        added[first] = f'<button data-edge="{first}>{screen}" data-transition="modal" {style.format(700)}>Open</button>'

    def editor(**kwargs):
        page = flows.strip_runtime((run_dir / "mock" / "index.html").read_text())
        edits = []
        for sid, markup in added.items():
            close = page.index("</section>", page.index(re.search(rf'<section[^>]*data-screen="{sid}"', page).group(0)))
            edits.append(Edit(find=page[close - 40:close + 10], replace=page[close - 40:close] + markup
                              + page[close:close + 10], reason="added after the screen's own controls"))
        ad = f'<section data-screen="new:ad" data-flow="c01" data-parent="{screen}" data-ad></section>'
        return Edits(edits=[*edits, Edit(find="</body>", replace=ad + "</body>", reason="ad")]), None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", editor)
        flows.run(ctx_for(run_dir, app))
    row = next(line for line in (run_dir / "exhibits" / "07-flows.md").read_text().splitlines()
               if line.startswith("| c01"))
    saying_no = row.split("|")[6].strip()
    assert saying_no == ("ok" if starts_on_offer else
                         f"{flows.NOT_WIRED}: saying no led to {screen}, not back to {first}"), row


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


def test_an_idea_whose_id_is_not_a_plain_name_is_never_built():
    model = golden("luzia")
    assert "isn't a plain name" in flows.unbuildable(candidate(model, id="../model"))
    assert flows.unbuildable(candidate(model, id="c01-rev")) is None


def test_the_copy_check_reads_words_in_any_script():
    assert flows.words("「広告を見て」30分 無料!") == "広告を見て 30分 無料"
    assert flows.words("Échale un vistazo, ¡gratis!") == "échale un vistazo gratis"
    assert flows.words("!!!") == ""


def test_code_dropped_ideas_show_their_reason_on_the_score_page(built):
    scores = scores_text(built)
    assert "c04 A dropped idea rejected 11/11 dropped by code: duplicate of c01: same benefit" in scores
    assert "before the review" not in scores


def test_the_editor_asks_for_its_roles_max_tokens_from_the_profile(tmp_path, monkeypatch):
    app = APPS[0]
    run_dir = seed_run(tmp_path, app)
    ctx = ctx_for(run_dir, app)
    roles = flows.config.roles(ctx.profile)
    monkeypatch.setattr(flows.config, "roles", lambda profile: {
        **roles, "flows_editor": {**roles["flows_editor"], "max_tokens": 1234}})
    asked = []
    monkeypatch.setattr(llm, "call", lambda **kwargs: (asked.append(kwargs["max_tokens"]), (Edits(edits=[]), None))[1])
    page = flows.strip_runtime((run_dir / "mock" / "index.html").read_text())
    flows.ask_editor(ctx, golden_idea(run_dir), golden(app), page, llm.Budget("flows", 1.0))
    assert asked == [1234], "one number: the profile's, the same one doctor --keys probes"
