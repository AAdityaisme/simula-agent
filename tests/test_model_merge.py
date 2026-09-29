"""The merge check and the whole stage, with a recorded answer standing in for the model call.

The recorded answer for each app is its golden model's meaning, so every assertion runs on all three apps."""

import json

import pytest

from simula import llm
from simula.config import app_config
from simula.contracts import (Device, Edge, Element, ElementMeaning, Flow, LedgerItem, Mechanic, ModelMeaning,
                              ProductModel, QuestionDraft, Rect, State, StateMeaning, TermMeaning)
from simula.runlog import read_trace
from simula.stages import Ctx, rerun_command
from simula.stages import model as stage
from tests.conftest import APPS, FIXTURES
from tests.explore_fixture import add_core_loop, build

DEVICE = Device()
KEYWORDS = ["nsfw", "18+", "explicit"]


def golden(app: str) -> ProductModel:
    return ProductModel.model_validate_json((FIXTURES / "golden" / app / "product_model.json").read_text())


def recorded_answer(g: ProductModel) -> ModelMeaning:
    return ModelMeaning(
        app_category=g.app_category,
        states=[StateMeaning(state_id=s.id, name=s.name, purpose=s.purpose, content_rating=s.content_rating)
                for s in g.states],
        elements=[ElementMeaning(element_id=e.id, role=e.role, font_guess="Inter")
                  for e in [e for s in g.states for e in s.elements if e.text][::2]],
        flows=g.flows, mechanics=g.mechanics, cross_screen_values=g.cross_screen_values, value_ledger=g.value_ledger,
        terms=[], open_questions=[QuestionDraft(id=f"q{n}", question=q, start_state=g.states[0].id,
                                                look_for="the screen that answers it")
                                  for n, q in enumerate(g.open_questions, start=1)])


