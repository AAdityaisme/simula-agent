"""How an explore ends: a device error or an empty explore is a failure (failure.json), a blocked root seen after
the full splash wait is a result (done.json)."""

from contextlib import nullcontext

import pytest

from simula import cli, runfolder, runlog
from simula.device import observe as ob
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


def anr_dialog() -> list[dict]:
    def element(ref, kind, text, ident, y):
        return {"ref": ref, "type": f"android.widget.{kind}", "text": text, "identifier": f"android:id/{ident}",
                "coordinates": {"x": 120, "y": y, "width": 840, "height": 140}}
    return [element("@a1", "TextView", "Example isn't responding", "alertTitle", 1000),
            element("@a2", "Button", "Close app", "aerr_close", 1160),
            element("@a3", "Button", "Wait", "aerr_wait", 1320)]


def test_only_the_system_dialog_is_an_anr():
    reply = {"ref": "@r", "type": "android.widget.TextView", "text": "Sorry, the server isn't responding right now.",
             "coordinates": {"x": 42, "y": 900, "width": 900, "height": 120}}
    assert ob.anr(anr_dialog()) and not ob.anr([reply])


def test_an_anr_gets_one_wait_tap_by_its_id_and_the_tour_goes_on(tmp_path, monkeypatch):
    def hangs_once(clock):
        phone = janitor_like(clock)
        elements, tap, armed = phone.elements, phone.tap, [True]

        def with_dialog():
            reply, listed = elements()
            return (reply, listed + anr_dialog()) if armed[0] else (reply, listed)

        def tapping(x, y):
            if armed[0] and 1320 <= y < 1460:
                armed[0] = False
                return
            tap(x, y)
        phone.elements, phone.tap = with_dialog, tapping
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, hangs_once)
    stage.explore_app(ex)
    notes = [line.note for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.step == "anr"]
    assert notes == ["app not responding: tapped Wait once"]
    assert not any("not responding" in why for why in ex.relaunch_reasons) and len(ex.states) >= 8


def test_the_exhibit_lists_one_reason_per_relaunch_and_the_refused_one_goes_to_needs_human(tmp_path, monkeypatch):
    ex, _ = new_explorer(tmp_path, monkeypatch, janitor_like)
    for n in range(stage.MAX_RELAUNCHES):
        ex.count_relaunch(f"reason {n}")
    with pytest.raises(stage.Stop, match="relaunch cap"):
        ex.count_relaunch("the refused one")
    assert ex.relaunches == len(ex.relaunch_reasons) == stage.MAX_RELAUNCHES
    human = (ex.run_dir / "needs-human.md").read_text()
    assert "the next was for: the refused one" in human and "# re-runs explore; it starts over" in human


def test_a_failed_model_call_is_counted_for_the_exhibit(tmp_path, monkeypatch):
    from simula import llm
    from simula.contracts import Progress
    ex, _ = new_explorer(tmp_path, monkeypatch, janitor_like)

    def failing(**kwargs):
        raise llm.LLMFailure("timeout", "no answer in 60 s")
    monkeypatch.setattr(llm, "call", failing)
    with pytest.raises(llm.LLMFailure):
        ex.ask("settle", "settle", "text", b"png", Progress)
    assert ex.counts["model call failures"] == 1
    assert "model calls that failed (each traced where it happened): 1" in stage.counters(ex)


def test_a_screen_turned_sideways_is_recorded_as_rotated_and_left_with_back(tmp_path, monkeypatch):
    from PIL import Image

    from simula.contracts import StateFile
    from tests.fake_device import PACKAGE, Screen

    def sideways(clock):
        phone = janitor_like(clock)
        exit_button = {"ref": "@exit", "type": "android.widget.Button", "text": "Exit game",
                       "coordinates": {"x": 60, "y": 40, "width": 300, "height": 120}}
        phone.screens["game"] = Screen([exit_button], Image.new("RGB", (2400, 1080), (10, 90, 40)), PACKAGE)
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "game"
        return phone
    ex, phone, out = through_cli(tmp_path, monkeypatch, sideways, fails=False)
    rotated = [StateFile.model_validate_json((out / "states" / f"{s.sid}.json").read_text())
               for s in ex.states if s.kind == "rotated"]
    assert rotated and not ex.stop_reason.startswith(stage.DEVICE_STOPS)
    assert ("back", "game") in phone.log and not any(e[:2] == ("tap", "game") for e in phone.log)
    assert (out / "done.json").exists()


def explored(tmp_path, monkeypatch, factory, **patches):
    """An explore run through the CLI's run_stage, and the outcome its done.json records."""
    ex, phone = new_explorer(tmp_path, monkeypatch, factory)
    for name, value in patches.items():
        monkeypatch.setattr(ex, name, value)
    monkeypatch.setattr(stage, "run", lambda ctx: stage.explore_app(ex))
    cli.run_stage("explore", ex.ctx, force=True)
    return ex, phone, runfolder.read_done(ex.run_dir / "explore").outcome


def silent(clock):
    phone = janitor_like(clock)

    def send():
        phone.draft, phone.sent = "", phone.sent + 1
    phone.send = send
    return phone


def test_an_explore_that_ran_every_pass_is_complete(tmp_path, monkeypatch):
    ex, phone, outcome = explored(tmp_path, monkeypatch, janitor_like)
    assert ex.core_completed == ex.core_reps and (outcome.status, outcome.reasons) == ("complete", [])


def test_an_explore_stopped_by_the_relaunch_cap_is_partial(tmp_path, monkeypatch):
    def capped(n):
        raise stage.Stop("relaunch cap")
    ex, phone, outcome = explored(tmp_path, monkeypatch, janitor_like, at_core=capped)
    assert outcome.status == "partial" and outcome.resume.startswith("simula explore janitorai --run ")
    assert any(r.startswith(f"it used all {ex.relaunches} relaunches allowed") for r in outcome.reasons)


def test_an_explore_whose_core_loop_was_cut_short_is_partial(tmp_path, monkeypatch):
    ex, phone, outcome = explored(tmp_path, monkeypatch, silent)
    assert phone.sent == ex.core_reps and outcome.status == "partial"
    assert outcome.reasons == [f"the core loop completed 0 of {ex.core_reps} passes "
                               f"(last: {ex.core_results[-1]})"]
    assert "partial output" in (ex.run_dir / "needs-human.md").read_text()


def test_an_explore_whose_paywall_pass_stopped_early_is_partial(tmp_path, monkeypatch):
    def paywall_pass():
        raise stage.Stop("the upsell screen could not be reached")
    ex, phone, outcome = explored(tmp_path, monkeypatch, janitor_like, paywall_pass=paywall_pass)
    assert ex.core_completed == ex.core_reps and outcome.status == "partial"
    assert outcome.reasons == ["the paywall pass stopped early (Stop: the upsell screen could not be reached)"]


def test_an_explore_whose_replay_check_stopped_early_is_partial(tmp_path, monkeypatch):
    def verify_replay():
        raise stage.Stop("the recorded screen could not be reached")
    ex, phone, outcome = explored(tmp_path, monkeypatch, janitor_like, verify_replay=verify_replay)
    assert ex.core_completed == ex.core_reps and outcome.status == "partial"
    assert outcome.reasons == ["the replay check stopped early (Stop: the recorded screen could not be reached)"]
