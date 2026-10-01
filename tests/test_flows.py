"""Stage 7 offline, on every golden: selection, the tap-through, the reward rule, and the deck's parts."""

import dataclasses
import html
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from simula import llm, render, runfolder, validate
from simula.contracts import (GATES, JUDGMENT, CandidatesFile, Check, Decision, DecisionsFile, Economics, Edit, Edits,
                              FlowStep, ProductModel, Provenance, SavedVerdict, StageOutcome, Verdict)
from simula.runlog import read_trace
from simula.stages import flows, judge
from simula.stages.mock import copy_assets, pick_scope, with_runtime
from simula.stages.propose import anchor_ids, depths, root_id, with_bucket
from tests.conftest import APPS, FIXTURES, ROOT
from tests.mock_fake import golden, skeleton_html
from tests.propose_fixtures import anchored, candidate
from tests.test_mock_isolation import ctx_for

COST_LINE = "Costs nothing extra to serve, so any completed view pays for it above $0.00 eCPM."
ROUND6 = FIXTURES / "flows" / "luzia-round6"
FLAG = 'uses "Zap", whose meaning was never observed'
FIXTURE = Provenance(source="fixture", fixture_path="tests/fixtures")


def decision(cid: str, final: str, rank: float, passed: int = 11) -> Decision:
    return Decision(candidate_id=cid, final=final, checks_passed=passed, checks_total=11, rank_score=rank,
                    gate_fails=[], judgment_splits=[], verdict_paths=[f"judge/verdicts/{cid}_judge_1_r1.json"],
                    economics_verdict=None, revision_of=None, failure_type=None, rerun_stage=None)


def fallback(cid: str, rank: float) -> Decision:
    """The judge's fallback pick: a reject made CONDITIONAL, which keeps its failure_type as the judge's mark."""
    return decision(cid, "conditional", rank, passed=10).model_copy(update={"failure_type": "proposal"})


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
    """What a good editor returns for any idea: an entry point on the trigger, the offer, an empty ad screen, a plain
    screen for each new step after the ad, and a badge that shows the reward. The options plant one defect each:
    ad_section "unmarked" draws the ad screen without data-ad and "missing" leaves it out; block_play covers the game's
    Play button; decline_to sends "No thanks" to a new screen instead of back; show_copy=False drops the offer copy;
    break_page leaves an HTML comment open, which kills the page's scripts; show_reward=False draws no badge; hide_note
    hides the note a failed ad shows."""
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
    screens += "".join(f'<section data-screen="{s.state_id}" data-flow="{c.id}"><p>Back to the app</p></section>'
                       for s in c.flow_steps[3:] if s.state_id.startswith("new:"))
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
        page = flows.page.strip_runtime((run_dir / "mock" / "index.html").read_text())
        return fake_edits(ideas[cid], page, **(options if only in (None, cid) else {})), None
    return call


def run_flows(root, app: str, changes: dict | None = None, calls: list | None = None, **options):
    run_dir = seed_run(root, app, changes)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir, calls, **options))
        flows.stage.run(ctx_for(run_dir, app))
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
    """A flow as build_flow returns it, every step wired, the ad at step 3, and the reward shown on each existing
    screen after it, for building slides without a walk."""
    shots = [{"state_id": s.state_id, "caption": s.caption, "png": f"step-{i}.png", "wired": True, "tap": None,
              "reward_shown": True if i > 2 and not s.state_id.startswith("new:") else None, "reward_labels": None}
             for i, s in enumerate(c.flow_steps)]
    return {"candidate": c, "decision": d, "before": "before.png", "shots": shots, "ad_at": 2, "copy_shown": True,
            "decline": "", "ad_fail": ""}


def slides(run_dir) -> list[tuple[str, str, str]]:
    """(idea, part, visible text) for every main slide."""
    deck = (run_dir / "flows" / "slides.html").read_text()
    found = re.findall(r'<section class="slide main" data-part="(\w+)" data-idea="(\w+)">(.*?)</section>', deck, re.S)
    return [(idea, part, html.unescape(re.sub(r"<[^>]+>", " ", body))) for part, idea, body in found]


def flow_phones(run_dir, idea: str) -> list[dict]:
    """The phones on an idea's flow slide, in order: label, caption, screenshot, and whether it is dimmed."""
    deck = (run_dir / "flows" / "slides.html").read_text()
    slide = re.search(rf'data-part="flow" data-idea="{idea}">(.*?)</section>', deck, re.S).group(1)
    return [{"label": re.search(r"<b>(.*?)</b>", step).group(1),
             "text": html.unescape(re.search(r"<p>(.*?)</p>", step, re.S).group(1)),
             "img": (re.search(r'<img src="([^"]+)"', step) or [None, None])[1],
             "dim": '<div class="shot dim">' in step}
            for step in slide.split('<div class="step">')[1:]]


def review_text(run_dir) -> str:
    return (run_dir / "flows" / "review.html").read_text()


def notes_beside(review: str, idea: str) -> list[str]:
    """The lines under an idea on the review's notes pages; [] when it isn't there."""
    pages = "".join(re.findall(r"<h2>Notes kept off the deck's slides.*?</section>", review, re.S))
    item = re.search(rf"<li><b>{idea} · .*?</b>(.*?)</li>", pages, re.S)
    return [html.unescape(line) for line in item.group(1).split("<br>")[1:]] if item else []


def notes_of(run_dir, idea: str) -> list[str]:
    return notes_beside(review_text(run_dir), idea)


def cover_of(document: str) -> str:
    return text_of(document.split('<section class="slide cover">')[1].split("</section>")[0])


def scores_text(run_dir) -> str:
    deck = review_text(run_dir)
    return text_of(deck[deck.index('<section class="slide scores">'):])