@pytest.fixture(params=APPS)
def app(request, tmp_path):
    explore = build(request.param, tmp_path / "explore")
    states, _ = stage.load_states(explore, DEVICE)
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
    answer = ModelMeaning(app_category="other", states=[StateMeaning(state_id="s01", name="x", purpose="x",
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
    meaning = ModelMeaning(app_category="other", states=[], elements=[], flows=[], cross_screen_values=[],
                           value_ledger=[], open_questions=[], terms=[],
                           mechanics=[Mechanic(id="m1", kind="ad", evidence_ids=["s03"], summary="x",
                                               observed_numbers=[], status="observed")])
    assert stage.mock_scope(states, [], meaning) == ["s01", "s02", "s03"]
    unsafe_parent = [states[0], states[1].model_copy(update={"content_rating": "unsafe"}), states[2]]
    assert stage.mock_scope(unsafe_parent, [], meaning) == ["s01"]
    blocked_parent = [states[0], states[1].model_copy(update={"kind": "blocked"}), states[2]]
    assert stage.mock_scope(blocked_parent, [], meaning) == ["s01"]
    rotated = [states[0], states[1], states[2].model_copy(update={"kind": "rotated", "parent_id": None})]
    assert stage.mock_scope(rotated, [], meaning) == ["s01"]


def test_a_flow_dialog_keeps_its_parent_even_when_the_parent_is_unsafe_and_off_the_flow():
    """Whether the unsafe parent's content shows under the dialog is the renderer's and the blur's job."""
    def state(sid, kind="screen", parent=None, rating="safe"):
        return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[], in_mock_scope=False, content_rating=rating, dynamic_regions=[], blocked_reason=None)
    states = [state("s01"), state("s02", rating="unsafe"), state("s03", "modal", "s02")]
    edges = [Edge(id="s03.e01>s01", from_state="s03", to_state="s01", element_id=None, action="tap",
                  transition="back", change_summary="")]
    meaning = ModelMeaning(app_category="other", states=[], elements=[], cross_screen_values=[], value_ledger=[],
                           open_questions=[], terms=[], mechanics=[],
                           flows=[Flow(id="f01", name="x", purpose="x", edge_ids=["s03.e01>s01"], evidence_ids=[])])
    assert stage.mock_scope(states, edges, meaning) == ["s01", "s02", "s03"]


def test_a_sheet_over_a_modal_brings_the_whole_stack_parent_first():
    def state(sid, kind="screen", parent=None):
        return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[], in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)
    states = [state("s01"), state("s02"), state("s03", "modal", "s02"), state("s04", "sheet", "s03")]
    meaning = ModelMeaning(app_category="other", states=[], elements=[], flows=[], cross_screen_values=[],
                           value_ledger=[], open_questions=[], terms=[],
                           mechanics=[Mechanic(id="m1", kind="paywall", evidence_ids=["s04"], summary="x",
                                               observed_numbers=[], status="observed")])
    assert stage.mock_scope(states, [], meaning) == ["s01", "s02", "s03", "s04"]
    unsafe_screen = [states[0], states[1].model_copy(update={"content_rating": "unsafe"}), *states[2:]]
    assert stage.mock_scope(unsafe_screen, [], meaning) == ["s01"]


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


def test_a_term_keeps_its_meaning_only_when_a_cited_element_carries_it(app):
    _, states, edges, answer = app
    carrier = next(e for s in states for e in s.elements if len(e.text.split()) >= 2)
    word = carrier.text.split()[-1]
    other = next(e for s in states for e in s.elements if e.text and word.lower() not in e.text.lower())
    answer.mechanics.append(Mechanic(id="m-term", kind="other", evidence_ids=[states[0].id], summary=f"Uses {word}.",
                                     observed_numbers=[], status="observed"))
    answer.terms += [TermMeaning(term=word.upper(), meaning="a plain meaning", defined_by=[carrier.id, "s01.e999"],
                                 used_in=["m-term"]),
                     TermMeaning(term=word, meaning="a guess", defined_by=[other.id], used_in=["m-term"]),
                     TermMeaning(term="Zorblax", meaning="x", defined_by=[carrier.id], used_in=["m-term"])]
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["term 'Zorblax': used_in ['m-term'] names no kept mechanic or ledger line that uses it"]
    observed, unobserved = stage.resolve_terms(kept, states)
    assert (observed.observed, observed.meaning, observed.defined_by) == (True, "a plain meaning", [carrier.id])
    assert (unobserved.observed, unobserved.meaning, unobserved.defined_by) == (False, stage.NOT_OBSERVED, [])


def test_the_line_that_uses_a_term_cannot_define_it(app):
    _, states, edges, answer = app
    bullet = next(e for s in states for e in s.elements if len(e.text.split()) >= 2)
    word = bullet.text.split()[-1]
    answer.value_ledger.append(LedgerItem(id="l-term", kind="meter", verbatim=bullet.text, evidence_ids=[bullet.id]))
    answer.terms.append(TermMeaning(term=word, meaning="a guess", defined_by=[bullet.id], used_in=["l-term"]))
    kept, rejected = stage.check_meaning(answer, states, edges)
    (term,) = stage.resolve_terms(kept, states)
    assert rejected == [] and (term.observed, term.meaning, term.defined_by) == (False, stage.NOT_OBSERVED, [])


def test_questions_need_a_real_start_state_and_are_capped(app):
    _, states, edges, answer = app
    answer.open_questions[:] = [QuestionDraft(id=f"q{n}", question="?", start_state=states[0].id, look_for="x")
                                for n in range(7)]
    answer.open_questions.insert(1, QuestionDraft(id="nowhere", question="?", start_state="s99", look_for="x"))
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["question nowhere: start_state 's99' is not a recorded state"]
    assert [q.id for q in kept.open_questions] == ["q0", "q1", "q2", "q3", "q4"]


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
        app_category="chat", states=[], elements=[], cross_screen_values=[], value_ledger=[], open_questions=[],
        terms=[], flows=[Flow(id="f1", name="core", purpose="x", edge_ids=[f"{a}.tap>{b}" for a, b in hops],
                              evidence_ids=[])],
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
    clean = recorded_answer(golden(name))
    calls = fake_calls(monkeypatch, [clean, clean])
    ctx = make_ctx(name, tmp_path)
    stage.run(ctx)
    out = ctx.run_dir / "model"
    model = ProductModel.model_validate_json((out / "product_model.json").read_text())
    assert calls[0]["schema"] is ModelMeaning and calls[0]["max_tokens"] == 64000
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
    assert md.count("```mermaid") == 1 + len(model.flows)
    assert model.open_questions == [q.question for q in model.questions] and not any(q.answered for q in model.questions)
    assert (ctx.run_dir / "exhibits" / "02-model.md").exists()
    images = [p for m in calls[0]["messages"] for p in m["content"] if p["type"] == "image"]
    assert 1 <= len(images) <= stage.MAX_IMAGES


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
