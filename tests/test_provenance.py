"""Fixtures are test data only: a stage refuses fixture-derived input unless --allow-fixtures is passed."""

import json

import pytest

from simula import cli, runfolder
from simula.contracts import Manifest
from simula.runlog import fixture_banner, read_trace
from tests.conftest import FIXTURES

GOLDEN = str(FIXTURES / "golden" / "janitorai")


def fake_stage(name):
    def run(ctx):
        (ctx.run_dir / name / "out.txt").write_text(name)
    return run


@pytest.fixture
def built_stages(monkeypatch):
    import importlib
    for stage in ("mock", "qa", "propose", "judge", "flows"):
        monkeypatch.setattr(importlib.import_module(f"simula.stages.{stage}"), "run", fake_stage(stage))


def latest(runs):
    return (runs / "janitorai" / "latest").resolve()


def test_a_real_run_stops_at_the_first_stub(runs, capsys):
    assert cli.main(["run", "janitorai", "--new"]) == cli.EXIT_NOT_BUILT
    run_dir = latest(runs)
    manifest = Manifest.model_validate_json((run_dir / "manifest.json").read_text())
    assert manifest.provenance.source == "explorer_run" and manifest.budget == "transfer"
    last = read_trace(run_dir / "trace.jsonl")[-1]
    assert (last.stage, last.outcome) == ("explore", "not_built")
    assert "stopped at explore" in capsys.readouterr().out


def test_fixture_seed_without_flag_is_refused(runs):
    assert cli.main(["run", "janitorai", "--new", "--fixture", f"model={GOLDEN}"]) == 2
    assert not (runs / "janitorai").exists()


def test_fixture_run_is_marked_and_provenance_is_inherited(runs, built_stages):
    code = cli.main(["run", "janitorai", "--new", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--from", "mock"])
    assert code == 0
    run_dir = latest(runs)
    assert run_dir.name.endswith("-fixture")
    for stage in ("model", "mock", "qa", "propose", "judge", "flows"):
        marker = json.loads((run_dir / stage / "done.json").read_text())
        assert marker["provenance"]["source"] == "fixture", stage
    assert "FIXTURE TEST DATA" in fixture_banner(run_dir)


def test_later_stage_on_fixture_input_needs_the_flag(runs, built_stages):
    cli.main(["run", "janitorai", "--new", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--from", "mock"])
    run_id = latest(runs).name
    assert cli.main(["qa", "janitorai", "--run", run_id]) == 2
    assert cli.main(["qa", "janitorai", "--run", run_id, "--allow-fixtures"]) == 0


def test_chain_skips_a_stage_whose_hashes_match(runs, built_stages):
    cli.main(["run", "janitorai", "--new", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--from", "mock"])
    args = cli.parser().parse_args(["run", "janitorai", "--allow-fixtures"])
    ctx = cli.open_run(args)
    assert cli.run_stage("mock", ctx, force=False)
    assert read_trace(ctx.run_dir / "trace.jsonl")[-1].step == "skip"
    (ctx.run_dir / "model" / "product_model.json").write_text("{}")
    assert cli.run_stage("mock", ctx, force=False)
    assert read_trace(ctx.run_dir / "trace.jsonl")[-1].step != "skip"


def test_real_runs_are_never_suffixed(runs):
    cli.main(["run", "luzia", "--new"])
    assert not (runs / "luzia" / "latest").resolve().name.endswith("-fixture")
