"""The core-loop pass: the core action comes from what the tour saw (a chat composer or a feed), and the reply
timing is read from a sequence of trees."""

import json
from pathlib import Path

from simula.contracts import Device
from simula.device import observe as ob
from simula.device.mcp import parse_elements

TREES = Path(__file__).parent / "fixtures" / "trees"
DEVICE = Device()


def cands(app: str, name: str) -> list[ob.Candidate]:
    return ob.controls(parse_elements(json.loads((TREES / app / f"{name}.elements.json").read_text())), DEVICE)


def test_a_chat_screen_yields_a_composer_and_its_send_control():
    box, send = ob.composer(cands("luzia", "luzia-chat-thread"), DEVICE)
    assert box.kind == "EditText" and send.ident == "sendButton"


def test_a_text_box_without_send_is_not_a_chat():
    assert ob.composer(cands("janitorai", "j02_home"), DEVICE) is None
    assert ob.composer(cands("aol", "aol-home"), DEVICE) is None


def test_feed_screens_yield_their_items():
    titles = ob.feed_items(cands("aol", "aol-home"), DEVICE)
    assert len(titles) >= 2 and all(t.kind == "TextView" for t in titles)
    cards = ob.feed_items(cands("janitorai", "janitorai-limited"), DEVICE)
    assert {c.tree_label for c in cards} >= {"JJK - GOJO’S RELATIVE"}
    assert not ob.feed_items(cands("luzia", "luzia-tab-services"), DEVICE)


def texts_of(name: str, extra: str | None = None) -> frozenset[str]:
    elements = parse_elements(json.loads((TREES / "luzia" / f"{name}.elements.json").read_text()))
    if extra:
        elements.append({"ref": "@r", "type": "android.widget.TextView", "text": extra,
                         "coordinates": {"x": 42, "y": 900, "width": 996, "height": 200}})
    return frozenset(ob.texts(elements, DEVICE))


def test_reply_timing_from_a_sequence_of_trees():
    before = texts_of("luzia-chat-thread")
    reply = "Once upon a time a lighthouse keeper counted ships."
    frames = [None, None, reply[:10], reply[:30], reply, reply, reply]
    samples = [(0.5 * n, texts_of("luzia-chat-thread", frame) - before) for n, frame in enumerate(frames)]
    started, finished, chars = ob.reply_timing(samples)
    assert (started, finished, chars) == (1.0, 2.0, len(reply))
    assert ob.timing_line("reply", samples, 60) == f"reply started 1.0 s, finished 2.0 s, {len(reply)} chars"


def test_no_reply_is_reported_as_such():
    assert ob.timing_line("reply", [(0.5, frozenset()), (1.0, frozenset())], 60) == "no reply within 60 s"


def test_upsell_screens_are_recognized():
    paywall = parse_elements(json.loads((TREES / "luzia" / "luzia-paywall.elements.json").read_text()))
    home = parse_elements(json.loads((TREES / "aol" / "aol-home.elements.json").read_text()))
    assert ob.is_upsell(paywall, DEVICE) and not ob.is_upsell(home, DEVICE)
