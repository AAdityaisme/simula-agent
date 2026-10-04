"""The agent's hard blocks: the Play Store gets BACK, Google's account chooser is part of signing in, a small generic
word list refuses account, public, social, purchase and (outside the core loop) send taps, also at the tap point and
on a dialog's confirm, and the agent types only the fixed neutral texts."""

import json
from pathlib import Path

import pytest

from simula.contracts import Rect
from simula.device import guard
from simula.device.mcp import parse_elements
from simula.device.observe import center, inside, rect
from simula.stages.explore import CORE_MESSAGES, SEARCH_QUERIES
from tests.fake_device import capture
from tests.test_invariants import APP_WORDS

CHAT = Path(__file__).parent / "fixtures" / "arrival" / "janitorai" / "20260928-095854-s21.elements.json"

def shown(words: str, y: int, x: int = 42, w: int = 996, h: int = 100) -> dict:
    return {"ref": f"@{y}", "type": "android.widget.TextView", "text": words,
            "coordinates": {"x": x, "y": y, "width": w, "height": h}}


def test_the_play_store_is_billing_and_googles_account_chooser_is_signing_in():
    assert guard.in_billing("com.android.vending") and not guard.in_billing(guard.ACCOUNT_CHOOSER)
    assert guard.signing_in("com.google.android.gms")
    assert not guard.signing_in(guard.BILLING) and not guard.signing_in("com.example.app")


@pytest.mark.parametrize("element", [{"text": "Delete account"}, {"label": "Sign out"}, {"text": "LOG OUT"},
                                     {"text": "Yes, permanently delete my account"}, {"text": "Deactivate"},
                                     {"text": "Change password"}, {"text": "Update Email"}, {"text": "Phone number"},
                                     {"text": "Post"}, {"label": "Share"}, {"text": "Publish"},
                                     {"identifier": "com.example.app:id/btn_logout"},
                                     {"identifier": "deleteAccountButton"}, {"text": "Sign\nout"}])
def test_a_hard_block_word_in_the_text_label_or_id_refuses_the_tap(element):
    assert guard.blocked_tap(element)


# Not handled (triage of the red team on 87a3bc3): look-alike letters ("pоst" with a Cyrillic o), letter-spaced labels
# ("S H A R E"), wordless toggles, a Save or Confirm with no blocked word on the screen, "Use suggested username",
# Subscribe, Upgrade and free trials (Play billing gets BACK), and description keys the driver doesn't emit.
@pytest.mark.parametrize("element", [{"label": "Sign–out"}, {"text": "Log—out"}, {"text": "Delete/account"},
                                     {"label": "Delete.account"}, {"identifier": "delete.account.button"},
                                     {"label": "Change e-mail"}, {"text": "Change e‑mail"},
                                     {"label": "Ｐｏｓｔ"}, {"label": "Ｓｈａｒｅ"},
                                     {"text": "P​ost"}, {"label": "Sha‍re"}, {"text": "Pub­lish"},
                                     {"label": "Sign​out"}, {"identifier": "com.example.app:id/btnDELETEAccount"},
                                     {"identifier": "com.example.app:id/deleteAccount2"},
                                     {"identifier": "com.example.app:id/btn_post2"},
                                     {"identifier": "com.example.app:id/btnPOSTAction"},
                                     {"identifier": "com.example.app:id/LOGOUTButton"},
                                     {"identifier": "com.example.app:id/signOUTButton"},
                                     {"identifier": "com.example.app:id/changePassword2"}])
def test_dashes_slashes_invisible_characters_full_width_letters_and_id_word_breaks_still_refuse(element):
    assert guard.blocked_tap(element)


@pytest.mark.parametrize("words", ["Invite", "Follow", "Unfollow", "Like", "Unlike", "React", "Report user", "Block",
                                   "Send request", "Accept request", "Friend request", "Add friend"])
def test_actions_toward_other_people_are_refused(words):
    assert guard.blocked_tap({"label": words})


def test_an_icon_only_like_is_refused_by_its_id_and_its_counts_and_lists_are_not():
    assert guard.blocked_tap({"identifier": "creationDetailLike"}) == "like"  # a recorded heart icon's id
    for words in ("Following", "Followers", "Likes", "Liked by you"):
        assert guard.blocked_tap({"text": words}) is None


