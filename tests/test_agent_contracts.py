"""The agent explorer's contracts, its settings and the --explorer switch, and account creation with no off switch."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from simula import cli, config, runfolder
from simula.contracts import AdLine, AgentStep, AgentTurn
from simula.runlog import read_manifest
from simula.stages import ROLES, explore
from tests.fake_device import new_run

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


def test_the_scripted_explorer_and_jev_on_are_the_defaults_until_the_agent_lands():
    assert (config.profiles()["explorer"], config.profiles()["explore_jev"]) == ("scripted", "on")
    assert "explore_agent" in ROLES["explore"]


@pytest.mark.parametrize("key", ["explorer", "explore_jev"])
def test_a_misspelled_setting_stops_the_run(monkeypatch, key):
    profiles = config.profiles()
    monkeypatch.setattr(config, "profiles", lambda: {**profiles, key: "agnet"})
    with pytest.raises(SystemExit, match=f"{key} must be one of"):
        config.choice(key)


def test_explorer_is_a_run_flag_and_account_creation_has_no_switch():
    assert cli.parser().parse_args(["explore", "janitorai", "--explorer", "agent"]).explorer == "agent"
    assert cli.parser().parse_args(["run", "janitorai"]).explorer == "scripted"
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


def test_an_agent_explorer_that_cant_load_leaves_the_last_explore(tmp_path, monkeypatch):
    ctx = new_run(tmp_path, explorer="agent")
    (ctx.run_dir / "explore").mkdir()
    (ctx.run_dir / "explore" / "explore.json").write_text("{}")
    monkeypatch.setitem(sys.modules, "simula.stages.explore_agent", None)
    with pytest.raises(ImportError):
        explore.run(ctx)
    assert (ctx.run_dir / "explore" / "explore.json").read_text() == "{}"


def test_explores_code_hash_will_cover_the_agent_module(tmp_path):
    """run() imports the agent explorer inside the function; the hash walks the whole syntax tree, so it sees it."""
    stages = tmp_path / "simula" / "stages"
    stages.mkdir(parents=True)
    (stages / "explore.py").write_text((config.ROOT / "simula" / "stages" / "explore.py").read_text())
    (stages / "explore_agent.py").write_text("")
    assert stages / "explore_agent.py" in runfolder.code_files("explore", tmp_path)
