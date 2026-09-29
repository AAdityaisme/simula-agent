"""Money on failure. A provider refusing the account (a usage, spend, quota or billing limit, even as a 400) is an
outage, not our cap: ProviderUnavailable fails the stage with no done.json, needs-human.md, exit 5. Any other API
error is a typed LLMFailure. An aborted stream is charged its worst case."""

import json
import re
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from simula import cli, llm
from simula.runlog import read_trace
from tests.explore_fixture import build

USAGE_LIMIT = "You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."


def error_400(sdk, url: str, message: str, code: str | None = None):
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message, "code": code}}
    response = httpx2.Response(400, request=httpx2.Request("POST", url), json=body)
    return sdk.BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


def fake_anthropic(monkeypatch, error):
    def fail(**kwargs):
        raise error
    client = SimpleNamespace(messages=SimpleNamespace(stream=fail, with_raw_response=SimpleNamespace(create=fail)))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)


def fake_openai(monkeypatch, error):
    def fail(**kwargs):
        raise error
    client = SimpleNamespace(responses=SimpleNamespace(with_raw_response=SimpleNamespace(create=fail)))
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)


def ask(provider: str, model: str, max_tokens: int = 100):
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    return llm.PROVIDERS[provider](model, "", messages, None, None, max_tokens)


@pytest.mark.parametrize("max_tokens", [100, 64000], ids=["plain", "streamed"])
def test_an_anthropic_usage_limit_400_is_a_provider_outage_not_our_cap(monkeypatch, max_tokens):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", USAGE_LIMIT))
    with pytest.raises(llm.ProviderUnavailable, match="usage limit"):
        ask("anthropic", "claude-haiku-4-5-20251001", max_tokens)


def test_any_other_anthropic_400_is_a_typed_error(monkeypatch):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", "max_tokens: too large"))
    with pytest.raises(llm.LLMFailure) as failure:
        ask("anthropic", "claude-haiku-4-5-20251001")
    assert failure.value.outcome == "error"


def test_an_openai_billing_limit_400_is_a_provider_outage_not_our_cap(monkeypatch):
    fake_openai(monkeypatch, error_400(openai, "https://api.openai.com/v1/responses",
                                       "Billing hard limit has been reached", "billing_hard_limit_reached"))
    with pytest.raises(llm.ProviderUnavailable, match="billing limit"):
        ask("openai", "gpt-6-luna")


def test_any_other_openai_400_is_a_typed_error(monkeypatch):
    fake_openai(monkeypatch, error_400(openai, "https://api.openai.com/v1/responses", "Invalid schema"))
    with pytest.raises(llm.LLMFailure) as failure:
        ask("openai", "gpt-6-luna")
    assert failure.value.outcome == "error"


@pytest.mark.parametrize("sdk, provider, model, url", [
    (anthropic, "anthropic", "claude-haiku-4-5-20251001", "https://api.anthropic.com/v1/messages"),
    (openai, "openai", "gpt-6-luna", "https://api.openai.com/v1/responses")])
def test_server_and_connection_errors_are_typed_errors(monkeypatch, sdk, provider, model, url):
    request = httpx2.Request("POST", url)
    errors = [sdk.InternalServerError("overloaded", response=httpx2.Response(500, request=request), body=None),
              sdk.APIConnectionError(message="connection reset", request=request)]
    for error in errors:
        (fake_anthropic if provider == "anthropic" else fake_openai)(monkeypatch, error)
        with pytest.raises(llm.LLMFailure) as failure:
            ask(provider, model)
        assert failure.value.outcome == "error" and (failure.value.tokens_in, failure.value.tokens_out) == (0, 0)


def test_streamed_calls_leave_retries_to_our_attempts_loop(monkeypatch):
    seen = []

    def client(**kwargs):
        seen.append(kwargs["max_retries"])
        raise RuntimeError("stop here")
    monkeypatch.setattr(anthropic, "Anthropic", client)
    for max_tokens in (100, 64000):
        with pytest.raises(RuntimeError):
            ask("anthropic", "claude-haiku-4-5-20251001", max_tokens)
    assert seen == [2, 0]


class AbortedStream:
    """Streams events forever, like a run-away answer; the fake clock moves 10 s per event."""
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        while True:
            yield "event"

    @property
    def current_message_snapshot(self):
        assert self.snapshot is not None
        return self.snapshot


