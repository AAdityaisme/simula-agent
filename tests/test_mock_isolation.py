"""Stage 3 reads model/ only: a run folder holding nothing but model/ still produces a rendered, valid mock."""

import re

import pytest

from simula import llm
from simula.contracts import ContractReport, ProductModel
from simula.runlog import read_trace
from simula.stages import Ctx, mock
from tests.conftest import APPS
from tests.mock_fake import seed_model, skeleton_html


def ctx_for(run_dir, app, profile="dev") -> Ctx:
    return Ctx(app={"name": app, "package": "x"}, run_dir=run_dir, profile=profile, no_cache=False, replay=False,
               usd_cap=None, allow_fixtures=True)


def without_edges(html: str) -> str:
    """A builder that places every element but forgets every tap: code must wire them all."""
    return re.sub(r' data-(edge|transition)="[^"]*"', "", html)


def fake_builder(calls: list):
    def call(**kwargs):
        calls.append(kwargs)
        model = ProductModel.model_validate_json((kwargs["trace_path"].parent / "model" / "product_model.json").read_text())
        return f"Here it is.\n```html\n{without_edges(skeleton_html(model))}\n```\n", None
    return call


@pytest.mark.parametrize("app", APPS)
def test_only_model_folder_still_renders(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    calls = []
    monkeypatch.setattr(llm, "call", fake_builder(calls))
    mock.run(ctx_for(run_dir, app))

    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    screens = [s.id for s in mock.pick_scope(model)]
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    assert report.passed, report.errors
    assert sorted(p.stem for p in (run_dir / "mock" / "renders").glob("*.png")) == sorted(screens)
    assert (run_dir / "exhibits" / "03-mock.md").exists()
    assert {p.name for p in run_dir.iterdir()} == {"model", "mock", "trace.jsonl", "exhibits"}

    [call] = calls
    content = call["messages"][0]["content"]
    assert sum(p["type"] == "image" for p in content) == len(screens) <= mock.MAX_SCREENS
    assert "## 7. Mock contract" in call["system"]
    assert [line.step for line in read_trace(run_dir / "trace.jsonl")] == ["scope", "contract"]
