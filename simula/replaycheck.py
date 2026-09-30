"""Replays each committed run in a throwaway `git clone --shared` of the commit runs/PINS.toml pins it to (HEAD when it
has no pin), with no keys and outside requests refused, so the checkout is never touched: a bare --replay must skip
every stage the run finished, and --from model --replay must write the same structured outputs. HEAD must still hold
what the pin replays, so what replays at the pin is what HEAD ships. `simula replay-check` and tests/test_replay.py
both run it.

The pinned code runs on this checkout's dependencies, not the pin's uv.lock. uv.lock hasn't changed since
submitted-2026-09-30, so a venv per clone isn't worth it yet."""

import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from simula.config import ROOT

DROPPED_ENV = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TYPESAFE_API_KEY", "ANDROID_SERIAL",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
DEAD_PROXY = "http://127.0.0.1:9"  # nothing listens on the discard port, so any outside request fails at once
MARKERS = {"done.json", "failure.json", "manifest.json"}
PINS = tomllib.loads((ROOT / "runs" / "PINS.toml").read_text())
# Commits whose code keyed images by PNG bytes, which Linux writes differently; commits, so a tagless clone knows them.
BYTE_KEYED = {"071309fb5188d39f7101cbf45bdd05b3dd12703d"}  # submitted-2026-09-30


class Mismatch(Exception):
    """A committed run that doesn't replay as committed, or a pin HEAD doesn't hold."""


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def committed_runs() -> list[Path]:
    """runs/<app>/<run_id>/ for every manifest HEAD holds (not one only staged, which HEAD's clone lacks); none outside
    a git checkout."""
    try:
        tracked = git("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", "runs/").split("\0")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return sorted(Path(p).parent for p in tracked if Path(p).name == "manifest.json" and len(Path(p).parts) == 4)


def key(run: Path) -> str:
    return f"{run.parent.name}/{run.name}"


def resolve(ref: str) -> str:
    """The commit a pin names: a tag or a full sha in HEAD's history, never a branch, which moves. One this checkout
    can't resolve, or one only this machine has (a commit a rebase left behind), fails instead of falling back to
    HEAD, so a laptop and CI agree."""
    name = ref if re.fullmatch(r"[0-9a-f]{40}", ref) else f"refs/tags/{ref}"
    try:
        sha = git("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        raise Mismatch(f"runs/PINS.toml pins a run to {ref!r}, which is neither a tag this checkout has nor a "
                       "full sha") from None
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=ROOT).returncode:
        raise Mismatch(f"runs/PINS.toml pins a run to {ref!r}, which is not in HEAD's history")
    return sha


def commit(run: Path) -> str:
    """The commit the run replays against: the one its pin names, or HEAD."""
    return resolve(PINS[key(run)]) if key(run) in PINS else git("rev-parse", "HEAD").strip()


def clone(sha: str, under: Path) -> Path:
    """The commit, cloned once under `under`: only what git tracks, so a cache entry or an input that was never
    committed makes the replay miss there, even when this checkout has it."""
    root = under / sha / "simula-agent"
    if not root.exists():
        git("clone", "--quiet", "--shared", "--no-checkout", git("rev-parse", "--git-common-dir").strip(), str(root))
        git("checkout", "--quiet", "--detach", sha, cwd=root)
    return root


