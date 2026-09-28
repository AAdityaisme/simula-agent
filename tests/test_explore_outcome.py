"""How an explore ends: a device error or an empty explore is a failure (failure.json), a blocked root seen after
the full splash wait is a result (done.json)."""

from contextlib import nullcontext

import pytest

from simula import cli
from simula.stages import explore as stage
from tests.fake_device import FakePhone, blank
from tests.fake_device import explorer as new_explorer
from tests.test_explore_offline import janitor_like


def through_cli(tmp_path, monkeypatch, factory, fails: bool):
    ex, phone = new_explorer(tmp_path, monkeypatch, factory)
    monkeypatch.setattr(stage, "run", lambda ctx: stage.explore_app(ex))
    with pytest.raises(stage.ExploreFailed) if fails else nullcontext():
        cli.run_stage("explore", ex.ctx, force=True)
    return ex, phone, ex.run_dir / "explore"


def never_answers(clock):
    phone = janitor_like(clock)
    phone.hung_lists = 10_000
    return phone


def hangs_on_a_tab(clock):
    phone = janitor_like(clock)
    go = phone.go

    def hang_on_search(target):
        go(target)
        if phone.screen == "search":
            phone.hung_lists = 10_000
    phone.go = hang_on_search
    return phone


def launcher_only(clock):
    return FakePhone(screens={"home": blank("com.android.launcher")}, start="home", taps={}, clock=clock)


def test_a_device_that_never_answers_fails_with_no_states(tmp_path, monkeypatch):
    ex, _, out = through_cli(tmp_path, monkeypatch, never_answers, fails=True)
    assert not ex.states and ex.stop_reason.startswith("device error")
    assert (out / "failure.json").exists() and not (out / "done.json").exists()


def test_a_device_error_mid_tour_fails_but_keeps_what_it_saw(tmp_path, monkeypatch):
    ex, _, out = through_cli(tmp_path, monkeypatch, hangs_on_a_tab, fails=True)
    assert ex.states and ex.stop_reason.startswith(stage.DEVICE_STOPS)
    assert (out / "failure.json").exists() and not (out / "done.json").exists()
    assert (out / "explore.json").exists()


def test_a_blocked_root_after_the_full_splash_wait_is_done(tmp_path, monkeypatch):
    ex, phone, out = through_cli(tmp_path, monkeypatch, launcher_only, fails=False)
    assert ex.stop_reason.startswith("blocked root")
    assert phone.clock.t >= stage.SPLASH_WAIT_S
    assert (out / "done.json").exists() and not (out / "failure.json").exists()
    assert (ex.run_dir / "needs-human.md").exists()
