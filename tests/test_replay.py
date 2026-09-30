"""Every run committed under runs/ replays from a clean checkout with no keys, no device and no network, against the
code it's pinned to in runs/PINS.toml (HEAD when it has no pin): its markers still describe that code, and the grader's
command re-executes model onward from the committed cache and writes the same structured outputs. HEAD still holds
each run byte for byte and every cache entry its pin had, so what replays at the pin is what HEAD ships. A run replays
the stages it finished: one that stopped at explore (an app that refused the emulator) replays explore alone. The
mechanism is simula/replaycheck.py, which `simula replay-check` runs too. Parametrized over the committed runs, so with
none it skips."""

from collections import defaultdict
from pathlib import Path

import pytest

from simula import replaycheck
from simula.replaycheck import PINS, commit, key, read

RUNS = replaycheck.committed_runs()
by_run = pytest.mark.parametrize("run", RUNS, ids=[key(r) for r in RUNS])


@pytest.fixture(scope="module")
def clones(tmp_path_factory) -> Path:
    """Where the module's clones live, one per pinned commit, shared by its tests."""
    return tmp_path_factory.mktemp("replay")


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


@by_run
def test_the_graders_replay_reproduces_the_committed_outputs(clone, run):
    if skip := replaycheck.graders_skip(run):
        pytest.skip(skip)
    replaycheck.check_graders_replay(clone, run)
