import threading
import time

import pytest

from simula import llm
from tests.test_cache import MODEL, call, fake_provider, message


def test_reserve_allows_up_to_the_cap_exactly():
    budget = llm.Budget("qa", cap=1.0, spent=0.5)
    budget.reserve(0.5)
    with pytest.raises(llm.CapReached):
        budget.reserve(0.5000001)


def test_worst_case_uses_max_tokens_not_actual_output():
    assert llm.worst_case_usd(MODEL, 1000, 8000) == pytest.approx((1000 * 1.0 + 8000 * 5.0) / 1e6)


def test_call_stops_before_crossing_the_cap(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}'], calls))
    tight = llm.Budget("model", cap=0.0001)
    with pytest.raises(llm.CapReached):
        call(tmp_path, budget=tight, max_tokens=8000)
    assert calls == []


def test_spend_is_charged_from_actual_usage(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}'], []))
    budget = llm.Budget("model", cap=1.0)
    call(tmp_path, budget=budget)
    assert budget.spent == pytest.approx(llm.usd(MODEL, 100, 10))


def test_budget_resumes_from_the_trace(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}'], []))
    call(tmp_path)
    resumed = llm.Budget.for_stage("model", tmp_path / "trace.jsonl", cap=1.0)
    assert resumed.spent == pytest.approx(llm.usd(MODEL, 100, 10), abs=1e-6)


def test_calls_in_flight_hold_their_worst_case_until_charged():
    budget = llm.Budget("propose", cap=1.0)
    gate = threading.Barrier(5)
    reserved = []

    def reserve():
        gate.wait()
        try:
            budget.reserve(0.3)
            reserved.append(1)
        except llm.CapReached:
            pass

    threads = [threading.Thread(target=reserve) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reserved) == 3 and budget.held == pytest.approx(0.9)
    budget.charge(0.1, 0.3)
    assert budget.spent == pytest.approx(0.1) and budget.held == pytest.approx(0.6)


def test_overlapping_calls_stop_at_the_cap_and_release_what_they_held(tmp_path, monkeypatch):
    def slow(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        time.sleep(0.2)
        return llm.Reply(text='{"word": "a"}', model=model, tokens_in=100, tokens_out=10)

    monkeypatch.setitem(llm.PROVIDERS, "anthropic", slow)
    worst = llm.worst_case_usd(MODEL, llm.estimate_tokens_in("", message()), 8000)
    budget = llm.Budget("model", cap=worst * 2.5)
    outcomes = []

    def one(n):
        try:
            call(tmp_path, budget=budget, max_tokens=8000, step=f"t{n}", no_cache=True)
            outcomes.append("ok")
        except llm.CapReached:
            outcomes.append("cap")

    threads = [threading.Thread(target=one, args=(n,)) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["cap", "ok", "ok"]
    assert budget.held == pytest.approx(0) and budget.spent == pytest.approx(2 * llm.usd(MODEL, 100, 10))