def test_each_idea_gets_a_flow_slide_and_a_why_slide_and_every_idea_is_scored_in_the_review(built):
    """The product team's deck is the cover and the idea slides; Simula's review is its own cover, the notes the
    slides leave out, and the score pages, and draws no idea."""
    parts = {}
    for idea, part, _ in slides(built):
        parts.setdefault(idea, []).append(part)
    assert parts == {"c01": list(flows.deck.PARTS), "c02": list(flows.deck.PARTS)}
    deck, review = (built / "flows" / "slides.html").read_text(), review_text(built)
    assert deck.count('<section class="slide') == 1 + 2 * len(parts) and "slide scores" not in deck
    assert re.findall(r'<section class="slide (\w+)', review) == ["cover", "scores", "scores"]
    assert "<h2>Notes kept off the deck's slides</h2>" in review
    assert not notes_of(built, "c01") and notes_of(built, "c02")
    assert "The product team's deck, flows/slides.pdf, draws c01, c02." in cover_of(review)
    scores = scores_text(built)
    for cid, verdict, checks in (("c01", "accepted", "11/11"), ("c02", "conditional", "10/11"),
                                 ("c03", "rejected", "9/11"), ("c04", "rejected", "11/11")):
        assert f"{cid} " in scores and f"{verdict} {checks}" in scores
    assert "didn't pass the right moment" in scores and "didn't pass a brand-safe place for the offer" in scores
    assert "exhibits/06-judge.md" in scores and "appendix" not in deck + review
    assert "FIXTURE TEST DATA" in deck and "FIXTURE TEST DATA" in review
    assert (built / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")
    assert (built / "flows" / "review.pdf").read_bytes().startswith(b"%PDF")
    exhibit = (built / "exhibits" / "07-flows.md").read_text()
    assert "## Not wired" not in exhibit and "## Layout" not in exhibit


def test_the_flow_slide_walks_from_today_to_what_they_get_and_says_each_thing_once(built):
    c = golden_idea(built)
    phones = flow_phones(built, "c01")
    assert [p["label"] for p in phones] == ["Today", "When it appears", "The offer", "The ad plays", "What they get"]
    assert [p["img"] for p in phones] == ["c01/screens/before.png", *(f"c01/screens/step-{i}.png" for i in range(4))]
    assert phones[1]["text"] == flows.wording.plain(c.trigger_event)
    assert phones[-1]["text"] == "A label appears: “Badge on”."
    assert not notes_of(built, "c01")
    flow = {(idea, part): text for idea, part, text in slides(built)}[("c01", "flow")]
    for words in (flows.wording.plain(flows.wording.caption(c)),
                  f"The offer says: “{c.offer_copy}” Saying no changes nothing.",
                  f"They get: {flows.wording.reward_line(c)}", f"How often: {c.frequency_cap}"):
        assert words in " ".join(flow.split()), words
    why = " ".join({(idea, part): text for idea, part, text in slides(built)}[("c01", "why")].split())
    reward = flows.wording.reward_line(c)
    assert f"What the user gets {reward[:1].upper()}{reward[1:]}." in why and "Badge shows" not in why
    deck = text_of((built / "flows" / "slides.html").read_text())
    assert deck.count("When the reward runs out") == 2


def test_the_cover_names_the_app_as_the_model_reads_it_else_the_config_key_title_cased(built):
    deck = (built / "flows" / "slides.html").read_text()
    app = built.parent.name
    title = golden(app).app_name or app.title()
    assert f"<h1>Rewarded-ad ideas for {title}</h1>" in deck and f"<title>Rewarded-ad ideas for {title}" in deck
    unnamed = golden(app).model_copy(update={"app_name": ""})
    named = unnamed.model_copy(update={"app_name": "Janitor AI"})
    assert flows.wording.app_title(named, app) == "Janitor AI" and flows.wording.app_title(unnamed, app) == app.title()


def test_the_reviews_cover_carries_a_line_for_each_earlier_stage_that_finished_only_part_of_its_work(tmp_path):
    """Never the product team's cover: a run's notes on itself are Simula's, whatever the app."""
    run_dir = seed_run(tmp_path, "luzia")
    for stage, outcome in (("qa", StageOutcome(status="partial", reasons=["round 1 stopped on the $ cap"],
                                                resume="simula run luzia --from qa")),
                           ("judge", StageOutcome())):
        (run_dir / stage).mkdir(exist_ok=True)
        runfolder.write_done(run_dir / stage, run_dir, [], [], {}, [run_dir / stage], FIXTURE, outcome=outcome)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    cover = cover_of(review_text(run_dir))
    assert cover.endswith("The qa step finished only part of its work: round 1 stopped on the $ cap.")
    assert "judge step" not in cover
    product = cover_of((run_dir / "flows" / "slides.html").read_text())
    assert "step finished only part" not in product and "round 1" not in product


def test_main_slides_carry_no_ids_or_cost_math(built):
    texts = [text for _, _, text in slides(built)]
    assert texts
    for text in texts:
        assert not re.search(r"\b(?:[sc]\d{2}(?:\.e\d+)?|M\d+)\b|new:|\$|eCPM|REWARD_VERIFIED|judge_1", text), text


def test_a_conditional_idea_carries_no_verdict_label_and_the_review_names_the_check_it_failed_in_plain_words(built):
    for idea, part, text in slides(built):
        assert "Conditional" not in text
        assert "didn't pass" not in text and "the right moment" not in text
        assert (flows.deck.OPEN_NOTES in " ".join(text.split())) == (part == "why" and idea == "c02")
    [note] = notes_of(built, "c02")
    assert note.startswith("Not every check passed: It didn't pass the right moment (") and "c5_moment" not in note


def test_review_notes_on_real_output_never_claim_a_failed_check_that_passed():
    decisions = DecisionsFile.model_validate_json((ROUND6 / "judge" / "decisions.json").read_text()).decisions
    candidates = flows.stage.load_candidates(ROUND6)
    chosen = flows.stage.select(decisions)
    assert len(chosen) == 4
    for d in chosen:
        c = candidates[d.candidate_id]
        text = " ".join(flows.deck.review_notes(drawn(c, d), ROUND6, False))
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
        idea = candidate(model, trigger_state_id=sid, trigger_event="After 3 days away.")
        text = flows.wording.reach_text(idea, model)
        assert text == (f"Reach scenario, not a measurement: the offer {where}, and appears only when this happens: "
                        "After 3 days away.")
    deep = candidate(model, trigger_state_id="s99", trigger_event="The user saves a story")
    assert "sits on a screen a few taps in" in flows.wording.reach_text(deep, model)
    assert "every visit" not in flows.wording.reach_text(deep, model)


@pytest.mark.parametrize("app", APPS)
def test_a_pop_up_is_placed_by_the_screen_it_covers_not_called_a_main_tab(app):
    model = golden(app)
    popups = [s for s in model.states if s.kind in ("modal", "sheet") and s.parent_id]
    if not popups:
        pytest.skip(f"{app}'s golden has no modal or sheet")
    for s in popups:
        covered = flows.wording.reach_text(candidate(model, trigger_state_id=s.parent_id), model).split("sits on ")[1]
        text = flows.wording.reach_text(candidate(model, trigger_state_id=s.id), model)
        assert text.split("the offer ")[1] == f"sits in a pop-up over {covered}", (s.id, text)
    if app == "janitorai":
        assert "sits in a pop-up over the first screen people see" in flows.wording.reach_text(
            candidate(model, trigger_state_id="s02"), model)


def judged(tmp_path, cid: str, fail: str | None) -> Decision:
    """A CONDITIONAL decision whose one verdict file fails `fail` (or nothing). With one judge a fail is every
    judge's, so a failing one can only be the judge's fallback pick, which keeps its reject's failure_type."""
    (tmp_path / "judge" / "verdicts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "judge" / "verdicts" / f"{cid}_judge_1_r1.json").write_text(verdict(cid, fail).model_dump_json())
    return fallback(cid, 1.0) if fail else decision(cid, "conditional", 1.0)


def test_the_judges_fallback_pick_reads_as_the_closest_idea_not_a_recommendation(tmp_path):
    missed = judged(tmp_path, "c01", "c5_moment")
    assert flows.deck.condition(missed, tmp_path, none_accepted=True) == (
        "The closest idea, not a recommendation:",
        ("No idea passed every check. This one passes every safety check but not the right moment "
         "(c5_moment reason for c01)."))
    assert flows.deck.condition(missed, tmp_path, none_accepted=False) == (
        "Not every check passed:", "It didn't pass the right moment (c5_moment reason for c01).")
    assert flows.deck.condition(judged(tmp_path, "c02", "g_policy"), tmp_path, none_accepted=True)[0] == \
        "Not every check passed:"
    assert flows.deck.condition(decision("c03", "conditional", 1.0, passed=10), ROUND6.parent / "nowhere", True) == (
        "Not every check passed:", "It passed 10 of 11 checks.")
    assert flows.deck.condition(judged(tmp_path, "c04", None), tmp_path, True) is None
    assert flows.deck.condition(decision("c05", "accept", 1.0), tmp_path, True) is None


def test_the_reviews_cover_takes_its_counts_by_name_so_two_can_never_swap():
    with pytest.raises(TypeError):
        flows.deck.review_cover_html("App", [], 4, 1, 0, 0, 2)
    cover = text_of(flows.deck.review_cover_html("App", [], cap=4, cut=2, unbuilt_fallbacks=1))
    assert "2 more idea(s) passed the review; the deck draws only the top 4 by rank" in cover
    assert "the closest couldn't be drawn" in cover and "passed the review but couldn't be drawn" not in cover


def test_a_survivor_past_the_cap_is_named_in_the_trace_and_counted_on_the_reviews_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(flows.stage, "MAX_IDEAS", 1)
    run_dir = run_flows(tmp_path, "luzia")
    note = next(line.note for line in read_trace(run_dir / "trace.jsonl") if line.step == "select")
    assert note == "accepted + conditional: c01; past the cap of 1, not drawn: c02"
    cover = cover_of(review_text(run_dir))
    assert ("1 more idea(s) passed the review; the deck draws only the top 1 by rank, and the score pages score the "
            "rest.") in cover
    assert "passed the review" not in cover_of((run_dir / "flows" / "slides.html").read_text())
    assert {idea for idea, _, _ in slides(run_dir)} == {"c01"}


def test_an_unbuilt_fallback_pick_is_named_as_the_closest_idea_not_as_one_that_passed(tmp_path):
    one_step = candidate(golden("luzia")).flow_steps[:1]
    run_dir = seed_run(tmp_path, "luzia", {"c01": {"flow_steps": one_step}})
    decisions = [fallback("c01", 1.0), decision("c03", "reject", 2.0, passed=9)]
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=decisions).model_dump_json())
    (run_dir / "judge" / "verdicts" / "c01_judge_1_r1.json").write_text(verdict("c01", "c5_moment").model_dump_json())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    cover = cover_of(review_text(run_dir))
    assert "No idea passed every check; the closest couldn't be drawn, and the score pages say why." in cover
    assert "passed the review" not in cover, cover
    assert cover_of((run_dir / "flows" / "slides.html").read_text()) == \
        "Rewarded-ad ideas for Luzia No idea is drawn in this deck."


def test_a_drawn_and_an_unbuilt_fallback_pick_read_as_one_coherent_line_on_the_cover(tmp_path):
    one_step = candidate(golden("luzia")).flow_steps[:1]
    run_dir = seed_run(tmp_path, "luzia", {"c01": {"flow_steps": one_step}})
    decisions = [fallback("c01", 2.0), fallback("c02", 1.0),
                 decision("c03", "reject", 3.0, passed=9)]
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=decisions).model_dump_json())
    for cid in ("c01", "c02"):
        path = run_dir / "judge" / "verdicts" / f"{cid}_judge_1_r1.json"
        path.write_text(verdict(cid, "c5_moment").model_dump_json())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    cover = cover_of(review_text(run_dir))
    assert ("No idea passed every check, so the closest are marked as not a recommendation; 1 of them couldn't be "
            "drawn, and the score pages say why.") in cover, cover
    assert "the closest is drawn" not in cover and "the closest couldn't be drawn" not in cover
    product = cover_of((run_dir / "flows" / "slides.html").read_text())
    assert "the strongest is drawn and marked as the closest idea" in product and "couldn't" not in product
    assert "check" not in product


