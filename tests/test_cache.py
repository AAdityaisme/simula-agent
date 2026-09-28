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
