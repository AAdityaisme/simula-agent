import json
from types import SimpleNamespace

import httpx2
import pytest
from pydantic import BaseModel

from simula import llm, runfolder
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


def test_a_chain_the_model_answered_badly_is_never_paid_for_again_until_its_input_changes(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['nope', 'still nope'], []))
    with pytest.raises(llm.LLMFailure):
        call(tmp_path)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    for replay in (True, False):
        with pytest.raises(llm.LLMFailure) as failure:
            call(tmp_path, replay=replay)
        assert (failure.value.outcome, failure.value.raw) == ("schema_fail", "still nope")
    rerun_calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "fresh"}'], rerun_calls))
    result, _ = call(tmp_path, max_tokens=200)
    assert result.word == "fresh" and len(rerun_calls) == 1, "a changed input is a new call"


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
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    assert stage(replay=True).word == "short"
    assert stage().word == "short", "a rerun follows the recorded max_tokens to the retry's answer, and pays for neither"


def test_a_rerun_of_an_interrupted_chain_with_no_recorded_answer_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['nope'], []))
    # attempt 1 dies before answering (an untyped error, settled as a typed one), so only attempt 0 is recorded
    with pytest.raises(llm.LLMFailure, match="error: pop from empty list"):
        call(tmp_path)
    rerun_calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "first"}'], rerun_calls))
    result, _ = call(tmp_path)
    assert result.word == "first" and len(rerun_calls) == 1
    assert [line.cache_hit for line in read_trace(tmp_path / "trace.jsonl")][-1:] == [False]


def test_no_cache_skips_reads(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}', '{"word": "b"}'], calls))
    call(tmp_path)
    result, _ = call(tmp_path, no_cache=True)
    assert result.word == "b" and len(calls) == 2
    assert call(tmp_path)[0].word == "b" and len(calls) == 2, "a normal run then takes the latest answer"
    assert sorted(json.loads(p.read_text())["text"] for p in (tmp_path / "cache").glob("*.json")) == \
        ['{"word": "a"}', '{"word": "b"}'], "and the first is kept, not overwritten"


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


def test_a_broken_later_entry_does_not_block_a_recorded_first_answer(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "first"}'], []))
    call(tmp_path)
    params = {"effort": None, "max_tokens": 100, "schema": llm.json_schema_for("anthropic", Answer)}
    (tmp_path / "cache" / f"{key(params=params, attempt=1)}.json").write_text("{torn")
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    assert call(tmp_path)[0].word == "first"


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


def test_a_transport_failure_is_recorded_and_replays_under_replay(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", failing_provider([]))
    with pytest.raises(llm.LLMFailure):
        call(tmp_path)
    assert [json.loads(p.read_text())["failure"] for p in (tmp_path / "cache").glob("*.json")] == ["error", "error"]
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    with pytest.raises(llm.LLMFailure) as failure:
        call(tmp_path, replay=True)
    assert str(failure.value) == "error: 429 rate limited"


def test_replay_of_a_primary_error_gives_the_fallbacks_answer_with_no_calls(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", failing_provider([]))
    monkeypatch.setitem(llm.PROVIDERS, "openai", fake_provider(['{"word": "luna"}'], []))
    call(tmp_path, fallback="gpt-6-luna")
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    monkeypatch.setitem(llm.PROVIDERS, "openai", None)
    result, _ = call(tmp_path, fallback="gpt-6-luna", replay=True)
    assert result.word == "luna"


def test_a_timeout_is_tried_again_on_a_rerun_and_each_run_replays_its_own_path(tmp_path, monkeypatch):
    def times_out(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        raise llm.LLMFailure("timeout", "stream stalled")
    run_a, run_b = tmp_path / "a" / "trace.jsonl", tmp_path / "b" / "trace.jsonl"
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", times_out)
    with pytest.raises(llm.LLMFailure):
        call(tmp_path, trace_path=run_a, attempts=1)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    with pytest.raises(llm.LLMFailure, match="timeout"):
        call(tmp_path, trace_path=run_a, attempts=1, replay=True)
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "later"}'], calls))
    assert call(tmp_path, trace_path=run_b, attempts=1)[0].word == "later" and len(calls) == 1
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)
    with pytest.raises(llm.LLMFailure, match="timeout"):  # the new try never overwrote run A's record
        call(tmp_path, trace_path=run_a, attempts=1, replay=True)
    assert call(tmp_path, trace_path=run_b, attempts=1, replay=True)[0].word == "later"
    assert call(tmp_path, trace_path=tmp_path / "c.jsonl", attempts=1)[0].word == "later", "a rerun is now free"
    assert sorted(json.loads(p.read_text())["failure"] for p in (tmp_path / "cache").glob("*.json")) == ["", "timeout"]


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


class StalledStream(SlowStream):
    """Streams a little, then the connection goes quiet until the read timeout fires."""
    current_message_snapshot = SimpleNamespace(content=[SimpleNamespace(text="x" * 300)],
                                               usage=SimpleNamespace(input_tokens=1000, output_tokens=1))

    def __iter__(self):
        yield "event"
        raise httpx2.ReadTimeout("The read operation timed out")


def serve_stream(monkeypatch, stream):
    import anthropic
    client = type("Client", (), {"messages": type("Messages", (), {"stream": lambda self, **kw: stream})()})()
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: client)


