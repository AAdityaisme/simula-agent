"""The merge check and the whole stage, with a recorded answer standing in for the model call.

The recorded answer for each app is its golden model's meaning, so every assertion runs on all three apps."""

import json

import pytest
from anthropic.lib._parse._transform import transform_schema
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from simula import llm
from simula.config import app_config
from simula.contracts import (Device, Edge, Element, ElementMeaning, Flow, LedgerItem, Mechanic, ModelMeaning,
                              ProductModel, QuestionDraft, Rect, State, StateMeaning, Term, TermMeaning)
from simula.runlog import read_trace
from simula.stages import Ctx, rerun_command
from simula.stages import model as stage
from tests.conftest import APPS, FIXTURES
from tests.explore_fixture import add_core_loop, build
from tests.test_schema_gate import objects

DEVICE = Device()
KEYWORDS = ["nsfw", "18+", "explicit"]


def golden(app: str) -> ProductModel:
    return ProductModel.model_validate_json((FIXTURES / "golden" / app / "product_model.json").read_text())


def recorded_answer(g: ProductModel) -> ModelMeaning:
    return ModelMeaning(
        app_name="", app_category=g.app_category,
        states=[StateMeaning(state_id=s.id, name=s.name, purpose=s.purpose, content_rating=s.content_rating)
                for s in g.states],
        elements=[ElementMeaning(element_id=e.id, role=e.role, font_guess="Inter")
                  for e in [e for s in g.states for e in s.elements if e.text][::2]],
        flows=g.flows, mechanics=g.mechanics, cross_screen_values=g.cross_screen_values, value_ledger=g.value_ledger,
        terms=[], open_questions=[QuestionDraft(question=q, start_state=g.states[0].id,
                                                look_for="the screen that answers it") for q in g.open_questions])


@pytest.fixture(params=APPS)
def app(request, tmp_path):
    explore = build(request.param, tmp_path / "explore")
    states, _, _ = stage.load_states(explore, DEVICE)
    edges, _ = stage.load_edges(explore, states)
    return request.param, states, edges, recorded_answer(golden(request.param))


def test_the_recorded_answer_passes_clean(app):
    _, states, edges, answer = app
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == [] and kept == answer


def test_unknown_ids_are_rejected(app):
    _, states, edges, answer = app
    answer.states.append(StateMeaning(state_id="s99", name="x", purpose="x", content_rating="safe"))
    answer.elements.append(ElementMeaning(element_id="s01.e999", role="x", font_guess="x"))
    answer.mechanics.append(Mechanic(id="mx", kind="ad", evidence_ids=["s01.e999"], summary="x",
                                     observed_numbers=[], status="observed"))
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert [r.split(":")[0] for r in rejected] == ["state s99", "element s01.e999", "mechanic mx"]
    assert "s99" not in {s.state_id for s in kept.states} and "mx" not in {m.id for m in kept.mechanics}


def test_a_ledger_item_that_is_not_verbatim_is_rejected(app):
    _, states, edges, answer = app
    element = next(e for s in states for e in s.elements if e.text)
    answer.value_ledger += [
        LedgerItem(id="ok", kind="meter", verbatim=element.text, evidence_ids=[element.id]),
        LedgerItem(id="bad", kind="price", verbatim=element.text + " (paraphrased)", evidence_ids=[element.id])]
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert [r.split(":")[0] for r in rejected] == ["ledger bad"]
    assert "ok" in {i.id for i in kept.value_ledger}


def test_a_ledger_line_may_not_span_text_and_label_but_whitespace_is_normalized():
    element = Element(id="s01.e01", mcp_ref="@e1", type="Button", text="Go\xa0 $\xa01.99", label="Premium", source="mcp",
                      rect_px=Rect(x=0, y=200, w=100, h=50), rect_dp=Rect(x=0, y=24, w=38, h=19), role="button",
                      asset_png=None, fg_hex=None, bg_hex=None, font_px=None, font_guess="unknown", in_mock=False,
                      repeat_group=None)
    state = State(id="s01", kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                  elements=[element], in_mock_scope=False, content_rating="safe", dynamic_regions=[],
                  blocked_reason=None)
    answer = ModelMeaning(app_name="", app_category="other", states=[StateMeaning(state_id="s01", name="x", purpose="x",
                                                                     content_rating="safe")],
                          elements=[], flows=[], mechanics=[], cross_screen_values=[], open_questions=[], terms=[],
                          value_ledger=[LedgerItem(id="span", kind="price", verbatim="$ 1.99 Premium", evidence_ids=["s01.e01"]),
                                        LedgerItem(id="label", kind="price", verbatim="Premium", evidence_ids=["s01.e01"]),
                                        LedgerItem(id="nbsp", kind="price", verbatim="Go $ 1.99", evidence_ids=["s01.e01"]),
                                        LedgerItem(id="respaced", kind="price", verbatim="Go $1.99", evidence_ids=["s01.e01"])])
    kept, rejected = stage.check_meaning(answer, [state], [])
    assert [(i.id, i.verbatim) for i in kept.value_ledger] == [("label", "Premium"), ("nbsp", "Go\xa0 $\xa01.99")]
    assert [r.split(":")[0] for r in rejected] == ["ledger span", "ledger respaced"]


def test_a_money_mechanic_must_cite_an_element(app):
    _, states, edges, answer = app
    answer.mechanics.append(Mechanic(id="m-state-only", kind="paywall", evidence_ids=[states[0].id], summary="x",
                                     observed_numbers=[], status="inferred"))
    _, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["mechanic m-state-only: a paywall must cite an element"]


def test_a_number_its_evidence_does_not_show_is_dropped_but_the_mechanic_stays(app):
    _, states, edges, answer = app
    element = next(e for s in states for e in s.elements if e.text)
    squeezed = "".join(element.text.split())
    answer.mechanics.append(Mechanic(id="m-num", kind="other", evidence_ids=[element.id], summary="x",
                                     observed_numbers=[element.text, squeezed, "$9.99"], status="observed"))
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["mechanic m-num: number '$9.99' is not shown in its evidence elements"]
    assert next(m for m in kept.mechanics if m.id == "m-num").observed_numbers == [element.text, element.text]


def test_a_number_is_observed_only_where_the_screen_shows_it_whole(app):
    _, states, edges, answer = app
    state = next(s for s in states if s.elements)

    def kept_numbers(screen_text):
        element = state.elements[0].model_copy(update={"text": screen_text, "label": ""})
        shown = [s.model_copy(update={"elements": [element, *s.elements[1:]]}) if s is state else s for s in states]
        mechanic = Mechanic(id="m-99", kind="other", evidence_ids=[element.id], summary="x",
                            observed_numbers=["99"], status="observed")
        kept, _ = stage.check_meaning(answer.model_copy(update={"mechanics": [*answer.mechanics, mechanic]}),
                                      shown, edges)
        return next(m for m in kept.mechanics if m.id == "m-99").observed_numbers

    assert kept_numbers("$199") == []
    assert kept_numbers("$99") == ["99"] and kept_numbers("99 credits") == ["99"]


def test_flows_with_missing_or_disconnected_edges_are_rejected(app):
    name, states, edges, answer = app
    a, b = next((a, b) for a in edges for b in edges if a.to_state != b.from_state)
    answer.flows += [Flow(id="missing", name="x", purpose="x", edge_ids=["s01.e01>s99"], evidence_ids=[]),
                     Flow(id="empty", name="x", purpose="x", edge_ids=[], evidence_ids=[]),
                     Flow(id="broken", name="x", purpose="x", edge_ids=[a.id, b.id], evidence_ids=[])]
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert [r.split(":")[0] for r in rejected] == ["flow missing", "flow empty", "flow broken"]
    assert {f.id for f in kept.flows} == {f.id for f in golden(name).flows}


