"""The merge check and the whole stage, with a recorded answer standing in for the model call.

The recorded answer for each app is its golden model's meaning, so every assertion runs on all three apps.
A golden may carry an imprecision the check catches (a number its evidence doesn't show); tests compare
against that baseline instead of assuming the golden is spotless."""

import json

import pytest

from simula import llm
from simula.config import app_config
from simula.contracts import (Device, Element, ElementMeaning, Flow, LedgerItem, Mechanic, ModelMeaning,
                              ProductModel, Rect, State, StateMeaning)
from simula.runlog import read_trace
from simula.stages import Ctx
from simula.stages import model as stage
from tests.conftest import APPS, FIXTURES
from tests.explore_fixture import build

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
        open_questions=g.open_questions)


@pytest.fixture(params=APPS)
def app(request, tmp_path):
    explore = build(request.param, tmp_path / "explore")
    states, _ = stage.load_states(explore, DEVICE)
    edges, _ = stage.load_edges(explore, states)
    answer = recorded_answer(golden(request.param))
    _, baseline = stage.check_meaning(answer, states, edges)
    return request.param, states, edges, answer, baseline


def new_rejections(answer, states, edges, baseline):
    kept, rejected = stage.check_meaning(answer, states, edges)
    return kept, [r for r in rejected if r not in baseline]


def test_the_recorded_answer_loses_at_most_unshown_numbers(app):
    _, states, edges, answer, baseline = app
    assert all(r.startswith("mechanic ") and " number " in r for r in baseline)
    kept, _ = stage.check_meaning(answer, states, edges)
    assert kept.model_dump(exclude={"mechanics"}) == answer.model_dump(exclude={"mechanics"})
    assert [m.id for m in kept.mechanics] == [m.id for m in answer.mechanics]


def test_unknown_ids_are_rejected(app):
    _, states, edges, answer, baseline = app
    answer.states.append(StateMeaning(state_id="s99", name="x", purpose="x", content_rating="safe"))
    answer.elements.append(ElementMeaning(element_id="s01.e999", role="x", font_guess="x"))
    answer.mechanics.append(Mechanic(id="mx", kind="ad", evidence_ids=["s01.e999"], summary="x",
                                     observed_numbers=[], status="observed"))
    kept, rejected = new_rejections(answer, states, edges, baseline)
    assert [r.split(":")[0] for r in rejected] == ["state s99", "element s01.e999", "mechanic mx"]
    assert "s99" not in {s.state_id for s in kept.states} and "mx" not in {m.id for m in kept.mechanics}


def test_a_ledger_item_that_is_not_verbatim_is_rejected(app):
    _, states, edges, answer, baseline = app
    element = next(e for s in states for e in s.elements if e.text)
    answer.value_ledger += [
        LedgerItem(id="ok", kind="meter", verbatim=element.text, evidence_ids=[element.id]),
        LedgerItem(id="bad", kind="price", verbatim=element.text + " (paraphrased)", evidence_ids=[element.id])]
    kept, rejected = new_rejections(answer, states, edges, baseline)
    assert [r.split(":")[0] for r in rejected] == ["ledger bad"]
    assert "ok" in {i.id for i in kept.value_ledger}


def test_a_ledger_line_may_not_span_text_and_label():
    element = Element(id="s01.e01", mcp_ref="@e1", type="Button", text="Go", label="Premium", source="mcp",
                      rect_px=Rect(x=0, y=200, w=100, h=50), rect_dp=Rect(x=0, y=24, w=38, h=19), role="button",
                      asset_png=None, fg_hex=None, bg_hex=None, font_px=None, font_guess="unknown", in_mock=False,
                      repeat_group=None)
    state = State(id="s01", kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                  elements=[element], in_mock_scope=False, content_rating="safe", dynamic_regions=[],
                  blocked_reason=None)
    answer = ModelMeaning(app_category="other", states=[StateMeaning(state_id="s01", name="x", purpose="x",
                                                                     content_rating="safe")],
                          elements=[], flows=[], mechanics=[], cross_screen_values=[], open_questions=[],
                          value_ledger=[LedgerItem(id="span", kind="price", verbatim="Go Premium", evidence_ids=["s01.e01"]),
                                        LedgerItem(id="label", kind="price", verbatim="Premium", evidence_ids=["s01.e01"])])
    kept, rejected = stage.check_meaning(answer, [state], [])
    assert [i.id for i in kept.value_ledger] == ["label"] and rejected[0].startswith("ledger span")


def test_a_money_mechanic_must_cite_an_element(app):
    _, states, edges, answer, baseline = app
    answer.mechanics.append(Mechanic(id="m-state-only", kind="paywall", evidence_ids=[states[0].id], summary="x",
                                     observed_numbers=[], status="inferred"))
    _, rejected = new_rejections(answer, states, edges, baseline)
    assert rejected == ["mechanic m-state-only: a paywall must cite an element"]


def test_a_number_its_evidence_does_not_show_is_dropped_but_the_mechanic_stays(app):
    _, states, edges, answer, baseline = app
    element = next(e for s in states for e in s.elements if e.text)
    answer.mechanics.append(Mechanic(id="m-num", kind="other", evidence_ids=[element.id], summary="x",
                                     observed_numbers=[element.text, "$9.99"], status="observed"))
    kept, rejected = new_rejections(answer, states, edges, baseline)
    assert rejected == ["mechanic m-num: number '$9.99' is not shown in its evidence elements"]
    assert next(m for m in kept.mechanics if m.id == "m-num").observed_numbers == [element.text]


