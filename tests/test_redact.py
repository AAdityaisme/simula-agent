"""The account handle and any email never reach a saved file: listed strings (any case) and email addresses are
replaced in any string of an element and painted over in the screenshot at capture, where the element is when the
screenshot is taken too. The explorer never taps the account's own name, so it never copies it to the clipboard."""

import json

import pytest
from PIL import Image

from simula.contracts import Rect
from simula.device import observe as ob
from simula.device.observe import ELEMENTS_PREFIX, REDACTED, redact
from simula.stages import explore as stage
from tests.fake_device import Screen, new_run
from tests.fake_device import explorer as new_explorer
from tests.test_explore_offline import janitor_like


def element(ref, text, x, y, label=None):
    e = {"ref": ref, "type": "android.widget.TextView", "text": text, "coordinates": {"x": x, "y": y, "width": 200,
                                                                                         "height": 50}}
    return {**e, "label": label} if label else e


def test_handles_and_emails_are_replaced_and_painted_over():
    elements = [element("@e1", "@SecretHandle", 100, 300), element("@e2", "Contact me at a.b+c@mail.example.org", 100, 500),
                element("@e3", "Settings", 100, 700), element("@e4", "", 100, 900, label="Profile of secrethandle")]
    reply = {"content": [{"type": "text", "text": ELEMENTS_PREFIX + json.dumps(elements)}], "isError": False}
    image = Image.new("RGB", (1080, 2400), (200, 200, 200))
    redacted, parsed, hits = redact(reply, image, ["@secrethandle", "SecretHandle", " "])
    text = json.dumps(redacted)
    assert "ecrethandle" not in text.lower() and "mail.example.org" not in text
    assert [e.get("text") for e in parsed][:3] == [REDACTED, f"Contact me at {REDACTED}", "Settings"]
    assert parsed[3]["label"] == f"Profile of {REDACTED}"
    assert image.getpixel((150, 320)) == image.getpixel((150, 520)) == image.getpixel((150, 920)) == (0, 0, 0)
    assert image.getpixel((150, 720)) == (200, 200, 200)
    assert redacted["content"][0]["text"].startswith(ELEMENTS_PREFIX)
    assert hits == 3


def test_nothing_listed_still_redacts_emails():
    reply = {"content": [{"type": "text", "text": ELEMENTS_PREFIX + json.dumps([element("@e1", "x@y.io", 0, 200)])}]}
    _, parsed, hits = redact(reply, Image.new("RGB", (1080, 2400)), [])
    assert parsed[0]["text"] == REDACTED and hits == 1


def test_explore_refuses_to_start_without_a_redact_list(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMULA_REDACT", " , ")
    monkeypatch.setattr(stage, "resolve_serial", lambda flag: pytest.fail("the device was touched"))
    ctx = new_run(tmp_path)
    with pytest.raises(stage.ExploreFailed, match="SIMULA_REDACT is empty"):
        stage.run(ctx)
    assert "SIMULA_REDACT is empty" in (ctx.run_dir / "needs-human.md").read_text()


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


SECRET = "fakehandle9"


def test_every_string_of_an_element_is_redacted_and_a_moved_screen_is_painted_where_it_is(tmp_path, monkeypatch):
    chip = {"ref": "@chip", "type": "android.widget.TextView", "text": "", "hint": f"paste {SECRET}",
            "coordinates": {"x": 40, "y": 350, "width": 300, "height": 90}}

    def moving(clock):
        phone = janitor_like(clock)
        root = phone.screens["root"]
        phone.screens["root"] = Screen([*root.elements, chip], root.image, root.package)
        lists = phone.elements

        def elements():
            reply, found = lists()
            if phone.screen == "root" and phone.log and phone.log[-1][0] == "shot":
                moved = [{**e, "coordinates": {**e["coordinates"], "y": 1200}} if e["ref"] == "@chip" else e
                         for e in found]
                return {"content": [{"type": "text", "text": ob.ELEMENTS_PREFIX + json.dumps(moved)}]}, moved
            return reply, found
        phone.elements = elements
        return phone
    monkeypatch.setenv("SIMULA_REDACT", SECRET)
    ex, phone = new_explorer(tmp_path, monkeypatch, moving)
    phone.screen = "root"
    obs = ex.observe()
    assert all(SECRET not in json.dumps(e) for e in obs.elements)
    assert obs.image.getpixel((100, 380)) == obs.image.getpixel((100, 1230)) == (0, 0, 0)
    saved = Image.open(ex.scratch / "now.png").convert("RGB")
    assert saved.getpixel((100, 380)) == saved.getpixel((100, 1230)) == (0, 0, 0)


def test_a_control_that_shows_the_account_name_is_never_tapped():
    mine = ob.Candidate(label=ob.REDACTED, kind="TextView", rect=Rect(x=0, y=0, w=10, h=10), ref="@u",
                        tree_label=ob.REDACTED)
    assert ob.denied(mine) == "account text"
