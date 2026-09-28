"""The merge check and the whole stage, with a recorded answer standing in for the model call.

The recorded answer for each app is its golden model's meaning, so every assertion runs on all three apps."""

import json

import pytest

from simula import llm
from simula.config import app_config
from simula.contracts import (Device, ElementMeaning, Flow, LedgerItem, Mechanic, ModelMeaning, ProductModel,
                              StateMeaning)
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
        elements=[ElementMeaning(element_id=e.id, role=e.role, font_guess="Inter") for s in g.states for e in s.elements],
        flows=g.flows, mechanics=g.mechanics, cross_screen_values=g.cross_screen_values, value_ledger=g.value_ledger,
        open_questions=g.open_questions)


@pytest.fixture(params=APPS)
def app(request, tmp_path):
    explore = build(request.param, tmp_path / "explore")
    states, images = stage.load_states(explore, DEVICE)
    edges, _ = stage.load_edges(explore, states)
    return request.param, states, edges


def test_the_recorded_answer_passes_clean(app):
    name, states, edges = app
    answer = recorded_answer(golden(name))
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == [] and kept == answer


def test_unknown_ids_are_rejected(app):
    name, states, edges = app
    answer = recorded_answer(golden(name))
    answer.states.append(StateMeaning(state_id="s99", name="x", purpose="x", content_rating="safe"))
    answer.elements.append(ElementMeaning(element_id="s01.e999", role="x", font_guess="x"))
    answer.mechanics.append(Mechanic(id="mx", kind="ad", evidence_ids=["s01.e999"], summary="x",
                                     observed_numbers=[], status="observed"))
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert len(rejected) == 3
    assert "s99" not in {s.state_id for s in kept.states} and "mx" not in {m.id for m in kept.mechanics}


def test_a_ledger_item_that_is_not_verbatim_is_rejected(app):
    name, states, edges = app
    element = next(e for s in states for e in s.elements if e.text)
    answer = recorded_answer(golden(name))
    answer.value_ledger += [
        LedgerItem(id="ok", kind="meter", verbatim=element.text, evidence_ids=[element.id]),
        LedgerItem(id="bad", kind="price", verbatim=element.text + " (paraphrased)", evidence_ids=[element.id])]
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert [r.split(":")[0] for r in rejected] == ["ledger bad"]
    assert "ok" in {i.id for i in kept.value_ledger}


def test_a_money_mechanic_must_cite_an_element(app):
    name, states, edges = app
    answer = recorded_answer(golden(name))
    answer.mechanics.append(Mechanic(id="m-state-only", kind="paywall", evidence_ids=[states[0].id], summary="x",
                                     observed_numbers=[], status="inferred"))
    _, rejected = stage.check_meaning(answer, states, edges)
    assert rejected == ["mechanic m-state-only: a paywall must cite an element"]


def test_flows_with_missing_or_disconnected_edges_are_rejected(app):
    name, states, edges = app
    a, b = next((a, b) for a in edges for b in edges if a.to_state != b.from_state)
    answer = recorded_answer(golden(name))
    answer.flows += [Flow(id="missing", name="x", purpose="x", edge_ids=["s01.e01>s99"], evidence_ids=[]),
                     Flow(id="empty", name="x", purpose="x", edge_ids=[], evidence_ids=[]),
                     Flow(id="broken", name="x", purpose="x", edge_ids=[a.id, b.id], evidence_ids=[])]
    kept, rejected = stage.check_meaning(answer, states, edges)
    assert [r.split(":")[0] for r in rejected] == ["flow missing", "flow empty", "flow broken"]
    assert {f.id for f in kept.flows} == {f.id for f in golden(name).flows}


def test_the_keyword_floor_raises_a_rating_and_never_lowers_one(app):
    name, states, edges = app
    target = next(s for s in states if s.elements)
    flagged = target.elements[0].model_copy(update={"text": "NSFW only"})
    states = [s.model_copy(update={"elements": [flagged, *s.elements[1:]]}) if s is target else s for s in states]
    answer = recorded_answer(golden(name))
    answer = answer.model_copy(update={"states": [
        m.model_copy(update={"content_rating": "safe" if m.state_id == target.id else "unsafe"})
        for m in answer.states]})
    rated = {s.id: s.content_rating for s in stage.apply_meaning(states, answer, KEYWORDS)}
    assert rated[target.id] == "unsafe"
    assert all(r == "unsafe" for r in rated.values())