def test_gaps_count_as_rejections_so_they_trigger_the_retry(app):
    _, states, edges, answer = app
    gappy = answer.model_copy(update={"states": answer.states[1:], "flows": [
        Flow(id="broken", name="x", purpose="x", edge_ids=["nope"], evidence_ids=[])]})
    _, rejected = stage.check_meaning(gappy, states, edges)
    assert f"state {states[0].id}: no meaning" in rejected
    assert rejected[-1] == "no core flow survived"


def test_the_keyword_floor_raises_a_rating_and_never_lowers_one(app):
    _, states, _, answer = app
    target = next(s for s in states if s.elements)
    flagged = target.elements[0].model_copy(update={"text": "NSFW only"})
    states = [s.model_copy(update={"elements": [flagged, *s.elements[1:]]}) if s is target else s for s in states]
    answer = answer.model_copy(update={"states": [
        m.model_copy(update={"content_rating": "safe" if m.state_id == target.id else "unsafe"})
        for m in answer.states]})
    rated = {s.id: s.content_rating for s in stage.apply_meaning(states, answer, KEYWORDS)}
    assert all(r == "unsafe" for r in rated.values())


def test_keywords_match_whole_words_only():
    element = Element(id="s01.e01", mcp_ref="@e1", type="TextView", text="Unexplicitly adultish", label="",
                      source="mcp", rect_px=Rect(x=0, y=200, w=100, h=50), rect_dp=Rect(x=0, y=24, w=38, h=19),
                      role="text", asset_png=None, fg_hex=None, bg_hex=None, font_px=None, font_guess="unknown",
                      in_mock=False, repeat_group=None)
    state = State(id="s01", kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                  elements=[element], in_mock_scope=False, content_rating="safe", dynamic_regions=[],
                  blocked_reason=None)
    assert stage.keyword_floor(state, KEYWORDS) == "safe"


def test_code_fields_are_never_overwritten(app):
    _, states, _, answer = app
    rated = stage.apply_meaning(states, answer, KEYWORDS)
    code_fields = ("id", "kind", "parent_id", "fingerprint", "canonical_png", "dynamic_regions", "blocked_reason")
    for before, after in zip(states, rated):
        assert all(getattr(before, f) == getattr(after, f) for f in code_fields)
        for e0, e1 in zip(before.elements, after.elements):
            assert e0.model_dump(exclude={"role", "font_guess"}) == e1.model_dump(exclude={"role", "font_guess"})


def test_code_names_tabs_and_tapped_boxes_and_fills_fonts(app):
    _, states, edges, answer = app
    named = stage.code_roles(stage.apply_meaning(states, answer, KEYWORDS), edges)
    elements = {e.id: e for s in named for e in s.elements}
    for edge in edges:
        if edge.element_id:
            role = elements[edge.element_id].role
            assert role == "tab" if edge.transition == "tab" else role != "container", edge.id
    assert all(e.font_guess != "unknown" for e in elements.values() if e.text)


def test_mock_scope_is_chosen_by_code(app):
    _, states, edges, answer = app
    rated = stage.apply_meaning(states, answer, KEYWORDS)
    scope = stage.mock_scope(rated, edges, answer)
    by_id = {s.id: s for s in rated}
    assert scope[0] == next(s.id for s in rated if s.kind == "screen")
    edge_by_id = {e.id: e for e in edges}
    flow_states = {sid for f in answer.flows for i in f.edge_ids for sid in (edge_by_id[i].from_state, edge_by_id[i].to_state)}
    money = {i.split(".")[0] for m in answer.mechanics for i in m.evidence_ids}
    reasons = flow_states | money | {by_id[s].parent_id for s in money if s in by_id}
    assert set(scope[1:]) <= reasons
    assert all(by_id[s].kind not in ("blocked", "external") for s in scope)
    for sid in scope:
        parent = by_id[sid].parent_id
        assert parent is None or (parent in scope and scope.index(parent) < scope.index(sid)), sid
    unsafe = [m.model_copy(update={"content_rating": "unsafe"}) for m in answer.states]
    rated = stage.apply_meaning(states, answer.model_copy(update={"states": unsafe}), KEYWORDS)
    assert set(stage.mock_scope(rated, edges, answer)) <= flow_states, "with every state unsafe, only flow states stay"


def test_a_modal_in_scope_brings_its_parent_first():
    def state(sid, kind="screen", parent=None):
        return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[], in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)
    states = [state("s01"), state("s02"), state("s03", "modal", "s02")]
    meaning = ModelMeaning(app_name="", app_category="other", states=[], elements=[], flows=[], cross_screen_values=[],
                           value_ledger=[], open_questions=[], terms=[],
                           mechanics=[Mechanic(id="m1", kind="ad", evidence_ids=["s03"], summary="x",
                                               observed_numbers=[], status="observed")])
    assert stage.mock_scope(states, [], meaning) == ["s01", "s02", "s03"]
    unsafe_parent = [states[0], states[1].model_copy(update={"content_rating": "unsafe"}), states[2]]
    assert stage.mock_scope(unsafe_parent, [], meaning) == ["s01"]
    blocked_parent = [states[0], states[1].model_copy(update={"kind": "blocked"}), states[2]]
    assert stage.mock_scope(blocked_parent, [], meaning) == ["s01"]


def test_a_flow_dialog_keeps_its_parent_even_when_the_parent_is_unsafe_and_off_the_flow():
    """Whether the unsafe parent's content shows under the dialog is the renderer's and the blur's job."""
    def state(sid, kind="screen", parent=None, rating="safe"):
        return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[], in_mock_scope=False, content_rating=rating, dynamic_regions=[], blocked_reason=None)
    states = [state("s01"), state("s02", rating="unsafe"), state("s03", "modal", "s02")]
    edges = [Edge(id="s03.e01>s01", from_state="s03", to_state="s01", element_id=None, action="tap",
                  transition="back", change_summary="")]
    meaning = ModelMeaning(app_name="", app_category="other", states=[], elements=[], cross_screen_values=[],
                           value_ledger=[], open_questions=[], terms=[], mechanics=[],
                           flows=[Flow(id="f01", name="x", purpose="x", edge_ids=["s03.e01>s01"], evidence_ids=[])])
    assert stage.mock_scope(states, edges, meaning) == ["s01", "s02", "s03"]


def test_a_sheet_over_a_modal_brings_the_whole_stack_parent_first():
    def state(sid, kind="screen", parent=None):
        return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[], in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)
    states = [state("s01"), state("s02"), state("s03", "modal", "s02"), state("s04", "sheet", "s03")]
    meaning = ModelMeaning(app_name="", app_category="other", states=[], elements=[], flows=[], cross_screen_values=[],
                           value_ledger=[], open_questions=[], terms=[],
                           mechanics=[Mechanic(id="m1", kind="paywall", evidence_ids=["s04"], summary="x",
                                               observed_numbers=[], status="observed")])
    assert stage.mock_scope(states, [], meaning) == ["s01", "s02", "s03", "s04"]
    unsafe_screen = [states[0], states[1].model_copy(update={"content_rating": "unsafe"}), *states[2:]]
    assert stage.mock_scope(unsafe_screen, [], meaning) == ["s01"]