def test_flows_with_missing_or_disconnected_edges_are_rejected(app):
    name, states, edges, answer, baseline = app
    a, b = next((a, b) for a in edges for b in edges if a.to_state != b.from_state)
    answer.flows += [Flow(id="missing", name="x", purpose="x", edge_ids=["s01.e01>s99"], evidence_ids=[]),
                     Flow(id="empty", name="x", purpose="x", edge_ids=[], evidence_ids=[]),
                     Flow(id="broken", name="x", purpose="x", edge_ids=[a.id, b.id], evidence_ids=[])]
    kept, rejected = new_rejections(answer, states, edges, baseline)
    assert [r.split(":")[0] for r in rejected] == ["flow missing", "flow empty", "flow broken"]
    assert {f.id for f in kept.flows} == {f.id for f in golden(name).flows}


def test_gaps_count_as_rejections_so_they_trigger_the_retry(app):
    _, states, edges, answer, baseline = app
    gappy = answer.model_copy(update={"states": answer.states[1:], "flows": [
        Flow(id="broken", name="x", purpose="x", edge_ids=["nope"], evidence_ids=[])]})
    _, rejected = new_rejections(gappy, states, edges, baseline)
    assert f"state {states[0].id}: no meaning" in rejected
    assert rejected[-1] == "no core flow survived"


def test_the_keyword_floor_raises_a_rating_and_never_lowers_one(app):
    _, states, _, answer, _ = app
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
    _, states, _, answer, _ = app
    rated = stage.apply_meaning(states, answer, KEYWORDS)
    code_fields = ("id", "kind", "parent_id", "fingerprint", "canonical_png", "dynamic_regions", "blocked_reason")
    for before, after in zip(states, rated):
        assert all(getattr(before, f) == getattr(after, f) for f in code_fields)
        for e0, e1 in zip(before.elements, after.elements):
            assert e0.model_dump(exclude={"role", "font_guess"}) == e1.model_dump(exclude={"role", "font_guess"})


def test_code_names_tabs_and_tapped_boxes_and_fills_fonts(app):
    _, states, edges, answer, _ = app
    named = stage.code_roles(stage.apply_meaning(states, answer, KEYWORDS), edges)
    elements = {e.id: e for s in named for e in s.elements}
    for edge in edges:
        if edge.element_id:
            role = elements[edge.element_id].role
            assert role == "tab" if edge.transition == "tab" else role != "container", edge.id
    assert all(e.font_guess != "unknown" for e in elements.values() if e.text)


def test_mock_scope_is_chosen_by_code(app):
    _, states, edges, answer, _ = app
    rated = stage.apply_meaning(states, answer, KEYWORDS)
    scope = stage.mock_scope(rated, edges, answer)
    by_id = {s.id: s for s in rated}
    assert 1 <= len(scope) <= stage.SCOPE_CAP
    assert scope[0] == next(s.id for s in rated if s.kind == "screen")
    assert all(by_id[s].content_rating != "unsafe" and by_id[s].kind not in ("blocked", "external") for s in scope)
    for sid in scope:
        parent = by_id[sid].parent_id
        assert parent is None or (parent in scope and scope.index(parent) < scope.index(sid)), sid
    unsafe = [m.model_copy(update={"content_rating": "unsafe"}) if m.state_id == scope[-1] else m for m in answer.states]
    rated = stage.apply_meaning(states, answer.model_copy(update={"states": unsafe}), KEYWORDS)
    assert scope[-1] not in stage.mock_scope(rated, edges, answer)


def test_a_modal_in_scope_brings_its_parent_first():
    def state(sid, kind="screen", parent=None):
        return State(id=sid, kind=kind, parent_id=parent, name="", purpose="", fingerprint="", canonical_png="",
                     elements=[], in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)
    states = [state("s01"), state("s02"), state("s03", "modal", "s02")]
    meaning = ModelMeaning(app_category="other", states=[], elements=[], flows=[], cross_screen_values=[],
                           value_ledger=[], open_questions=[],
                           mechanics=[Mechanic(id="m1", kind="ad", evidence_ids=["s03"], summary="x",
                                               observed_numbers=[], status="observed")])
    assert stage.mock_scope(states, [], meaning) == ["s01", "s02", "s03"]
    unsafe_parent = [states[0], states[1].model_copy(update={"content_rating": "unsafe"}), states[2]]
    assert stage.mock_scope(unsafe_parent, [], meaning) == ["s01"]


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
    assert all(e.in_mock for s in model.states for e in s.elements if e.id in tapped and s.id in scope)
    assert model.flows
    md = (out / "product_model.md").read_text()
    assert md.count("```mermaid") == 1 + len(model.flows)
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


def test_a_failed_call_asks_for_a_human_and_keeps_the_raw_answer(tmp_path, monkeypatch):
    fake_calls(monkeypatch, [llm.LLMFailure("schema_fail", "twice", raw='{"app_category": "chat", ')])
    ctx = make_ctx(APPS[2], tmp_path)
    with pytest.raises(llm.LLMFailure):
        stage.run(ctx)
    assert "meaning call failed" in (ctx.run_dir / "needs-human.md").read_text()
    assert (ctx.run_dir / "model" / "raw_reply.txt").read_text() == '{"app_category": "chat", '
    assert json.loads((ctx.run_dir / "trace.jsonl").read_text().splitlines()[-1])["outcome"] == "blocked"
