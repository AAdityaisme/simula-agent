"""Every run committed under runs/ replays from a clean checkout with no keys, no device and no network, against the
code it's pinned to in runs/PINS.toml (HEAD when it has no pin): its markers still describe that code, and the grader's
command re-executes model onward from the committed cache and writes the same structured outputs. HEAD still holds
each run byte for byte and every cache entry its pin had, so what replays at the pin is what HEAD ships. A run replays
the stages it finished: one that stopped at explore (an app that refused the emulator) replays explore alone. The
mechanism is simula/replaycheck.py, which `simula replay-check` runs too. Parametrized over the committed runs, so with
none it skips."""

import io
import os
import shutil
import signal
import subprocess
import sys
import tarfile
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import pytest

from simula import cli, replaycheck
from simula.config import STAGES
from simula.replaycheck import PINS, commit, git, key, read
from simula.runlog import read_manifest
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
        return "ok", "same"
    monkeypatch.setattr(replaycheck, "check", check)
    assert replaycheck.main() == 1
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ["FAIL"] + ["ok"] * (len(RUNS) - 1)


@pytest.fixture
def checked(monkeypatch, capsys):
    """replay-check's line for one run pinned to `pin`, whose replays pass, or whose grader's replay this platform
    `skip`s: the pin and the lock are real, the clone and the replays stubbed."""
    def line(pin: str, skip: str | None = None) -> str:
        run = RUNS[0]
        stubs = {"commit": lambda _: pin, "check_pin_held": lambda _: None, "clone": lambda sha, under: under,
                 "check_markers": lambda clone, run: 1, "graders_skip": lambda _: skip,
                 "check_graders_replay": lambda clone, run: None}
        for name, stub in stubs.items():
            monkeypatch.setattr(replaycheck, name, stub)
        assert replaycheck.main(run.parent.name, run.name) == 0
        return capsys.readouterr().out
    return line


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_replay_check_says_when_the_pins_lock_differs_from_the_checkouts(checked, stray_objects, monkeypatch,
                                                                        capsys):
    """The pinned code runs on this checkout's packages, not its pin's uv.lock (Sol's 10, Fable's L8), and a failing
    run says so too, where a package difference is the likeliest cause."""
    note = "(ran on this checkout's dependencies; the pin's lock differs)"
    git("read-tree", "HEAD")
    git("update-index", "--cacheinfo", f"100644,{git('rev-parse', 'HEAD:README.md').strip()},uv.lock")
    other_lock = git("commit-tree", git("write-tree").strip(), "-m", "another uv.lock").strip()
    git("read-tree", "HEAD")
    assert note in checked(other_lock)
    assert "lock" not in checked(git("rev-parse", "HEAD").strip())

    def mismatch(run, under):
        raise replaycheck.Mismatch("the replay wrote different structured outputs")
    monkeypatch.setattr(replaycheck, "commit", lambda _: other_lock)
    monkeypatch.setattr(replaycheck, "check", mismatch)
    assert replaycheck.main(RUNS[0].parent.name, RUNS[0].name) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL") and note in out


@pytest.mark.skipif(not RUNS, reason="no committed runs")
def test_replay_check_prints_skip_when_the_graders_replay_did_not_run(checked):
    head = git("rev-parse", "HEAD").strip()
    assert checked(head, skip="known limit").startswith("skip  ")
    assert checked(head).startswith("ok    ")


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


LEGACY = "submitted-2026-09-30"  # code from before --allow-account-create was removed (2026-10-03)
OPENED_WITH_ACCOUNTS = """from simula import cli, config, runfolder, runlog
from simula.contracts import Provenance, StageOutcome
args = cli.parser().parse_args(["run", "janitorai", "--run", "legacy", "--allow-account-create"])
run = config.ROOT / "runs" / "janitorai" / "legacy"
run.mkdir(parents=True)
provenance = Provenance(source="explorer_run", explorer_run_id="legacy")
runlog.write_manifest(run, cli.new_manifest(run, config.app_config("janitorai"), args, provenance))
ctx = cli.open_run(args)
explore = run / "explore"
explore.mkdir()
(explore / "explore.json").write_text("{}")
runfolder.write_done(explore, run, cli.stage_inputs("explore", ctx), cli.prompt_files("explore"),
                     cli.stage_params("explore", ctx), [explore], provenance, code=runfolder.code_files("explore"),
                     outcome=StageOutcome(status="partial", reasons=["the test account's email waits to be verified"]))
"""


def test_a_run_old_code_opened_with_account_creation_replays_its_partial_explore(tmp_path):
    """Red team on f6e9ba5: code from before 2026-10-03 hashes --allow-account-create into explore's params, and a
    partial explore nothing is built on replays only while they match, so its replay must pass the flag again."""
    archive = subprocess.run(["git", "archive", replaycheck.resolve(LEGACY), "simula", "config", "prompts"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp_path, filter="data")
    env = replaycheck.offline(tmp_path)
    subprocess.run([sys.executable, "-c", OPENED_WITH_ACCOUNTS], cwd=tmp_path, env=env, capture_output=True,
                   check=True)
    options = read(tmp_path, tmp_path / "runs" / "janitorai" / "legacy")["options"]
    assert "--allow-account-create" in options
    replayed = subprocess.run([sys.executable, "-m", "simula.cli", "run", "janitorai", *options, "--replay"],
                              cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert replayed.returncode == 0, replayed.stdout + replayed.stderr


def test_a_run_this_code_opened_replays_without_the_removed_flag(runs):
    """Its manifest says account creation was on, as it always is now, and this code's CLI has no flag to pass."""
    cli.main(["run", "janitorai", "--new"])
    run = (runs / "janitorai" / "latest").resolve()
    assert read_manifest(run).allow_account_create and "--allow-account-create" not in read(ROOT, run)["options"]
