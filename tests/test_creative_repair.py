import json

import pytest

from simula import llm
from simula.creative.facts import build_facts
from simula.creative.repair import generate_variant
from tests.conftest import ROOT

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
CLEAN = {"intro": "Three quick ones. Can you get them all?",
         "captions": ["Find x", "What comes next?", "Unscramble the word"], "right_line": "That's it!",
         "wrong_hint": "Not quite, try again", "end_headline": "Keep learning every day"}
ONE_RUN = {"paths": ("right",), "widths": (390,), "modes": ("fast",)}


@pytest.fixture(scope="module")
def facts():
    return build_facts(RUN)


def replying(replies: list[dict], seen: list):
    """A provider that records each request's text and answers with the next reply."""
    def provider(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        seen.append(messages[0]["content"][0]["text"])
        return llm.Reply(text=json.dumps(replies[len(seen) - 1]), model=model, tokens_in=100, tokens_out=10)
    return provider


def generate(facts, tmp_path):
    return generate_variant(facts=facts, host=facts.hosts[0], hook="challenge", seed=7, run_dir=RUN,
                            drafts_dir=tmp_path / "drafts", budget=llm.Budget("creative", cap=6.0),
                            trace_path=tmp_path / "trace.jsonl", cache_dir=tmp_path / "cache", **ONE_RUN)


def test_the_repair_call_gets_the_concrete_failures_and_both_drafts_are_kept(facts, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", replying([{**CLEAN, "intro": "Play free: three quick ones!"},
                                                               CLEAN], seen))
    result = generate(facts, tmp_path)
    assert len(seen) == 2
    assert '- intro uses the banned word "free"' in seen[1]
    assert '"intro": "Play free: three quick ones!"' in seen[1]
    assert (result.first_pass_accept, result.repair_round, result.passed) == (False, 1, True)
    assert [d.round for d in result.drafts] == [0, 1]
    variant = tmp_path / "drafts" / "teacher-challenge"
    assert (variant / "round0" / "lines.json").exists() and (variant / "round0" / "draft.json").exists()
    assert (variant / "round1" / "creative.html").exists() and result.final_dir == str(variant / "round1")
    assert result.usd == pytest.approx(2 * llm.usd("claude-sonnet-5-5", 100, 10)) and result.seconds > 0


def test_a_first_draft_that_passes_makes_no_repair_call(facts, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", replying([CLEAN], seen))
    result = generate(facts, tmp_path)
    assert len(seen) == 1
    assert (result.first_pass_accept, result.repair_round, result.passed, result.content_tier) == (True, 0, True, "sfw")
    assert not (tmp_path / "drafts" / "teacher-challenge" / "round1").exists()


def test_a_host_line_call_that_fails_twice_is_a_failed_variant(facts, tmp_path, monkeypatch):
    def failing(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        raise llm.LLMFailure("error", "provider down")
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", failing)
    result = generate(facts, tmp_path)
    assert (result.passed, result.repair_round, result.final_dir) == (False, 1, None)
    assert result.drafts[1].lines_failures == ["the host-line call failed: error"]
