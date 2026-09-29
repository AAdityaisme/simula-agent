"""simula <command> APP. One command per stage, plus `run` to chain them."""

import argparse
import importlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

from simula import config, runfolder, runlog
from simula.config import ROOT, STAGES
from simula.contracts import Manifest, Provenance, StageOutcome
from simula.llm import CapReached, ProviderUnavailable, ReplayMiss
from simula.stages import EXTRA_INPUTS, ROLES, UPSTREAM, Ctx

EXIT_NOT_BUILT, EXIT_CAP, EXIT_PROVIDER = 3, 4, 5


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def mobile_mcp_version() -> str | None:
    lock = ROOT / "package-lock.json"
    if not lock.exists():
        return None
    packages = json.loads(lock.read_text()).get("packages", {})
    return packages.get("node_modules/@mobilenext/mobile-mcp", {}).get("version")


def prompt_files(stage: str) -> list[Path]:
    return sorted((ROOT / "prompts" / stage).glob("*.md"))


def loader_files() -> list[Path]:
    """Every file a loader picks up by glob or folder listing rather than by exact name: the prompts, the app
    configs, and the folders stages read outside their run (EXTRA_INPUTS)."""
    extra = [ROOT / path for paths in EXTRA_INPUTS.values() for path in paths]
    return sorted({*(ROOT / "prompts").rglob("*.md"), *(ROOT / "config" / "apps").glob("*.toml"),
                   *runfolder.expand(extra)})


def untracked_inputs() -> list[str] | None:
    """The loader files in the checkout that git doesn't track, ignored ones included, or None when ROOT isn't its
    own git checkout."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    try:
        if Path(git("rev-parse", "--show-toplevel").strip()).resolve() != ROOT.resolve():
            return None
        files = [str(p.relative_to(ROOT)) for p in loader_files() if p.is_relative_to(ROOT)]
        listed = git("--literal-pathspecs", "ls-files", "--others", "-z", "--", *files)
        return sorted(p for p in listed.split("\0") if p)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def preflight() -> str | None:
    """Refuses to start while a loader could pick up a file git doesn't track, since it would change a prompt or a
    stage input with no error. Returns a note for the run's trace when there is no git checkout to check."""
    untracked = untracked_inputs()
    if untracked is None:
        return "not a git checkout: skipped the check that every file a loader reads is tracked"
    if untracked:
        raise SystemExit("refusing to start: a loader would read these files, which git doesn't track:\n"
                         + "".join(f"  {p}\n" for p in untracked)
                         + "Move each one out (or commit it); a copy named '<x> 2' is usually an iCloud conflict copy.")
    return None


def stage_params(stage: str, ctx: Ctx) -> dict:
    roles = config.roles(ctx.profile)
    return {"app": ctx.app, "profile": ctx.profile, "roles": {r: roles[r] for r in ROLES[stage]},
            "economics_mode": config.profiles()["economics_mode"] if stage in ("propose", "judge", "flows") else None,
            "no_send": ctx.no_send, "probe": ctx.probe,
            "budget": config.budget(ctx.budget) if stage == "explore" else None,
            "allow_account_create": ctx.allow_account_create if stage == "explore" else None}


def stage_inputs(stage: str, ctx: Ctx) -> list[Path]:
    return ([ROOT / "config" / "apps" / f"{ctx.app['name']}.toml"]
            + [ROOT / path for path in EXTRA_INPUTS.get(stage, [])]
            + [ctx.run_dir / up for up in UPSTREAM[stage]])


def new_manifest(run_dir: Path, app: dict, args, provenance: Provenance) -> Manifest:
    profile = args.profile
    roles = config.roles(profile)
    return Manifest(
        run_id=run_dir.name, app=app["name"], created_at=runlog.now(), git_sha=runfolder.git_sha(),
        git_dirty=runfolder.git_dirty(), profile=profile, budget=args.budget,
        allow_account_create=args.allow_account_create,
        roles={name: " ".join(str(v) for v in role.values()) for name, role in roles.items()},
        prompt_hashes={str(p.relative_to(ROOT)): runfolder.sha256(p) for p in sorted((ROOT / "prompts").rglob("*.md"))},
        app_package=app["package"], app_version=None, mobile_mcp_version=mobile_mcp_version(),
        playwright_version=package_version("playwright"), caps_usd=config.profiles()["caps_usd"],
        no_send=True, provenance=provenance, stages_done=[], usd_total=0.0,
    )


