from types import SimpleNamespace

from tests.conftest import pytest_collection_modifyitems


def collect(monkeypatch, items: list, env: dict[str, str]) -> tuple[list, list]:
    """What one machine runs and what it reports deselected, under env."""
    for name in ("PYTEST_SHARD", "PYTEST_SHARDS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    dropped = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: dropped.extend(items)))
    kept = list(items)
    pytest_collection_modifyitems(config, kept)
    return kept, dropped


def test_four_shards_run_every_test_exactly_once_and_no_shard_variables_run_them_all(monkeypatch):
    tests = [f"t{i}" for i in range(10)]
    shards = [collect(monkeypatch, tests, {"PYTEST_SHARD": str(s), "PYTEST_SHARDS": "4"}) for s in range(4)]
    assert sorted(t for kept, _ in shards for t in kept) == sorted(tests)
    assert all(sorted(kept + dropped) == sorted(tests) for kept, dropped in shards)
    assert collect(monkeypatch, tests, {}) == (tests, [])
