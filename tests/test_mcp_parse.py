"""Real saved mobile-mcp replies from all three apps parse; malformed ones fail loudly."""

import json
from pathlib import Path

import pytest
from PIL import Image

from simula.device.mcp import McpReplyError, check_png, parse_elements, parse_foreground, parse_screen_size

TREES = Path(__file__).parent / "fixtures" / "trees"


def reply(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


@pytest.mark.parametrize("path", sorted(TREES.glob("*/*.elements.json")), ids=lambda p: p.stem)
def test_every_saved_element_list_parses(path):
    elements = parse_elements(json.loads(path.read_text()))
    assert elements and all({"x", "y", "width", "height"} <= e["coordinates"].keys() for e in elements)


@pytest.mark.parametrize("path", sorted(TREES.glob("*/*.foreground.json")), ids=lambda p: p.stem)
def test_every_saved_foreground_reply_names_a_package(path):
    package = parse_foreground(json.loads(path.read_text()))
    assert "." in package and " " not in package


def test_a_move_out_of_the_app_reads_as_the_other_package():
    assert parse_foreground(json.loads((TREES / "aol" / "aol-article-external.foreground.json").read_text())) \
        == "com.android.chrome"


@pytest.mark.parametrize("bad", [
    reply("Found these elements on screen: []", error=True),
    reply("Screenshot saved to: /tmp/x.png"),
    reply('Found these elements on screen: [{"ref": "@e1", "text": "no box"}]'),
    reply('Found these elements on screen: {"not": "a list"}'),
])
def test_malformed_element_replies_are_rejected(bad):
    with pytest.raises(McpReplyError):
        parse_elements(bad)


def test_error_replies_and_sizes():
    with pytest.raises(McpReplyError):
        parse_foreground(reply("Foreground app: X (com.x)", error=True))
    assert parse_screen_size(reply("Screen size is 1080x2400 pixels")) == (1080, 2400)


def test_a_capture_turned_sideways_is_a_capture_and_any_other_size_is_not(tmp_path):
    for size, ok in (((1080, 2400), True), ((2400, 1080), True), ((1080, 1200), False)):
        path = tmp_path / f"{size[0]}x{size[1]}.png"
        Image.new("RGB", size).save(path)
        if ok:
            check_png(path, (1080, 2400))
        else:
            with pytest.raises(McpReplyError):
                check_png(path, (1080, 2400))
