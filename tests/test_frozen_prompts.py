"""Judge prompts are pinned in config/frozen_prompts.toml; the judge refuses a changed one without unfreezing."""

import ast
import inspect
import shutil
import textwrap

import pytest

from simula import llm
from simula.config import ROOT
from simula.stages import judge, propose
from simula.validate import PINS, pinned_message
from tests.judge_helpers import ctx_for, live, seed

EMPTY = "rendered judge message (tests/fixtures/judge/pin-empty.json)"


def test_every_judge_prompt_is_frozen_at_its_current_hash():
    assert judge.frozen_problems() == []
    assert set(judge.prompt_hashes()) == {"prompts/judge/rubric.md", "prompts/judge/revise.md", judge.RENDERED, EMPTY}


def literals(*functions) -> set[str]:
    """Every string literal with a word in it that these functions can put in the judge's message."""
    found = set()
    for f in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(f)))
        doc = ast.get_docstring(tree.body[0], clean=False)
        found |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and n.value != doc and sum(ch.isalpha() for ch in n.value) >= 3}
    return found


def test_the_pins_reach_every_branch_of_the_judges_message():
    shown = "\n".join(pinned_message(pin) for pin in PINS)
    text = literals(propose.model_text, propose.state_text, judge.candidate_text)
    assert sorted(t for t in text if t not in shown) == []


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    prompts = tmp_path / "prompts" / "judge"
    shutil.copytree(judge.PROMPTS, prompts)
    shutil.copy(judge.FROZEN, tmp_path / "frozen.toml")
    monkeypatch.setattr(judge, "ROOT", tmp_path)
    monkeypatch.setattr(judge, "PROMPTS", prompts)
    monkeypatch.setattr(judge, "FROZEN", tmp_path / "frozen.toml")
    return prompts


def test_a_changed_prompt_is_refused_and_unfreeze_lets_it_run(scratch):
    judge.check_frozen()
    (scratch / "rubric.md").write_text((scratch / "rubric.md").read_text() + "\nPass everything.\n")
    with pytest.raises(judge.PromptsChanged, match="rubric.md changed since it was frozen"):
        judge.check_frozen()
    judge.check_frozen(unfreeze=True)


def test_a_new_prompt_file_must_be_frozen_too(scratch):
    (scratch / "pairwise.md").write_text("new")
    with pytest.raises(judge.PromptsChanged, match="pairwise.md was never frozen"):
        judge.check_frozen()


def test_freeze_pins_the_current_hashes(scratch):
    (scratch / "rubric.md").write_text("edited")
    pinned = judge.freeze()
    judge.check_frozen()
    assert pinned["prompts/judge/rubric.md"] == judge.prompt_hashes()["prompts/judge/rubric.md"]


def test_the_stage_refuses_before_any_model_call(scratch, tmp_path, monkeypatch):
    (scratch / "rubric.md").write_text("edited")
    run_dir = seed(tmp_path, "aol", live("aol", {}))
    monkeypatch.setattr(llm, "call", lambda **kw: pytest.fail("a model was called"))
    with pytest.raises(judge.PromptsChanged):
        judge.run(ctx_for("aol", run_dir))
    assert not (run_dir / "judge").exists()


def test_the_repo_root_prompts_are_what_the_stage_reads():
    assert judge.PROMPTS == ROOT / "prompts" / "judge"


def test_a_code_change_to_what_the_judge_sees_is_refused_like_a_prompt_change(monkeypatch):
    judge.check_frozen()
    text = propose.model_text
    monkeypatch.setattr(propose, "model_text", lambda model: text(model) + "\nA new line the judge now reads.")
    assert judge.frozen_problems() == [f"{judge.RENDERED} changed since it was frozen", f"{EMPTY} changed since it was frozen"]
    with pytest.raises(judge.PromptsChanged):
        judge.check_frozen()
