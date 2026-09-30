"""Every run committed under runs/ replays from a clean checkout with no keys, no device and no network, against the
code it's pinned to in runs/PINS.toml (HEAD when it has no pin): its markers still describe that code, and the grader's
command re-executes model onward from the committed cache and writes the same structured outputs. HEAD still holds
each run byte for byte and every cache entry its pin had, so what replays at the pin is what HEAD ships. A run replays
the stages it finished: one that stopped at explore (an app that refused the emulator) replays explore alone.
Parametrized over the committed runs, so with none it skips."""

import itertools
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import pytest

from simula.runlog import read_manifest
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


def resolve(ref: str) -> str:
    """The commit a pin names: a tag or a full sha, never a branch, which moves. One this checkout can't resolve fails
    the test instead of falling back to HEAD."""
    name = ref if re.fullmatch(r"[0-9a-f]{40}", ref) else f"refs/tags/{ref}"
    try:
        return git("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        pytest.fail(f"runs/PINS.toml pins a run to {ref!r}, which is neither a tag this checkout has nor a full sha")


def commit(run: Path) -> str:
    """The commit the run replays against: the one its pin names, or HEAD."""
    return resolve(PINS[key(run)]) if key(run) in PINS else git("rev-parse", "HEAD").strip()


@pytest.fixture(scope="module")
def clones() -> dict[str, Path]:
    """One clone per pinned commit, shared by the module's tests."""
    return {}


@pytest.fixture
def clone(run, clones, tmp_path_factory) -> Path:
    """The run's pinned commit, cloned: only what git tracks, so a cache entry or an input that was never committed
    makes the replay miss here, even when the working checkout has it."""
    sha = commit(run)
    if sha not in clones:
        root = tmp_path_factory.mktemp("replay") / "simula-agent"
        git("clone", "--quiet", "--shared", "--no-checkout", git("rev-parse", "--git-common-dir").strip(), str(root))
        git("checkout", "--quiet", "--detach", sha, cwd=root)
        clones[sha] = root
    return clones[sha]


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
                  "options": ["--run", m.run_id, "--profile", m.profile, "--budget", m.budget,
                              *(["--allow-account-create"] if m.allow_account_create else []),
                              *(["--no-send"] if m.no_send else [])]}))"""


def read(clone: Path, run: Path) -> dict:
    """The stages, the run's trace as (stage, step) and the options it was opened with, as its resume line prints
    them, read by the clone's own code: a pinned run is never read with contracts newer than the code replaying it."""
    result = subprocess.run([sys.executable, "-c", READ, str(run)], cwd=clone, env=offline(clone),
                            capture_output=True, text=True)
    assert_ok(result)
    return json.loads(result.stdout)


def finished(run: Path, stages: list[str]) -> set[str]:
    """The stages the committed run finished, in order up to the first it never did: what a replay replays."""
    tracked = set(git("ls-files", "--", str(run)).splitlines())
    return set(itertools.takewhile(lambda stage: str(run / stage / "done.json") in tracked, stages))


def replay(clone: Path, run: Path, *extra: str) -> subprocess.CompletedProcess:
    """`simula run APP --replay` in the clone, on the run as committed, with the options it was opened with, so every
    stage hashes the same params."""
    git("checkout", "--quiet", "--", str(run), cwd=clone)
    git("clean", "-fdqx", "--", str(run), cwd=clone)  # -x: runs/ is gitignored, so what a stage adds is ignored too
    command = [sys.executable, "-m", "simula.cli", "run", run.parent.name, *read(clone, run)["options"], "--replay",
               *extra]
    return subprocess.run(command, cwd=clone, env=offline(clone), capture_output=True, text=True, timeout=1800)


def assert_ok(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"


@by_run
def test_a_committed_run_is_a_real_explorer_run(run):
    manifest = read_manifest(ROOT / run)
    assert manifest.provenance.source == "explorer_run" and not run.name.endswith("-fixture")


def test_every_pin_names_a_committed_run_and_a_tag_or_sha_this_checkout_has():
    stale = set(PINS) - {key(run) for run in RUNS}
    assert not stale, f"runs/PINS.toml pins runs that aren't committed: {sorted(stale)}"
    for ref in set(PINS.values()):
        resolve(ref)


@by_run
def test_head_holds_the_run_and_the_cache_its_pin_replays(run):
    """The replay reads the pin's copy of the run and of cache/, so HEAD must hold the same: the run byte for byte,
    and every cache entry the pin had (a later run adds entries)."""
    sha = commit(run)
    rewritten = git("diff", "--name-only", "--no-renames", sha, "HEAD", "--", str(run)).splitlines()
    dropped = git("diff", "--name-only", "--no-renames", "--diff-filter=a",  # lower-case a: every change but an add
                  sha, "HEAD", "--", "cache/").splitlines()
    assert not rewritten + dropped, f"changed since the run's pin: {rewritten + dropped}"


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_runs_that_share_a_pin_were_made_by_the_same_code():
    """No code changed between the app runs one pin replays: the out-of-set app ran on exactly what the others did."""
    made_by = defaultdict(set)
    for run in RUNS:
        made_by[PINS.get(key(run))].add(read_manifest(ROOT / run).git_sha)
    assert all(len(shas) == 1 for shas in made_by.values()), dict(made_by)


@by_run
def test_the_committed_markers_still_describe_the_pinned_code(clone, run):
    """A bare --replay re-executes nothing: every stage the run finished hashes its inputs, prompts, params, code and
    outputs as it did then, at the commit the run is pinned to. An unpinned run fails here once a merge touches a
    stage's code, a prompt or a param; pin it to the commit that added it."""
    before = len(read(clone, run)["trace"])
    assert_ok(replay(clone, run))
    after = read(clone, run)
    skipped = {stage for stage, step in after["trace"][before:] if step == "skip"}
    done = finished(run, after["stages"])
    assert skipped == done, f"no longer match, rerun them: {sorted(done - skipped)}"


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
