"""simula <command> APP. One command per stage, plus `run` to chain them."""

import argparse
import importlib
import os
import sys
from pathlib import Path

from simula import checkout, config, runfolder, runlog
from simula.config import ROOT, STAGES
from simula.contracts import Manifest, Provenance, StageOutcome
from simula.llm import CapReached, ProviderUnavailable, ReplayMiss, split_key
from simula.stages import EXTRA_INPUTS, ROLES, UPSTREAM, Ctx, NotStarted, rerun_command, resume_command

EXIT_NOT_BUILT, EXIT_CAP, EXIT_PROVIDER = 3, 4, 5
# These parse their own options in simula.validate; `simula <command> -h` lists them.
VALIDATE_COMMANDS = {"validate-judge": "judge validation report", "label": "blind human labels"}


def prompt_files(stage: str) -> list[Path]:
    return sorted((ROOT / "prompts" / stage).glob("*.md"))


def preflight() -> str | None:
    """Refuses to start while a loader could pick up a file git doesn't track, since it would change a prompt or a
    stage input with no error. Returns a note for the run's trace when there is no git checkout to check."""
    untracked = checkout.untracked_inputs()
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
            "no_send": ctx.no_send if stage == "explore" else None,
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
        app_package=app["package"], app_version=None, mobile_mcp_version=checkout.mobile_mcp_version(),
        playwright_version=checkout.package_version("playwright"), caps_usd=config.profiles()["caps_usd"],
        no_send=args.no_send, provenance=provenance, stages_done=[], usd_total=0.0,
    )


def upstream_problem(run_dir: Path, stage: str) -> str | None:
    """Why a stage can't run yet: an upstream stage whose marker doesn't hold (runlog.read_marker, the one rule), said
    as it never finished, or failed after it last did."""
    for up in UPSTREAM[stage]:
        if runlog.read_marker(run_dir, up) is not None:
            continue
        if not (run_dir / up / "failure.json").exists():
            return f"{up} is not done; run `simula {up}` first"
        if (run_dir / up / "done.json").exists():
            return f"{up} failed after it last finished (see {up}/failure.json)"
        return f"{up} is not done (see {up}/failure.json)"
    return None


