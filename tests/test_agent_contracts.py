"""The agent explorer's contracts, its settings and the --explorer switch, and account creation with no off switch."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from simula import cli, config, runfolder, stages
from simula.contracts import AdLine, AgentStep, AgentTurn
from simula.runlog import read_manifest
from simula.stages import ROLES, explore
from tests.fake_device import new_run
from tests.test_run_reuses_explore import finished_explore

TAP = {"action": "tap", "element": "e3", "expect": "a character's page opens"}


@pytest.mark.parametrize("n", [0, 6])
def test_a_turn_holds_one_to_five_steps(n):
    with pytest.raises(ValidationError):
        AgentTurn(screen="home", goal="find the core action", steps=[TAP] * n)
    assert len(AgentTurn(screen="home", goal="find the core action", steps=[TAP] * 5).steps) == 5


def test_start_core_names_no_recipient_unless_the_planner_does():
    assert AgentStep(action="start_core", element="e9", expect="a reply appears").recipient == "none"


def test_an_ad_line_round_trips():
    line = AdLine(step=4, state="s03", format="rewarded", advertiser="a game", tapped=True, landing="a.package")
    assert AdLine.model_validate_json(line.model_dump_json()) == line


def test_the_agent_explorer_and_jev_on_are_the_defaults():
    assert (config.profiles()["explorer"], config.profiles()["explore_jev"]) == ("agent", "on")
    assert "explore_agent" in ROLES["explore"]


@pytest.mark.parametrize("key", ["explorer", "explore_jev"])
def test_a_misspelled_setting_stops_the_run(monkeypatch, key):
    profiles = config.profiles()
    monkeypatch.setattr(config, "profiles", lambda: {**profiles, key: "agnet"})
    with pytest.raises(SystemExit, match=f"{key} must be one of"):
        config.choice(key)


def test_explorer_is_a_run_flag_and_account_creation_has_no_switch():
    assert cli.parser().parse_args(["explore", "janitorai", "--explorer", "agent"]).explorer == "agent"
    assert cli.parser().parse_args(["run", "janitorai"]).explorer == "agent"
    for flags in (["--allow-account-create"], ["--explorer", "llm"]):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(["explore", "janitorai", *flags])


def test_a_new_run_records_account_creation_on(runs):
    cli.main(["run", "janitorai", "--new"])
    assert read_manifest((runs / "janitorai" / "latest").resolve()).allow_account_create is True


class Built(Exception):
    """The explorer class run() picked was constructed."""


@pytest.mark.parametrize("explorer", ["scripted", "agent"])
def test_the_switch_builds_its_explorer(tmp_path, monkeypatch, explorer):
    built = []

    def kind(name):
        def construct(*args):
            built.append(name)
            raise Built
        return construct
    monkeypatch.setattr(explore, "Explorer", kind("scripted"))
    monkeypatch.setitem(sys.modules, "simula.stages.explore_agent", SimpleNamespace(AgentExplorer=kind("agent")))
    monkeypatch.setattr(explore, "Server", lambda cwd: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(explore, "Phone", lambda *args: None)
    monkeypatch.setattr(explore.signal, "signal", lambda *args: None)
    with pytest.raises(Built):
        explore.run(new_run(tmp_path, explorer=explorer))
    assert built == [explorer]


@pytest.mark.parametrize("load", ["missing", "raises"])
def test_an_agent_explorer_that_cant_load_leaves_the_last_explore(runs, tmp_path, monkeypatch, load):
    """Greptile on 250ec6e (a missing module) and the red team on f6e9ba5 (one that raises while it loads): the stage
    runner drops explore's marker before the stage runs, and only a stage that didn't start puts it back."""
    cli.main(["run", "janitorai", "--new"])
    run_dir = (runs / "janitorai" / "latest").resolve()
    finished_explore(run_dir)
    before = (run_dir / "explore" / "done.json").read_text()
    if load == "missing":
        monkeypatch.setitem(sys.modules, "simula.stages.explore_agent", None)
    else:
        (tmp_path / "agent").mkdir()
        (tmp_path / "agent" / "explore_agent.py").write_text("raise RuntimeError('agent initialization failed')\n")
        monkeypatch.delitem(sys.modules, "simula.stages.explore_agent", raising=False)
        monkeypatch.setattr(stages, "__path__", [str(tmp_path / "agent"), *stages.__path__])
    with pytest.raises(SystemExit, match="explore did not start: the agent explorer can't load"):
        cli.main(["explore", "janitorai", "--run", run_dir.name, "--explorer", "agent"])
    assert (run_dir / "explore" / "done.json").read_text() == before
    assert not (run_dir / "explore" / "failure.json").exists() and cli.upstream_problem(run_dir, "model") is None


def test_explores_code_hash_will_cover_the_agent_module(tmp_path):
    """run() imports the agent explorer inside the function; the hash walks the whole syntax tree, so it sees it."""
    stages = tmp_path / "simula" / "stages"
    stages.mkdir(parents=True)
    (stages / "explore.py").write_text((config.ROOT / "simula" / "stages" / "explore.py").read_text())
    (stages / "explore_agent.py").write_text("")
    assert stages / "explore_agent.py" in runfolder.code_files("explore", tmp_path)
