"""The stage wrapper: a stage refuses to run on an upstream stage that isn't done or while a loader could read a
file git doesn't track, and every way a stage can exit still leaves failure.json and a trace line."""

import importlib
import os
import subprocess
import sys

import pytest

from simula import cli, runfolder
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


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A git checkout with one tracked file in each folder a loader reads by glob, standing in for ROOT."""
    root = tmp_path / "checkout"
    for rel in ("prompts/mock/builder.md", "config/apps/janitorai.toml", "bible/BIBLE.md", "docs/CONTRACTS.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("tracked\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(cli, "ROOT", root)
    return root


def test_an_untracked_file_a_loader_reads_blocks_the_run_before_any_work(runs, checkout):
    (checkout / ".gitignore").write_text("*2.md\n")
    for rel in ("prompts/mock/builder 2.md", "bible/data/extra.json", "prompts/mock/notes.txt",
                "bible/__pycache__/breakeven.pyc"):
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        (checkout / rel).write_text("stray\n")
    with pytest.raises(SystemExit) as refused:
        cli.main(["run", "janitorai", "--new"])
    message = str(refused.value)
    assert "  bible/data/extra.json\n  prompts/mock/builder 2.md\n" in message, "an ignored copy still blocks"
    assert "notes.txt" not in message and "pycache" not in message, "no loader picks those up"
    assert not (runs / "janitorai").exists()


def test_a_modified_tracked_prompt_does_not_block(runs, checkout, mock_stage):
    (checkout / "prompts" / "mock" / "builder.md").write_text("edited while developing\n")
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    assert len(mock_stage[1]) == 1
    assert not any(line.step == "preflight" for line in read_trace(latest(runs) / "trace.jsonl"))


def test_without_a_git_checkout_the_check_is_skipped_with_a_trace_note(runs, tmp_path, monkeypatch, mock_stage):
    unzipped = tmp_path / "unzipped"
    (unzipped / "prompts" / "mock").mkdir(parents=True)
    (unzipped / "prompts" / "mock" / "builder 2.md").write_text("never checked\n")
    monkeypatch.setattr(cli, "ROOT", unzipped)
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    [note] = [line for line in read_trace(latest(runs) / "trace.jsonl") if line.step == "preflight"]
    assert (note.stage, note.outcome) == ("run", "ok") and note.note.startswith("not a git checkout: skipped")


# ---------- what a finished stage ran ----------

def rerun(run_dir, *flags):
    """`simula run`'s check of the mock: True when it ran again, False when it skipped on matching hashes."""
    ctx = cli.open_run(cli.parser().parse_args(["run", "janitorai", "--run", run_dir.name, "--allow-fixtures", *flags]))
    assert cli.run_stage("mock", ctx, force=False)
    return read_trace(run_dir / "trace.jsonl")[-1].step != "skip"


def test_the_chain_reruns_a_stage_whose_code_changed(runs, mock_stage, monkeypatch, tmp_path):
    code = tmp_path / "mock.py"
    code.write_text("BATCH = 4\n")
    monkeypatch.setattr(runfolder, "code_files", lambda stage: [code])
    run_dir = seeded_run(runs)
    assert not rerun(run_dir)
    code.write_text("BATCH = 5\n")
    assert rerun(run_dir) and len(mock_stage[1]) == 2