def test_a_fallback_pick_is_named_on_the_cover_and_its_why_slide_never_says_recommended(tmp_path):
    run_dir = seed_run(tmp_path, "luzia", {"c01": {}})
    decisions = [fallback("c01", 1.0), decision("c03", "reject", 2.0, passed=9)]
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=decisions).model_dump_json())
    (run_dir / "judge" / "verdicts" / "c01_judge_1_r1.json").write_text(verdict("c01", "c5_moment").model_dump_json())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    deck = (run_dir / "flows" / "slides.html").read_text()
    cover = text_of(deck.split('<section class="slide main"')[0])
    assert "None of these is a recommendation yet; the strongest is drawn and marked as the closest idea." in cover
    why = {(idea, part): " ".join(text.split()) for idea, part, text in slides(run_dir)}[("c01", "why")]
    assert f"The closest idea, not a recommendation. {flows.deck.OPEN_NOTES}" in why and "Recommended" not in why
    assert "No idea passed every check" not in why
    assert notes_of(run_dir, "c01")[0].startswith("The closest idea, not a recommendation: No idea passed every check.")


def test_a_cost_line_that_isnt_pass_is_a_review_note_and_never_the_verdict():
    """Real round-6 output, as recorded (the old judge made three 11/11 ideas CONDITIONAL for cost) and as the judge
    now decides them in annotate mode (accepted, cost carried as a mark). The mark is Simula's review's, never a
    slide's."""
    decisions = DecisionsFile.model_validate_json((ROUND6 / "judge" / "decisions.json").read_text()).decisions
    ideas, model = flows.stage.load_candidates(ROUND6), golden("luzia")
    chosen = flows.stage.select(decisions)
    assert sorted(ideas[d.candidate_id].economics.verdict for d in chosen) == ["CONDITIONAL", "CONDITIONAL", "FAIL",
                                                                               "PASS"]
    for d in chosen:
        c, marked = ideas[d.candidate_id], ideas[d.candidate_id].economics.verdict != "PASS"
        for final in ("conditional", "accept"):
            flow = drawn(c, d.model_copy(update={"final": final}))
            texts = [text_of(s) for s in flows.deck.idea_slides(flow, model, ROUND6, False)]
            notes = flows.deck.review_notes(flow, ROUND6, False)
            for text in texts:
                assert "Cost check" not in text and "May lose a sale" not in text
                assert not marked or flows.deck.cost_question(c) not in text
                assert "Conditional" not in text
                assert not re.search(r"\$|eCPM", text), text
            assert (f"Cost check ({c.economics.verdict}): {flows.deck.cost_question(c)}." in notes) == marked
            assert (flows.deck.OPEN_NOTES in texts[-1]) == bool(notes)


def test_a_pass_that_may_lose_a_sale_says_so_in_the_review_without_calling_itself_a_cost_problem():
    decisions = DecisionsFile.model_validate_json((ROUND6 / "judge" / "decisions.json").read_text()).decisions
    ideas, model = flows.stage.load_candidates(ROUND6), golden("luzia")
    d = next(d for d in flows.stage.select(decisions) if ideas[d.candidate_id].economics.verdict == "PASS")
    c = ideas[d.candidate_id]
    c = c.model_copy(update={"economics": c.economics.model_copy(update={"lost_sale": "a place ahead of other users"})})
    texts = [text_of(s) for s in flows.deck.idea_slides(drawn(c, d), model, ROUND6, False)]
    assert not any("May lose a sale" in t or "could sell" in t for t in texts) and flows.deck.OPEN_NOTES in texts[-1]
    assert flows.deck.review_notes(drawn(c, d), ROUND6, False) == [
        "Cost check (PASS): it may give away something the app could sell."]


def rendered_overflows(slides_html: str) -> list[str]:
    deck = flows.deck.deck_html("t", slides_html)
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": flows.deck.SLIDE_W, "height": flows.deck.SLIDE_H})
        page.set_content(deck)
        return flows.pdf.overflows(page)


def why_slide(words: int) -> str:
    return ('<section class="slide main" data-part="why" data-idea="c01"><div class="why">'
            f'<div class="why-block"><b>What the app gets</b><p>{"word " * words}</p></div></div>'
            '<footer>When the reward runs out: it ends.</footer></section>')


@pytest.mark.parametrize("words, fits", [(150, True), (400, False)])
def test_a_why_slide_steps_its_text_down_to_fit_and_the_check_reports_what_still_doesnt(words, fits):
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": flows.deck.SLIDE_W, "height": flows.deck.SLIDE_H})
        page.set_content(flows.deck.deck_html("t", why_slide(words)))
        problems = flows.pdf.overflows(page)
        size = page.evaluate("document.querySelector('.why').style.fontSize")
    assert size in ("15px", "14px", "13px") and (problems == []) == fits, (size, problems)
    if not fits:
        assert size == "13px" and problems[0].startswith("slide 1 (c01 why)")


def test_the_deck_brings_its_own_font_so_it_lays_out_the_same_on_every_machine():
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": flows.deck.SLIDE_W, "height": flows.deck.SLIDE_H})
        page.set_content(flows.deck.deck_html("t", why_slide(10)))
        flows.pdf.overflows(page)
        loaded = page.evaluate("() => Promise.all([...document.fonts].map(f => f.load()))"
                               ".then(faces => faces.filter(f => f.family === 'Inter').map(f => Number(f.weight)))")
    assert sorted(loaded) == list(flows.deck.FONT_WEIGHTS)
    assert (flows.deck.FONTS / "OFL.txt").exists()


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
    review notes carry both the missed check (with round 6's longest judge reason) and the cost line."""
    decisions = DecisionsFile.model_validate_json((ROUND6 / "judge" / "decisions.json").read_text()).decisions
    ideas, model = flows.stage.load_candidates(ROUND6), golden("luzia")
    fallback_run = tmp_path / "run"
    shutil.copytree(ROUND6, fallback_run)
    reasons = [getattr(v, k).reason for _, v in flows.deck.verdicts(decisions[0], ROUND6) for k in GATES + JUDGMENT]
    for path in (fallback_run / "judge" / "verdicts").iterdir():
        v = SavedVerdict.model_validate_json(path.read_text())
        path.write_text(v.model_copy(update={"c5_moment": Check(passed=False, reason=max(reasons, key=len))})
                        .model_dump_json())
    chosen = flows.stage.select(decisions)
    accepted = [drawn(ideas[d.candidate_id], d) for d in chosen]
    picks = [drawn(ideas[d.candidate_id], d.model_copy(update={"final": "conditional", "failure_type": "proposal"}))
             for d in chosen]
    deck = [slide for f in accepted for slide in flows.deck.idea_slides(f, model, ROUND6, False)]
    deck += [slide for f in picks for slide in flows.deck.idea_slides(f, model, fallback_run, True)]
    notes = flows.deck.notes_slides(accepted, ROUND6, False) + flows.deck.notes_slides(picks, fallback_run, True)
    assert sum("The closest idea, not a recommendation." in s for s in deck) == 4
    assert "".join(notes).count("The closest idea, not a recommendation:") == 4
    assert "".join(notes).count("Cost check (") == 6 and "Cost check (" not in "".join(deck)
    assert rendered_overflows("".join(deck + notes + flows.deck.score_slides(decisions, ideas, [], ROUND6))) == []


@pytest.mark.parametrize("run_dir", sorted(p.parents[1] for p in (ROOT / "runs").glob("*/*/judge/decisions.json")),
                         ids=lambda run_dir: run_dir.parent.name)
def test_a_committed_runs_slides_carry_none_of_its_review_notes_and_the_review_has_each_beside_its_idea(run_dir):
    """A committed run's flows inputs, drawn as the stage picks them, with every flag a walk can raise planted on the
    first idea: no slide says a flag, a missed check, a judge's reason or a cost line, and the review lists each
    idea's notes under it; an idea's why slide says only that they exist."""
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    ideas, none_accepted = flows.stage.load_candidates(run_dir), not any(d.final == "accept" for d in decisions)
    ideas_drawn = [drawn(ideas[d.candidate_id], d) for d in flows.stage.select(decisions)]
    if not ideas_drawn:
        pytest.skip("the judges passed no idea, so nothing is drawn")
    planted = ideas_drawn[0]
    planted["shots"][2]["wired"], planted["shots"][-1]["reward_shown"] = False, False
    planted.update(copy_shown=False, decline="saying no led elsewhere", ad_fail="no note that nothing was used")
    ctx = ctx_for(run_dir, run_dir.parent.name)
    deck = flows.deck.deck(ctx, model, ideas_drawn, decisions)
    review = flows.deck.review(ctx, model, ideas_drawn, [], decisions, ideas, cap=flows.stage.MAX_IDEAS)
    product = text_of(deck)
    texts = {(idea, part): text_of(body) for part, idea, body in re.findall(
        r'<section class="slide main" data-part="(\w+)" data-idea="([\w-]+)">(.*?)</section>', deck, re.S)}
    for f in ideas_drawn:
        c, notes = f["candidate"], flows.deck.review_notes(f, run_dir, none_accepted)
        missed, cost = flows.deck.condition(f["decision"], run_dir, none_accepted), flows.deck.cost_question(c)
        flags = [flag for phone in flows.deck.row_phones(f) for flag in phone["flags"]]
        said = flags + ([flows.wording.plain(missed[1])] if missed else []) + ([cost] if cost else [])
        assert len(notes) == len(said) and not [s for s in said if s in product], c.id
        assert notes_beside(review, c.id) == notes
        assert texts[(c.id, "why")].count(flows.deck.OPEN_NOTES) == bool(notes)
        assert texts[(c.id, "flow")].count(flows.deck.STEPS_PENDING) == any(flags)
    assert len(notes_beside(review, planted["candidate"].id)) >= 5
    assert "Cost check" not in product and "May lose a sale" not in product and 'class="flag"' not in deck


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
    ideas, model = flows.stage.load_candidates(run_dir), golden(app)
    scores = text_of("".join(flows.deck.score_slides(decisions, ideas, [], run_dir)))
    assert scores.count("flagged by code") == 1
    assert f"flagged by code: {FLAG}" in scores[scores.index("c01 "):scores.index("c02 ")]
    main = text_of("".join(flows.deck.idea_slides(drawn(ideas["c01"], decisions[0]), model, run_dir, False)))
    page = flows.page.strip_runtime((run_dir / "mock" / "index.html").read_text())
    editor = flows.editor.editor_brief(ideas["c01"], model, page) + flows.editor.system_prompt()
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
    assert flow_phones(run_dir, "c01")[-1]["text"] == "Badge shows"
    assert notes_of(run_dir, "c01") == [
        f"Flow slide, step 5 (What they get, walk step 4): {flows.wording.REWARD_NOT_SHOWN}."]
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    root = golden_idea(run_dir).flow_steps[-1].state_id
    assert f"| {flows.wording.REWARD_NOT_SHOWN}: step 4 |" in exhibit
    assert f"## Reward not shown\n\n- c01 step 4 (`{root}`): nothing on it changes when the reward is granted" in exhibit
    assert "| c02 · A second look | conditional | 2 / 1 | 4 / 4 | 1 | ok | ok | only a label: step 4 |" in exhibit
    assert any(line.step == "walk:c01" and line.outcome == "error" and flows.wording.REWARD_NOT_SHOWN in line.note
               for line in read_trace(run_dir / "trace.jsonl"))


@pytest.mark.parametrize("app", APPS)
def test_a_failed_ad_must_bring_the_user_back_with_nothing_granted_and_say_so(tmp_path, app):
    run_dir = run_flows(tmp_path, app, only="c01", hide_note=True)
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    assert f"| ok | {flows.wording.NOT_WIRED}: a failed ad shows no note that nothing was used |" in exhibit
    assert "| c02 · A second look | conditional | 2 / 1 | 4 / 4 | 1 | ok | ok |" in exhibit
    assert notes_of(run_dir, "c01") == [
        f"Flow slide, step 4 (The ad plays, walk step 3): a failed ad: {flows.wording.NOT_WIRED}."]
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
    ("[data-unrewarded]{top:200px!important}", "Level 2 open", "Level 2 locked", (True, None)),
    ("[data-unrewarded]{top:2000px!important}", "Badge on", "Old badge", (True, ["Badge on"])),
    (".card{z-index:1;background:#fff;width:200px;height:60px;top:590px!important}", "Badge on", "Old badge",
     (True, ["Badge on"])),
    ("[data-unrewarded]{top:200px!important;pointer-events:none}", "Level 2 open", "Level 2 locked", (True, None)),
    ("[data-unrewarded]{top:200px!important} body::after{content:'';position:fixed;inset:0;z-index:5}", "Level 2 open",
     "Level 2 locked", (True, None)),
    ("[data-unrewarded]{top:200px!important}", "Level 2 open", '<span style="visibility:visible">Level 2 locked</span>',
     (True, None)),
])
def test_the_reward_effect_names_labels_and_ignores_render_noise(tmp_path, extra, label, before, expected):
    """Only a label appears; a label appears and a card moves; nothing but a 3-level shade (render noise) changes;
    the unlocked form appears and the locked one the page marked data-unrewarded goes, elsewhere or in its place;
    a data-unrewarded element below the captured view, or covered, changes nothing the capture shows; one in place
    that ignores the pointer, sits under a transparent overlay, or paints only through a child that sets its own
    visibility, still does."""
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport=render.VIEWPORT)
        page.set_content(REWARD_PAGE.replace("{flow_css}", flows.page.FLOW_CSS).replace("{extra}", extra)
                         .replace("{label}", label).replace("{before}", before))
        page.evaluate(flows.walk.REWARDED_JS, True)
        render.screenshot(page, path=tmp_path / "on.png", animations="disabled")
        assert flows.walk.reward_effect(page, tmp_path / "on.png") == expected


