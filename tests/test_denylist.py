"""The deny-list on strings from all three apps' fixtures. Entry words may open an upsell; confirm words never
run; sending and typing belong to the core loop only."""

import dataclasses

import pytest

from simula.contracts import Device, Rect
from simula.device.observe import Candidate, denied, denied_at, dismiss_control


def control(label: str, kind: str = "TextView") -> Candidate:
    return Candidate(label=label, kind=kind, rect=Rect(x=0, y=300, w=300, h=100), ref="@e1", tree_label=label)


@pytest.mark.parametrize("label", ["Subscribe now", "Restore Purchases", "Delete Account", "Update Email",
                                   "New Persona", "Allow", "Enter your email", "Personas (1)",
                                   "Start 3-day free Trial", "Confirm purchase", "Buy 100 gems", "Logout",
                                   "Continue with Google", "Report"])
def test_denied_everywhere(label):
    assert denied(control(label))


@pytest.mark.parametrize("label", ["Not now", "Limited Only", "Upgrade to Janitor Plus", "Upgrade to Luzia+",
                                   "See janitor+", "Try Luzia", "Try it free*", "See plans", "Go Premium", "Settings"])
def test_allowed_outside_an_upsell(label):
    assert denied(control(label)) is None


@pytest.mark.parametrize("label", ["Continue", "Try it free*", "Get Plus", "Start now", "Unlock all"])
def test_call_to_action_words_are_denied_on_an_upsell(label):
    assert denied(control(label), upsell=True)


def test_sending_and_typing_only_in_the_core_loop():
    assert denied(control("sendButton", "View")) == "send"
    assert denied(control("Ask Teacher", "EditText")) == "text input"
    assert denied(control("sendButton", "View"), core=True) is None
    assert denied(control("Ask Teacher", "EditText"), core=True) is None
    assert denied(control("Subscribe", "View"), core=True)


def test_dismiss_never_picks_a_denied_control():
    assert dismiss_control([control("Cancel subscription"), control("Not now")]).label == "Not now"


def test_a_reason_is_never_its_text_twice():
    assert denied(control("Start free trial")) == "start free trial"


def test_words_under_a_tap_point_never_refuse_it_and_words_drawn_over_it_do():
    """JanitorAI's keyboard-lifted composer (def99ac s27): a reply listed before the text box and not around it lies
    under the box's tap point. A button listed after a row, inside its box, lies over the row's."""
    box = Candidate(label="", kind="EditText", rect=Rect(x=46, y=1215, w=988, h=123), ref="@box", tree_label="")
    def element(ref, kind, text, x, y, w, h):
        return {"ref": ref, "type": f"android.widget.{kind}", "text": text,
                "coordinates": {"x": x, "y": y, "width": w, "height": h}}
    reply = element("@reply", "TextView", "Here are three tips for staying focused", 134, 1103, 902, 300)
    target = element("@box", "EditText", "", 46, 1215, 988, 123)
    assert denied_at(box, [reply, target], Device(), core=True) == ""
    assert "tips" in denied_at(box, [target, reply], Device(), core=True)
    row = Candidate(label="Read item", kind="ViewGroup", rect=Rect(x=0, y=1100, w=1080, h=200), ref="@row",
                    tree_label="Read item")
    rows = [element("@row", "ViewGroup", "Read item", 0, 1100, 1080, 200),
            element("@next", "ViewGroup", "Next item", 0, 1300, 1080, 200)]
    sign = element("@sign", "Button", "Sign in", 440, 1170, 200, 60)
    assert "sign in" in denied_at(row, [*rows, sign], Device())


def test_a_controls_own_icon_listed_right_after_it_is_its_own_not_drawn_over_it():
    """Luzia's send control (a wordless View) lists its icon, labelled "Confirm button", right after it inside its
    box: the target's own, so sending isn't refused; the same words drawn later in the list would be."""
    send = Candidate(label="sendButton", kind="View", rect=Rect(x=933, y=1313, w=126, h=126), ref="@send",
                     tree_label="", ident="sendButton")
    target = {"ref": "@send", "type": "android.view.View", "text": "", "identifier": "app:id/sendButton",
              "coordinates": {"x": 933, "y": 1313, "width": 126, "height": 126}}
    icon = {"ref": "@icon", "type": "android.view.View", "text": "", "label": "Confirm button",
            "coordinates": {"x": 970, "y": 1350, "width": 52, "height": 52}}
    note = {"ref": "@note", "type": "android.widget.TextView", "text": "Luzia is AI and can make mistakes.",
            "coordinates": {"x": 21, "y": 1460, "width": 1038, "height": 36}}
    assert denied_at(send, [target, icon, note], Device(), core=True) == ""
    assert "confirm" in denied_at(send, [target, note, icon], Device(), core=True)