@pytest.mark.parametrize("words", ["Remove account", "Delete your profile", "Delete the account", "Account deletion",
                                   "Deactivate my profile", "Edit profile", "Change username", "Reset username",
                                   "Remove profile photo", "Make profile public", "Enable two-factor authentication",
                                   "Set up 2FA", "Turn on two-step verification", "Change recovery address"])
def test_account_deletion_and_profile_or_security_changes_are_refused(words):
    assert guard.blocked_tap({"text": words})


@pytest.mark.parametrize("words", ["Buy now", "Place order", "Pay $4.99", "Confirm purchase", "Complete purchase",
                                   "Checkout", "Send gift", "Tip $1"])
def test_a_purchase_inside_the_app_is_refused(words):
    assert guard.blocked_tap({"text": words})


@pytest.mark.parametrize("element", [{"text": "Continue with Google"}, {"text": "Sign in"}, {"text": "Settings"},
                                     {"label": "Posts"}, {"text": "Shared with you"}, {"text": "Delete chat"},
                                     {"text": "Subscribe now"}, {"text": "Start free trial"}, {"text": "See plans"},
                                     {"identifier": "com.share.post.app:id/home_tab"}, {},
                                     {"text": "Read article", "identifier": "com.example.app:id/post_item"},
                                     {"text": "Open story", "identifier": "com.example.app:id/share_card"}])
def test_sign_in_plans_package_words_and_a_rows_generic_id_are_no_hard_block(element):
    assert guard.blocked_tap(element) is None


@pytest.mark.parametrize("words", ["Send", "Submit", "Reply", "Add comment", "Post comment", "Send message"])
def test_sending_is_refused_outside_the_core_loop(words):
    assert guard.blocked_tap({"text": words})


def test_the_core_loop_may_send_and_nothing_else_opens_up():
    assert guard.blocked_tap({"text": "Send"}, core=True) is None
    assert guard.blocked_tap({"text": "Post"}, core=True) and guard.blocked_tap({"text": "Delete account"}, core=True)


def test_a_tap_point_under_a_worded_blocked_row_is_refused_and_an_id_only_one_is_not():
    sheet = capture("janitorai", "j17_character_sheet").elements
    for ref in ("@e50", "@e54"):  # an author name and a wordless icon under the open sheet's "Share character" row
        under = next(e for e in sheet if e["ref"] == ref)
        assert guard.blocked_tap(under) is None and guard.blocked_tap(under, sheet) == "share (at the tap point)"
    card = {"ref": "@c", "type": "android.view.ViewGroup", "identifier": "com.example.app:id/share_container",
            "coordinates": {"x": 0, "y": 300, "width": 1080, "height": 600}}
    title = shown("Open", 400)
    assert guard.blocked_tap(title, [card, title]) is None


@pytest.mark.parametrize("title", ["Delete account", "Publish", "Change email", "Delete your account?"])
@pytest.mark.parametrize("commit", ["Confirm", "Yes", "OK", "Delete", "Remove", "Proceed", "I understand"])
def test_a_confirm_on_a_dialog_that_names_a_blocked_action_is_refused(title, commit):
    button = shown(commit, 800, w=300)
    assert guard.blocked_tap(button) is None
    assert guard.blocked_tap(button, [shown(title, 400), button]).startswith(commit.lower())


@pytest.mark.parametrize("glyph,ident", [("↑", "btn_post"), ("↗", "button_share"),
                                         ("♥", "creationDetailLike"), ("2K", "creationDetailLike"),
                                         ("➤", "button_send")])
def test_an_icons_glyph_or_count_never_hides_its_id(glyph, ident):
    icon = {**shown(glyph, 300, w=84, h=84), "identifier": f"com.example.app:id/{ident}"}
    assert guard.blocked_tap(icon, [icon])


@pytest.mark.parametrize("title", ["Delete your app’s account", "Remove this app's account",
                                   "Delete your Example App account"])
def test_a_possessive_or_a_products_name_before_the_account_still_names_its_deletion(title):
    confirm = shown("Confirm", 800, w=300)
    assert guard.blocked_tap(shown(title, 400)) and guard.blocked_tap(confirm, [shown(title, 400), confirm])


