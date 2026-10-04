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


def generate(facts, tmp_path, host=None, cache="cache"):
    return generate_variant(facts=facts, host=host or facts.hosts[0], hook="challenge", seed=7, run_dir=RUN,
                            drafts_dir=tmp_path / "drafts", budget=llm.Budget("creative", cap=6.0),
                            trace_path=tmp_path / "trace.jsonl", cache_dir=tmp_path / cache, **ONE_RUN)


def saved(tmp_path) -> dict:
    return {p: p.read_bytes() for p in (tmp_path / "drafts").rglob("*") if p.is_file()}


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


def test_a_rerun_returns_the_saved_drafts_without_a_model_call(facts, tmp_path, monkeypatch):
    seen = []
    free = {**CLEAN, "intro": "Play free: three quick ones!"}
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", replying([free, free], seen))
    first = generate(facts, tmp_path)
    files = saved(tmp_path)
    again = generate(facts, tmp_path, cache="cache-gone")
    assert len(seen) == 2 and saved(tmp_path) == files
    assert again == first and (again.first_pass_accept, again.repair_round, again.passed) == (False, 1, False)
    assert again.usd == pytest.approx(2 * llm.usd("claude-sonnet-5-5", 100, 10)) and again.seconds > 0


def test_a_rerun_after_a_failed_first_round_runs_only_the_repair_round(facts, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", replying([{**CLEAN, "intro": "Play free: three quick ones!"},
                                                               CLEAN], seen))
    generate(facts, tmp_path)
    variant = tmp_path / "drafts" / "teacher-challenge"
    for path in (variant / "round1").iterdir():
        path.unlink()
    (variant / "round1").rmdir()
    round0 = saved(tmp_path)
    seen.pop()
    result = generate(facts, tmp_path, cache="cache-gone")
    assert len(seen) == 2 and '- intro uses the banned word "free"' in seen[1]
    assert (result.first_pass_accept, result.repair_round, result.passed) == (False, 1, True)
    assert result.usd == pytest.approx(2 * llm.usd("claude-sonnet-5-5", 100, 10))
    assert result.seconds == pytest.approx(sum(d.seconds for d in result.drafts)) and result.drafts[0].seconds > 0
    assert {p: raw for p, raw in saved(tmp_path).items() if p.parent.name == "round0"} == round0


def test_a_host_the_content_cannot_hold_fails_with_both_drafts_kept(facts, tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", replying([CLEAN, CLEAN], []))
    result = generate(facts, tmp_path, host=facts.hosts[0].model_copy(update={"name": "T" * 41}))
    assert (result.passed, result.repair_round, result.final_dir) == (False, 1, None)
    assert all("Character.name: String should have at most 40 characters" in d.lines_failures for d in result.drafts)
    assert all((tmp_path / "drafts" / "teacher-challenge" / f"round{n}" / "draft.json").exists() for n in (0, 1))
