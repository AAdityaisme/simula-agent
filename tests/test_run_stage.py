"""The stage wrapper: a stage refuses to run on an upstream stage that isn't done, and every way a stage can
exit still leaves failure.json and a trace line."""

import importlib
import os
import sys

import pytest

from simula import cli
from simula.runlog import read_trace
from tests.conftest import FIXTURES

GOLDEN = FIXTURES / "golden" / "janitorai"


def latest(runs):
    return (runs / "janitorai" / "latest").resolve()


@pytest.fixture
def mock_stage(monkeypatch):
    calls = []
    stage = importlib.import_module("simula.stages.mock")
    monkeypatch.setattr(stage, "run", lambda ctx: calls.append(ctx))
    return stage, calls


def seeded_run(runs):
    """A run whose model stage is a finished fixture seed, and whose mock stage has run once."""
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"])
    return latest(runs)


def test_a_stage_refuses_an_upstream_that_never_ran(runs, mock_stage):
    _, calls = mock_stage
    with pytest.raises(SystemExit, match="mock can't run: model is not done; run `simula model` first"):
        cli.main(["run", "janitorai", "--new", "--from", "mock"])
    last = read_trace(latest(runs) / "trace.jsonl")[-1]
    assert (last.stage, last.step, last.outcome) == ("mock", "upstream", "blocked")
    assert calls == [] and not (latest(runs) / "mock" / "failure.json").exists()


def test_a_stage_refuses_an_upstream_that_failed_after_it_finished(runs, mock_stage):
    run_dir = seeded_run(runs)
    failure = run_dir / "model" / "failure.json"
    failure.write_text('{"reason": "rerun crashed"}')
    later = (run_dir / "model" / "done.json").stat().st_mtime + 5
    os.utime(failure, (later, later))
    with pytest.raises(SystemExit, match="model failed after it last finished"):
        cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"])
    assert len(mock_stage[1]) == 1


def test_a_stage_runs_once_its_upstream_is_done(runs, mock_stage):
    run_dir = seeded_run(runs)
    assert len(mock_stage[1]) == 1 and (run_dir / "mock" / "done.json").exists()


@pytest.mark.parametrize("exit_with", [lambda: sys.exit("no Android device online"),
                                       lambda: (_ for _ in ()).throw(KeyboardInterrupt())],
                         ids=["system_exit", "ctrl_c"])
def test_every_exit_leaves_a_failure_record(runs, mock_stage, monkeypatch, exit_with):
    stage, _ = mock_stage
    run_dir = seeded_run(runs)
    monkeypatch.setattr(stage, "run", lambda ctx: exit_with())
    with pytest.raises((SystemExit, KeyboardInterrupt)):
        cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"])
    reason = (run_dir / "mock" / "failure.json").read_text()
    assert "SystemExit: no Android device online" in reason or "KeyboardInterrupt" in reason
    assert not (run_dir / "mock" / "done.json").exists()
    last = read_trace(run_dir / "trace.jsonl")[-1]
    assert (last.stage, last.step, last.outcome) == ("mock", "run", "error")
