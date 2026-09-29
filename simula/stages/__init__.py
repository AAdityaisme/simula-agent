"""The seven stages. Each reads its upstream stage folders and writes only its own folder."""

from dataclasses import dataclass
from pathlib import Path

UPSTREAM = {
    "explore": [],
    "model": ["explore"],
    "mock": ["model"],
    "qa": ["mock", "model"],
    "propose": ["model", "mock"],
    "judge": ["propose", "model"],
    "flows": ["qa", "judge", "propose"],
}

# Files outside the run folder that a stage reads, hashed like any input so an edit reruns the stage.
EXTRA_INPUTS = {
    "mock": ["docs/CONTRACTS.md"],
    "propose": ["bible"],
    "judge": ["bible"],
}

ROLES = {
    "explore": ["jev", "explore_vision"],
    "model": ["model_meaning"],
    "mock": ["mock_builder"],
    "qa": ["qa_critic", "qa_fixer"],
    "propose": ["proposer", "propose_dedupe"],
    "judge": ["judge_1", "judge_2", "pairwise", "proposer"],
    "flows": ["flows_editor"],
}


@dataclass
class Ctx:
    app: dict
    run_dir: Path
    profile: str
    no_cache: bool
    replay: bool
    usd_cap: float | None
    allow_fixtures: bool
    budget: str = "transfer"
    allow_account_create: bool = False
    no_send: bool = True
    probe: bool = False


def run_options(ctx: Ctx) -> str:
    """The run and the options it was opened with, so a printed resume command reruns it the same way."""
    return " ".join([f"--run {ctx.run_dir.name}", f"--profile {ctx.profile}", f"--budget {ctx.budget}",
                     *(["--allow-fixtures"] if ctx.allow_fixtures else []),
                     *(["--allow-account-create"] if ctx.allow_account_create else [])])


def rerun_command(stage: str, ctx: Ctx) -> str:
    return f"simula {stage} {ctx.app['name']} {run_options(ctx)}"