def shot(wired: bool = True, shown: bool | None = None, labels: list[str] | None = None) -> dict:
    return {"wired": wired, "reward_shown": shown, "reward_labels": labels}


@pytest.mark.parametrize("after_ad, expected", [
    ([shot(wired=False)], "not reached"),
    ([shot()], "new screen, not measured"),
    ([shot(), shot(shown=False)], f"{flows.wording.REWARD_NOT_SHOWN}: step 5"),
    ([shot(shown=True, labels=["Badge on"])], "only a label: step 4"),
    ([shot(shown=True, labels=["Badge on"]), shot(shown=True)], "yes"),
])
def test_the_exhibit_says_yes_only_when_the_reward_check_ran_and_found_more_than_a_label(after_ad, expected):
    before = [shot()] * 3
    assert flows.stage.reward_text({"shots": before + after_ad, "ad_at": 2}) == expected


def test_a_new_screen_after_the_ad_says_only_what_the_walk_saw_not_the_reward_the_idea_claims(tmp_path):
    """Greptile on #11: a new screen after the ad has no reward to switch off and check, yet its slide caption said the
    reward was there."""
    steps = candidate(golden("luzia")).flow_steps
    claim = "Their three bonus replies are ready."
    thanks = steps[-1].model_copy(update={"state_id": "new:thanks", "caption": claim})
    run_dir = run_flows(tmp_path, "luzia", changes={"c01": {"flow_steps": [*steps[:3], thanks]}})
    last = flow_phones(run_dir, "c01")[-1]
    assert (last["label"], last["text"]) == ("What they get", flows.deck.AFTER_PLAY) and not notes_of(run_dir, "c01")
    assert claim not in text_of((run_dir / "flows" / "slides.html").read_text())
    assert "| new screen, not measured |" in (run_dir / "exhibits" / "07-flows.md").read_text()


def test_labels_are_quoted_as_what_appears():
    assert flows.deck.labels_text(["Badge on"]) == "A label appears: “Badge on”."
    assert flows.deck.labels_text(["A", "B"]) == "Labels appear: “A”, “B”."
    assert flows.deck.labels_text([]) == "A label appears."


def test_the_ad_card_takes_the_apps_palette_and_its_most_colorful_color_as_the_accent():
    root = ":root{--bg-1:#303337;--bg-2:#000000;--fg-1:#ffffff;--fg-2:#af89f0;--fg-3:#5a5c63;--font-1:Roboto,sans-serif}"
    assert flows.page.ad_palette_css(root) == ".sa-card{--sa-accent:#af89f0;--sa-on-accent:#000}\n"
    assert flows.page.ad_palette_css(":root{--bg-1:#ffffff;--bg-3:#4264fc}") == ".sa-card{--sa-accent:#4264fc;--sa-on-accent:#fff}\n"
    assert flows.page.ad_palette_css(":root{--bg-1:#303337;--fg-1:#ffffff}") == ""
    assert flows.page.ad_palette_css("<p>a mock without a palette</p>") == ""
    assert "--sa-accent:#e11d48;" in flows.page.ad_palette_css(":root{--bg-1:#fffdf7;--bg-2:#e11d48}")
    assert "--sa-accent:#20808d;" in flows.page.ad_palette_css(":root{--bg-1:#000814;--fg-1:#20808d}")
    c = candidate(golden("luzia"))
    page = (f"<style>{root}{flows.page.FLOW_CSS}{flows.page.ad_palette_css(root)}</style>"
            f'<section data-screen="ad">{flows.page.ad_card(c)}</section>')
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
    ad = flow_phones(run_dir, "c01")[3]
    assert notes_of(run_dir, "c01")[0] == \
        f"Flow slide, step 4 (The ad plays, walk step 3): {flows.wording.NOT_WIRED}."
    assert not any("(The offer" in note for note in notes_of(run_dir, "c01"))
    assert ad["img"] == "c01/screens/step-1.png"
    assert "| 2 / 4 | 0 | ok | " in (run_dir / "exhibits" / "07-flows.md").read_text()
    assert "| not reached | `flows/c01/index.html` |" in (run_dir / "exhibits" / "07-flows.md").read_text()
    assert not (run_dir / "flows" / "c01" / "screens" / "step-2.png").exists()
    broken = [line for line in read_trace(run_dir / "trace.jsonl") if line.step == "walk:c01" and line.outcome == "error"]
    assert broken and "step 3 not wired" in broken[0].note
    assert "## Not wired" in (run_dir / "exhibits" / "07-flows.md").read_text()


@pytest.mark.parametrize("options", [{"wire_accept": False}, {"show_reward": False}],
                         ids=["unwired", "reward-not-shown"])