def test_a_bullet_folded_out_of_a_kept_paywall_list_gets_its_own_ledger_item():
    def text(n, words, x, y, group="s01.r1"):
        return Element(id=f"s01.e{n:02d}", mcp_ref=None, type="TextView", text=words, label="", source="mcp",
                       rect_px=Rect(x=x, y=y, w=745, h=52), rect_dp=Rect(x=0, y=0, w=0, h=0), role="text",
                       asset_png=None, fg_hex=None, bg_hex=None, font_px=None, font_guess="unknown", in_mock=False,
                       repeat_group=group)
    elements = [text(1, "More memory", 213, 1447), text(2, "Faster replies", 213, 1511),
                text(3, "A badge by your name", 213, 1692), text(4, "Same size, other column", 40, 2000),
                text(5, "", 213, 1760), text(6, "Another list", 213, 900, "s01.r2")]
    states = [State(id="s01", kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                    elements=elements, in_mock_scope=False, content_rating="safe", dynamic_regions=[],
                    blocked_reason=None)]
    ledger = [LedgerItem(id="pb1", kind="paywall_bullet", verbatim="More memory", evidence_ids=["s01.e01"]),
              LedgerItem(id="v2", kind="paywall_bullet", verbatim="Faster replies", evidence_ids=["s01.e02"])]
    assert stage.folded_bullets(ledger, states) == [
        LedgerItem(id="pb2", kind="paywall_bullet", verbatim="A badge by your name", evidence_ids=["s01.e03"])]
    assert stage.folded_bullets([i.model_copy(update={"kind": "meter"}) for i in ledger], states) == [], \
        "only a paywall bullet's list is completed"


def test_a_paywall_captured_twice_has_each_bullet_quoted_once():
    def capture(sid):
        def text(n, words, y):
            return Element(id=f"{sid}.e{n:02d}", mcp_ref=None, type="TextView", text=words, label="", source="mcp",
                           rect_px=Rect(x=213, y=y, w=745, h=52), rect_dp=Rect(x=0, y=0, w=0, h=0), role="text",
                           asset_png=None, fg_hex=None, bg_hex=None, font_px=None, font_guess="unknown",
                           in_mock=False, repeat_group=f"{sid}.r1")
        return State(id=sid, kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[text(1, "More memory", 1447), text(2, "Faster replies", 1511),
                               text(3, "A badge by your name", 1692)],
                     in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)
    ledger = [LedgerItem(id="v1", kind="paywall_bullet", verbatim="More memory", evidence_ids=["s01.e01"]),
              LedgerItem(id="v2", kind="paywall_bullet", verbatim="Faster  replies", evidence_ids=["s02.e02"])]
    assert stage.folded_bullets(ledger, [capture("s01"), capture("s02")]) == [
        LedgerItem(id="pb1", kind="paywall_bullet", verbatim="A badge by your name", evidence_ids=["s01.e03"])]
    elsewhere = LedgerItem(id="v3", kind="actor", verbatim="A badge by your name", evidence_ids=["s05.e01"])
    assert [i.evidence_ids for i in stage.folded_bullets([*ledger, elsewhere], [capture("s01"), capture("s02")])] == \
        [["s01.e03"]], "the same words quoted as something else still leave the benefit without a paywall bullet"
    as_limit = [ledger[0], LedgerItem(id="v2", kind="limit", verbatim="Faster replies", evidence_ids=["s01.e02"]),
                LedgerItem(id="v3", kind="paywall_bullet", verbatim="More memory", evidence_ids=["s02.e01"])]
    assert [i.evidence_ids for i in stage.folded_bullets(as_limit, [capture("s01"), capture("s02")])] == \
        [["s01.e03"]], "a bullet the model filed as another kind is quoted on neither capture"


def test_the_model_may_not_write_measured_experience(app):
    _, states, edges, answer = app
    element = next(e for s in states for e in s.elements if e.text)
    answer.value_ledger.append(LedgerItem(id="fake", kind="experience", verbatim=element.text,
                                          evidence_ids=[element.id]))
    _, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["ledger fake: experience items are measured by code, not written by the model"]


def test_evidence_may_cite_an_edge(app):
    _, states, edges, answer = app
    answer.mechanics.append(Mechanic(id="m-edge", kind="ad", evidence_ids=[edges[0].id], summary="x",
                                     observed_numbers=[], status="observed"))
    _, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == []


def test_a_term_keeps_its_meaning_only_when_a_cited_element_carries_it_and_says_more_in_words(app):
    _, states, edges, answer = app
    carrier = next(e for s in states for e in s.elements if len(stage.WORD.findall(e.text)) >= 2)
    word = stage.WORD.findall(carrier.text)[-1]
    beside = next(e for s in states if carrier in s.elements for e in s.elements
                  if e.text and word.lower() not in (e.text + e.label).lower())
    answer.mechanics.append(Mechanic(id="m-term", kind="other", evidence_ids=[carrier.id], summary=f"Uses {word}.",
                                     observed_numbers=[], status="observed"))
    answer.terms += [TermMeaning(term=word.upper(), meaning="a plain meaning", defined_by=[carrier.id, "s01.e999"],
                                 used_in=["m-term"], everyday=False),
                     TermMeaning(term=word, meaning="a guess", defined_by=[beside.id], used_in=["m-term"],
                                 everyday=False),
                     TermMeaning(term="Zorblax", meaning="x", defined_by=[carrier.id], used_in=["m-term"],
                                 everyday=False)]
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["term 'Zorblax': used_in ['m-term'] names no kept mechanic or ledger line that uses it"]
    observed, unobserved = stage.resolve_terms(kept, states, edges, set())
    assert (observed.observed, observed.meaning, observed.defined_by) == (True, "a plain meaning", [carrier.id])
    assert (unobserved.observed, unobserved.meaning, unobserved.defined_by) == (False, stage.NOT_OBSERVED, []), \
        "text beside the term on its screen, with no cited element that carries it, is a guess"


def test_a_line_uses_a_term_only_as_a_whole_word(app):
    _, states, edges, answer = app
    answer.mechanics.append(Mechanic(id="m-term", kind="other", evidence_ids=[states[0].id],
                                     summary="Protect your streak.", observed_numbers=[], status="observed"))
    answer.terms.append(TermMeaning(term="Pro", meaning="a guess", defined_by=[], used_in=["m-term"], everyday=False))
    _, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["term 'Pro': used_in ['m-term'] names no kept mechanic or ledger line that uses it"]


def test_an_element_the_ledger_also_quotes_defines_its_term_by_its_own_words(app):
    _, states, edges, answer = app
    bullet = next(e for s in states for e in s.elements if len(set(stage.WORD.findall(e.text.lower()))) >= 2)
    word = stage.WORD.findall(bullet.text)[-1]
    answer.value_ledger.append(LedgerItem(id="l-term", kind="paywall_bullet", verbatim=bullet.text,
                                          evidence_ids=[bullet.id]))
    answer.terms.append(TermMeaning(term=word, meaning="a plain meaning", defined_by=[bullet.id], used_in=["l-term"],
                                    everyday=False))
    kept, rejected = stage.check_meaning(answer, states, edges)
    (term,) = stage.resolve_terms(kept, states, edges, set())
    assert rejected == [] and (term.observed, term.meaning, term.defined_by) == (True, "a plain meaning", [bullet.id])


