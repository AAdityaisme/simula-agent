import json

import pytest
from pydantic import BaseModel

from simula import llm
from simula.runlog import read_trace

MODEL = "claude-haiku-4-5-20251001"


class Answer(BaseModel):
    word: str


def message(text="hi", png=None):
    parts = [{"type": "text", "text": text}]
    if png:
        parts.append({"type": "image", "png": png})
    return [{"role": "user", "content": parts}]


def fake_provider(texts, calls):
    def provider(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(model)
        return llm.Reply(text=texts.pop(0), model=model, tokens_in=100, tokens_out=10)
    return provider


def call(tmp_path, **overrides):
    kwargs = dict(trace_path=tmp_path / "trace.jsonl", stage="model", step="t", model=MODEL, effort=None,
                  system="", messages=message(), max_tokens=100, budget=llm.Budget("model", 1.0),
                  schema=Answer, cache_dir=tmp_path / "cache")
    return llm.call(**{**kwargs, **overrides})


def key(**overrides):
    args = dict(provider="anthropic", model=MODEL, system="", messages=message(), params={"schema": 1}, attempt=0)
    return llm.cache_key(**{**args, **overrides})


def test_same_request_same_key():
    assert key() == key()


def test_image_bytes_change_the_key_but_are_not_stored_raw():
    a, b = key(messages=message(png=b"\x89PNG one")), key(messages=message(png=b"\x89PNG two"))
    assert a != b
    assert "sha256" in json.dumps(llm.canonical(message(png=b"\x89PNG one")))


def test_schema_and_attempt_are_in_the_key():
    assert key(params={"schema": 2}) != key()
    assert key(attempt=1) != key()


def test_second_call_is_a_cache_hit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "ready"}'], calls))
    first, _ = call(tmp_path)
    second, reply = call(tmp_path)
    assert first == second == Answer(word="ready")
    assert len(calls) == 1
    assert [line.cache_hit for line in read_trace(tmp_path / "trace.jsonl")] == [False, True]


def test_invalid_response_is_cached_only_as_a_failed_attempt_and_retried(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"nope": 1}', '{"word": "ok"}'], calls))
    result, _ = call(tmp_path)
    assert result.word == "ok" and len(calls) == 2
    entries = [json.loads(p.read_text()) for p in (tmp_path / "cache").glob("*.json")]
    assert sorted(e["failure"] for e in entries) == ["", "schema_fail"]
    assert [line.outcome for line in read_trace(tmp_path / "trace.jsonl")] == ["schema_fail", "ok"]


def test_two_bad_answers_raise_a_typed_failure(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['nope', 'still nope'], []))
    with pytest.raises(llm.LLMFailure) as failure:
        call(tmp_path)
    assert failure.value.outcome == "schema_fail"
    assert failure.value.raw == "still nope"
    entries = [json.loads(p.read_text()) for p in (tmp_path / "cache").glob("*.json")]
    assert [e["failure"] for e in entries] == ["schema_fail", "schema_fail"]


def test_recorded_failures_replay_as_the_same_failure_without_a_call(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['nope', 'still nope'], []))
    with pytest.raises(llm.LLMFailure):
        call(tmp_path)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    for replay in (True, False):
        with pytest.raises(llm.LLMFailure) as failure:
            call(tmp_path, replay=replay)
        assert (failure.value.outcome, failure.value.raw) == ("schema_fail", "still nope")


def test_a_failed_one_attempt_call_replays_to_the_callers_own_retry(tmp_path, monkeypatch):
    """The mock stage's shape: its first call fails at max_tokens, then it retries with a different call."""
    def provider(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(effort)
        if effort == "xhigh":
            return llm.Reply(text="<html>cut", model=model, tokens_in=100, tokens_out=100, stop_reason="max_tokens")
        return llm.Reply(text='{"word": "short"}', model=model, tokens_in=100, tokens_out=10)

    def stage(**extra):
        try:
            return call(tmp_path, effort="xhigh", attempts=1, **extra)[0]
        except llm.LLMFailure as e:
            assert e.outcome == "max_tokens"
            return call(tmp_path, effort="high", attempts=1, **extra)[0]

    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    assert stage().word == "short" and calls == ["xhigh", "high"]
    assert stage().word == "short" and calls == ["xhigh", "high"]
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    assert stage(replay=True).word == "short"


def test_a_rerun_skips_recorded_failed_attempts_and_pays_only_for_the_next(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['nope'], []))
    # attempt 1 dies before answering (an untyped error, settled as a typed one), so only attempt 0 is recorded
    with pytest.raises(llm.LLMFailure, match="error: pop from empty list"):
        call(tmp_path)
    rerun_calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "second"}'], rerun_calls))
    result, _ = call(tmp_path)
    assert result.word == "second" and len(rerun_calls) == 1


def test_no_cache_skips_reads(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}', '{"word": "b"}'], calls))
    call(tmp_path)
    result, _ = call(tmp_path, no_cache=True)
    assert result.word == "b" and len(calls) == 2