def test_a_flow_slide_dims_each_step_the_walk_couldnt_show_and_says_once_that_some_arent_working(tmp_path, options):
    """Red team on #39: with the flags moved to the review, an unwired step's stale screenshot, and a reward screen the
    walk found unchanged, read as working steps. The flagged phones are dimmed and one line under the row says so; the
    other idea's slide is untouched."""
    run_dir = run_flows(tmp_path, "luzia", only="c01", **options)
    phones, notes = flow_phones(run_dir, "c01"), notes_of(run_dir, "c01")
    assert notes and {n for n, phone in enumerate(phones, 1) if phone["dim"]} == \
        {int(re.match(r"Flow slide, step (\d+) ", note).group(1)) for note in notes}
    flow = {(idea, part): " ".join(text.split()) for idea, part, text in slides(run_dir) if part == "flow"}
    assert flow[("c01", "flow")].count(flows.deck.STEPS_PENDING) == 1
    assert flows.deck.STEPS_PENDING not in flow[("c02", "flow")]
    assert not any(phone["dim"] for phone in flow_phones(run_dir, "c02"))
    assert "## Layout" not in (run_dir / "exhibits" / "07-flows.md").read_text()


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


def test_default_selection_is_every_accept_first_then_conditional_by_rank_at_most_four():
    """D10: a CONDITIONAL never takes an accept's slot, whatever its rank. D11: an idea the judges split on isn't
    drawn unless flows/approvals.json promotes it."""
    split = decision("c05", "conditional", 2.0).model_copy(update={"judgment_splits": ["c2_evidence"]})
    decisions = [decision("c01", "reject", 9.0), decision("c02", "accept", 1.0), decision("c03", "conditional", 3.0),
                 decision("c04", "needs_human", 5.0), split, *(decision(f"c1{n}", "accept", 0.5) for n in range(4))]
    assert [d.candidate_id for d in flows.stage.select(decisions)] == ["c02", "c10", "c11", "c12"]
    assert [d.candidate_id for d in flows.stage.select(decisions[:5])] == ["c02", "c03"]
    assert [d.candidate_id for d in flows.stage.select(decisions[:5], ["c05"])] == ["c02", "c03", "c05"]


def test_promoting_an_idea_the_judges_passed_keeps_the_closest_idea():
    """Fable L9: with nothing accepted, a promotion of a passed CONDITIONAL (gate mode's cost question) turned the
    closest idea off; only a person's call on a split replaces it."""
    split = decision("c02", "conditional", 2.0).model_copy(update={"judgment_splits": ["c2_evidence"]})
    decisions = [decision("c01", "conditional", 1.0), split]
    assert [d.candidate_id for d in flows.stage.select(decisions)] == ["c02", "c01"]
    assert [d.candidate_id for d in flows.stage.select(decisions, ["c01"])] == ["c02", "c01"]


def test_a_promotion_takes_the_slot_of_the_lowest_ranked_idea_nobody_promoted():
    """Four accepts fill the deck; a promoted fifth idea, a split, is drawn in place of the lowest accept."""
    split = decision("c05", "conditional", 2.0).model_copy(update={"judgment_splits": ["c2_evidence"]})
    decisions = [*(decision(f"c1{n}", "accept", 1.0 - n / 10) for n in range(4)), split]

    def picked(promoted=()) -> list[str]:
        return [d.candidate_id for d in flows.stage.select(decisions, promoted)]
    assert picked() == ["c10", "c11", "c12", "c13"]
    assert picked(["c05"]) == ["c10", "c11", "c12", "c05"]
    assert picked(["c05", "c13"]) == ["c10", "c11", "c13", "c05"]



def split_run(tmp_path, approvals: dict | None = None, changes: dict | None = None):
    """A seeded run whose c02 the judges split on, with pd-c7-subtle's committed r1 verdicts under VF': judge_1 fails
    C7 and passes C5, judge_2 the reverse. c01 is accepted."""
    run_dir = seed_run(tmp_path, "luzia", changes)
    saved = validate.load_runs(validate.VERDICTS / "VF2")
    paths = [f"judge/verdicts/c02_{who}_r1.json" for who in validate.JUDGES]
    for path, who in zip(paths, validate.JUDGES):
        (run_dir / path).write_text(saved[("pd-c7-subtle", who)][0].model_dump_json())
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    decisions = [d.model_copy(update={"judgment_splits": ["c5_moment", "c7_specific"], "verdict_paths": paths,
                                      "checks_passed": 9}) if d.candidate_id == "c02" else d for d in decisions]
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=decisions).model_dump_json())
    if approvals is not None:
        (run_dir / "flows").mkdir(exist_ok=True)
        (run_dir / "flows" / "approvals.json").write_text(json.dumps(approvals))
    calls = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir, calls))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    return run_dir, calls


def test_an_idea_the_judges_split_on_isnt_drawn_and_waits_on_the_needs_your_call_page(tmp_path):
    run_dir, calls = split_run(tmp_path)
    assert calls == ["c01"] and (run_dir / "flows" / "c01").is_dir() and not (run_dir / "flows" / "c02").exists()
    assert {idea for idea, _, _ in slides(run_dir)} == {"c01"}
    deck = review_text(run_dir)
    assert "Needs your call" not in (run_dir / "flows" / "slides.html").read_text()
    page = text_of(deck.split("<h2>Needs your call")[1].split("</section>")[0])
    saved = validate.load_runs(validate.VERDICTS / "VF2")
    j1, j2 = (saved[("pd-c7-subtle", who)][0] for who in validate.JUDGES)
    for k, no, yes in [("c5_moment", j2, j1), ("c7_specific", j1, j2)]:
        clause = (f'"{flows.wording.PLAIN_CHECKS[k]}": one reviewer: no ({getattr(no, k).reason.rstrip(".")}); '
                  f'another: yes ({getattr(yes, k).reason.rstrip(".")}).')
        assert text_of(html.escape(flows.wording.plain(clause))) in page
    assert page.index("the right moment") < page.index("specific to this app") and "c02 · " in page
    assert "flows/approvals.json" in page and "add its \"To approve\" entry to promote" in page
    assert "1 idea(s) split the reviewers, so they aren't drawn" in cover_of(deck)



def run3(tmp_path, approvals: dict | None = None):
    """Run 3 of the saved VF' runs as a seeded deck: nothing accepted, and three known-good ideas split on c2
    (judge_2 fails it, judge_1 passes it), ranked c02, c03, c01."""
    run_dir = seed_run(tmp_path, "luzia")
    cases = {c.id: c for c in validate.load_cases()}
    saved = validate.load_runs(validate.VERDICTS / "VF2")
    decisions = [decision("c04", "reject", 0.1)]
    for cid, case, rank in [("c01", "kg-candycrush-01", 1.0), ("c02", "kg-run-janitorai-c02", 3.0),
                            ("c03", "kg-run-luzia-c05", 2.0)]:
        pair = [saved[(case, who)][2] for who in validate.JUDGES]
        paths = [f"judge/verdicts/{cid}_{who}_r3.json" for who in validate.JUDGES]
        for path, v in zip(paths, pair):
            (run_dir / path).write_text(v.model_dump_json())
        d = judge.decide(cases[case].candidate, pair, 2, "annotate", paths)
        assert (d.final, d.judgment_splits) == ("conditional", ["c2_evidence"])
        decisions.append(d.model_copy(update={"candidate_id": cid, "rank_score": rank}))
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=decisions).model_dump_json())
    if approvals is not None:
        (run_dir / "flows").mkdir(exist_ok=True)
        (run_dir / "flows" / "approvals.json").write_text(json.dumps(approvals))
    calls = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir, calls))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    return run_dir, calls


def test_with_no_accept_the_top_split_is_drawn_as_the_closest_idea_and_the_rest_wait(tmp_path):
    """The deck still gets exactly one full slide, the top-ranked split, worded as the closest idea; the other two
    wait on Needs your call."""
    run_dir, calls = run3(tmp_path)
    assert calls == ["c02"] and {idea for idea, _, _ in slides(run_dir)} == {"c02"}
    assert not (run_dir / "flows" / "c01").exists() and not (run_dir / "flows" / "c03").exists()
    why = {(idea, part): " ".join(text.split()) for idea, part, text in slides(run_dir)}[("c02", "why")]
    assert f"The closest idea, not a recommendation. {flows.deck.OPEN_NOTES}" in why and "reviewer" not in why
    assert notes_of(run_dir, "c02")[0].startswith(
        'Closest idea: The reviewers split on "backed by what was seen in the app"; confirm it before building. '
        '"backed by what was seen in the app": one reviewer: no (')
    review = review_text(run_dir)
    page = text_of(review.split("<h2>Needs your call")[1].split("</section>")[0])
    assert "c01 · " in page and "c03 · " in page and "c02 · " not in page
    assert "the strongest is drawn" in cover_of((run_dir / "flows" / "slides.html").read_text())
    assert "the closest is drawn" in cover_of(review) and "2 idea(s) split the reviewers" in cover_of(review)


