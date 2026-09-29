"""The seven stages. Each reads its upstream stage folders and writes only its own folder."""

from dataclasses import dataclass
from pathlib import Path

from simula.llm import Budget, CapReached, ProviderUnavailable

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

# Failures llm never replays on a normal run (lost calls), so a plain rerun calls again.
TRANSIENT = ("timeout", "error")
FRESH_CALLS = ("--no-cache asks every model call of the stage again, not only the one that fell short, and the same "
               "input may be answered the same way.")

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


def resume_command(ctx: Ctx, stage: str, *causes: BaseException, fresh: bool = False) -> str:
    """The command that gets `stage` past what stopped it short, built from why:
    - the provider refusing the account: the run again from this stage, as it was opened;
    - our own $ cap: the stage with --usd-cap raised to the figure the cap names;
    - an answer the cache keeps (a refusal, max_tokens, an answer that failed its schema or couldn't be parsed), or
      `fresh` (the stage's own recorded answers are what fell short): a normal rerun replays it, so --no-cache;
    - a lost call (`TRANSIENT`) isn't replayed, so the plain stage.
    Several causes add their flags up. --no-cache asks every call again against a budget that already counts what
    the stage spent, so it comes with --usd-cap at what is spent plus a whole cap again, the same figure a cap stop
    that names none gets: every printed command runs as printed."""
    if any(isinstance(c, ProviderUnavailable) for c in causes):
        return (f"simula run {ctx.app['name']} --from {stage} {run_options(ctx)}"
                + (f" --usd-cap {ctx.usd_cap:g}" if ctx.usd_cap is not None else ""))
    fresh = fresh or any(not isinstance(c, CapReached) and getattr(c, "outcome", None) not in TRANSIENT for c in causes)
    figures = [c.usd_needed for c in causes if isinstance(c, CapReached)]
    if fresh or None in figures:
        again = Budget.for_stage(stage, ctx.run_dir / "trace.jsonl", ctx.usd_cap).rerun_cap(0)
        figures = [f or again for f in figures] + ([again] if fresh else [])
    command = rerun_command(stage, ctx)
    if figures:
        command += f" --usd-cap {max(figures):.2f}"
    return command + (" --no-cache" if fresh else "")
