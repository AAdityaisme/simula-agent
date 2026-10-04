"""The test password (SIMULA_TEST_PASSWORD), should an element echo it, never reaches a saved element list; the
explorer never taps a control that shows an email, so it never copies one to the clipboard."""

import json

import pytest

from simula.contracts import Rect
from simula.device import observe as ob
from simula.device.observe import ELEMENTS_PREFIX
from simula.stages import explore as stage
from tests.fake_device import new_run


def reply_of(*texts: str) -> dict:
    elements = [{"ref": f"@{n}", "type": "android.widget.TextView", "text": t,
                 "coordinates": {"x": 0, "y": 100 * n, "width": 200, "height": 50}} for n, t in enumerate(texts)]
    return {"content": [{"type": "text", "text": ELEMENTS_PREFIX + json.dumps(elements)}]}


def test_the_test_password_is_hidden_wherever_an_element_echoes_it():
    reply, elements = ob.hide(reply_of("Your password is hunter2pass", "someone@example.com"), "hunter2pass")
    assert [e["text"] for e in elements] == [f"Your password is {ob.HIDDEN}", "someone@example.com"]
    assert "hunter2pass" not in json.dumps(reply)


def test_no_test_password_leaves_the_list_as_it_is():
    reply = reply_of("Ann Lee", "someone@example.com")
    assert ob.hide(reply, "") == (reply, json.loads(reply["content"][0]["text"].removeprefix(ELEMENTS_PREFIX)))


def test_the_small_settle_screenshot_stays_in_scratch(tmp_path, monkeypatch):
    seen = {}

    class Server:
        def __init__(self, cwd):
            pass

        def close(self):
            pass

    def phone(server, package, scratch, serial, avd):
        seen["scratch"] = scratch
        raise RuntimeError("stop before the device")
    monkeypatch.setattr(stage, "Server", Server)
    monkeypatch.setattr(stage, "Phone", phone)
    monkeypatch.setattr(stage, "adb_shell", lambda *a: None)
    ctx = new_run(tmp_path)
    with pytest.raises(RuntimeError):
        stage.run(ctx)
    assert seen["scratch"] == ctx.run_dir / "explore" / ".scratch" and seen["scratch"].is_dir()


def test_a_control_that_shows_an_email_is_never_tapped():
    mine = ob.Candidate(label="someone@example.com", kind="TextView", rect=Rect(x=0, y=0, w=10, h=10), ref="@u",
                        tree_label="someone@example.com")
    assert ob.denied(mine) == "account text"