# What a person reads off the screenshots of PR 2's real runs: does any screen say what the term means?
# tests/fixtures/terms/<app>.json holds those runs' saved states, the labels their explore models wrote, and the
# model's checked answer.
OBSERVED = {
    "janitorai": {
        "Limitless": False,  # a bare tag on character cards (s01.e48, s01.e52)
        "Janitor+": True,
        "tokens": False,  # counts on cards (s01.e49 "1.8k tokens"); nothing says what a token is
    },
    "luzia": {
        "Luzia+": True,
        "Toki": True,
        "Weekly": False,  # s02 plan card: the name (s02.e09) and a price (s02.e10); a price isn't a meaning
        "Monthly": False,  # the same: "Monthly" (s02.e12), "$ 4.99", "Most popular"
        "Annual": False,  # the same: "Annual" (s02.e16), "$ 39.99"
    },
    "aol": {
        "Taboola": True,
        "Inbox": False,  # the bare tab name (s01.e72); the sign-in wall it opens (s03) doesn't say what it holds
    },
}

# The cited elements that surely say what a shown term means; each must be in its defined_by.
EXPLAINS = {
    "Janitor+": ["s02.e04"],  # s02 modal: "Longer memory, priority routing, ... are now available in janitor+."
    "Luzia+": [f"s02.e0{n}" for n in range(2, 8)],  # the six benefits under "Unlock Luzia+" on the s02 paywall
    "Toki": ["s01.e26", "s01.e27", "s04.e04"],  # "Meet Toki, your virtual pet!", "...take care of your own pet"
    "Taboola": ["s01.e44", "s02.e13"],  # the sponsored cards' labels "... in Taboola advertising section · Sponsored"
}


# Terms whose plain-English word is what they mean in the app, as a person reads them. These runs predate the label,
# so their fixtures carry it by hand. AOL's Inbox is its mail inbox (AOL Help, "Overview of the updated AOL app
# experience for Android").
EVERYDAY = {"janitorai": set(), "luzia": {"Weekly", "Monthly", "Annual"}, "aol": {"Inbox"}}


def real_terms(app: str) -> tuple[list[State], ModelMeaning, list[Edge], set[str]]:
    fixture = json.loads((FIXTURES / "terms" / f"{app}.json").read_text())
    return ([State.model_validate(s) for s in fixture["states"]], ModelMeaning.model_validate(fixture["meaning"]),
            [Edge.model_validate(g) for g in fixture["edges"]], set(fixture["model_labels"]))


@pytest.mark.parametrize("name", APPS)
def test_a_real_term_is_observed_as_a_person_reads_its_screens(name):
    states, meaning, edges, model_labels = real_terms(name)
    terms = stage.resolve_terms(meaning, states, edges, model_labels)
    assert {t.term: t.observed for t in terms} == OBSERVED[name]
    for drafted, term in zip(meaning.terms, terms):
        assert set(term.defined_by) <= set(drafted.defined_by), "only the model's own citations can count"
        if term.observed:
            assert term.meaning == drafted.meaning and set(EXPLAINS[term.term]) <= set(term.defined_by), term.term
        else:
            assert (term.meaning, term.defined_by) == (stage.NOT_OBSERVED, [])


@pytest.mark.parametrize("name", APPS)
def test_the_everyday_label_is_kept_as_written_and_never_changes_what_was_observed(name):
    states, meaning, edges, model_labels = real_terms(name)
    assert {t.term for t in stage.resolve_terms(meaning, states, edges, model_labels) if t.everyday} == EVERYDAY[name]
    flipped = meaning.model_copy(update={"terms": [t.model_copy(update={"everyday": not t.everyday})
                                                   for t in meaning.terms]})
    terms = stage.resolve_terms(flipped, states, edges, model_labels)
    assert {t.term for t in terms if not t.everyday} == EVERYDAY[name]
    assert {t.term: t.observed for t in terms} == OBSERVED[name]


@pytest.mark.parametrize("to_schema", [transform_schema, to_strict_json_schema], ids=["anthropic", "openai"])
def test_the_meaning_model_must_label_every_term(to_schema):
    """A default made `everyday` optional in the Anthropic schema, so constrained decoding could skip it and the term
    would silently count as an app name."""
    optional = [(o.get("title"), k) for o in objects(to_schema(ModelMeaning)) for k in o.get("properties", {})
                if k not in o.get("required", [])]
    assert optional == []


def test_a_product_model_stored_before_the_label_existed_still_parses():
    stored = {"term": "Free", "meaning": stage.NOT_OBSERVED, "defined_by": [], "used_in": ["m2"], "observed": False}
    assert Term.model_validate(stored).everyday is False
    with pytest.raises(ValidationError):
        TermMeaning.model_validate({k: v for k, v in stored.items() if k != "observed"})


# The approved JanitorAI model-stage rerun (tests/fixtures/terms/janitorai-2026-09-29.json), as a person reads its
# screens. Every term a screen explains sits on an element the model also quoted in the ledger. The model cited
# nothing for the six other terms, so any rule leaves them unobserved.
RERUN_OBSERVED = {
    "Janitor Plus": True,  # s13 never names it, but explore recorded "Upgrade to Janitor Plus" (s06.e44) opening s13
    "Free": False,
    "context": True,
    "Priority routing": True,
    "swipes": False,
    "frontier models": False,
    "tokens": False,
    "Hidden Gems": True,
    "Golden checkmark": True,
    "Proxy": False,
    "chats": False,
}
RERUN_EXPLAINS = {
    "Janitor Plus": "s13.e10",  # the paywall's bullets, e.g. "Priority routing for faster replies"
    "context": "s13.e02",  # "Keep more of the story in context, get faster replies, and unlock smarter swipes."
    "Priority routing": "s13.e10",  # "Priority routing for faster replies"
    "Hidden Gems": "s01.e15",  # "Hidden Gems show characters from smaller creators with engaging conversations, ..."
    "Golden checkmark": "s13.e12",  # "Golden checkmark next to your username"
}


def test_a_term_its_screen_explains_is_observed_although_the_ledger_quotes_that_element():
    states, meaning, edges, model_labels = real_terms("janitorai-2026-09-29")
    quoted = {i for item in meaning.value_ledger for i in item.evidence_ids}
    terms = stage.resolve_terms(meaning, states, edges, model_labels)
    assert {t.term: t.observed for t in terms} == RERUN_OBSERVED
    for t in terms:
        if t.observed:
            assert RERUN_EXPLAINS[t.term] in set(t.defined_by) & quoted, t.term
    assert {t.term: t.anchor_taps for t in terms if t.anchor_taps} == {"Janitor Plus": ["s06.e44>s13"]}


PAYWALL_BULLETS = ["s13.e02", "s13.e09", "s13.e10", "s13.e11"]  # what the model cited for Janitor Plus


def janitor_plus(edges: list[Edge], states: list[State], meaning: ModelMeaning, model_labels: set[str]) -> Term:
    (term,) = [t for t in stage.resolve_terms(meaning, states, edges, model_labels) if t.term == "Janitor Plus"]
    return term