def test_with_no_accept_splits_a_person_approved_are_labelled_as_approved_never_as_the_closest(tmp_path):
    run_dir, calls = run3(tmp_path, {"promote": [{"id": cid, "splits": ["c2_evidence"]} for cid in ("c02", "c03")]})
    assert sorted(calls) == ["c02", "c03"]
    whys = {idea: " ".join(text.split()) for idea, part, text in slides(run_dir) if part == "why"}
    assert all(w.count(flows.deck.OPEN_NOTES) == 1 and "closest idea" not in w.lower() and "reviewer" not in w
               for w in whys.values())
    assert all(" ".join(notes_of(run_dir, idea)).count("Approved by a person: The reviewers split on") == 1
               for idea in whys)
    review = review_text(run_dir)
    assert "2 idea(s) the reviewers split on are drawn because a person approved them" in cover_of(review)
    assert "the closest" not in cover_of(review) + cover_of((run_dir / "flows" / "slides.html").read_text())
    assert "c01 · " in text_of(review.split("<h2>Needs your call")[1])

def test_with_no_accept_a_promotion_replaces_the_closest_idea_and_the_deck_is_just_the_persons_pick(tmp_path):
    run_dir, calls = run3(tmp_path, {"promote": [{"id": "c03", "splits": ["c2_evidence"]}]})
    assert calls == ["c03"] and {idea for idea, _, _ in slides(run_dir)} == {"c03"}
    assert "closest" not in cover_of((run_dir / "flows" / "slides.html").read_text())
    review = cover_of(review_text(run_dir))
    assert "closest" not in review and "1 idea(s) the reviewers split on are drawn because a person approved" in review
    assert "2 idea(s) split the reviewers" in review


def test_with_no_accept_holding_the_closest_idea_draws_no_replacement(tmp_path):
    run_dir, calls = run3(tmp_path, {"hold": ["c02"]})
    assert calls == [] and not slides(run_dir)
    assert cover_of((run_dir / "flows" / "slides.html").read_text()).endswith("No idea is drawn in this deck.")
    review = cover_of(review_text(run_dir))
    assert "draws no idea." in review and "A person held c02 out of the deck" in review
    assert "2 idea(s) split the reviewers" in review and "No idea passed the review" not in review


def select_note(run_dir) -> str:
    return next(line.note for line in read_trace(run_dir / "trace.jsonl") if line.step == "select")


def test_the_reviews_cover_names_a_persons_hold_of_a_split_waiting_on_them_but_not_of_a_reject(tmp_path):
    run_dir, calls = split_run(tmp_path / "split", approvals={"hold": ["c02"]})
    review = review_text(run_dir)
    assert calls == ["c01"] and "Needs your call" not in review
    assert "A person held c02 out of the deck in flows/approvals.json." in cover_of(review)
    assert "split the reviewers" not in cover_of(review)
    assert select_note(run_dir) == "accepted + conditional, flows/approvals.json holding c02: c01"


@pytest.mark.parametrize("approvals, effect", [
    ({"hold": ["c03"]}, "holding c03, which the judges didn't pass"),  # c03 was rejected, so never drawn
    ({"promote": ["c01"]}, "promoting c01, which the judges passed already"),
])
def test_an_override_that_changes_nothing_is_reported_as_no_effect_never_as_applied(tmp_path, approvals, effect):
    run_dir, calls = split_run(tmp_path, approvals=approvals)
    assert calls == ["c01"] and "held" not in cover_of(review_text(run_dir))
    assert select_note(run_dir) == f"accepted + conditional: c01; needs your call: c02; no effect: {effect}"
    assert f"no effect: {effect}" in (run_dir / "exhibits" / "07-flows.md").read_text()


def test_a_hold_past_the_cap_is_reported_as_past_the_cap_not_as_taking_an_idea_out(tmp_path, monkeypatch):
    """c01 and c02 both pass; with a cap of 1 the deck draws c01 only, so holding c02 changes nothing."""
    monkeypatch.setattr(flows.stage, "MAX_IDEAS", 1)
    run_dir = seed_run(tmp_path, "luzia")
    (run_dir / "flows").mkdir()
    (run_dir / "flows" / "approvals.json").write_text('{"hold": ["c02"]}')
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    cover = cover_of(review_text(run_dir))
    assert ("A person held c02 in flows/approvals.json, past the deck's cap of 1, so the deck would have left it out "
            "anyway.") in cover and "out of the deck in" not in cover
    assert select_note(run_dir) == ("accepted + conditional: c01; no effect: holding c02, which the cap of 1 leaves "
                                    "out anyway")


def test_a_promoted_split_that_cant_be_drawn_is_named_as_a_persons_approval_not_as_one_that_passed(tmp_path):
    one_step = candidate(golden("luzia")).flow_steps[:1]
    run_dir, calls = split_run(tmp_path, approvals={"promote": [APPROVED_C02]},
                               changes={"c02": {"flow_steps": one_step}})
    cover = cover_of(review_text(run_dir))
    assert calls == ["c01"] and "passed the review but couldn't be drawn" not in cover
    assert "1 idea(s) the reviewers split on were approved by a person but couldn't be drawn" in cover


def test_an_approval_that_doesnt_hold_on_the_closest_idea_is_named_on_the_reviews_cover(tmp_path):
    """run3's top split c02 is drawn as the closest idea; an approval naming a check the reviewers no longer split on
    is set aside, and since c02 isn't on Needs your call, the review's cover says so."""
    run_dir, calls = run3(tmp_path, {"promote": [{"id": "c02", "splits": ["c1_revealed_value"]}]})
    assert calls == ["c02"]
    assert ("The approval of c02 in flows/approvals.json doesn't hold (the reviewers' disagreement changed since your "
            "approval), so it is treated as the closest idea, not as a person's approval.") in \
        cover_of(review_text(run_dir))
    why = {(idea, part): " ".join(text.split()) for idea, part, text in slides(run_dir)}[("c02", "why")]
    assert "The closest idea, not a recommendation." in why and "Approved by a person" not in why
    assert notes_of(run_dir, "c02")[0].startswith("Closest idea:")


APPROVED_C02 = {"id": "c02", "splits": ["c5_moment", "c7_specific"]}


def test_approving_a_split_idea_draws_it_as_any_idea_and_only_the_review_records_the_persons_call(tmp_path):
    run_dir, calls = split_run(tmp_path, approvals={"promote": [APPROVED_C02]})
    assert sorted(calls) == ["c01", "c02"] and (run_dir / "flows" / "c02").is_dir()
    deck = (run_dir / "flows" / "slides.html").read_text()
    assert "Needs your call" not in deck + review_text(run_dir)
    c02 = re.findall(r'<section class="slide main" data-part="\w+" data-idea="c02">.*?</section>', deck, re.S)
    assert len(c02) == 2 and not any(chip in s for s in c02 for chip in ("Approved by a person", "Conditional",
                                                                          "Closest idea"))
    assert "1 idea(s) the reviewers split on are drawn because a person approved them" in cover_of(review_text(run_dir))
    why = {(idea, part): " ".join(text.split()) for idea, part, text in slides(run_dir)}[("c02", "why")]
    assert flows.deck.OPEN_NOTES in why and "reviewers" not in why
    assert notes_of(run_dir, "c02")[0].startswith(
        'Approved by a person: The reviewers split on "the right moment" and "specific to this app".')


def test_an_approval_naming_a_check_that_doesnt_exist_is_set_aside_as_a_typo():
    """Fable L9: a mistyped check name was reported as the reviewers' disagreement having changed."""
    split = decision("c02", "conditional", 0.5).model_copy(update={"judgment_splits": ["c5_moment", "c7_specific"]})
    approval = {"id": "c02", "splits": ["c5_momnet", "c7_specific"]}
    assert flows.stage.honored([approval], [split]) == (
        [], {"c02": "your approval names a check that doesn't exist: c5_momnet"})


def test_an_approval_holds_only_for_the_disagreement_it_approved(tmp_path):
    """The judges now split on C5 and C7; an approval naming only C7, or naming no checks, isn't honored: the idea
    goes back to Needs your call and says why."""
    for entry, why in [({"id": "c02", "splits": ["c7_specific"]}, "the reviewers' disagreement changed since your "
                        "approval"), ("c02", "approved without the checks the reviewers split on")]:
        run_dir, calls = split_run(tmp_path / why[:12].replace(" ", "-"), approvals={"promote": [entry]})
        page = text_of(review_text(run_dir).split("<h2>Needs your call")[1].split("</section>")[0])
        assert calls == ["c01"] and f"Not drawn: {why}." in page
        assert 'To approve: {"id": "c02", "splits": ["c5_moment", "c7_specific"]}' in page


def test_an_approved_split_at_the_cap_takes_the_slot_of_the_idea_nobody_promoted(tmp_path, monkeypatch):
    monkeypatch.setattr(flows.stage, "MAX_IDEAS", 1)
    run_dir, calls = split_run(tmp_path, approvals={"promote": [APPROVED_C02]})
    review = review_text(run_dir)
    assert calls == ["c02"] and "Needs your call" not in review
    assert select_note(run_dir).endswith("promoting c02: c02; past the cap of 1, not drawn: c01")
    assert ("1 more idea(s) passed the review; the deck draws only 1, a person's promotions first and then by rank"
            in cover_of(review))


