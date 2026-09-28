"""Money on failure. A provider's own spend limit stops the run cleanly (CapReached: needs-human.md, exit 4),
even when it arrives as a 400. Any other API error is a typed LLMFailure. An aborted stream is charged for what
it already spent."""

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
def test_an_anthropic_usage_limit_400_is_a_cap(monkeypatch, max_tokens):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", USAGE_LIMIT))
    with pytest.raises(llm.CapReached, match="usage limit"):
        ask("anthropic", "claude-haiku-4-5-20251001", max_tokens)


def test_any_other_anthropic_400_is_a_typed_error(monkeypatch):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", "max_tokens: too large"))
    with pytest.raises(llm.LLMFailure) as failure:
        ask("anthropic", "claude-haiku-4-5-20251001")
    assert failure.value.outcome == "error"


def test_an_openai_billing_limit_400_is_a_cap(monkeypatch):
    fake_openai(monkeypatch, error_400(openai, "https://api.openai.com/v1/responses",
                                       "Billing hard limit has been reached", "billing_hard_limit_reached"))
    with pytest.raises(llm.CapReached, match="billing limit"):
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
                 system="", messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                 max_tokens=64000, budget=budget, cache_dir=tmp_path / "cache", attempts=1, total_timeout=60)
    return failure.value, budget, read_trace(tmp_path / "trace.jsonl")[-1]


def test_an_aborted_stream_is_charged_its_snapshot_with_output_at_least_what_streamed(monkeypatch, tmp_path):
    snapshot = SimpleNamespace(usage=SimpleNamespace(input_tokens=5000, output_tokens=1),
                               content=[SimpleNamespace(type="thinking", thinking="t" * 3000),
                                        SimpleNamespace(type="text", text="x" * 30_000)])
    failure, budget, line = aborted_call(monkeypatch, AbortedStream(snapshot), tmp_path)
    assert failure.outcome == "timeout" and (failure.tokens_in, failure.tokens_out) == (5000, 11_000)
    expected = llm.usd("claude-opus-5-5", 5000, 11_000)
    assert budget.spent == pytest.approx(expected) and line.usd == pytest.approx(expected, abs=1e-6)
    assert (line.tokens_in, line.tokens_out, line.outcome) == (5000, 11_000, "timeout")


def test_an_aborted_stream_without_a_snapshot_is_charged_the_worst_case(monkeypatch, tmp_path):
    failure, budget, line = aborted_call(monkeypatch, AbortedStream(), tmp_path)
    assert failure.tokens_out == 64000 and budget.spent == pytest.approx(llm.usd("claude-opus-5-5", failure.tokens_in, 64000))
    assert line.usd > 1.0


def test_a_usage_limit_stops_the_run_with_needs_human_and_exit_4(runs, tmp_path, monkeypatch):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", USAGE_LIMIT))
    explore = build("luzia", tmp_path / "explore")
    code = cli.main(["run", "luzia", "--new", "--allow-fixtures", "--fixture", f"explore={explore}", "--from", "model",
                     "--profile", "dev"])
    run_dir = (runs / "luzia" / "latest").resolve()
    assert code == cli.EXIT_CAP
    assert "$ cap reached" in (run_dir / "needs-human.md").read_text()
    assert "usage limit" in (run_dir / "model" / "failure.json").read_text()
    assert not (run_dir / "model" / "done.json").exists()
    assert read_trace(run_dir / "trace.jsonl")[-1].outcome == "blocked"
