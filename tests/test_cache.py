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
    def provider(model, system, messages, effort, schema, max_tokens):
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


def test_invalid_response_is_not_cached_and_retried(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"nope": 1}', '{"word": "ok"}'], calls))
    result, _ = call(tmp_path)
    assert result.word == "ok" and len(calls) == 2
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    assert [line.outcome for line in read_trace(tmp_path / "trace.jsonl")] == ["schema_fail", "ok"]


def test_two_bad_answers_raise_a_typed_failure(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['nope', 'still nope'], []))
    with pytest.raises(llm.LLMFailure) as failure:
        call(tmp_path)
    assert failure.value.outcome == "schema_fail"
    assert failure.value.raw == "still nope"
    assert not (tmp_path / "cache").exists() or not list((tmp_path / "cache").iterdir())


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
    def provider(model, system, messages, effort, schema, max_tokens):
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