def test_replay_miss_fails_before_any_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}'], calls))
    with pytest.raises(llm.ReplayMiss):
        call(tmp_path, replay=True)
    assert calls == []


def test_replay_hit_needs_no_provider(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}'], []))
    call(tmp_path)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    result, _ = call(tmp_path, replay=True)
    assert result.word == "a"


def test_replay_after_a_retry_hits_the_cache(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"nope": 1}', '{"word": "good"}'], []))
    call(tmp_path)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    result, _ = call(tmp_path, replay=True)
    assert result.word == "good"


def test_rerun_after_a_retry_makes_no_paid_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"nope": 1}', '{"word": "good"}'], calls))
    call(tmp_path)
    result, _ = call(tmp_path)
    assert result.word == "good" and len(calls) == 2


def failing_provider(calls):
    def provider(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(model)
        raise llm.LLMFailure("error", "429 rate limited")
    return provider


def test_declared_fallback_is_used_and_traced(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", failing_provider(calls))
    monkeypatch.setitem(llm.PROVIDERS, "openai", fake_provider(['{"word": "luna"}'], calls))
    result, reply = call(tmp_path, fallback="gpt-6-luna")
    assert result.word == "luna" and calls == [MODEL, MODEL, "gpt-6-luna"]
    notes = [line.note for line in read_trace(tmp_path / "trace.jsonl")]
    assert any(n.startswith(f"declared fallback used: {MODEL} -> gpt-6-luna") for n in notes)


def test_no_fallback_unless_declared(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", failing_provider([]))
    with pytest.raises(llm.LLMFailure):
        call(tmp_path)


def test_a_schema_failure_never_falls_back(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(["bad", "bad"], calls))
    monkeypatch.setitem(llm.PROVIDERS, "openai", fake_provider(['{"word": "luna"}'], calls))
    with pytest.raises(llm.LLMFailure):
        call(tmp_path, fallback="gpt-6-luna")
    assert "gpt-6-luna" not in calls


def test_fallback_is_recorded_in_the_run_manifest(runs, monkeypatch):
    from simula import cli
    from simula.runlog import read_manifest
    cli.main(["run", "janitorai", "--new"])
    run_dir = (runs / "janitorai" / "latest").resolve()
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", failing_provider([]))
    monkeypatch.setitem(llm.PROVIDERS, "openai", fake_provider(['{"word": "luna"}'], []))
    call(run_dir, trace_path=run_dir / "trace.jsonl", fallback="gpt-6-luna")
    assert read_manifest(run_dir).fallbacks_used == [f"declared fallback used: {MODEL} -> gpt-6-luna after error"]


def test_one_attempt_means_one_try_and_one_cache_key(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(["bad", '{"word": "never"}'], calls))
    with pytest.raises(llm.LLMFailure):
        call(tmp_path, attempts=1)
    assert calls == [MODEL]


class SlowStream:
    """Yields events forever; the fake clock moves 10 s per event."""
    def __init__(self):
        self.closed = self.finished = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def __iter__(self):
        while True:
            yield "event"

    def get_final_message(self):
        self.finished = True


def test_total_timeout_cancels_a_stream_that_keeps_going(monkeypatch):
    import anthropic
    stream, clock = SlowStream(), iter(range(0, 10_000, 10))
    client = type("Client", (), {"messages": type("Messages", (), {"stream": lambda self, **kw: stream})()})()
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: client)
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(clock))
    with pytest.raises(llm.LLMFailure) as failure:
        llm.call_anthropic(MODEL, "", message(), None, None, max_tokens=20_000, total_timeout=60)
    assert failure.value.outcome == "timeout"
    assert stream.closed and not stream.finished


def test_a_failed_attempt_is_recorded_and_releases_its_hold_so_a_rerun_pays_only_for_the_next(tmp_path, monkeypatch):
    first_calls = []

    def first_run(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        first_calls.append(model)
        if len(first_calls) == 1:
            return llm.Reply(text="nope", model=model, tokens_in=100, tokens_out=10)
        raise llm.LLMFailure("timeout", "stream stalled", tokens_in=100, tokens_out=5)

    budget = llm.Budget("model", 1.0)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", first_run)
    with pytest.raises(llm.LLMFailure):
        call(tmp_path, budget=budget)
    assert budget.held == pytest.approx(0)
    assert budget.spent == pytest.approx(llm.usd(MODEL, 100, 10) + llm.usd(MODEL, 100, 5))
    rerun_calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "second"}'], rerun_calls))
    result, _ = call(tmp_path, budget=budget)
    recorded = sorted(json.loads(p.read_text()).get("failure", "") for p in (tmp_path / "cache").glob("*.json"))
    assert result.word == "second" and len(rerun_calls) == 1 and recorded == ["", "schema_fail"]
    assert budget.held == pytest.approx(0)
