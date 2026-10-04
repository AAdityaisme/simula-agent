import json

import pytest

from simula import llm
from simula.creative.facts import build_facts
from simula.creative.lines import CTA, HostLines, build_content, problems, write_lines
from simula.creative.puzzles import puzzles
from simula.runlog import read_trace
from tests.conftest import ROOT

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
CLEAN = {"intro": "Three quick ones. Can you get them all?",
         "captions": ["Find x", "What comes next?", "Unscramble the word"], "right_line": "That's it!",
         "wrong_hint": "Not quite, try again", "end_headline": "Keep learning every day"}


@pytest.fixture(scope="module")
def facts():
    return build_facts(RUN)


def host(facts, host_id="teacher"):
    return next(h for h in facts.hosts if h.id == host_id)


def recorded(reply: dict, seen: list):
    """A provider that records each request's text and answers with the recorded reply."""
    def provider(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        seen.append(messages[0]["content"][0]["text"])
        return llm.Reply(text=json.dumps(reply), model=model, tokens_in=100, tokens_out=10)
    return provider


def call(tmp_path, facts, **extra):
    return write_lines(facts=facts, host=host(facts), hook="challenge", puzzles=puzzles(7),
                       budget=llm.Budget("creative", cap=6.0), trace_path=tmp_path / "trace.jsonl",
                       cache_dir=tmp_path / "cache", **extra)


@pytest.mark.parametrize("field, value, flag", [
    ("intro", "x" * 91, "intro is 91 characters; the limit is 90"),
    ("right_line", "It's free to keep going!", 'right_line uses the banned word "free"'),
    ("end_headline", "Grow your own Toki today", 'end_headline names the app feature "Toki"'),
])
def test_a_recorded_reply_with_a_bad_line_is_flagged(tmp_path, monkeypatch, facts, field, value, flag):
    seen = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", recorded({**CLEAN, field: value}, seen))
    lines = call(tmp_path, facts)
    assert flag in problems(lines, facts, host(facts), puzzles(7))
    assert len(seen) == 1 and '"forbidden_terms"' in seen[0] and "Hello! I am Teacher" in seen[0]


def test_a_clean_reply_passes_and_builds_the_content(tmp_path, monkeypatch, facts):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", recorded(CLEAN, []))
    lines = call(tmp_path, facts)
    assert problems(lines, facts, host(facts), puzzles(7)) == []
    content = build_content(facts, host(facts), "challenge", 7, lines)
    assert (content.variant_id, content.cta, content.proof.screen_id) == ("teacher-challenge", CTA, "s15")
    assert [(c.evidence_id, c.text) for c in content.proof.claims] == [
        ("s15.e02", "I am Teacher, your personal tutor."),
        ("s15.e02", "I'm here to resolve your doubts, clarify those difficult concepts")]
    assert content.copy_.intro == CLEAN["intro"] and content.puzzles == puzzles(7)
    assert content.model_dump()["copy"]["intro"] == CLEAN["intro"]
    assert [line.stage for line in read_trace(tmp_path / "trace.jsonl")] == ["creative"]


def test_the_repair_call_carries_the_previous_answer_and_every_failure(tmp_path, monkeypatch, facts):
    seen = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", recorded(CLEAN, seen))
    call(tmp_path, facts, previous=HostLines(**{**CLEAN, "intro": "x" * 91}),
         failures=["intro is 91 characters; the limit is 90", 'right_line uses the banned word "free"'])
    assert "It failed these checks" in seen[0]
    assert "- intro is 91 characters; the limit is 90" in seen[0] and '- right_line uses the banned word "free"' in seen[0]
    assert "x" * 91 in seen[0]


def test_toki_may_say_its_own_name_but_teacher_may_not(facts):
    lines = HostLines(**{**CLEAN, "intro": "Toki needs your help with three quick ones!"})
    assert problems(lines, facts, host(facts, "toki"), puzzles(7)) == []
    assert 'intro names the app feature "Toki"' in problems(lines, facts, host(facts), puzzles(7))


def test_a_caption_or_hint_that_gives_away_an_answer_is_flagged(facts):
    ps = puzzles(7)
    word, x = ps[2].options[ps[2].answer], ps[0].options[ps[0].answer]
    lines = HostLines(**{**CLEAN, "captions": ["Find x", "What comes next?", f"Hint: it is {word.lower()}"],
                         "wrong_hint": f"Try {x}"})
    found = problems(lines, facts, host(facts), ps)
    assert f"caption 3 gives away the answer {word}" in found
    assert f"wrong_hint gives away the answer {x}" in found


@pytest.mark.parametrize("field", ["intro", "right_line", "caption 2"])
def test_a_line_shown_before_the_word_puzzle_that_names_its_answer_is_flagged(facts, field):
    ps = puzzles(7)
    assert [p.options[p.answer] for p in ps] == ["3", "30", "CHALK"]
    update = ({"captions": ["Find x", "The final word is chalk!", "Unscramble the word"]} if field == "caption 2"
              else {field: "The final word is chalk!"})
    found = problems(HostLines(**{**CLEAN, **update}), facts, host(facts), ps)
    assert f"{field} gives away the answer CHALK" in found


def test_a_line_shown_only_after_a_puzzle_is_solved_may_name_its_answer(facts):
    lines = HostLines(**{**CLEAN, "captions": ["Find x", "What comes next?", "You found 3 and 30!"],
                         "right_line": "Yes, 3!", "end_headline": "The word was chalk"})
    assert problems(lines, facts, host(facts), puzzles(7)) == []


@pytest.mark.parametrize("field, value, answer", [
    ("caption 1", "The answer is three", "3"),
    ("wrong_hint", "Try three", "3"),
    ("caption 2", "Is it thirty?", "30"),
    ("intro", "Here is a clue: x is three", "3"),
    ("right_line", "Next one equals thirty", "30"),
])
def test_a_numeric_answer_spelled_out_is_flagged(facts, field, value, answer):
    lines = HostLines(**{**CLEAN, **({"captions": [value if i == int(field[-1]) - 1 else c
                                                    for i, c in enumerate(CLEAN["captions"])]}
                                     if field.startswith("caption") else {field: value})})
    assert f"{field} gives away the answer {answer}" in problems(lines, facts, host(facts), puzzles(7))


def test_a_number_word_that_does_not_read_as_an_answer_passes_in_the_intro_and_right_line(facts):
    lines = HostLines(**{**CLEAN, "intro": "Three quick ones. Can you get all three?",
                         "right_line": "Three for three!"})
    assert problems(lines, facts, host(facts), puzzles(7)) == []


@pytest.mark.parametrize("term", ["Custom  Bestie", "Custom\nBestie", "Custom\tBestie", "Custom-Bestie"])
def test_a_feature_term_with_other_whitespace_is_still_flagged(facts, term):
    lines = HostLines(**{**CLEAN, "end_headline": f"Meet your {term}"})
    assert 'end_headline names the app feature "Custom Bestie"' in problems(lines, facts, host(facts), puzzles(7))


@pytest.mark.parametrize("intro, right_line", [
    ("The answer is exactly three.", "The next answer is exactly thirty."),
    ("Try the number three.", "Try the number thirty next."),
    ("x equals precisely three.", "The next answer equals precisely thirty."),
    ("x = exactly three.", "Next = exactly thirty."),
])
def test_a_cue_then_up_to_two_ordinary_words_before_a_number_word_still_gives_it_away(facts, intro, right_line):
    found = problems(HostLines(**{**CLEAN, "intro": intro, "right_line": right_line}), facts, host(facts), puzzles(7))
    assert "intro gives away the answer 3" in found and "right_line gives away the answer 30" in found


@pytest.mark.parametrize("field", ["intro", "right_line"])
@pytest.mark.parametrize("number", ["twenty-one", "twenty four", "twenty-five"])
def test_a_different_complete_number_does_not_give_away_its_tens_answer(facts, field, number):
    ps = puzzles(70)
    assert [p.options[p.answer] for p in ps] == ["5", "20", "CIRCLE"]
    lines = HostLines(**{**CLEAN, field: f"Your goal is {number} seconds. Ready?"})
    assert problems(lines, facts, host(facts), ps) == []


def test_a_ones_word_inside_a_larger_number_does_not_give_away_its_answer(facts):
    lines = HostLines(**{**CLEAN, "wrong_hint": "Not twenty three, try again"})
    assert problems(lines, facts, host(facts), puzzles(7)) == []
