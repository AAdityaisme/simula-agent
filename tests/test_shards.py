import json
import random
import re

from tests.conftest import ROOT, balance

pytest_plugins = ["pytester"]

# Defined last to first, so the order a shard runs its tests in comes from the split, not from the file.
FIXTURE = "import pytest\n\n@pytest.mark.live\ndef test_live():\n    pass\n" + "".join(
    f"\ndef test_{i}():\n    pass\n" for i in reversed(range(9)))
TESTS = [f"test_shards_fixture.py::test_{i}" for i in range(9)]
JOB = re.compile(r"""^  ("[^"]+"|'[^']+'|[\w-]+):[ \t]*(?:#.*)?$""", re.M)


def test_a_test_the_durations_dont_list_counts_as_their_mean_and_each_shard_runs_its_longest_first():
    seconds = {"a": 9.0, "b": 6.0, "c": 5.0, "d": 4.0, "e": 1.0}
    assert balance([*seconds, "x", "y"], seconds, 3) == [["a", "d"], ["b", "y"], ["c", "x", "e"]]


def test_shards_are_disjoint_cover_every_test_and_dont_depend_on_the_collection_order():
    rng = random.Random(0)
    for shards in range(1, 10):
        tests = [f"t{i}" for i in range(rng.randrange(1, 60))]
        listed = rng.sample(tests, len(tests) // 2)
        seconds = {test: rng.choice([0.0, 0.5, 2.0, rng.uniform(0, 200)]) for test in listed} | {"since-removed": 30.0}
        split = balance(tests, seconds, shards)
        assert len(split) == shards and sorted(test for shard in split for test in shard) == sorted(tests)
        assert balance(rng.sample(tests, len(tests)), seconds, shards) == split


def setup(pytester, monkeypatch, env: dict[str, str]) -> None:
    """A suite of nine tests, none in tests/durations.json, and a live one first, so a split made before -m 'not live'
    drops it would give shard 0 other tests; env is CI's sharding variables, and none of the outer run's leak in."""
    pytester.makeconftest("from tests.conftest import pytest_collection_modifyitems, pytest_terminal_summary  # noqa")
    pytester.makeini("[pytest]\nmarkers = live: needs API keys\n")
    pytester.makepyfile(test_shards_fixture=FIXTURE)
    for name in ("PYTEST_SHARD", "PYTEST_SHARDS", "PYTEST_IDS", "PYTEST_DURATIONS", "PYTEST_XDIST_WORKER"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def collect(pytester, monkeypatch, env: dict[str, str]) -> tuple[list[str], int]:
    """The tests one machine runs under env, in its order, collected by pytest as CI collects them, and how many it
    reports deselected."""
    setup(pytester, monkeypatch, env)
    result = pytester.runpytest("-m", "not live", "--collect-only", "-q")
    assert result.ret == 0, "\n".join(result.outlines)
    return [line for line in result.outlines if "::" in line], result.parseoutcomes()["deselected"]


def test_four_shards_run_every_test_that_is_not_live_exactly_once_and_one_variable_alone_splits_nothing(pytester,
                                                                                                          monkeypatch):
    shards = [collect(pytester, monkeypatch, {"PYTEST_SHARD": str(s), "PYTEST_SHARDS": "4"}) for s in range(4)]
    assert shards[0] == ([TESTS[0], TESTS[4], TESTS[8]], 7)
    assert sorted(test for shard, _ in shards for test in shard) == TESTS
    assert all(len(shard) + deselected == 10 for shard, deselected in shards)
    assert collect(pytester, monkeypatch, {}) == (TESTS[::-1], 1)
    assert collect(pytester, monkeypatch, {"PYTEST_SHARD": "0"}) == (TESTS[::-1], 1)
    assert collect(pytester, monkeypatch, {"PYTEST_SHARDS": "4"}) == (TESTS[::-1], 1)


def test_a_shard_under_xdist_writes_the_ids_it_kept_and_the_seconds_of_every_test_it_ran(pytester, monkeypatch):
    """Each xdist worker collects, but only the controller sees every test's report."""
    ids, seconds = pytester.path / "ids.txt", pytester.path / "durations.json"
    setup(pytester, monkeypatch, {"PYTEST_SHARD": "1", "PYTEST_SHARDS": "2", "PYTEST_IDS": str(ids),
                                  "PYTEST_DURATIONS": str(seconds), "PYTHONPATH": str(ROOT)})
    result = pytester.runpytest_subprocess("-m", "not live", "-n", "2")
    assert result.ret == 0, "\n".join(result.outlines)
    assert ids.read_text().splitlines() == TESTS[1::2]
    assert list(json.loads(seconds.read_text())) == TESTS[1::2]


def test_the_seconds_a_shard_writes_include_its_fixtures_setup_and_teardown(pytester, monkeypatch):
    """Under xdist the controller gets every phase's report, passed setup and teardown included, as --durations does."""
    seconds = pytester.path / "durations.json"
    setup(pytester, monkeypatch, {"PYTEST_DURATIONS": str(seconds), "PYTHONPATH": str(ROOT)})
    pytester.makepyfile(test_slow_fixture="import time\n\nimport pytest\n\n\n@pytest.fixture\ndef slow():\n"
                        "    time.sleep(0.3)\n    yield\n    time.sleep(0.3)\n\n\ndef test_slow(slow):\n    pass\n")
    result = pytester.runpytest_subprocess("-n", "2", "test_slow_fixture.py")
    assert result.ret == 0, "\n".join(result.outlines)
    assert json.loads(seconds.read_text())["test_slow_fixture.py::test_slow"] >= 0.6


def test_the_ci_gate_collects_every_test_that_is_not_live_with_no_shard_set(pytester, monkeypatch):
    ids = pytester.path / "ids.txt"
    collect(pytester, monkeypatch, {"PYTEST_IDS": str(ids)})
    assert ids.read_text().splitlines() == TESTS[::-1]


def time_limits(workflow: str) -> dict[str, bool]:
    """Each job in a workflow's text, bare or quoted, and whether it sets a positive timeout-minutes of its own."""
    parts = JOB.split(workflow.split("\njobs:\n", 1)[1])
    return {key.strip("\"'"): bool(re.search(r"^    timeout-minutes: [1-9]\d*", block, re.M))
            for key, block in zip(parts[1::2], parts[2::2])}


def test_every_ci_job_has_a_time_limit():
    """Without one, a job that hangs sits for GitHub's 6 hours, as a Playwright install on a stalled apt mirror did."""
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text()
    limits = time_limits(workflow)
    assert limits and all(limits.values())
    body = "\n    runs-on: ubuntu-latest\n    steps:\n      - run: sleep 3600\n"
    for unguarded in ('  "unguarded":', "  unguarded: # slow check"):
        assert time_limits(workflow + unguarded + body) == limits | {"unguarded": False}