def test_a_recorded_tap_on_an_anchor_carries_its_term_to_the_screen_it_opened():
    states, meaning, edges, model_labels = real_terms("janitorai-2026-09-29")
    assert "s06.e44>s13" in {g.id for g in edges}
    term = janitor_plus(edges, states, meaning, model_labels)
    assert (term.observed, term.defined_by, term.anchor_taps) == (True, PAYWALL_BULLETS, ["s06.e44>s13"])
    assert not janitor_plus([], states, meaning, model_labels).observed, "with no recorded tap, s13 never names it"
    md = stage.render_md(golden("janitorai").model_copy(update={"terms": [term]}))
    line = f"- **Janitor Plus**: {term.meaning} · defined by {', '.join(PAYWALL_BULLETS)} · through tap s06.e44>s13"
    assert line in md


def bare_name(states: list[State]) -> list[State]:
    return [s.model_copy(update={"elements": [e.model_copy(update={"label": "Janitor Plus"}) if e.id == "s06.e44" else e
                                              for e in s.elements]}) for s in states]


@pytest.mark.parametrize("change", ["a bare name", "a label the model wrote", "not a tap"])
def test_only_a_tap_on_app_text_that_says_more_than_the_term_carries_it_to_the_next_screen(change):
    states, meaning, edges, model_labels = real_terms("janitorai-2026-09-29")
    if change == "a bare name":
        states = bare_name(states)
    elif change == "a label the model wrote":
        model_labels = model_labels | {"s06.e44"}
    else:
        edges = [g.model_copy(update={"action": "swipe"}) if g.id == "s06.e44>s13" else g for g in edges]
    term = janitor_plus(edges, states, meaning, model_labels)
    assert (term.observed, term.defined_by) == (False, [])


def test_a_tap_that_changed_its_own_screen_opened_nothing():
    """Red team PR13 @408cd35 #4: an in-place tap on the uncited anchor would make its own screen's cited neighbors
    count, as if it had opened that screen."""
    states, meaning, edges, model_labels = real_terms("janitorai-2026-09-29")
    drafted = next(t for t in meaning.terms if t.term == "Janitor Plus")
    meaning.terms[:] = [drafted.model_copy(update={"defined_by": ["s06.e42"]})]  # "Billing", beside the anchor
    in_place = Edge(id="s06.e44>s06", from_state="s06", to_state="s06", element_id="s06.e44", action="tap",
                    transition="unknown", change_summary="+'Plan selected'")
    (term,) = stage.resolve_terms(meaning, states, [*edges, in_place], model_labels)
    assert (term.observed, term.defined_by, term.anchor_taps) == (False, [], [])


@pytest.mark.parametrize("cited, kept", [(["s15.e04"], []), (["s03.e08"], []), (["s03.e05", "s03.e08"], ["s03.e05"])],
                         ids=["on-the-screen-a-tap-opened", "on-its-own-screen", "beside-a-cited-anchor"])
def test_a_count_never_defines_its_term_wherever_it_sits(cited, kept):
    """Red team PR13 @408cd35 #1: explore tapped the row "Kang Jun-Seo (Idol x Idol), 08:52, 7 chats" (s03.e05, an
    anchor for "chats"), which opened the sheet s15. "7 chats" there (s15.e04) carries the term but is a count, so
    it can't define it, just like the same count on the list (s03.e08). Red team PR13 @f9bf25f #3: when the model
    also cites that row, s03 is explained (the documented in-passing limit), and the count beside it still doesn't
    count."""
    states, meaning, edges, model_labels = real_terms("janitorai-2026-09-29")
    assert "s03.e05>s15" in {g.id for g in edges}
    drafted = next(t for t in meaning.terms if t.term == "chats")
    meaning.terms[:] = [drafted.model_copy(update={"defined_by": cited})]
    (term,) = stage.resolve_terms(meaning, states, edges, model_labels)
    assert (term.observed, term.defined_by, term.anchor_taps) == (bool(kept), kept, [])


def test_a_tap_from_any_element_that_names_the_term_carries_it_to_the_screen_it_opened():
    """The documented known limit, pinned: code can't tell whether the opened screen is about the term, so a tap on
    an element that names it in passing still carries it. On the five saved real runs every such tap opens a screen
    about its term: a plan's paywall, a pet's page, a character's chat list."""
    states, meaning, edges, model_labels = real_terms("janitorai-2026-09-29")
    drafted = next(t for t in meaning.terms if t.term == "Hidden Gems")
    meaning.terms[:] = [drafted.model_copy(update={"defined_by": ["s13.e10"]})]
    unrelated = Edge(id="s01.e15>s13", from_state="s01", to_state="s13", element_id="s01.e15", action="tap",
                     transition="push", change_summary="")
    assert not stage.resolve_terms(meaning, states, edges, model_labels)[0].observed
    (term,) = stage.resolve_terms(meaning, states, [*edges, unrelated], model_labels)
    assert (term.observed, term.defined_by) == (True, ["s13.e10"])


def test_a_sentence_that_only_uses_a_term_counts_if_the_model_cites_it():
    """The documented known limit, pinned so that closing or widening it shows up here: s13.e11 uses "swipes"
    without saying what one is."""
    states, meaning, edges, model_labels = real_terms("janitorai-2026-09-29")
    drafted = next(t for t in meaning.terms if t.term == "swipes")
    meaning.terms[:] = [drafted.model_copy(update={"defined_by": ["s13.e11"]})]
    (term,) = stage.resolve_terms(meaning, states, edges, model_labels)
    assert (term.observed, term.defined_by) == (True, ["s13.e11"])


def test_product_model_md_and_the_exhibit_show_which_unobserved_terms_are_everyday_words():
    states, meaning, edges, model_labels = real_terms("luzia")
    model = golden("luzia").model_copy(update={"terms": stage.resolve_terms(meaning, states, edges, model_labels)})
    md = stage.render_md(model)
    assert "- **Weekly** (everyday word, never flagged): meaning not observed" in md
    assert "- **Luzia+**: " in md and "**Luzia+** (everyday" not in md
    assert ("app terms: 5, meaning not observed for: Weekly (everyday word, never flagged), Monthly (everyday word, "
            "never flagged), Annual (everyday word, never flagged)") in stage.exhibit(model, [], [], "")


# Guesses built from PR 2's real screens (red team D): the model's own meaning and used_in, with these citations.
GUESSES = [
    ("janitorai", "Limitless", ["s01.e48", "s01.e49"], "the bare tag and the count beside it on its card"),
    ("janitorai", "Limitless", ["s01.e15"], "a blurb on the same screen that never names it"),
    ("janitorai", "tokens", ["s01.e48"], "a different bare tag"),
    ("janitorai", "tokens", ["s01.e34"], "a chat count"),
    ("aol", "Inbox", ["s01.e72", "s01.e73"], "the bare tab name and the tab beside it"),
    ("aol", "Inbox", ["s01.e36"], "a headline on the same screen"),
    ("luzia", "Toki", ["s01.e03"], "another banner on the same screen"),
    ("luzia", "Luzia+", ["s01.e02"], "an icon the explore pass named 'Luzia+ upsell badge'; s01 never shows Luzia+"),
    ("luzia", "Luzia+", ["s01.e02", "s01.e26"], "that icon and a banner beside it"),
]


@pytest.mark.parametrize("name, term, cited, why", GUESSES, ids=[f"{g[1]}<-{'+'.join(g[2])}" for g in GUESSES])
def test_a_guess_cited_next_to_a_term_stays_unobserved(name, term, cited, why):
    states, meaning, edges, model_labels = real_terms(name)
    drafted = next(t for t in meaning.terms if t.term == term)
    meaning.terms[:] = [drafted.model_copy(update={"defined_by": cited})]
    (resolved,) = stage.resolve_terms(meaning, states, edges, model_labels)
    assert (resolved.observed, resolved.defined_by) == (False, []), why


