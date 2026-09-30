"""Every run committed under runs/ replays from a clean checkout with no keys, no device and no network, against the
code it's pinned to in runs/PINS.toml (HEAD when it has no pin): its markers still describe that code, and the grader's
command re-executes model onward from the committed cache and writes the same structured outputs. A run replays the
stages it finished: one that stopped at explore (an app that refused the emulator) replays explore alone.
Parametrized over the committed runs, so with none it skips."""

import itertools
import os
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import pytest

from simula.config import STAGES
from simula.runlog import read_manifest, read_trace
from tests.conftest import ROOT

DROPPED_ENV = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TYPESAFE_API_KEY", "ANDROID_SERIAL",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
DEAD_PROXY = "http://127.0.0.1:9"  # nothing listens on the discard port, so any outside request fails at once
MARKERS = {"done.json", "failure.json", "manifest.json"}
PINS = tomllib.loads((ROOT / "runs" / "PINS.toml").read_text())


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def committed_runs() -> list[Path]:
    """runs/<app>/<run_id>/ for every manifest git tracks; none outside a git checkout."""
    try:
        tracked = git("ls-files", "-z", "--", "runs/").split("\0")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return sorted(Path(p).parent for p in tracked if Path(p).name == "manifest.json" and len(Path(p).parts) == 4)


def key(run: Path) -> str:
    return f"{run.parent.name}/{run.name}"


RUNS = committed_runs()
by_run = pytest.mark.parametrize("run", RUNS, ids=[key(r) for r in RUNS])


def pin(run: Path) -> str:
    """The git ref the run replays against: its entry in runs/PINS.toml, or HEAD."""
    return PINS.get(key(run), "HEAD")


def commit(ref: str) -> str:
    """The commit a pin names. A ref this checkout doesn't have fails the test instead of falling back to HEAD."""
    try:
        return git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        pytest.fail(f"runs/PINS.toml pins a run to {ref!r}, which this checkout doesn't have (fetch its tags)")


@pytest.fixture(scope="module")
def clones() -> dict[str, Path]:
    """One clone per pinned commit, shared by the module's tests."""
    return {}


@pytest.fixture
def clone(run, clones, tmp_path_factory) -> Path:
    """The run's pinned commit, cloned: only what git tracks, so a cache entry or an input that was never committed
    makes the replay miss here, even when the working checkout has it."""
    sha = commit(pin(run))
    if sha not in clones:
        root = tmp_path_factory.mktemp("replay") / "simula-agent"
        git("clone", "--quiet", "--shared", "--no-checkout", git("rev-parse", "--git-common-dir").strip(), str(root))
        git("checkout", "--quiet", "--detach", sha, cwd=root)
        clones[sha] = root
    return clones[sha]


def finished(run: Path) -> set[str]:
    """The stages the committed run finished, in order up to the first it never did: what a replay replays."""
    tracked = set(git("ls-files", "--", str(run)).splitlines())
    return set(itertools.takewhile(lambda stage: str(run / stage / "done.json") in tracked, STAGES))


def run_options(run_dir: Path) -> list[str]:
    """The options the run was opened with, as its resume line prints them, so every stage hashes the same params."""
    m = read_manifest(run_dir)
    return ["--run", m.run_id, "--profile", m.profile, "--budget", m.budget,
            *(["--allow-account-create"] if m.allow_account_create else []), *(["--no-send"] if m.no_send else [])]


def replay(clone: Path, run: Path, *extra: str) -> subprocess.CompletedProcess:
    """`simula run APP --replay` in the clone, on the run as committed, with no keys and outside requests refused."""
    git("checkout", "--quiet", "--", str(run), cwd=clone)
    git("clean", "-fdqx", "--", str(run), cwd=clone)  # -x: runs/ is gitignored, so what a stage adds is ignored too
    env = {k: v for k, v in os.environ.items() if k.upper() not in DROPPED_ENV}
    env |= {"PYTHONPATH": str(clone), "HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY, "ALL_PROXY": DEAD_PROXY,
            "NO_PROXY": "localhost,127.0.0.1"}
    command = [sys.executable, "-m", "simula.cli", "run", run.parent.name, *run_options(clone / run), "--replay",
               *extra]
    return subprocess.run(command, cwd=clone, env=env, capture_output=True, text=True, timeout=1800)


def assert_ok(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"


@by_run
def test_a_committed_run_is_a_real_explorer_run(run):
    manifest = read_manifest(ROOT / run)
    assert manifest.provenance.source == "explorer_run" and not run.name.endswith("-fixture")


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_every_pin_names_a_committed_run_and_a_ref_this_checkout_has():
    stale = set(PINS) - {key(run) for run in RUNS}
    assert not stale, f"runs/PINS.toml pins runs that aren't committed: {sorted(stale)}"
    for ref in set(PINS.values()):
        commit(ref)


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_runs_that_share_a_pin_were_made_by_the_same_code():
    """No code changed between the app runs one pin replays: the out-of-set app ran on exactly what the others did."""
    made_by = defaultdict(set)
    for run in RUNS:
        made_by[pin(run)].add(read_manifest(ROOT / run).git_sha)
    assert all(len(shas) == 1 for shas in made_by.values()), dict(made_by)


@by_run
def test_the_committed_markers_still_describe_the_pinned_code(clone, run):
    """A bare --replay re-executes nothing: every stage the run finished hashes its inputs, prompts, params, code and
    outputs as it did then, at the commit the run is pinned to. An unpinned run fails here once a merge touches a
    stage's code, a prompt or a param; pin it to the commit that added it."""
    before = len(read_trace(clone / run / "trace.jsonl"))
    assert_ok(replay(clone, run))
    skipped = {line.stage for line in read_trace(clone / run / "trace.jsonl")[before:] if line.step == "skip"}
    assert skipped == finished(run), f"no longer match, rerun them: {sorted(finished(run) - skipped)}"


@pytest.mark.skipif(sys.platform != "darwin", reason="the committed cache was recorded on macOS; the model stage "
                    "re-encodes screenshots to PNG and the bytes are in the cache key, and Linux writes different PNG "
                    "bytes for the same pixels (known limit, README)")
@by_run
def test_the_graders_replay_reproduces_the_committed_outputs(clone, run):
    """Model onward re-executes from the committed cache, as far as the run got (explore needs the device, so it keeps
    its capture; a run that stopped at explore has nothing past it to re-execute). Every
    structured output comes back byte for byte. Renders (PNG, PDF) and the HTML laid out around them aren't
    byte-stable across machines, so they aren't compared; the numbers QA measured on them are, in qa_report.json."""
    assert_ok(replay(clone, run, "--from", "model"))
    changed = [line[3:] for line in git("status", "--porcelain", "--ignored", "--untracked-files=all", "--", str(run),
                                        cwd=clone).splitlines()]
    differs = [p for p in changed if p.endswith(".json") and Path(p).name not in MARKERS]
    assert not differs, f"the replay wrote different structured outputs: {differs}"
