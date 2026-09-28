import pytest

from simula import runfolder
from simula.contracts import Provenance

REAL = Provenance(source="explorer_run", explorer_run_id="r1")


@pytest.fixture
def run(tmp_path):
    (tmp_path / "config.toml").write_text("package = 'x'\n")
    (tmp_path / "prompt.md").write_text("v1")
    stage = tmp_path / "model"
    stage.mkdir()
    (stage / "product_model.json").write_text("{}")
    return tmp_path


def mark(run, params=None, stage="model", inputs=None):
    stage_dir = run / stage
    inputs = inputs or [run / "config.toml"]
    runfolder.write_done(stage_dir, run, inputs, [run / "prompt.md"], params or {"effort": "high"}, [stage_dir], REAL)
    return stage_dir, inputs


def done(run, params=None, stage="model", inputs=None):
    return runfolder.is_done(run / stage, run, inputs or [run / "config.toml"], [run / "prompt.md"],
                             params or {"effort": "high"})


def test_no_marker_means_not_done(run):
    assert not done(run)


def test_matching_hashes_skip(run):
    mark(run)
    assert done(run)


def test_changed_input_reruns(run):
    mark(run)
    (run / "config.toml").write_text("package = 'y'\n")
    assert not done(run)


def test_changed_prompt_reruns(run):
    mark(run)
    (run / "prompt.md").write_text("v2")
    assert not done(run)


def test_changed_params_rerun(run):
    mark(run)
    assert not done(run, params={"effort": "xhigh"})


def test_edited_or_deleted_output_reruns(run):
    mark(run)
    (run / "model" / "product_model.json").write_text('{"edited": true}')
    assert not done(run)
    mark(run)
    (run / "model" / "product_model.json").unlink()
    assert not done(run)


def test_rerunning_upstream_makes_downstream_stale(run):
    mark(run)
    (run / "mock").mkdir()
    (run / "mock" / "index.html").write_text("<html>")
    mark(run, stage="mock", inputs=[run / "model"])
    assert done(run, stage="mock", inputs=[run / "model"])
    (run / "model" / "product_model.json").write_text('{"new": 1}')
    assert not done(run, stage="mock", inputs=[run / "model"])


def test_half_written_marker_does_not_count(run):
    (run / "model" / "done.json.tmp").write_text("{}")
    assert not done(run)


def test_failure_replaces_done(run):
    mark(run)
    runfolder.write_failure(run / "model", "boom")
    assert not (run / "model" / "done.json").exists()
    assert (run / "model" / "failure.json").exists()


def test_same_second_runs_get_a_numbered_suffix(runs, monkeypatch):
    from datetime import datetime

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 28, 1, 2, 3)
    monkeypatch.setattr(runfolder, "datetime", Frozen)
    monkeypatch.setattr(runfolder, "git_sha", lambda: "abc1234")
    names = [runfolder.new_run("aol").name for _ in range(3)]
    assert names == ["20260928-010203-abc1234", "20260928-010203-abc1234-2", "20260928-010203-abc1234-3"]
    assert (runs / "aol" / "latest").resolve().name == names[-1]


def test_editing_a_file_outside_the_run_folder_reruns_the_stage(runs, tmp_path, monkeypatch):
    from simula import cli, stages
    knowledge = tmp_path / "bible.md"
    knowledge.write_text("v1")
    monkeypatch.setitem(stages.EXTRA_INPUTS, "propose", [str(knowledge)])
    cli.main(["run", "janitorai", "--new"])
    ctx = cli.open_run(cli.parser().parse_args(["propose", "janitorai"]))
    inputs = cli.stage_inputs("propose", ctx)
    assert knowledge in inputs
    (ctx.run_dir / "propose").mkdir()
    runfolder.write_done(ctx.run_dir / "propose", ctx.run_dir, inputs, [], {}, [ctx.run_dir / "propose"], REAL)
    assert runfolder.is_done(ctx.run_dir / "propose", ctx.run_dir, cli.stage_inputs("propose", ctx), [], {})
    knowledge.write_text("v2")
    assert not runfolder.is_done(ctx.run_dir / "propose", ctx.run_dir, cli.stage_inputs("propose", ctx), [], {})


def test_real_stage_inputs_include_the_bible_and_the_contract():
    from simula.stages import EXTRA_INPUTS
    assert EXTRA_INPUTS["propose"] == EXTRA_INPUTS["judge"] == ["bible"]
    assert EXTRA_INPUTS["mock"] == ["docs/CONTRACTS.md"]