# However the ledger quotes JanitorAI's card meters, "1.8k tokens" (s01.e49) and "2.2k tokens" (s01.e61) are counts of
# tokens, not what a token is (red team D, finding 3). The used_in the model wrote never lists these meter lines.
COUNTS = [
    ("s01.e49", {"vl5": "1.8k tokens", "vl6": "2.2k tokens"}, "each card is quoted whole"),
    ("s01.e49", {"vl5": "1.8k", "vl6": "2.2k"}, "only the numbers are quoted"),
    ("s01.e49", {}, "no meter line"),
    ("s01.e61", {"vl5": "1.8k tokens"}, "only the other card is quoted"),
]


@pytest.mark.parametrize("cited, quotes, why", COUNTS, ids=[c[2] for c in COUNTS])
def test_a_count_of_a_term_never_defines_it(cited, quotes, why):
    states, meaning, edges, model_labels = real_terms("janitorai")
    meters = {i.id: i for i in meaning.value_ledger if i.id in ("vl5", "vl6")}
    meaning.value_ledger[:] = ([i for i in meaning.value_ledger if i.id not in meters]
                               + [meters[i].model_copy(update={"verbatim": q}) for i, q in quotes.items()])
    drafted = next(t for t in meaning.terms if t.term == "tokens")
    meaning.terms[:] = [drafted.model_copy(update={"defined_by": [cited]})]
    (resolved,) = stage.resolve_terms(meaning, states, edges, model_labels)
    assert (resolved.observed, resolved.defined_by) == (False, []), why


@pytest.mark.parametrize("term, words", [("トークン", "トークンを使うと、キャラクターが長い会話を覚えます"),
                                         ("토큰", "토큰을 쓰면 캐릭터가 긴 대화를 기억합니다")], ids=["japanese", "korean"])
def test_a_term_in_a_script_without_spaces_can_be_observed(term, words):
    states, meaning, edges, model_labels = real_terms("janitorai")
    states = [s.model_copy(update={"elements": [e.model_copy(update={"text": words}) if e.id == "s02.e04" else e
                                                for e in s.elements]}) for s in states]
    meaning.terms[:] = [TermMeaning(term=term, meaning="a unit characters spend", defined_by=["s02.e04"], used_in=[],
                                    everyday=False)]
    (resolved,) = stage.resolve_terms(meaning, states, edges, model_labels)
    assert (resolved.observed, resolved.defined_by) == (True, ["s02.e04"])


@pytest.mark.parametrize("term, words", [("टोकन", "टोकन से मैसेज भेजें"), ("টোকেন", "টোকেন দিয়ে মেসেজ পাঠান")],
                         ids=["hindi", "bengali"])
def test_a_term_in_a_script_with_vowel_signs_can_be_observed(term, words):
    """Each sentence says "send messages with tokens". Its vowel signs are combining marks, which regex \\w leaves out,
    so without counting them every word falls apart into one-letter runs."""
    states, meaning, edges, model_labels = real_terms("janitorai")
    states = [s.model_copy(update={"elements": [e.model_copy(update={"text": words}) if e.id == "s02.e04" else e
                                                for e in s.elements]}) for s in states]
    meaning.terms[:] = [TermMeaning(term=term, meaning="a unit characters spend", defined_by=["s02.e04"], used_in=[],
                                    everyday=False)]
    (resolved,) = stage.resolve_terms(meaning, states, edges, model_labels)
    assert (resolved.observed, resolved.defined_by) == (True, ["s02.e04"])


@pytest.mark.parametrize("term, words", [("Energy", "⚡️Energy refills every 4 hours"), ("Gems", "💎️Gems: 120 left"),
                                         ("Premium", "⭐️Premium members skip the line"),
                                         ("Tags", "#️⃣Tags you follow: 12")],
                         ids=["energy", "gems", "premium", "keycap"])
def test_a_term_written_right_after_an_emoji_is_kept_and_observed(term, words):
    """Red team PR13 @408cd35 #3 and @f9bf25f #1: an emoji's presentation selector (U+FE0F) and a keycap (U+20E3) only
    draw a symbol, so they don't join it to the word after it. Before, the merge check dropped such a term."""
    states, meaning, edges, model_labels = real_terms("janitorai")
    states = [s.model_copy(update={"elements": [e.model_copy(update={"text": words}) if e.id == "s02.e04" else e
                                                for e in s.elements]}) for s in states]
    meaning.value_ledger.append(LedgerItem(id="vl-term", kind="meter", verbatim=words, evidence_ids=["s02.e04"]))
    meaning.terms[:] = [TermMeaning(term=term, meaning="a unit characters spend", defined_by=["s02.e04"],
                                    used_in=["vl-term"], everyday=False)]
    kept, rejected = stage.check_meaning(meaning, states, edges)
    assert not [r for r in rejected if r.startswith("term") or "vl-term" in r]
    (resolved,) = stage.resolve_terms(kept, states, edges, model_labels)
    assert (resolved.observed, resolved.defined_by) == (True, ["s02.e04"])


def test_a_term_shows_only_as_a_whole_word():
    """s01.e48 "Limitless" would explain "Limit" if part of a word counted: "less" is left once "Limit" is cut."""
    states, meaning, edges, model_labels = real_terms("janitorai")
    meaning.terms[:] = [TermMeaning(term="Limit", meaning="a guess", defined_by=["s01.e48"], used_in=[],
                                    everyday=False)]
    (resolved,) = stage.resolve_terms(meaning, states, edges, model_labels)
    assert (resolved.observed, resolved.defined_by) == (False, [])


def test_questions_need_a_real_start_state_and_are_capped(app):
    _, states, edges, answer = app
    answer.open_questions[:] = [QuestionDraft(question=f"q{n}?", start_state=states[0].id, look_for="x")
                                for n in range(7)]
    answer.open_questions.insert(1, QuestionDraft(question="nowhere?", start_state="s99", look_for="x"))
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["question 'nowhere?': start_state 's99' is not a recorded state"]
    assert [q.question for q in kept.open_questions] == ["q0?", "q1?", "q2?", "q3?", "q4?"]


def test_scope_is_root_then_money_screens_then_other_mechanics_then_the_core_flow_and_nothing_else():
    """20 eligible states: 6 tabs, a 4-state core flow, a paywall over the root, and filler (depth-1 screens,
    one with an entitlement mechanic)."""
    def state(sid, kind="screen", parent=None):
        return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[], in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)

    def edge(a, b, transition="push"):
        return Edge(id=f"{a}.tap>{b}", from_state=a, to_state=b, element_id=None, action="tap",
                    transition=transition, change_summary="")
    ids = [f"s{n:02d}" for n in range(1, 21)]
    tabs, flow, paywall, filler = ids[1:7], ids[7:11], ids[11], ids[12:]
    states = [state(sid, "modal", "s01") if sid == paywall else state(sid) for sid in ids]
    hops = list(zip(["s01", *flow[:-1]], flow))
    edges = ([edge("s01", t, "tab") for t in tabs] + [edge(a, b) for a, b in hops] + [edge("s01", paywall, "modal")]
             + [edge("s01", f) for f in filler])
    meaning = ModelMeaning(
        app_name="", app_category="chat", states=[], elements=[], cross_screen_values=[], value_ledger=[],
        open_questions=[], terms=[],
        flows=[Flow(id="f1", name="core", purpose="x", edge_ids=[f"{a}.tap>{b}" for a, b in hops], evidence_ids=[])],
        mechanics=[Mechanic(id="m1", kind="paywall", evidence_ids=[paywall], summary="x", observed_numbers=[],
                            status="observed"),
                   Mechanic(id="m2", kind="entitlement", evidence_ids=[filler[0]], summary="x", observed_numbers=[],
                            status="observed")])
    order = stage.mock_scope(states, edges, meaning)
    assert order == ["s01", paywall, filler[0], *flow]
    assert not set(order) & set(tabs + filler[1:])

    unsafe = {flow[1], filler[0], tabs[0]}
    rated = [s.model_copy(update={"content_rating": "unsafe"}) if s.id in unsafe else s for s in states]
    meaning.mechanics.append(Mechanic(id="m3", kind="other", evidence_ids=[tabs[0]], summary="x",
                                      observed_numbers=[], status="observed"))
    order = stage.mock_scope(rated, edges, meaning)
    assert flow[1] in order, "an unsafe state on a core flow stays, so the flow isn't broken"
    assert filler[0] not in order and tabs[0] not in order, "an unsafe state holding only a mechanic stays out"


