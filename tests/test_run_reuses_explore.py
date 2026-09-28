"""The core-loop pass is on unless --no-send, the manifest records the real value, and `simula run` reuses a
finished explore as it is: only --new or --from explore explores again."""

import pytest

from simula import cli, runfolder
from simula.contracts import Provenance
from simula.runlog import read_manifest, read_trace


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


def finished_explore(run_dir):
    explore = run_dir / "explore"
    explore.mkdir(exist_ok=True)
    (explore / "explore.json").write_text("{}")
    runfolder.write_done(explore, run_dir, [], [], {"made": "by an earlier explore"}, [explore],
                         Provenance(source="explorer_run", explorer_run_id=run_dir.name))


def test_run_reuses_a_finished_explore_even_when_its_params_changed(runs):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir)
    cli.main(["run", "janitorai", "--no-send", "--budget", "deep"])
    assert (run_dir / "explore" / "explore.json").read_text() == "{}"
    skip = [line for line in read_trace(run_dir / "trace.jsonl") if line.stage == "explore" and line.step == "skip"]
    assert skip and "--from explore" in skip[-1].note


def test_from_explore_explores_again(runs):
    cli.main(["run", "janitorai", "--new"])
    run_dir = latest(runs)
    finished_explore(run_dir)
    cli.main(["run", "janitorai", "--from", "explore"])
    assert not (run_dir / "explore" / "explore.json").exists()
    assert read_trace(run_dir / "trace.jsonl")[-1].outcome == "not_built"