def test_keywords_match_whole_words_only():
    state = stage.State(id="s01", kind="screen", parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                        elements=[], in_mock_scope=False, content_rating="safe", dynamic_regions=[], blocked_reason=None)
    assert stage.keyword_floor(state, KEYWORDS) == "safe"


def test_code_fields_are_never_overwritten(app):
    name, states, edges = app
    rated = stage.apply_meaning(states, recorded_answer(golden(name)), KEYWORDS)
    code_fields = ("id", "kind", "parent_id", "fingerprint", "canonical_png", "dynamic_regions", "blocked_reason")
    for before, after in zip(states, rated):
        assert all(getattr(before, f) == getattr(after, f) for f in code_fields)
        for e0, e1 in zip(before.elements, after.elements):
            assert e0.model_dump(exclude={"role", "font_guess"}) == e1.model_dump(exclude={"role", "font_guess"})


def test_mock_scope_is_chosen_by_code(app):
    name, states, edges = app
    answer = recorded_answer(golden(name))
    rated = stage.apply_meaning(states, answer, KEYWORDS)
    scope = stage.mock_scope(rated, edges, answer)
    by_id = {s.id: s for s in rated}
    assert 1 <= len(scope) <= stage.SCOPE_CAP
    assert scope[0] == next(s.id for s in rated if s.kind == "screen")
    assert all(by_id[s].content_rating != "unsafe" and by_id[s].kind not in ("blocked", "external") for s in scope)
    unsafe = [m.model_copy(update={"content_rating": "unsafe"}) if m.state_id == scope[-1] else m for m in answer.states]
    rated = stage.apply_meaning(states, answer.model_copy(update={"states": unsafe}), KEYWORDS)
    assert scope[-1] not in stage.mock_scope(rated, edges, answer)


# ---------- the whole stage ----------

def make_ctx(name: str, tmp_path) -> Ctx:
    run_dir = tmp_path / "run"
    build(name, run_dir / "explore")
    return Ctx(app=app_config(name), run_dir=run_dir, profile="dev", no_cache=False, replay=False, usd_cap=None,
               allow_fixtures=True)


@pytest.mark.parametrize("name", APPS)
def test_the_stage_writes_a_valid_model(name, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "call", lambda **kw: calls.append(kw) or (recorded_answer(golden(name)), None))
    ctx = make_ctx(name, tmp_path)
    stage.run(ctx)
    out = ctx.run_dir / "model"
    model = ProductModel.model_validate_json((out / "product_model.json").read_text())
    assert len(calls) == 1 and calls[0]["schema"] is ModelMeaning
    assert all((out / s.canonical_png).exists() for s in model.states)
    assert all((out / e.asset_png).exists() for s in model.states for e in s.elements if e.asset_png)
    assert model.flows and model.edges == stage.load_edges(ctx.run_dir / "explore", model.states)[0]
    md = (out / "product_model.md").read_text()
    assert md.count("```mermaid") == 1 + len(model.flows)
    assert (ctx.run_dir / "exhibits" / "02-model.md").exists()
    images = [p for m in calls[0]["messages"] for p in m["content"] if p["type"] == "image"]
    assert 1 <= len(images) <= stage.MAX_IMAGES


def test_a_rejected_answer_is_retried_once_with_the_rejection_list(tmp_path, monkeypatch):
    name = APPS[1]
    good = recorded_answer(golden(name))
    bad = good.model_copy(update={"value_ledger": [
        LedgerItem(id="made-up", kind="price", verbatim="$1,000,000", evidence_ids=["s01.e01"])]})
    answers, calls = [bad, good], []
    monkeypatch.setattr(llm, "call", lambda **kw: calls.append(kw) or (answers[len(calls) - 1], None))
    ctx = make_ctx(name, tmp_path)
    stage.run(ctx)
    assert len(calls) == 2
    retry_text = calls[1]["messages"][-1]["content"][0]["text"]
    assert "ledger made-up" in retry_text
    model = ProductModel.model_validate_json((ctx.run_dir / "model" / "product_model.json").read_text())
    assert "made-up" not in {i.id for i in model.value_ledger}


def test_a_failed_call_asks_for_a_human(tmp_path, monkeypatch):
    def fail(**kw):
        raise llm.LLMFailure("schema_fail", "twice")
    monkeypatch.setattr(llm, "call", fail)
    ctx = make_ctx(APPS[2], tmp_path)
    with pytest.raises(llm.LLMFailure):
        stage.run(ctx)
    assert "meaning call failed" in (ctx.run_dir / "needs-human.md").read_text()
    assert json.loads((ctx.run_dir / "trace.jsonl").read_text().splitlines()[-1])["outcome"] == "blocked"
