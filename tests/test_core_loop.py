"""The core-loop pass: the core action comes from what the tour saw (a chat composer or a feed), and the reply
timing is read from a sequence of trees."""

import json
from pathlib import Path

from simula.contracts import Device, Rect
from simula.device import observe as ob
from simula.device.mcp import parse_elements
from simula.stages import explore as stage

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


def test_a_counter_in_the_header_counts_and_a_changing_reply_does_not():
    def tree(counter, reply):
        return [{"ref": "@e1", "type": "android.widget.TextView", "text": counter,
                 "coordinates": {"x": 700, "y": 180, "width": 300, "height": 50}},
                {"ref": "@e2", "type": "android.widget.TextView", "text": reply,
                 "coordinates": {"x": 42, "y": 1200, "width": 900, "height": 60}}]
    bands = [(0, ob.TOP_CHROME_BOTTOM_PX)]
    assert ob.counters(tree("5 left", "at 3 pm"), tree("4 left", "at 4 pm"), DEVICE, bands) == ["5 left → 4 left"]
    assert ob.counters(tree("5 left", "at 3 pm"), tree("5 left", "at 4 pm"), DEVICE, bands) == []


def test_a_message_timestamp_beside_the_composer_is_not_a_counter():
    def tree(stamp):
        return [{"ref": "@t", "type": "android.widget.TextView", "text": stamp,
                 "coordinates": {"x": 900, "y": 1950, "width": 120, "height": 40}}]
    band = [(1850, 2200)]
    assert ob.counters(tree("5:21 PM"), tree("5:22 PM"), DEVICE, band) == []
    assert ob.counters(tree("1 min ago"), tree("2 min ago"), DEVICE, band) == []
    assert ob.counters(tree("3 messages left"), tree("2 messages left"), DEVICE, band) == ["3 messages left → 2 messages left"]


def control(label, kind, x, y, w, h, ref):
    return ob.Candidate(label=label, kind=kind, rect=Rect(x=x, y=y, w=w, h=h), ref=ref, tree_label=label)


def test_the_send_control_sits_on_the_text_box_row():
    box = control("", "EditText", 42, 2028, 800, 126, "@box")
    feedback = control("Send feedback", "TextView", 42, 300, 400, 60, "@fb")
    arrow = control("", "ImageButton", 900, 2040, 110, 110, "@arrow")
    assert ob.composer([feedback, box, arrow], DEVICE) == (box, arrow)
    assert ob.composer([feedback, box], DEVICE) is None


def test_a_gift_button_on_the_row_is_never_the_send_control():
    box = control("", "EditText", 42, 2028, 700, 126, "@box")
    gift = control("Send gift", "Button", 760, 2040, 110, 110, "@gift")
    send = control("sendButton", "View", 900, 2040, 110, 110, "@send")
    assert ob.composer([box, gift, send], DEVICE) == (box, send)
    for word in ("Send gift", "100 coins", "Gems", "Tip the creator", "Donate", "Buy credits"):
        assert ob.denied(control(word, "Button", 0, 0, 10, 10, "@x"), core=True), word


def test_a_price_is_what_makes_a_paywall():
    def shows(text):
        return ob.priced([{"ref": "@p", "type": "android.widget.TextView", "text": text,
                           "coordinates": {"x": 42, "y": 900, "width": 600, "height": 60}}], DEVICE)
    for text in ("$4.99 / month", "9,99 €", "₹199", "USD 4.99", "$\xa039.99", "£12"):
        assert shows(text), text
    for text in ("18+", "v2.5.0.136", "1,000 members", "Top 10", "Subscription"):
        assert not shows(text), text


def test_a_send_drawn_over_a_reply_the_keyboard_left_under_the_composer_is_still_its_send():
    """JanitorAI's pass 3 on 2026-10-01: the keyboard lifted the composer over a reply paragraph, listed before the
    text box, whose bounds hold the send; the send is listed after the box, so it is drawn over the reply."""
    def element(ref, kind, x, y, w, h, **words):
        return {"ref": ref, "type": f"android.widget.{kind}", **words,
                "coordinates": {"x": x, "y": y, "width": w, "height": h}}
    lifted = [element("@reply", "TextView", 134, 1227, 902, 261, text="He stopped abruptly, his chest heaving."),
              element("@box", "EditText", 46, 1215, 988, 123, text="Hi! What can you help me with?"),
              element("@persona", "Button", 174, 1338, 284, 86, label="Change who you are in this chat"),
              element("@send", "ViewGroup", 922, 1338, 89, 89, label="Send")]
    box, send = ob.composer(ob.controls(lifted, DEVICE), DEVICE)
    assert (box.ref, send.ref) == ("@box", "@send")
    card = [element("@card", "ViewGroup", 0, 900, 1080, 400, text="A card"),
            element("@title", "TextView", 40, 940, 600, 60, text="Its title"),
            element("@open", "Button", 40, 1100, 300, 120, text="Open")]
    assert [c.ref for c in ob.controls(card, DEVICE)] == ["@card"]


# Fable E7: the explorer's own message carried a price, and any price on a chat made the conversation a paywall.
def test_no_conversation_is_a_paywall_only_a_price_on_a_control_is():
    assert not [m for m in stage.CORE_MESSAGES if ob.PRICE.search(m)]
    chat = parse_elements(json.loads((TREES / "luzia" / "luzia-chat-thread.elements.json").read_text()))
    reply = {"ref": "@reply", "type": "android.widget.TextView", "text": "The Pixel 8a at $299 is a solid pick.",
             "coordinates": {"x": 42, "y": 900, "width": 900, "height": 80}}
    plan = {"ref": "@plan", "type": "android.widget.Button", "text": "Get Plus for $4.99/month",
            "coordinates": {"x": 240, "y": 1500, "width": 600, "height": 120}}
    assert ob.priced([reply], DEVICE) and not ob.priced(chat + [reply], DEVICE)
    assert ob.priced(chat + [reply, plan], DEVICE)


def test_a_send_control_in_the_toolbar_under_the_text_box_counts():
    box = control("", "EditText", 46, 2057, 988, 123, "@box")
    persona = control("Change who you are in this chat", "Button", 174, 2180, 284, 86, "@persona")
    write = control("AI write for me", "ViewGroup", 68, 2180, 89, 89, "@write")
    send = control("Send", "ViewGroup", 922, 2180, 89, 89, "@send")
    assert ob.composer([box, write, persona, send], DEVICE) == (box, send)
    far = control("Send", "ViewGroup", 922, 2330, 89, 89, "@far")
    assert ob.composer([box, write, persona, far], DEVICE) is None
