import pytest

from simula import llm
from tests.test_cache import MODEL, call, fake_provider


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
