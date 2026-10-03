"""The agent's hard blocks: the Play Store gets BACK, Google's account chooser is part of signing in, a small generic
word list refuses account and public-post taps, and the agent types only the fixed neutral texts."""

import pytest

from simula.device import guard
from simula.stages.explore import CORE_MESSAGES, SEARCH_QUERIES
from tests.test_invariants import APP_WORDS


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


@pytest.mark.parametrize("element", [{"text": "Continue with Google"}, {"text": "Send"}, {"text": "Sign in"},
                                     {"text": "Settings"}, {"label": "Posts"}, {"text": "Shared with you"},
                                     {"identifier": "com.share.post.app:id/home_tab"}, {}])
def test_sign_in_sending_and_package_words_are_no_hard_block(element):
    assert guard.blocked_tap(element) is None


def test_the_agent_types_only_a_core_message_in_the_core_loop_or_a_search_query_in_a_search_box():
    assert guard.allowed_text(CORE_MESSAGES[0], core=True, field="composer")
    assert not guard.allowed_text(CORE_MESSAGES[0], core=False, field="composer")
    assert not guard.allowed_text("hello", core=True, field="composer")
    assert guard.allowed_text("popular", core=False, field="search") and "popular" in SEARCH_QUERIES
    assert not guard.allowed_text("hello", core=False, field="search")
    assert not guard.allowed_text("popular", core=False, field="composer")
    assert not guard.allowed_text(CORE_MESSAGES[0] + " ", core=True, field="composer")


def test_the_core_loop_starts_only_when_an_ai_receives_it():
    assert guard.core_allowed("ai")
    assert not guard.core_allowed("person") and not guard.core_allowed("none")


def test_the_word_list_names_no_app():
    assert guard.WORDS and not [w for w in guard.WORDS for app in APP_WORDS if app in w.lower()]
