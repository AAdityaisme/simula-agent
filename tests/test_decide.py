"""Jev through the stage's $ cap, cache, and --replay: stable ids for duplicate labels, one retry on an error,
a refusal or a second error raises JevFailed (the explorer then asks Sonnet), chunked ranking."""

import pytest

from simula import decide, llm
from simula.runlog import read_trace


def answer(option_id="o01", n=3):
    probabilities = {f"o{i:02d}": (0.8 if f"o{i:02d}" == option_id else 0.1) for i in range(1, n + 1)}
    return decide.ChoiceResult(option_id=option_id, confidence=0.8, probabilities=probabilities, model="jev-1.13.0",
                               tokens_in=200, tokens_out=0, usd=0.00002, seconds=0.2)


@pytest.fixture
def jev(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMULA_JEV_BACKEND", raising=False)
    calls, script = [], []

    def ask(state, instructions, labels, backend):
        calls.append(labels)
        step = script.pop(0) if script else answer(n=len(labels))
        if isinstance(step, Exception):
            raise step
        return step
    monkeypatch.setattr(decide, "ask_choice", ask)
    return calls, script


def choose(tmp_path, labels=("Like", "Chat", "Like"), **kwargs):
    options = dict(budget=llm.Budget("explore", 3.0), cache_dir=tmp_path / "cache")
    return decide.choose(tmp_path / "trace.jsonl", "explore", "rank.s01", "a screen", "which tap?", list(labels),
                         **{**options, **kwargs})


def test_duplicate_labels_get_distinct_ids():
    assert list(decide.option_ids(["Like", "Chat", "Like"])) == ["o01", "o02", "o03"]


def test_one_retry_on_an_error(tmp_path, jev):
    calls, script = jev
    script.append(RuntimeError("503"))
    assert choose(tmp_path).option_id == "o01" and len(calls) == 2
    assert [line.outcome for line in read_trace(tmp_path / "trace.jsonl")] == ["retry", "ok"]


def test_a_second_error_hands_off(tmp_path, jev):
    _, script = jev
    script += [RuntimeError("503"), RuntimeError("503")]
    with pytest.raises(decide.JevFailed):
        choose(tmp_path)


def test_a_refusal_hands_off_without_a_retry(tmp_path, jev):
    calls, script = jev
    script.append(answer("o09"))
    with pytest.raises(decide.JevFailed):
        choose(tmp_path)
    assert len(calls) == 1 and read_trace(tmp_path / "trace.jsonl")[-1].outcome == "refusal"


def test_second_ask_is_a_cache_hit_and_replay_never_calls(tmp_path, jev):
    calls, _ = jev
    choose(tmp_path)
    assert choose(tmp_path, replay=True).option_id == "o01" and len(calls) == 1
    assert read_trace(tmp_path / "trace.jsonl")[-1].cache_hit
    with pytest.raises(llm.ReplayMiss):
        choose(tmp_path, labels=("Other",), replay=True)
    assert len(calls) == 1


def test_no_cache_asks_again(tmp_path, jev):
    calls, _ = jev
    choose(tmp_path)
    choose(tmp_path, no_cache=True)
    assert len(calls) == 2


def test_the_cap_stops_before_the_call(tmp_path, jev, monkeypatch):
    calls, _ = jev
    monkeypatch.setenv("SIMULA_JEV_BACKEND", "adapter")
    with pytest.raises(llm.CapReached):
        choose(tmp_path, budget=llm.Budget("explore", 0.0001))
    assert not calls


def test_spend_is_charged_to_the_stage_budget(tmp_path, jev):
    budget = llm.Budget("explore", 3.0)
    choose(tmp_path, budget=budget)
    assert budget.spent == pytest.approx(0.00002)


def test_a_jev_question_gives_its_hold_back_when_it_answers_and_when_it_fails(tmp_path, jev, monkeypatch):
    calls, script = jev
    monkeypatch.setenv("SIMULA_JEV_BACKEND", "adapter")
    budget = llm.Budget("explore", 3.0)
    held = []
    monkeypatch.setattr(decide, "ask_choice", lambda *a: held.append(budget.held) or answer(n=3))
    choose(tmp_path, budget=budget)
    assert held[0] > 0 and budget.held == 0 and budget.spent == pytest.approx(0.00002)

    def failing(*a):
        held.append(budget.held)
        raise RuntimeError("backend down")
    monkeypatch.setattr(decide, "ask_choice", failing)
    with pytest.raises(decide.JevFailed):
        choose(tmp_path, labels=("Open", "Close"), budget=budget)
    assert len(held) == 3 and budget.held == 0 and budget.spent == pytest.approx(0.00002)


def test_more_than_ten_options_go_through_a_final_round(tmp_path, jev):
    calls, script = jev
    labels = [f"tap {i}" for i in range(23)]
    script += [answer("o03", 10), answer("o01", 10), answer("o02", 3), answer("o02", 3)]
    order, confidence = decide.rank(tmp_path / "trace.jsonl", "explore", "rank.s01", "a screen", "which tap?", labels,
                                    budget=llm.Budget("explore", 3.0), cache_dir=tmp_path / "cache")
    assert [len(c) for c in calls] == [10, 10, 3, 3]
    assert order[0] == 10 and order[1] == 2 and sorted(order) == list(range(23)) and confidence == 0.8
