"""The account handle and any email never reach a saved file: listed strings (any case) and email addresses are
replaced in the element list and painted over in the screenshot at capture."""

import json

import pytest
from PIL import Image

from simula.device.observe import ELEMENTS_PREFIX, REDACTED, redact
from simula.stages import explore as stage
from tests.fake_device import new_run


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

    def phone(server, package, scratch, names):
        seen["scratch"] = scratch
        raise RuntimeError("stop before the device")
    monkeypatch.setattr(stage, "Server", Server)
    monkeypatch.setattr(stage, "Phone", phone)
    monkeypatch.setattr(stage, "adb_shell", lambda *a: None)
    ctx = new_run(tmp_path)
    with pytest.raises(RuntimeError):
        stage.run(ctx)
    assert seen["scratch"] == ctx.run_dir / "explore" / ".scratch" and seen["scratch"].is_dir()