def run_stage(stage: str, ctx: Ctx, force: bool) -> bool:
    """Runs one stage unless its done.json still matches. Returns False when the stage isn't built yet."""
    stage_dir = ctx.run_dir / stage
    problem = upstream_problem(ctx.run_dir, stage)
    if problem:
        runlog.run_trace(ctx.run_dir, stage=stage, step="upstream", decider="code", outcome="blocked", note=problem)
        raise SystemExit(f"{stage} can't run: {problem}")
    provenance = runfolder.upstream_provenance(ctx.run_dir, UPSTREAM[stage])
    runfolder.require_real(provenance, ctx.allow_fixtures)
    marker = runlog.read_marker(ctx.run_dir, stage) if stage == "explore" and not force else None
    built = built_on(ctx.run_dir) if marker else []
    if marker and (marker.outcome.status == "complete" or built):
        # explore has no resume: exploring again replaces the screens every later stage was built from
        why = "a complete explore is reused as it is" if marker.outcome.status == "complete" else (
            f"a partial explore ({'; '.join(marker.outcome.reasons)}) is reused, since {', '.join(built)} "
            f"{'is' if len(built) == 1 else 'are'} built on it")
        runlog.run_trace(ctx.run_dir, stage=stage, step="skip", decider="code",
                         note=f"{why}; --new or --from explore explores again")
        return True
    inputs, prompts, params = stage_inputs(stage, ctx), prompt_files(stage), stage_params(stage, ctx)
    code = runfolder.code_files(stage)
    if not force and runfolder.is_done(runlog.read_marker(ctx.run_dir, stage), ctx.run_dir, inputs, prompts, params,
                                       code=code, accept_partial=ctx.replay):
        runlog.run_trace(ctx.run_dir, stage=stage, step="skip", decider="code", note="hashes match")
        return True
    module = importlib.import_module(f"simula.stages.{stage}")
    stage_dir.mkdir(exist_ok=True)
    # A live rerun's old marker no longer holds. --replay holds the committed one instead, and every exit that doesn't
    # write a new marker puts it back (failed), so a replay never costs a run its committed record.
    # A stage that never starts (NotStarted) puts it back too.
    marker, committed = stage_dir / "done.json", None
    try:
        committed = (marker.read_text(), marker.stat())
    except (OSError, ValueError):  # none, or one that can't be read: nothing to keep, and the stage writes anew
        pass
    if not ctx.replay:
        marker.unlink(missing_ok=True)

    def restore() -> None:
        runfolder.write_json_atomic(marker, committed[0])
        os.utime(marker, ns=(committed[1].st_atime_ns, committed[1].st_mtime_ns))

    def failed(reason: str) -> None:
        """Records why the stage stopped, in a folder the stage may have removed (QA's rmtree). Under --replay the
        committed marker goes back as it was, its time included, before the newer failure.json, so it stays on disk
        and counts as not done, in the manifest too, whose usd_total then counts what the stage spent."""
        stage_dir.mkdir(parents=True, exist_ok=True)
        if committed and ctx.replay:
            restore()
        runfolder.write_failure(stage_dir, reason)
        runlog.sync_manifest(ctx.run_dir)  # the restored marker counts as not done, and the stage's spend counts
    runlog.sync_manifest(ctx.run_dir)
    trace_path = ctx.run_dir / "trace.jsonl"
    traced_before = len(runlog.read_trace(trace_path))
    try:
        result = module.run(ctx)
        marker.unlink(missing_ok=True)  # the stage ran, so an old marker no longer describes its output
        runlog.sync_manifest(ctx.run_dir)
        traced = [line for line in runlog.read_trace(trace_path)[traced_before:] if line.stage == stage]
        capped = [split_key(line.note)[1] for line in traced if line.outcome == "cap"]
        outcome = finished_outcome(stage, ctx, result, capped)
        partial = outcome.status == "partial"
        if partial:
            runlog.needs_human(ctx.run_dir, stage, "partial output", "; ".join(outcome.reasons),
                               [f"{stage}/done.json"], outcome.resume)
        elif not any(line.step == "needs_human" and line.outcome == "blocked" for line in traced):
            # Only a clean finish resolves: a stage that asked for a person during this run still needs one.
            runlog.resolve_needs_human(ctx.run_dir, stage)
        # done.json is the commit point, written after needs-human.md: if anything before it fails, the stage has no
        # new marker, so the next run redoes it. The manifest is read from the markers, so it never lists one without.
        runfolder.write_done(stage_dir, ctx.run_dir, inputs, prompts, params, [stage_dir], provenance, code=code,
                             outcome=outcome)
    except NotImplementedError as e:
        failed(f"not built yet ({e})")
        runlog.run_trace(ctx.run_dir, stage=stage, step="run", decider="code", outcome="not_built",
                         note=f"stub: {e}")
        print(f"stopped at {stage}: not built yet ({e})")
        return False
    except CapReached as e:
        failed(str(e))
        runlog.needs_human(ctx.run_dir, stage, "$ cap reached", str(e), [f"{stage}/failure.json"],
                           resume_command(ctx, stage, e))
        raise
    except ProviderUnavailable as e:
        failed(str(e))
        runlog.needs_human(ctx.run_dir, stage, "the model provider is refusing calls", str(e),
                           [f"{stage}/failure.json"], resume_command(ctx, stage, e))
        raise
    except NotStarted as e:
        if committed:
            restore()
        runlog.sync_manifest(ctx.run_dir)
        runlog.run_trace(ctx.run_dir, stage=stage, step="run", decider="code", outcome="blocked",
                         note=f"did not start, so the last {stage} stands: {e}"[:300])
        raise SystemExit(f"{stage} did not start: {e}") from None
    except BaseException as e:
        # Every other exit, SystemExit, Ctrl-C and a failed record included, still leaves a failure record; then it
        # propagates.
        reason = f"{type(e).__name__}: {e}"
        failed(reason)
        runlog.run_trace(ctx.run_dir, stage=stage, step="run", decider="code", outcome="error", note=reason[:300])
        raise
    runlog.sync_manifest(ctx.run_dir)
    runlog.run_trace(ctx.run_dir, stage=stage, step="done", decider="code",
                     note=provenance.source + (f"; partial: {'; '.join(outcome.reasons)}"[:300] if partial else ""))
    print(f"{stage}: done" + (" (partial: see needs-human.md)" if partial else ""))
    return True


