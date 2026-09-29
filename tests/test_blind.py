"""Judges are blind to authorship: no model or lens names, an opaque id, a fixed field order, one candidate
per call, and nothing from propose's bookkeeping (drop reason, cost line, rank)."""

import re

import pytest

from simula import config, llm
from simula.stages import judge, propose
from tests.conftest import APPS
from tests.judge_helpers import idea, verdict
from tests.propose_fixtures import golden

MODEL_NAMES = re.compile(r"claude|sonnet|opus|haiku|gpt|anthropic|openai|luna|\bsol\b", re.I)
FIELD_ORDER = ["id:", "title:", "kind:", "cited evidence:", "what makes it work here:", "adds:",
               "removes nothing free:", "trigger event:", "trigger screen:", "placement:", "offer copy:", "reward:",
               "offered to:", "piece of a paid benefit:", "frequency cap:", "if the user declines:",
               "if the ad fails:", "subscribers:", "advertiser category:", "characters in the ad moment:", "flow:",
               "when the reward runs out:", "rationale:"]


def prompt(c, model) -> str:
    return judge.read_prompt("rubric.md") + "\n" + judge.judge_messages(c, model)[0]["content"][0]["text"]


def lenses(model):
    return propose.build_lenses(model)


@pytest.mark.parametrize("app", APPS)
def test_no_lens_or_model_name_and_an_opaque_id(app):
    model = golden(app)
    for lens in lenses(model):
        c = idea(model, "c07", lens=lens.id)
        text = prompt(c, model)
        assert "lens" not in text.lower() and lens.focus not in text
        assert "_" not in lens.id or lens.id not in text
        assert not MODEL_NAMES.search(text)
        assert "c07" not in text and f"id: {judge.opaque_id(c)}" in text


@pytest.mark.parametrize("app", APPS)
def test_the_same_proposal_reads_the_same_whoever_wrote_it(app):
    model = golden(app)
    first, other = lenses(model)[0], lenses(model)[-1]
    a = idea(model, "c01", lens=first.id, rank=5)
    b = idea(model, "c09", lens=other.id, rank=0.25, econ="FAIL", dropped_reason="duplicate of c01")
    assert judge.judge_messages(a, model) == judge.judge_messages(b, model)


@pytest.mark.parametrize("app", APPS)
def test_propose_bookkeeping_never_reaches_a_judge(app):
    model = golden(app)
    c = idea(model, dropped_reason="MARKER-DROP", bible_mechanic="M11")
    text = prompt(c, model)
    assert "MARKER-DROP" not in text and "M11" not in text
    assert c.economics.assumption_line not in text and "eCPM" not in text.split("## Proposal")[1]


@pytest.mark.parametrize("app", APPS)
def test_code_flags_are_shown_once_under_their_heading_and_change_nothing_else(app):
    model = golden(app)
    plain = idea(model)
    flagged = plain.model_copy(update={"flags": ["MARKER-FLAG"]})
    assert prompt(flagged, model) == prompt(plain, model) + (
        "\nCode flags (rule on them under the existing checks):\n- MARKER-FLAG")


@pytest.mark.parametrize("app", APPS)
def test_fields_come_in_a_fixed_order(app):
    model = golden(app)
    text = judge.candidate_text(idea(model), model)
    starts = [next(i for i, line in enumerate(text.splitlines()) if line.startswith(label)) for label in FIELD_ORDER]
    assert starts == sorted(starts)


@pytest.mark.parametrize("app", APPS)
def test_cited_evidence_is_quoted_and_a_made_up_id_is_flagged(app):
    model = golden(app)
    element = next(e for s in model.states for e in s.elements if e.text)
    c = idea(model, anchor_evidence_ids=[element.id, "s99.e99"])
    text = judge.candidate_text(c, model)
    assert f'"{element.text}"' in text and "s99.e99: not in the product model" in text


def test_one_candidate_per_call(tmp_path, monkeypatch):
    model = golden("janitorai")
    cands = [idea(model, f"c0{n}", title=f"Idea {n}") for n in range(1, 4)]
    seen = []

    def call(**kw):
        seen.append(kw["messages"][0]["content"][0]["text"])
        return verdict(), None
    monkeypatch.setattr(llm, "call", call)
    ctx = type("Ctx", (), dict(run_dir=tmp_path, profile="dev", no_cache=False, replay=False))
    (tmp_path / "judge" / "verdicts").mkdir(parents=True)
    budget = llm.Budget("judge", 1.0)
    verdicts, _ = judge.judge_all(ctx, tmp_path / "judge", cands, model, ["judge_1"], budget, 1)
    assert len(seen) == 3 and all(t.count("## Proposal") == 1 for t in seen)
    assert all(sum(f"title: {c.title}" in t for c in cands) == 1 for t in seen)
    assert {cid: list(v) for cid, v in verdicts.items()} == {c.id: ["judge_1"] for c in cands}


def test_both_judges_get_the_same_rubric_and_no_other_system_prompt(monkeypatch):
    model = golden("luzia")
    systems = []
    monkeypatch.setattr(llm, "call", lambda **kw: systems.append((kw["model"], kw["system"])) or (verdict(), None))
    for role in ("judge_1", "judge_2"):
        judge.ask_judge(config.roles("real")[role], idea(model), model, trace_path=None, stage="judge", step="t",
                        budget=None)
    assert systems[0][1] == systems[1][1] == judge.read_prompt("rubric.md")
    assert systems[0][0] != systems[1][0]


def test_cited_evidence_on_an_unsafe_screen_is_not_quoted():
    model = golden("janitorai")
    state = next(s for s in model.states if any(e.text for e in s.elements))
    element = next(e for e in state.elements if e.text)
    unsafe = model.model_copy(update={"states": [s.model_copy(update={"content_rating": "unsafe"}) if s.id == state.id
                                                 else s for s in model.states]})
    text = judge.candidate_text(idea(unsafe, anchor_evidence_ids=[element.id]), unsafe)
    assert f"{element.id} on {state.id}" in text and f'"{element.text}"' not in text.split("cited evidence:")[1].split("what makes")[0]
