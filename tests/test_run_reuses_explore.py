"""The core-loop pass is on unless --no-send, the manifest records the real value, and `simula run` reuses a
finished explore as it is: only --new or --from explore explores again."""

import pytest

from simula import cli, runfolder, runlog
from simula.contracts import Coverage, Device, ExploreFile, Provenance, StageOutcome
from simula.runlog import read_manifest, read_trace
from simula.stages import model


def latest(runs):
    return (runs / "janitorai" / "latest").resolve()


def test_no_send_is_the_one_opt_out_on_explore_and_run():
    assert cli.parser().parse_args(["explore", "janitorai"]).no_send is False
    assert cli.parser().parse_args(["explore", "janitorai", "--no-send"]).no_send is True
    assert cli.parser().parse_args(["run", "janitorai", "--no-send"]).no_send is True
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["explore", "janitorai", "--probe"])


def test_the_manifest_records_no_send(runs):
    cli.main(["run", "janitorai", "--new"])
    assert read_manifest(latest(runs)).no_send is False
    cli.main(["run", "janitorai", "--new", "--no-send"])
    assert read_manifest(latest(runs)).no_send is True


def finished_explore(run_dir, outcome=None):
    explore = run_dir / "explore"
    explore.mkdir(exist_ok=True)
    coverage = Coverage(states_found=0, actions_taken=0, stop_reason="made by an earlier explore",
                        checklist_answered=[], checklist_open=[])
    (explore / "explore.json").write_text(ExploreFile(
        app_package="com.janitor.ai", app_version=None, budget="transfer", relaunches=0, content_filter=None,
        blocked_state_ids=[], coverage=coverage, device=Device()).model_dump_json())
    runfolder.write_done(explore, run_dir, [], [], {"made": "by an earlier explore"}, [explore],
                         Provenance(source="explorer_run", explorer_run_id=run_dir.name), outcome=outcome)


def not_built(ctx):
    raise NotImplementedError("stubbed: this test is about explore")


def test_run_reuses_a_finished_explore_even_when_its_params_changed(runs, monkeypatch):
    monkeypatch.setattr(model, "run", not_built)
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir)
    before = (run_dir / "explore" / "explore.json").read_text()
    assert cli.main(["run", "janitorai", "--no-send", "--budget", "deep"]) == cli.EXIT_NOT_BUILT
    assert (run_dir / "explore" / "explore.json").read_text() == before
    skip = [line for line in read_trace(run_dir / "trace.jsonl") if line.stage == "explore" and line.step == "skip"]
    assert skip and "--from explore" in skip[-1].note


def test_run_explores_again_when_the_last_explore_was_partial(runs):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir, StageOutcome(status="partial", reasons=["the core loop completed 0 of 3 passes"]))
    cli.main(["run", "janitorai"])
    trace = read_trace(run_dir / "trace.jsonl")
    assert not (run_dir / "explore" / "explore.json").exists() and trace[-1].outcome == "not_built"
    assert not any(line.stage == "explore" and line.step == "skip" for line in trace)


def model_built_on(run_dir):
    (run_dir / "model").mkdir(exist_ok=True)
    runfolder.write_done(run_dir / "model", run_dir, [], [], {}, [run_dir / "model"],
                         Provenance(source="explorer_run", explorer_run_id=run_dir.name))


def no_device_online(monkeypatch):
    from simula.device import devices
    from simula.stages import explore
    monkeypatch.setattr(explore, "resolve_serial", devices.resolve_serial)
    monkeypatch.setattr(devices, "adb", lambda: None)


def test_run_reuses_a_partial_explore_a_later_stage_was_built_on(runs, monkeypatch):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir, StageOutcome(status="partial", reasons=["the core loop completed 0 of 3 passes"]))
    model_built_on(run_dir)
    no_device_online(monkeypatch)
    ctx = cli.open_run(cli.parser().parse_args(["run", "janitorai"]))
    assert cli.run_stage("explore", ctx, force=False)  # the step `simula run` takes first, as cmd_run calls it
    assert (run_dir / "explore" / "explore.json").exists() and runlog.read_marker(run_dir, "explore")
    assert runlog.read_marker(run_dir, "model")
    skip = [line.note for line in read_trace(run_dir / "trace.jsonl") if line.stage == "explore" and line.step == "skip"]
    assert skip[-1] == ("a partial explore (the core loop completed 0 of 3 passes) is reused, since model is built on "
                        "it; --new or --from explore explores again")


def test_an_explore_with_no_device_online_leaves_the_last_explore_where_it_was(runs, monkeypatch):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir, StageOutcome(status="partial", reasons=["the core loop completed 0 of 3 passes"]))
    no_device_online(monkeypatch)
    with pytest.raises(SystemExit, match="0 devices online"):
        cli.main(["run", "janitorai"])
    assert (run_dir / "explore" / "explore.json").exists() and runlog.read_marker(run_dir, "explore")
    assert cli.upstream_problem(run_dir, "model") is None and not (run_dir / "explore" / "failure.json").exists()


def test_from_explore_with_no_device_online_leaves_the_explore_and_what_was_built_on_it(runs, monkeypatch):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir)
    model_built_on(run_dir)
    no_device_online(monkeypatch)
    with pytest.raises(SystemExit, match="explore did not start: 0 devices online"):
        cli.main(["run", "janitorai", "--from", "explore"])
    assert (run_dir / "explore" / "explore.json").exists() and runlog.complete(run_dir, "explore")
    assert cli.upstream_problem(run_dir, "model") is None and runlog.read_marker(run_dir, "model")


def test_from_explore_explores_again(runs):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir)
    cli.main(["run", "janitorai", "--from", "explore"])
    assert not (run_dir / "explore" / "explore.json").exists()
    assert read_trace(run_dir / "trace.jsonl")[-1].outcome == "not_built"


@pytest.mark.parametrize("option, one, other", [("no_send", True, False), ("explorer", "agent", "scripted"),
                                                ("explore_jev", "on", "off")])
def test_an_explore_option_changes_only_explores_params(tmp_path, option, one, other):
    from simula.stages import Ctx
    ctx = {"app": {"name": "janitorai", "package": "com.janitor.ai"}, "run_dir": tmp_path, "profile": "real",
           "no_cache": False, "replay": False, "usd_cap": None, "allow_fixtures": False}
    for stage in cli.STAGES:
        params = [cli.stage_params(stage, Ctx(**ctx, **{option: value})) for value in (one, other)]
        assert (params[0] == params[1]) == (stage != "explore"), stage


def test_explore_new_starts_a_new_run(runs):
    cli.main(["run", "janitorai", "--new"])
    first = latest(runs)
    assert cli.main(["explore", "janitorai", "--new"]) == cli.EXIT_NOT_BUILT
    assert latest(runs) != first


def test_explore_wont_replace_an_explore_later_stages_were_built_on(runs, capsys):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir)
    (run_dir / "model").mkdir()
    runfolder.write_done(run_dir / "model", run_dir, [], [], {}, [run_dir / "model"],
                         Provenance(source="explorer_run", explorer_run_id=run_dir.name))
    assert cli.main(["explore", "janitorai"]) == 2
    assert "model" in capsys.readouterr().err and (run_dir / "explore" / "explore.json").exists()
    assert cli.main(["explore", "janitorai", "--run", run_dir.name]) == cli.EXIT_NOT_BUILT
