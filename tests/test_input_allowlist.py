from pathlib import Path

import pytest

from simula.config import ROOT
from simula.stages import propose
from tests.conftest import APPS
from tests.propose_fixtures import golden


@pytest.mark.parametrize("path", [
    ROOT / "reference" / "luzia-deck.png",
    ROOT / "docs" / "ASSIGNMENT.md",
    ROOT / "tests" / "fixtures" / "golden" / "luzia" / "meaning.json",
    ROOT.parent / "agent-takehome" / "notes" / "test-apps-web.md",
    ROOT / "bible" / ".." / "reference" / "deck.md",
])
def test_paths_off_the_allow_list_are_refused(path):
    with pytest.raises(PermissionError):
        propose.read_input(path)


@pytest.mark.parametrize("app", APPS)
def test_prompts_read_only_the_bible_and_propose_prompts(app, monkeypatch):
    model = golden(app)
    reads = []
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self) or original(self, *a, **k))
    system = propose.system_prompt()
    prompts = [propose.lens_prompt(model, lens) for lens in propose.build_lenses(model)]
    allowed = (ROOT / "bible", ROOT / "prompts" / "propose")
    assert reads and all(any(p.resolve().is_relative_to(a) for a in allowed) for p in reads)
    assert "Rewarded-ad bible" in system and all(model.app_category in p for p in prompts)
    assert all(item.verbatim in prompts[0] for item in model.value_ledger)