def test_total_timeout_cancels_a_stream_that_keeps_going(monkeypatch):
    stream, clock = SlowStream(), iter(range(0, 10_000, 10))
    serve_stream(monkeypatch, stream)
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(clock))
    with pytest.raises(llm.LLMFailure) as failure:
        llm.call_anthropic(MODEL, "", message(), None, None, max_tokens=20_000, total_timeout=60)
    assert failure.value.outcome == "timeout"
    assert stream.closed and not stream.finished


def test_a_failed_attempt_is_recorded_and_releases_its_hold_and_only_a_lost_call_is_tried_again(tmp_path, monkeypatch):
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
    assert result.word == "second" and len(rerun_calls) == 1 and recorded == ["", "schema_fail", "timeout"]
    assert budget.held == pytest.approx(0)


def test_a_stalled_stream_is_a_typed_timeout_that_charges_its_worst_case(tmp_path, monkeypatch):
    serve_stream(monkeypatch, StalledStream())
    budget = llm.Budget("model", 1.0)
    with pytest.raises(llm.LLMFailure) as failure:
        call(tmp_path, budget=budget, max_tokens=20_000, attempts=1)
    worst = llm.usd(MODEL, 1000, 20_000)  # the reported input plus max_tokens, more than the ~100 tokens streamed
    assert failure.value.outcome == "timeout"
    assert budget.held == pytest.approx(0) and budget.spent == pytest.approx(worst)
    last = read_trace(tmp_path / "trace.jsonl")[-1]
    assert (last.outcome, last.usd) == ("timeout", round(worst, 6))
    entry = json.loads(next((tmp_path / "cache").glob("*.json")).read_text())
    assert (entry["failure"], entry["tokens_in"], entry["tokens_out"]) == ("timeout", 1000, 20_000)


def test_an_untyped_exit_gives_back_its_hold(tmp_path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    budget = llm.Budget("model", 1.0)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", interrupted)
    with pytest.raises(KeyboardInterrupt):
        call(tmp_path, budget=budget)
    assert budget.held == pytest.approx(0) and budget.spent == 0


def test_every_model_trace_line_names_the_cache_file_behind_it(tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"nope": 1}', '{"word": "ok"}'], []))
    call(tmp_path)
    call(tmp_path)
    stored = {p.stem[:12] for p in (tmp_path / "cache").glob("*.json")}
    notes = [line.note.split() for line in read_trace(tmp_path / "trace.jsonl")]
    assert len(notes) == 4 and all(n[0] == "key" and n[1] in stored for n in notes)


def test_a_crash_mid_write_never_leaves_a_torn_cache_file(tmp_path, monkeypatch):
    llm.cache_write("k", llm.Reply(text='{"word": "old"}', model=MODEL), tmp_path)

    def crash(src, dst):
        raise KeyboardInterrupt
    monkeypatch.setattr(runfolder.os, "replace", crash)
    with pytest.raises(KeyboardInterrupt):
        llm.cache_write("k", llm.Reply(text="x" * 100_000, model=MODEL), tmp_path)
    monkeypatch.undo()
    assert llm.cache_read("k", tmp_path).text == '{"word": "old"}'
    assert [p.name for p in tmp_path.iterdir()] == ["k.json"]


def test_a_refused_only_screenshot_is_a_traced_refusal_not_a_none_answer(tmp_path, monkeypatch):
    def refuses(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        return llm.Reply(text="", model=model, stop_reason="refusal")
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", refuses)
    with pytest.raises(llm.LLMFailure) as failure:
        llm.without_refused_images([b"\x89PNG"], lambda kept: call(tmp_path, messages=message(png=kept[0]),
                                                                    attempts=1)[0])
    assert failure.value.outcome == "refusal"
    assert [line.outcome for line in read_trace(tmp_path / "trace.jsonl")] == ["refusal"]


def test_bisecting_drops_only_refused_screenshots_and_refuses_when_none_is_left():
    def attempt(kept):
        if "bad" in kept:
            raise llm.LLMFailure("refusal", "no")
        return len(kept)
    assert llm.without_refused_images(["ok", "bad", "fine"], attempt) == (2, ["bad"])
    with pytest.raises(llm.LLMFailure, match="refusal"):
        llm.without_refused_images(["bad", "bad"], attempt)


@pytest.mark.parametrize("no_cache", [False, True], ids=["normal", "no_cache"])
def test_an_unreadable_entry_is_skipped_and_traced_and_the_next_try_goes_after_it(tmp_path, monkeypatch, no_cache):
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_provider(['{"word": "a"}', '{"word": "b"}'], calls))
    call(tmp_path)
    [entry] = (tmp_path / "cache").glob("*.json")
    entry.write_text('{"text": "{\\"word\\": ')
    result, _ = call(tmp_path, no_cache=no_cache)
    assert result.word == "b" and len(calls) == 2
    assert entry.read_text() == '{"text": "{\\"word\\": ', "the damaged entry is left as it was"
    assert call(tmp_path)[0].word == "b" and len(calls) == 2, "the next run reads the try written after it"
    [skipped] = [line for line in read_trace(tmp_path / "trace.jsonl") if line.outcome == "error"][:1]
    assert "can't be read" in skipped.note and entry.stem[:12] in skipped.note
