"""Every run committed under runs/ replays from a clean checkout with no keys, no device and no network, against the
code it's pinned to in runs/PINS.toml (HEAD when it has no pin): its markers still describe that code, and the grader's
command re-executes model onward from the committed cache and writes the same structured outputs. HEAD still holds
each run byte for byte and every cache entry its pin had, so what replays at the pin is what HEAD ships. A run replays
the stages it finished: one that stopped at explore (an app that refused the emulator) replays explore alone. The
mechanism is simula/replaycheck.py, which `simula replay-check` runs too. Parametrized over the committed runs, so with
none it skips."""

import os
import shutil
import signal
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import pytest

from simula import replaycheck
from simula.config import STAGES
from simula.replaycheck import PINS, commit, git, key, read
from tests.conftest import ROOT

RUNS = replaycheck.committed_runs()
by_run = pytest.mark.parametrize("run", RUNS, ids=[key(r) for r in RUNS])


@pytest.fixture(scope="module")
def clones(tmp_path_factory) -> Iterator[Path]:
    """Where the module's clones live, one per pinned commit, shared by its tests and removed after them, passed or
    failed."""
    root = tmp_path_factory.mktemp("replay")
    yield root
    shutil.rmtree(root)


@pytest.fixture
def clone(run, clones) -> Path:
    return replaycheck.clone(commit(run), clones)


@by_run
def test_a_committed_run_is_a_real_explorer_run(clone, run):
    assert read(clone, run)["source"] == "explorer_run" and not run.name.endswith("-fixture")


def test_every_pin_names_a_committed_run_and_a_tag_or_sha_this_checkout_has():
    stale = set(PINS) - {key(run) for run in RUNS}
    assert not stale, f"runs/PINS.toml pins runs that aren't committed: {sorted(stale)}"
    for ref in set(PINS.values()):
        replaycheck.resolve(ref)


@by_run
def test_head_holds_the_run_and_the_cache_its_pin_replays(run):
    replaycheck.check_pin_held(run)


