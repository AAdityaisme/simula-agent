pytest_plugins = ["pytester"]

TESTS = [f"test_shards_fixture.py::test_{i}" for i in range(9)]


def collect(pytester, monkeypatch, env: dict[str, str]) -> tuple[list[str], int]:
    """The tests one machine runs under env, collected by pytest as CI collects them, and how many it reports
    deselected."""
    for name in ("PYTEST_SHARD", "PYTEST_SHARDS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    result = pytester.runpytest("-m", "not live", "--collect-only", "-q")
    return [line for line in result.outlines if "::" in line], result.parseoutcomes()["deselected"]


def test_four_shards_run_every_test_that_is_not_live_exactly_once_and_no_shard_variables_run_them_all(pytester,
                                                                                                    monkeypatch):
    """The live test comes first, so a split made before -m 'not live' drops it would give shard 0 other tests."""
    pytester.makeconftest("from tests.conftest import pytest_collection_modifyitems  # noqa: F401")
    pytester.makeini("[pytest]\nmarkers = live: needs API keys\n")
    pytester.makepyfile(test_shards_fixture="import pytest\n\n@pytest.mark.live\ndef test_live():\n    pass\n"
                        + "".join(f"\ndef test_{i}():\n    pass\n" for i in range(9)))
    shards = [collect(pytester, monkeypatch, {"PYTEST_SHARD": str(s), "PYTEST_SHARDS": "4"}) for s in range(4)]
    assert shards[0] == ([TESTS[0], TESTS[4], TESTS[8]], 7)
    assert sorted(test for shard, _ in shards for test in shard) == sorted(TESTS)
    assert all(len(shard) + deselected == 10 for shard, deselected in shards)
    assert collect(pytester, monkeypatch, {}) == (TESTS, 1)