def test_promoting_a_passed_idea_past_the_cap_draws_it_and_is_reported_as_applied(tmp_path, monkeypatch):
    """c01 and c02 both pass; with a cap of 1 the deck draws c01 unless a person promotes c02."""
    monkeypatch.setattr(flows.stage, "MAX_IDEAS", 1)
    run_dir = seed_run(tmp_path, "luzia")
    (run_dir / "flows").mkdir()
    (run_dir / "flows" / "approvals.json").write_text('{"promote": ["c02"]}')
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    assert select_note(run_dir) == ("accepted + conditional, flows/approvals.json promoting c02: c02; past the cap of "
                                    "1, not drawn: c01")


def test_more_promotions_that_hold_than_the_cap_stop_flows_before_it_clears_the_last_deck(tmp_path, monkeypatch):
    run_dir, _ = split_run(tmp_path)
    before = sorted(p.name for p in (run_dir / "flows").iterdir())
    monkeypatch.setattr(flows.stage, "MAX_IDEAS", 1)
    (run_dir / "flows" / "approvals.json").write_text(json.dumps({"promote": ["c01", "c02"]}))  # c02's doesn't hold
    assert flows.stage.load_approvals(run_dir / "flows", DecisionsFile.model_validate_json(
        (run_dir / "judge" / "decisions.json").read_text()).decisions) == (["c01", "c02"], [])
    (run_dir / "flows" / "approvals.json").write_text(json.dumps({"promote": ["c01", APPROVED_C02]}))
    with pytest.MonkeyPatch.context() as mp, \
            pytest.raises(ValueError, match="2 promotions hold, more than the deck's cap of 1; promote at most 1"):
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    assert sorted(p.name for p in (run_dir / "flows").iterdir()) == sorted([*before, "approvals.json"])
    assert (run_dir / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")


def test_a_deck_whose_only_survivors_wait_on_a_person_never_says_no_idea_passed_the_review(tmp_path):
    run_dir, calls = split_run(tmp_path, approvals={"hold": ["c01"]})
    cover = cover_of(review_text(run_dir))
    assert calls == [] and "No idea passed the review" not in cover and "1 idea(s) split the reviewers" in cover
    assert "A person held c01 out of the deck in flows/approvals.json." in cover


def test_promoting_a_split_adds_it_to_the_accepted_ideas_and_holding_leaves_just_that_idea_out(tmp_path):
    """A person's approvals change only the ideas they name: promoting c02 keeps the accepted c01 drawn; holding c01
    draws everything else the judges would; an older file's "approved" list adds the same way."""
    for name, approvals, drawn in [("promote", {"promote": [APPROVED_C02]}, ["c01", "c02"]),
                                   ("both", {"promote": [APPROVED_C02], "hold": ["c01"]}, ["c02"]),
                                   ("older", {"approved": [APPROVED_C02]}, ["c01", "c02"])]:
        run_dir, calls = split_run(tmp_path / name, approvals=approvals)
        assert sorted(calls) == drawn and sorted({idea for idea, _, _ in slides(run_dir)}) == drawn, name
    note = next(line.note for line in read_trace(run_dir / "trace.jsonl") if line.step == "select")
    assert note == "accepted + conditional, flows/approvals.json promoting c02: c01 c02"


def test_approvals_add_and_hold_by_id_and_never_draw_a_reject():
    split = decision("c04", "conditional", 3.0).model_copy(update={"judgment_splits": ["c2_evidence"]})
    decisions = [decision("c01", "accept", 1.0), decision("c02", "conditional", 0.5), decision("c03", "reject", 2.0),
                 split]

    def picked(promoted=(), held=()) -> list[str]:
        return [d.candidate_id for d in flows.stage.select(decisions, promoted, held)]
    assert picked() == ["c01", "c02"]
    assert picked(["c04", "c03", "zz"]) == ["c01", "c04", "c02"]
    assert picked(held=["c01", "c04"]) == ["c02"]
    assert picked(["c04"], ["c04"]) == ["c01", "c02"]
    splits = [decision(cid, "conditional", rank).model_copy(update={"judgment_splits": ["c2_evidence"]})
              for cid, rank in (("c05", 2.0), ("c06", 1.0))]
    assert [d.candidate_id for d in flows.stage.select(splits)] == ["c05"]
    assert [d.candidate_id for d in flows.stage.select(splits, ["c06"])] == ["c06"]
    assert flows.stage.select(splits, held=["c05"]) == []


def test_approvals_are_read_as_promote_and_hold(tmp_path):
    (tmp_path / "approvals.json").write_text('{"promote": [{"id": "c02", "splits": ["c5_moment"]}], "hold": ["c01"]}')
    known = [decision("c01", "accept", 1.0), decision("c02", "conditional", 0.5)]
    assert flows.stage.load_approvals(tmp_path, known) == ([{"id": "c02", "splits": ["c5_moment"]}], ["c01"])
    (tmp_path / "approvals.json").write_text('{"approved": ["c01"]}')
    assert flows.stage.load_approvals(tmp_path, known) == (["c01"], [])
    assert flows.stage.load_approvals(tmp_path / "nowhere", known) == ([], [])


def test_a_crash_while_flows_rebuilds_leaves_the_last_deck_and_a_rebuild_keeps_the_approvals(tmp_path, monkeypatch):
    """Fable L9: flows/ was cleared before the deck was rebuilt, so a crash in between left no deck."""
    run_dir, _ = split_run(tmp_path, approvals={"hold": ["c02"]})
    deck = {p.relative_to(run_dir / "flows"): p.read_bytes() for p in (run_dir / "flows").rglob("*") if p.is_file()}
    assert {Path("approvals.json"), Path("slides.pdf"), Path("c01/index.html")} <= set(deck)

    def crash(path):
        raise RuntimeError("the renderer died")
    monkeypatch.setattr(flows.stage, "write_pdf", crash)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        with pytest.raises(RuntimeError, match="the renderer died"):
            flows.stage.run(ctx_for(run_dir, "luzia"))
    assert {p.relative_to(run_dir / "flows"): p.read_bytes() for p in (run_dir / "flows").rglob("*")
            if p.is_file()} == deck


def deck_files(flows_dir: Path) -> dict[Path, bytes]:
    return {p.relative_to(flows_dir): p.read_bytes() for p in flows_dir.rglob("*") if p.is_file()}


def cut_short_swap(tmp_path) -> tuple[Path, dict[Path, bytes]]:
    """A deck holding c02, then a crash between swap_in's two renames: the last deck, approvals.json with it, is in
    flows.old/, a half-built flows.tmp/ beside it, and flows/ is the empty folder the CLI makes before a stage runs."""
    run_dir, _ = split_run(tmp_path, approvals={"hold": ["c02"]})
    deck = deck_files(run_dir / "flows")
    (run_dir / "flows").rename(run_dir / "flows.old")
    (run_dir / "flows.tmp").mkdir()
    (run_dir / "flows.tmp" / "slides.html").write_text("half built")
    (run_dir / "flows").mkdir()
    return run_dir, deck


def test_a_swap_cut_short_between_its_renames_is_recovered_whole_and_its_approvals_apply(tmp_path):
    """Greptile: the next run takes flows.old/ back whole, so the person's approvals still apply."""
    run_dir, _ = cut_short_swap(tmp_path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    notes = [line.note for line in read_trace(run_dir / "trace.jsonl") if line.step == "select"]
    assert notes == ["accepted + conditional, flows/approvals.json holding c02: c01"] * 2
    assert json.loads((run_dir / "flows" / "approvals.json").read_text()) == {"hold": ["c02"]}
    assert not (run_dir / "flows.old").exists() and not (run_dir / "flows.tmp").exists()


def test_a_recovered_deck_survives_an_install_that_then_fails(tmp_path, monkeypatch):
    """Greptile: recovery moved only approvals.json out of flows.old/, so when the next install failed, swap_in's
    rollback put back a folder holding nothing but approvals.json and the last complete deck was gone."""
    run_dir, deck = cut_short_swap(tmp_path)
    replace = os.replace

    def install_fails(src, dst):
        if Path(src).name == "flows.tmp":
            raise OSError("the disk filled up")
        replace(src, dst)
    monkeypatch.setattr(judge.os, "replace", install_fails)
    with pytest.MonkeyPatch.context() as mp, pytest.raises(OSError, match="the disk filled up"):
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    assert deck_files(run_dir / "flows") == deck


def test_a_leftover_flows_old_beside_a_whole_deck_goes_and_brings_no_approvals_back(tmp_path):
    """Red team rt-37: a crash after swap_in's second rename leaves the new deck in flows/ and the one before in
    flows.old/. The person then deletes flows/approvals.json; the next run must not bring it back from flows.old/."""
    run_dir, _ = split_run(tmp_path, approvals={"hold": ["c02"]})
    shutil.copytree(run_dir / "flows", run_dir / "flows.old")
    (run_dir / "flows" / "approvals.json").unlink()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", fake_editor(run_dir))
        flows.stage.run(ctx_for(run_dir, "luzia"))
    assert "holding c02" not in select_note_last(run_dir) and not (run_dir / "flows.old").exists()
    assert not (run_dir / "flows" / "approvals.json").exists()


def select_note_last(run_dir) -> str:
    return [line.note for line in read_trace(run_dir / "trace.jsonl") if line.step == "select"][-1]


@pytest.mark.parametrize("written, error", [
    ('{"held": ["c01"]}', "expected"),
    ('{"hold": "c01"}', "expected"),  # a string would hold every id it contains
    ('[{"id": "c01"}]', "expected"),
    ('{"hold": [{"id": "c02", "splits": ["c5_moment"]}]}', "a hold entry is an id"),  # Needs your call's entry
    ('{"promote": [{"id": "c02"}]}', "a hold entry is an id"),
    ('{"promote": ["c01"], "hold": ["c03rev", "c9"]}', "has the id 'c03rev', 'c9'"),
    ('{"promote": ["c03"]}', "c03 was rejected; promote draws an accepted or split idea"),
    ('{"promote": [{"id": "c02", "splits": ["c5_moment", "c7_specific"]}], "hold": ["c02"]}',
     "c02 is both promoted and held"),
])
def test_a_malformed_approvals_file_stops_flows_before_it_clears_the_last_deck(tmp_path, written, error):
    run_dir, _ = split_run(tmp_path)
    before = sorted(p.name for p in (run_dir / "flows").iterdir())
    (run_dir / "flows" / "approvals.json").write_text(written)
    with pytest.raises(ValueError, match=re.escape(error)):
        flows.stage.run(ctx_for(run_dir, "luzia"))
    assert sorted(p.name for p in (run_dir / "flows").iterdir()) == sorted([*before, "approvals.json"])
    assert (run_dir / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")


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
    css = flows.page.blur_css(model, tmp_path / "assets")
    (tmp_path / "index.html").write_text(flows.page.with_flow_css(page, css))
    with render.open_mock(tmp_path) as (page, _):
        page.evaluate("() => window.simula.go('new:offer')")
        blurred = page.evaluate("() => [...document.querySelectorAll('[data-screen=\"new:offer\"] img')]"
                                ".map(i => getComputedStyle(i).filter)")
    assert blurred == ["blur(14px)", "none", "none"]
    safe_model = model.model_copy(update={"states": [s.model_copy(update={"content_rating": "safe"})
                                                     for s in model.states]})
    assert flows.page.blur_css(safe_model, tmp_path / "assets") == ""


def assert_contained(run_dir, reason: str):
    """c02 failed to build: c01 still gets its slides, and the deck, the review, both PDFs, and the exhibit still get
    written, with c02 listed as not built in the review."""
    assert {idea for idea, _, _ in slides(run_dir)} == {"c01"}
    deck = review_text(run_dir)
    not_built = re.search(r'<ul class="not-built">(.*?)</ul>', deck, re.S).group(1)
    assert "c02 · " in not_built and reason in html.unescape(not_built)
    assert (run_dir / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")
    assert (run_dir / "flows" / "review.pdf").read_bytes().startswith(b"%PDF")
    assert not (run_dir / "flows" / "c02").exists()
    exhibit = (run_dir / "exhibits" / "07-flows.md").read_text()
    assert "## Not built" in exhibit and f"- c02: " in exhibit and reason in exhibit
    assert "1 idea(s) passed the review but couldn't be drawn" in html.unescape(deck).replace("1 more", "1")


def test_an_idea_the_cap_turns_away_is_not_built_and_the_rest_still_make_the_deck(tmp_path, monkeypatch):
    """Greptile on #11: a refused editor call escaped the thread pool, and the stage died with no deck. Greptile on
    #18: the calls took the budget in thread order, so a lower-ranked idea could push the best one off the deck."""
    ideas = ("c01", "c02", "c03", "c04")
    run_dir = seed_run(tmp_path, "luzia", changes={"c04": {"dropped_reason": None}})
    ranked = [decision(cid, "accept", 4.0 - n) for n, cid in enumerate(ideas)]
    (run_dir / "judge" / "decisions.json").write_text(DecisionsFile(decisions=ranked).model_dump_json())
    for cid in ideas:
        (run_dir / "judge" / "verdicts" / f"{cid}_judge_1_r1.json").write_text(verdict(cid).model_dump_json())
    edit = fake_editor(run_dir)

    def held_until_the_end(**kwargs):
        if kwargs["step"] == "edit:c01":
            time.sleep(0.3)  # the best-ranked idea is the last to ask
        kwargs["budget"].reserve(1.0, step=kwargs["step"])  # as llm.call does, never settled: $3 fits three calls
        return edit(**kwargs)
    monkeypatch.setattr(llm, "call", held_until_the_end)
    flows.stage.run(dataclasses.replace(ctx_for(run_dir, "luzia"), usd_cap=3.0))
    drawn_ideas = {idea for idea, _, _ in slides(run_dir)}
    [capped] = set(ideas) - drawn_ideas
    assert capped == "c04", "the lowest-ranked idea is the one the cap turns away"
    assert not (run_dir / "flows" / capped).exists()
    deck = review_text(run_dir)
    not_built = html.unescape(re.search(r'<ul class="not-built">(.*?)</ul>', deck, re.S).group(1))
    assert f"{capped} · " in not_built and flows.stage.OVER_BUDGET in not_built
    assert "1 more idea(s) passed the review but couldn't be drawn" in html.unescape(deck)
    assert (run_dir / "flows" / "slides.pdf").read_bytes().startswith(b"%PDF")
    assert f"- {capped}: {flows.stage.OVER_BUDGET}" in (run_dir / "exhibits" / "07-flows.md").read_text()
    trace = read_trace(run_dir / "trace.jsonl")
    assert any(line.step == f"build:{capped}" and flows.stage.OVER_BUDGET in line.note for line in trace)
    assert [line.step for line in trace if line.outcome == "cap"] == [f"edit:{capped}"], \
        "the budget's own record of the refusal, which makes run_stage mark the stage partial"


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
    assert notes_of(run_dir, "c01") == ["Flow slide, step 3 (The offer, walk step 2): copy not on the screen.",
                                        "Flow slide, step 3 (The offer, walk step 2): saying no: "
                                        f"{flows.wording.NOT_WIRED}."]
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
        page = flows.page.strip_runtime((run_dir / "mock" / "index.html").read_text())
        edits = []
        for sid, markup in added.items():
            start = page.index(re.search(rf'<section[^>]*data-screen="{sid}"', page).group(0))
            section = page[start:page.index("</section>", start) + len("</section>")]
            edits.append(Edit(find=section, replace=section.removesuffix("</section>") + markup + "</section>",
                              reason="added after the screen's own controls"))
        ad = f'<section data-screen="new:ad" data-flow="c01" data-parent="{screen}" data-ad></section>'
        return Edits(edits=[*edits, Edit(find="</body>", replace=ad + "</body>", reason="ad")]), None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "call", editor)
        flows.stage.run(ctx_for(run_dir, app))
    row = next(line for line in (run_dir / "exhibits" / "07-flows.md").read_text().splitlines()
               if line.startswith("| c01"))
    saying_no = row.split("|")[6].strip()
    assert saying_no == ("ok" if starts_on_offer else
                         f"{flows.wording.NOT_WIRED}: saying no led to {screen}, not back to {first}"), row


@pytest.mark.parametrize("app", APPS)
def test_a_new_control_that_returns_to_the_first_step_goes_back(app):
    model = golden(app)
    known = model.edges[0]
    first = known.to_state
    page = (f'<a data-edge="new:offer>{first}" data-transition="replace">No</a><b data-edge="new:offer>new:ad">Yes</b>'
            f'<i data-edge="{known.id}" data-transition="{known.transition}">x</i>')
    fixed, relabeled = flows.page.decline_edges(page, first, {e.id for e in model.edges})
    assert relabeled == 1
    assert fixed == page.replace('data-transition="replace"', 'data-transition="back"')


def test_an_idea_whose_id_is_not_a_plain_name_is_never_built():
    model = golden("luzia")
    assert "isn't a plain name" in flows.stage.unbuildable(candidate(model, id="../model"))
    assert flows.stage.unbuildable(candidate(model, id="c01-rev")) is None


def test_the_copy_check_reads_words_in_any_script():
    assert flows.walk.words("「広告を見て」30分 無料!") == "広告を見て 30分 無料"
    assert flows.walk.words("Échale un vistazo, ¡gratis!") == "échale un vistazo gratis"
    assert flows.walk.words("!!!") == ""


def test_code_dropped_ideas_show_their_reason_on_the_score_page(built):
    scores = scores_text(built)
    assert "c04 A dropped idea rejected 11/11 dropped by code: duplicate of c01: same benefit" in scores
    assert "before the review" not in scores


def test_the_editor_asks_for_its_roles_max_tokens_from_the_profile(tmp_path, monkeypatch):
    app = APPS[0]
    run_dir = seed_run(tmp_path, app)
    ctx = ctx_for(run_dir, app)
    roles = flows.editor.config.roles(ctx.profile)
    monkeypatch.setattr(flows.editor.config, "roles", lambda profile: {
        **roles, "flows_editor": {**roles["flows_editor"], "max_tokens": 1234}})
    asked = []
    monkeypatch.setattr(llm, "call", lambda **kwargs: (asked.append(kwargs["max_tokens"]), (Edits(edits=[]), None))[1])
    page = flows.page.strip_runtime((run_dir / "mock" / "index.html").read_text())
    flows.editor.ask_editor(ctx, golden_idea(run_dir), golden(app), page, llm.Budget("flows", 1.0))
    assert asked == [1234], "one number: the profile's, the same one doctor --keys probes"
