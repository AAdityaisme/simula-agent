"""The deny-list on strings from all three apps' fixtures. Entry words may open an upsell; confirm words never
run; sending and typing belong to the core loop only."""

import dataclasses

import pytest

from simula.contracts import Rect
from simula.device.observe import Candidate, denied, dismiss_control


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


def test_the_exhibit_counts_denied_taps_that_reached_the_device(tmp_path, monkeypatch):
    from simula.stages import explore as stage
    from tests.fake_device import explorer
    from tests.test_explore_offline import janitor_like
    ex, phone = explorer(tmp_path, monkeypatch, janitor_like)
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
                                   "Personal Finance", "Remove ads", "Close paywall", "Halloween 2025 badge"])
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


def test_remove_ads_opens_an_upsell_and_is_a_confirm_on_one():
    from simula.device.observe import ENTRY
    assert all(ENTRY.search(label) for label in ["Remove ads", "Go ad-free", "No ads for a month"])
    assert denied(control("Remove ads")) is None
    assert denied(control("Remove ads"), upsell=True) == "remove"
