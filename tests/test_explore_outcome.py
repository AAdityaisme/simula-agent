"""How an explore ends: a device error or an empty explore is a failure (failure.json), a blocked root seen after
the full splash wait is a result (done.json)."""

from contextlib import nullcontext

import pytest

from simula import cli, runlog
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


def loses_the_device_in_the_core_loop(clock):
    from simula.device.mcp import McpReplyError
    phone = janitor_like(clock)
    save = phone.screenshot

    def screenshot(path, size=None):
        if phone.typed:
            raise McpReplyError('Device "fake-1" not found')
        return save(path, size)
    phone.screenshot = screenshot
    return phone


def refuses_twice_on_search(clock):
    from simula.device.mcp import McpReplyError
    phone = janitor_like(clock)
    elements = phone.elements

    def refusing():
        if phone.screen == "search":
            raise McpReplyError('Device "fake-1" not found')
        return elements()
    phone.elements = refusing
    return phone


def test_a_device_lost_after_the_tour_fails_with_no_done_marker(tmp_path, monkeypatch):
    ex, _, out = through_cli(tmp_path, monkeypatch, loses_the_device_in_the_core_loop, fails=True)
    assert ex.stop_reason.startswith("device error in core_loop (tour: ")
    assert (out / "failure.json").exists() and not (out / "done.json").exists()
    assert (out / "explore.json").exists() and (ex.run_dir / "needs-human.md").exists()


def test_a_device_lost_in_the_tour_is_a_device_error_not_a_hang(tmp_path, monkeypatch):
    ex, _, out = through_cli(tmp_path, monkeypatch, refuses_twice_on_search, fails=True)
    assert ex.stop_reason.startswith("device error on s") and "not found" in ex.stop_reason
    steps = [line.step for line in runlog.read_trace(ex.run_dir / "trace.jsonl")]
    assert "device" in steps and "hang" not in steps
    assert (out / "failure.json").exists() and not (out / "done.json").exists()


def test_a_blocked_root_after_the_full_splash_wait_is_done(tmp_path, monkeypatch):
    ex, phone, out = through_cli(tmp_path, monkeypatch, launcher_only, fails=False)
    assert ex.stop_reason.startswith("blocked root")
    assert phone.clock.t >= stage.SPLASH_WAIT_S
    assert (out / "done.json").exists() and not (out / "failure.json").exists()
    assert (ex.run_dir / "needs-human.md").exists()


@pytest.mark.parametrize("error", [SystemExit("explore stopped by SIGTERM"), TimeoutError("respawn"),
                                   KeyboardInterrupt(), RuntimeError("the server died")],
                         ids=["SystemExit", "TimeoutError", "KeyboardInterrupt", "RuntimeError"])
def test_a_crash_on_the_first_core_loop_screenshot_keeps_the_tour(tmp_path, monkeypatch, error):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    save = phone.screenshot

    def screenshot(path, size=None):
        if ex.core is not None:
            raise error
        return save(path, size)
    phone.screenshot = screenshot
    with pytest.raises(type(error)):
        stage.explore_app(ex)
    assert ex.core is not None and len(ex.states) >= 8
    assert (ex.out / "explore.json").exists()
    assert all((ex.out / "states" / f"{s.sid}.json").exists() for s in ex.states)
    assert (ex.run_dir / "exhibits" / "01-explore.md").exists()
    assert any(f"core_loop crashed: {type(error).__name__}" in r for r in ex.core_results)


def test_after_a_failed_capture_the_next_tap_is_aimed_from_a_fresh_look(tmp_path, monkeypatch):
    from simula.device.mcp import McpReplyError

    def flaky(clock):
        phone = janitor_like(clock)
        save, armed = phone.screenshot, [True]

        def screenshot(path, size=None):
            if armed[0] and phone.screen == "search":
                armed[0] = False
                raise McpReplyError("screenshot not written")
            return save(path, size)
        phone.screenshot = screenshot
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, flaky)
    stage.explore_app(ex)
    shot_on = None
    for entry in phone.log:
        if entry[0] == "shot":
            shot_on = entry[1]
        elif entry[0] == "tap":
            assert entry[1] == shot_on, f"a tap on {entry[1]} was aimed from a look at {shot_on}"
        elif entry[0] in ("back", "swipe", "launch"):
            shot_on = None
    assert any(entry == ("shot", "search") for entry in phone.log)


def test_a_tap_the_device_refuses_is_logged_as_an_error_not_done(tmp_path, monkeypatch):
    from simula.contracts import ActionLine
    from simula.device.mcp import McpReplyError

    def refuses_once(clock):
        phone = janitor_like(clock)
        tap, armed = phone.tap, [True]

        def refusing_tap(x, y):
            if armed[0] and phone.screen == "root":
                armed[0] = False
                raise McpReplyError("mobile_click_on_screen_at_coordinates failed: 'device offline'")
            tap(x, y)
        phone.tap = refusing_tap
        return phone
    ex, _ = new_explorer(tmp_path, monkeypatch, refuses_once)
    stage.explore_app(ex)
    lines = [ActionLine.model_validate_json(raw) for raw in (ex.out / "actions.jsonl").read_text().splitlines()]
    refused = [line for line in lines if "refused the tap" in line.change_summary]
    assert len(refused) == 1 and refused[0].outcome == "error" and refused[0].to_state is None
    assert len(ex.states) >= 8


def test_a_logo_splash_is_never_taken_for_the_root(tmp_path, monkeypatch):
    from tests.fake_device import Screen

    def logo_first(clock):
        phone = janitor_like(clock)
        logo = {"ref": "@logo", "type": "android.widget.ImageView", "text": "", "label": "App logo",
                "coordinates": {"x": 390, "y": 1050, "width": 300, "height": 300}}
        phone.screens["splash"] = Screen([logo], phone.screens["launch"].image, phone.screens["launch"].package)
        phone.splash, phone.splash_screen = 12, "splash"
        return phone
    ex, _ = new_explorer(tmp_path, monkeypatch, logo_first)
    stage.explore_app(ex)
    assert all(c.label != "App logo" for s in ex.states for c in s.cands)
    assert ex.root is not None and not ex.stop_reason.startswith("blocked root")


def test_a_dump_that_times_out_while_the_launch_screen_settles_still_explores(tmp_path, monkeypatch):
    def settles_slowly(clock):
        phone = janitor_like(clock)
        save, settled = phone.screenshot, []

        def screenshot(path, size=None):
            out = save(path, size)
            if phone.screen == "launch" and not phone.splash_left:
                settled.append(path)
                if len(settled) == 2:
                    phone.hung_lists = 1
            return out
        phone.screenshot = screenshot
        return phone
    ex, _ = new_explorer(tmp_path, monkeypatch, settles_slowly)
    stage.explore_app(ex)
    assert ex.root is not None and len(ex.states) >= 8