def aborted_call(monkeypatch, stream, tmp_path):
    client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: stream))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(clock))
    budget = llm.Budget("mock", 100.0)
    with pytest.raises(llm.LLMFailure) as failure:
        llm.call(trace_path=tmp_path / "trace.jsonl", stage="mock", step="t", model="claude-opus-5-5", effort="xhigh",
                 system="", messages=[{"role": "user", "content": [{"type": "text", "text": "build the mock " * 200}]}],
                 max_tokens=64000, budget=budget, cache_dir=tmp_path / "cache", attempts=1, total_timeout=60)
    return failure.value, budget, read_trace(tmp_path / "trace.jsonl")[-1]


@pytest.mark.parametrize("streamed_chars, charged_out", [(33_000, 64_000), (240_000, 80_000)],
                         ids=["less-than-max-tokens-streamed", "more-streamed"])
def test_an_aborted_stream_is_charged_its_worst_case_or_what_streamed_if_more(monkeypatch, tmp_path,
                                                                             streamed_chars, charged_out):
    """The API bills thinking a stream may not carry, so 11K streamed tokens can't bound the bill: the worst case
    does (the reported input plus max_tokens), unless what streamed was more."""
    snapshot = SimpleNamespace(usage=SimpleNamespace(input_tokens=5000, output_tokens=1),
                               content=[SimpleNamespace(type="thinking", thinking="t" * 3000),
                                        SimpleNamespace(type="text", text="x" * (streamed_chars - 3000))])
    failure, budget, line = aborted_call(monkeypatch, AbortedStream(snapshot), tmp_path)
    assert failure.outcome == "timeout" and (failure.tokens_in, failure.tokens_out) == (5000, charged_out)
    expected = llm.usd("claude-opus-5-5", 5000, charged_out)
    assert budget.spent == pytest.approx(expected) and line.usd == pytest.approx(expected, abs=1e-6)
    assert (line.tokens_in, line.tokens_out, line.outcome) == (5000, charged_out, "timeout")


def test_a_stream_that_never_started_is_charged_only_its_input(monkeypatch, tmp_path):
    failure, budget, line = aborted_call(monkeypatch, AbortedStream(), tmp_path)
    assert failure.tokens_in > 0 and failure.tokens_out == 0
    assert budget.spent == pytest.approx(llm.usd("claude-opus-5-5", failure.tokens_in, 0)) and line.usd < 0.01


class StalledStream(AbortedStream):
    """Streams one event, then the connection stalls: the SDK lets httpx2's ReadTimeout through unwrapped."""
    def __iter__(self):
        yield "event"
        raise httpx2.ReadTimeout("The read operation timed out")


def test_a_stream_that_stalls_mid_answer_is_a_charged_timeout(monkeypatch, tmp_path):
    snapshot = SimpleNamespace(usage=SimpleNamespace(input_tokens=5000, output_tokens=1),
                               content=[SimpleNamespace(type="text", text="x" * 3000)])
    failure, budget, line = aborted_call(monkeypatch, StalledStream(snapshot), tmp_path)
    assert failure.outcome == "timeout" and "timed out" in str(failure)
    assert budget.held == 0 and budget.spent == pytest.approx(llm.usd("claude-opus-5-5", 5000, 64_000))
    assert (line.outcome, line.tokens_in, line.tokens_out) == ("timeout", 5000, 64_000)


class BrokenStream(AbortedStream):
    """Streams one event, then fails with an error the SDK raises unwrapped."""
    def __init__(self, snapshot, error):
        super().__init__(snapshot)
        self.error = error

    def __iter__(self):
        yield "event"
        raise self.error


