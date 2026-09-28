"""The seven stages. Each reads its upstream stage folders and writes only its own folder."""

from dataclasses import dataclass
from pathlib import Path

UPSTREAM = {
    "explore": [],
    "model": ["explore"],
    "mock": ["model"],
    "qa": ["mock", "model"],
    "propose": ["model"],
    "judge": ["propose", "model"],
    "flows": ["qa", "judge", "propose"],
}

ROLES = {
    "explore": ["jev", "explore_vision"],
    "model": ["model_meaning"],
    "mock": ["mock_builder"],
    "qa": ["qa_critic", "qa_fixer"],
    "propose": ["proposer"],
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

