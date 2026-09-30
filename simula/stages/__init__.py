"""The seven stages. Each reads its upstream stage folders and writes only its own folder."""

from dataclasses import dataclass, replace
from pathlib import Path

from simula.llm import TRANSPORT, Budget, CapReached, ProviderUnavailable

UPSTREAM = {
    "explore": [],
    "model": ["explore"],
    "mock": ["model"],
    "qa": ["mock", "model"],
    "propose": ["model", "mock"],
    "judge": ["propose", "model"],
    "flows": ["qa", "judge", "propose", "mock", "model"],
}

# Files outside the run folder that a stage reads, hashed like any input so an edit reruns the stage.
EXTRA_INPUTS = {
    "mock": ["docs/CONTRACTS.md"],
    "qa": ["docs/CONTRACTS.md"],
    "propose": ["bible"],
    "judge": ["bible"],
    "flows": ["templates", "docs/CONTRACTS.md"],
}

# Why a model call fell short, for the app's product team; the stages' own words go in a resume note.
PLAIN_FAILURE = {"refusal": "the model declined", "max_tokens": "the model's answer was cut off",
                 "timeout": "the model call timed out", "error": "the model call failed"}
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
    no_send: bool = False
    device: str | None = None


def run_options(ctx: Ctx) -> str:
    """The run and the options it was opened with, so a printed resume command reruns it the same way: every option
    a stage hashes, the opt-ins, the device, and an overridden $ cap. --no-cache and --replay are modes of one
    command, not of the run. A line that raises the cap passes a ctx with usd_cap=None and adds its own --usd-cap."""
    return " ".join([f"--run {ctx.run_dir.name}", f"--profile {ctx.profile}", f"--budget {ctx.budget}",
                     *(["--allow-fixtures"] if ctx.allow_fixtures else []),
                     *(["--allow-account-create"] if ctx.allow_account_create else []),
                     *(["--no-send"] if ctx.no_send else []),
                     *([f"--device {ctx.device}"] if ctx.device else []),
                     *([f"--usd-cap {ctx.usd_cap}"] if ctx.usd_cap is not None else [])])


def rerun_command(stage: str, ctx: Ctx) -> str:
    return f"simula {stage} {ctx.app['name']} {run_options(ctx)}"


def in_plain_words(cause: BaseException) -> str:
    """Why a stage's model call fell short, for the app's product team: over our $ budget, a named failure, or an
    answer that couldn't be used (a schema or parse failure)."""
    if isinstance(cause, CapReached):
        return "over the $ budget"
    return PLAIN_FAILURE.get(getattr(cause, "outcome", None), "the model's answer couldn't be used")


def resume_command(ctx: Ctx, stage: str, *causes: BaseException, fresh: bool = False) -> str:
    """The command that gets `stage` past what stopped it short, built from why:
    - the provider refusing the account: the run again from this stage, as it was opened;
    - our own $ cap: the stage with --usd-cap raised to the figure the cap names;
    - an answer the cache keeps (a refusal, max_tokens, an answer that failed its schema or couldn't be parsed), or
      `fresh` (the stage's own recorded answers are what fell short): a normal rerun replays it, so --no-cache;
    - a lost call (`llm.TRANSPORT`) is tried again on a rerun, so the plain stage.
    Several causes add their flags up. --no-cache asks every call again against a budget that already counts what
    the stage spent, so it comes with --usd-cap at what is spent plus a whole cap again, the same figure a cap stop
    that names none gets: every printed command runs as printed. A raised cap replaces the run's own --usd-cap."""
    if any(isinstance(c, ProviderUnavailable) for c in causes):
        return f"simula run {ctx.app['name']} --from {stage} {run_options(ctx)}"
    fresh = fresh or any(not isinstance(c, CapReached) and getattr(c, "outcome", None) not in TRANSPORT for c in causes)
    figures = [c.usd_needed for c in causes if isinstance(c, CapReached)]
    if fresh or None in figures:
        again = Budget.for_stage(stage, ctx.run_dir / "trace.jsonl", ctx.usd_cap).rerun_cap(0)
        figures = [f or again for f in figures] + ([again] if fresh else [])
    if figures:
        return rerun_command(stage, replace(ctx, usd_cap=None)) + f" --usd-cap {max(figures):.2f}" + (
            " --no-cache" if fresh else "")
    return rerun_command(stage, ctx)