@pytest.fixture
def stray_objects(tmp_path, monkeypatch):
    """Commits and index entries the test makes land in tmp_path, never in the repo, which git still reads through."""
    repo_objects = Path(ROOT, git("rev-parse", "--git-path", "objects").strip())
    (tmp_path / "objects").mkdir()
    env = {"GIT_OBJECT_DIRECTORY": tmp_path / "objects", "GIT_ALTERNATE_OBJECT_DIRECTORIES": repo_objects,
           "GIT_INDEX_FILE": tmp_path / "index", "GIT_AUTHOR_NAME": "test", "GIT_COMMITTER_NAME": "test",
           "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))


def test_a_pin_to_a_commit_outside_heads_history_is_refused(stray_objects):
    """A commit a rebase left behind resolves on the laptop that has it and nowhere else."""
    stray = git("commit-tree", "HEAD^{tree}", "-m", "left behind").strip()
    with pytest.raises(replaycheck.Mismatch, match="not in HEAD's history"):
        replaycheck.resolve(stray)


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_a_staged_run_or_marker_is_not_committed(stray_objects):
    """HEAD's clone replays only what HEAD holds, not what's staged."""
    run = min(RUNS, key=lambda r: len(replaycheck.finished(r, STAGES)))
    done = replaycheck.finished(run, STAGES)
    manifest = git("rev-parse", f"HEAD:{run}/manifest.json").strip()
    git("read-tree", "HEAD")
    for path in ["runs/staged/20260101-000000-0000000/manifest.json", *(f"{run}/{s}/done.json" for s in STAGES)]:
        git("update-index", "--add", "--cacheinfo", f"100644,{manifest},{path}")
    assert replaycheck.committed_runs() == RUNS and replaycheck.finished(run, STAGES) == done


@pytest.mark.skipif(not PINS, reason="no pinned runs")
def test_a_pinned_run_cant_leave_together_with_its_pin_line(stray_objects, monkeypatch):
    run = next(r for r in RUNS if key(r) in PINS)
    pin = commit(run)
    git("read-tree", pin)
    git("update-index", "--add", "--cacheinfo", f"100644,{git('rev-parse', f'{pin}:{run}/manifest.json').strip()},"
        "runs/gone/20260101-000000-0000000/manifest.json")
    had_another_run = git("commit-tree", git("write-tree").strip(), "-m", "the pin, with a run HEAD dropped").strip()
    monkeypatch.setattr(replaycheck, "commit", lambda _: had_another_run)
    with pytest.raises(replaycheck.Mismatch, match="runs/gone/"):
        replaycheck.check_pin_held(run)


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_runs_that_share_a_pin_were_made_by_the_same_code(clones):
    """No code changed between the app runs one pin replays: the out-of-set app ran on exactly what the others did.
    An unpinned run replays at HEAD, which its markers test already holds it to."""
    made_by = defaultdict(set)
    for run in RUNS:
        if key(run) in PINS:
            made_by[PINS[key(run)]].add(read(replaycheck.clone(commit(run), clones), run)["git_sha"])
    assert all(len(shas) == 1 for shas in made_by.values()), dict(made_by)


@by_run
def test_the_committed_markers_still_describe_the_pinned_code(clone, run):
    """An unpinned run fails here once a merge touches a stage's code, a prompt or a param; pin it to the commit that
    added it."""
    replaycheck.check_markers(clone, run)


@pytest.mark.parametrize("tags", ["visible", "hidden"])
def test_a_full_sha_pin_of_byte_keyed_code_skips_the_graders_replay_off_macos(monkeypatch, tags):
    run = next((r for r in RUNS if key(r) in PINS and commit(r) in replaycheck.BYTE_KEYED), None)
    if run is None:
        pytest.skip("no run pinned to byte-keyed code")
    monkeypatch.setitem(PINS, key(run), commit(run))
    if tags == "hidden":  # a fork or a tagless clone

        def tagless(*args: str, **kwargs) -> str:
            if any("refs/tags/" in arg for arg in args):
                raise subprocess.CalledProcessError(1, "git")
            return git(*args, **kwargs)
        monkeypatch.setattr(replaycheck, "git", tagless)
    monkeypatch.setattr(sys, "platform", "linux")
    assert replaycheck.graders_skip(run)


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_an_unpinned_run_is_never_skipped_and_resolves_nothing(monkeypatch):
    run = RUNS[0]
    monkeypatch.delitem(PINS, key(run), raising=False)
    monkeypatch.setattr(replaycheck, "resolve", lambda ref: pytest.fail(f"resolved {ref!r} for an unpinned run"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert replaycheck.graders_skip(run) is None


@by_run
def test_the_graders_replay_reproduces_the_committed_outputs(clone, run):
    if skip := replaycheck.graders_skip(run):
        pytest.skip(skip)
    replaycheck.check_graders_replay(clone, run)


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_replay_check_prints_a_line_per_run_and_fails_when_any_run_does(monkeypatch, capsys):
    def check(run, under):
        if run == RUNS[0]:
            raise replaycheck.Mismatch("the replay wrote different structured outputs")
        return "same"
    monkeypatch.setattr(replaycheck, "check", check)
    assert replaycheck.main() == 1
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ["FAIL"] + ["ok"] * (len(RUNS) - 1)


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_replay_check_removes_its_clones_when_a_run_fails(monkeypatch):
    made = []

    def check(run, under):
        made.append(under)
        (under / "clone").mkdir(exist_ok=True)
        raise replaycheck.Mismatch("the replay wrote different structured outputs")
    monkeypatch.setattr(replaycheck, "check", check)
    assert replaycheck.main() == 1
    assert made and not made[0].exists()


STOPPED = """import os, signal
from simula import cli, replaycheck
def check(run, under):
    (under / "clone").mkdir()
    print(under, flush=True)
    os.kill(os.getpid(), signal.SIGTERM)
replaycheck.check = check
cli.main(["replay-check"])"""


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_replay_check_removes_its_clones_when_it_is_stopped(tmp_path):
    result = subprocess.run([sys.executable, "-c", STOPPED], cwd=ROOT, env=os.environ | {"TMPDIR": str(tmp_path)},
                            capture_output=True, text=True)
    under = Path(result.stdout.split()[0])
    assert result.returncode == 128 + signal.SIGTERM and not under.exists(), (result.returncode,
                                                                              list(tmp_path.iterdir()))