def offline(clone: Path) -> dict[str, str]:
    """The clone's own code, with no keys and outside requests refused."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in DROPPED_ENV}
    return env | {"PYTHONPATH": str(clone), "HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY,
                  "ALL_PROXY": DEAD_PROXY, "NO_PROXY": "localhost,127.0.0.1"}


READ = """import json, sys
from pathlib import Path
from simula.config import STAGES
from simula.runlog import read_manifest, read_trace
run = Path(sys.argv[1])
m = read_manifest(run)
print(json.dumps({"stages": STAGES, "trace": [[line.stage, line.step] for line in read_trace(run / "trace.jsonl")],
                  "source": m.provenance.source, "git_sha": m.git_sha,
                  "options": ["--run", m.run_id, "--profile", m.profile, "--budget", m.budget,
                              *(["--allow-account-create"] if m.allow_account_create else []),
                              *(["--no-send"] if m.no_send else [])]}))"""


def read(clone: Path, run: Path) -> dict:
    """The stages, the run's trace as (stage, step), its provenance source, the sha that made it and the options it
    was opened with, as its resume line prints them, read by the clone's own code: a pinned run is never read with
    contracts newer than the code replaying it."""
    result = subprocess.run([sys.executable, "-c", READ, str(run)], cwd=clone, env=offline(clone),
                            capture_output=True, text=True)
    ok(result)
    return json.loads(result.stdout)


def finished(run: Path, stages: list[str]) -> set[str]:
    """The stages the committed run finished, in order up to the first it never did: what a replay replays."""
    tracked = set(git("ls-tree", "-r", "--name-only", "HEAD", "--", str(run)).splitlines())
    return set(itertools.takewhile(lambda stage: str(run / stage / "done.json") in tracked, stages))


def replay(clone: Path, run: Path, *extra: str) -> subprocess.CompletedProcess:
    """`simula run APP --replay` in the clone, on the run as committed, with the options it was opened with, so every
    stage hashes the same params."""
    git("checkout", "--quiet", "--", str(run), cwd=clone)
    git("clean", "-fdqx", "--", str(run), cwd=clone)  # -x: runs/ is gitignored, so what a stage adds is ignored too
    command = [sys.executable, "-m", "simula.cli", "run", run.parent.name, *read(clone, run)["options"], "--replay",
               *extra]
    return subprocess.run(command, cwd=clone, env=offline(clone), capture_output=True, text=True, timeout=1800)


def ok(result: subprocess.CompletedProcess) -> None:
    if result.returncode:
        raise Mismatch(f"exit {result.returncode}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")


def check_pin_held(run: Path) -> None:
    """The replay reads the pin's copy of the run and of cache/, so HEAD must hold the same: the run byte for byte,
    every cache entry the pin had (a later run adds entries), and every file the pin had under runs/, so a pinned run
    can't leave together with its pin line."""
    sha = commit(run)
    changed = {*git("diff", "--name-only", "--no-renames", sha, "HEAD", "--", str(run)).splitlines(),
               *git("diff", "--name-only", "--no-renames", "--diff-filter=a",  # lower-case a: every change but an add
                    sha, "HEAD", "--", "cache/").splitlines(),
               *git("diff", "--name-only", "--no-renames", "--diff-filter=D", sha, "HEAD", "--", "runs/").splitlines()}
    if changed:
        raise Mismatch(f"changed since the run's pin: {sorted(changed)}")


def check_markers(clone: Path, run: Path) -> int:
    """A bare --replay re-executes nothing: every stage the run finished hashes its inputs, prompts, params, code and
    outputs as it did then. Returns how many stages that is."""
    before = len(read(clone, run)["trace"])
    ok(replay(clone, run))
    after = read(clone, run)
    skipped = {stage for stage, step in after["trace"][before:] if step == "skip"}
    done = finished(run, after["stages"])
    if skipped != done:
        raise Mismatch(f"no longer match, rerun them: {sorted(done - skipped)}")
    return len(done)


def graders_skip(run: Path) -> str | None:
    """Why the grader's replay can't run on this machine, or None. Decided by the commit a pinned run replays at,
    however its pin spells it."""
    if sys.platform == "darwin" or key(run) not in PINS:
        return None
    if (sha := commit(run)) in BYTE_KEYED:
        return (f"it replays at {sha[:12]}, whose code keyed images by PNG bytes, and this platform encodes the same "
                "pixels to other bytes than the macOS that recorded the cache (known limit, README)")
    return None


def check_graders_replay(clone: Path, run: Path) -> None:
    """Model onward re-executes from the committed cache, as far as the run got, and every structured output comes back
    byte for byte. Renders (PNG, PDF) and the HTML laid out around them aren't byte-stable across machines, so they
    aren't compared; the numbers QA measured on them are, in qa_report.json."""
    ok(replay(clone, run, "--from", "model"))
    changed = [line[3:] for line in git("status", "--porcelain", "--ignored", "--untracked-files=all", "--", str(run),
                                        cwd=clone).splitlines()]
    differs = [p for p in changed if p.endswith(".json") and Path(p).name not in MARKERS]
    if differs:
        raise Mismatch(f"the replay wrote different structured outputs: {differs}")


def check(run: Path, under: Path) -> str:
    """Every replay check on one run, in a clone under `under`; what passed, or Mismatch."""
    check_pin_held(run)
    where = clone(commit(run), under)
    line = f"a bare --replay skipped every finished stage ({check_markers(where, run)})"
    if skip := graders_skip(run):
        return f"{line}; --from model --replay not run: {skip}"
    check_graders_replay(where, run)
    return f"{line}; --from model --replay changed no structured output"


def main(app: str | None = None, run_id: str | None = None) -> int:
    """`simula replay-check`: one line per committed run (all of them, APP's, or the one --run names); 1 when any
    doesn't replay as committed. Its clones are removed however it returns or raises."""
    runs = [r for r in committed_runs() if app in (None, r.parent.name) and run_id in (None, r.name)]
    if not runs:
        print("no committed run matches", *filter(None, (app, run_id)), file=sys.stderr)
        return 2
    failed = False
    with tempfile.TemporaryDirectory(prefix="simula-replay-") as tmp:
        for run in runs:
            label = f"{key(run)} at {PINS.get(key(run), 'HEAD')}"
            try:
                print(f"ok    {label}: {check(run, Path(tmp))}", flush=True)
            except (Mismatch, subprocess.SubprocessError) as e:
                failed = True
                print(f"FAIL  {label}: {e}", flush=True)
    return 1 if failed else 0