def finished_outcome(stage: str, ctx: Ctx, result, capped: list[str]) -> StageOutcome:
    """What a stage that returned delivered: what it reported (a stage may return a StageOutcome), made partial when
    its $ cap turned work away (`capped`, the trace notes saying so), since a higher cap could change the output. A
    stage that returns its own resume (QA) built it and its reasons from everything that stopped it, its cap included,
    so it is kept as the stage wrote it."""
    outcome = result if isinstance(result, StageOutcome) else StageOutcome()
    if capped and not outcome.resume:
        # Sorted: calls running together reach the cap in no fixed order, and a replay must write the same reasons.
        return StageOutcome(status="partial", reasons=[*outcome.reasons, *sorted(dict.fromkeys(capped))],
                            resume=resume_command(ctx, stage, *map(CapReached, capped)))
    if outcome.status == "partial" and not outcome.resume:
        return outcome.model_copy(update={"resume": rerun_command(stage, ctx)})
    return outcome


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
               allow_account_create=args.allow_account_create, no_send=args.no_send, device=args.device)


def built_on(run_dir: Path) -> list[str]:
    """The later stages whose marker holds, all built on this run's explore."""
    return [s for s in STAGES[1:] if runlog.read_marker(run_dir, s)]


def built_on_latest_explore(app: str) -> list[str]:
    latest = runfolder.RUNS / app / "latest"
    return built_on(latest.resolve()) if latest.exists() else []


def cmd_stage(args) -> int:
    replacing = args.command == "explore" and not args.new and args.run is None
    built = built_on_latest_explore(args.app) if replacing else []
    if built:
        print(f"the latest {args.app} run has {', '.join(built)} built on its explore, "
              "so explore won't replace it: add --new for a new run, or --run ID to replace that run's explore",
              file=sys.stderr)
        return 2
    ctx = open_run(args)
    return 0 if run_stage(args.command, ctx, force=True) else EXIT_NOT_BUILT


def cmd_run(args) -> int:
    ctx = open_run(args)
    first = STAGES.index(args.from_stage) if args.from_stage else 0
    for stage in STAGES[first:]:
        if ctx.replay and runlog.read_marker(ctx.run_dir, stage) is None:
            # A replay has only what the run recorded: a stage it never finished (an app that refused the emulator
            # stops at explore) has nothing to replay, and neither has any stage after it.
            runlog.run_trace(ctx.run_dir, stage=stage, step="replay", decider="code",
                             note="never finished in this run: the replay stops here")
            print(f"replay: {stage} never finished in this run; nothing from it on to replay")
            break
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
    p.add_argument("--no-send", action="store_true", help="explore without the core-loop pass (sends nothing)")
    p.add_argument("--device", metavar="SERIAL", help="adb serial to explore on (default: ANDROID_SERIAL, "
                                                      "else the only device online)")
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
            s.add_argument("--new", action="store_true", help="explore into a new run folder")
        s.set_defaults(func=cmd_stage)

    r = sub.add_parser("run", help="chain all seven stages; skips a stage whose hashes still match")
    add_run_flags(r)
    r.add_argument("--new", action="store_true", help="start a new run folder")
    r.add_argument("--from", dest="from_stage", choices=STAGES, help="rerun from this stage onward")
    r.set_defaults(func=cmd_run)

    for name, text in VALIDATE_COMMANDS.items():
        sub.add_parser(name, help=f"{text}; `simula {name} -h` lists its options")
    c = sub.add_parser("compare-rankers", help="Jev vs Haiku vs Sonnet on labeled screens")
    c.add_argument("app", nargs="?")
    c.set_defaults(func=cmd_later(1))

    n = sub.add_parser("note", help="log a hand fix or build spend")
    n.add_argument("text")
    n.add_argument("--usd", type=float, default=0.0)
    n.add_argument("--run", metavar="ID", help="run id under runs/<app>/; default logs to build/trace.jsonl")
    n.set_defaults(func=cmd_note)
    return p


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] and argv[0] in VALIDATE_COMMANDS:
            from simula import validate
            return validate.main(argv)
        args = parser().parse_args(argv)
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