UNWRAPPED = {
    "malformed event": json.JSONDecodeError("Expecting value", "data: {", 6),
    "bad chunk encoding": httpx2.DecodingError("bad gzip"),
    "unvalidated response": anthropic.APIResponseValidationError(
        response=httpx2.Response(200, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
        body=None),
}


@pytest.mark.parametrize("error", UNWRAPPED.values(), ids=UNWRAPPED.keys())
def test_any_error_mid_stream_is_charged_traced_and_releases_its_hold(monkeypatch, tmp_path, error):
    snapshot = SimpleNamespace(usage=SimpleNamespace(input_tokens=5000, output_tokens=1),
                               content=[SimpleNamespace(type="text", text="x" * 3000)])
    failure, budget, line = aborted_call(monkeypatch, BrokenStream(snapshot, error), tmp_path)
    assert failure.outcome == "error" and budget.held == 0 and budget.spent > 0
    assert (line.decider, line.outcome, line.tokens_in) == ("model", "error", 5000) and line.usd > 0


def ask_through_call(tmp_path, budget, max_tokens: int = 100):
    return llm.call(trace_path=tmp_path / "trace.jsonl", stage="model", step="t", model="claude-haiku-4-5-20251001",
                    effort=None, system="", messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                    max_tokens=max_tokens, budget=budget, cache_dir=tmp_path / "cache", attempts=1)


def test_an_untyped_error_on_a_plain_call_is_a_typed_failure_that_releases_its_hold(monkeypatch, tmp_path):
    fake_anthropic(monkeypatch, ValueError("the SDK could not read the response"))
    budget = llm.Budget("model", 10.0)
    with pytest.raises(llm.LLMFailure, match="error: the SDK could not read the response"):
        ask_through_call(tmp_path, budget)
    assert (budget.held, budget.spent) == (0, 0) and read_trace(tmp_path / "trace.jsonl")[-1].outcome == "error"


def test_a_bug_in_our_own_code_is_raised_as_itself_after_settling_and_is_never_retried(monkeypatch, tmp_path):
    """Greptile on 5604df5: a TypeError in an adapter was retried as LLMFailure("error"), hiding its traceback."""
    calls = []

    def broken(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(model)
        raise TypeError("unexpected keyword argument 'effrt'")

    monkeypatch.setitem(llm.PROVIDERS, "anthropic", broken)
    budget = llm.Budget("model", 10.0)
    with pytest.raises(TypeError, match="effrt"):
        llm.call(trace_path=tmp_path / "trace.jsonl", stage="model", step="t", model="claude-haiku-4-5-20251001",
                 effort=None, system="", messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                 max_tokens=100, budget=budget, cache_dir=tmp_path / "cache", attempts=2,
                 fallback="claude-haiku-4-5-20251001")
    line = read_trace(tmp_path / "trace.jsonl")[-1]
    assert calls == ["claude-haiku-4-5-20251001"] and (budget.held, budget.spent) == (0, 0)
    assert line.outcome == "error" and line.note.startswith("TypeError, not a provider error")


def test_a_provider_usage_limit_stops_the_call_releases_its_hold_and_is_never_cached(monkeypatch, tmp_path):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", USAGE_LIMIT))
    budget = llm.Budget("model", 10.0)
    with pytest.raises(llm.ProviderUnavailable, match="usage limit"):
        ask_through_call(tmp_path, budget)
    assert (budget.held, budget.spent) == (0, 0) and read_trace(tmp_path / "trace.jsonl")[-1].outcome == "blocked"
    assert not (tmp_path / "cache").exists() or not list((tmp_path / "cache").iterdir())


def test_a_cap_stop_prints_a_resume_command_with_the_runs_own_options(runs, tmp_path):
    explore = build("luzia", tmp_path / "explore")
    code = cli.main(["run", "luzia", "--new", "--allow-fixtures", "--fixture", f"explore={explore}", "--from", "model",
                     "--profile", "dev", "--usd-cap", "0.000001"])
    run_dir = (runs / "luzia" / "latest").resolve()
    human = (run_dir / "needs-human.md").read_text()
    assert code == cli.EXIT_CAP and "$ cap reached" in human
    assert (f"simula model luzia --run {run_dir.name} --profile dev --budget transfer --allow-fixtures "
            "--usd-cap <higher>") in human


def test_a_usage_limit_fails_the_stage_with_needs_human_and_exit_5_so_a_rerun_resumes_there(runs, tmp_path,
                                                                                               monkeypatch):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", USAGE_LIMIT))
    explore = build("luzia", tmp_path / "explore")
    code = cli.main(["run", "luzia", "--new", "--allow-fixtures", "--fixture", f"explore={explore}", "--from", "model",
                     "--profile", "dev"])
    run_dir = (runs / "luzia" / "latest").resolve()
    assert code == cli.EXIT_PROVIDER == 5
    human = (run_dir / "needs-human.md").read_text()
    assert "the model provider is refusing calls" in human and "$ cap reached" not in human
    resume = re.search(r"simula run luzia[^`\n]*", human).group(0)
    assert resume == (f"simula run luzia --from model --run {run_dir.name} --profile dev --budget transfer "
                      "--allow-fixtures")
    # Greptile on 363456b: the printed command dropped --allow-fixtures and the profile; run it as printed.
    assert cli.main(resume.split()[1:]) == cli.EXIT_PROVIDER
    assert "usage limit" in (run_dir / "model" / "failure.json").read_text()
    assert not (run_dir / "model" / "done.json").exists()
    assert read_trace(run_dir / "trace.jsonl")[-1].outcome == "blocked"