def test_a_reply_listed_before_a_lifted_composers_send_never_refuses_it_and_an_ancestors_words_do():
    """JanitorAI's chat with the keyboard up (PR O's run 20261001-035432-69c8c4f, act114; words shortened): the reply
    and its last paragraph hold Send's box but are no ancestor of it (the text box, listed between, lies outside), so
    a reply saying "Sign in" doesn't refuse the send. Words on an element that holds the target and all listed between
    them, an ancestor's, still do."""
    def element(ref, kind, text, label, x, y, w, h):
        return {"ref": ref, "type": f"android.widget.{kind}", "text": text, "label": label,
                "coordinates": {"x": x, "y": y, "width": w, "height": h}}
    said = "He stopped. Sign in, he said, and he would go."
    chat = [element("@e26", "FrameLayout", "", "", 0, 0, 1080, 2400),
            element("@e33", "ViewGroup", "", f"Miro blinked. {said}", 134, 274, 902, 1243),
            element("@e34", "TextView", "There was once a light.", "", 134, 274, 902, 104),
            element("@e35", "TextView", "He looked at the boots again.", "", 134, 403, 902, 135),
            element("@e36", "TextView", "The light stayed awake.", "", 134, 563, 902, 639),
            element("@e37", "TextView", said, "", 134, 1227, 902, 261),
            element("@e38", "TextView", "That is all I know.", "", 134, 1513, 902, 4),
            element("@e39", "EditText", "What's a good phone for a student?", "", 46, 1215, 988, 123),
            element("@e40", "ViewGroup", "", "AI write for me", 68, 1338, 89, 89),
            element("@e41", "Button", "", "Change who you are in this chat", 174, 1338, 284, 86),
            element("@e42", "TextView", "Persona", "", 270, 1361, 108, 39),
            element("@e43", "ViewGroup", "", "Send", 922, 1338, 89, 89)]
    send = Candidate(label="Send", kind="ViewGroup", rect=Rect(x=922, y=1338, w=89, h=89), ref="@e43", tree_label="Send")
    assert denied_at(send, chat, Device(), core=True) == ""
    card = element("@card", "ViewGroup", "", "Sign in to keep chatting", 900, 1320, 140, 130)
    icon = element("@icon", "ImageView", "", "", 940, 1330, 40, 40)
    assert "sign in" in denied_at(send, [*chat[:-1], card, icon, chat[-1]], Device(), core=True)

def test_the_exhibit_counts_denied_taps_that_reached_the_device(tmp_path, monkeypatch):
    from simula.stages import explore as stage
    from tests.fake_device import explorer
    from tests.test_explore_offline import janitor_like
    ex, phone = explorer(tmp_path, monkeypatch, janitor_like)
    ex.observe()  # the live list a tap is checked against; neither control is in it
    ex.perform(stage.Move("tap", control("Subscribe now")), control("Subscribe now"))
    ex.perform(stage.Move("tap", control("Settings")), control("Settings"))
    assert ex.denied_executed == 1


@pytest.mark.parametrize("label", ["Submit", "Place order", "Checkout", "Proceed", "Donate", "Tip", "Rate",
                                   "Rate this app", "Give 5 stars", "Sign in", "Sign up", "Create account",
                                   "Save changes", "Publish", "Post"])
def test_confirm_and_account_words_are_denied(label):
    assert denied(control(label))


@pytest.mark.parametrize("label", ["Posts", "Tips & tricks", "Rated for teens", "Top rated"])
def test_nearby_words_stay_allowed(label):
    assert denied(control(label)) is None


def test_toggles_are_denied_outside_the_filter_row():
    assert denied(control("Show mature content", "Switch")) == "toggle"
    assert denied(control("", "CheckBox")) == "toggle"
    assert denied(control("Safe mode", "Switch"), toggle_ok=True) is None


@pytest.mark.parametrize("label,kind", [("Hide NSFW", "TextView"), ("Hide NSFW", "Switch"), ("SFW only", "Switch"),
                                        ("Block explicit content", "Switch"), ("Hide mature content", "Button"),
                                        ("Safe mode", "CheckBox"), ("Family mode", "TextView")])
def test_the_filter_questions_own_examples_can_be_applied(label, kind):
    assert denied(control(label, kind), toggle_ok=True) is None


def test_a_filter_row_is_still_held_to_every_word_but_its_own_phrase():
    assert denied(control("Hide NSFW", "Button")) == "hide"
    assert denied(control("Report", "Button"), toggle_ok=True) == "report"
    assert denied(control("Block user", "Switch"), toggle_ok=True) == "block"
    assert denied(control("Hide this post", "Switch"), toggle_ok=True) == "hide"
    assert denied(control("Delete history", "Switch"), toggle_ok=True) == "delete"
    assert denied(control("Unlock mature content", "Button"), upsell=True, toggle_ok=True) == "unlock"


