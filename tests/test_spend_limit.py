"""A provider's own spend limit stops the run cleanly (CapReached: needs-human.md, exit 4), even when it
arrives as a 400 instead of a 429. Every other 400 still raises as it is."""

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


def test_any_other_anthropic_400_still_raises(monkeypatch):
    fake_anthropic(monkeypatch, error_400(anthropic, "https://api.anthropic.com/v1/messages", "max_tokens: too large"))
    with pytest.raises(anthropic.BadRequestError):
        ask("anthropic", "claude-haiku-4-5-20251001")


def test_an_openai_billing_limit_400_is_a_cap(monkeypatch):
    fake_openai(monkeypatch, error_400(openai, "https://api.openai.com/v1/responses",
                                       "Billing hard limit has been reached", "billing_hard_limit_reached"))
    with pytest.raises(llm.CapReached, match="billing limit"):
        ask("openai", "gpt-6-luna")


def test_any_other_openai_400_still_raises(monkeypatch):
    fake_openai(monkeypatch, error_400(openai, "https://api.openai.com/v1/responses", "Invalid schema"))
    with pytest.raises(openai.BadRequestError):
        ask("openai", "gpt-6-luna")


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
