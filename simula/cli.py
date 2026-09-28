"""simula <command> APP. One command per stage, plus `run` to chain them."""

import argparse
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

from simula import config, runfolder, runlog
from simula.config import ROOT, STAGES
from simula.contracts import Manifest, Provenance
from simula.llm import CapReached, ReplayMiss
from simula.stages import ROLES, UPSTREAM, Ctx

EXIT_NOT_BUILT, EXIT_CAP = 3, 4


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


def stage_params(stage: str, ctx: Ctx) -> dict:
    roles = config.roles(ctx.profile)
    return {"app": ctx.app, "profile": ctx.profile, "roles": {r: roles[r] for r in ROLES[stage]},
            "economics_mode": config.profiles()["economics_mode"] if stage in ("propose", "judge", "flows") else None,
            "no_send": ctx.no_send, "probe": ctx.probe,
            "budget": config.budget(ctx.budget) if stage == "explore" else None,
            "allow_account_create": ctx.allow_account_create if stage == "explore" else None}


def stage_inputs(stage: str, ctx: Ctx) -> list[Path]:
    return [ROOT / "config" / "apps" / f"{ctx.app['name']}.toml"] + [ctx.run_dir / up for up in UPSTREAM[stage]]


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


def run_stage(stage: str, ctx: Ctx, force: bool) -> bool:
    """Runs one stage unless its done.json still matches. Returns False when the stage isn't built yet."""
    stage_dir = ctx.run_dir / stage
    provenance = runfolder.upstream_provenance(ctx.run_dir, UPSTREAM[stage])
    runfolder.require_real(provenance, ctx.allow_fixtures)
    inputs, prompts, params = stage_inputs(stage, ctx), prompt_files(stage), stage_params(stage, ctx)
    if not force and runfolder.is_done(stage_dir, ctx.run_dir, inputs, prompts, params):
        runlog.run_trace(ctx.run_dir, stage=stage, step="skip", decider="code", note="hashes match")
        return True
    module = importlib.import_module(f"simula.stages.{stage}")
    stage_dir.mkdir(exist_ok=True)
    try:
        module.run(ctx)
    except NotImplementedError as e:
        runfolder.write_failure(stage_dir, f"not built yet ({e})")
        runlog.run_trace(ctx.run_dir, stage=stage, step="run", decider="code", outcome="not_built",
                         note=f"stub: {e}")
        print(f"stopped at {stage}: not built yet ({e})")
        return False
    except CapReached as e:
        runfolder.write_failure(stage_dir, str(e))
        runlog.needs_human(ctx.run_dir, stage, "$ cap reached", str(e), [f"{stage}/failure.json"],
                           f"simula {stage} {ctx.app['name']} --run {ctx.run_dir.name} --usd-cap <higher>")
        raise
    runfolder.write_done(stage_dir, ctx.run_dir, inputs, prompts, params, [stage_dir], provenance)
    runlog.run_trace(ctx.run_dir, stage=stage, step="done", decider="code", note=provenance.source)
    usd_total = sum(line.usd for line in runlog.read_trace(ctx.run_dir / "trace.jsonl"))
    done = runlog.read_manifest(ctx.run_dir).stages_done
    runlog.update_manifest(ctx.run_dir, stages_done=sorted(set(done) | {stage}, key=STAGES.index),
                           usd_total=round(usd_total, 4))
    print(f"{stage}: done")
    return True


def open_run(args, stage_hint: str | None = None) -> Ctx:
    app = config.app_config(args.app)
    fixtures = dict(f.split("=", 1) for f in getattr(args, "fixture", None) or [])
    if fixtures and not args.allow_fixtures:
        raise runfolder.FixtureRefused("--fixture needs --allow-fixtures: fixtures are test data only")
    if getattr(args, "new", False) or (args.run is None and not (runfolder.RUNS / args.app / "latest").exists()):
        run_dir = runfolder.new_run(args.app, fixture=args.allow_fixtures and bool(fixtures))
        provenance = Provenance(source="fixture", fixture_path=";".join(fixtures.values())) if fixtures \
            else Provenance(source="explorer_run", explorer_run_id=run_dir.name)
        runlog.write_manifest(run_dir, new_manifest(run_dir, app, args, provenance))
        runlog.run_trace(run_dir, stage="run", step="new", decider="code", note=f"profile={args.profile}")
        print(f"new run {run_dir}")
    else:
        run_dir = runfolder.resolve_run(args.app, args.run)
    for stage, path in fixtures.items():
        runfolder.seed_from_fixture(run_dir, stage, Path(path), args.allow_fixtures)
        runlog.run_trace(run_dir, stage=stage, step="seed", decider="human", note=f"fixture {path}")
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
    run_dir = Path(args.run).resolve() if args.run else None
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
                   help="seed a stage folder from a fixture (needs --allow-fixtures)")


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
    n.add_argument("--run", help="run folder path; default logs to build/trace.jsonl")
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


if __name__ == "__main__":
    sys.exit(main())
