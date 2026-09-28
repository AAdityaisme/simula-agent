"""Step 0: a launch dialog (a subscription announcement, an onboarding modal) is screenshotted and recorded as a
state before it is dismissed; the dismissal never taps its upgrade button."""

import re

from PIL import ImageChops

from simula import runlog
from simula.device.observe import words
from simula.stages import explore as stage
from tests.fake_device import PACKAGE, Clock, FakePhone, capture, fake_jev, fake_sonnet, new_run

LAUNCH_STATES = {
    "janitorai": ("j01_launch", "janitorai-a", "Close subscription announcement"),
    "luzia": ("luzia-pet-intro", "luzia-home", "540,1736"),
}


def launch_explorer(tmp_path, monkeypatch, app: str, strip_dismiss: bool = False):
    dialog, home, close = LAUNCH_STATES[app]
    monkeypatch.setattr(stage.decide, "ask_choice", fake_jev)
    monkeypatch.setitem(stage.llm.PROVIDERS, "anthropic", fake_sonnet)
    clock = Clock()
    screens = {"dialog": capture(app, dialog, package=PACKAGE), "home": capture(app, home, package=PACKAGE)}
    if strip_dismiss:
        screens["dialog"].elements = [e for e in screens["dialog"].elements
                                      if not re.match(r"close|not now", words(e), re.IGNORECASE)]
    taps = {("dialog", close): "home"}
    phone = FakePhone(screens=screens, start="dialog", taps=taps, clock=clock, backs={"dialog": "home"})
    ctx = new_run(tmp_path)
    ex = stage.Explorer(ctx, phone, ctx.run_dir / "explore", clock=clock, sleep=clock.sleep)
    ex.cache_dir = tmp_path / "cache"
    ex.relaunch(first=True)
    return ex, phone


def test_the_dialog_is_recorded_before_it_is_dismissed(tmp_path, monkeypatch):
    for app in LAUNCH_STATES:
        ex, phone = launch_explorer(tmp_path / app, monkeypatch, app)
        dialog, home = ex.states[0], ex.root
        assert dialog.kind == "modal" and dialog.done and dialog.parent == home.sid
        saved = (ex.out / "states" / f"{dialog.sid}.png")
        assert not ImageChops.difference(stage.Image.open(saved).convert("RGB"),
                                         phone.screens["dialog"].image).getbbox()
        notes = [line.note for line in runlog.read_trace(ex.run_dir / "trace.jsonl")]
        recorded = notes.index(next(n for n in notes if n.startswith(f"new modal {dialog.sid}")))
        dismissed = notes.index(next(n for n in notes if "dismiss a launch dialog" in n))
        assert recorded < dismissed
        assert ("tap", "dialog", LAUNCH_STATES[app][2]) in phone.log


def test_back_when_there_is_no_dismiss_control(tmp_path, monkeypatch):
    ex, phone = launch_explorer(tmp_path, monkeypatch, "janitorai", strip_dismiss=True)
    assert ("back", "dialog") in phone.log and ex.current is ex.root and ex.states[0].kind == "modal"


def test_the_upgrade_button_is_never_the_dismissal(tmp_path, monkeypatch):
    _, phone = launch_explorer(tmp_path, monkeypatch, "janitorai")
    assert not any(key == "See janitor+" for _, *rest in phone.log for key in rest[1:])
