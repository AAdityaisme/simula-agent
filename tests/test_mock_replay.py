"""A live mock that lost batches: its --replay rebuilds the same page, a replay that misses leaves the committed record
whole, and the stage reports the lost batches as a partial outcome in done.json."""

import anthropic
import httpx2
import pytest

from simula import cli, llm, runfolder, runlog
from simula.runlog import read_trace
from simula.stages import mock
from tests.conftest import FIXTURES
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_batches import provider_drawing, with_cache_in
from tests.test_mock_isolation import batch_screens, ctx_for, without_edges

APP = "janitorai"
GOLDEN = FIXTURES / "golden" / APP


@pytest.fixture(autouse=True)
def two_batches(monkeypatch):
    monkeypatch.setattr(mock, "BATCH_SCREENS", 2)


@pytest.fixture(autouse=True)
def no_desktop_notice(monkeypatch):
    monkeypatch.setattr(runlog, "notify", lambda title, message: True)


@pytest.mark.parametrize("gap", ["no_plan_record", "cache_moved_aside"])
def test_a_replay_that_misses_leaves_the_committed_record_whole(runs, tmp_path, monkeypatch, gap):
    """A replay that can't rebuild the mock keeps the run's done.json, so every file that marker records must still
    be there: a replay never clears what the live run built."""
    with_cache_in(tmp_path, monkeypatch)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(golden(APP), []))
    assert cli.main(["mock", APP, "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    run_dir = (runs / APP / "latest").resolve()
    mock_dir = run_dir / "mock"
    committed = (mock_dir / "done.json").read_bytes()
    if gap == "no_plan_record":  # a run built before plans were recorded
        (mock_dir / "plan.json").rename(tmp_path / "plan-moved-aside.json")
    else:  # a machine whose cache lacks the run's answers
        (tmp_path / "cache").rename(tmp_path / "cache-moved-aside")

    assert cli.main(["mock", APP, "--run", run_dir.name, "--allow-fixtures", "--replay"]) == cli.EXIT_CAP
    assert (mock_dir / "done.json").read_bytes() == committed
    moved = {"mock/plan.json"} if gap == "no_plan_record" else set()
    missing = [h.path for h in runfolder.read_done(mock_dir).output_hashes
               if h.path not in moved and not (run_dir / h.path).exists()]
    assert missing == []

