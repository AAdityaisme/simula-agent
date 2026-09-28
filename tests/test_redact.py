"""The account handle and any email never reach a saved file: listed strings (any case) and email addresses are
replaced in the element list and painted over in the screenshot at capture."""

import json

from PIL import Image

from simula.device.observe import ELEMENTS_PREFIX, REDACTED, redact


def element(ref, text, x, y, label=None):
    e = {"ref": ref, "type": "android.widget.TextView", "text": text, "coordinates": {"x": x, "y": y, "width": 200,
                                                                                         "height": 50}}
    return {**e, "label": label} if label else e


def test_handles_and_emails_are_replaced_and_painted_over():
    elements = [element("@e1", "@SecretHandle", 100, 300), element("@e2", "Contact me at a.b+c@mail.example.org", 100, 500),
                element("@e3", "Settings", 100, 700), element("@e4", "", 100, 900, label="Profile of secrethandle")]
    reply = {"content": [{"type": "text", "text": ELEMENTS_PREFIX + json.dumps(elements)}], "isError": False}
    image = Image.new("RGB", (1080, 2400), (200, 200, 200))
    redacted, parsed = redact(reply, image, ["@secrethandle", "SecretHandle", " "])
    text = json.dumps(redacted)
    assert "ecrethandle" not in text.lower() and "mail.example.org" not in text
    assert [e.get("text") for e in parsed][:3] == [REDACTED, f"Contact me at {REDACTED}", "Settings"]
    assert parsed[3]["label"] == f"Profile of {REDACTED}"
    assert image.getpixel((150, 320)) == image.getpixel((150, 520)) == image.getpixel((150, 920)) == (0, 0, 0)
    assert image.getpixel((150, 720)) == (200, 200, 200)
    assert redacted["content"][0]["text"].startswith(ELEMENTS_PREFIX)


def test_nothing_listed_still_redacts_emails():
    reply = {"content": [{"type": "text", "text": ELEMENTS_PREFIX + json.dumps([element("@e1", "x@y.io", 0, 200)])}]}
    _, parsed = redact(reply, Image.new("RGB", (1080, 2400)), [])
    assert parsed[0]["text"] == REDACTED
