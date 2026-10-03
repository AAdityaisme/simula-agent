"""The agent's hard blocks: the Play Store gets BACK, Google's account chooser is part of signing in, a small generic
word list refuses account, public, social, purchase and (outside the core loop) send taps, also at the tap point and
on a dialog's confirm, and the agent types only the fixed neutral texts."""

import json
from pathlib import Path

import pytest

from simula.device import guard
from simula.device.mcp import parse_elements
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
                                     {"identifier": "com.example.app:id/password2"}])
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
                                   "Set up 2FA", "Two-step verification", "Change recovery address"])
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


def test_a_word_inside_prose_is_no_button_but_a_phrase_in_a_long_text_still_blocks_a_tap_inside_it():
    bubble, copy = shown("what would you like to know", 600, h=300), shown("Copy", 700, w=200)
    assert guard.blocked_tap(bubble, [bubble]) is None and guard.blocked_tap(copy, [bubble, copy]) is None
    assert guard.blocked_tap({"text": "Characters like this one show up here"}) is None
    story = shown("She said she would delete my account if I ever told anyone the secret", 600, h=900)
    assert guard.blocked_tap(story) and guard.blocked_tap(copy, [story, copy]) == "delete my account (at the tap point)"


@pytest.mark.parametrize("words", ["Pay $4.99 with saved card", "Post to my public profile",
                                   "Share with friends and family", "Report this user for spam",
                                   "Send to all selected contacts", "Send this reply back to Alex"])
def test_a_long_label_that_starts_with_a_one_word_entry_is_refused(words):
    assert guard.blocked_tap({"text": words})


def test_the_core_loop_lifts_only_the_send_family_on_a_long_label_too():
    assert guard.blocked_tap({"text": "Send this reply back to Alex"}, core=True) is None
    for words in ("Post to my public profile", "Pay $4.99 with saved card", "Share with friends and family"):
        assert guard.blocked_tap({"text": words}, core=True)


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


def test_a_glyph_inside_a_long_send_message_button_is_refused_at_its_tap_point():
    row, icon = shown("Send message to all selected group members", 700, h=200), shown("➤", 750, x=800, w=80, h=80)
    assert guard.blocked_tap(icon, [row, icon]) == "send message (at the tap point)"


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