def test_whitespace_text_never_hides_a_label_from_the_screens_checks():
    title, confirm = {**shown(" ", 400), "label": "Delete account"}, shown("Confirm", 800, w=300)
    assert guard.blocked_tap(confirm, [title, confirm]) == "confirm (delete account on the screen)"
    row, name = {**shown(" ", 1000, h=200), "label": "Share"}, shown("Author", 1050, w=200)
    assert guard.blocked_tap(name, [row, name]) == "share (at the tap point)"


def test_the_core_loops_send_on_a_real_chat_is_not_blocked_by_the_storys_container_around_it():
    chat = parse_elements(json.loads(CHAT.read_text()))
    send = next(e for e in chat if e["ref"] == "@e48")
    assert guard.blocked_tap(send, chat, core=True) is None


def test_an_ai_reply_that_says_log_out_around_the_real_chats_core_send_leaves_send_allowed():
    chat = parse_elements(json.loads(CHAT.read_text()))
    send = next(e for e in chat if e["ref"] == "@e48")
    reply = shown("Sure! If you ever want to log out, open the menu and pick the last option at the bottom.", 300,
                  x=0, w=1080, h=2100)
    x, y = center(rect(send))
    assert guard.blocked_tap(reply) is None and inside(Rect(x=x, y=y, w=0, h=0), rect(reply))
    assert guard.blocked_tap(send, [*chat, reply], core=True) is None


def test_a_word_inside_prose_is_no_button_and_a_long_text_never_blocks_a_tap_inside_it_but_still_guards_a_confirm():
    bubble, copy = shown("what would you like to know", 600, h=300), shown("Copy", 700, w=200)
    assert guard.blocked_tap(bubble, [bubble]) is None and guard.blocked_tap(copy, [bubble, copy]) is None
    assert guard.blocked_tap({"text": "Characters like this one show up here"}) is None
    story = shown("She said she would delete my account if I ever told anyone the secret", 600, h=900)
    confirm = shown("Confirm", 1600, w=300)
    assert guard.blocked_tap(story) is None and guard.blocked_tap(copy, [story, copy, confirm]) is None
    assert guard.blocked_tap(confirm, [story, copy, confirm]) == "confirm (delete my account on the screen)"


@pytest.mark.parametrize("words", ["Pay $4.99 with saved card", "Post to my public profile",
                                   "Share with friends and family", "Report this user for spam",
                                   "Send to all selected contacts", "Send this reply back to Alex"])
def test_a_long_button_label_that_starts_with_a_one_word_entry_is_refused(words):
    assert guard.blocked_tap({"type": "android.widget.Button", "text": words})
    assert guard.blocked_tap({"type": "android.view.ViewGroup", "label": words})


@pytest.mark.parametrize("ident,word", [("toolbar_menu_action_share_button", "share"),
                                        ("chat_message_composer_send_button", "send")])
def test_an_ids_words_count_at_any_length(ident, word):
    icon = {"identifier": f"com.example.app:id/{ident}"}
    assert guard.blocked_tap(icon) == word and guard.blocked_tap({**icon, "text": "\u2197"}) == word
    assert (guard.blocked_tap(icon, core=True) is None) is (word == "send")


@pytest.mark.parametrize("words", ["Change the password for my account", "Enable 2FA for your account",
                                   "Forgot password?"])
def test_a_password_or_2fa_change_is_refused_in_any_text_that_opens_with_it(words):
    assert guard.blocked_tap({"text": words}) and guard.blocked_tap({"text": words}, core=True)


def test_a_button_that_changes_a_password_is_refused_at_any_length_and_position():
    button = {"type": "android.widget.Button", "text": "Tap here if you want to change your password"}
    assert guard.blocked_tap(button) == "change your password"


def test_the_core_loop_lifts_only_the_send_family_on_a_long_label_too():
    button = {"type": "android.widget.Button"}
    assert guard.blocked_tap({**button, "text": "Send this reply back to Alex"}, core=True) is None
    for words in ("Post to my public profile", "Pay $4.99 with saved card", "Share with friends and family"):
        assert guard.blocked_tap({**button, "text": words}, core=True)


def test_a_long_affirmative_under_a_short_deletion_title_is_refused():
    yes = shown("Yes, I am absolutely sure", 800, w=600)
    assert guard.blocked_tap(yes, [shown("Delete account", 400), yes]) == "yes (delete account on the screen)"