def upstream_problem(run_dir: Path, stage: str) -> str | None:
    """Why a stage can't run yet: an upstream stage that never finished, or failed after it last did."""
    for up in UPSTREAM[stage]:
        done, failure = run_dir / up / "done.json", run_dir / up / "failure.json"
        if not done.exists():
            return f"{up} is not done" + (f" (see {up}/failure.json)" if failure.exists() else f"; run `simula {up}` first")
        if failure.exists() and failure.stat().st_mtime > done.stat().st_mtime:
            return f"{up} failed after it last finished (see {up}/failure.json)"
    return None


def run_options(ctx: Ctx) -> str:
    """The run and the options it was opened with, so a printed resume command reruns it the same way."""
    return " ".join([f"--run {ctx.run_dir.name}", f"--profile {ctx.profile}", f"--budget {ctx.budget}",
                     *(["--allow-fixtures"] if ctx.allow_fixtures else []),
                     *(["--allow-account-create"] if ctx.allow_account_create else [])])


def run_stage(stage: str, ctx: Ctx, force: bool) -> bool:
    """Runs one stage unless its done.json still matches. Returns False when the stage isn't built yet."""
    stage_dir = ctx.run_dir / stage
    problem = upstream_problem(ctx.run_dir, stage)
    if problem:
        runlog.run_trace(ctx.run_dir, stage=stage, step="upstream", decider="code", outcome="blocked", note=problem)
        raise SystemExit(f"{stage} can't run: {problem}")
    provenance = runfolder.upstream_provenance(ctx.run_dir, UPSTREAM[stage])
    runfolder.require_real(provenance, ctx.allow_fixtures)
    inputs, prompts, params = stage_inputs(stage, ctx), prompt_files(stage), stage_params(stage, ctx)
    code = runfolder.code_files(stage)
    if not force and runfolder.is_done(stage_dir, ctx.run_dir, inputs, prompts, params, code=code):
        runlog.run_trace(ctx.run_dir, stage=stage, step="skip", decider="code", note="hashes match")
        return True
    module = importlib.import_module(f"simula.stages.{stage}")
    stage_dir.mkdir(exist_ok=True)
    (stage_dir / "done.json").unlink(missing_ok=True)  # the stage is being redone: its old marker no longer holds
    runlog.sync_manifest(ctx.run_dir)
    trace_path = ctx.run_dir / "trace.jsonl"
    traced_before = len(runlog.read_trace(trace_path))
    try:
        result = module.run(ctx)
    except NotImplementedError as e:
        runfolder.write_failure(stage_dir, f"not built yet ({e})")
        runlog.run_trace(ctx.run_dir, stage=stage, step="run", decider="code", outcome="not_built",
                         note=f"stub: {e}")
        print(f"stopped at {stage}: not built yet ({e})")
        return False
    except CapReached as e:
        runfolder.write_failure(stage_dir, str(e))
        runlog.needs_human(ctx.run_dir, stage, "$ cap reached", str(e), [f"{stage}/failure.json"],
                           f"{rerun_command(stage, ctx)} --usd-cap <higher>")
        raise
    except ProviderUnavailable as e:
        runfolder.write_failure(stage_dir, str(e))
        runlog.needs_human(ctx.run_dir, stage, "the model provider is refusing calls", str(e),
                           [f"{stage}/failure.json"],
                           f"simula run {ctx.app['name']} --from {stage} {run_options(ctx)}"
                           + (f" --usd-cap {ctx.usd_cap:g}" if ctx.usd_cap is not None else ""))
        raise
    except BaseException as e:
        # Every other exit, SystemExit and Ctrl-C included, still leaves a failure record; then it propagates.
        reason = f"{type(e).__name__}: {e}"
        runfolder.write_failure(stage_dir, reason)
        runlog.run_trace(ctx.run_dir, stage=stage, step="run", decider="code", outcome="error", note=reason[:300])
        raise
    traced = [line for line in runlog.read_trace(trace_path)[traced_before:] if line.stage == stage]
    outcome = finished_outcome(stage, ctx, result, [line.note for line in traced if line.outcome == "cap"])
    partial = outcome.status == "partial"
    if partial:
        runlog.needs_human(ctx.run_dir, stage, "partial output", "; ".join(outcome.reasons), [f"{stage}/done.json"],
                           outcome.resume)
    elif not any(line.step == "needs_human" and line.outcome == "blocked" for line in traced):
        # Only a clean finish resolves: a stage that asked for a person during this run still needs one.
        runlog.resolve_needs_human(ctx.run_dir, stage)
    # done.json is the commit point, written after needs-human.md: if that fails, the stage has no marker, so the next
    # run redoes it. The manifest is read from the markers, so it never lists a stage without one.
    runfolder.write_done(stage_dir, ctx.run_dir, inputs, prompts, params, [stage_dir], provenance, code=code,
                         outcome=outcome)
    runlog.sync_manifest(ctx.run_dir)
    runlog.run_trace(ctx.run_dir, stage=stage, step="done", decider="code",
                     note=provenance.source + (f"; partial: {'; '.join(outcome.reasons)}"[:300] if partial else ""))
    print(f"{stage}: done" + (" (partial: see needs-human.md)" if partial else ""))
    return True


