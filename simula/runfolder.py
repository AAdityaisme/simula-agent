"""Run ids, the latest link, done.json markers, and fixture provenance."""

import ast
import hashlib
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from simula.config import ROOT
from simula.contracts import DoneMarker, FileHash, Provenance

RUNS = ROOT / "runs"


class FixtureRefused(SystemExit):
    pass


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def git_dirty() -> bool:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return bool(out.stdout.strip())


def new_run(app: str, fixture: bool = False) -> Path:
    base = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + git_sha() + ("-fixture" if fixture else "")
    run_id, n = base, 1
    while (RUNS / app / run_id).exists():
        n += 1
        run_id = f"{base}-{n}"
    run_dir = RUNS / app / run_id
    run_dir.mkdir(parents=True)
    latest = RUNS / app / "latest"
    latest.unlink(missing_ok=True)
    latest.symlink_to(run_id)
    return run_dir


def resolve_run(app: str, run_id: str | None) -> Path:
    run_dir = RUNS / app / (run_id or "latest")
    if not run_dir.exists():
        raise SystemExit(f"no run {run_dir}; start one with `simula run {app} --new`")
    return run_dir.resolve()


def find_run(run_id: str) -> Path:
    """Resolves a bare run id to its folder, whichever app it belongs to."""
    matches = sorted(RUNS.glob(f"*/{run_id}"))
    if len(matches) != 1:
        raise SystemExit(f"run id {run_id!r} matches {len(matches)} runs under {RUNS}")
    return resolve_run(matches[0].parent.name, run_id)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


MARKERS = {"done.json", "failure.json"}


def expand(paths: list[Path]) -> list[Path]:
    files = []
    for p in paths:
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.is_file() and f.name not in MARKERS
                            and f.suffix != ".pyc" and "__pycache__" not in f.relative_to(p).parts)
        elif p.exists():
            files.append(p)
    return files


def hashes(paths: list[Path], base: Path) -> list[FileHash]:
    return [FileHash(path=_rel(f, base), sha256=sha256(f)) for f in expand(paths)]


def params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def code_files(stage: str, root: Path = ROOT) -> list[Path]:
    """The code a stage runs: its module and every simula module it imports, transitively, with the packages each
    import runs on the way in. Read from the source, not sys.modules, so a chained `simula run` and a lone
    `simula <stage>` hash the same files."""
    # ponytail: absolute imports only (the package has none relative); a relative import would escape the hash.
    todo, seen = _module_files(root, f"simula.stages.{stage}"), set()
    while todo:
        path = todo.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            else:
                continue
            todo += [f for name in names if name.split(".")[0] == "simula" for f in _module_files(root, name)]
    return sorted(seen)


def _module_files(root: Path, dotted: str) -> list[Path]:
    """The files importing `dotted` runs: each package's __init__.py on the way in, then the module. A name that is
    no module (a class imported from one) maps to a path that doesn't exist, which code_files skips."""
    parts = dotted.split(".")
    modules = [root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
    return [m / "__init__.py" if m.is_dir() else m.with_suffix(".py") for m in modules]


def _rel(path: Path, base: Path) -> str:
    for root in (base, ROOT):
        if path.resolve().is_relative_to(root.resolve()):
            return str(path.resolve().relative_to(root.resolve()))
    return str(path.resolve())


def write_json_atomic(path: Path, data: str) -> None:
    """Writes to a temp file, then renames it over the target, so a reader never sees half a file. The temp
    name is per process and thread, so two writers of one path can't interleave; a write that stops early
    removes it, so no stray temp file lands among a stage's hashed outputs."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        tmp.write_text(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_done(stage_dir: Path, run_dir: Path, inputs: list[Path], prompts: list[Path], params: dict,
               outputs: list[Path], provenance: Provenance, *, code: list[Path] | None = None) -> DoneMarker:
    (stage_dir / "failure.json").unlink(missing_ok=True)
    marker = DoneMarker(
        stage=stage_dir.name,
        input_hashes=hashes(inputs, run_dir),
        prompt_hashes=hashes(prompts, run_dir),
        params_hash=params_hash(params),
        code_hashes=hashes(code or [], run_dir),
        output_hashes=hashes(outputs, run_dir),
        provenance=provenance,
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )
    write_json_atomic(stage_dir / "done.json", marker.model_dump_json(indent=1))
    return marker


def read_done(stage_dir: Path) -> DoneMarker | None:
    path = stage_dir / "done.json"
    return DoneMarker.model_validate_json(path.read_text()) if path.exists() else None


def is_done(stage_dir: Path, run_dir: Path, inputs: list[Path], prompts: list[Path], params: dict, *,
            code: list[Path] | None = None) -> bool:
    """A stage is done while its inputs, prompts, params, code and outputs are what they were."""
    marker = read_done(stage_dir)
    if marker is None:
        return False
    same_outputs = all((run_dir / h.path).exists() and sha256(run_dir / h.path) == h.sha256
                       for h in marker.output_hashes)
    return (same_outputs
            and marker.input_hashes == hashes(inputs, run_dir)
            and marker.prompt_hashes == hashes(prompts, run_dir)
            and marker.params_hash == params_hash(params)
            and marker.code_hashes == hashes(code or [], run_dir))


def write_failure(stage_dir: Path, reason: str) -> None:
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "done.json").unlink(missing_ok=True)
    write_json_atomic(stage_dir / "failure.json", json.dumps({
        "reason": reason, "at": datetime.now().isoformat(timespec="seconds")}, indent=1))


def upstream_provenance(run_dir: Path, stages: list[str]) -> Provenance:
    """Any fixture anywhere upstream makes the output a fixture."""
    for stage in stages:
        marker = read_done(run_dir / stage)
        if marker and marker.provenance.source == "fixture":
            return marker.provenance
    return Provenance(source="explorer_run", explorer_run_id=run_dir.name)


def require_real(provenance: Provenance, allow_fixtures: bool) -> None:
    if provenance.source == "fixture" and not allow_fixtures:
        raise FixtureRefused(
            f"refusing fixture input ({provenance.fixture_path}): fixtures are test data only. "
            "Pass --allow-fixtures to run on them anyway; the output will be marked FIXTURE.")


def seed_from_fixture(run_dir: Path, stage: str, fixture: Path, allow_fixtures: bool) -> None:
    """Copies a fixture folder in as a finished stage, so later stages can be built before the explorer works."""
    provenance = Provenance(source="fixture", fixture_path=_rel(fixture, ROOT))
    require_real(provenance, allow_fixtures)
    stage_dir = run_dir / stage
    shutil.copytree(fixture, stage_dir, dirs_exist_ok=True)
    write_done(stage_dir, run_dir, [fixture], [], {"seed": str(fixture)}, [stage_dir], provenance)