@pytest.mark.parametrize("title", ["Are you sure you want to delete your account?",
                                   "Delete your account and all saved data",
                                   "Change email address for your existing account",
                                   "Make profile public for everyone to see"])
def test_a_long_dialog_title_that_names_a_blocked_action_still_refuses_its_confirm(title):
    confirm = shown("Confirm", 800, w=300)
    assert guard.blocked_tap(confirm, [shown(title, 400), confirm])


@pytest.mark.parametrize("kind", ["android.widget.Button", "android.widget.TextView", "android.view.ViewGroup"])
def test_a_glyph_inside_a_long_row_that_opens_with_a_blocked_entry_is_refused_at_its_tap_point(kind):
    row = {**shown("Send message to all selected group members", 700, h=200), "type": kind}
    icon = shown("➤", 750, x=800, w=80, h=80)
    assert guard.blocked_tap(icon, [row, icon]) == "send message (at the tap point)"


def test_an_ai_reply_that_opens_with_share_around_the_real_chats_core_send_leaves_send_allowed():
    chat = parse_elements(json.loads(CHAT.read_text()))
    send = next(e for e in chat if e["ref"] == "@e48")
    reply = shown("Share your favourite memory from this week and I will turn it into a short story.", 300,
                  x=0, w=1080, h=2100)
    assert guard.blocked_tap(reply, core=True) is None
    assert guard.blocked_tap(send, [*chat, reply], core=True) is None


def test_a_long_text_that_names_a_blocked_entry_only_mid_sentence_never_blocks_a_tap_inside_it():
    text = shown("You can always send message requests later from the settings page", 700, h=200)
    icon = shown("➤", 750, x=800, w=80, h=80)
    assert guard.blocked_tap(text) is None and guard.blocked_tap(icon, [text, icon]) is None


def test_a_two_letter_word_is_readable_so_a_go_button_submits_a_search_and_a_glyph_still_shows_its_id():
    go = {**shown("Go", 800, w=200), "identifier": "com.example.app:id/btn_submit"}
    query = {**shown("", 400), "label": "Search", "type": "android.widget.EditText"}
    assert guard.blocked_tap(go, [query, go]) is None
    assert guard.blocked_tap({"text": "OK", "identifier": "com.example.app:id/btn_submit"}) is None
    assert guard.blocked_tap({"text": "2K", "identifier": "com.example.app:id/btn_submit"}) == "submit"


def test_saving_next_to_a_sign_out_row_and_deleting_a_chat_stay_allowed():
    for commit in ("Save", "Done", "Apply", "Continue"):
        button = shown(commit, 800, w=300)
        assert guard.blocked_tap(button, [shown("Sign out", 400), button]) is None
    delete = shown("Delete", 800, w=300)
    assert guard.blocked_tap(delete, [shown("Delete chat?", 400), delete]) is None


def test_the_agent_types_only_a_core_message_in_the_core_loop_or_a_search_query_in_a_search_box():
    assert guard.allowed_text(CORE_MESSAGES[0], core=True, field="composer")
    assert not guard.allowed_text(CORE_MESSAGES[0], core=False, field="composer")
    assert not guard.allowed_text(CORE_MESSAGES[0], core=True, field="email")
    assert not guard.allowed_text("hello", core=True, field="composer")
    assert guard.allowed_text("popular", core=False, field="search") and "popular" in SEARCH_QUERIES
    assert not guard.allowed_text("hello", core=False, field="search")
    assert not guard.allowed_text("popular", core=False, field="composer")
    assert not guard.allowed_text(CORE_MESSAGES[0] + " ", core=True, field="composer")


def test_the_core_loop_starts_only_when_an_ai_receives_it():
    assert guard.core_allowed("ai")
    assert not guard.core_allowed("person") and not guard.core_allowed("none")


def test_the_word_lists_name_no_app():
    listed = [w for key in ("words", "patterns", "outside_core", "confirm") for w in guard.BLOCKS[key]]
    assert listed and not [w for w in listed for app in APP_WORDS if app in w.lower()]


def box(ref: str, kind: str, x: int, y: int, w: int, h: int, text: str = "", label: str = "", ident: str = "") -> dict:
    return {"ref": ref, "type": f"android.{kind}", "text": text, "label": label,
            "identifier": f"com.example.app:id/{ident}", "coordinates": {"x": x, "y": y, "width": w, "height": h}}


