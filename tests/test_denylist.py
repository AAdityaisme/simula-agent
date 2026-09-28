"""The deny-list on strings from all three apps' fixtures. Entry words may open an upsell; confirm words never
run; sending and typing belong to the core loop only."""

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