# ---------- the whole stage ----------

def make_ctx(name: str, tmp_path) -> Ctx:
    run_dir = tmp_path / "run"
    build(name, run_dir / "explore")
    return Ctx(app=app_config(name), run_dir=run_dir, profile="dev", no_cache=False, replay=False, usd_cap=None,
               allow_fixtures=True)


def fake_calls(monkeypatch, answers):
    calls = []

    def call(**kw):
        calls.append(kw)
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer, None
    monkeypatch.setattr(llm, "call", call)
    return calls


def merge_notes(ctx):
    return [(t.step, t.note) for t in read_trace(ctx.run_dir / "trace.jsonl") if t.step.startswith("merge")]


@pytest.mark.parametrize("name", APPS)
def test_the_stage_writes_a_valid_model(name, tmp_path, monkeypatch):
    clean = recorded_answer(golden(name)).model_copy(update={"app_name": "Shown Name"})
    calls = fake_calls(monkeypatch, [clean, clean])
    ctx = make_ctx(name, tmp_path)
    stage.run(ctx)
    out = ctx.run_dir / "model"
    model = ProductModel.model_validate_json((out / "product_model.json").read_text())
    assert calls[0]["schema"] is ModelMeaning and calls[0]["max_tokens"] == 64000
    assert (model.app, model.app_name) == (name, "Shown Name")
    assert model.device == golden(name).device
    assert all((out / s.canonical_png).exists() for s in model.states)
    assert all((out / e.asset_png).exists() for s in model.states for e in s.elements if e.asset_png)
    tapped = {e.element_id for e in model.edges if e.element_id}
    scope = {s.id for s in model.states if s.in_mock_scope}
    assert set(model.mock_order) == scope and len(model.mock_order) == len(scope)
    assert model.mock_order[0] == next(s.id for s in model.states if s.kind == "screen")
    assert all(e.in_mock for s in model.states for e in s.elements if e.id in tapped and s.id in scope)
    assert model.flows
    md = (out / "product_model.md").read_text()
    assert md.startswith("# Product model: Shown Name ") and md.count("```mermaid") == 1 + len(model.flows)
    assert model.open_questions == [q.question for q in model.questions] and not any(q.answered for q in model.questions)
    assert [q.id for q in model.questions] == [f"q{n}" for n in range(1, len(model.questions) + 1)]
    assert (ctx.run_dir / "exhibits" / "02-model.md").exists()
    images = [p for m in calls[0]["messages"] for p in m["content"] if p["type"] == "image"]
    assert 1 <= len(images) <= stage.MAX_IMAGES


@pytest.mark.parametrize("cited", [["s02.e03"], ["s03.e05", "s02.e03"]], ids=["bullet", "anchor-and-bullet"])
def test_a_term_observed_through_a_tap_names_that_tap_in_the_trace_and_the_model(cited, tmp_path, monkeypatch):
    """Luzia's golden: "Upgrade to Luzia+" (s03.e05) opens the paywall s02, whose bullet "Advanced reasoning mode"
    (s02.e03) the model cites; nothing cited on s02 names the plan. When the model also cites that anchor, it sits on
    s03, a screen the tap didn't open (red team PR13 @f9bf25f #2), so the note doesn't say which screen each is on."""
    answer = recorded_answer(golden("luzia"))
    answer.terms.append(TermMeaning(term="Luzia+", meaning="The paid plan.", defined_by=cited, used_in=["m01"],
                                    everyday=False))
    fake_calls(monkeypatch, [answer, answer])
    ctx = make_ctx("luzia", tmp_path)
    stage.run(ctx)
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    (term,) = model.terms
    assert (term.observed, term.defined_by, term.anchor_taps) == (True, cited, ["s03.e05>s02"])
    [line] = [t for t in read_trace(ctx.run_dir / "trace.jsonl") if t.step == "term_tap"]
    assert line.note == f"Luzia+ observed through tap s03.e05>s02; defined by {', '.join(cited)}"
    assert "· through tap s03.e05>s02" in (ctx.run_dir / "model" / "product_model.md").read_text()


def test_a_rejected_answer_is_retried_once_and_both_rounds_are_logged(tmp_path, monkeypatch):
    name = APPS[1]
    good = recorded_answer(golden(name))
    bad = good.model_copy(update={"value_ledger": [
        LedgerItem(id="made-up", kind="price", verbatim="$1,000,000", evidence_ids=["s01.e01"])]})
    calls = fake_calls(monkeypatch, [bad, good])
    ctx = make_ctx(name, tmp_path)
    stage.run(ctx)
    assert len(calls) == 2
    assert "ledger made-up" in calls[1]["messages"][-1]["content"][0]["text"]
    first_images = [p["png"] for p in calls[0]["messages"][0]["content"] if p["type"] == "image"]
    assert [p["png"] for p in calls[1]["messages"][0]["content"] if p["type"] == "image"] == first_images
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    assert "made-up" not in {i.id for i in model.value_ledger}
    steps = [step for step, _ in merge_notes(ctx)]
    assert steps == ["merge_round1", "merge_round2"]
    exhibit = (ctx.run_dir / "exhibits" / "02-model.md").read_text()
    assert "round 1 (first answer)" in exhibit and "ledger made-up" in exhibit and "round 2 (retry)" in exhibit


def test_a_failed_retry_keeps_the_checked_first_answer(tmp_path, monkeypatch):
    name = APPS[0]
    good = recorded_answer(golden(name))
    bad = good.model_copy(update={"value_ledger": [*good.value_ledger,
        LedgerItem(id="made-up", kind="price", verbatim="$1,000,000", evidence_ids=["s01.e01"])]})
    fake_calls(monkeypatch, [bad, llm.LLMFailure("max_tokens", "end_turn")])
    ctx = make_ctx(name, tmp_path)
    stage.run(ctx)
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    assert {i.id for i in model.value_ledger} == {i.id for i in good.value_ledger}
    assert model.flows == good.flows
    retry = [t for t in read_trace(ctx.run_dir / "trace.jsonl") if t.step == "retry"]
    assert retry[-1].outcome == "retry" and "kept the checked first answer" in retry[-1].note
    assert "The retry failed" in (ctx.run_dir / "exhibits" / "02-model.md").read_text()
    assert not (ctx.run_dir / "needs-human.md").exists()