def finished_outcome(stage: str, ctx: Ctx, result, capped: list[str]) -> StageOutcome:
    """What a stage that returned delivered: what it reported (a stage may return a StageOutcome), made partial when
    its $ cap turned work away (`capped`, the trace notes saying so), since a higher cap could change the output."""
    outcome = result if isinstance(result, StageOutcome) else StageOutcome()
    if capped:
        return StageOutcome(status="partial", reasons=[*outcome.reasons, *dict.fromkeys(capped)],
                            resume=f"{rerun_command(stage, ctx)} --usd-cap <higher>")
    if outcome.status == "partial" and not outcome.resume:
        return outcome.model_copy(update={"resume": rerun_command(stage, ctx)})
    return outcome


def rerun_command(stage: str, ctx: Ctx) -> str:
    return f"simula {stage} {ctx.app['name']} {run_options(ctx)}"


def open_run(args) -> Ctx:
    skipped = preflight()
    app = config.app_config(args.app)
    fixtures = dict(f.split("=", 1) for f in getattr(args, "fixture", None) or [])
    creating = getattr(args, "new", False) or (args.run is None and not (runfolder.RUNS / args.app / "latest").exists())
    if fixtures and not args.allow_fixtures:
        raise runfolder.FixtureRefused("--fixture needs --allow-fixtures: fixtures are test data only")
    if fixtures and not creating:
        raise runfolder.FixtureRefused("--fixture only seeds a run this call creates (add --new): "
                                       "a fixture must never enter an existing run")
    if creating:
        run_dir = runfolder.new_run(args.app, fixture=args.allow_fixtures and bool(fixtures))
        provenance = Provenance(source="fixture", fixture_path=";".join(fixtures.values())) if fixtures \
            else Provenance(source="explorer_run", explorer_run_id=run_dir.name)
        runlog.write_manifest(run_dir, new_manifest(run_dir, app, args, provenance))
        runlog.run_trace(run_dir, stage="run", step="new", decider="code", note=f"profile={args.profile}")
        print(f"new run {run_dir}")
    else:
        run_dir = runfolder.resolve_run(args.app, args.run)
    if skipped:
        runlog.run_trace(run_dir, stage="run", step="preflight", decider="code", note=skipped)
    for stage, path in fixtures.items():
        runfolder.seed_from_fixture(run_dir, stage, Path(path), args.allow_fixtures)
        runlog.run_trace(run_dir, stage=stage, step="seed", decider="human", note=f"fixture {path}")
    runlog.sync_manifest(run_dir)  # heals a manifest an earlier command failed to update
    return Ctx(app=app, run_dir=run_dir, profile=args.profile, no_cache=args.no_cache, replay=args.replay,
               usd_cap=args.usd_cap, allow_fixtures=args.allow_fixtures, budget=args.budget,
               allow_account_create=args.allow_account_create, probe=getattr(args, "probe", False))