# A news app's saved-articles list, as recorded on 2026-10-03: each card holds a headline, a remove icon and a share
# icon, both named only by their accessibility label.
SAVED = [box("@e40", "widget.FrameLayout", 0, 0, 1080, 2400),
         box("@e41", "view.ViewGroup", 0, 294, 1080, 367, ident="root_view"),
         box("@e43", "widget.TextView", 100, 322, 120, 43, "Parade", ident="tv_articleProvider"),
         box("@e44", "widget.TextView", 53, 380, 646, 201, "Doctors recommend getting your flu shot by this date",
             ident="tv_articleTitle"),
         box("@e46", "widget.ImageView", 195, 581, 42, 42, label="Remove this article from your saved list",
             ident="iv_save"),
         box("@e47", "widget.ImageView", 279, 581, 42, 42, label="Share this news article", ident="iv_share")]


def test_removing_a_saved_item_is_allowed_beside_its_share_icon_and_the_share_icon_is_refused():
    remove, share = SAVED[4], SAVED[5]
    assert guard.blocked_tap(remove, SAVED) is None
    assert guard.blocked_tap(share, SAVED) == "share"


@pytest.mark.parametrize("element", [
    box("@e39", "widget.TextView", 0, 649, 474, 57, "Biometric & Password", "Biometric & Password", "settings_title"),
    box("@e92", "widget.TextView", 53, 2087, 646, 201,
        "Trump shares Republican senator's phone number in feud over time switch", ident="tv_articleTitle"),
    shown("Recovery", 2250, w=200), shown("Washington Post", 300), shown("Special report", 300),
    shown("Daily tip", 300), shown("Sleep report", 300), shown("You Might Also Like", 300),
    shown("We sent a link to reset your password", 300), shown("Two-step verification", 300)])
def test_headings_rows_and_articles_that_mention_a_blocked_word_are_content(element):
    assert guard.blocked_tap(element, [element]) is None


@pytest.mark.parametrize("element,word", [
    ({"type": "android.widget.Button", "text": "Post"}, "post"),
    ({"type": "android.widget.Button", "text": "Send to Alex"}, "send"),
    ({"type": "android.widget.ImageView", "label": "Share"}, "share"),
    ({"type": "android.widget.Button", "text": "Report"}, "report"),
    ({"type": "android.view.ViewGroup", "label": "Block user"}, "block"),
    ({"type": "android.widget.Button", "text": "Follow"}, "follow"),
    ({"type": "android.widget.Button", "text": "Buy now"}, "buy now"),
    ({"type": "android.widget.TextView", "text": "Post"}, "post"),
    ({"type": "android.widget.TextView", "text": "Delete account"}, "delete account"),
    ({"type": "android.widget.TextView", "text": "Change password"}, "change password"),
    ({"type": "android.widget.TextView", "text": "Enable 2FA"}, "enable 2 fa"),
    ({"type": "android.widget.TextView", "text": "Log out"}, "log out"),
    ({"type": "android.widget.Button", "label": "", "text": "",
      "identifier": "com.example.app:id/toolbar_share_button"}, "share")])
def test_real_controls_stay_refused(element, word):
    assert guard.blocked_tap(element) == word


def test_a_confirm_is_judged_by_its_own_dialogs_text_not_the_page_behind_it():
    page = [box("@1", "widget.FrameLayout", 0, 0, 1080, 2400),
            box("@2", "widget.ImageView", 900, 300, 84, 84, label="Share"),
            box("@3", "widget.TextView", 42, 500, 600, 60, "Log out")]
    clear = [box("@10", "widget.LinearLayout", 90, 900, 900, 500, ident="parentPanel"),
             box("@11", "widget.TextView", 140, 950, 800, 60, "Clear your history?", ident="alertTitle"),
             box("@12", "widget.LinearLayout", 90, 1250, 900, 150, ident="buttonPanel"),
             box("@13", "widget.Button", 700, 1280, 250, 100, "OK", ident="button1")]
    assert guard.blocked_tap(clear[-1], [*page, *clear]) is None
    delete = [{**clear[0]}, {**clear[1], "text": "Delete account?"}, clear[2], {**clear[3], "text": "Delete"}]
    assert guard.blocked_tap(delete[-1], [*page, *delete]) == "delete (delete account on the screen)"