def test_a_failed_retry_on_a_model_with_gaps_asks_for_a_human_and_continues(tmp_path, monkeypatch):
    name = APPS[1]
    good = recorded_answer(golden(name))
    gappy = good.model_copy(update={"states": good.states[1:], "flows": []})
    fake_calls(monkeypatch, [gappy, llm.LLMFailure("schema_fail", "twice", raw='{"flows": [')])
    ctx = make_ctx(name, tmp_path)
    stage.run(ctx)
    assert (ctx.run_dir / "model" / "product_model.json").exists()
    assert (ctx.run_dir / "model" / "raw_reply.txt").read_text() == '{"flows": ['
    asked = (ctx.run_dir / "needs-human.md").read_text()
    assert "the product model has gaps" in asked and "no core flow survived" in asked
    assert "**Continue with:** `simula model luzia --run run --profile dev --budget transfer --allow-fixtures`" in asked


def test_a_failed_call_asks_for_a_human_and_keeps_the_raw_answer(tmp_path, monkeypatch):
    fake_calls(monkeypatch, [llm.LLMFailure("schema_fail", "twice", raw='{"app_category": "chat", ')])
    ctx = make_ctx(APPS[2], tmp_path)
    with pytest.raises(llm.LLMFailure):
        stage.run(ctx)
    asked = (ctx.run_dir / "needs-human.md").read_text()
    assert "meaning call failed" in asked and f"`{rerun_command('model', ctx)}`" in asked and "--profile dev" in asked
    assert (ctx.run_dir / "model" / "raw_reply.txt").read_text() == '{"app_category": "chat", '
    assert json.loads((ctx.run_dir / "trace.jsonl").read_text().splitlines()[-1])["outcome"] == "blocked"


@pytest.mark.parametrize("name", APPS)
def test_core_loop_passes_become_measured_experience_in_the_model(name, tmp_path, monkeypatch):
    calls = fake_calls(monkeypatch, [recorded_answer(golden(name))])
    ctx = make_ctx(name, tmp_path)
    add_core_loop(ctx.run_dir / "explore", passes=4)
    stage.run(ctx)
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    experience = [i for i in model.value_ledger if i.kind == "experience"]
    assert [i.id for i in experience] == ["exp1", "exp2"]
    edges = {e.id for e in model.edges}
    assert all(set(i.evidence_ids) <= edges for i in experience)
    assert "MEASURED BY CODE" in calls[0]["messages"][0]["content"][0]["text"]
    assert experience[0].verbatim in calls[0]["messages"][0]["content"][0]["text"]


def drawn_state(sid: str, kind: str, parent: str | None, texts: list[tuple[str, int]]) -> State:
    """A state in the mock's scope whose (text, top) pairs are full-width TextViews, all drawn."""
    elements = [Element(id=f"{sid}.e{n:02d}", mcp_ref=None, type="TextView", text=t, label="", source="mcp",
                        rect_px=Rect(x=40, y=y, w=1000, h=100), rect_dp=Rect(x=0, y=0, w=0, h=0), role="text",
                        asset_png=None, fg_hex=None, bg_hex=None, font_px=None, font_guess="unknown", in_mock=True,
                        repeat_group=None) for n, (t, y) in enumerate(texts, start=1)]
    return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                 elements=elements, in_mock_scope=True, content_rating="safe", dynamic_regions=[], blocked_reason=None)


def test_a_sheets_copies_of_its_parents_elements_are_not_drawn_again():
    """The mock draws the parent as its own layer under a sheet, behind the backdrop, so a copy the sheet's capture
    lists is drawn once, by the parent, whether the sheet covers it or not."""
    page = [("Header above", 200), ("Card under", 1500), ("Card across the top edge", 1150), ("Tapped under", 1800)]
    parent = drawn_state("s02", "screen", None, page)
    sheet = drawn_state("s03", "sheet", "s02", [*page, ("Filters", 1250), ("Header above", 1400)])
    external = drawn_state("s04", "external", "s02", [("Header above", 200)])
    states, copies = stage.hide_parent_copies([parent, sheet, external], tapped={"s03.e04"})
    assert [(e.text, e.in_mock) for e in states[1].elements] == [
        ("Header above", False), ("Card under", False), ("Card across the top edge", False), ("Tapped under", True),
        ("Filters", True), ("Header above", True)], "the sheet's own text, even words the parent also shows, stays drawn"
    assert all(e.in_mock for e in states[0].elements + states[2].elements), "only a modal or sheet is a layer"
    assert copies == ["s03.e01", "s03.e02", "s03.e03"]


def test_a_dialog_over_a_dialog_hides_only_what_the_layer_under_it_draws():
    """The runtime shows a dialog's layer and its parent's. The paywall's layer leaves the chat to the chat's layer,
    which isn't shown under the confirm, so the confirm's layer draws the chat itself. The confirm comes first, as a
    launch dialog can come before its screen."""
    chat, paywall = [("Chat title", 200), ("Last message", 900)], [("Upgrade to Plus", 1300), ("$9.99 / month", 1400)]
    states, copies = stage.hide_parent_copies([
        drawn_state("s05", "modal", "s04", [*chat, *paywall, ("Leave without upgrading?", 1000)]),
        drawn_state("s03", "screen", None, chat), drawn_state("s04", "modal", "s03", [*chat, *paywall])], set())
    assert {s.id: [e.text for e in s.elements if e.in_mock] for s in states} == {
        "s03": ["Chat title", "Last message"], "s04": ["Upgrade to Plus", "$9.99 / month"],
        "s05": ["Chat title", "Last message", "Leave without upgrading?"]}, "every text shows in a layer on screen"
    assert copies == ["s04.e01", "s04.e02", "s05.e03", "s05.e04"]


def test_the_stage_draws_a_parents_element_once_when_its_sheet_lists_it_too(tmp_path, monkeypatch):
    fake_calls(monkeypatch, [recorded_answer(golden("janitorai"))] * 2)
    ctx = make_ctx("janitorai", tmp_path)
    folder = ctx.run_dir / "explore" / "states"

    def read(sid):
        reply = json.loads((folder / f"{sid}.elements.json").read_text())
        return reply, json.loads(reply["content"][0]["text"].removeprefix(stage.PREFIX))
    _, parent_tree = read("s08")
    reply, sheet_tree = read("s09")
    copied = next(e for e in parent_tree if e.get("text") and stage.in_content(e, DEVICE))
    reply["content"][0]["text"] = stage.PREFIX + json.dumps([*sheet_tree, {**copied, "ref": "@e999"}])
    (folder / "s09.elements.json").write_text(json.dumps(reply))
    stage.run(ctx)
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    by_id = {s.id: s for s in model.states}
    assert by_id["s09"].in_mock_scope and by_id["s08"].in_mock_scope
    assert [e.in_mock for e in by_id["s08"].elements if e.mcp_ref == copied["ref"]] == [True]
    assert [e.in_mock for e in by_id["s09"].elements if e.mcp_ref == "@e999"] == [False]
    assets = [t.note for t in read_trace(ctx.run_dir / "trace.jsonl") if t.step == "assets"]
    assert len(assets) == 1 and assets[0].endswith("not drawn again: 1")
    shown = (ctx.run_dir / "exhibits" / "02-model.md").read_text()
    assert f"- drawing (code): {assets[0]}" in shown and "- app name (the meaning call, read off the screens): " in shown