def cmd_stage(args) -> int:
    ctx = open_run(args)
    return 0 if run_stage(args.command, ctx, force=True) else EXIT_NOT_BUILT


def cmd_run(args) -> int:
    ctx = open_run(args)
    first = STAGES.index(args.from_stage) if args.from_stage else 0
    for stage in STAGES[first:]:
        if not run_stage(stage, ctx, force=args.from_stage is not None):
            return EXIT_NOT_BUILT
    return 0


def cmd_note(args) -> int:
    run_dir = runfolder.find_run(args.run) if args.run else None
    runlog.note(args.text, args.usd, run_dir)
    print(f"noted in {(run_dir / 'trace.jsonl') if run_dir else runlog.BUILD_TRACE}")
    return 0


def cmd_later(pr: int):
    def handler(args) -> int:
        print(f"{args.command}: not built yet (PR {pr})")
        return EXIT_NOT_BUILT
    return handler


def cmd_doctor(args) -> int:
    from simula import doctor
    return doctor.main(keys=args.keys)


def add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("app")
    p.add_argument("--run", help="run id under runs/APP (default: latest)")
    p.add_argument("--profile", choices=["real", "dev"], default="real")
    p.add_argument("--no-cache", action="store_true", help="skip cache reads (writes still happen)")
    p.add_argument("--replay", action="store_true", help="cache only: no network, no device; a miss is an error")
    p.add_argument("--usd-cap", type=float, help="override this stage's $ cap")
    p.add_argument("--budget", choices=["deep", "transfer"], default="transfer",
                   help="exploration size: deep = 80 actions / 25 min, transfer = 40 / 12")
    p.add_argument("--allow-account-create", action="store_true",
                   help="let the explorer create a guest account if the app asks for one")
    p.add_argument("--allow-fixtures", action="store_true", help="accept fixture inputs (test data only)")
    p.add_argument("--fixture", action="append", metavar="STAGE=PATH",
                   help="the only way a fixture enters a run: seeds a stage folder in the run this call "
                        "creates (needs --new or no existing run, and --allow-fixtures)")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="simula", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="preflight: emulator, apps, mobile-mcp, browser, keys")
    d.add_argument("--keys", action="store_true", help="also make one tiny call per model (costs cents)")
    d.set_defaults(func=cmd_doctor)

    for stage in STAGES:
        s = sub.add_parser(stage, help=f"run the {stage} stage")
        add_run_flags(s)
        if stage == "explore":
            s.add_argument("--probe", action="store_true", help="allow the bounded chat probe (<= 8 messages)")
        s.set_defaults(func=cmd_stage)

    r = sub.add_parser("run", help="chain all seven stages; skips a stage whose hashes still match")
    add_run_flags(r)
    r.add_argument("--new", action="store_true", help="start a new run folder")
    r.add_argument("--from", dest="from_stage", choices=STAGES, help="rerun from this stage onward")
    r.set_defaults(func=cmd_run)

    for name, pr, text in [("validate-judge", 6, "judge validation report"), ("label", 6, "blind human labels"),
                           ("compare-rankers", 1, "Jev vs Haiku vs Sonnet on labeled screens")]:
        c = sub.add_parser(name, help=text)
        c.add_argument("app", nargs="?")
        c.set_defaults(func=cmd_later(pr))

    n = sub.add_parser("note", help="log a hand fix or build spend")
    n.add_argument("text")
    n.add_argument("--usd", type=float, default=0.0)
    n.add_argument("--run", metavar="ID", help="run id under runs/<app>/; default logs to build/trace.jsonl")
    n.set_defaults(func=cmd_note)
    return p


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except runfolder.FixtureRefused as e:
        print(e, file=sys.stderr)
        return 2
    except (CapReached, ReplayMiss) as e:
        print(e, file=sys.stderr)
        return EXIT_CAP
    except ProviderUnavailable as e:
        print(e, file=sys.stderr)
        return EXIT_PROVIDER


if __name__ == "__main__":
    sys.exit(main())