def test_an_age_mark_is_not_an_upsell_entry():
    from simula.device.observe import ENTRY
    assert not ENTRY.search("18+") and ENTRY.search("Upgrade to Luzia+") and ENTRY.search("See janitor+")


def test_replay_taps_pass_the_deny_list_and_the_foreground_check(tmp_path, monkeypatch):
    from simula.device import observe as ob
    from tests.fake_device import capture, explorer
    from tests.test_explore_offline import janitor_like
    ex, phone = explorer(tmp_path, monkeypatch, janitor_like)
    ex.observe()
    paywall = capture("luzia", "luzia-paywall", package=ex.package)
    ex.obs = dataclasses.replace(ex.obs, elements=paywall.elements, fg=ex.package)
    assert not ex.safe_tap(control("Continue"), "replay")
    ex.obs = dataclasses.replace(ex.obs, fg="com.android.chrome")
    assert not ex.safe_tap(control("Settings"), "replay")
    assert not any(entry[0] == "tap" for entry in phone.log) and ex.denied_executed == 0
    assert ob.is_upsell(paywall.elements, ex.device)


@pytest.mark.parametrize("label", ["Stocks slide as report shows hiring slowdown", "Senate votes to block new tariffs",
                                   "Fans like the new season", "Likely", "Followers", "Preview", "Photos",
                                   "Personal Finance", "Remove ads", "Close paywall", "Halloween 2025 badge",
                                   "Apple to pay $490 million to settle the lawsuit",
                                   "Senate votes to confirm the nominee",
                                   "How to delete your old Facebook account", "Why you should not buy a new phone yet",
                                   "Netflix to cancel its cheapest plan", "What payment methods do you accept?"])
def test_whole_words_on_control_shaped_labels_only(label):
    assert denied(control(label)) is None


def test_core_words_are_whole_words():
    assert denied(control("Management"), core=True) is None
    assert denied(control("Bitcoin"), core=True) is None
    assert denied(control("Send 5 coins"), core=True) == "coins"


@pytest.mark.parametrize("label, kind", [("Delete note", "TextView"), ("Unfollow", "TextView"), ("Following", "Button"),
                                         ("Remove from library", "TextView"), ("buttonFavorite", "View"),
                                         ("btn_like", "View"),
                                         ("Delete my account and all of its data", "Button"),
                                         ("Delete my account and all of its data", "TextView"),
                                         ("Buy 1,000 coins for a limited time", "ViewGroup"),
                                         ("Sign in with Google to save your chats", "TextView")])
def test_actions_stay_denied(label, kind):
    assert denied(control(label, kind))


def test_a_long_label_is_denied_when_it_starts_with_the_action():
    assert denied(control("• Delete my account and all of its data")) == "delete"
    assert denied(control("Payment will be charged to your Google Play account")) == "payment"


@pytest.mark.parametrize("label, word", [("Yes, permanently delete my account", "delete"),
                                         ("Permanently delete my account and data", "delete"),
                                         ("I understand, cancel my subscription", "cancel"),
                                         ("Already have an account? Log in", "log in")])
def test_a_command_after_an_interjection_is_still_a_command(label, word):
    assert denied(control(label, "ViewGroup")) == word


def test_remove_ads_opens_an_upsell_and_is_a_confirm_on_one():
    from simula.device.observe import ENTRY
    assert all(ENTRY.search(label) for label in ["Remove ads", "Go ad-free", "No ads for a month"])
    assert denied(control("Remove ads")) is None
    assert denied(control("Remove ads"), upsell=True) == "remove"


def icon(ident: str) -> Candidate:
    return Candidate(label=ident, kind="View", rect=Rect(x=37, y=152, w=126, h=126), ref="@e27", tree_label="",
                     ident=ident)


def test_an_icon_only_close_named_by_its_id_is_allowed_and_text_commands_are_not():
    assert denied(icon("login-close-button")) is None
    assert [denied(control(label)) for label in ("Sign in", "Log in", "Close account")] == \
        ["sign in", "log in", "close account"]
    assert denied(control("Sign-in with Google")) == "sign in" and denied(control("E-mail")) == "e mail"


@pytest.mark.parametrize("ident", ["delete-button", "btn_report", "buyButton", "delete-and-close", "confirm-close",
                                   "skip-and-subscribe", "cancel-subscription-dismiss", "login-button"])
def test_an_icon_only_control_is_released_only_from_the_sign_in_words_and_only_when_it_dismisses(ident):
    assert denied(icon(ident))
