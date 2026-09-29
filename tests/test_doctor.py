"""Doctor's paid probes log their own spend to build/trace.jsonl."""

import pytest

from simula import decide, doctor, llm, runlog
from simula.runlog import read_trace


@pytest.fixture
def build_trace(tmp_path, monkeypatch):
    path = tmp_path / "build" / "trace.jsonl"
    monkeypatch.setattr(runlog, "BUILD_TRACE", path)
    return path


def test_a_model_ping_logs_one_spend_line(build_trace, monkeypatch):
    def provider(model, system, messages, effort, schema, max_tokens):
        return llm.Reply(text='{"ok": true, "word": "ready"}', model=model, tokens_in=260, tokens_out=17)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    assert doctor.ping("sonnet probe", "claude-sonnet-5-5", "high")["ok"]
    [line] = read_trace(build_trace)
    assert (line.stage, line.step, line.decider, line.model, line.effort) == ("build", "doctor", "model", "claude-sonnet-5-5", "high")
    assert (line.tokens_in, line.tokens_out) == (260, 17)
    assert line.usd == pytest.approx(llm.usd("claude-sonnet-5-5", 260, 17), abs=1e-6)


def test_a_failed_ping_logs_a_zero_cost_error(build_trace, monkeypatch):
    def provider(*args):
        raise llm.LLMFailure("error", "boom")
    monkeypatch.setitem(llm.PROVIDERS, "openai", provider)
    assert not doctor.ping("sol probe", "gpt-6-sol", "high")["ok"]
    [line] = read_trace(build_trace)
    assert (line.outcome, line.usd) == ("error", 0.0)


def test_a_jev_probe_logs_its_spend(build_trace, monkeypatch):
    result = decide.ChoiceResult(option_id="o16", confidence=0.97, probabilities={}, model="jev-1.13.0",
                                 tokens_in=586, tokens_out=163, usd=0.00002, seconds=0.2)
    monkeypatch.setattr(decide, "ask_choice", lambda *args: result)
    assert doctor.jev_probe("typesafe", 16)["ok"]
    [line] = read_trace(build_trace)
    assert (line.decider, line.model, line.confidence, line.usd) == ("jev", "jev-1.13.0", 0.97, 0.00002)
